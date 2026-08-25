from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ambientqa.__main__ import AmbientController, _AnswerJob
from ambientqa.bus import AnswerResult, DropOldestQueue, Transcript
from ambientqa.config import default_config
from ambientqa.context import TranscriptContext
from ambientqa.knowledge import KnowledgeHit, KnowledgeIndex, _parse_entries
from ambientqa.profile import Profile

GUARDDUTY_DOC = """# Detection

## What is GuardDuty?
Aliases: guardduty | what does guardduty do
Tags: guardduty, threat-detection
GuardDuty is AWS's managed threat detection over CloudTrail, VPC, and DNS logs.

• No agents to deploy
• Detection, not remediation
"""

IAM_DOC = """# IAM & Identity

## What is the difference between an IAM user and an IAM role?
Aliases: iam user vs role | roles versus users | when do you use a role
Tags: iam, identity, roles, sts

A user is a permanent identity with long-lived keys; a role is assumed for short-lived credentials via STS.

• Users: people, static keys
• Roles: workloads and federation, temp creds
• Prefer roles, avoid long-lived keys
"""


def _index() -> KnowledgeIndex:
    return KnowledgeIndex(entries=_parse_entries(IAM_DOC, "iam-identity"), doc_count=1)


@dataclass
class Decision:
    accepted: bool
    query: str
    reason: str = "explicit_interrogative"
    latency_ms: float = 5.0


@dataclass
class FakeGate:
    decision: Decision
    answered: list[str] = field(default_factory=list)
    answer_text: list[str] = field(default_factory=list)

    async def evaluate(self, _transcript, _background, _policy):
        return self.decision

    def mark_answered(self, text: str, _ts: float) -> None:
        self.answered.append(text)

    def mark_answer_text(self, text: str, _ts: float) -> None:
        self.answer_text.append(text)


@dataclass
class FakeApp:
    resolved: list[AnswerResult] = field(default_factory=list)
    questions: list[tuple[str, str]] = field(default_factory=list)

    def resolve_answer(self, result: AnswerResult) -> None:
        self.resolved.append(result)

    async def add_question(self, question_id: str, question: str) -> None:
        self.questions.append((question_id, question))


@dataclass
class FakeSpeech:
    queued: list[tuple[str, str]] = field(default_factory=list)
    muted: bool = False

    def enqueue(self, question_id: str, text: str) -> None:
        if self.muted:
            return
        self.queued.append((question_id, text))

    def stop_current(self, flush: bool = False) -> None:
        if flush:
            self.queued.clear()


@dataclass
class FakeLogger:
    records: list[dict] = field(default_factory=list)

    def append(self, record: dict) -> None:
        self.records.append(record)


def build_controller(
    index: KnowledgeIndex | None,
    *,
    accepted_query: str,
    style: str = "cue",
    speech: FakeSpeech | None = None,
) -> AmbientController:
    controller = AmbientController.__new__(AmbientController)
    controller.config = default_config()
    controller.config.knowledge.enabled = True
    controller.config.answer.style = style
    controller.agent_mode = False
    controller.interaction_mode = "normal"
    controller.paused = False
    controller.input_channels_enabled = {"mic": True, "sys": True}
    controller._input_after = {"mic": 0.0, "sys": 0.0}
    controller._voice_ignore_before = 0.0
    controller.knowledge = index
    controller.gate = FakeGate(Decision(accepted=True, query=accepted_query))
    controller.app = FakeApp()
    controller.logger = FakeLogger()
    controller.context = TranscriptContext()
    controller.speech = speech
    controller.answer_count = 0
    controller.estimated_tokens = 0
    controller._qa_history = deque(maxlen=8)
    controller._recent_rejections = deque(maxlen=24)
    controller._sweep_ready_at = {}
    controller._sweep_stage_latencies = {}
    controller._open_answer_jobs = {}
    controller.answers = DropOldestQueue(16)
    controller._last_completed_answer = None
    controller._last_voice_answer = None
    controller._gate_semaphore = asyncio.Semaphore(4)
    controller._report = lambda _message: None  # type: ignore[method-assign]
    return controller


