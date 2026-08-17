from __future__ import annotations

from ambientqa.bus import Transcript
from ambientqa.context import TranscriptContext, token_set_ratio


def transcript(channel: str, text: str, timestamp: float) -> Transcript:
    return Transcript(channel, text, timestamp)


def test_token_set_similarity_is_order_independent() -> None:
    assert token_set_ratio("blue ocean tides", "tides ocean blue") == 1.0
    assert token_set_ratio("one two", "three four") == 0.0


def test_system_copy_after_mic_is_suppressed() -> None:
    context = TranscriptContext()
    assert context.add(transcript("mic", "What causes ocean tides?", 10.0))
    assert not context.add(transcript("sys", "What causes the ocean tides?", 10.5))
    assert len(context.recent()) == 1
    assert context.recent()[0].channel == "mic"


def test_mic_replaces_earlier_system_copy() -> None:
    context = TranscriptContext()
    assert context.add(transcript("sys", "How does this option work?", 10.0))
    assert context.add(transcript("mic", "How does this option work?", 10.5))
    assert len(context.recent()) == 1
    assert context.recent()[0].channel == "mic"


def test_echo_window_expires() -> None:
    context = TranscriptContext(echo_window_s=2.0)
    assert context.add(transcript("mic", "What causes ocean tides?", 10.0))
    assert context.add(transcript("sys", "What causes ocean tides?", 12.1))
    assert len(context.recent()) == 2


def test_rendered_context_limits_and_excludes_latest() -> None:
    context = TranscriptContext()
    for index in range(8):
        context.add(transcript("mic", f"line number {index}", float(index)))
    rendered = context.rendered(3, exclude_latest=True)
    assert rendered == ["[mic] line number 4", "[mic] line number 5", "[mic] line number 6"]

