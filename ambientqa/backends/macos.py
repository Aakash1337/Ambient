"""macOS CoreAudio capture through :mod:`sounddevice`.

CoreAudio exposes microphones as input devices but, unlike WASAPI and
PipeWire, does not expose physical outputs as recordable loopback endpoints.
System audio therefore arrives through a virtual CoreAudio input such as
BlackHole.  Known virtual drivers are classified automatically; an explicitly
pinned ``audio.output_device`` may select any input so less-common drivers work
without a code change.

``sounddevice`` stays lazy and injectable.  Importing Ambient on Windows or
Linux never imports PortAudio, and the full backend contract can be tested with
small fakes on either platform.
"""

from __future__ import annotations

import logging
import threading
from contextlib import suppress
from typing import Any, Callable, Iterable

import numpy as np

from .base import CaptureDevice, DeviceKind, SourceStream

log = logging.getLogger(__name__)

_MISSING_SOUNDDEVICE = (
    "sounddevice is not installed; run ./setup-macos.sh to install the "
    "macOS CoreAudio dependencies"
)
_MISSING_LOOPBACK = (
    "No macOS system-audio loopback input was found. Install BlackHole 2ch "
    "(for example, `brew install --cask blackhole-2ch`), restart the Mac, "
    "create a Multi-Output Device in Audio MIDI Setup, then relaunch. Microphone capture can "
    "continue without system audio."
)

# These are input-device names exposed by established macOS virtual audio
# drivers.  Matching is intentionally conservative: a user can still pin any
# other driver by name through audio.output_device.
_LOOPBACK_MARKERS = (
    "blackhole",
    "soundflower",
    "loopback audio",
    "background music",
    "vb-cable",
    "vb cable",
    "vb-audio",
)


def _strict_stream_action(action: Callable[..., Any]) -> Any:
    """Run a sounddevice stop/abort without its silent-error default.

    sounddevice 0.5.x defaults ``ignore_errors=True`` for both methods, which
    makes a failed PortAudio abort look successful and defeats our fallback.
    Tiny injected fakes and older compatible wrappers may accept no keyword, so
    retry the legacy call shape only for that signature mismatch.
    """
    try:
        return action(ignore_errors=False)
    except TypeError:
        return action()


def _sounddevice_module() -> Any:
    try:
        import sounddevice

        return sounddevice
    except ImportError as exc:  # pragma: no cover - exercised on a real Mac
        raise RuntimeError(_MISSING_SOUNDDEVICE) from exc


def is_loopback_device_name(name: str) -> bool:
    """Return whether a CoreAudio input is a known virtual loopback driver."""

    folded = name.casefold()
    return any(marker in folded for marker in _LOOPBACK_MARKERS)


def _as_device(raw: dict[str, Any], kind: DeviceKind) -> CaptureDevice:
    index = int(raw["index"])
    return CaptureDevice(
        id=str(index),
        name=str(raw.get("name") or f"CoreAudio input {index}"),
        kind=kind,
        channels=max(1, min(int(raw.get("max_input_channels", 1)), 2)),
        sample_rate=max(1, int(float(raw.get("default_samplerate", 48000)))),
    )


def classify_coreaudio_devices(
    raw_devices: Iterable[dict[str, Any]],
) -> list[CaptureDevice]:
    """Classify input-capable CoreAudio records, microphones first."""

    microphones: list[CaptureDevice] = []
    loopbacks: list[CaptureDevice] = []
    seen: set[int] = set()
    for raw in raw_devices:
        if int(raw.get("max_input_channels", 0)) <= 0:
            continue
        index = int(raw["index"])
        if index in seen:
            continue
        seen.add(index)
        name = str(raw.get("name", ""))
        kind: DeviceKind = "loopback" if is_loopback_device_name(name) else "mic"
        (loopbacks if kind == "loopback" else microphones).append(
            _as_device(raw, kind)
        )
    return microphones + loopbacks


def _coreaudio_input_records(sounddevice_module: Any) -> list[dict[str, Any]]:
    """Return sounddevice records belonging to the Core Audio host API."""

    try:
        devices = list(sounddevice_module.query_devices())
    except Exception as exc:
        raise RuntimeError(f"Unable to enumerate CoreAudio devices: {exc}") from exc

    host_indexes: set[int] | None = None
    query_hostapis = getattr(sounddevice_module, "query_hostapis", None)
    if callable(query_hostapis):
        try:
            host_apis = list(query_hostapis())
        except Exception as exc:
            raise RuntimeError(f"Unable to enumerate CoreAudio host APIs: {exc}") from exc
        host_indexes = {
            index
            for index, item in enumerate(host_apis)
            if "core audio" in str(dict(item).get("name", "")).casefold()
        }
        if not host_indexes:
            raise RuntimeError("The Core Audio host API is unavailable")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(devices):
        record = dict(item)
        if host_indexes is not None and int(record.get("hostapi", -1)) not in host_indexes:
            continue
        if int(record.get("max_input_channels", 0)) <= 0:
            continue
        record["index"] = index
        records.append(record)
    return records


def _default_input_index(sounddevice_module: Any) -> int | None:
    default = getattr(sounddevice_module, "default", None)
    value = getattr(default, "device", None)
    # sounddevice exposes this as its own indexable _InputOutputPair, not a
    # tuple/list.  Accept ordinary scalar test doubles as well as the real pair.
    try:
        value = value[0]
    except (TypeError, KeyError, IndexError):
        pass
    except Exception:
        # Resolving sounddevice's lazy default can itself fail when CoreAudio has
        # no default input.  Enumeration order remains a usable fallback.
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


