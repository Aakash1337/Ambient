from __future__ import annotations

import asyncio

import pytest

from ambientqa.bus import Transcript
from ambientqa.config import GateConfig
from ambientqa.gate import (
    QuestionGate,
    RecentAnswers,
    answer_containment,
    has_need_marker,
)

# The answer that was on screen while the user rehearsed it aloud.
ANSWER = (
    "Security in Bedrock mostly comes down to guardrails and data isolation. "
    "Guardrails let you filter prompts and outputs for things like PII, toxic "
    "content, or off-topic jailbreak attempts before they ever hit the model. "
    "On top of that, your data stays in your own VPC, isn't used to train the "
    "underlying models, and you get the usual IAM roles and KMS encryption for "
    "access control and encryption at rest and in transit."
)

# Verbatim mic lines from a real session; every one produced a duplicate answer.
REHEARSED = [
    "Security in Bedrock mostly comes down to guardrails and data isolation.",
    "prompts and outputs.",
    "And you can also usually have IAM roles and KMS encryption for access"
    " control at rest and in transit.",
]


def build_gate(**overrides) -> QuestionGate:
    gate = QuestionGate(GateConfig(**overrides))
    gate.mark_answer_text(ANSWER, timestamp=100.0)
    return gate


def evaluate(gate: QuestionGate, text: str, ts: float = 110.0):
    # These inputs all resolve before the Ollama stage, so no network is used.
    return asyncio.run(gate.evaluate(Transcript("mic", text, ts), []))


@pytest.mark.parametrize("text", REHEARSED)
def test_rehearsed_answer_is_not_re_answered(text: str) -> None:
    result = evaluate(build_gate(), text)
    assert not result.accepted, f"rehearsing the answer triggered a new answer: {text!r}"
    assert result.reason == "answer_echo"


def test_explicit_question_is_exempt_from_echo_suppression() -> None:
    # Shares vocabulary with the answer, but the user genuinely asked.
    result = evaluate(build_gate(), "What are guardrails in Bedrock?")
    assert result.accepted
    assert result.reason == "explicit_interrogative"


def test_disabled_when_ratio_is_zero() -> None:
    gate = build_gate(answer_echo_ratio=0.0)
    # Falls through to the semantic stage instead of short-circuiting as echo.
    assert gate.config.answer_echo_ratio == 0.0
    assert gate.recent_answers.best_containment(REHEARSED[0], timestamp=110.0) > 0.5


# --- the two signals that make this work, tested independently ---


def test_containment_is_one_directional() -> None:
    # A short recital of a long answer scores high; containment must not be
    # dragged down by the answer's extra length the way a symmetric ratio is.
    assert answer_containment("prompts and outputs", ANSWER) >= 0.9
    assert answer_containment("I should probably get lunch soon", ANSWER) < 0.4


def test_need_markers_protect_genuine_follow_ups() -> None:
    # Overlaps the answer heavily, but expresses not-knowing, so it is a question.
    assert has_need_marker("I don't understand how the VPC isolation works")
    assert has_need_marker("remind me what guardrails filter")
    # Pure recitation carries no such marker.
    assert not has_need_marker("Guardrails let you filter prompts and outputs")
    assert not has_need_marker("IAM roles and KMS encryption at rest and in transit")


def test_window_expiry_forgets_old_answers() -> None:
    recent = RecentAnswers(window_s=60.0)
    recent.add(ANSWER, timestamp=0.0)
    assert recent.best_containment("prompts and outputs", timestamp=10.0) >= 0.9
    assert recent.best_containment("prompts and outputs", timestamp=500.0) == 0.0
