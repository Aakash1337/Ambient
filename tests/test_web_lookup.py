"""Some answers must be looked up, because memory is confidently wrong.

Measured: asked "What is Vertex AI called now again?" the model answered "it
hasn't been renamed" -- by then it had become the Gemini Enterprise Agent
Platform. That is worse than a hedge, because it reads as a real answer.

The counter-pressure is latency. A lookup measured 17.1s against 3.5s from
memory, which is ruinous mid-conversation, so the trigger has to stay rare:
across 569 recorded questions it fires on 3.
"""

from __future__ import annotations

import pytest

from ambientqa.answer import ClaudeAnswerer, needs_current_facts
from ambientqa.config import AnswerConfig


@pytest.mark.parametrize(
    "query",
    [
        "What is Vertex AI called now again?",
        "Has Vertex AI been renamed?",
        "What is Bedrock called these days?",
        "Is it still called Vertex AI?",
        "What's the new name for that service?",
        "What is the latest Claude model?",
        "What version of Python is newest?",
        "What is the current pricing for Bedrock?",
        "How much does Opus cost now?",
        "What is the most recent LangGraph release?",
        "Is that library still up to date?",
        "What models does Bedrock support nowadays?",
    ],
)
def test_currency_questions_trigger_a_lookup(query: str) -> None:
    assert needs_current_facts(query)


@pytest.mark.parametrize(
    "query",
    [
        # A bare "now" is discourse filler, not a request for current facts.
        # Treating it as one would put 13s of latency on ordinary questions.
        "Okay, what do you mean, how, how do I truncate it?",
        "So how do we handle that now?",
        "What is retrieval augmented generation?",
        "How do you evaluate a RAG pipeline?",
        "Can I fine-tune whatever model I want on AgentCore?",
        "Is there an AWS alternative to this?",
        "What is the relationship between CI/CD and MCP servers?",
        "How do you prevent duplicate orders during retries?",
        "Tell me about your current project.",
        "What's the reason?",
    ],
)
def test_ordinary_questions_answer_from_memory(query: str) -> None:
    assert not needs_current_facts(query)


def _answerer(mode: str) -> ClaudeAnswerer:
    config = AnswerConfig()
    config.web_lookup = mode
    return ClaudeAnswerer(config)


def test_auto_mode_only_looks_up_currency_questions() -> None:
    answerer = _answerer("auto")
    assert answerer._wants_lookup("What is Vertex AI called now again?")
    assert not answerer._wants_lookup("What is a vector database?")


def test_off_mode_never_looks_up() -> None:
    answerer = _answerer("off")
    assert not answerer._wants_lookup("What is Vertex AI called now again?")


def test_always_mode_always_looks_up() -> None:
    answerer = _answerer("always")
    assert answerer._wants_lookup("What is a vector database?")


def test_slow_deltas_never_build_a_zero_interval_timer() -> None:
    """A zero-delay Textual timer divides by its own interval and raises.

    `set_timer(max(0.0, 0.1 - elapsed))` hit exactly 0.0 whenever two deltas
    arrived more than 100ms apart -- the normal case on a slow stream, not an
    edge -- and then blew up with ZeroDivisionError when the card was torn down.
    """
    import asyncio

    from ambientqa.ui import AmbientQAApp, QACard

    class Stub:
        paused = False

        def status_text(self) -> str:
            return "listening"

    app = AmbientQAApp(Stub(), status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_question("q1", "First?")
            card = app.query_one("#qa-q1", QACard)
            card.append_answer("alpha ")
            # Backdate the last render so the coalescing interval has elapsed.
            card._last_stream_render -= 5.0
            card.append_answer("beta")
            assert card._flush_timer is None, "scheduled a degenerate timer"
            assert "beta" in card._raw_answer
            await pilot.pause()

    asyncio.run(drive())


def test_lookup_directive_demands_a_search_and_suppresses_sources() -> None:
    """Permitting WebSearch was measured as insufficient on its own.

    With the tool allowed but not demanded the model answered from memory in
    3.6s and was still wrong; it has to be told the memory is the problem. The
    sources block is suppressed because a cue card is read at a glance.
    """
    directive = ClaudeAnswerer.LOOKUP
    assert "WebSearch FIRST" in directive
    assert "do not answer from memory" in directive.lower()
    assert "sources" in directive.lower()
