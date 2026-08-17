"""Non-blocking microphone and WASAPI loopback capture."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import numpy as np

from .bus import AudioFrame, DropOldestQueue, put_threadsafe
from .config import AudioConfig

log = logging.getLogger(__name__)


def _pyaudio_module() -> Any:
    try:
        import pyaudiowpatch as pyaudio
        return pyaudio
    except ImportError as exc:
        raise RuntimeError("pyaudiowpatch is not installed") from exc


def iter_devices() -> Iterator[dict[str, Any]]:
    pyaudio = _pyaudio_module()
    audio = pyaudio.PyAudio()
    try:
        for index in range(audio.get_device_count()):
            info = dict(audio.get_device_info_by_index(index))
            info["index"] = index
            yield info
    finally:
        audio.terminate()


def list_capture_devices() -> list[dict[str, Any]]:
    """Return normal input devices plus WASAPI loopback endpoints."""
    devices = []
    for info in iter_devices():
        is_input = int(info.get("maxInputChannels", 0)) > 0
        is_loopback = bool(info.get("isLoopbackDevice", False))
        if is_input or is_loopback:
            info["kind"] = "loopback" if is_loopback else "input"
            devices.append(info)
    return devices


def _find_input(audio: Any, substring: str) -> dict[str, Any]:
    pyaudio = _pyaudio_module()
    wasapi = dict(audio.get_host_api_info_by_type(pyaudio.paWASAPI))
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
    pyaudio = _pyaudio_module()
    wasapi = dict(audio.get_host_api_info_by_type(pyaudio.paWASAPI))
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


class LoopbackArbiter:
    """Forwards frames from only the loopback endpoint that is carrying speech.

    Several endpoints are open at once, but they all feed one `sys` channel, and
    one segmenter cannot be fed two interleaved conversations. So exactly one
    endpoint wins at a time.

    Handover is instant once the incumbent has been quiet for `hold_s`: a
    challenger's very first speech frame takes over, so switching devices between
    sessions costs nothing. The hold only prevents flapping while the incumbent is
    mid-utterance -- room noise on another endpoint cannot steal the stream.
    """

    def __init__(self, hold_s: float = 1.5) -> None:
        self.hold_s = hold_s
        self._lock = threading.Lock()
        self._winner: int | None = None
        self._winner_signal_at = 0.0

    @property
    def winner(self) -> int | None:
        with self._lock:
            return self._winner

    def observe(self, source_id: int, rms: float, now: float) -> bool:
        """Record one endpoint's level; return whether its frames should be used."""
        with self._lock:
            if rms > SIGNAL_RMS:
                if self._winner is None or source_id == self._winner:
                    self._winner = source_id
                    self._winner_signal_at = now
                elif now - self._winner_signal_at >= self.hold_s:
                    self._winner = source_id
                    self._winner_signal_at = now
            # Before anything has ever spoken every endpoint is forwarding
            # silence, which is harmless and keeps the first word from being lost.
            return self._winner is None or source_id == self._winner


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


# RMS below this is room tone or a muted endpoint, not speech. Chosen well under
# the energy-VAD threshold (0.012) so that anything the VAD could ever act on
# counts as signal here.
SIGNAL_RMS = 0.004


@dataclass(slots=True)
class SourceState:
    name: str
    active: bool = False
    detail: str = "stopped"
    opened_at: float = 0.0
    last_signal_at: float = 0.0

    def silent_for(self) -> float | None:
        """Seconds this source has been open without carrying audible audio.

        None when the source is not running -- "off" and "open but deaf" are
        different problems and the status bar must not conflate them.
        """
        if not self.active:
            return None
        return max(0.0, time.time() - (self.last_signal_at or self.opened_at))


