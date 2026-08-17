from __future__ import annotations

import asyncio

import pytest

from ambientqa.config import GateConfig
from ambientqa.bus import Transcript
from ambientqa.gate import OllamaGate, PROMPTS, QuestionGate, heuristic_decision
from ambientqa.profile import Profile


@pytest.mark.parametrize("text", ["why?", "hello there", "um"])
def test_rejects_fewer_than_minimum_real_words(text: str) -> None:
    assert heuristic_decision(text).outcome == "reject"
    assert heuristic_decision(text).reason == "too_few_words"


@pytest.mark.parametrize("text", ["uh um hmm", "yeah okay right", "um, uh, yeah?"])
def test_rejects_filler_only_content(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("reject", "filler_only")


@pytest.mark.parametrize(
    "ending",
    [
        "right?",
        "you know?",
        "isn't it?",
        "innit?",
        "yeah?",
        "okay?",
        "know what I mean?",
        "am I right?",
    ],
)
def test_rejects_tag_and_rhetorical_endings(ending: str) -> None:
    decision = heuristic_decision(f"This is already settled, {ending}")
    assert (decision.outcome, decision.reason) == ("reject", "tag_or_rhetorical")


@pytest.mark.parametrize(
    "text",
    [
        "Hey Sarah, can you close the window?",
        "Hey Jamal could you send that?",
        "Maria, can you review this?",
        "O'Neil, would you join us?",
    ],
)
def test_rejects_human_vocatives(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("reject", "human_vocative")


def test_lowercase_non_name_does_not_trigger_vocative_rule() -> None:
    assert heuristic_decision("server, can you explain this?").reason != "human_vocative"


def test_rejects_near_duplicate_recent_answer() -> None:
    decision = heuristic_decision(
        "What causes the blue ocean tides?",
        recent_answered=["What causes blue ocean tides?"],
    )
    assert (decision.outcome, decision.reason) == ("reject", "near_duplicate")


@pytest.mark.parametrize(
    "text",
    [
        "What causes ocean tides?",
        "Why does ice float?",
        "How can I parse JSON?",
        "When will this finish?",
        "Where are logs stored?",
        "Who wrote this package?",
        "Which option is fastest?",
        "Whose turn is next?",
        "Can Python do this?",
        "Could this be cached?",
        "Would that solve it?",
        "Should we retry now?",
        "Do birds migrate south?",
        "Does Windows support WASAPI?",
        "Did the process exit?",
        "Is this thread safe?",
        "Are those values correct?",
        "Was the request sent?",
        "Were any frames dropped?",
        "Will it keep running?",
        "Have we tested this?",
        "Has the model loaded?",
        "Am I using CUDA?",
        "May this run offline?",
        "Might this be stale?",
        "Shall we continue now?",
    ],
)
def test_fast_accepts_question_mark_interrogatives(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("accept", "explicit_interrogative")


def test_non_question_falls_through_to_local_llm() -> None:
    decision = heuristic_decision("I wonder what that option means")
    assert (decision.outcome, decision.reason) == ("llm", "needs_semantic_gate")


def test_right_suffix_inside_a_word_is_not_a_tag() -> None:
    decision = heuristic_decision("What protects a software copyright?")
    assert decision.outcome == "accept"


def test_numbers_are_not_counted_as_real_words() -> None:
    decision = heuristic_decision("123 456 789?")
    assert (decision.outcome, decision.reason) == ("reject", "too_few_words")


def test_all_prompt_modes_are_shipped() -> None:
    assert set(PROMPTS) == {"strict", "balanced", "eager"}
    assert all(PROMPTS[mode].strip() for mode in PROMPTS)


def test_every_ollama_body_disables_thinking() -> None:
    gate = OllamaGate(GateConfig())
    body = gate._body([{"role": "user", "content": "test"}])
    assert body["think"] is False
    assert body["stream"] is False
    assert body["keep_alive"] == "30m"
    assert body["options"] == {"temperature": 0, "num_predict": 64}
    assert body["format"]["required"] == ["q", "query"]


def test_warmup_uses_the_same_think_false_body(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = OllamaGate(GateConfig())
    seen = {}

    def fake_post(body, _timeout=None):
        seen.update(body)
        return {"message": {"content": '{"q":false,"query":""}'}}

    monkeypatch.setattr(gate, "_post", fake_post)
    assert asyncio.run(gate.warmup()) is True
    assert seen["think"] is False


def test_empty_self_contained_rewrite_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = OllamaGate(GateConfig())

    def fake_post(_body, _timeout=None):
        return {"message": {"content": '{"q":true,"query":""}'}}

    monkeypatch.setattr(gate, "_post", fake_post)
    accepted, query = asyncio.run(gate.classify("What about the second one?", []))
    assert accepted is False
    assert query == ""


def test_schema_violating_string_false_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = OllamaGate(GateConfig())

    def fake_post(_body, _timeout=None):
        return {"message": {"content": '{"q":"false","query":"invented question"}'}}

    monkeypatch.setattr(gate, "_post", fake_post)
    accepted, query = asyncio.run(gate.classify("ambient statement only", []))
    assert accepted is False
    assert query == ""


def test_profile_topic_is_disambiguation_only_in_gate_prompt() -> None:
    profile = Profile(
        "AWS",
        "AWS cloud architecture focused on Amazon Bedrock",
        "This must not appear",
        ["Guardrails"],
        "",
    )
    gate = OllamaGate(GateConfig(), profile=profile)
    prompt = gate.system_prompt
    assert profile.topic in prompt
    assert profile.background not in prompt
    assert "Guardrails" not in prompt
    assert "never supply a topic the current utterance lacks" in prompt
    assert "not a relevance filter" in prompt


def test_profile_cannot_turn_contentless_fragment_into_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = Profile("AWS", "Amazon Bedrock security", "", ["Bedrock"], "")
    gate = QuestionGate(GateConfig(), profile=profile)

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("contentless fragment reached semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript("mic", "uh, um, so, the thing is", 1.0, "u1"),
            ["We were discussing Amazon Bedrock security"],
        )
    )
    assert result.accepted is False
    assert result.reason in {"no_content_words", "trailing_fragment"}


# --- regression: contentless fragments must never reach the semantic gate ---
# A trailing-off fragment used to pass Stage A, and the LLM then invented a
# question out of the surrounding transcript context ("What is the retry logic?").


@pytest.mark.parametrize(
    "text",
    [
        "uh, um, so, the thing is",
        "and then it was like, you know, just",
        "so I mean, it was, well",
        "but that is the thing with that",
    ],
)
def test_contentless_fragments_rejected(text: str) -> None:
    assert heuristic_decision(text).outcome == "reject"
    assert heuristic_decision(text).reason in {
        "no_content_words",
        "filler_only",
        "tag_or_rhetorical",
        "trailing_fragment",
    }


@pytest.mark.parametrize(
    "text",
    [
        "hmm I have no idea how python decorators handle arguments",
        "I wonder how much memory that actually uses",
        "remind me how to flush a socket in python",
        "what is the default timeout for fetch in node",
    ],
)
def test_real_questions_survive_content_word_rule(text: str) -> None:
    assert heuristic_decision(text).outcome != "reject"


def test_short_explicit_question_precedes_content_word_rejection() -> None:
    decision = heuristic_decision("So, how are you?")
    assert (decision.outcome, decision.reason) == (
        "accept",
        "explicit_interrogative",
    )


@pytest.mark.parametrize(
    "text",
    [
        "So, tell me about",
        "how you manage context in",
    ],
)
def test_trailing_function_word_fragments_are_rejected(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("reject", "trailing_fragment")
