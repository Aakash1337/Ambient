"""WASAPI device discovery and low-cost, concurrent level probes."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from .audio import _pyaudio_module

DeviceKind = Literal["mic", "loopback"]


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    name: str
    kind: DeviceKind
    channels: int
    sample_rate: int

    @property
    def key(self) -> tuple[DeviceKind, int]:
        return self.kind, self.index

    @property
    def display_name(self) -> str:
        if self.kind == "mic" and "nvidia broadcast" in self.name.casefold():
            return f"{self.name} (NVIDIA Broadcast - noise removal)"
        return self.name


@dataclass(frozen=True, slots=True)
class MeterReading:
    peak: float = 0.0
    rms: float = 0.0
    bar: int = 0
    unavailable: str | None = None

    @property
    def peak_db(self) -> float:
        return amplitude_to_db(self.peak)

    @property
    def rms_db(self) -> float:
        return amplitude_to_db(self.rms)


class MeterSession(Protocol):
    devices: list[AudioDevice]
    active_mic: str
    active_loopback: str

    def snapshot(self, width: int = 18) -> dict[tuple[DeviceKind, int], MeterReading]: ...
    def close(self) -> None: ...


def classify_capture_devices(
    raw_devices: Iterable[dict[str, Any]],
    wasapi_host_index: int,
) -> list[AudioDevice]:
    """Return WASAPI non-loopback inputs followed by loopback endpoints."""

    microphones: list[AudioDevice] = []
    loopbacks: list[AudioDevice] = []
    seen: set[tuple[DeviceKind, int]] = set()
    for raw in raw_devices:
        if int(raw.get("hostApi", -1)) != wasapi_host_index:
            continue
        is_loopback = bool(raw.get("isLoopbackDevice", False))
        channels = int(raw.get("maxInputChannels", 0))
        if not is_loopback and channels <= 0:
            continue
        kind: DeviceKind = "loopback" if is_loopback else "mic"
        device = AudioDevice(
            index=int(raw["index"]),
            name=str(raw.get("name", f"Device {raw['index']}")),
            kind=kind,
            channels=max(1, channels),
            sample_rate=max(1, int(float(raw.get("defaultSampleRate", 48000)))),
        )
        if device.key in seen:
            continue
        seen.add(device.key)
        (loopbacks if is_loopback else microphones).append(device)
    return microphones + loopbacks


def list_wasapi_capture_devices(
    audio_factory: Callable[[], Any] | None = None,
) -> list[AudioDevice]:
    pyaudio = _pyaudio_module()
    audio = (audio_factory or pyaudio.PyAudio)()
    try:
        wasapi = dict(audio.get_host_api_info_by_type(pyaudio.paWASAPI))
        raw_devices = []
        for index in range(audio.get_device_count()):
            item = dict(audio.get_device_info_by_index(index))
            item["index"] = index
            raw_devices.append(item)
        return classify_capture_devices(raw_devices, int(wasapi["index"]))
    finally:
        audio.terminate()


def calculate_levels(samples: NDArray[np.float32]) -> tuple[float, float]:
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    if mono.size == 0:
        return 0.0, 0.0
    absolute = np.abs(mono)
    peak = min(1.0, float(np.max(absolute)))
    rms = min(1.0, float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))))
    return peak, rms


def amplitude_to_db(amplitude: float, floor_db: float = -60.0) -> float:
    if amplitude <= 0.0:
        return floor_db
    return max(floor_db, 20.0 * math.log10(min(1.0, amplitude)))


def level_to_bar(amplitude: float, width: int = 18, floor_db: float = -60.0) -> int:
    if width <= 0 or amplitude <= 0.0:
        return 0
    normalized = (amplitude_to_db(amplitude, floor_db) - floor_db) / -floor_db
    return min(width, max(0, int(round(normalized * width))))


def short_error(error: BaseException, limit: int = 72) -> str:
    message = str(error).splitlines()[0].strip() or error.__class__.__name__
    return message if len(message) <= limit else message[: limit - 1] + "…"


@dataclass(slots=True)
class _ProbeState:
    stream: Any | None = None
    pending_peak: float = 0.0
    latest_rms: float = 0.0
    held_peak: float = 0.0
    sampled_at: float = 0.0
    unavailable: str | None = None


class DeviceMeterPool:
    """Open and meter every endpoint concurrently using ordinary shared streams."""

    def __init__(
        self,
        devices: list[AudioDevice],
        *,
        audio_factory: Callable[[], Any] | None = None,
        frames_per_buffer: int = 480,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.devices = devices
        self.frames_per_buffer = frames_per_buffer
        self._audio_factory = audio_factory
        self._clock = clock
        self._audio: Any | None = None
        self._states = {device.key: _ProbeState() for device in devices}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        try:
            pyaudio = _pyaudio_module()
            factory = self._audio_factory or pyaudio.PyAudio
            self._audio = factory()
        except Exception as exc:
            reason = short_error(exc)
            for state in self._states.values():
                state.unavailable = reason
            return

        for device in self.devices:
            state = self._states[device.key]
            try:
                # No host-API stream-info is supplied: WASAPI therefore stays in
                # shared mode and coexists with NVIDIA Broadcast and other apps.
                state.stream = self._audio.open(
                    format=pyaudio.paFloat32,
                    channels=device.channels,
                    rate=device.sample_rate,
                    input=True,
                    input_device_index=device.index,
                    frames_per_buffer=self.frames_per_buffer,
                )
            except Exception as exc:
                state.unavailable = short_error(exc)
                continue
            thread = threading.Thread(
                target=self._read_device,
                args=(device, state),
                name=f"ambientqa-meter-{device.index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _read_device(self, device: AudioDevice, state: _ProbeState) -> None:
        assert state.stream is not None
        try:
            while not self._stop.is_set():
                raw = state.stream.read(
                    self.frames_per_buffer, exception_on_overflow=False
                )
                samples = np.frombuffer(raw, dtype=np.float32)
                if device.channels > 1:
                    complete = samples.size - samples.size % device.channels
                    samples = samples[:complete].reshape(-1, device.channels).mean(axis=1)
                peak, rms = calculate_levels(samples)
                with self._lock:
                    state.pending_peak = max(state.pending_peak, peak)
                    state.latest_rms = rms
        except Exception as exc:
            if not self._stop.is_set():
                with self._lock:
                    state.unavailable = short_error(exc)

    def snapshot(
        self, width: int = 18
    ) -> dict[tuple[DeviceKind, int], MeterReading]:
        now = self._clock()
        readings: dict[tuple[DeviceKind, int], MeterReading] = {}
        with self._lock:
            for key, state in self._states.items():
                if state.unavailable is not None:
                    readings[key] = MeterReading(unavailable=state.unavailable)
                    continue
                elapsed = max(0.0, now - state.sampled_at) if state.sampled_at else 0.1
                decay = 0.72 ** (elapsed / 0.1)
                state.held_peak = max(state.pending_peak, state.held_peak * decay)
                state.pending_peak = 0.0
                state.sampled_at = now
                readings[key] = MeterReading(
                    peak=state.held_peak,
                    rms=state.latest_rms,
                    bar=level_to_bar(max(state.held_peak, state.latest_rms), width),
                )
        return readings

    def close(self) -> None:
        self._stop.set()
        for state in self._states.values():
            if state.stream is not None:
                try:
                    state.stream.stop_stream()
                except Exception:
                    pass
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        for state in self._states.values():
            if state.stream is not None:
                try:
                    state.stream.close()
                except Exception:
                    pass
                state.stream = None
        if self._audio is not None:
            try:
                self._audio.terminate()
            finally:
                self._audio = None


class AudioDeviceSession:
    def __init__(
        self,
        devices: list[AudioDevice],
        meter: DeviceMeterPool,
        *,
        active_mic: str = "",
        active_loopback: str = "",
    ) -> None:
        self.devices = devices
        self.meter = meter
        self.active_mic = active_mic
        self.active_loopback = active_loopback

    @classmethod
    def open(
        cls,
        *,
        active_mic: str = "",
        active_loopback: str = "",
    ) -> AudioDeviceSession:
        devices = list_wasapi_capture_devices()
        meter = DeviceMeterPool(devices)
        meter.start()
        return cls(
            devices,
            meter,
            active_mic=active_mic,
            active_loopback=active_loopback,
        )

    def snapshot(
        self, width: int = 18
    ) -> dict[tuple[DeviceKind, int], MeterReading]:
        return self.meter.snapshot(width)

    def close(self) -> None:
        self.meter.close()
