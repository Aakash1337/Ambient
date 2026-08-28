from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ambientqa import gate as gate_module
from ambientqa.bus import Transcript
from ambientqa.config import GateConfig
from ambientqa.gate import (
    PROMPTS,
    OllamaGate,
    QuestionGate,
    heuristic_decision,
    is_question_shaped,
)
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


_REPORTED_CONTRASTIVE_FOLLOWUP = (
    "You said there wasn't much support for Teams and Firefox in terms of "
    "sharing audio, right? But I was sharing the whole screen of my monitor. "
    "So it should just share the audio that the system is getting, right?"
)


def test_contrastive_callback_ending_in_tag_reaches_semantic_gate() -> None:
    # 10:48:58 production regression: looking only at the final "right?" hid
    # the actual information need -- reconcile an earlier answer with a
    # conflicting observation.  This is intentionally not fast-accepted; the
    # semantic gate still decides whether the contrast really asks anything.
    decision = heuristic_decision(_REPORTED_CONTRASTIVE_FOLLOWUP)
    assert (decision.outcome, decision.reason) == ("llm", "needs_semantic_gate")


def test_contrastive_callback_is_judged_on_explicit_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())
    calls: list[str] = []

    async def fake_classify(text: str, _context: list[str]) -> tuple[bool, str]:
        calls.append(text)
        return True, "Should sharing the whole screen also share system audio?"

    monkeypatch.setattr(gate.ollama, "classify", fake_classify)
    result = asyncio.run(
        gate.evaluate(
            Transcript(
                "mic",
                _REPORTED_CONTRASTIVE_FOLLOWUP,
                100.0,
                "reported-tag-followup",
            ),
            ["[mic] Are there any workarounds?"],
            policy="explicit",
        )
    )

    assert calls == [_REPORTED_CONTRASTIVE_FOLLOWUP]
    assert (result.accepted, result.reason, result.query) == (
        True,
        "ollama_accept",
        "Should sharing the whole screen also share system audio?",
    )


@pytest.mark.parametrize(
    "text",
    [
        # A lone callback asks only for agreement; there is no conflicting
        # observation for Ambient to reconcile.
        "You said the retry is automatic, right?",
        # Multiple tags alone are not enough.  Requiring an explicit contrast
        # prevents ordinary agreement-seeking conversation from flooding cards.
        "You said the retry is automatic, right? So we're done, right?",
        # A contrast is not enough without an explicit callback to an earlier
        # answer; this is ordinary conversation between the two people.
        "The retry is automatic, right? But it failed again, right?",
    ],
)
def test_non_substantive_or_non_callback_tags_stay_rejected(text: str) -> None:
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


def test_multiword_acknowledgment_does_not_block_fast_accept() -> None:
    decision = heuristic_decision("Got it, so what should I improve?")
    assert (decision.outcome, decision.reason) == (
        "accept",
        "explicit_interrogative",
    )


def test_multiword_acknowledgment_with_lost_mark_reaches_semantic_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (
        "Got it, so, what do I do to improve, the, the, the, the, the, "
        "the, the, the, the, the, the, video, video, audio capture, "
        "video, audio capture,"
    )
    gate = QuestionGate(GateConfig())
    calls: list[str] = []

    async def fake_classify(raw: str, _context: list[str]) -> tuple[bool, str]:
        calls.append(raw)
        return True, "What do I do to improve video and audio capture?"

    monkeypatch.setattr(gate.ollama, "classify", fake_classify)
    result = asyncio.run(
        gate.evaluate(
            Transcript("mic", text, 100.0, "reported-disfluent-question"),
            [],
            policy="explicit",
        )
    )

    assert calls == [text]
    assert (result.accepted, result.reason, result.query) == (
        True,
        "ollama_accept",
        "What do I do to improve video and audio capture?",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Got it, so the video capture needs improvement.",
        "Got it working, so the video capture is better.",
    ],
)
def test_multiword_acknowledgment_does_not_make_narration_question_shaped(
    text: str,
) -> None:
    assert is_question_shaped(text) is False


def test_acknowledgment_prefixed_other_side_narration_is_not_blindly_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Got it, so what I did was improve video capture."
    gate = QuestionGate(GateConfig())
    calls: list[str] = []

    async def reject_narration(raw: str, _context: list[str]) -> tuple[bool, str]:
        calls.append(raw)
        return False, ""

    monkeypatch.setattr(gate.ollama, "classify", reject_narration)
    result = asyncio.run(
        gate.evaluate(
            Transcript("sys", text, 100.0, "other-side-narration"),
            [],
            policy="full",
        )
    )

    assert calls == [text]
    assert (result.accepted, result.reason) == (False, "ollama_reject")


def test_leading_discourse_marker_is_not_a_vocative_name() -> None:
    # Whisper capitalizes every sentence start, so without the '?' the
    # fast-accept cannot save this one; it must fall through to the semantic
    # gate rather than dying as human_vocative.
    decision = heuristic_decision("Okay, can you explain the CAP theorem.")
    assert (decision.outcome, decision.reason) == ("llm", "needs_semantic_gate")


