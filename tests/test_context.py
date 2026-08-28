from __future__ import annotations

from ambientqa.bus import Transcript
import pytest

from ambientqa.context import (
    TranscriptContext,
    token_set_ratio,
    transcript_quality_reason,
)


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


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "…. …. List or should games … algún … questionnaire … GOD … "
            "Agent …!!!! … aldль … don't mess … … … …",
            "punctuation_noise",
        ),
        ("IAM IAM IAM IAM IAM", "repetition_loop"),
        ("client assurance \ufffd PMIP", "invalid_unicode"),
    ],
)
def test_high_confidence_recognition_garble_is_identified(
    text: str, reason: str
) -> None:
    assert transcript_quality_reason(text) == reason


@pytest.mark.parametrize(
    "text",
    [
        "How should we evaluate the retrieval pipeline?",
        "¿Cómo funciona la configuración de audio?",
        "Compare IAM, KMS, STS, and S3 controls.",
        "Wait... should we retry now?",
        "No no no no, do not delete that.",
        "Stop stop stop stop the deployment.",
        "Very very very very important.",
        "Please translate this 日本語の質問 for the customer.",
        "Help help help help!",
        "Stop! Stop! Stop! Stop!",
        "No no no no!",
        "Fire fire fire fire!",
        "Wait wait wait wait!",
        "Help help help help help help help help help help help help!",
        "Wait... Wait... Wait... Wait... Wait... Wait...",
        "Help... Help... Help... Help... Help... Help...",
        "Stop!!! Stop!!! Stop!!! Stop!!!",
        "Fire!!! Fire!!! Fire!!! Fire!!!",
        "Help me! Help me! Help me! Help me! Help me! Help me!",
        "Please help me!!! Please help me!!! Please help me!!! Please help me!!! Please help me!!!",
    ],
)
def test_quality_filter_preserves_plausible_speech(text: str) -> None:
    assert transcript_quality_reason(text) is None