class AudioCapture:
    def __init__(
        self,
        config: AudioConfig,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.status_callback = status_callback or (lambda _message: None)
        self.mic = SourceState("mic")
        self.loopback = SourceState("loopback")
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._enabled.set()
        self._threads: list[threading.Thread] = []
        self._audio: Any | None = None
        self._streams: list[Any] = []
        self._arbiter: LoopbackArbiter | None = None
        # Several threads share one SourceState when many endpoints feed `sys`.
        # Without a count, the first thread to exit marks the whole channel dead.
        self._runners: dict[str, int] = {}
        self._runner_lock = threading.Lock()

    def _enter_source(self, state: SourceState, detail: str) -> None:
        with self._runner_lock:
            count = self._runners.get(state.name, 0)
            self._runners[state.name] = count + 1
            if count == 0:
                state.opened_at = time.time()
                state.last_signal_at = 0.0
                state.detail = detail
            state.active = True

    def _exit_source(self, state: SourceState, detail: str | None = None) -> None:
        with self._runner_lock:
            count = max(0, self._runners.get(state.name, 1) - 1)
            self._runners[state.name] = count
            if count == 0:
                state.active = False
                if detail is not None:
                    state.detail = detail

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        output: DropOldestQueue[AudioFrame],
        *,
        enabled: bool = True,
    ) -> None:
        self._stop.clear()
        self.set_enabled(enabled)
        try:
            pyaudio = _pyaudio_module()
            self._audio = pyaudio.PyAudio()
        except Exception as exc:
            self.status_callback(f"Audio capture unavailable: {exc}")
            return
        sources: list[tuple[str, dict[str, Any], Any]] = []
        for channel in ("mic", "sys"):
            try:
                if channel == "mic":
                    candidates = _input_candidates(self._audio, self.config.mic_device)
                    # Only one microphone can be the speaker's; the rest are
                    # fallbacks tried in order until one opens.
                    keep_all = False
                else:
                    candidates = _loopback_candidates(
                        self._audio,
                        self.config.output_device,
                        self.status_callback,
                    )
                    keep_all = len(candidates) > 1
                opened: list[tuple[dict[str, Any], Any]] = []
                last_error: Exception | None = None
                for info in candidates:
                    try:
                        rate = int(info["defaultSampleRate"])
                        channels = int(info["maxInputChannels"])
                        native_frames = max(
                            1, int(rate * self.config.frame_ms / 1000)
                        )
                        stream = self._audio.open(
                            format=pyaudio.paFloat32,
                            channels=channels,
                            rate=rate,
                            input=True,
                            input_device_index=int(info["index"]),
                            frames_per_buffer=native_frames,
                        )
                    except Exception as exc:
                        # One dead endpoint among many is normal (virtual devices
                        # from Steam, NVIDIA and the like refuse to open). Only a
                        # total failure is worth reporting.
                        last_error = exc
                        log.debug("Could not open %s device %s: %s", channel, info.get("name"), exc)
                        continue
                    opened.append((info, stream))
                    if not keep_all:
                        break
                if not opened:
                    raise last_error or RuntimeError(f"No usable {channel} device")
                for info, stream in opened:
                    self._streams.append(stream)
                    sources.append((channel, info, stream))
            except Exception as exc:
                if channel == "sys":
                    message = f"Loopback unavailable; continuing mic-only: {exc}"
                    log.warning(message)
                else:
                    message = f"Microphone unavailable: {exc}"
                    log.error(message)
                self.status_callback(message)
        loopback_sources = [item for item in sources if item[0] == "sys"]
        self._arbiter = (
            LoopbackArbiter() if len(loopback_sources) > 1 else None
        )
        if self._arbiter is not None:
            self.status_callback(
                f"sys active: watching {len(loopback_sources)} output endpoints"
            )
        for channel, info, stream in sources:
            thread = threading.Thread(
                target=self._capture_source,
                args=(channel, info, stream, loop, output, self._arbiter),
                name=f"ambientqa-{channel}-{info.get('index')}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        for stream in self._streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as exc:
                log.debug("Error while closing audio stream: %s", exc)
        self._streams.clear()
        self._arbiter = None
        with self._runner_lock:
            self._runners.clear()
            # A capture thread can still be blocked in stream.read() past the
            # join timeout, so its own exit cannot be relied on to clear this.
            # After stop() the sources ARE stopped; say so rather than leaving
            # the status bar reporting a channel that is no longer running.
            for state in (self.mic, self.loopback):
                state.active = False
                state.detail = "stopped"
        if self._audio is not None:
            self._audio.terminate()
            self._audio = None

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    def _capture_source(
        self,
        channel: str,
        info: dict[str, Any],
        stream: Any,
        loop: asyncio.AbstractEventLoop,
        output: DropOldestQueue[AudioFrame],
        arbiter: LoopbackArbiter | None = None,
    ) -> None:
        state = self.mic if channel == "mic" else self.loopback
        name = str(info.get("name", channel))
        source_id = int(info.get("index", -1))
        entered = False
        if channel != "sys":
            # The arbiter picks between competing LOOPBACK endpoints. Letting it
            # judge the mic means a winning sys endpoint mutes the microphone
            # outright -- the mic loses every contest it was never entered in.
            arbiter = None
        try:
            import soxr

            rate = int(info["defaultSampleRate"])
            channels = int(info["maxInputChannels"])
            native_frames = max(1, int(rate * self.config.frame_ms / 1000))
            self._enter_source(state, name)
            entered = True
            if arbiter is None:
                self.status_callback(f"{channel} active: {name}")
            output_buffer = np.empty(0, dtype=np.float32)
            target_frames = int(16000 * self.config.frame_ms / 1000)
            resampler = (
                soxr.ResampleStream(
                    rate, 16000, 1, dtype="float32", quality="LQ"
                )
                if rate != 16000
                else None
            )
            while not self._stop.is_set():
                raw = stream.read(native_frames, exception_on_overflow=False)
                samples = np.frombuffer(raw, dtype=np.float32)
                if channels > 1:
                    samples = samples.reshape(-1, channels).mean(axis=1)
                # Measured pre-resample and regardless of the pause state: this
                # answers "is this endpoint carrying anything at all", which stays
                # a useful question even while capture is paused.
                now = time.time()
                # Squared in float64: some host APIs hand back frames that are
                # not valid float32, and squaring those in float32 overflows to
                # inf, which would poison the level for every later frame.
                level = (
                    float(
                        np.sqrt(
                            np.mean(np.multiply(samples, samples, dtype=np.float64))
                        )
                    )
                    if samples.size
                    else 0.0
                )
                if not np.isfinite(level):
                    level = 0.0
                if level > SIGNAL_RMS:
                    state.last_signal_at = now
                forwarding = True
                if arbiter is not None:
                    forwarding = arbiter.observe(source_id, level, now)
                    if forwarding and level > SIGNAL_RMS and state.detail != name:
                        state.detail = name
                        self.status_callback(f"{channel} active: {name}")
                if resampler is not None:
                    samples = resampler.resample_chunk(samples, last=False)
                output_buffer = np.concatenate(
                    (output_buffer, np.asarray(samples, dtype=np.float32))
                )
                # Resampling still ran, so the stream's resampler state stays
                # consistent and a later handover starts clean.
                if not self._enabled.is_set() or not forwarding:
                    output_buffer = np.empty(0, dtype=np.float32)
                    continue
                while len(output_buffer) >= target_frames:
                    frame = output_buffer[:target_frames].copy()
                    output_buffer = output_buffer[target_frames:]
                    put_threadsafe(loop, output, AudioFrame(channel, frame, time.time()))
        except Exception as exc:
            if channel == "sys" and arbiter is not None:
                # One endpoint of many dying is not a channel outage; the others
                # keep watching. Reporting it as "continuing mic-only" would be
                # a lie that trains the user to ignore the warning that matters.
                log.warning("Loopback endpoint %s stopped: %s", name, exc)
            elif channel == "sys":
                message = f"Loopback unavailable; continuing mic-only: {exc}"
                log.warning(message)
                self.status_callback(message)
            else:
                message = f"Microphone unavailable: {exc}"
                log.error(message)
                self.status_callback(message)
            if entered:
                self._exit_source(state, str(exc))
        else:
            self._exit_source(state)
