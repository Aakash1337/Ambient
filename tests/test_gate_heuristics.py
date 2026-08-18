from __future__ import annotations

import asyncio
import threading
import time

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
    # The fast-accept is exempt from answer-echo suppression but NOT from
    # re-ask dedupe, so dedupe must keep running first: this input is a
    # '?'-terminated interrogative the fast-accept would otherwise take.
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


# --- regression: rule ordering must never eat a well-formed interrogative ---
# TAG_PATTERNS anchors on the final word and the vocative check on the first
# token; both used to run before the fast-accept, so a '?'-terminated question
# whose last word happened to be a tag word (or whose first token was a
# capitalized discourse marker) was rejected without the question between those
# two words ever being looked at.


@pytest.mark.parametrize(
    "text",
    [
        "Is my understanding of the GIL right?",
        "Which one is right?",
        "Is that answer okay?",
    ],
)
def test_interrogative_ending_on_a_tag_word_still_fast_accepts(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("accept", "explicit_interrogative")


@pytest.mark.parametrize(
    "text",
    [
        # Interrogative-shaped, but the utterance IS the tag phrase (nothing
        # but function words around it) or a statement with a comma-appended
        # tag. The fast-accept must never answer the interviewer's rhetorical
        # check-in.
        "Am I right?",
        "Do you know what I mean?",
        "Should we deploy on Friday, okay?",
    ],
)
def test_pure_tag_phrases_are_rejected_despite_interrogative_shape(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("reject", "tag_or_rhetorical")


def test_names_that_look_like_interrogatives_stay_vocative() -> None:
    # A name that casefolds into INTERROGATIVES must not ride the fast-accept
    # past the vocative check: this is addressed to Will, not the assistant.
    decision = heuristic_decision("Will, can you review this?")
    assert (decision.outcome, decision.reason) == ("reject", "human_vocative")


@pytest.mark.parametrize(
    "text",
    [
        "Okay, can you explain the CAP theorem?",
        "Great, could you walk me through your project?",
        # "right" is also a tag/filler word, making it the interesting prefix.
        "Right, so what does the scheduler actually do?",
    ],
)
def test_acknowledgment_lead_ins_do_not_block_fast_accept(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("accept", "explicit_interrogative")


def test_leading_discourse_marker_is_not_a_vocative_name() -> None:
    # Whisper capitalizes every sentence start, so without the '?' the
    # fast-accept cannot save this one; it must fall through to the semantic
    # gate rather than dying as human_vocative.
    decision = heuristic_decision("Okay, can you explain the CAP theorem.")
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
    assert gate.available is True
    assert gate._warming is None


def test_warmup_failure_degrades_to_heuristics_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    gate = OllamaGate(GateConfig(), status_callback=messages.append)

    def failing_post(_body, _timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(gate, "_post", failing_post)
    assert asyncio.run(gate.warmup()) is False
    assert gate.available is False
    assert any("heuristics-only" in message for message in messages)
    assert gate._warming is None


def test_warmup_cancellation_does_not_wait_for_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cold Ollama load holds the 90s warmup request open. Task.cancel cannot
    # interrupt a running executor future, so a to_thread warmup pinned shutdown
    # until the HTTP call returned; the daemon-thread version must let the
    # awaiting task finish cancelled immediately, with the request still open.
    gate = OllamaGate(GateConfig())
    release = threading.Event()
    entered = threading.Event()

    def blocking_post(_body, _timeout=None):
        entered.set()
        release.wait(timeout=30.0)
        return {"message": {"content": '{"q":false,"query":""}'}}

    monkeypatch.setattr(gate, "_post", blocking_post)

    async def drive() -> float:
        task = asyncio.create_task(gate.warmup())
        await asyncio.to_thread(entered.wait, 5.0)
        started = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.perf_counter() - started

    try:
        elapsed = asyncio.run(drive())
    finally:
        release.set()
    assert elapsed < 1.0
    assert gate._warming is None


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
    assert result.reason == "trailing_fragment"


# --- vocative demotion: full-policy channels consult the semantic gate ---
# On the sys channel a vocative is usually the interviewer addressing the
# CANDIDATE by name -- the tool's core scenario -- so it must reach the gate,
# whose prompt already returns FALSE for questions aimed at another human. On
# the mic channel the hard reject stands: the user hailing someone by name is
# definitionally talking to another human.


def test_full_policy_routes_vocative_to_semantic_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())
    consulted: list[str] = []

    async def fake_classify(text: str, _context: list[str]) -> tuple[bool, str]:
        consulted.append(text)
        return True, "Can you explain decorators?"

    monkeypatch.setattr(gate.ollama, "classify", fake_classify)
    result = asyncio.run(
        gate.evaluate(
            Transcript("sys", "Aakash, can you explain decorators?", 1.0, "u1"),
            [],
            policy="full",
        )
    )
    assert consulted == ["Aakash, can you explain decorators?"]
    assert result.accepted is True
    assert result.reason == "ollama_accept"
    assert result.query == "Can you explain decorators?"


def test_full_policy_vocative_rejected_by_semantic_gate_stays_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())

    async def fake_classify(_text: str, _context: list[str]) -> tuple[bool, str]:
        return False, ""

    monkeypatch.setattr(gate.ollama, "classify", fake_classify)
    result = asyncio.run(
        gate.evaluate(
            Transcript("sys", "Sarah, can you grab the door?", 1.0, "u1"),
            [],
            policy="full",
        )
    )
    assert result.accepted is False
    assert result.reason == "ollama_reject"


def test_explicit_policy_keeps_the_vocative_hard_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("mic-channel vocative reached the semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript("mic", "Aakash, can you explain decorators?", 1.0, "u1"),
            [],
            policy="explicit",
        )
    )
    assert result.accepted is False
    assert result.reason == "human_vocative"


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


# --- recovery: a re-ask after a missed answer must be answered, not deduped ---
# The first answer can be wrong for reasons upstream of the gate (a mishearing
# turned "prompt engineering" into "prompt injection"). Near-duplicate dedupe
# exists for MECHANICAL duplicates, which arrive within seconds; a human retry
# arrives after they have read the bad answer, and eating it strands them.


def test_near_duplicate_only_within_the_reask_cooldown() -> None:
    gate = QuestionGate(GateConfig(reask_cooldown_s=8.0))
    gate.mark_answered("Am I going to use prompt injection?", timestamp=100.0)

    inside = asyncio.run(
        gate.evaluate(
            Transcript("mic", "Am I going to use prompt engineering?", 104.0, "u1"),
            [],
            policy="explicit",
        )
    )
    assert inside.accepted is False
    assert inside.reason == "near_duplicate"

    outside = asyncio.run(
        gate.evaluate(
            Transcript("mic", "Am I going to use prompt engineering?", 115.0, "u2"),
            [],
            policy="explicit",
        )
    )
    assert outside.accepted is True
    assert outside.reason == "explicit_interrogative"


def test_statement_retry_of_recent_answer_is_accepted_outright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Accepted without consulting the semantic gate: a retry reads as a
    # correction or a plan, which the gate prompt rejects as narration. The
    # answerer resolves it against its Q&A history instead.
    gate = QuestionGate(GateConfig())
    gate.mark_answered(
        "What am I going to use? Am I going to use prompt injection?",
        timestamp=100.0,
    )

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("retry must not depend on the semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript(
                "mic",
                "I am going to use prompt engineering, and RAG or multi-agent orchestration.",
                130.0,
                "u1",
            ),
            [],
            policy="explicit",
        )
    )
    assert result.accepted is True
    assert result.reason == "reask_of_recent"
    assert "prompt engineering" in result.query


