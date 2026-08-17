from __future__ import annotations

import numpy as np
import pytest

from ambientqa.bus import Utterance
from ambientqa.config import STTConfig
from ambientqa.profile import Profile
from ambientqa.stt import WhisperTranscriber


class Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeWhisper:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs = {}

    def transcribe(self, _audio, **kwargs):
        self.kwargs = kwargs
        return [Segment(self.text)], object()


def utterance() -> Utterance:
    return Utterance("mic", np.zeros(16000, dtype=np.float32), 0.0, 1.0, "u1")


def test_keeps_question_punctuation_and_disables_previous_text() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper(" How does this work? ")
    result = transcriber._transcribe_sync(utterance())
    assert result is not None
    assert result.text == "How does this work?"
    assert result.started_at == 0.0
    assert transcriber.model.kwargs["condition_on_previous_text"] is False


def test_blocks_variable_subtitle_credit_suffix() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper("Subtitles by Example Studio")
    assert transcriber._transcribe_sync(utterance()) is None


def test_drops_pure_punctuation() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper("...?!")
    assert transcriber._transcribe_sync(utterance()) is None


def test_active_profile_passes_hotwords_and_short_topic_prompt() -> None:
    profile = Profile(
        "AWS",
        "AWS cloud architecture, focused on Amazon Bedrock and GenAI services.",
        "",
        ["Bedrock", "PrivateLink", "FastAPI"],
        "",
    )
    transcriber = WhisperTranscriber(STTConfig(), profile=profile)
    transcriber.model = FakeWhisper("How does Bedrock security work?")
    result = transcriber._transcribe_sync(utterance())
    assert result is not None
    assert transcriber.model.kwargs["hotwords"] == "Bedrock, PrivateLink, FastAPI"
    assert "Amazon Bedrock" in transcriber.model.kwargs["initial_prompt"]
    assert len(transcriber.model.kwargs["initial_prompt"]) <= 120
    assert transcriber.model.kwargs["condition_on_previous_text"] is False


def test_no_profile_omits_hotwords_and_initial_prompt() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper("How does this work?")
    transcriber._transcribe_sync(utterance())
    assert "hotwords" not in transcriber.model.kwargs
    assert "initial_prompt" not in transcriber.model.kwargs


def test_silent_transcription_stays_empty_with_profile_active() -> None:
    profile = Profile("AWS", "Amazon Bedrock", "", ["Bedrock"], "")
    transcriber = WhisperTranscriber(STTConfig(), profile=profile)
    transcriber.model = FakeWhisper("")
    assert transcriber._transcribe_sync(utterance()) is None
    assert transcriber.model.kwargs["hotwords"] == "Bedrock"



# --- regression: CUDA DLLs are resolved, and inference-time CUDA failure recovers ---
# faster-whisper constructs a CUDA model successfully even when cuBLAS is missing;
# the failure only appears at the FIRST INFERENCE. A load-time-only fallback
# therefore reports "Whisper ready on cuda" and then throws on every utterance.


class FlakyWhisper:
    """Fails once the way a missing cublas64_12.dll does, then succeeds."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, _audio, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return [Segment(self.text)], object()


def test_cuda_inference_failure_falls_back_to_cpu(monkeypatch) -> None:
    import faster_whisper

    config = STTConfig()
    config.device = "cuda"
    transcriber = WhisperTranscriber(config)
    flaky = FlakyWhisper(" How does this work? ")
    transcriber.model = flaky
    transcriber.device = "cuda"
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *a, **k: flaky)

    result = transcriber._transcribe_sync(utterance())

    assert result is not None and result.text == "How does this work?"
    assert transcriber.device == "cpu", "must degrade instead of dying"
    assert flaky.calls == 2, "must retry the utterance on the CPU model"


def test_cpu_runtime_errors_are_not_swallowed() -> None:
    config = STTConfig()
    transcriber = WhisperTranscriber(config)
    transcriber.device = "cpu"

    class Broken:
        def transcribe(self, _audio, **kwargs):
            raise RuntimeError("genuinely broken")

    transcriber.model = Broken()
    with pytest.raises(RuntimeError, match="genuinely broken"):
        transcriber._transcribe_sync(utterance())


def test_cuda_dll_dirs_are_registered_and_on_path() -> None:
    import os
    from ambientqa.stt import register_cuda_dll_dirs

    added = register_cuda_dll_dirs()
    if os.name != "nt" or not added:
        pytest.skip("no pip-installed CUDA libraries present")
    # add_dll_directory alone does not help CTranslate2's dynamic cuBLAS load.
    for directory in added:
        assert directory.lower() in os.environ["PATH"].lower()