def test_again_prefixed_command_is_not_mistaken_for_a_persons_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = heuristic_decision("Again, describe RAG pipelines.")
    assert (decision.outcome, decision.reason) == (
        "accept",
        "imperative_request",
    )

    # Prove the live mic policy fast-accepts the exact reported transcript;
    # neither the semantic model nor a later recovery pass should be needed.
    gate = QuestionGate(GateConfig())

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("clear imperative reached the semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript(
                "mic", "Again, describe RAG pipelines.", 1.0, "reported-miss"
            ),
            [],
            policy="explicit",
        )
    )
    assert (result.accepted, result.reason) == (True, "imperative_request")


def test_again_prefixed_narration_is_not_accidentally_accepted() -> None:
    decision = heuristic_decision("Again, we deployed the RAG pipeline.")
    assert (decision.outcome, decision.reason) == ("llm", "needs_semantic_gate")


@pytest.mark.parametrize(
    "text",
    [
        "EXPLAIN RAG",
        "Define RAG.",
        "Describe Kubernetes.",
    ],
)
def test_complete_two_word_imperatives_bypass_the_noise_floor(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == (
        "accept",
        "imperative_request",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Tell me.",
        "Talk about.",
        "Walk me.",
        "Give me.",
        "Explain about.",
        "EXPREME LAG!",
    ],
)
def test_incomplete_or_declarative_two_word_phrases_stay_rejected(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("reject", "too_few_words")


def test_generic_honorific_does_not_turn_a_request_into_human_vocative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Sir, talk to me about RAG."
    assert heuristic_decision(text).reason == "imperative_request"

    gate = QuestionGate(GateConfig())

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("honorific-prefixed request reached semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript("mic", text, 1.0, "reported-honorific-miss"),
            [],
            policy="explicit",
        )
    )
    assert (result.accepted, result.reason) == (True, "imperative_request")


def test_generic_honorific_does_not_fast_accept_an_action_for_a_human() -> None:
    decision = heuristic_decision("Sir, can you close the window?")
    assert (decision.outcome, decision.reason) == (
        "reject",
        "human_vocative",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sir, give me a second.",
        "Again, tell me about it.",
        "Okay, give me a break.",
    ],
)
def test_prefixed_imperative_idioms_are_not_information_requests(text: str) -> None:
    assert heuristic_decision(text).reason != "imperative_request"


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


def test_normal_gate_timeout_does_not_join_a_stuck_http_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = OllamaGate(GateConfig(request_timeout_s=0.01))
    release = threading.Event()

    def blocking_post(_body, _timeout=None):
        release.wait(timeout=30.0)
        return {"message": {"content": '{"q":false,"query":""}'}}

    monkeypatch.setattr(gate, "_post", blocking_post)
    started = time.perf_counter()
    try:
        assert asyncio.run(gate.classify("What about this?", [])) == (False, "")
    finally:
        release.set()
    assert time.perf_counter() - started < 1.0


def test_ollama_request_bypasses_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_bodies: list[bytes] = []
    proxy_bodies: list[bytes] = []

    def handler_for(destination: list[bytes]):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                destination.append(self.rfile.read(length))
                payload = json.dumps(
                    {
                        "message": {
                            "content": json.dumps({"q": False, "query": ""})
                        }
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return None

        return Handler

    direct = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(direct_bodies))
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(proxy_bodies))
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (direct, proxy)
    ]
    for thread in threads:
        thread.start()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    gate = OllamaGate(
        GateConfig(ollama_url=f"http://127.0.0.1:{direct.server_port}/api/chat")
    )
    try:
        assert asyncio.run(
            gate.classify("PRIVATE CURRENT UTTERANCE", ["PRIVATE CONTEXT"])
        ) == (False, "")
    finally:
        for server in (direct, proxy):
            server.shutdown()
            server.server_close()

    assert len(direct_bodies) == 1
    assert b"PRIVATE CURRENT UTTERANCE" in direct_bodies[0]
    assert b"PRIVATE CONTEXT" in direct_bodies[0]
    assert proxy_bodies == []


def test_ollama_response_is_size_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 1024 * 1024 + 1
            return b"x" * limit

    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: Opener())
    with pytest.raises(ValueError, match="exceeded 1 MiB"):
        OllamaGate(GateConfig())._post({"private": "text"})


def test_managed_macos_listener_must_be_owned_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AMBIENTQA_REQUIRE_MANAGED_OLLAMA", "1")
    monkeypatch.setenv("AMBIENTQA_OLLAMA_PID", "4242")
    monkeypatch.setattr(gate_module.sys, "platform", "darwin")
    seen: list[list[str]] = []

    def wrong_owner(command, **_kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(gate_module.subprocess, "run", wrong_owner)
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_args: pytest.fail("HTTP opened before listener ownership passed"),
    )

    with pytest.raises(OSError, match="no longer owns"):
        OllamaGate(GateConfig())._post({"private": "SECRET TRANSCRIPT"})
    assert seen and "4242" in seen[0]
    assert "-iTCP:11434" in seen[0]


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