def test_unrelated_statement_still_never_reaches_the_semantic_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())
    gate.mark_answered("What am I going to use, prompt injection?", timestamp=100.0)

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("unrelated narration reached the semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript(
                "mic",
                "So yesterday I finally cleaned up the deployment pipeline scripts.",
                130.0,
                "u1",
            ),
            [],
            policy="explicit",
        )
    )
    assert result.accepted is False
    assert result.reason == "not_a_direct_question"


# --- imperative requests: command-form asks have no '?' to fast-accept on ---
# "Evaluation metrics. Talk about them." was structurally unanswerable on an
# "explicit"-policy channel: no question mark, no interrogative start. A
# sentence-initial request verb is as explicit as an ask gets, and it is how
# interviewers open ("Tell me about yourself.").


@pytest.mark.parametrize(
    "text",
    [
        "Evaluation metrics. Talk about them.",
        "Evaluation matrix talk about them",
        "So, tell me about your experience with Kubernetes.",
        "Explain the CAP theorem.",
        "Please walk me through your project.",
    ],
)
def test_imperative_requests_are_accepted(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("accept", "imperative_request")


@pytest.mark.parametrize(
    "text",
    [
        # Idiom, narrated plan, and a request addressed to a named human:
        # each shares the imperative shape and asks the assistant nothing.
        "Tell me about it.",
        "Give me a second.",
        "We'll talk about the design.",
        "I'm going to talk about scaling next.",
        "Sarah, tell me about your weekend.",
    ],
)
def test_imperative_lookalikes_are_not_accepted(text: str) -> None:
    assert heuristic_decision(text).outcome != "accept"
