"""Backend-neutral device discovery and low-cost, concurrent level probes."""

from __future__ import annotations

import math
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np
from numpy.typing import NDArray

from .backends.base import (
    AudioBackend,
    BackendSession,
    CaptureDevice,
    DeviceKind,
    SourceStream,
)

__all__ = [
    "AudioDeviceSession",
    "CaptureDevice",
    "DeviceKind",
    "DeviceMeterPool",
    "MeterReading",
    "MeterSession",
    "amplitude_to_db",
    "calculate_levels",
    "level_to_bar",
    "short_error",
]


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
    devices: list[CaptureDevice]
    active_mic: str
    active_loopback: str

    def snapshot(self, width: int = 18) -> dict[tuple[str, str], MeterReading]: ...
    def close(self) -> None: ...


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
    # splitlines() on an empty message is [], not [""]; exceptions raised with
    # no args must still fall through to the class name instead of crashing
    # the very error path this exists to describe.
    lines = str(error).splitlines()
    message = (lines[0].strip() if lines else "") or error.__class__.__name__
    return message if len(message) <= limit else message[: limit - 1] + "…"


@dataclass(slots=True)
class _ProbeState:
    stream: SourceStream | None = None
    pending_peak: float = 0.0
    latest_rms: float = 0.0
    held_peak: float = 0.0
    sampled_at: float = 0.0
    unavailable: str | None = None


class DeviceMeterPool:
    """Open and meter every endpoint concurrently through one backend session."""

    def __init__(
        self,
        devices: list[CaptureDevice],
        session: BackendSession,
        *,
        frames_per_buffer: int = 480,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.devices = devices
        self.session = session
        self.frames_per_buffer = frames_per_buffer
        self._clock = clock
        self._states = {device.key: _ProbeState() for device in devices}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        for device in self.devices:
            state = self._states[device.key]
            try:
                state.stream = self.session.open(device)
            except Exception as exc:
                state.unavailable = short_error(exc)
                continue
            try:
                thread = threading.Thread(
                    target=self._read_device,
                    args=(device, state),
                    name=f"ambientqa-meter-{device.id}",
                    daemon=True,
                )
                thread.start()
            except Exception as exc:
                # Thread creation failing (resource exhaustion) must not
                # propagate: the caller would never receive the pool, so
                # close() -- the only path that releases the streams already
                # opened above -- would be unreachable and every one of them
                # (a live parec child per device on Linux) would leak.
                state.unavailable = short_error(exc)
                with suppress(Exception):
                    state.stream.stop()
                with suppress(Exception):
                    state.stream.close()
                state.stream = None
                continue
            self._threads.append(thread)

    def _read_device(self, device: CaptureDevice, state: _ProbeState) -> None:
        stream = state.stream
        assert stream is not None
        try:
            while not self._stop.is_set():
                samples = stream.read(self.frames_per_buffer)
                if stream.channels > 1:
                    complete = samples.size - samples.size % stream.channels
                    samples = (
                        samples[:complete].reshape(-1, stream.channels).mean(axis=1)
                    )
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
    ) -> dict[tuple[str, str], MeterReading]:
        now = self._clock()
        readings: dict[tuple[str, str], MeterReading] = {}
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
                    state.stream.stop()
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
        try:
            self.session.close()
        except Exception:
            pass


class AudioDeviceSession:
    def __init__(
        self,
        devices: list[CaptureDevice],
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
        backend: AudioBackend,
        *,
        active_mic: str = "",
        active_loopback: str = "",
    ) -> AudioDeviceSession:
        devices = backend.list_devices()
        meter = DeviceMeterPool(devices, backend.open_session())
        meter.start()
        return cls(
            devices,
            meter,
            active_mic=active_mic,
            active_loopback=active_loopback,
        )

    def snapshot(
        self, width: int = 18
    ) -> dict[tuple[str, str], MeterReading]:
        return self.meter.snapshot(width)

    def close(self) -> None:
        self.meter.close()