def test_gate_prompt_rejects_word_salad_and_prefers_nearest_antecedent() -> None:
    """Two live-session failure modes, pinned as prompt invariants.

    A garbled rehearsal ("who are the details, IAM and identity, I am the
    limitation...") was rewritten into a question nobody asked, and a dangling
    'it' was resolved to an older exchange instead of the setup statement
    spoken one line earlier.
    """
    prompt = OllamaGate(GateConfig()).system_prompt
    assert "NEVER assemble a question out of garble fragments" in prompt
    assert "word-salad" in prompt
    assert "NEAREST plausible antecedent" in prompt
    assert "usually" in prompt and "setup" in prompt
    # The salvage rule for lightly-garbled real questions must survive.
    assert "garble alone is never a reason to reject an otherwise clear ask" in prompt


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


@pytest.mark.parametrize(
    "text",
    [
        "Okay, let's talk about that, yeah.",
        "Okay, let’s talk about that, yeah.",
        "Okay, let us talk about that, yeah.",
    ],
)
def test_reported_lets_talk_narration_does_not_fast_accept(text: str) -> None:
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == ("llm", "needs_semantic_gate")


def test_reported_word_salad_cannot_reach_an_imperative_fast_path() -> None:
    text = (
        "…. …. List or should games … algún … … And direct information "
        "questionnaire … is result of a word … you can submit … GOD … "
        "Agent …!!!! … aldль … don't mess… … …"
    )
    decision = heuristic_decision(text)
    assert (decision.outcome, decision.reason) == (
        "reject",
        "garbled_transcript",
    )


@pytest.mark.parametrize(
    "text",
    [
        "List or should games and direct information questionnaire result word submit GOD Agent mess",
        "Describe potato security wonder station violet architecture window system",
        "Tell me airplane IAM orange limitation professor questionnaire.",
    ],
)
def test_full_policy_semantically_judges_imperative_shaped_word_salad(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = QuestionGate(GateConfig())
    calls: list[str] = []

    async def reject_word_salad(value: str, _context: list[str]) -> tuple[bool, str]:
        calls.append(value)
        return False, ""

    monkeypatch.setattr(gate.ollama, "classify", reject_word_salad)
    result = asyncio.run(
        gate.evaluate(Transcript("sys", text, 100.0, "salad"), [], policy="full")
    )

    assert calls == [text]
    assert result.accepted is False


def test_explicit_policy_keeps_clear_imperative_zero_call_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("explicit command reached semantic gate")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript("mic", "Explain the CAP theorem.", 100.0, "command"),
            [],
            policy="explicit",
        )
    )

    assert (result.accepted, result.reason) == (True, "imperative_request")


def test_full_policy_imperative_fails_closed_when_semantic_gate_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())

    async def unavailable(_text: str, _context: list[str]) -> tuple[bool, str]:
        gate.ollama.available = False
        return False, ""

    monkeypatch.setattr(gate.ollama, "classify", unavailable)
    result = asyncio.run(
        gate.evaluate(
            Transcript("sys", "Tell me about yourself.", 100.0, "outage"),
            [],
            policy="full",
        )
    )

    assert (result.accepted, result.reason) == (False, "ollama_unavailable")


# --- question shape: an interrogative START counts, not just the '?' ---
# Whisper drops the question mark when the tail garbles ("Why does it always
# take a little time... Ferry 2. Ferry 2."), and the blatant interrogative
# died unheard while its terse re-ask sailed through. Shape only earns the
# right to be JUDGED by the semantic gate -- never an answer by itself.


def test_interrogative_start_without_mark_reaches_the_semantic_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())
    calls: list[str] = []

    async def fake_classify(text: str, _context: list[str]) -> tuple[bool, str]:
        calls.append(text)
        return True, "Why does it take time after the first words are spoken?"

    monkeypatch.setattr(gate.ollama, "classify", fake_classify)
    result = asyncio.run(
        gate.evaluate(
            Transcript(
                "mic",
                "Why does it always take A little bit of time After the first words are spoken Ferry 2. Ferry 2.",
                100.0,
                "u1",
            ),
            [],
            policy="explicit",
        )
    )
    assert calls, "interrogative-start utterance never reached the gate"
    assert result.accepted is True


def test_plain_statements_still_never_reach_the_gate_on_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = QuestionGate(GateConfig())

    async def must_not_run(_text: str, _context: list[str]) -> tuple[bool, str]:
        raise AssertionError("statement reached the semantic gate on explicit")

    monkeypatch.setattr(gate.ollama, "classify", must_not_run)
    result = asyncio.run(
        gate.evaluate(
            Transcript("mic", "I built the retriever with hybrid search last week.", 100.0, "u1"),
            [],
            policy="explicit",
        )
    )
    assert result.accepted is False
    assert result.reason == "not_a_direct_question"
