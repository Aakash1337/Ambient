"""WASAPI capture backend built on pyaudiowpatch (Windows only).

pyaudiowpatch is a Windows-only package, so its import stays lazy inside
`_pyaudio_module`; the two PortAudio constants this module needs are spelled
out as literals below so an injected test fake never forces the real import.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

import numpy as np

from .base import CaptureDevice, DeviceKind, SourceStream

log = logging.getLogger(__name__)

# PortAudio ABI constants (pyaudio.paFloat32 / pyaudio.paWASAPI). Fixed by the
# PortAudio headers, not by any installation, so hardcoding them lets every
# code path that received an injected audio_factory run without pyaudiowpatch
# installed -- which is exactly how the test suite passes on Linux.
PA_FLOAT32 = 1
PA_WASAPI = 13


def _pyaudio_module() -> Any:
    try:
        import pyaudiowpatch as pyaudio
        return pyaudio
    except ImportError as exc:
        raise RuntimeError("pyaudiowpatch is not installed") from exc


def _clamped_channels(info: dict[str, Any]) -> int:
    # ALSA plug devices (default/pipewire/pulse) advertise absurd channel
    # counts (32-128); trusting them drowns the mic in a downmix and hits
    # PortAudio's crash-prone mmap path. Real capture hardware is 1-2 channels.
    return max(1, min(int(info.get("maxInputChannels", 1)), 2))


def _as_device(info: dict[str, Any], kind: DeviceKind) -> CaptureDevice:
    return CaptureDevice(
        id=str(int(info["index"])),
        name=str(info.get("name", f"Device {info['index']}")),
        kind=kind,
        channels=_clamped_channels(info),
        sample_rate=max(1, int(float(info.get("defaultSampleRate", 48000)))),
    )


def classify_capture_devices(
    raw_devices: Iterable[dict[str, Any]],
    wasapi_host_index: int,
) -> list[CaptureDevice]:
    """Return WASAPI non-loopback inputs followed by loopback endpoints."""

    microphones: list[CaptureDevice] = []
    loopbacks: list[CaptureDevice] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_devices:
        if int(raw.get("hostApi", -1)) != wasapi_host_index:
            continue
        is_loopback = bool(raw.get("isLoopbackDevice", False))
        channels = int(raw.get("maxInputChannels", 0))
        if not is_loopback and channels <= 0:
            continue
        kind: DeviceKind = "loopback" if is_loopback else "mic"
        device = _as_device(raw, kind)
        if device.key in seen:
            continue
        seen.add(device.key)
        (loopbacks if is_loopback else microphones).append(device)
    return microphones + loopbacks


def _find_input(audio: Any, substring: str) -> dict[str, Any]:
    wasapi = dict(audio.get_host_api_info_by_type(PA_WASAPI))
    if not substring:
        default_index = int(wasapi.get("defaultInputDevice", -1))
        if default_index >= 0:
            return dict(audio.get_device_info_by_index(default_index))
        return dict(audio.get_default_input_device_info())
    wanted = substring.casefold()
    matches = [
        dict(audio.get_device_info_by_index(index))
        for index in range(audio.get_device_count())
        if wanted in str(audio.get_device_info_by_index(index).get("name", "")).casefold()
        and int(audio.get_device_info_by_index(index).get("maxInputChannels", 0)) > 0
        and not bool(audio.get_device_info_by_index(index).get("isLoopbackDevice", False))
    ]
    if not matches:
        raise RuntimeError(f"No input device name contains {substring!r}")
    # Prefer the WASAPI duplicate so both capture sources use the intended host API.
    wasapi_matches = [
        item for item in matches if int(item.get("hostApi", -1)) == int(wasapi["index"])
    ]
    return (wasapi_matches or matches)[0]


def _input_candidates(audio: Any, substring: str) -> list[dict[str, Any]]:
    selected = _find_input(audio, substring)
    if substring:
        return [selected]
    wasapi = dict(audio.get_host_api_info_by_type(PA_WASAPI))
    candidates = [selected]
    for index in range(audio.get_device_count()):
        item = dict(audio.get_device_info_by_index(index))
        if (
            int(item.get("hostApi", -1)) == int(wasapi["index"])
            and int(item.get("maxInputChannels", 0)) > 0
            and not bool(item.get("isLoopbackDevice", False))
            and int(item["index"]) != int(selected["index"])
        ):
            candidates.append(item)
    return candidates


def _default_loopback(audio: Any, loopbacks: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return dict(audio.get_default_wasapi_loopback())
    except Exception:
        pass
    try:
        output = dict(audio.get_default_output_device_info())
    except Exception:
        output = {}
    name = str(output.get("name", "")).casefold()
    if name:
        for item in loopbacks:
            loop_name = str(item.get("name", "")).casefold()
            if name in loop_name or loop_name in name:
                return item
    if loopbacks:
        return loopbacks[0]
    raise RuntimeError("No WASAPI loopback endpoint is available")


def _find_loopback(
    audio: Any,
    substring: str,
    on_warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    loopbacks = [dict(item) for item in audio.get_loopback_device_info_generator()]
    if not substring:
        return _default_loopback(audio, loopbacks)
    wanted = substring.casefold()
    matches = [
        item for item in loopbacks if wanted in str(item.get("name", "")).casefold()
    ]
    if matches:
        return matches[0]
    # A stale pinned name in config is the most common way this breaks: headsets
    # and monitors come and go, and the configured endpoint stops existing.
    # Falling back to the current default output beats raising, because raising
    # means mic-only, which silently loses the other half of the conversation --
    # exactly the half worth answering.
    fallback = _default_loopback(audio, loopbacks)
    if on_warn is not None:
        on_warn(
            f"No loopback device matches {substring!r}; "
            f"falling back to {fallback.get('name', 'default')!r}"
        )
    return fallback


def _loopback_candidates(
    audio: Any,
    substring: str,
    on_warn: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Loopback endpoints to open, most likely to carry the call first.

    With no pinned substring this returns EVERY endpoint rather than just the
    Windows default. Which endpoint a call plays through is not knowable ahead of
    time -- it moves between headset and speakers, and an app may render to a
    non-default device regardless of what Windows reports as default. Pinning one
    guess is how an entire conversation gets captured with the other speaker
    missing. Opening an idle endpoint costs almost nothing: it delivers silence,
    and the arbiter forwards only whichever endpoint actually has speech on it.
    """
    loopbacks = [dict(item) for item in audio.get_loopback_device_info_generator()]
    if not loopbacks:
        raise RuntimeError("No WASAPI loopback endpoint is available")
    if substring:
        return [_find_loopback(audio, substring, on_warn)]
    preferred = _default_loopback(audio, loopbacks)
    rest = [
        item
        for item in loopbacks
        if int(item.get("index", -1)) != int(preferred.get("index", -1))
    ]
    return [preferred, *rest]


