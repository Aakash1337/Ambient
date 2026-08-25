"""Application entry point and bounded five-stage pipeline orchestration."""

from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from textual._context import active_app

from .agent import classify_agent_turn, guard_agent_answer, local_agent_reply
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
from .instances import InstanceRegistry
from .knowledge import KnowledgeHit, KnowledgeIndex, load_pack
from .logging_ import SessionLogger
from .profile import Profile, load_profile
from .segmenter import UtteranceSegmenter, segment_worker
from .stt import WhisperTranscriber, stt_worker
from .tts import (
    SpeakWindows,
    SpeechOutput,
    build_engine,
    speakable,
    voice_followup_intent,
)
from .ui import AmbientQAApp

log = logging.getLogger(__name__)
_DEFAULT_WEB_PORT = 8802


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
    answer_style: str | None = None
    speech_mode: str | None = None
    # Optional stages that happen before the gate/answer proper. Keeping these
    # on the job makes the eventual JSONL/card explain recovery latency without
    # changing when any card is created or when any answer starts.
    stage_latencies_ms: dict[str, float] = field(default_factory=dict)
    # Knowledge-pack entries injected into the prompt as authoritative reference
    # when the question was a weak cache match. Empty in the common case; kept
    # last so existing positional _AnswerJob construction stays source-stable.
    grounding: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _RecentVoiceAnswer:
    question_id: str
    answer: str
    spoken: str
    completed_at: float


@dataclass(slots=True)
class _RecentAnswer:
    """Most recent successful answer, independent of speech/UI mode."""

    question_id: str
    answer: str
    completed_at: float


@dataclass(slots=True)
class _PendingSystem:
    release_at: float
    transcript: Transcript


_AGENT_DANGLING_WORDS = frozenset(
    {
        "about",
        "and",
        "because",
        "but",
        "for",
        "from",
        "if",
        "of",
        "or",
        "so",
        "that",
        "the",
        "then",
        "to",
        "when",
        "which",
        "with",
    }
)
_AGENT_CONTINUITY_HOLD_S = 1.5


