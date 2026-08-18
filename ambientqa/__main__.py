"""Application entry point and bounded five-stage pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from textual._context import active_app

from .answer import ClaudeAnswerer
from .audio import AudioCapture
from .audio_devices import AudioDeviceSession, MeterSession
from .backends import get_backend
from .backends.base import CaptureDevice
from .bus import AnswerResult, AudioFrame, DropOldestQueue, Transcript, Utterance
from .config import Config, load_config
from .config_write import set_audio_device, set_context_profile
from .continuity import ContinuityMerger
from .context import TranscriptContext, token_set_ratio
from .gate import QuestionGate
from .logging_ import SessionLogger
from .profile import Profile, load_profile
from .segmenter import UtteranceSegmenter, segment_worker
from .stt import WhisperTranscriber, stt_worker
from .ui import AmbientQAApp

log = logging.getLogger(__name__)


class _EnergyVAD:
    """Last-resort VAD used only when Silero cannot initialize."""

    def __call__(self, audio: np.ndarray) -> float:
        return 1.0 if float(np.sqrt(np.mean(np.square(audio)))) > 0.012 else 0.0


@dataclass(slots=True)
class _AnswerJob:
    transcript: Transcript
    query: str
    context: list[str]
    reason: str
    gate_latency_ms: float


@dataclass(slots=True)
class _PendingSystem:
    release_at: float
    transcript: Transcript


def _close_abandoned_meter_session(task: asyncio.Task[MeterSession]) -> None:
    """Close a meter session whose awaiting coroutine was cancelled mid-open.

    session.close() joins reader threads and reaps subprocesses, so it runs on
    its own thread rather than the event loop this callback fires on.
    """
    if task.cancelled() or task.exception() is not None:
        return
    session = task.result()
    threading.Thread(
        target=session.close,
        name="ambientqa-abandoned-meter-close",
        daemon=True,
    ).start()


class AmbientController:
    def __init__(
        self,
        config: Config,
        config_path: str | Path = "config.toml",
    ) -> None:
        self.config = config
        self.config_path = Path(config_path)
        self.paused = False
        self.stop = asyncio.Event()
        self.frames: DropOldestQueue[AudioFrame] = DropOldestQueue(
            config.audio.queue_size
        )
        self.utterances: DropOldestQueue[Utterance] = DropOldestQueue(
            config.stt.queue_size
        )
        self.transcripts: DropOldestQueue[Transcript] = DropOldestQueue(
            config.gate.queue_size
        )
        self.answers: DropOldestQueue[_AnswerJob] = DropOldestQueue(
            config.answer.queue_size
        )
        self.context = TranscriptContext(
            echo_window_s=config.gate.echo_window_s,
            echo_ratio=config.gate.echo_ratio,
        )
        self.continuity = ContinuityMerger(config.merge)
        self.warnings: list[str] = []
        self.status_note = "initializing"
        self._loop_thread_id: int | None = None
        self._ignore_before = 0.0
        # Built once and shared: capture and the device picker must agree on
        # which platform stack they are talking to.
        self.backend = get_backend(config.audio)
        self.capture = AudioCapture(config.audio, self._report, backend=self.backend)
        self.transcriber = WhisperTranscriber(config.stt, self._report)
        self.gate = QuestionGate(config.gate, self._report)
        self.answerer = ClaudeAnswerer(
            config.answer,
            self._report,
            delta_callback=self._answer_delta,
        )
        self.profile: Profile | None = None
        self.logger = SessionLogger(config.ui.log_dir)
        self.app = AmbientQAApp(
            self,
            config.ui.show_transcripts,
            config.ui.status_interval_s,
            feed_direction=config.ui.feed_direction,
            log_dir=config.ui.log_dir,
        )
        if config.context.enabled and config.context.profile:
            self._apply_profile(
                load_profile(
                    self._resolve_profile_path(config.context.profile),
                    self._report,
                )
            )
        self.last_transcript: Transcript | None = None
        self.answer_count = 0
        self.estimated_tokens = 0
        self._tasks: list[asyncio.Task[Any]] = []
        self._ui_tasks: set[asyncio.Task[Any]] = set()
        self._force_lock = asyncio.Lock()
        self._device_lock = asyncio.Lock()
        self._capture_loop: asyncio.AbstractEventLoop | None = None
        self._pending_system: deque[_PendingSystem] = deque()
        # Completed (query, answer) pairs, oldest first. This -- not the raw
        # transcript -- is what lets a follow-up like "elaborate on the second
        # method" resolve: the methods exist only in the answer prose.
        # maxlen=0 (history_turns = 0) drops every append: history disabled.
        self._qa_history: deque[tuple[str, str]] = deque(
            maxlen=config.answer.history_turns
        )
        self._open_answer_jobs: dict[str, _AnswerJob] = {}
        self._gate_tasks: set[asyncio.Task[Any]] = set()
        self._verify_tasks: set[asyncio.Task[Any]] = set()
        # Rejections from the JUDGMENT stages only (policy shape-check and the
        # semantic gate). Mechanical rejections -- filler, dedupe, echo, tags,
        # pause -- are not misses and never enter the sweep.
        self._recent_rejections: deque[Transcript] = deque(maxlen=24)
        # Audits run strictly after their answer is on screen, and never more
        # than one at a time: they must not compete with primary answers for
        # the CLI process budget.
        self._verify_semaphore = asyncio.Semaphore(1)
        self._gate_semaphore = asyncio.Semaphore(config.gate.max_concurrent)
        # The sys hold exists so a matching mic copy can win the echo contest,
        # whichever STT finishes first. It costs ~2.5s on EVERY question from the
        # other speaker, so it only earns its keep when the mic copy would be
        # judged just as freely. Under "explicit" the mic copy of a bled-through
        # question is either accepted identically or dropped, and near-duplicate
        # dedupe already covers the overlap -- so the delay buys nothing.
        self._hold_system_for_echo = (
            config.gate.channel_policy.get("mic", "full") == "full"
        )

        try:
            # UtteranceSegmenter eagerly probes Silero, allowing graceful fallback.
            self.segmenter = UtteranceSegmenter(config.audio)
        except Exception as exc:
            self._report(f"Silero VAD unavailable; using energy fallback: {exc}")
            self.segmenter = UtteranceSegmenter(
                config.audio, vad_factory=lambda: _EnergyVAD()
            )

    def _report(self, message: str) -> None:
        is_normal = (
            message.startswith("mic active")
            or message.startswith("sys active")
            or message.startswith("Whisper ready")
            or message.startswith("Audio device selected")
            or message.startswith("Profile active")
            or message.startswith("Profile disabled")
        )
        if is_normal:
            self.status_note = message
            log.info(message)
            return
        log.warning(message)
        self.warnings.append(message)
        self.warnings[:] = self.warnings[-5:]
        if not self.app.is_running:
            return
        if self._loop_thread_id is not None and threading.get_ident() != self._loop_thread_id:
            with suppress(Exception):
                self.app.call_from_thread(self.app.add_warning, message)
        else:
            self.app.call_later(self.app.add_warning, message)

    def _answer_delta(self, question_id: str, delta: str) -> None:
        """Route one process's delta only to the card bound to its question id."""
        try:
            self.app.append_answer_delta(question_id, delta)
        except Exception as exc:
            self._report(f"Unable to update streaming answer card: {exc}")

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        self.capture.set_enabled(not self.paused)
        self.frames.drain()
        self.utterances.drain()
        for transcript in self.transcripts.drain():
            self.logger.append(
                {
                    **self._base_record(transcript),
                    "gate": False,
                    "gate_reason": "paused",
                    "answer": None,
                    "latencies_ms": {"stt": transcript.latency_ms},
                }
            )
        while self._pending_system:
            transcript = self._pending_system.popleft().transcript
            self.logger.append(
                {
                    **self._base_record(transcript),
                    "gate": False,
                    "gate_reason": "paused",
                    "answer": None,
                    "latencies_ms": {"stt": transcript.latency_ms},
                }
            )
        for transcript in self.continuity.flush_all():
            self.logger.append(
                {
                    **self._base_record(transcript),
                    "gate": False,
                    "gate_reason": "paused",
                    "answer": None,
                    "latencies_ms": {"stt": transcript.latency_ms},
                }
            )
        self.segmenter.reset_all()
        if not self.paused:
            # Ignore a transcription that started before the resume boundary.
            self._ignore_before = time.time()
        return self.paused

    async def _restart_capture(self) -> None:
        loop = self._capture_loop or asyncio.get_running_loop()
        self._capture_loop = loop
        # Shielded: a worker cancellation arriving while the executor call is
        # still queued would otherwise silently discard the restart and leave
        # capture off. The start itself is serialised by the capture's own
        # lifecycle lock, so letting it finish unobserved is always safe.
        await asyncio.shield(
            asyncio.to_thread(
                self.capture.start,
                loop,
                self.frames,
                enabled=not self.paused,
            )
        )

    async def open_audio_devices(self) -> MeterSession:
        await self._device_lock.acquire()
        active_mic = (
            self.capture.mic.detail
            if self.capture.mic.active
            else self.config.audio.mic_device
        )
        active_loopback = (
            self.capture.loopback.detail
            if self.capture.loopback.active
            else self.config.audio.output_device
        )
        try:
            await asyncio.to_thread(self.capture.stop)
            self.frames.drain()
            self.utterances.drain()
            self.segmenter.reset_all()
            # The open spawns a live meter stream per endpoint on an executor
            # thread, and cancelling this coroutine cannot cancel that thread:
            # unshielded, the finished session's return value is simply
            # discarded and its streams meter forever with no owner. Shielding
            # lets the thread finish either way; if this coroutine has already
            # been cancelled, the completed session is closed instead of kept.
            opening = asyncio.ensure_future(
                asyncio.to_thread(
                    AudioDeviceSession.open,
                    self.backend,
                    active_mic=active_mic,
                    active_loopback=active_loopback,
                )
            )
            try:
                return await asyncio.shield(opening)
            except BaseException:
                opening.add_done_callback(_close_abandoned_meter_session)
                raise
        except BaseException:
            try:
                await self._restart_capture()
            finally:
                self._device_lock.release()
            raise

    async def close_audio_devices(
        self,
        session: MeterSession,
        selected: CaptureDevice | None,
    ) -> None:
        try:
            if selected is not None:
                key = "mic_device" if selected.kind == "mic" else "output_device"
                await asyncio.to_thread(
                    set_audio_device,
                    self.config_path,
                    key,
                    selected.name,
                )
                setattr(self.config.audio, key, selected.name)
                self._report(f"Audio device selected: {selected.name}")
        finally:
            try:
                # Shielded for the same reason as the open side: a cancellation
                # landing while close is still queued on the executor would
                # skip it entirely, leaving every meter stream (a live parec
                # child per endpoint) capturing for the life of the process.
                await asyncio.shield(asyncio.to_thread(session.close))
            finally:
                try:
                    await self._restart_capture()
                finally:
                    self._device_lock.release()

    def cycle_gate_mode(self) -> str:
        modes = ["strict", "balanced", "eager"]
        index = (modes.index(self.config.gate.mode) + 1) % len(modes)
        self.gate.set_mode(modes[index])
        return modes[index]

    def _resolve_profile_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.config_path.parent / path

    def _apply_profile(self, profile: Profile | None) -> None:
        self.profile = profile
        self.transcriber.set_profile(profile)
        self.gate.set_profile(profile)
        self.answerer.set_profile(profile)

    def profile_choices(self) -> tuple[list[str], str]:
        root = self.config_path.parent / "profiles"
        choices: list[str] = []
        if root.is_dir():
            for path in sorted(root.glob("*.md"), key=lambda item: item.name.casefold()):
                choices.append(path.relative_to(self.config_path.parent).as_posix())
        active = ""
        if self.profile is not None and self.config.context.profile:
            configured = self._resolve_profile_path(self.config.context.profile).resolve()
            active = next(
                (
                    choice
                    for choice in choices
                    if self._resolve_profile_path(choice).resolve() == configured
                ),
                self.config.context.profile,
            )
        return choices, active

    async def select_profile(self, value: str) -> str:
        profile: Profile | None = None
        if value and self.config.context.enabled:
            profile = await asyncio.to_thread(
                load_profile,
                self._resolve_profile_path(value),
                self._report,
            )
        await asyncio.to_thread(set_context_profile, self.config_path, value)
        self.config.context.profile = value
        self._apply_profile(profile)
        if profile is not None:
            self._report(f"Profile active: {profile.name}")
            return profile.name
        if value and not self.config.context.enabled:
            self._report("Profile selected but context.enabled is false")
            return "none"
        if not value:
            self._report("Profile disabled")
        return "none"

    def _source_status(self, state: Any) -> str:
        """Report a source as SILENT rather than "on" once it has gone deaf.

        A loopback pinned to an endpoint the call is not playing through opens
        without error and reads "on" forever. That is how a whole session gets
        recorded with the other speaker missing and no visible sign of it.
        """
        if not state.active:
            return "off"
        quiet = state.silent_for()
        if quiet is not None and quiet >= self.config.audio.silent_source_warn_s:
            return f"SILENT {int(quiet)}s ⚠"
        return "on"

    def status_text(self) -> str:
        listening = "⏸ PAUSED" if self.paused else "● listening"
        mic = self._source_status(self.capture.mic)
        loopback = self._source_status(self.capture.loopback)
        queues = (
            f"{self.frames.qsize()}/{self.utterances.qsize()}/"
            f"{self.transcripts.qsize()}/{self.answers.qsize()}"
        )
        warning = f"  ⚠ {self.warnings[-1]}" if self.warnings else ""
        profile_name = self.profile.name if self.profile is not None else "none"
        return (
            f"{listening}  mic:{mic} sys:{loopback}  whisper:{self.transcriber.device}  "
            f"gate:{self.config.gate.mode}  profile:{profile_name}  queues:{queues}  "
            f"answers:{self.answerer.in_flight} active/{self.answer_count} done  "
            f"~tokens:{self.estimated_tokens}{warning}"
        )

    async def _log(self, record: dict[str, Any]) -> None:
        await asyncio.to_thread(self.logger.append, record)

    def _base_record(self, transcript: Transcript) -> dict[str, Any]:
        return {
            "id": transcript.utterance_id,
            "timestamp": transcript.timestamp,
            "channel": transcript.channel,
            "text": transcript.text,
        }

    # Reasons meaning "this reached judgment and got voted down" -- the only
    # rejections a wrongly-dropped question can hide behind. Both live misses
    # so far were exactly these: an ollama_reject on a real question and a
    # not_a_direct_question on a command-form ask.
    _SWEEP_REASONS = frozenset(
        {"not_a_direct_question", "ollama_reject", "ollama_unavailable"}
    )

    async def _log_rejection(self, transcript: Transcript, reason: str) -> None:
        if reason in self._SWEEP_REASONS:
            self._recent_rejections.append(transcript)
        record = self._base_record(transcript)
        record.update(
            {
                "gate": False,
                "gate_reason": reason,
                "answer": None,
                "latencies_ms": {"stt": transcript.latency_ms},
            }
        )
        await self._log(record)

    async def _complete_answer(self, job: _AnswerJob, result: AnswerResult) -> None:
        self.answer_count += 1
        self.estimated_tokens += int(len(result.answer.split()) * 1.35)
        if result.status == "ok":
            now = time.time()
            self.gate.mark_answered(job.transcript.text, now)
            # Remember the answer prose too: the user rehearsing it aloud would
            # otherwise be gated as a fresh question and answered again.
            self.gate.mark_answer_text(result.answer, now)
            self._qa_history.append((job.query, result.answer))
            if self.config.answer.verify == "always":
                task = asyncio.create_task(self._verify_answer(job, result))
                self._verify_tasks.add(task)
                task.add_done_callback(self._verify_tasks.discard)
        try:
            self.app.resolve_answer(result)
        except Exception as exc:
            self._report(f"Unable to update answer card: {exc}")
        record = self._base_record(job.transcript)
        record.update(
            {
                "gate": True,
                "gate_reason": job.reason,
                "query": job.query,
                "answer": result.answer,
                "answer_status": result.status,
                "web_lookup": result.searched,
                "latencies_ms": {
                    "stt": job.transcript.latency_ms,
                    "gate": job.gate_latency_ms,
                    "answer": result.latency_ms,
                },
            }
        )
        # Keep record commit and ownership removal atomic with respect to task
        # cancellation; a single local JSONL append is brief.
        self.logger.append(record)
        self._open_answer_jobs.pop(job.transcript.utterance_id, None)

    def _replace_history_answer(self, query: str, revision: str) -> None:
        # Follow-ups resolve against the history, so a revised answer must
        # replace the wrong one there -- otherwise "elaborate on that" expands
        # the answer the audit just retracted.
        for index in range(len(self._qa_history) - 1, -1, -1):
            if self._qa_history[index][0] == query:
                self._qa_history[index] = (query, revision)
                return

    async def _verify_answer(self, job: _AnswerJob, result: AnswerResult) -> None:
        try:
            async with self._verify_semaphore:
                started = time.perf_counter()
                revision = await self.answerer.verify(
                    job.transcript.utterance_id,
                    job.transcript.text,
                    job.query,
                    result.answer,
                    # A fresh, wider context snapshot: catching what the fast
                    # path missed is the audit's entire purpose.
                    self.context.rendered(self.config.answer.verify_context_turns),
                    list(self._qa_history),
                    channel=job.transcript.channel,
                )
            if revision is None:
                return
            latency = (time.perf_counter() - started) * 1000
            now = time.time()
            self.gate.mark_answer_text(revision, now)
            self._replace_history_answer(job.query, revision)
            try:
                self.app.resolve_answer(
                    AnswerResult(
                        job.transcript.utterance_id,
                        job.query,
                        revision,
                        "revised",
                        latency,
                    )
                )
            except Exception as exc:
                self._report(f"Unable to update revised answer card: {exc}")
            record = self._base_record(job.transcript)
            record.update(
                {
                    "gate": True,
                    "gate_reason": "verify_revision",
                    "query": job.query,
                    "answer": revision,
                    "answer_status": "revised",
                    "latencies_ms": {"verify": latency},
                }
            )
            await self._log(record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Detached task: best-effort by design, but never silently broken.
            log.exception("Answer verification failed")
            self._report(f"Answer verification failed: {exc}")

    async def _sweep_worker(self) -> None:
        """Detection second pass: recover questions the gate wrongly rejected."""
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(
                    self.stop.wait(), timeout=self.config.answer.sweep_interval_s
                )
                return
            except asyncio.TimeoutError:
                pass
            if self.paused or not self._recent_rejections:
                continue
            candidates = list(self._recent_rejections)
            self._recent_rejections.clear()
            try:
                answered = [query for query, _answer in self._qa_history] + [
                    job.query for job in self._open_answer_jobs.values()
                ]
                missed = await self.answerer.detect_missed(
                    [(t.channel, t.text) for t in candidates],
                    self.context.rendered(self.config.answer.verify_context_turns),
                    answered,
                )
                for index, question in missed:
                    original = candidates[index]
                    recovered_id = f"{original.utterance_id}-recovered"
                    recovered = Transcript(
                        original.channel,
                        original.text,
                        original.timestamp,
                        recovered_id,
                        original.latency_ms,
                    )
                    try:
                        await self.app.add_question(recovered_id, question)
                    except Exception as exc:
                        self._report(f"Unable to add recovered answer card: {exc}")
                    await self._enqueue_answer(
                        _AnswerJob(
                            recovered,
                            question,
                            self.context.rendered(
                                self.config.answer.context_turns
                            ),
                            "second_pass_recovery",
                            0.0,
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Missed-question sweep failed")
                self._report(f"Missed-question sweep failed: {exc}")

    async def _answer_worker(self) -> None:
        while not self.stop.is_set():
            try:
                job = await asyncio.wait_for(self.answers.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            try:
                # History is read here, at answer time, not when the job was
                # queued: a follow-up asked seconds after an answer completes
                # must see that answer even if it was enqueued first.
                result = await self.answerer.answer(
                    job.transcript.utterance_id,
                    job.query,
                    job.context,
                    history=list(self._qa_history),
                    channel=job.transcript.channel,
                )
                await self._complete_answer(job, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Answer worker failed")
                fallback = AnswerResult(
                    job.transcript.utterance_id,
                    job.query,
                    f"answer failed: {exc}",
                    "error",
                    0.0,
                )
                await self._complete_answer(job, fallback)
            finally:
                self.answers.task_done()

    async def _enqueue_answer(self, job: _AnswerJob) -> None:
        self._open_answer_jobs[job.transcript.utterance_id] = job
        dropped = self.answers.put_drop_oldest(job)
        if dropped is not None:
            result = AnswerResult(
                dropped.transcript.utterance_id,
                dropped.query,
                "dropped because the answer queue was full",
                "dropped",
                0.0,
            )
            await self._complete_answer(dropped, result)

    async def _process_transcript(self, transcript: Transcript) -> None:
        if not self.context.add(transcript):
            await self._log_rejection(transcript, "cross_channel_echo")
            return
        self.last_transcript = transcript
        try:
            await self.app.add_transcript(transcript)
        except Exception as exc:
            self._report(f"Unable to update transcript pane: {exc}")
        policy = self.config.gate.channel_policy.get(transcript.channel, "full")
        if policy == "off":
            # Context-only channel. It has already been added to the context
            # window above, so it still resolves referents for the channels that
            # do answer -- it just cannot become a question itself.
            await self._log_rejection(transcript, "channel_not_answered")
            return
        # Both context views are snapshotted HERE, on the ordered path, so each
        # question is judged and answered against the conversation as it stood
        # when it was asked -- not as it stands whenever its task happens to run.
        background = self.context.rendered(
            self.config.gate.context_turns, exclude_latest=True
        )
        answer_context = self.context.rendered(
            self.config.answer.context_turns, exclude_latest=True
        )
        # Everything above is ordered and fast. Gating is a ~900ms network call,
        # and awaiting it here stalls the whole consumer loop: a second question
        # arriving mid-call could not reach the answer queue until the first had
        # been judged, so its answer started a full gate late for no reason.
        task = asyncio.create_task(
            self._gate_and_enqueue(transcript, background, answer_context, policy)
        )
        self._gate_tasks.add(task)
        task.add_done_callback(self._gate_tasks.discard)

    async def _gate_and_enqueue(
        self,
        transcript: Transcript,
        background: list[str],
        answer_context: list[str],
        policy: str = "full",
    ) -> None:
        try:
            async with self._gate_semaphore:
                decision = await self.gate.evaluate(transcript, background, policy)
            if self.paused:
                await self._log_rejection(transcript, "paused_during_gate")
                return
            if not decision.accepted:
                record = self._base_record(transcript)
                record.update(
                    {
                        "gate": False,
                        "gate_reason": decision.reason,
                        "answer": None,
                        "latencies_ms": {
                            "stt": transcript.latency_ms,
                            "gate": decision.latency_ms,
                        },
                    }
                )
                await self._log(record)
                return
            try:
                await self.app.add_question(transcript.utterance_id, decision.query)
            except Exception as exc:
                self._report(f"Unable to add answer card: {exc}")
            await self._enqueue_answer(
                _AnswerJob(
                    transcript,
                    decision.query,
                    answer_context,
                    decision.reason,
                    decision.latency_ms,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # This runs detached, so an unhandled error would otherwise surface
            # only as a question that silently never got an answer card.
            log.exception("Gate task failed")
            self._report(f"Gate error: {exc}")

    async def _ingest_transcript(self, transcript: Transcript) -> None:
        for merged in self.continuity.push(transcript):
            await self._process_transcript(merged)

    async def _flush_continuity_transcripts(self) -> None:
        for transcript in self.continuity.flush_expired():
            await self._process_transcript(transcript)

    async def _flush_system_transcripts(self, force: bool = False) -> None:
        now = time.monotonic()
        while self._pending_system and (
            force or now >= self._pending_system[0].release_at
        ):
            pending = self._pending_system.popleft()
            await self._ingest_transcript(pending.transcript)

    async def _consume_transcripts(self) -> None:
        while not self.stop.is_set():
            await self._flush_system_transcripts()
            await self._flush_continuity_transcripts()
            try:
                transcript = await asyncio.wait_for(self.transcripts.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                if self.paused or transcript.timestamp < self._ignore_before:
                    await self._log_rejection(transcript, "paused")
                    continue
                if transcript.channel == "sys":
                    # If a mic copy is already in context, suppress immediately.
                    if self.context.is_cross_channel_echo(transcript):
                        await self._log_rejection(transcript, "cross_channel_echo")
                    elif self._hold_system_for_echo:
                        self._pending_system.append(
                            _PendingSystem(
                                time.monotonic()
                                + self.config.gate.echo_window_s
                                + transcript.latency_ms / 1000.0,
                                transcript,
                            )
                        )
                    else:
                        await self._ingest_transcript(transcript)
                    continue

                # Hold sys transcripts briefly so a matching mic copy always wins,
                # regardless of which STT result happened to arrive first.
                survivors: deque[_PendingSystem] = deque()
                for pending in self._pending_system:
                    duplicate = (
                        abs(transcript.timestamp - pending.transcript.timestamp)
                        <= self.config.gate.echo_window_s
                        and token_set_ratio(transcript.text, pending.transcript.text)
                        >= self.config.gate.echo_ratio
                    )
                    if duplicate:
                        await self._log_rejection(
                            pending.transcript, "cross_channel_echo"
                        )
                    else:
                        survivors.append(pending)
                self._pending_system = survivors
                await self._ingest_transcript(transcript)
            except Exception as exc:
                log.exception("Transcript pipeline failed")
                self._report(f"Transcript pipeline error: {exc}")
            finally:
                self.transcripts.task_done()

    async def force_answer_last(self) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._ui_tasks.add(task)
        try:
            if self._force_lock.locked() or self.stop.is_set():
                self.app.notify("A force-answer action is already being handled")
                return
            async with self._force_lock:
                transcript = self.last_transcript
                if transcript is None:
                    self.app.notify("No utterance to answer yet")
                    return
                forced_id = f"{transcript.utterance_id}-forced-{int(time.time() * 1000)}"
                forced = Transcript(
                    transcript.channel,
                    transcript.text,
                    transcript.timestamp,
                    forced_id,
                    transcript.latency_ms,
                )
                await self.app.add_question(forced_id, transcript.text)
                await self._enqueue_answer(
                    _AnswerJob(
                        forced,
                        transcript.text,
                        self.context.rendered(
                            self.config.answer.context_turns, exclude_latest=True
                        ),
                        "forced_by_user",
                        0.0,
                    )
                )
        finally:
            if task is not None:
                self._ui_tasks.discard(task)

    async def run(self) -> None:
        # Textual resolves the running app through the active_app ContextVar,
        # including inside every Timer it starts. The controller's tasks are
        # NOT descendants of Textual's message pump, so a widget method they
        # await (QACard.append_answer's flush throttle, via the answer-delta
        # callback) creates a timer whose task cannot resolve active_app: it
        # dies instantly with LookupError, and shutdown re-raises it when it
        # awaits dead timers -- crashing quit. Seeding the var here makes every
        # task created below inherit a context in which Textual timers work.
        active_app.set(self.app)
        loop = asyncio.get_running_loop()
        self._capture_loop = loop
        self._loop_thread_id = threading.get_ident()
        self.capture.start(loop, self.frames, enabled=not self.paused)
        self._tasks = [
            asyncio.create_task(
                segment_worker(self.frames, self.utterances, self.segmenter, self.stop)
            ),
            asyncio.create_task(
                stt_worker(
                    self.utterances,
                    self.transcripts,
                    self.transcriber,
                    self.stop,
                    lambda transcript: self._log_rejection(
                        transcript, "transcript_queue_overflow"
                    ),
                )
            ),
            asyncio.create_task(self._consume_transcripts()),
            asyncio.create_task(self.gate.ollama.warmup()),
        ]
        self._tasks.extend(
            asyncio.create_task(self._answer_worker())
            for _ in range(self.config.answer.max_concurrent)
        )
        if self.config.answer.sweep == "always":
            self._tasks.append(asyncio.create_task(self._sweep_worker()))
        try:
            await self.app.run_async()
        finally:
            self.stop.set()
            self.capture.stop()
            for task in self._tasks:
                task.cancel()
            for task in self._ui_tasks:
                task.cancel()
            for task in list(self._gate_tasks):
                task.cancel()
            for task in list(self._verify_tasks):
                task.cancel()
            await asyncio.gather(
                *self._tasks,
                *self._ui_tasks,
                *list(self._gate_tasks),
                *list(self._verify_tasks),
                return_exceptions=True,
            )
            # Preserve exactly one final JSONL record for every confirmed but
            # unfinished question when the user quits.
            for job in list(self._open_answer_jobs.values()):
                record = self._base_record(job.transcript)
                record.update(
                    {
                        "gate": True,
                        "gate_reason": job.reason,
                        "query": job.query,
                        "answer": "cancelled on shutdown",
                        "answer_status": "cancelled",
                        "latencies_ms": {
                            "stt": job.transcript.latency_ms,
                            "gate": job.gate_latency_ms,
                        },
                    }
                )
                await self._log(record)
            self._open_answer_jobs.clear()
            while self._pending_system:
                await self._log_rejection(
                    self._pending_system.popleft().transcript, "shutdown_before_echo_window"
                )
            for transcript in self.continuity.flush_all():
                await self._log_rejection(transcript, "shutdown_before_merge_window")


async def _main() -> None:
    config_path = Path("config.toml")
    config = load_config(config_path)
    # Once Textual owns the terminal, anything logged to stderr is painted raw
    # over the UI -- one component's traceback makes the whole app look dead.
    # Root logging moves to a file next to the session logs; the UI surfaces
    # what matters through the status bar and warning toasts.
    log_dir = Path(config.ui.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "ambientqa.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    controller = AmbientController(config, config_path)
    loop = asyncio.get_running_loop()
    with suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, controller.app.exit)
    await controller.run()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if os.name == "posix":
        # Whisper's lazy model load (huggingface_hub -> tqdm) creates a
        # multiprocessing lock at FIRST TRANSCRIPTION, which spawns the POSIX
        # resource-tracker process and passes sys.stderr.fileno() to it. By
        # then Textual has replaced stderr with a capture whose fileno() is -1,
        # the spawn dies with "bad value(s) in fds_to_keep", and every
        # transcription fails -- CUDA and the CPU fallback alike. Start the
        # tracker NOW, while stderr is still the real terminal; later lock
        # creation only writes to the already-running tracker's pipe.
        from multiprocessing import resource_tracker

        resource_tracker.ensure_running()
    # Inside a TUI a download progress bar could only render into Textual's
    # stdout capture as garbage; this also skips most of the tqdm machinery
    # that needed the multiprocessing lock in the first place.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