class _PyAudioStream:
    """SourceStream over a live PyAudio stream.

    stop() calls stop_stream(), which is the only call that unblocks a reader
    wedged inside stream.read() -- PortAudio forbids close() or terminate()
    while another thread is in Pa_ReadStream, so callers must stop, join, and
    only then close.
    """

    def __init__(self, stream: Any, rate: int, channels: int) -> None:
        self._stream = stream
        self._stopped = False
        self.rate = rate
        self.channels = channels

    def read(self, frames: int) -> np.ndarray:
        raw = self._stream.read(frames, exception_on_overflow=False)
        return np.frombuffer(raw, dtype=np.float32)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stream.stop_stream()

    def close(self) -> None:
        self._stream.close()


class WasapiSession:
    """One PyAudio instance; lives from open_session() to close()."""

    def __init__(self, audio: Any, frame_ms: int) -> None:
        self._audio = audio
        self._frame_ms = frame_ms

    def mic_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        # Only one microphone can be the speaker's; with no pinned name the
        # rest are fallbacks tried in order until one opens.
        return [_as_device(info, "mic") for info in _input_candidates(self._audio, substring)]

    def loopback_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        return [
            _as_device(info, "loopback")
            for info in _loopback_candidates(self._audio, substring, on_warn)
        ]

    def open(self, device: CaptureDevice) -> SourceStream:
        native_frames = max(1, int(device.sample_rate * self._frame_ms / 1000))
        # No host-API stream-info is supplied: WASAPI therefore stays in
        # shared mode and coexists with NVIDIA Broadcast and other apps.
        stream = self._audio.open(
            format=PA_FLOAT32,
            channels=device.channels,
            rate=device.sample_rate,
            input=True,
            input_device_index=int(device.id),
            frames_per_buffer=native_frames,
        )
        return _PyAudioStream(stream, device.sample_rate, device.channels)

    def close(self) -> None:
        self._audio.terminate()


class WasapiBackend:
    name = "wasapi"
    has_system_audio = True

    def __init__(
        self,
        *,
        audio_factory: Callable[[], Any] | None = None,
        frame_ms: int = 25,
    ) -> None:
        self._audio_factory = audio_factory
        self._frame_ms = frame_ms

    def _new_audio(self) -> Any:
        # The factory must be consulted FIRST: resolving pyaudiowpatch before
        # it would make an injected fake useless anywhere the real package is
        # absent, i.e. everywhere except Windows.
        if self._audio_factory is not None:
            return self._audio_factory()
        return _pyaudio_module().PyAudio()

    def list_devices(self) -> list[CaptureDevice]:
        audio = self._new_audio()
        try:
            wasapi = dict(audio.get_host_api_info_by_type(PA_WASAPI))
            raw_devices = []
            for index in range(audio.get_device_count()):
                item = dict(audio.get_device_info_by_index(index))
                item["index"] = index
                raw_devices.append(item)
            return classify_capture_devices(raw_devices, int(wasapi["index"]))
        finally:
            audio.terminate()

    def open_session(self) -> WasapiSession:
        return WasapiSession(self._new_audio(), self._frame_ms)
