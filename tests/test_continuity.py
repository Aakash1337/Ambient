from __future__ import annotations

import asyncio

from ambientqa.__main__ import AmbientController
from ambientqa.bus import Transcript
from ambientqa.config import MergeConfig
from ambientqa.continuity import (
    ContinuityMerger,
    is_open_utterance,
    join_fragments,
)


def transcript(
    text: str,
    *,
    start: float,
    end: float,
    channel: str = "mic",
    utterance_id: str | None = None,
) -> Transcript:
    return Transcript(
        channel=channel,
        text=text,
        timestamp=end,
        utterance_id=utterance_id or f"{channel}-{start}",
        latency_ms=10.0,
        started_at=start,
    )


def test_bedrock_fragments_are_gated_only_as_one_merged_question() -> None:
    merger = ContinuityMerger(MergeConfig(max_merge_parts=6))
    parts = [
        transcript("So, tell me about", start=0.0, end=1.0, utterance_id="first"),
        transcript("talk to me briefly about", start=1.3, end=2.0),
        transcript("Amazon Bedrock", start=2.2, end=3.0),
        transcript("how you manage context in", start=3.2, end=4.0),
        transcript(
            "How do you manage context in Amazon Bedrock?",
            start=4.2,
            end=5.0,
        ),
    ]

    gated: list[Transcript] = []
    for index, part in enumerate(parts):
        emitted = merger.push(part, now=float(index))
        if index < len(parts) - 1:
            assert emitted == []
        gated.extend(emitted)

    assert len(gated) == 1
    assert gated[0].utterance_id == "first"
    assert gated[0].text == (
        "So, tell me about talk to me briefly about Amazon Bedrock "
        "how you manage context in How do you manage context in Amazon Bedrock?"
    )
    assert all(part.text != gated[0].text for part in parts)


def test_connect_knowledge_base_continuation_merges() -> None:
    merger = ContinuityMerger(MergeConfig())
    first = transcript("connect knowledge base", start=0.0, end=1.0)
    second = transcript("and add to it.", start=1.5, end=2.0)
    assert merger.push(first, now=0.0) == []
    emitted = merger.push(second, now=0.5)
    assert [item.text for item in emitted] == ["connect knowledge base and add to it."]


def test_complete_question_gates_immediately_without_hold() -> None:
    merger = ContinuityMerger(MergeConfig())
    question = transcript(
        "How do you manage context in Amazon Bedrock?",
        start=0.0,
        end=1.0,
    )
    assert merger.push(question, now=10.0) == [question]
    assert merger.flush_expired(now=10.0) == []


def test_continuation_after_merge_gap_does_not_merge() -> None:
    merger = ContinuityMerger(MergeConfig(merge_window_s=10.0))
    first = transcript("connect knowledge base", start=0.0, end=1.0)
    second = transcript("and add to it.", start=6.0, end=7.0)
    assert merger.push(first, now=0.0) == []
    emitted = merger.push(second, now=1.0)
    assert [item.text for item in emitted] == [
        "connect knowledge base",
        "and add to it.",
    ]


def test_max_merge_parts_cap_emits_accumulated_text() -> None:
    merger = ContinuityMerger(MergeConfig(max_merge_parts=2))
    assert merger.push(
        transcript("tell me about", start=0.0, end=1.0),
        now=0.0,
    ) == []
    emitted = merger.push(
        transcript("context in", start=1.1, end=2.0),
        now=0.1,
    )
    assert [item.text for item in emitted] == ["tell me about context in"]
    assert merger.flush_all() == []


def test_max_merge_duration_cap_emits_accumulated_text() -> None:
    merger = ContinuityMerger(
        MergeConfig(max_merge_parts=10, max_merge_s=3.0)
    )
    assert merger.push(
        transcript("tell me about", start=0.0, end=1.0),
        now=0.0,
    ) == []
    emitted = merger.push(
        transcript("context in", start=1.1, end=3.0),
        now=0.1,
    )
    assert [item.text for item in emitted] == ["tell me about context in"]


def test_different_channels_never_merge() -> None:
    merger = ContinuityMerger(MergeConfig())
    mic = transcript("connect knowledge base", start=0.0, end=1.0)
    system = transcript(
        "and add to it.",
        start=1.1,
        end=2.0,
        channel="sys",
    )
    assert merger.push(mic, now=0.0) == []
    assert merger.push(system, now=0.1) == [system]
    assert merger.flush_all() == [mic]


def test_joining_collapses_boundary_periods_and_spaces() -> None:
    assert join_fragments(
        "  how   you manage context in.  ",
        "  Amazon   Bedrock. ",
    ) == "how you manage context in Amazon Bedrock."
    assert ".." not in join_fragments("about.", ".Amazon Bedrock.")
    assert "  " not in join_fragments("about.", "Amazon   Bedrock.")


def test_hold_expiry_emits_accumulated_text_once() -> None:
    merger = ContinuityMerger(MergeConfig(merge_window_s=2.5))
    first = transcript("tell me about", start=0.0, end=1.0)
    assert merger.push(first, now=10.0) == []
    assert merger.flush_expired(now=12.49) == []
    assert merger.flush_expired(now=12.5) == [first]
    assert merger.flush_expired(now=20.0) == []


def test_merging_disabled_restores_per_utterance_gating() -> None:
    merger = ContinuityMerger(MergeConfig(enabled=False))
    first = transcript("connect knowledge base", start=0.0, end=1.0)
    second = transcript("and add to it.", start=1.1, end=2.0)
    assert merger.push(first, now=0.0) == [first]
    assert merger.push(second, now=0.1) == [second]


def test_open_detection_ignores_whisper_period_after_trailing_word() -> None:
    assert is_open_utterance("how you manage context in.")
    assert is_open_utterance("connect knowledge base")
    assert is_open_utterance("tell me more,")
    assert is_open_utterance("tell me more—")
    assert not is_open_utterance("How does this work?")


def test_controller_processes_only_the_merged_transcript() -> None:
    controller = AmbientController.__new__(AmbientController)
    controller.continuity = ContinuityMerger(MergeConfig())
    processed: list[Transcript] = []

    async def record(item: Transcript) -> None:
        processed.append(item)

    controller._process_transcript = record  # type: ignore[method-assign]
    first = transcript("connect knowledge base", start=0.0, end=1.0)
    second = transcript("and add to it.", start=1.1, end=2.0)

    async def drive() -> None:
        await controller._ingest_transcript(first)
        assert processed == []
        await controller._ingest_transcript(second)

    asyncio.run(drive())
    assert [item.text for item in processed] == [
        "connect knowledge base and add to it."
    ]