def _transcript(text: str) -> Transcript:
    return Transcript("sys", text, 100.0, utterance_id="q1", latency_ms=12.0)


def test_strong_cache_hit_is_served_verbatim_without_a_model_call() -> None:
    controller = build_controller(
        _index(), accepted_query="Can you explain IAM roles versus users?"
    )
    transcript = _transcript("Can you explain IAM roles versus users?")

    asyncio.run(controller._gate_and_enqueue(transcript, [], [], "full", {}))

    # Answered from cache: nothing was enqueued for the live model.
    assert controller.answers.empty()
    assert controller._open_answer_jobs == {}
    resolved = controller.app.resolved[-1]
    assert resolved.answer.startswith("A user is a permanent identity")
    assert resolved.latency_ms == 0.0
    assert controller.answer_count == 1
    # A cache hit costs no output tokens, so the estimate must not move.
    assert controller.estimated_tokens == 0
    assert list(controller._qa_history) == [
        (
            "Can you explain IAM roles versus users?",
            resolved.answer,
        )
    ]
    record = controller.logger.records[-1]
    assert record["gate_reason"] == "knowledge_cache"
    assert record["answer_status"] == "ok"
    assert record["knowledge_match"].startswith("What is the difference")
    assert record["latencies_ms"]["answer"] == 0.0
    # The cache hit is repeatable via the existing local-repeat path.
    assert controller._last_completed_answer is not None


def test_cache_miss_enqueues_a_live_job_with_grounding() -> None:
    controller = build_controller(
        _index(), accepted_query="How should I think about IAM policy evaluation?"
    )
    transcript = _transcript("How should I think about IAM policy evaluation?")

    asyncio.run(controller._gate_and_enqueue(transcript, [], [], "full", {}))

    # Not a confident enough match to serve verbatim, so it goes to the model...
    assert controller.app.resolved == []
    job = controller.answers.get_nowait()
    controller.answers.task_done()
    # ...but the closest entry rides along as authoritative reference.
    assert job.grounding
    assert any("short-lived credentials via STS" in block for block in job.grounding)


def test_unrelated_question_enqueues_with_no_grounding() -> None:
    controller = build_controller(
        _index(), accepted_query="What time does the cafeteria open today?"
    )
    transcript = _transcript("What time does the cafeteria open today?")

    asyncio.run(controller._gate_and_enqueue(transcript, [], [], "full", {}))

    job = controller.answers.get_nowait()
    controller.answers.task_done()
    assert job.grounding == []


def test_non_cue_style_grounds_instead_of_serving_verbatim() -> None:
    # A strong hit, but interview prose must not be replaced by a stored cue
    # card; the entry is injected as grounding for the live answer instead.
    controller = build_controller(
        _index(),
        accepted_query="Can you explain IAM roles versus users?",
        style="interview",
    )
    transcript = _transcript("Can you explain IAM roles versus users?")

    asyncio.run(controller._gate_and_enqueue(transcript, [], [], "full", {}))

    assert controller.app.resolved == []
    job = controller.answers.get_nowait()
    controller.answers.task_done()
    assert job.answer_style == "interview"
    assert job.grounding


def test_served_cache_answer_speaks_opening_line_in_voice_mode() -> None:
    speech = FakeSpeech()
    controller = build_controller(
        _index(),
        accepted_query="iam roles versus users",
        speech=speech,
    )
    hit = KnowledgeHit(controller.knowledge.entries[0], 0.9)
    mic = Transcript("mic", "iam roles versus users", 100.0, "m1", 10.0)

    asyncio.run(
        controller._serve_cached_answer(mic, "iam roles versus users", hit, 5.0, {})
    )

    # first_line speech mode speaks only the sayable opening sentence.
    assert speech.queued == [
        ("m1", "A user is a permanent identity with long-lived keys; a role is "
               "assumed for short-lived credentials via STS.")
    ]