def _agent_turn_is_complete(text: str) -> bool:
    """Fast conversational completeness check, independent of question shape."""
    if classify_agent_turn(text) != "content":
        return True
    stripped = text.strip().rstrip("\"')]}»”’")
    if not stripped:
        return True
    if stripped.endswith((",", "-", "–", "—", "…", "...")):
        return False
    tokens = re.findall(r"[A-Za-z0-9']+", stripped.casefold())
    return not tokens or tokens[-1] not in _AGENT_DANGLING_WORDS


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
        voice: bool = False,
        instances: InstanceRegistry | None = None,
        app_factory: Any | None = None,
    ) -> None:
        self.config = config
        self.config_path = Path(config_path)
        self.voice_enabled = voice
        # Runtime-only and deliberately reset on every launch. Normal keeps the
        # configured cue-card behaviour; conversation produces speech-shaped
        # prose and reads the complete answer. The pinned emergency build never
        # imports or depends on this state.
        self.interaction_mode = "normal"
        # The runtime role is independent from the selected knowledge profile.
        # Profiles may retain legacy ``## Interaction`` metadata, but choosing a
        # profile must never silently turn direct participation on or off. Every
        # launch starts safely in Assist; the operator explicitly enters Agent.
        self.agent_mode = False
        self._pre_agent_interaction_mode: str | None = None
        self._agent_customer_channel = "mic"
        self._agent_greeting_pending = False
        self._agent_profile_key: tuple[str, str, str, str] | None = None
        self._agent_had_customer_turn = False
        self._agent_awaiting_reply = False
        self._last_agent_turn: tuple[str, float] | None = None
        self._obsolete_answer_ids: set[str] = set()
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
        # A pause/resume or mute/unmute boundary invalidates voice work for
        # earlier questions. Answers may still finish in the background, but
        # they must not begin speaking after the user has deliberately cut the
        # voice backlog.
        self._voice_ignore_before = 0.0
        # Manual input controls are independent of global Pause, spoken-output
        # mute, and automatic playback echo windows.  The boundary rejects a
        # late Whisper result that began before the most recent toggle.
        self.input_channels_enabled = {"mic": True, "sys": True}
        self._input_after = {"mic": 0.0, "sys": 0.0}
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
        self.instances = instances or InstanceRegistry()
        # Every instance watches speaking windows -- a silent pane must be
        # just as deaf to a voice instance's playback as the speaker itself.
        # Only voice instances ever WRITE windows (via SpeechOutput).
        self.speak_windows = SpeakWindows()
        self.speech: SpeechOutput | None = None
        self.logger = SessionLogger(config.ui.log_dir)
        # The default surface is, and stays, the Textual pane. A factory here
        # is the ONLY seam the opt-in web console (--web) uses: it swaps in a
        # duck-typed app object and changes nothing else about the pipeline.
        if app_factory is None:
            self.app = AmbientQAApp(
                self,
                config.ui.show_transcripts,
                config.ui.status_interval_s,
                feed_direction=config.ui.feed_direction,
                log_dir=config.ui.log_dir,
            )
        else:
            self.app = app_factory(self)
        if voice:
            # After the app exists (_report touches it) but still inside
            # __init__: the engine's onnxruntime import and model load must
            # run while stderr is the real terminal -- the same reason main()
            # pre-warms the multiprocessing resource tracker.
            engine = build_engine(
                config.tts, self._report, self.config_path.resolve().parent
            )
            self.speech = SpeechOutput(
                config.tts, engine, self.speak_windows, self._report
            )
        if config.context.enabled and config.context.profile:
            self._apply_profile(
                load_profile(
                    self._resolve_profile_path(config.context.profile),
                    self._report,
                )
            )
        # The pre-answered knowledge pack. Opt-in, and it TRAVELS WITH THE
        # PROFILE: a profile's ## Knowledge path (or the global fallback) is
        # loaded here at startup and again whenever the profile changes, so
        # switching profiles in the picker swaps packs with no config edit and
        # no restart. A missing or empty pack degrades to None, leaving the
        # pipeline exactly as it was before the feature existed.
        self.knowledge: KnowledgeIndex | None = None
        self._reload_knowledge_for_profile(self.profile)
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
        # Unlike _last_voice_answer, this exists in Assist as well as Voice and
        # therefore gives every surface the same deterministic repeat command.
        self._last_completed_answer: _RecentAnswer | None = None
        self._last_voice_answer: _RecentVoiceAnswer | None = None
        self._gate_tasks: set[asyncio.Task[Any]] = set()
        self._verify_tasks: set[asyncio.Task[Any]] = set()
        # Rejections from the JUDGMENT stages only (policy shape-check and the
        # semantic gate). Mechanical rejections -- filler, dedupe, echo, and
        # pause -- are not misses and never enter the sweep. A rhetorical-tag
        # verdict is different: deciding whether "right?" is a real follow-up
        # is judgment, so that reason remains eligible for recovery.
        self._recent_rejections: deque[Transcript] = deque(maxlen=24)
        # Wall-clock UI timestamps describe when captured audio ended, not when
        # each later pipeline stage ran. These monotonic sidecars let the log
        # distinguish a continuity hold from a slow transcription, and a sweep
        # timer wait from the sweep model itself. They never participate in
        # detection or scheduling.
        self._continuity_arrived_at: dict[str, float] = {}
        self._sweep_ready_at: dict[str, float] = {}
        self._sweep_stage_latencies: dict[str, dict[str, float]] = {}
        # Audits run strictly after their answer is on screen and never more
        # than one at a time. ClaudeAnswerer applies the shared aggregate CLI
        # process limit across primary answers, audits, and sweeps.
        self._verify_semaphore = asyncio.Semaphore(1)
        self._gate_semaphore = asyncio.Semaphore(config.gate.max_concurrent)
        # General cue-card answers remain parallel. Direct customer turns are
        # serialized so replies, speech, and dialogue history preserve order.
        self._agent_answer_lock = asyncio.Lock()
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
            or message.startswith("Microphone listening")
            or message.startswith("Microphone muted")
            or message.startswith("System audio listening")
            or message.startswith("System audio muted")
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
        if question_id in getattr(self, "_obsolete_answer_ids", set()):
            return
        # Agent prose is passed through a deterministic courtesy guard before it
        # can be shown or spoken. Streaming the model's unguarded partial text
        # would defeat that guarantee even though the final TTS path is safe.
        job = getattr(self, "_open_answer_jobs", {}).get(question_id)
        if job is not None and job.answer_style == "agent":
            return
        try:
            self.app.append_answer_delta(question_id, delta)
        except Exception as exc:
            self._report(f"Unable to update streaming answer card: {exc}")

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        boundary = time.time()
        self._voice_ignore_before = boundary
        self.capture.set_enabled(not self.paused)
        if self.speech is not None:
            # Pausing means silence: cut the in-flight utterance and drop the
            # backlog rather than resuming into stale speech.
            self.speech.stop_current(flush=True)
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
            self._ignore_before = boundary
            if getattr(self, "agent_mode", False) and not getattr(
                self, "_agent_had_customer_turn", False
            ):
                self._agent_greeting_pending = True
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

    def _knowledge_path_for_profile(self, profile: Profile | None) -> Path | None:
        """Which pack directory this profile should use, or None.

        A profile's own ``## Knowledge`` path wins, so a pack travels with its
        profile; the global ``knowledge.path`` is only a fallback (used when the
        active profile declares none, or when no profile is active). The master
        ``knowledge.enabled`` switch gates the whole feature.
        """
        if not self.config.knowledge.enabled:
            return None
        declared = getattr(profile, "knowledge", "") if profile is not None else ""
        path = declared or self.config.knowledge.path
        return self._resolve_profile_path(path) if path else None

    def _reload_knowledge_for_profile(self, profile: Profile | None) -> None:
        """Load (or clear) the knowledge pack that belongs to a profile.

        Called at startup and on every profile switch, so the picker swaps packs
        with no restart. Any load problem degrades to no pack rather than
        disturbing the pipeline.
        """
        path = self._knowledge_path_for_profile(profile)
        if path is None:
            self.knowledge = None
            return
        pack = load_pack(path, self._report)
        self.knowledge = pack or None

    def _start_agent_session_boundary(self) -> None:
        """Isolate a new Agent call from every prior role/profile turn."""
        boundary = time.time()
        self._ignore_before = boundary
        self._voice_ignore_before = boundary
        speech = getattr(self, "speech", None)
        if speech is not None:
            speech.stop_current(flush=True)

        for task in list(getattr(self, "_gate_tasks", ())):
            task.cancel()
        open_jobs = getattr(self, "_open_answer_jobs", {})
        obsolete = getattr(self, "_obsolete_answer_ids", None)
        if obsolete is None:
            obsolete = set()
            self._obsolete_answer_ids = obsolete
        obsolete.update(open_jobs)
        # New-call turns must not wait behind an obsolete Agent request that is
        # still unwinding its subprocess. Old waiters retain the old lock and
        # are discarded by the obsolete check before they call the model.
        self._agent_answer_lock = asyncio.Lock()

        answers = getattr(self, "answers", None)
        if answers is not None:
            for job in answers.drain():
                obsolete.discard(job.transcript.utterance_id)
                open_jobs.pop(job.transcript.utterance_id, None)
                cancelled = AnswerResult(
                    job.transcript.utterance_id,
                    job.query,
                    "cancelled because the conversation context changed",
                    "cancelled",
                    0.0,
                )
                with suppress(Exception):
                    self.app.resolve_answer(cancelled)
                record = self._base_record(job.transcript)
                record.update(
                    {
                        "gate": True,
                        "gate_reason": job.reason,
                        "query": job.query,
                        "answer": cancelled.answer,
                        "answer_status": "cancelled",
                        "latencies_ms": {
                            "stt": job.transcript.latency_ms,
                            **job.stage_latencies_ms,
                            "gate": job.gate_latency_ms,
                        },
                    }
                )
                self.logger.append(record)

        self.context.clear()
        history = getattr(self, "_qa_history", None)
        if history is not None:
            history.clear()
        recent = getattr(self, "_recent_rejections", None)
        if recent is not None:
            recent.clear()
        for sidecar_name in (
            "_continuity_arrived_at",
            "_sweep_ready_at",
            "_sweep_stage_latencies",
        ):
            sidecar = getattr(self, sidecar_name, None)
            if sidecar is not None:
                sidecar.clear()
        continuity = getattr(self, "continuity", None)
        if continuity is not None:
            continuity.flush_all()
        pending_system = getattr(self, "_pending_system", None)
        if pending_system is not None:
            pending_system.clear()
        segmenter = getattr(self, "segmenter", None)
        if segmenter is not None:
            segmenter.reset_all()
        self._last_completed_answer = None
        self._last_voice_answer = None
        self.last_transcript = None

    @staticmethod
    def _agent_profile_signature(
        profile: Profile | None,
    ) -> tuple[str, str, str, str]:
        """Identity for session isolation, including all domain context."""
        if profile is None:
            return ("", "mic", "", "")
        # ``raw`` catches an edited or same-title profile. The explicit parsed
        # fields also protect programmatically constructed profiles whose raw
        # source is intentionally empty (as in tests and integrations).
        domain = "\0".join(
            (
                profile.raw,
                profile.topic,
                profile.background,
                "\n".join(profile.vocabulary),
            )
        )
        return (
            profile.name,
            getattr(profile, "customer_channel", "mic"),
            getattr(profile, "greeting", ""),
            domain,
        )

    def _apply_profile(self, profile: Profile | None) -> None:
        """Apply domain context without changing the operator-selected role.

        ``Profile.interaction`` remains parseable for old profile files, but it
        is metadata only. Agent/Assist is runtime state: cybersecurity,
        interview, and support profiles can each be used in either role.
        """
        was_agent = bool(getattr(self, "agent_mode", False))
        previous_key = getattr(self, "_agent_profile_key", None)
        self.profile = profile
        self.transcriber.set_profile(profile)
        self.gate.set_profile(profile)
        self.answerer.set_profile(profile)
        set_agent = getattr(self.transcriber, "set_agent_mode", None)
        if callable(set_agent):
            set_agent(was_agent)

        key = self._agent_profile_signature(profile)
        channel = key[1]
        self._agent_customer_channel = channel

        if not was_agent:
            # No active call signature exists in Assist. Entering Agent later
            # always creates a fresh session and proactive greeting.
            self._agent_profile_key = None
            return

        # A profile change during Agent mode is a new conversation with new
        # domain context, but the runtime role remains Agent.
        if key != previous_key:
            self._start_agent_session_boundary()
            self._agent_greeting_pending = True
            self._agent_had_customer_turn = False
            self._agent_awaiting_reply = False
            self._last_agent_turn = None
        self._agent_profile_key = key

    def set_agent_mode(self, enabled: bool) -> str:
        """Select the runtime Assist/Agent role without changing the profile."""
        enabled = bool(enabled)
        active = bool(getattr(self, "agent_mode", False))
        if enabled and self.speech is None:
            return "Agent mode requires Voice mode"
        if enabled == active:
            return (
                "Agent mode: direct conversational participation"
                if enabled
                else "Assist mode: listening for questions and requests"
            )

        # Role changes are conversation boundaries: old context, queued model
        # work, and speech must not bleed into the newly selected role.
        self._start_agent_session_boundary()
        self.agent_mode = enabled
        set_agent = getattr(self.transcriber, "set_agent_mode", None)
        if callable(set_agent):
            set_agent(enabled)

        if enabled:
            # Agent opens with the natural full-answer delivery most callers
            # expect, while remembering the operator's Assist preference. The
            # Delivery control remains live and can still be changed in Agent.
            self._pre_agent_interaction_mode = self.interaction_mode
            self.interaction_mode = "conversational"
            profile = self.profile
            channel = (
                getattr(profile, "customer_channel", "mic")
                if profile is not None
                else "mic"
            )
            self._agent_customer_channel = channel
            self._agent_profile_key = self._agent_profile_signature(profile)
            self._agent_greeting_pending = True
            self._agent_had_customer_turn = False
            self._agent_awaiting_reply = False
            self._last_agent_turn = None
            return "Agent mode: direct conversational participation"

        if self._pre_agent_interaction_mode is not None:
            self.interaction_mode = self._pre_agent_interaction_mode
        self._pre_agent_interaction_mode = None
        self._agent_customer_channel = "mic"
        self._agent_greeting_pending = False
        self._agent_profile_key = None
        self._agent_had_customer_turn = False
        self._agent_awaiting_reply = False
        self._last_agent_turn = None
        return "Assist mode: listening for questions and requests"

    def toggle_agent_mode(self) -> str:
        """Toggle the runtime role; profile and delivery preference stay put."""
        return self.set_agent_mode(not bool(getattr(self, "agent_mode", False)))

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
        # The pack follows the profile: reload it on the switch so the picker
        # alone is enough to change both domain context and cached answers.
        await asyncio.to_thread(self._reload_knowledge_for_profile, profile)
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
        channel = "mic" if state is self.capture.mic else "sys"
        if not self.input_channel_enabled(channel):
            return "muted"
        if not state.active:
            return "off"
        quiet = state.silent_for()
        if quiet is not None and quiet >= self.config.audio.silent_source_warn_s:
            return f"SILENT {int(quiet)}s ⚠"
        return "on"

    def toggle_voice(self) -> str:
        if self.speech is None:
            return "voice mode is off (relaunch with --voice)"
        self._voice_ignore_before = time.time()
        self.speech.muted = not self.speech.muted
        if self.speech.muted:
            self.speech.stop_current(flush=True)
            return "voice muted"
        if getattr(self, "agent_mode", False) and not getattr(
            self, "_agent_had_customer_turn", False
        ):
            self._agent_greeting_pending = True
        return "voice on"

    @staticmethod
    def _validate_input_channel(channel: str) -> str:
        if channel not in {"mic", "sys"}:
            raise ValueError('input channel must be "mic" or "sys"')
        return channel

    def input_channel_enabled(self, channel: str) -> bool:
        channel = self._validate_input_channel(channel)
        state = getattr(self, "input_channels_enabled", None)
        if isinstance(state, dict):
            return bool(state.get(channel, True))
        capture = getattr(self, "capture", None)
        getter = getattr(capture, "channel_enabled", None)
        return bool(getter(channel)) if callable(getter) else True

    def _transcript_input_is_live(self, transcript: Transcript) -> bool:
        """Whether this transcript belongs to the current enabled interval."""
        channel = self._validate_input_channel(transcript.channel)
        if not self.input_channel_enabled(channel):
            return False
        captured_from = (
            transcript.timestamp
            if transcript.started_at is None
            else transcript.started_at
        )
        return captured_from >= getattr(self, "_input_after", {}).get(channel, 0.0)

    def _input_or_playback_muted(self, channel: str, timestamp: float) -> bool:
        """Compose manual listening controls with automatic TTS echo suppression."""
        if not self.input_channel_enabled(channel):
            return True
        if timestamp < getattr(self, "_input_after", {}).get(channel, 0.0):
            return True
        return self.speak_windows.muted(channel, timestamp)

    def _log_channel_discard(self, transcript: Transcript) -> None:
        self.logger.append(
            {
                **self._base_record(transcript),
                # A manual listening mute is a privacy boundary. Whisper may
                # finish work that was already in flight, but its recognized
                # words must not then be persisted in the session log.
                "text": "",
                "text_redacted": True,
                "gate": False,
                "gate_reason": "channel_muted",
                "answer": None,
                "latencies_ms": {"stt": transcript.latency_ms},
            }
        )

    async def _log_muted_transcript(
        self,
        transcript: Transcript,
        stage_latencies_ms: dict[str, float] | None = None,
    ) -> None:
        record = {
            **self._base_record(transcript),
            "text": "",
            "text_redacted": True,
            "gate": False,
            "gate_reason": "channel_muted",
            "answer": None,
            "latencies_ms": {
                "stt": transcript.latency_ms,
                **dict(stage_latencies_ms or {}),
            },
        }
        await self._log(record)

    def toggle_input_channel(self, channel: str) -> bool:
        """Toggle one capture source without disturbing the other source."""
        channel = self._validate_input_channel(channel)
        enabled = not self.input_channel_enabled(channel)
        boundary = time.time()
        self.input_channels_enabled[channel] = enabled
        self._input_after[channel] = boundary
        self.capture.set_channel_enabled(channel, enabled)

        # A toggle is a hard privacy boundary: nothing already buffered for
        # this channel may appear later after the channel is re-enabled.
        self.frames.discard_where(lambda item: item.channel == channel)
        self.utterances.discard_where(lambda item: item.channel == channel)
        for transcript in self.transcripts.discard_where(
            lambda item: item.channel == channel
        ):
            self._log_channel_discard(transcript)
        self.segmenter.discard(channel)
        pending = self.continuity.discard(channel)
        if pending is not None:
            self._log_channel_discard(pending)
        if channel == "sys":
            while self._pending_system:
                self._log_channel_discard(self._pending_system.popleft().transcript)
        elif not enabled:
            # A sys transcript no longer needs to wait for a possible mic echo.
            for item in self._pending_system:
                item.release_at = 0.0

        rejected = [
            item for item in self._recent_rejections if item.channel != channel
        ]
        self._recent_rejections = deque(
            rejected, maxlen=self._recent_rejections.maxlen
        )
        retained_ids = {item.utterance_id for item in self._recent_rejections}
        for sidecar in (self._sweep_ready_at, self._sweep_stage_latencies):
            for question_id in list(sidecar):
                if question_id not in retained_ids:
                    sidecar.pop(question_id, None)
        for question_id in list(self._continuity_arrived_at):
            if question_id == getattr(pending, "utterance_id", None):
                self._continuity_arrived_at.pop(question_id, None)

        label = "Microphone" if channel == "mic" else "System audio"
        self._report(
            f"{label} {'listening' if enabled else 'muted — not transcribing'}"
        )
        return enabled

    def toggle_interaction_mode(self) -> str:
        """Switch future voice answers between cue and conversational delivery."""
        if self.speech is None:
            return "conversation mode requires Voice mode"
        self.interaction_mode = (
            "conversational"
            if self.interaction_mode == "normal"
            else "normal"
        )
        if self.interaction_mode == "conversational":
            return "Conversation mode: full answers in natural spoken prose"
        return "Normal mode: cue answers, opening line spoken"

    def _answer_style_for_mode(self) -> str:
        if getattr(self, "agent_mode", False):
            return "agent"
        if getattr(self, "interaction_mode", "normal") == "conversational":
            return "interview"
        return self.config.answer.style

    def _speech_mode_for_mode(self) -> str:
        if getattr(self, "interaction_mode", "normal") == "conversational":
            return "full"
        return self.config.tts.speak

    async def _agent_greeting_worker(self) -> None:
        """Speak one transparent welcome whenever a new Agent session starts."""
        while not self.stop.is_set():
            speech = getattr(self, "speech", None)
            if (
                not getattr(self, "_agent_greeting_pending", False)
                or not getattr(self, "agent_mode", False)
                or self.paused
                or speech is None
                or getattr(speech, "muted", False)
                or not getattr(self.app, "is_running", False)
            ):
                await asyncio.sleep(0.1)
                continue
            # Let the terminal/browser chrome render before the voice opens the
            # call. Re-check afterwards because profile/mute state may change.
            await asyncio.sleep(0.35)
            if (
                not self._agent_greeting_pending
                or not self.agent_mode
                or self.paused
                or getattr(speech, "muted", False)
            ):
                continue
            profile = self.profile
            greeting = (
                getattr(profile, "greeting", "").strip()
                if profile is not None
                else ""
            )
            greeting = greeting or local_agent_reply("greeting") or (
                "Hello. I'm Ambient, an AI assistant. What would you like to work through?"
            )
            greeting = guard_agent_answer(greeting)
            self._agent_greeting_pending = False
            now = time.time()
            self.gate.mark_answer_text(greeting, now)
            speech.enqueue(f"agent-greeting-{int(now * 1000)}", greeting)
            self._qa_history.append(("(conversation opened)", greeting))
            self._agent_awaiting_reply = greeting.rstrip().endswith("?")
            with suppress(Exception):
                self.app.notify(greeting)
            self.logger.append(
                {
                    "id": f"agent-greeting-{int(now * 1000)}",
                    "timestamp": now,
                    "channel": self._agent_customer_channel,
                    "text": "",
                    "gate": True,
                    "gate_reason": "agent_proactive_greeting",
                    "query": "(conversation opened)",
                    "answer": greeting,
                    "answer_status": "ok",
                    "web_lookup": False,
                    "latencies_ms": {"gate": 0.0, "answer": 0.0},
                }
            )

    async def _deliver_local_agent_reply(
        self,
        transcript: Transcript,
        reply: str,
        reason: str,
        stage_latencies_ms: dict[str, float] | None = None,
    ) -> None:
        reply = guard_agent_answer(reply)
        try:
            await self.app.add_question(transcript.utterance_id, transcript.text)
        except Exception as exc:
            self._report(f"Unable to add Agent conversation card: {exc}")
        result = AnswerResult(
            transcript.utterance_id,
            transcript.text,
            reply,
            "ok",
            0.0,
        )
        self.answer_count += 1
        now = time.time()
        self._last_completed_answer = _RecentAnswer(
            transcript.utterance_id, reply, now
        )
        self.gate.mark_answered(transcript.text, now)
        self.gate.mark_answer_text(reply, now)
        self._qa_history.append((transcript.text, reply))
        self._agent_had_customer_turn = True
        self._agent_awaiting_reply = reply.rstrip().endswith("?")
        self._enqueue_speech(
            _AnswerJob(
                transcript,
                transcript.text,
                [],
                reason,
                0.0,
                "agent",
                self._speech_mode_for_mode(),
                dict(stage_latencies_ms or {}),
            ),
            reply,
            now,
        )
        try:
            self.app.resolve_answer(result)
        except Exception as exc:
            self._report(f"Unable to resolve Agent conversation card: {exc}")
        record = self._base_record(transcript)
        record.update(
            {
                "gate": True,
                "gate_reason": reason,
                "query": transcript.text,
                "answer": reply,
                "answer_status": "ok",
                "web_lookup": False,
                "latencies_ms": {
                    "stt": transcript.latency_ms,
                    **dict(stage_latencies_ms or {}),
                    "gate": 0.0,
                    "answer": 0.0,
                },
            }
        )
        await self._log(record)

    async def _handle_agent_turn(
        self,
        transcript: Transcript,
        answer_context: list[str],
        stage_latencies_ms: dict[str, float] | None = None,
        kind: str | None = None,
    ) -> bool:
        """Route one meaningful customer turn without the question-only gate."""
        if not getattr(self, "agent_mode", False):
            return False
        if transcript.channel != self._agent_customer_channel:
            await self._log_rejection(
                transcript, "agent_non_customer_channel", stage_latencies_ms
            )
            return True

        kind = kind or classify_agent_turn(transcript.text)
        compact = " ".join(transcript.text.casefold().split())
        if kind == "filler" and self._agent_awaiting_reply and compact.strip(" .?!") in {
            "yes",
            "no",
            "yep",
            "yeah",
            "nope",
        }:
            kind = "content"
        if kind == "filler":
            await self._log_rejection(
                transcript, "agent_filler", stage_latencies_ms
            )
            return True

        now = time.time()
        previous = self._last_agent_turn
        if (
            previous is not None
            and now - previous[1] <= 3.0
            and token_set_ratio(previous[0], transcript.text) >= 0.9
        ):
            await self._log_rejection(
                transcript, "agent_duplicate", stage_latencies_ms
            )
            return True
        self._last_agent_turn = (transcript.text, now)

        if kind == "greeting" and self._agent_greeting_pending:
            # The caller spoke before the proactive welcome fired. Turn that
            # exchange into the one opening greeting instead of playing a
            # second welcome 350 ms later.
            self._agent_greeting_pending = False
            reply = local_agent_reply(
                kind,
                (
                    getattr(self.profile, "greeting", "")
                    if self.profile is not None
                    else ""
                ),
            )
        elif kind == "greeting":
            # Once Ambient has introduced itself, a customer's "hello" should
            # get a natural hello back rather than the full scripted opener.
            reply = "Hello! It's good to hear from you. How can I help today?"
        else:
            reply = local_agent_reply(
                kind,
                (
                    getattr(self.profile, "greeting", "")
                    if self.profile is not None
                    else ""
                ),
            )
        if reply is not None:
            await self._deliver_local_agent_reply(
                transcript,
                reply,
                f"agent_local_{kind}",
                stage_latencies_ms,
            )
            return True

        self._agent_had_customer_turn = True
        self._agent_awaiting_reply = False
        try:
            await self.app.add_question(transcript.utterance_id, transcript.text)
        except Exception as exc:
            self._report(f"Unable to add Agent conversation card: {exc}")
        await self._enqueue_answer(
            _AnswerJob(
                transcript,
                transcript.text,
                answer_context,
                "agent_turn",
                0.0,
                "agent",
                self._speech_mode_for_mode(),
                dict(stage_latencies_ms or {}),
            )
        )
        return True

    def status_text(self) -> str:
        if getattr(self, "agent_mode", False):
            inputs_live = self.input_channel_enabled(self._agent_customer_channel)
        else:
            inputs_live = any(
                self.input_channel_enabled(channel) for channel in ("mic", "sys")
            )
        listening = (
            "⏸ PAUSED"
            if self.paused
            else (
                (
                    f"● AGENT LISTENING:{self._agent_customer_channel}"
                    if getattr(self, "agent_mode", False)
                    else "● listening"
                )
                if inputs_live
                else (
                    f"◌ SPEAKER {self._agent_customer_channel.upper()} MUTED"
                    if getattr(self, "agent_mode", False)
                    else "◌ INPUTS MUTED"
                )
            )
        )
        mic = self._source_status(self.capture.mic)
        loopback = self._source_status(self.capture.loopback)
        voice = ""
        if self.speech is not None:
            # Plain attribute reads only: this runs inside the 0.5s UI tick
            # that doubles as the instance heartbeat.
            state = (
                "muted"
                if self.speech.muted
                else ("♪" if self.speech.speaking else "on")
            )
            delivery = getattr(self, "interaction_mode", "normal")
            mode = (
                f"agent/{delivery}"
                if getattr(self, "agent_mode", False)
                else delivery
            )
            voice = f"voice:{state}/{mode}  "
        queues = (
            f"{self.frames.qsize()}/{self.utterances.qsize()}/"
            f"{self.transcripts.qsize()}/{self.answers.qsize()}"
        )
        warning = f"  ⚠ {self.warnings[-1]}" if self.warnings else ""
        profile_name = self.profile.name if self.profile is not None else "none"
        # Doubles as the heartbeat: the status bar refreshes every tick anyway,
        # so counting here keeps liveness and display on the same cadence.
        instance_count = self.instances.heartbeat_and_count()
        # instances sits BEFORE the variable-length profile name: on a narrow
        # terminal the line truncates from the right, and the counter exists
        # to be seen.
        sweep = "on" if self.config.answer.sweep == "always" else "off"
        return (
            f"{listening}  {voice}mic:{mic} sys:{loopback}  whisper:{self.transcriber.device}  "
            f"gate:{self.config.gate.mode} sweep:{sweep}  "
            f"instances:{instance_count}  "
            f"profile:{profile_name}  queues:{queues}  "
            f"answers:{self.answerer.in_flight} active/{self.answer_count} done  "
            # This is deliberately labelled as output-only: Claude input,
            # audits, and sweeps are not observable from the answer prose and
            # must never be disguised as a total spend counter.
            f"~output_tokens:{self.estimated_tokens}{warning}"
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
    # rejections a wrongly-dropped question can hide behind. This includes the
    # vocative heuristic because deciding who was addressed is fallible; the
    # sweep prompt independently rejects genuine human-directed speech.
    _SWEEP_REASONS = frozenset(
        {
            "not_a_direct_question",
            "ollama_reject",
            "ollama_unavailable",
            # A tag can be rhetorical, but that is a semantic judgment rather
            # than a mechanical rejection.  In particular, clarification
            # follow-ups often state a premise and end in "right?"; if the fast
            # heuristic guesses wrong, the context-aware sweep must still get
            # its promised chance to recover them.
            "tag_or_rhetorical",
            # This is a judgment about who was addressed, not a mechanical
            # transport rejection. The wide-context sweep is explicitly told
            # to keep real human-directed vocatives rejected, while recovering
            # false name matches such as "Again, describe RAG pipelines."
            "human_vocative",
        }
    )

    def _remember_sweep_rejection(
        self,
        transcript: Transcript,
        reason: str,
        stage_latencies_ms: dict[str, float] | None = None,
    ) -> None:
        """Feed only judgment-stage misses to the optional recovery pass."""
        if reason in self._SWEEP_REASONS:
            # A bounded deque silently evicts its oldest candidate. Mirror that
            # eviction in the timing sidecars so a long session cannot retain
            # timing entries for questions the sweeper can no longer recover.
            evicted_id: str | None = None
            if (
                self._recent_rejections.maxlen is not None
                and len(self._recent_rejections) >= self._recent_rejections.maxlen
            ):
                evicted_id = self._recent_rejections[0].utterance_id
            self._recent_rejections.append(transcript)
            ready = getattr(self, "_sweep_ready_at", None)
            stages = getattr(self, "_sweep_stage_latencies", None)
            if evicted_id is not None and evicted_id != transcript.utterance_id:
                if ready is not None:
                    ready.pop(evicted_id, None)
                if stages is not None:
                    stages.pop(evicted_id, None)
            if ready is not None:
                ready.setdefault(transcript.utterance_id, time.perf_counter())
            if stages is not None and stage_latencies_ms:
                stages.setdefault(transcript.utterance_id, {}).update(
                    stage_latencies_ms
                )

    async def _log_rejection(
        self,
        transcript: Transcript,
        reason: str,
        stage_latencies_ms: dict[str, float] | None = None,
    ) -> None:
        stages = dict(stage_latencies_ms or {})
        self._remember_sweep_rejection(transcript, reason, stages)
        latencies = {"stt": transcript.latency_ms, **stages}
        record = self._base_record(transcript)
        record.update(
            {
                "gate": False,
                "gate_reason": reason,
                "answer": None,
                "latencies_ms": latencies,
            }
        )
        await self._log(record)

    def _enqueue_speech(
        self,
        job: _AnswerJob,
        answer: str,
        timestamp: float | None = None,
    ) -> None:
        """Queue final answer prose only while this controller is listening."""
        agent_customer_turn = (
            job.answer_style == "agent"
            and job.transcript.channel
            == getattr(self, "_agent_customer_channel", "mic")
        )
        if (
            self.paused
            or self.speech is None
            or job.transcript.timestamp < self._voice_ignore_before
            or (
                not agent_customer_turn
                and job.transcript.channel
                not in (self.config.tts.speak_channels or [])
            )
        ):
            return
        mode = job.speech_mode or self.config.tts.speak
        spoken = speakable(answer, mode)
        if not spoken:
            return
        now = time.time() if timestamp is None else timestamp
        self._last_voice_answer = _RecentVoiceAnswer(
            job.transcript.utterance_id,
            answer,
            "" if getattr(self.speech, "muted", False) else spoken,
            now,
        )
        # Record exactly what will be emitted before playback begins.
        self.gate.mark_answer_text(spoken, now)
        self.speech.enqueue(job.transcript.utterance_id, spoken)

    _VOICE_FOLLOWUP_MAX_AGE_S = 90.0

    async def _handle_local_repeat(self, transcript: Transcript) -> bool:
        """Render the exact last answer again without gating or calling Claude."""
        repeat_channel = (
            getattr(self, "_agent_customer_channel", "mic")
            if getattr(self, "agent_mode", False)
            else "mic"
        )
        if getattr(self, "paused", False) or transcript.channel != repeat_channel:
            return False
        recent = getattr(self, "_last_completed_answer", None)
        if recent is None:
            # Compatibility for lightweight controller fixtures, and for an
            # answer that was queued for speech by older in-process state.
            recent = getattr(self, "_last_voice_answer", None)
        now = time.time()
        if (
            recent is None
            or now - recent.completed_at > self._VOICE_FOLLOWUP_MAX_AGE_S
        ):
            return False

        answer = recent.answer
        try:
            await self.app.add_question(transcript.utterance_id, transcript.text)
        except Exception as exc:
            self._report(f"Unable to add repeated-answer card: {exc}")
        try:
            self.app.resolve_answer(
                AnswerResult(
                    transcript.utterance_id,
                    transcript.text,
                    answer,
                    "ok",
                    0.0,
                )
            )
        except Exception as exc:
            self._report(f"Unable to resolve repeated-answer card: {exc}")

        # Use the delivery mode captured now: Normal repeats its spoken opening
        # line, while Conversational repeats the complete answer. Assist still
        # gets the exact card because _enqueue_speech is simply skipped.
        if getattr(self, "speech", None) is not None:
            self._enqueue_speech(
                _AnswerJob(
                    transcript,
                    transcript.text,
                    [],
                    "local_repeat",
                    0.0,
                    self._answer_style_for_mode(),
                    self._speech_mode_for_mode(),
                ),
                answer,
                now,
            )
        recent.completed_at = now
        self.answer_count = getattr(self, "answer_count", 0) + 1
        with suppress(Exception):
            self.app.notify("Repeated the last answer")
        record = self._base_record(transcript)
        record.update(
            {
                "gate": True,
                "gate_reason": "local_repeat",
                "query": transcript.text,
                "answer": answer,
                "answer_status": "ok",
                "web_lookup": False,
                "latencies_ms": {
                    "stt": transcript.latency_ms,
                    "gate": 0.0,
                    "answer": 0.0,
                },
            }
        )
        await self._log(record)
        return True

    async def _handle_voice_followup(self, transcript: Transcript) -> bool:
        """Handle narrowly recognised repeat/continue requests without Claude.

        Whisper can turn "weren't you going to continue reading..." into the
        semantically opposite "I'm not going to continue reading...". Neither
        question pass can recover words that are no longer in the transcript.
        Repeats work in every mode; continue remains an opt-in conversational
        playback control.
        """
        intent = voice_followup_intent(transcript.text)
        if intent == "repeat":
            return await self._handle_local_repeat(transcript)
        if (
            getattr(self, "interaction_mode", "normal") != "conversational"
            or getattr(self, "speech", None) is None
            or getattr(self, "paused", False)
            or transcript.channel
            != (
                getattr(self, "_agent_customer_channel", "mic")
                if getattr(self, "agent_mode", False)
                else "mic"
            )
        ):
            return False
        recent = getattr(self, "_last_voice_answer", None)
        now = time.time()
        if (
            intent is None
            or recent is None
            or now - recent.completed_at > self._VOICE_FOLLOWUP_MAX_AGE_S
        ):
            return False

        full = speakable(recent.answer, "full")
        if not full:
            return False
        if intent == "repeat":
            spoken = full
            notice = "Repeating the last answer"
        else:
            spoken = full
            if full.startswith(recent.spoken):
                spoken = full[len(recent.spoken) :].lstrip(" \t\r\n.,;:—-")
            if spoken:
                notice = "Reading the rest of the last answer"
            else:
                spoken = "That was the complete answer."
                notice = "The complete answer was already spoken"

        if getattr(self.speech, "muted", False):
            notice = "Voice is muted; press m to hear conversational replies"
        else:
            self.gate.mark_answer_text(spoken, now)
            self.speech.enqueue(
                f"{transcript.utterance_id}-voice-{intent}", spoken
            )
            recent.spoken = full
            recent.completed_at = now
        with suppress(Exception):
            self.app.notify(notice)
        record = self._base_record(transcript)
        record.update(
            {
                "gate": False,
                "gate_reason": f"voice_control_{intent}",
                "answer": None,
                "latencies_ms": {
                    "stt": transcript.latency_ms,
                    "gate": 0.0,
                },
            }
        )
        await self._log(record)
        return True

    async def _complete_answer(self, job: _AnswerJob, result: AnswerResult) -> None:
        if job.transcript.utterance_id in getattr(
            self, "_obsolete_answer_ids", set()
        ):
            self._obsolete_answer_ids.discard(job.transcript.utterance_id)
            cancelled = AnswerResult(
                job.transcript.utterance_id,
                job.query,
                "cancelled because the conversation context changed",
                "cancelled",
                result.latency_ms,
            )
            with suppress(Exception):
                self.app.resolve_answer(cancelled)
            record = self._base_record(job.transcript)
            record.update(
                {
                    "gate": True,
                    "gate_reason": job.reason,
                    "query": job.query,
                    "answer": cancelled.answer,
                    "answer_status": "cancelled",
                    "latencies_ms": {
                        "stt": job.transcript.latency_ms,
                        **job.stage_latencies_ms,
                        "gate": job.gate_latency_ms,
                        "answer": result.latency_ms,
                    },
                }
            )
            self.logger.append(record)
            self._open_answer_jobs.pop(job.transcript.utterance_id, None)
            return
        is_agent = job.answer_style == "agent"
        if is_agent:
            safe_answer = (
                guard_agent_answer(result.answer)
                if result.status == "ok"
                else (
                    "I'm sorry, I'm having trouble responding right now. "
                    "Would you like me to try that again?"
                )
            )
            if safe_answer != result.answer:
                result = AnswerResult(
                    result.question_id,
                    result.question,
                    safe_answer,
                    result.status,
                    result.latency_ms,
                    timestamp=result.timestamp,
                    searched=result.searched,
                )
        self.answer_count += 1
        self.estimated_tokens += int(len(result.answer.split()) * 1.35)
        if result.status == "ok":
            now = time.time()
            self._last_completed_answer = _RecentAnswer(
                job.transcript.utterance_id,
                result.answer,
                now,
            )
            self.gate.mark_answered(job.transcript.text, now)
            # Remember the answer prose too: the user rehearsing it aloud would
            # otherwise be gated as a fresh question and answered again.
            self.gate.mark_answer_text(result.answer, now)
            self._qa_history.append((job.query, result.answer))
            if is_agent:
                self._agent_had_customer_turn = True
                self._agent_awaiting_reply = result.answer.rstrip().endswith("?")
            if self.config.answer.verify == "always":
                # When voice and verification are both enabled, wait for the
                # audit before speaking.  The card still resolves immediately,
                # but a material correction can no longer arrive after the
                # wrong answer was already read aloud.
                task = asyncio.create_task(
                    self._verify_answer(
                        job,
                        result,
                        speak_after=self.speech is not None,
                    )
                )
                self._verify_tasks.add(task)
                task.add_done_callback(self._verify_tasks.discard)
            else:
                self._enqueue_speech(job, result.answer, now)
        elif is_agent:
            # Operational failures remain errors in the card/log, but a live
            # customer hears a calm recovery prompt instead of CLI diagnostics.
            now = time.time()
            self.gate.mark_answer_text(result.answer, now)
            self._qa_history.append((job.query, result.answer))
            self._agent_had_customer_turn = True
            self._agent_awaiting_reply = True
            self._enqueue_speech(job, result.answer, now)
        try:
            self.app.resolve_answer(result)
        except Exception as exc:
            self._report(f"Unable to update answer card: {exc}")
        record = self._base_record(job.transcript)
        latencies = {
            "stt": job.transcript.latency_ms,
            **job.stage_latencies_ms,
            "gate": job.gate_latency_ms,
            "answer": result.latency_ms,
        }
        record.update(
            {
                "gate": True,
                "gate_reason": job.reason,
                "query": job.query,
                "answer": result.answer,
                "answer_status": result.status,
                "web_lookup": result.searched,
                "latencies_ms": latencies,
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

    async def _verify_answer(
        self,
        job: _AnswerJob,
        result: AnswerResult,
        speak_after: bool = False,
    ) -> None:
        final_answer = result.answer
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
                    style=job.answer_style,
                    grounding=job.grounding,
                )
            if revision is not None:
                final_answer = revision
                latency = (time.perf_counter() - started) * 1000
                now = time.time()
                self.gate.mark_answer_text(revision, now)
                self._replace_history_answer(job.query, revision)
                recent = getattr(self, "_last_completed_answer", None)
                if (
                    recent is not None
                    and recent.question_id == job.transcript.utterance_id
                ):
                    recent.answer = revision
                    recent.completed_at = now
                if job.answer_style == "agent":
                    self._agent_awaiting_reply = revision.rstrip().endswith("?")
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
        if speak_after and not self.stop.is_set():
            self._enqueue_speech(job, final_answer)

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
            if (
                self.paused
                or getattr(self, "agent_mode", False)
                or not self._recent_rejections
            ):
                continue
            candidates = list(self._recent_rejections)
            try:
                answered = [query for query, _answer in self._qa_history] + [
                    job.query for job in self._open_answer_jobs.values()
                ]
                sweep_started = time.perf_counter()
                missed = await self.answerer.detect_missed(
                    [(t.channel, t.text) for t in candidates],
                    self.context.rendered(self.config.answer.verify_context_turns),
                    answered,
                )
                sweep_latency_ms = (
                    time.perf_counter() - sweep_started
                ) * 1000
                if missed is None:
                    # CLI timeout/quota/parse failures are retryable. Keep the
                    # snapshot (and any new rejections appended meanwhile)
                    # instead of silently losing the whole candidate batch.
                    continue
                if getattr(self, "agent_mode", False):
                    # A sweep that began just before an Agent profile was
                    # selected cannot resurrect old Assist candidates into the
                    # new customer call.
                    continue
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
                    stage_latencies = dict(
                        getattr(self, "_sweep_stage_latencies", {}).get(
                            original.utterance_id, {}
                        )
                    )
                    ready_at = getattr(self, "_sweep_ready_at", {}).get(
                        original.utterance_id
                    )
                    if ready_at is not None:
                        stage_latencies["sweep_wait"] = max(
                            0.0, (sweep_started - ready_at) * 1000
                        )
                    stage_latencies["sweep"] = sweep_latency_ms
                    await self._enqueue_answer(
                        _AnswerJob(
                            recovered,
                            question,
                            self.context.rendered(
                                self.config.answer.context_turns
                            ),
                            "second_pass_recovery",
                            0.0,
                            self._answer_style_for_mode(),
                            self._speech_mode_for_mode(),
                            stage_latencies,
                        )
                    )
                processed_ids = {
                    transcript.utterance_id for transcript in candidates
                }
                self._recent_rejections = deque(
                    (
                        transcript
                        for transcript in self._recent_rejections
                        if transcript.utterance_id not in processed_ids
                    ),
                    maxlen=24,
                )
                for transcript_id in processed_ids:
                    ready = getattr(self, "_sweep_ready_at", None)
                    if ready is not None:
                        ready.pop(transcript_id, None)
                    stages = getattr(self, "_sweep_stage_latencies", None)
                    if stages is not None:
                        stages.pop(transcript_id, None)
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
                async def generate() -> None:
                    # History is read here, at answer time, not when the job was
                    # queued: a follow-up asked seconds after an answer completes
                    # must see that answer even if it was enqueued first.
                    result = await self.answerer.answer(
                        job.transcript.utterance_id,
                        job.query,
                        job.context,
                        history=list(self._qa_history),
                        channel=job.transcript.channel,
                        style=job.answer_style,
                        grounding=job.grounding,
                    )
                    await self._complete_answer(job, result)

                if job.answer_style == "agent":
                    lock = getattr(self, "_agent_answer_lock", None)
                    if lock is None:
                        lock = asyncio.Lock()
                        self._agent_answer_lock = lock
                    async with lock:
                        if job.transcript.utterance_id in getattr(
                            self, "_obsolete_answer_ids", set()
                        ):
                            await self._complete_answer(
                                job,
                                AnswerResult(
                                    job.transcript.utterance_id,
                                    job.query,
                                    "cancelled because the conversation context changed",
                                    "cancelled",
                                    0.0,
                                ),
                            )
                        else:
                            await generate()
                else:
                    await generate()
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

    async def _process_transcript(
        self,
        transcript: Transcript,
        stage_latencies_ms: dict[str, float] | None = None,
    ) -> None:
        stage_latencies = dict(stage_latencies_ms or {})
        if not self._transcript_input_is_live(transcript):
            await self._log_muted_transcript(transcript, stage_latencies)
            return

        if getattr(self, "agent_mode", False):
            # Only the explicitly selected caller channel can steer an Agent
            # reply. The operator-side channel remains visible for monitoring,
            # but is deliberately excluded from model context as well as action.
            try:
                await self.app.add_transcript(transcript)
            except Exception as exc:
                self._report(f"Unable to update transcript pane: {exc}")
            if transcript.channel != self._agent_customer_channel:
                await self._handle_agent_turn(
                    transcript, [], stage_latencies, kind="content"
                )
                return

            kind = classify_agent_turn(transcript.text)
            if kind == "filler":
                compact = " ".join(transcript.text.casefold().split()).strip(" .?!")
                if self._agent_awaiting_reply and compact in {
                    "yes",
                    "no",
                    "yep",
                    "yeah",
                    "nope",
                }:
                    kind = "content"
                else:
                    await self._handle_agent_turn(
                        transcript, [], stage_latencies, kind=kind
                    )
                    return
            if not self.context.add(transcript):
                await self._log_rejection(
                    transcript, "cross_channel_echo", stage_latencies
                )
                return
            self.last_transcript = transcript
            if await self._handle_voice_followup(transcript):
                return
            answer_context = self.context.rendered(
                self.config.answer.context_turns, exclude_latest=True
            )
            await self._handle_agent_turn(
                transcript, answer_context, stage_latencies, kind=kind
            )
            return

        if not self.context.add(transcript):
            if stage_latencies:
                await self._log_rejection(
                    transcript, "cross_channel_echo", stage_latencies
                )
            else:
                await self._log_rejection(transcript, "cross_channel_echo")
            return
        self.last_transcript = transcript
        try:
            await self.app.add_transcript(transcript)
        except Exception as exc:
            self._report(f"Unable to update transcript pane: {exc}")
        if await self._handle_voice_followup(transcript):
            return
        policy = self.config.gate.channel_policy.get(transcript.channel, "full")
        if policy == "off":
            # Context-only channel. It has already been added to the context
            # window above, so it still resolves referents for the channels that
            # do answer -- it just cannot become a question itself.
            if stage_latencies:
                await self._log_rejection(
                    transcript, "channel_not_answered", stage_latencies
                )
            else:
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
            self._gate_and_enqueue(
                transcript,
                background,
                answer_context,
                policy,
                stage_latencies,
            )
        )
        self._gate_tasks.add(task)
        task.add_done_callback(self._gate_tasks.discard)

    def _knowledge_grounding(self, query: str) -> list[str]:
        """Reference snippets to inject on a live answer, or empty when off."""
        knowledge = getattr(self, "knowledge", None)
        if (
            knowledge is None
            or not self.config.knowledge.ground_on_miss
            or self.config.knowledge.retrieve_k <= 0
        ):
            return []
        return knowledge.grounding(query, self.config.knowledge.retrieve_k)

    async def _serve_cached_answer(
        self,
        transcript: Transcript,
        query: str,
        hit: KnowledgeHit,
        gate_latency_ms: float,
        stage_latencies_ms: dict[str, float],
    ) -> None:
        """Answer an anticipated question from the pack with no model call.

        The question card already exists (the caller added it), so this mirrors
        _handle_local_repeat: fill in the answer, update the same session state a
        live answer would, optionally speak it, and log the hit with its match
        so pack quality is auditable from the session log.
        """
        answer = hit.answer
        now = time.time()
        self.answer_count += 1
        self._last_completed_answer = _RecentAnswer(
            transcript.utterance_id, answer, now
        )
        self.gate.mark_answered(transcript.text, now)
        self.gate.mark_answer_text(answer, now)
        self._qa_history.append((query, answer))
        if self.speech is not None:
            self._enqueue_speech(
                _AnswerJob(
                    transcript,
                    query,
                    [],
                    "knowledge_cache",
                    gate_latency_ms,
                    self._answer_style_for_mode(),
                    self._speech_mode_for_mode(),
                ),
                answer,
                now,
            )
        try:
            self.app.resolve_answer(
                AnswerResult(transcript.utterance_id, query, answer, "ok", 0.0)
            )
        except Exception as exc:
            self._report(f"Unable to resolve cached answer card: {exc}")
        record = self._base_record(transcript)
        record.update(
            {
                "gate": True,
                "gate_reason": "knowledge_cache",
                "query": query,
                "answer": answer,
                "answer_status": "ok",
                "web_lookup": False,
                "knowledge_match": hit.entry.question,
                "knowledge_score": round(hit.score, 3),
                "latencies_ms": {
                    "stt": transcript.latency_ms,
                    **stage_latencies_ms,
                    "gate": gate_latency_ms,
                    "answer": 0.0,
                },
            }
        )
        await self._log(record)

    async def _gate_and_enqueue(
        self,
        transcript: Transcript,
        background: list[str],
        answer_context: list[str],
        policy: str = "full",
        stage_latencies_ms: dict[str, float] | None = None,
    ) -> None:
        stage_latencies = dict(stage_latencies_ms or {})
        try:
            async with self._gate_semaphore:
                decision = await self.gate.evaluate(transcript, background, policy)
            if self.paused:
                if stage_latencies:
                    await self._log_rejection(
                        transcript, "paused_during_gate", stage_latencies
                    )
                else:
                    await self._log_rejection(transcript, "paused_during_gate")
                return
            if not self._transcript_input_is_live(transcript):
                await self._log_muted_transcript(transcript, stage_latencies)
                return
            if not decision.accepted:
                # This path used to bypass _log_rejection(), leaving the
                # second-pass sweep permanently empty despite hundreds of
                # eligible judgment-stage rejections.
                self._remember_sweep_rejection(
                    transcript, decision.reason, stage_latencies
                )
                latencies = {
                    "stt": transcript.latency_ms,
                    **stage_latencies,
                    "gate": decision.latency_ms,
                }
                record = self._base_record(transcript)
                record.update(
                    {
                        "gate": False,
                        "gate_reason": decision.reason,
                        "answer": None,
                        "latencies_ms": latencies,
                    }
                )
                await self._log(record)
                return
            try:
                await self.app.add_question(transcript.utterance_id, decision.query)
            except Exception as exc:
                self._report(f"Unable to add answer card: {exc}")
            grounding: list[str] = []
            knowledge = getattr(self, "knowledge", None)
            if knowledge is not None:
                hit = knowledge.lookup(
                    decision.query,
                    self.config.knowledge.hit_threshold,
                    self.config.knowledge.min_query_words,
                )
                # Verbatim serving is for cue style only: the pack stores cue
                # cards, and an anticipated interview question resolves in
                # milliseconds instead of a full model round trip. Other styles
                # (interview prose, terse, spoken agent) instead ground the live
                # answer on the same entries so the miss is still fast and right.
                if hit is not None and self._answer_style_for_mode() == "cue":
                    await self._serve_cached_answer(
                        transcript,
                        decision.query,
                        hit,
                        decision.latency_ms,
                        stage_latencies,
                    )
                    return
                grounding = self._knowledge_grounding(decision.query)
            await self._enqueue_answer(
                _AnswerJob(
                    transcript,
                    decision.query,
                    answer_context,
                    decision.reason,
                    decision.latency_ms,
                    self._answer_style_for_mode(),
                    self._speech_mode_for_mode(),
                    stage_latencies,
                    grounding,
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
        arrivals = getattr(self, "_continuity_arrived_at", None)
        if arrivals is not None:
            arrivals.setdefault(transcript.utterance_id, time.perf_counter())
        agent_complete = bool(
            getattr(self, "agent_mode", False)
            and (
                transcript.channel != self._agent_customer_channel
                or _agent_turn_is_complete(transcript.text)
            )
        )
        for merged in self.continuity.push(
            transcript,
            complete=agent_complete,
            hold_s=(
                _AGENT_CONTINUITY_HOLD_S
                if getattr(self, "agent_mode", False)
                and transcript.channel == self._agent_customer_channel
                else None
            ),
        ):
            stage_latencies: dict[str, float] = {}
            if arrivals is not None:
                arrived_at = arrivals.pop(merged.utterance_id, None)
                if arrived_at is not None:
                    stage_latencies["continuity"] = max(
                        0.0, (time.perf_counter() - arrived_at) * 1000
                    )
            if stage_latencies:
                await self._process_transcript(merged, stage_latencies)
            else:
                # Lightweight fixtures (and third-party duck types) may still
                # replace the historical one-argument method. No timing map
                # means there is no metadata to pass anyway.
                await self._process_transcript(merged)

    async def _flush_continuity_transcripts(self) -> None:
        arrivals = getattr(self, "_continuity_arrived_at", None)
        for transcript in self.continuity.flush_expired():
            stage_latencies: dict[str, float] = {}
            if arrivals is not None:
                arrived_at = arrivals.pop(transcript.utterance_id, None)
                if arrived_at is not None:
                    stage_latencies["continuity"] = max(
                        0.0, (time.perf_counter() - arrived_at) * 1000
                    )
            if stage_latencies:
                await self._process_transcript(transcript, stage_latencies)
            else:
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
                captured_from = (
                    transcript.timestamp
                    if transcript.started_at is None
                    else transcript.started_at
                )
                if self.paused or captured_from < self._ignore_before:
                    await self._log_rejection(transcript, "paused")
                    continue
                if not self._transcript_input_is_live(transcript):
                    await self._log_muted_transcript(transcript)
                    continue
                if getattr(self, "agent_mode", False):
                    # Agent mode has an explicit customer channel, so it does
                    # not need mic-wins arbitration. Both streams remain fully
                    # isolated and the non-customer side is rejected later.
                    await self._ingest_transcript(transcript)
                    continue
                if transcript.channel == "sys":
                    # If a mic copy is already in context, suppress immediately.
                    if self.context.is_cross_channel_echo(transcript):
                        await self._log_rejection(transcript, "cross_channel_echo")
                    elif (
                        self._hold_system_for_echo
                        and self.input_channel_enabled("mic")
                    ):
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
                        self._answer_style_for_mode(),
                        self._speech_mode_for_mode(),
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
                segment_worker(
                    self.frames,
                    self.utterances,
                    self.segmenter,
                    self.stop,
                    mute=self._input_or_playback_muted,
                )
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
        if self.speech is not None:
            self._tasks.append(asyncio.create_task(self.speech.worker(self.stop)))
            self._tasks.append(asyncio.create_task(self._agent_greeting_worker()))
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
                latencies = {
                    "stt": job.transcript.latency_ms,
                    **job.stage_latencies_ms,
                    "gate": job.gate_latency_ms,
                }
                record.update(
                    {
                        "gate": True,
                        "gate_reason": job.reason,
                        "query": job.query,
                        "answer": "cancelled on shutdown",
                        "answer_status": "cancelled",
                        "latencies_ms": latencies,
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
            if self.speech is not None:
                self.speech.close()
            self.instances.close()


async def _main(
    voice: bool = False,
    allow_multiple: bool = False,
    web_port: int | None = None,
    web_open_browser: bool = False,
    web_allow_port_fallback: bool = True,
) -> None:
    config_path = Path("config.toml")
    config = load_config(config_path)
    app_factory = None
    if web_port is not None:
        # Imported only behind the flag: the default launch path must not
        # depend on the web console even existing.
        from .webui import WebUIApp

        app_factory = lambda controller: WebUIApp(  # noqa: E731
            controller,
            port=web_port,
            open_browser=web_open_browser,
            allow_port_fallback=web_allow_port_fallback,
        )
    # A second process repeats capture, Whisper, gating and every paid answer
    # request.  That exhausted GPU memory and doubled Claude traffic during the
    # voice demo. Claim the lifetime lock and heartbeat before loading either
    # model, with an explicit escape hatch for advanced experiments.
    instances = InstanceRegistry()
    if not allow_multiple and not instances.claim_exclusive():
        instances.close()
        print(
            "Ambient is already running (the application lock is held). "
            "Quit the existing pane with q, then relaunch; use "
            "--allow-multiple only for deliberate testing.",
            file=sys.stderr,
        )
        raise SystemExit(3)
    instance_count = instances.heartbeat_and_count()
    if instance_count > 1 and not allow_multiple:
        instances.close()
        print(
            "Ambient is already running. Quit the existing pane with q, "
            "then relaunch; use --allow-multiple only for deliberate testing.",
            file=sys.stderr,
        )
        raise SystemExit(3)
    if os.name == "posix":
        # Whisper's lazy model load (huggingface_hub -> tqdm) creates a
        # multiprocessing lock at FIRST TRANSCRIPTION. Start its resource
        # tracker while stderr is still the real terminal, but only after the
        # instance guard so a rejected launch spawns no helper process.
        from multiprocessing import resource_tracker

        resource_tracker.ensure_running()
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
    try:
        controller = AmbientController(
            config,
            config_path,
            voice=voice,
            instances=instances,
            app_factory=app_factory,
        )
    except BaseException:
        instances.close()
        raise
    loop = asyncio.get_running_loop()
    with suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, controller.app.exit)
    await controller.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ambientqa",
        description="Ambient: a passive listening pane that answers real questions.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="voice mode: speak answers aloud (Linux/PipeWire only)",
    )
    parser.add_argument(
        "--allow-multiple",
        action="store_true",
        help="allow another full capture/Whisper/answer pipeline (unsafe for demos)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help=(
            "serve the opt-in web console on localhost instead of the "
            "terminal pane (the terminal pane remains the default)"
        ),
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        help=(
            "pin the --web console to this localhost port (default: 8802; "
            "an unpinned default automatically moves if busy)"
        ),
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help=(
            "with --web: open the default browser once the console is "
            "serving (used by the mode picker, where no terminal shows the URL)"
        ),
    )
    args = parser.parse_args()
    if args.voice and (sys.platform == "win32" or shutil.which("paplay") is None):
        # Checked here, before the TUI owns the terminal, so the message is
        # actually readable.
        print(
            "--voice needs Linux with PipeWire's paplay available",
            file=sys.stderr,
        )
        raise SystemExit(2)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Inside a TUI a download progress bar could only render into Textual's
    # stdout capture as garbage; this also skips most of the tqdm machinery
    # that needed the multiprocessing lock in the first place.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    selected_web_port = (
        args.web_port if args.web_port is not None else _DEFAULT_WEB_PORT
    )
    try:
        asyncio.run(
            _main(
                voice=args.voice,
                allow_multiple=args.allow_multiple,
                web_port=selected_web_port if args.web else None,
                web_open_browser=args.open_browser,
                web_allow_port_fallback=args.web_port is None,
            )
        )
    except OSError as exc:
        if args.web and exc.errno == errno.EADDRINUSE:
            if args.web_port is None:
                detail = (
                    f"No free web-console port near {selected_web_port}. "
                    "Close the conflicting service or choose --web-port PORT."
                )
            else:
                detail = (
                    f"Web-console port {selected_web_port} is already in use. "
                    "Choose another --web-port or omit it for automatic fallback."
                )
            print(detail, file=sys.stderr)
            raise SystemExit(2) from None
        raise


if __name__ == "__main__":
    main()
