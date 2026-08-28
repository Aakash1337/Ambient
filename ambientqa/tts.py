"""Voice mode: speak finished answers aloud without the pipeline hearing them.

The defence against self-hearing is deterministic muting, not audio-graph
surgery. That is a measured decision, not a guess: a second PipeWire
module-echo-cancel instance subtracting app playback from the sys-channel
monitor reached only 6-9 dB of cancellation on this hardware (2026-08-18,
webrtc AEC, every argument variant tried) -- residual roughly ten times the
capture pipeline's signal threshold, so Whisper would still transcribe our
own voice. Muting is exact instead: while ANY instance is speaking, every
instance drops capture frames for the speaker's muted channels.

Speaking windows are JSON files in a shared per-user directory, so the
muting is cross-instance by construction -- instance B never even segments
instance A's speech. The directory is deliberately NOT the instances
heartbeat directory: the status-bar counter counts every fresh file there,
and a speaking window must not read as an extra running instance.

Two backstops cover window-edge leakage (a mis-tuned tail, a crashed
speaker's stale window): the exact spoken string is recorded as answer-echo
text before playback starts, and each iteration of any surviving loop runs
through this same machinery again, so a leak damps out instead of
oscillating.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

import numpy as np

from .bus import DropOldestQueue
from .config import TtsConfig

log = logging.getLogger(__name__)

# Freshness horizon for window files, matching the heartbeat discipline in
# instances.py: long enough to survive scheduling hiccups between refresh
# ticks, short enough that a SIGKILLed speaker unmutes everyone in seconds.
WINDOW_TTL_S = 5.0

# A claim is deliberately separate from a speaking window.  It participates
# in the cross-process election, but carries no capture channels and therefore
# cannot make a slow synthesizer render every instance deaf.  Claims are
# refreshed while synthesis runs so a second instance cannot take the floor
# merely because Kokoro needed longer than WINDOW_TTL_S.
_ELECTION_DELAY_S = 0.1
_CLAIM_REFRESH_S = 1.0

# The platform player normally finishes at the end of the PCM stream. Its hard deadline is
# the exact audio duration plus this allowance for process startup, buffering,
# and scheduler delay.  A fixed deadline would incorrectly kill long answers.
_PLAYER_TIMEOUT_SLACK_S = 2.0
_PLAYER_POLL_S = 0.05
_PLAYER_STOP_GRACE_S = 0.5
_FEEDER_JOIN_TIMEOUT_S = 1.0

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_ORPHAN_FENCE_RE = re.compile(r"```.*\Z", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE_RE = re.compile(r"`+([^`]+)`+")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(\*|_)(.+?)\1")
_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]\s+)", re.MULTILINE)


def voice_followup_intent(text: str) -> str | None:
    """Recognise tightly-scoped requests about the most recent spoken answer.

    This deliberately is not a general conversational classifier. Repeat is a
    local content command in every mode; continue runs only in opt-in
    conversation mode. Both still require the controller to have a recent
    answer. The slightly odd first-person form is intentional: it is the exact
    way Whisper rendered "weren't you going to continue..." in a live failure,
    reversing the meaning before either question pass saw it.
    """
    compact = re.sub(r"[^a-z0-9']+", " ", text.casefold()).strip()

    repeat_patterns = (
        r"^(?:(?:can|could|will|would) you (?:please )?|please )?"
        r"repeat (?:that|the answer|the response|what you (?:just )?said|"
        r"your (?:last|previous) (?:answer|response))(?: again)?$",
        r"^(?:(?:can|could|will|would) you (?:please )?|please )?"
        r"(?:say|read) (?:that|the answer|the response|what you (?:just )?said) again$",
    )
    if any(re.fullmatch(pattern, compact) for pattern in repeat_patterns):
        return "repeat"

    # One deliberately exact exception for the live ASR inversion. Do not
    # generalise this to arbitrary negative statements: "don't continue" must
    # never cause precisely the action it prohibited.
    if re.fullmatch(
        r"i(?:'m| am) not going to continue reading(?: out)? the "
        r"(?:whole|entire) answer",
        compact,
    ):
        return "continue"

    continue_patterns = (
        # Anchored imperatives. The optional object allows the natural short
        # "continue reading", but a different object such as "the contract"
        # makes the whole match fail.
        r"^(?:please )?(?:continue|keep|finish) reading(?: out)?(?: the)? "
        r"(?:answer|response|rest|remainder|remaining (?:answer|response|part)|"
        r"whole answer|entire answer|bullets|options)$",
        r"^(?:please )?(?:continue|keep|finish) reading$",
        r"^(?:please )?finish(?: reading)?(?: out)? the (?:answer|response)$",
        r"^(?:please )?read(?: out)? the "
        r"(?:rest|remainder|remaining part|whole answer|entire answer|bullets|options)"
        r"(?: of the (?:answer|response|bullets|options))?$",
        r"^(?:can|could|will|would) you (?:please )?"
        r"(?:continue|keep|finish) reading(?: out)?(?: the)?(?: "
        r"(?:answer|response|whole answer|entire answer|rest|remainder|bullets|options))?$",
        r"^(?:can|could|will|would) you (?:please )?read(?: out)? "
        r"(?:(?:all of )?the )?"
        r"(?:rest|remainder|remaining part|whole answer|entire answer|bullets|options)"
        r"(?: of the (?:answer|response|bullets|options))?$",
        # The intended form that Whisper inverted in the reported session.
        r"^(?:are|aren't|were|weren't) you (?:going to )?"
        r"(?:continue|finish) reading(?: out)?(?: the)? "
        r"(?:answer|response|whole answer|entire answer|rest|remainder|bullets|options)$",
        r"^(?:i want|i'd like) you to (?:continue|finish) reading(?: out)?"
        r"(?: the)? (?:answer|response|whole answer|entire answer|rest|remainder)$",
    )
    if any(re.fullmatch(pattern, compact) for pattern in continue_patterns):
        return "continue"
    return None


def speakable(answer: str, mode: str = "first_line") -> str:
    """Reduce an answer to words worth saying aloud.

    Unlike ui.plain_text, fenced code is DROPPED, not preserved: code is
    unpronounceable, and its transcription-mangled tokens are exactly the
    text-drift that slips past answer-echo containment.
    """
    text = _FENCE_RE.sub(" ", answer)
    text = _ORPHAN_FENCE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\2", text)
    text = _ITALIC_RE.sub(r"\2", text)
    text = _HEADING_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if mode == "first_line":
        return lines[0]
    # Cue bullets and terse fragments read as run-ons without a boundary.
    return " ".join(
        line if line.endswith((".", "!", "?", ":", ";")) else line + "."
        for line in lines
    )


def _default_root() -> Path:
    # Per-user for the same reason as the instances directory: another user's
    # window files would mute this user's capture and never be prunable.
    if hasattr(os, "getuid"):
        suffix = str(os.getuid())
    else:  # pragma: no cover - Windows
        suffix = os.environ.get("USERNAME", "shared")
    return Path(tempfile.gettempdir()) / f"ambientqa-speaking-{suffix}"


class SpeakWindows:
    """Cross-instance speaking windows: who is talking, until when, muting what.

    Every failure path fails OPEN (nothing muted): a broken registry must
    degrade to today's behaviour, never to a silently deaf pipeline. The
    text-level answer-echo backstop covers what then leaks.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        scan_interval_s: float = 0.25,
    ) -> None:
        self.root = Path(root) if root is not None else _default_root()
        self._clock = clock
        self._own = self.root / f"{os.getpid()}.json"
        self._own_claim = self.root / f"{os.getpid()}.claim.json"
        self._scan_interval = scan_interval_s
        self._scanned_at = float("-inf")
        self._cached_entries: tuple[dict, ...] = ()

    def _publish(
        self,
        path: Path,
        until: float,
        channels: Sequence[str],
        since: float | None = None,
    ) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "since": self._clock() if since is None else since,
                    "until": until,
                    "channels": list(channels),
                }
            )
            # Keep the full target name in the temporary path.  In particular,
            # ``123.json`` and ``123.claim.json`` must not share ``123.tmp``.
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            return False
        # Own writes must be visible to the next muted() call immediately;
        # the scan cache exists for the per-frame hot path, not for us.
        self._scanned_at = float("-inf")
        return True

    def publish(
        self,
        until: float,
        channels: Sequence[str],
        since: float | None = None,
    ) -> bool:
        """Write (or refresh) this instance's audible speaking window.

        Refreshing keeps the mtime young, which is what lets a window outlive
        WINDOW_TTL_S while still expiring seconds after its owner dies
        mid-utterance.
        """
        return self._publish(self._own, until, channels, since)

    def publish_claim(self, until: float) -> bool:
        """Claim the speaker election without muting any capture channel."""
        return self._publish(self._own_claim, until, ())

    def clear_claim(self) -> None:
        with suppress(OSError):
            self._own_claim.unlink(missing_ok=True)
        self._scanned_at = float("-inf")

    def clear_window(self) -> None:
        with suppress(OSError):
            self._own.unlink(missing_ok=True)
        self._scanned_at = float("-inf")

    def clear(self) -> None:
        self.clear_window()
        self.clear_claim()

    def _entries(self, scan_now: float) -> list[dict]:
        try:
            paths = list(self.root.iterdir())
        except OSError:
            return []
        found: list[dict] = []
        for entry in paths:
            if entry.suffix != ".json":
                continue
            try:
                if scan_now - entry.stat().st_mtime > WINDOW_TTL_S:
                    entry.unlink(missing_ok=True)
                    continue
                data = json.loads(entry.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                pid = data.get("pid")
                has_since = "since" in data
                since = data.get("since", float("-inf"))
                until = data.get("until")
                channels = data.get("channels")
                # json.loads accepts values such as null, booleans and even
                # non-standard NaN/Infinity.  Treat every malformed shape as
                # inactive; registry corruption must never mute capture or
                # crash the hot path.
                if isinstance(pid, bool) or not isinstance(pid, int):
                    continue
                if has_since:
                    if isinstance(since, bool) or not isinstance(
                        since, (int, float)
                    ):
                        continue
                    if not math.isfinite(float(since)):
                        continue
                if isinstance(until, bool) or not isinstance(until, (int, float)):
                    continue
                if not math.isfinite(float(until)):
                    continue
                if not isinstance(channels, list) or not all(
                    isinstance(channel, str) for channel in channels
                ):
                    continue
            except (OSError, OverflowError, TypeError, ValueError):
                # A file vanishing mid-read or torn mid-write is normal here.
                continue
            # Keep expired-but-fresh entries in the cache: a frame may have
            # been captured during playback and reached the segmenter only
            # after the audible window ended.
            data["since"] = float(since)
            data["until"] = float(until)
            found.append(data)
        return found

    def muted(self, channel: str, now: float | None = None) -> bool:
        """Whether capture on this channel should be dropped right now.

        Called per 25 ms frame, so scans are cached for scan_interval_s.
        """
        scan_now = self._clock()
        frame_time = scan_now if now is None else now
        if scan_now - self._scanned_at >= self._scan_interval:
            self._scanned_at = scan_now
            self._cached_entries = tuple(self._entries(scan_now))
        return any(
            data["since"] <= frame_time < data["until"]
            and channel in data.get("channels", [])
            for data in self._cached_entries
        )

    def foreign_speaker(self, now: float | None = None) -> int | None:
        """Lowest other pid with a live claim/window, for speaker election."""
        now = self._clock() if now is None else now
        own = os.getpid()
        pids = [
            int(data["pid"])
            for data in self._entries(now)
            if isinstance(data.get("pid"), int)
            and data["pid"] != own
            and data["since"] <= now < data["until"]
        ]
        return min(pids) if pids else None


class SpeechEngine(Protocol):
    sample_rate: int

    def synthesize(self, text: str) -> bytes: ...


class PlayerInput(Protocol):
    def write(self, data: bytes) -> Any: ...

    def close(self) -> Any: ...


class PlayerProcess(Protocol):
    """Small subprocess-shaped contract used by Linux and macOS playback."""

    stdin: PlayerInput | None

    def poll(self) -> int | None: ...

    def terminate(self) -> Any: ...

    def kill(self) -> Any: ...

    def wait(self, timeout: float | None = None) -> int: ...


class EspeakEngine:
    """Formant fallback: robotic, instant, zero Python dependencies."""

    sample_rate = 22050

    def __init__(self, speed: float = 1.0) -> None:
        # espeak-ng speaks in words per minute; 175 is its default rate.
        self._wpm = max(80, min(450, int(175 * speed)))

    def synthesize(self, text: str) -> bytes:
        result = subprocess.run(
            ["espeak-ng", "--stdout", "-s", str(self._wpm), "--stdin"],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        # espeak-ng streams a WAV whose RIFF sizes are placeholders, which the
        # wave module rejects; the PCM simply follows the "data" chunk header.
        wav = result.stdout
        marker = wav.find(b"data")
        return wav[marker + 8 :] if marker >= 0 else b""


class KokoroEngine:
    """Kokoro-82M through kokoro-onnx, on CPU.

    CPU is deliberate: measured on this machine, Whisper large-v3-turbo plus
    the Ollama gate leave ~3.9 GB of the 16 GB card, and CPU synthesis runs
    ~5x realtime (2.6 s for 11.9 s of audio) -- negligible next to the
    3.5-17 s the answer itself takes.
    """

    sample_rate = 24000

    def __init__(
        self,
        model_path: str | Path,
        voices_path: str | Path,
        voice: str = "af_heart",
        speed: float = 1.0,
    ) -> None:
        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(str(model_path), str(voices_path))
        self._voice = voice
        self._speed = speed

    def synthesize(self, text: str) -> bytes:
        samples, rate = self._kokoro.create(
            text, voice=self._voice, speed=self._speed
        )
        self.sample_rate = int(rate)
        clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        return (clipped * 32767.0).astype(np.int16).tobytes()


def build_engine(
    config: TtsConfig,
    report: Callable[[str], None],
    base_dir: str | Path = ".",
) -> SpeechEngine:
    """Construct the configured engine, degrading to espeak-ng on any failure.

    Voice mode must start even when the neural model is missing or its
    dependencies broke on a Python upgrade -- a robotic voice beats a crash.
    """
    if config.engine == "kokoro":
        base = Path(base_dir)
        try:
            return KokoroEngine(
                base / config.model_path,
                base / config.voices_path,
                voice=config.voice,
                speed=config.speed,
            )
        except Exception as exc:
            report(f"Kokoro unavailable ({exc}); voice falls back to espeak-ng")
    return EspeakEngine(config.speed)


def _spawn_paplay(sample_rate: int) -> subprocess.Popen[bytes]:
    # stderr must never reach the terminal once Textual owns it, and never be
    # a pipe nobody drains; paplay says nothing of value on success anyway.
    return subprocess.Popen(
        [
            "paplay",
            "--raw",
            f"--rate={sample_rate}",
            "--channels=1",
            "--format=s16le",
            "--client-name=ambientqa-voice",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class _CoreAudioPlayer:
    """A ``Popen``-shaped raw PCM player backed by CoreAudio/PortAudio.

    ``SpeechOutput`` already feeds stdin from an isolated worker thread.  A
    blocking ``RawOutputStream.write`` therefore fits without changing its
    cancellation or timeout machinery, while abort() gives stop_current() the
    same immediate cut-off semantics as terminating paplay.
    """

    def __init__(self, sample_rate: int, sounddevice_module: Any | None = None) -> None:
        if sounddevice_module is None:
            from .backends.macos import _sounddevice_module

            sounddevice_module = _sounddevice_module()
        stream: Any | None = None
        try:
            stream = sounddevice_module.RawOutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            stream.start()
        except Exception as exc:
            if stream is not None:
                with suppress(Exception):
                    stream.close()
            raise RuntimeError(f"Unable to open the CoreAudio output: {exc}") from exc
        self._stream = stream
        self.stdin: PlayerInput | None = self
        self._state_lock = threading.Lock()
        self._done = threading.Event()
        self._closing = False
        self.returncode: int | None = None

    def write(self, data: bytes) -> Any:
        with self._state_lock:
            if self._closing:
                raise BrokenPipeError("CoreAudio player is closed")
        try:
            return self._stream.write(data)
        except Exception as exc:
            with self._state_lock:
                closing = self._closing
            if closing:
                raise BrokenPipeError("CoreAudio playback was stopped") from exc
            raise

    def close(self) -> None:
        self._finish(0, abort=False)

    def poll(self) -> int | None:
        with self._state_lock:
            return self.returncode

    def terminate(self) -> None:
        self._finish(-15, abort=True)

    def kill(self) -> None:
        self._finish(-9, abort=True)

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("CoreAudio", timeout)
        with self._state_lock:
            assert self.returncode is not None
            return self.returncode

    def _finish(self, returncode: int, *, abort: bool) -> None:
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
        try:
            stop = getattr(self._stream, "stop", None)
            action = getattr(self._stream, "abort", None) if abort else stop
            if callable(action):
                try:
                    try:
                        action(ignore_errors=False)
                    except TypeError:
                        action()
                except Exception:
                    # PortAudio abort is the preferred immediate unblock, but
                    # an output driver may reject it while still accepting a
                    # normal stop. Cleanup must remain best-effort and must not
                    # strand the feeder thread.
                    if not abort or not callable(stop):
                        raise
                    try:
                        stop(ignore_errors=False)
                    except TypeError:
                        stop()
        finally:
            with suppress(Exception):
                self._stream.close()
            with self._state_lock:
                self.returncode = returncode
            self._done.set()


def _spawn_platform_player(sample_rate: int) -> PlayerProcess:
    if sys.platform == "darwin":
        return _CoreAudioPlayer(sample_rate)
    return _spawn_paplay(sample_rate)


async def _synthesize_without_shutdown_block(
    engine: SpeechEngine,
    text: str,
) -> bytes:
    """Run local synthesis without enrolling it in asyncio's default executor.

    ``asyncio.run`` waits for every default-executor thread during shutdown,
    even after the awaiting task was cancelled. A wedged ONNX call would
    therefore make ``q`` hang and prevent the emergency launcher from taking
    over. This isolated daemon worker can finish naturally, but never owns the
    process lifetime. Completion is handed back to the event loop only while
    it still exists.
    """
    loop = asyncio.get_running_loop()
    completed: asyncio.Future[bytes] = loop.create_future()

    def work() -> None:
        try:
            pcm = engine.synthesize(text)
        except Exception as exc:
            def finish_error(error: Exception = exc) -> None:
                if not completed.done():
                    completed.set_exception(error)

            callback = finish_error
        else:
            def finish_result(result: bytes = pcm) -> None:
                if not completed.done():
                    completed.set_result(result)

            callback = finish_result
        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError:
            # The app already exited; a daemon worker must not resurrect it.
            pass

    threading.Thread(
        target=work,
        name="ambientqa-voice-synthesis",
        daemon=True,
    ).start()
    return await completed


@dataclass(slots=True)
class _SpeakJob:
    question_id: str
    text: str
    created_at: float


class SpeechOutput:
    """Serial speech: one voice, one utterance at a time, best-effort.

    Cards remain the authoritative record; speech that would arrive stale is
    dropped, not queued indefinitely.
    """

    def __init__(
        self,
        config: TtsConfig,
        engine: SpeechEngine,
        windows: SpeakWindows,
        report: Callable[[str], None],
        spawn_player: Callable[[int], PlayerProcess] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.engine = engine
        self.windows = windows
        self.report = report
        self.queue: DropOldestQueue[_SpeakJob] = DropOldestQueue(config.queue_size)
        self.muted = False
        self.speaking = False
        self._spawn_player = spawn_player or _spawn_platform_player
        self._player_name = (
            "CoreAudio" if spawn_player is None and sys.platform == "darwin" else "paplay"
        )
        self._clock = clock
        self._current: PlayerProcess | None = None
        self._state_lock = threading.Lock()
        self._cancel_generation = 0

    def enqueue(self, question_id: str, text: str) -> None:
        if self.muted:
            return
        dropped = self.queue.put_drop_oldest(
            _SpeakJob(question_id, text, self._clock())
        )
        if dropped is not None:
            log.info("voice: dropped queued answer %s", dropped.question_id)

    def stop_current(self, flush: bool = False) -> None:
        """Cancel pending synthesis/election and cut audio already playing.

        Safe from any thread; the worker owns and reaps the child.  The
        generation check closes the race where stop used to arrive while
        Kokoro was synthesizing, only for playback to begin afterwards.
        """
        if flush:
            self.queue.drain()
        with self._state_lock:
            self._cancel_generation += 1
            proc = self._current
        # A claim never represents audible output and is safe to withdraw
        # immediately.  Keep an audible window until the worker applies its
        # room-decay tail after terminating the player.
        self.windows.clear_claim()
        if proc is not None:
            try:
                running = proc.poll() is None
            except Exception as exc:
                self.report(f"voice: playback status failed: {exc}")
                running = True
            if running:
                try:
                    proc.terminate()
                except Exception as exc:
                    self.report(f"voice: could not terminate playback: {exc}")

    def _generation(self) -> int:
        with self._state_lock:
            return self._cancel_generation

    def _aborted(self, generation: int) -> bool:
        with self._state_lock:
            cancelled = generation != self._cancel_generation
        return cancelled or self.muted

    async def _refresh_claim(
        self,
        done: asyncio.Event,
        failed: asyncio.Event,
        generation: int,
    ) -> None:
        """Keep a slow synthesis visible to other voice instances."""
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=_CLAIM_REFRESH_S)
            except asyncio.TimeoutError:
                if self._aborted(generation):
                    self.windows.clear_claim()
                    return
                if not self.windows.publish_claim(
                    self._clock() + WINDOW_TTL_S
                ):
                    failed.set()
                    return

    def _start_player(
        self, generation: int
    ) -> PlayerProcess | None:
        """Atomically reject a cancellation or install the new child."""
        with self._state_lock:
            if generation != self._cancel_generation or self.muted:
                return None
            proc = self._spawn_player(self.engine.sample_rate)
            self._current = proc
            self.speaking = True
            return proc

    def _finish_player(self, proc: PlayerProcess) -> None:
        with self._state_lock:
            if self._current is proc:
                self._current = None
                self.speaking = False

    async def _reap_player(self, proc: PlayerProcess) -> None:
        """Stop and reap a live player without letting cleanup hang forever."""
        try:
            running = proc.poll() is None
        except Exception as exc:
            self.report(f"voice: playback status failed: {exc}")
            running = True
        if not running:
            return
        try:
            proc.terminate()
        except Exception as exc:
            self.report(f"voice: could not terminate playback: {exc}")
        try:
            await asyncio.to_thread(proc.wait, timeout=_PLAYER_STOP_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception as exc:
            self.report(f"voice: could not reap playback: {exc}")
        try:
            proc.kill()
        except Exception as exc:
            self.report(f"voice: could not kill playback: {exc}")
            return
        try:
            await asyncio.to_thread(proc.wait, timeout=_PLAYER_STOP_GRACE_S)
        except Exception as exc:
            self.report(f"voice: could not reap killed playback: {exc}")

    async def worker(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                job = await asyncio.wait_for(self.queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            try:
                await self._speak(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("voice: failed to speak answer %s", job.question_id)
                self.report(f"voice: failed to speak answer: {exc}")
            finally:
                self.queue.task_done()

    async def _speak(self, job: _SpeakJob) -> None:
        generation = self._generation()
        now = self._clock()
        if self._aborted(generation):
            return
        if now - job.created_at > self.config.max_age_s:
            log.info("voice: skipped stale answer %s", job.question_id)
            return
        if self.windows.foreign_speaker(now) is not None:
            log.info(
                "voice: another instance is speaking; skipped %s", job.question_id
            )
            return

        # Claim and elect BEFORE expensive synthesis.  The claim has no mute
        # channels, so capture continues while the neural voice is rendering.
        if not self.windows.publish_claim(now + WINDOW_TTL_S):
            self.report("voice: could not claim speaker election; skipped speech")
            return
        claim_done = asyncio.Event()
        claim_failed = asyncio.Event()
        refresher = asyncio.create_task(
            self._refresh_claim(claim_done, claim_failed, generation)
        )
        proc: PlayerProcess | None = None
        feeder: asyncio.Task[None] | None = None
        audio_started = False
        window_published = False
        try:
            # Give simultaneous claim writers one scheduling window, then let
            # the lower pid keep the floor.  A cancellation/mute during this
            # wait is checked before synthesis starts.
            await asyncio.sleep(_ELECTION_DELAY_S)
            if self._aborted(generation):
                return
            other = self.windows.foreign_speaker(self._clock())
            if other is not None and other < os.getpid():
                log.info(
                    "voice: instance %s won the speaker election; skipped %s",
                    other,
                    job.question_id,
                )
                return

            try:
                pcm = await _synthesize_without_shutdown_block(
                    self.engine, job.text
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.report(f"voice: synthesis failed: {exc}")
                return
            finally:
                claim_done.set()
                await refresher

            # stop_current(), mute, or a failed claim refresh while synthesis
            # was in flight all veto playback.
            if self._aborted(generation) or claim_failed.is_set() or not pcm:
                if claim_failed.is_set():
                    self.report("voice: speaker claim expired; skipped playback")
                elif not self._aborted(generation) and not pcm:
                    self.report("voice: synthesis returned no audio")
                return
            if self.engine.sample_rate <= 0:
                self.report("voice: invalid speech sample rate; skipped playback")
                return

            duration = len(pcm) / 2 / self.engine.sample_rate
            tail = self.config.gate_tail_s
            audio_since = self._clock()
            deadline = audio_since + duration + tail
            # Transition from the non-muting claim to an audible window before
            # spawning the platform player. If the registry cannot be written, stay silent
            # rather than risk feeding our own answer back into the pipeline.
            if not self.windows.publish(
                deadline,
                self.config.mute_channels,
                since=audio_since,
            ):
                self.report(
                    "voice: could not publish speaking window; skipped playback"
                )
                return
            window_published = True
            self.windows.clear_claim()

            try:
                proc = self._start_player(generation)
            except FileNotFoundError:
                self.report(
                    f"voice: {self._player_name} not found; cannot play speech"
                )
                return
            except Exception as exc:
                self.report(f"voice: playback failed to start: {exc}")
                return
            if proc is None:
                return
            audio_started = True
            feeder = asyncio.create_task(asyncio.to_thread(self._feed, proc, pcm))

            loop = asyncio.get_running_loop()
            timeout_at = loop.time() + duration + _PLAYER_TIMEOUT_SLACK_S
            timed_out = False
            status_failed = False
            feed_failed = False
            while True:
                if self._aborted(generation):
                    break
                if feeder.done() and not feed_failed:
                    try:
                        feed_error = feeder.exception()
                    except asyncio.CancelledError:
                        feed_error = None
                    if feed_error is not None:
                        self.report(f"voice: audio feed failed: {feed_error}")
                        feed_failed = True
                        break
                try:
                    returncode = proc.poll()
                except Exception as exc:
                    self.report(f"voice: playback status failed: {exc}")
                    status_failed = True
                    break
                if returncode is not None:
                    break
                remaining = timeout_at - loop.time()
                if remaining <= 0:
                    timed_out = True
                    self.report(
                        "voice: playback timed out "
                        f"after {duration + _PLAYER_TIMEOUT_SLACK_S:.2f}s"
                    )
                    break
                # Refresh keeps the mtime fresh for windows longer than the
                # TTL.  The short rolling lease prevents a killed worker from
                # muting every instance indefinitely.
                if not self.windows.publish(
                    max(deadline, self._clock() + 1.5),
                    self.config.mute_channels,
                    since=audio_since,
                ):
                    self.report("voice: could not refresh speaking window")
                    status_failed = True
                    break
                await asyncio.sleep(min(_PLAYER_POLL_S, remaining))

            await self._reap_player(proc)

            if feeder is not None:
                try:
                    await asyncio.wait_for(
                        feeder, timeout=_FEEDER_JOIN_TIMEOUT_S
                    )
                except asyncio.TimeoutError:
                    self.report("voice: audio feeder did not stop")
                    feeder.cancel()
                except Exception as exc:
                    self.report(f"voice: audio feed failed: {exc}")

            if (
                not self._aborted(generation)
                and not timed_out
                and not status_failed
                and not feed_failed
            ):
                try:
                    returncode = proc.poll()
                except Exception as exc:
                    self.report(f"voice: playback status failed: {exc}")
                else:
                    if returncode not in (None, 0):
                        self.report(
                            f"voice: {self._player_name} exited with status {returncode}"
                        )
        except FileNotFoundError:
            self.report(f"voice: {self._player_name} not found; cannot play speech")
        finally:
            claim_done.set()
            if not refresher.done():
                refresher.cancel()
                with suppress(asyncio.CancelledError):
                    await refresher
            self.windows.clear_claim()
            if proc is not None:
                await self._reap_player(proc)
                self._finish_player(proc)
            if feeder is not None and not feeder.done():
                feeder.cancel()
                with suppress(asyncio.CancelledError):
                    await feeder
            if audio_started:
                # The room is still ringing with our audio: hold the mute for
                # the tail, then let the window expire on its own.
                self.windows.publish(
                    self._clock() + self.config.gate_tail_s,
                    self.config.mute_channels,
                    since=audio_since,
                )
            elif window_published:
                # A speaking window may have been published just before a
                # concurrent stop won the spawn lock.  Do not leave that
                # phantom window muting capture.
                self.windows.clear_window()

    @staticmethod
    def _feed(proc: PlayerProcess, pcm: bytes) -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(pcm)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            # The player was stopped mid-utterance; nothing to salvage.
            pass

    def close(self) -> None:
        self.stop_current(flush=True)
        proc = self._current
        if proc is not None:
            with suppress(Exception):
                proc.wait(timeout=1.0)
        self.windows.clear()