def test_disabled_pack_never_serves_or_grounds() -> None:
    controller = build_controller(
        None, accepted_query="Can you explain IAM roles versus users?"
    )
    transcript = _transcript("Can you explain IAM roles versus users?")

    asyncio.run(controller._gate_and_enqueue(transcript, [], [], "full", {}))

    job = controller.answers.get_nowait()
    controller.answers.task_done()
    assert job.grounding == []
    assert controller.app.resolved == []


# --- pack follows the profile (reload on switch) ---


def _reload_controller(
    tmp_path: Path, *, enabled: bool = True, fallback: str = ""
) -> AmbientController:
    controller = AmbientController.__new__(AmbientController)
    controller.config = default_config()
    controller.config.knowledge.enabled = enabled
    controller.config.knowledge.path = fallback
    controller.config_path = tmp_path / "config.toml"
    controller._report = lambda _message: None  # type: ignore[method-assign]
    controller.knowledge = None
    return controller


def _write_pack(tmp_path: Path, name: str, doc: str) -> None:
    pack_dir = tmp_path / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "doc.md").write_text(doc, encoding="utf-8")


def test_profile_declared_pack_is_loaded_on_apply(tmp_path: Path) -> None:
    _write_pack(tmp_path, "packA", IAM_DOC)
    controller = _reload_controller(tmp_path)

    controller._reload_knowledge_for_profile(
        Profile("A", "t", "b", [], "", knowledge="packA")
    )

    assert controller.knowledge is not None
    assert controller.knowledge.lookup("iam roles versus users", 0.5, 3) is not None


def test_profile_without_pack_and_no_fallback_has_no_cache(tmp_path: Path) -> None:
    controller = _reload_controller(tmp_path)
    controller._reload_knowledge_for_profile(Profile("A", "t", "b", [], ""))
    assert controller.knowledge is None


def test_global_fallback_used_when_profile_declares_none(tmp_path: Path) -> None:
    _write_pack(tmp_path, "packF", GUARDDUTY_DOC)
    controller = _reload_controller(tmp_path, fallback="packF")
    controller._reload_knowledge_for_profile(Profile("A", "t", "b", [], ""))
    assert controller.knowledge is not None
    assert controller.knowledge.lookup("what is guardduty", 0.5, 3) is not None


def test_declared_pack_overrides_global_fallback(tmp_path: Path) -> None:
    _write_pack(tmp_path, "packA", IAM_DOC)
    _write_pack(tmp_path, "packF", GUARDDUTY_DOC)
    controller = _reload_controller(tmp_path, fallback="packF")

    controller._reload_knowledge_for_profile(
        Profile("A", "t", "b", [], "", knowledge="packA")
    )

    # The profile's own pack loaded, not the fallback.
    assert controller.knowledge.lookup("iam roles versus users", 0.5, 3) is not None
    assert controller.knowledge.lookup("what is guardduty", 0.5, 3) is None


def test_disabled_feature_loads_no_pack_even_when_declared(tmp_path: Path) -> None:
    _write_pack(tmp_path, "packA", IAM_DOC)
    controller = _reload_controller(tmp_path, enabled=False)
    controller._reload_knowledge_for_profile(
        Profile("A", "t", "b", [], "", knowledge="packA")
    )
    assert controller.knowledge is None


def test_switching_to_a_pack_less_profile_clears_the_pack(tmp_path: Path) -> None:
    _write_pack(tmp_path, "packA", IAM_DOC)
    controller = _reload_controller(tmp_path)

    controller._reload_knowledge_for_profile(
        Profile("A", "t", "b", [], "", knowledge="packA")
    )
    assert controller.knowledge is not None

    controller._reload_knowledge_for_profile(Profile("B", "t", "b", [], ""))
    assert controller.knowledge is None