def _matches(device: CaptureDevice, substring: str) -> bool:
    wanted = substring.casefold()
    return wanted in device.name.casefold() or wanted in device.id.casefold()


def _as_kind(device: CaptureDevice, kind: DeviceKind) -> CaptureDevice:
    if device.kind == kind:
        return device
    return CaptureDevice(
        id=device.id,
        name=device.name,
        kind=kind,
        channels=device.channels,
        sample_rate=device.sample_rate,
    )


class CoreAudioStream:
    """Blocking ``SourceStream`` over ``sounddevice.RawInputStream``."""

    def __init__(self, stream: Any, rate: int, channels: int) -> None:
        self._stream = stream
        self.rate = rate
        self.channels = channels
        self._lock = threading.Lock()
        self._stopped = False
        self._closed = False

    def read(self, frames: int) -> np.ndarray:
        with self._lock:
            if self._stopped:
                raise RuntimeError("CoreAudio stream is stopped")
        try:
            result = self._stream.read(frames)
        except Exception as exc:
            with self._lock:
                stopped = self._stopped
            if stopped:
                raise RuntimeError("CoreAudio stream stopped") from exc
            raise RuntimeError(f"CoreAudio capture failed: {exc}") from exc

        if isinstance(result, tuple):
            raw, overflowed = result
        else:  # Small injected fakes may return the buffer directly.
            raw, overflowed = result, False
        if overflowed:
            log.warning("CoreAudio input overflowed; one capture block was delayed")
        samples = np.frombuffer(raw, dtype=np.float32).copy()
        expected = frames * self.channels
        if samples.size < expected:
            raise RuntimeError(
                f"CoreAudio returned {samples.size} samples; expected {expected}"
            )
        return samples[:expected]

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        # abort() is PortAudio's immediate cross-thread unblock.  Retain stop()
        # as a defensive seam for older sounddevice versions and test doubles.
        abort = getattr(self._stream, "abort", None)
        stop = getattr(self._stream, "stop", None)
        if callable(abort):
            try:
                _strict_stream_action(abort)
                return
            except Exception as exc:
                log.debug("CoreAudio abort failed; trying stop(): %s", exc)
        if callable(stop):
            with suppress(Exception):
                _strict_stream_action(stop)

    def close(self) -> None:
        self.stop()
        with self._lock:
            if self._closed:
                return
            self._closed = True
        with suppress(Exception):
            self._stream.close()


class CoreAudioSession:
    def __init__(self, sounddevice_module: Any, frame_ms: int) -> None:
        self._sounddevice = sounddevice_module
        self._frame_ms = frame_ms

    def _devices(self) -> list[CaptureDevice]:
        return classify_coreaudio_devices(
            _coreaudio_input_records(self._sounddevice)
        )

    def mic_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        microphones = [device for device in self._devices() if device.kind == "mic"]
        if substring:
            matches = [device for device in microphones if _matches(device, substring)]
            if not matches:
                raise RuntimeError(f"No input device name contains {substring!r}")
            return matches
        default_index = _default_input_index(self._sounddevice)
        if default_index is None:
            return microphones
        preferred = [device for device in microphones if device.id == str(default_index)]
        return preferred + [device for device in microphones if device not in preferred]

    def loopback_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        devices = self._devices()
        recognized = [device for device in devices if device.kind == "loopback"]
        if substring:
            # Explicit selection is the escape hatch for virtual drivers whose
            # configurable names do not contain one of our conservative markers.
            matches = [device for device in devices if _matches(device, substring)]
            if matches:
                return [_as_kind(matches[0], "loopback")]
            if recognized:
                if on_warn is not None:
                    on_warn(
                        f"No loopback device matches {substring!r}; "
                        "watching all detected loopback inputs instead"
                    )
                # CoreAudio has no physical-output-to-virtual-input mapping from
                # which to identify one default.  Keep every viable virtual
                # input so the existing level arbiter can follow the active one.
                return recognized
        if not recognized:
            raise RuntimeError(_MISSING_LOOPBACK)
        return recognized

    def open(self, device: CaptureDevice) -> SourceStream:
        frames = max(1, int(device.sample_rate * self._frame_ms / 1000))
        stream: Any | None = None
        try:
            stream = self._sounddevice.RawInputStream(
                samplerate=device.sample_rate,
                blocksize=frames,
                device=int(device.id),
                channels=device.channels,
                dtype="float32",
            )
            stream.start()
        except Exception as exc:
            if stream is not None:
                with suppress(Exception):
                    stream.close()
            raise RuntimeError(f"Unable to open CoreAudio device {device.name!r}: {exc}") from exc
        assert stream is not None
        return CoreAudioStream(stream, device.sample_rate, device.channels)

    def close(self) -> None:
        # sounddevice owns no per-session handle; individual streams carry the
        # PortAudio resources and are closed by the orchestrator first.
        return None


class CoreAudioBackend:
    name = "coreaudio"
    has_system_audio = True

    def __init__(
        self,
        *,
        sounddevice_module: Any | None = None,
        frame_ms: int = 25,
    ) -> None:
        self._injected_sounddevice = sounddevice_module
        self._frame_ms = frame_ms

    def _module(self) -> Any:
        return (
            self._injected_sounddevice
            if self._injected_sounddevice is not None
            else _sounddevice_module()
        )

    def list_devices(self) -> list[CaptureDevice]:
        return classify_coreaudio_devices(_coreaudio_input_records(self._module()))

    def open_session(self) -> CoreAudioSession:
        return CoreAudioSession(self._module(), self._frame_ms)
