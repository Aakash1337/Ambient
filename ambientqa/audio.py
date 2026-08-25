"""Non-blocking microphone and system-audio capture over a platform backend."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .backends import get_backend
from .backends.base import AudioBackend, BackendSession, CaptureDevice, SourceStream
from .bus import AudioFrame, DropOldestQueue, put_threadsafe
from .config import AudioConfig

log = logging.getLogger(__name__)


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
        self._winner: str | int | None = None
        self._winner_signal_at = 0.0

    @property
    def winner(self) -> str | int | None:
        with self._lock:
            return self._winner

    def observe(self, source_id: str | int, rms: float, now: float) -> bool:
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
        backend: AudioBackend | None = None,
    ) -> None:
        self.config = config
        self.status_callback = status_callback or (lambda _message: None)
        self.backend = backend or get_backend(config)
        self.mic = SourceState("mic")
        self.loopback = SourceState("loopback")
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._enabled.set()
        # Runtime input switches are independent of the global pause switch.
        # Keep the streams open even while a channel is disabled so source
        # health remains measurable and re-enabling is instant.  These events
        # deliberately survive stop()/start(): opening the device picker must
        # not silently undo the user's listening choices.
        self._channel_enabled = {
            "mic": threading.Event(),
            "sys": threading.Event(),
        }
        for event in self._channel_enabled.values():
            event.set()
        # An Event tells readers the current state, but not whether it changed
        # while a blocking stream.read() was in flight.  A quick off -> on
        # cycle could otherwise make pre-mute samples look newly enabled.  The
        # generation makes every read that spans either boundary disposable.
        self._channel_generation = {"mic": 0, "sys": 0}
        self._channel_state_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._session: BackendSession | None = None
        self._streams: list[SourceStream] = []
        self._arbiter: LoopbackArbiter | None = None
        # Several threads share one SourceState when many endpoints feed `sys`.
        # Without a count, the first thread to exit marks the whole channel dead.
        self._runners: dict[str, int] = {}
        self._runner_lock = threading.Lock()
        # Bumped by every start() and stop(). A capture thread that outlives
        # stop()'s join timeout would otherwise error out of its stale stream
        # AFTER the next start() and decrement the NEW session's runner count,
        # falsely marking a healthy channel dead with a stale error.
        self._generation = 0
        # start() and stop() run on executor threads and the callers cannot
        # guarantee ordering: cancelling the device-picker worker abandons a
        # stop() that keeps running while the recovery path immediately calls
        # start(). Unserialised, the abandoned stop() closes the NEW session's
        # streams under their readers and its generation bump lands after the
        # new start()'s, deadening the fresh session's accounting. Held for
        # the whole of either call so overlaps become strict sequences.
        self._lifecycle_lock = threading.Lock()

    def _enter_source(self, state: SourceState, detail: str, generation: int) -> bool:
        with self._runner_lock:
            if generation != self._generation:
                return False
            count = self._runners.get(state.name, 0)
            self._runners[state.name] = count + 1
            if count == 0:
                state.opened_at = time.time()
                state.last_signal_at = 0.0
                state.detail = detail
            state.active = True
            return True

    def _exit_source(
        self,
        state: SourceState,
        detail: str | None = None,
        *,
        generation: int,
    ) -> None:
        with self._runner_lock:
            if generation != self._generation:
                return
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
        with self._lifecycle_lock:
            self._start_locked(loop, output, enabled=enabled)

    def _start_locked(
        self,
        loop: asyncio.AbstractEventLoop,
        output: DropOldestQueue[AudioFrame],
        *,
        enabled: bool = True,
    ) -> None:
        self._stop.clear()
        self.set_enabled(enabled)
        with self._runner_lock:
            self._generation += 1
            generation = self._generation
        try:
            self._session = self.backend.open_session()
        except Exception as exc:
            self.status_callback(f"Audio capture unavailable: {exc}")
            return
        sources: list[tuple[str, CaptureDevice, SourceStream]] = []
        for channel in ("mic", "sys"):
            try:
                if channel == "mic":
                    candidates = self._session.mic_candidates(
                        self.config.mic_device, self.status_callback
                    )
                    # Only one microphone can be the speaker's; the rest are
                    # fallbacks tried in order until one opens.
                    keep_all = False
                else:
                    candidates = self._session.loopback_candidates(
                        self.config.output_device, self.status_callback
                    )
                    keep_all = len(candidates) > 1
                opened: list[tuple[CaptureDevice, SourceStream]] = []
                last_error: Exception | None = None
                for device in candidates:
                    try:
                        stream = self._session.open(device)
                    except Exception as exc:
                        # One dead endpoint among many is normal (virtual devices
                        # from Steam, NVIDIA and the like refuse to open). Only a
                        # total failure is worth reporting.
                        last_error = exc
                        log.debug("Could not open %s device %s: %s", channel, device.name, exc)
                        continue
                    opened.append((device, stream))
                    if not keep_all:
                        break
                if not opened:
                    raise last_error or RuntimeError(f"No usable {channel} device")
                for device, stream in opened:
                    self._streams.append(stream)
                    sources.append((channel, device, stream))
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
        for channel, device, stream in sources:
            thread = threading.Thread(
                target=self._capture_source,
                args=(channel, device, stream, loop, output, self._arbiter, generation),
                name=f"ambientqa-{channel}-{device.id}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        # _stop is set before taking the lifecycle lock so that a stop racing a
        # long start() begins winding the new session down the moment start()
        # releases the lock, rather than letting its capture run on.
        self._stop.set()
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop.set()
        # Stop every stream FIRST: a thread wedged in a blocking read() never
        # observes _stop on its own, and closing a stream (or tearing down the
        # backend) under a live reader is a native use-after-free. stop() is
        # safe from any thread and makes the blocked read return or raise.
        for stream in self._streams:
            try:
                stream.stop()
            except Exception as exc:
                log.debug("Error while stopping audio stream: %s", exc)
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        for stream in self._streams:
            try:
                stream.close()
            except Exception as exc:
                log.debug("Error while closing audio stream: %s", exc)
        self._streams.clear()
        self._arbiter = None
        with self._runner_lock:
            # Invalidate outstanding threads: one still blocked past the join
            # timeout must not touch the runner counts or status of whatever
            # session runs next.
            self._generation += 1
            self._runners.clear()
            # After stop() the sources ARE stopped; say so rather than leaving
            # the status bar reporting a channel that is no longer running.
            for state in (self.mic, self.loopback):
                state.active = False
                state.detail = "stopped"
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:
                log.debug("Error while closing audio session: %s", exc)
            self._session = None

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    def set_channel_enabled(self, channel: str, enabled: bool) -> None:
        """Enable or disable one logical input without closing its stream."""
        try:
            event = self._channel_enabled[channel]
        except KeyError:
            raise ValueError(f"Unknown audio channel: {channel}") from None
        with self._channel_state_lock:
            if event.is_set() == enabled:
                return
            self._channel_generation[channel] += 1
            if enabled:
                event.set()
            else:
                event.clear()

    def channel_enabled(self, channel: str) -> bool:
        """Return the runtime listening choice for ``mic`` or ``sys``."""
        try:
            event = self._channel_enabled[channel]
        except KeyError:
            raise ValueError(f"Unknown audio channel: {channel}") from None
        with self._channel_state_lock:
            return event.is_set()

    def _capture_source(
        self,
        channel: str,
        device: CaptureDevice,
        stream: SourceStream,
        loop: asyncio.AbstractEventLoop,
        output: DropOldestQueue[AudioFrame],
        arbiter: LoopbackArbiter | None,
        generation: int,
    ) -> None:
        state = self.mic if channel == "mic" else self.loopback
        name = device.name
        source_id = device.id
        entered = False
        if channel != "sys":
            # The arbiter picks between competing LOOPBACK endpoints. Letting it
            # judge the mic means a winning sys endpoint mutes the microphone
            # outright -- the mic loses every contest it was never entered in.
            arbiter = None
        # Resolve once per runner. The Event itself is persistent and
        # thread-safe, so later UI toggles are observed without a stream
        # restart or a dictionary lookup on every 25 ms frame.
        channel_enabled = self._channel_enabled[channel]
        try:
            rate = int(stream.rate)
            channels = int(stream.channels)
            native_frames = max(1, int(rate * self.config.frame_ms / 1000))
            # False means this thread belongs to a superseded session: stay
            # silent and die, announcing nothing over the live generation.
            entered = self._enter_source(state, name, generation)
            if entered and arbiter is None:
                self.status_callback(f"{channel} active: {name}")
            output_buffer = np.empty(0, dtype=np.float32)
            target_frames = int(16000 * self.config.frame_ms / 1000)
            resampler = None
            if rate != 16000:
                # Imported here, not at module top: soxr is only needed when a
                # stream arrives off-rate, and never on backends (parec) that
                # already deliver 16kHz.
                import soxr

                resampler = soxr.ResampleStream(
                    rate, 16000, 1, dtype="float32", quality="LQ"
                )
            # The generation test matters when this thread survived a stop()
            # whose stream teardown failed silently: the next start() clears
            # _stop, and without the check the zombie would resume pushing its
            # stale device's frames into the live session's queue.
            while entered and not self._stop.is_set() and generation == self._generation:
                with self._channel_state_lock:
                    read_generation = self._channel_generation[channel]
                samples = stream.read(native_frames)
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
                # Keep the check and queue handoff atomic with respect to a
                # channel toggle. If a read spans an off/on cycle, its original
                # samples are discarded even though the Event is set again by
                # the time the blocking read returns. If a toggle begins after
                # this section, the controller's boundary purge sees anything
                # handed off here before set_channel_enabled() can return.
                with self._channel_state_lock:
                    if (
                        read_generation != self._channel_generation[channel]
                        or not self._enabled.is_set()
                        or not channel_enabled.is_set()
                        or not forwarding
                    ):
                        output_buffer = np.empty(0, dtype=np.float32)
                        continue
                    while len(output_buffer) >= target_frames:
                        frame = output_buffer[:target_frames].copy()
                        output_buffer = output_buffer[target_frames:]
                        put_threadsafe(
                            loop,
                            output,
                            AudioFrame(channel, frame, time.time()),
                        )
        except Exception as exc:
            if self._stop.is_set() or generation != self._generation:
                # stop() unblocks readers by stopping their streams, so a read
                # error here IS the shutdown, not a device failure; warning
                # about it would cry wolf on every restart. A stale generation
                # is the same situation seen late: the next start() has already
                # cleared _stop, and a zombie announcing "Microphone
                # unavailable" would be reporting its long-dead stream over a
                # healthy session.
                log.debug("Capture thread for %s exited on stop: %s", name, exc)
            elif channel == "sys" and arbiter is not None:
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
                self._exit_source(state, str(exc), generation=generation)
        else:
            self._exit_source(state, generation=generation)
