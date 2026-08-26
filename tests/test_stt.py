from __future__ import annotations

import asyncio

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


# --- regression: the hallucination blocklist is exact-match only ---
# Whisper's silence hallucinations ("Thank you.", "Thanks for watching!") are
# whole-utterance artifacts. A prefix match ate real speech: an interviewer's
# courteous opener made the whole question vanish before gating, with no log
# record at all.


def test_exact_hallucination_phrase_is_dropped() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper(" Thank you. ")
    assert transcriber._transcribe_sync(utterance()) is None


@pytest.mark.parametrize(
    "courtesy",
    ["Thank you.", "Thank you very much.", "Thank you so much!"],
)
def test_agent_runtime_preserves_real_courtesy_with_cyber_profile(
    courtesy: str,
) -> None:
    profile = Profile(
        "Cybersecurity analytics",
        "Defensive cybersecurity analytics",
        "",
        [],
        "",
    )
    transcriber = WhisperTranscriber(STTConfig(), profile=profile)
    transcriber.set_agent_mode(True)
    transcriber.model = FakeWhisper(courtesy)

    result = transcriber._transcribe_sync(utterance())

    assert result is not None
    assert result.text == courtesy


@pytest.mark.parametrize(
    "artifact",
    ["Thank you for watching.", "Thanks for watching!", "Please subscribe."],
)
def test_agent_runtime_still_drops_non_conversational_artifacts(
    artifact: str,
) -> None:
    profile = Profile(
        "Customer service",
        "Customer support",
        "",
        [],
        "",
        interaction="agent",
    )
    transcriber = WhisperTranscriber(STTConfig(), profile=profile)
    transcriber.set_agent_mode(True)
    transcriber.model = FakeWhisper(artifact)

    assert transcriber._transcribe_sync(utterance()) is None


def test_customer_service_profile_in_assist_still_drops_thank_you() -> None:
    profile = Profile(
        "Customer service",
        "Customer support",
        "",
        [],
        "",
        interaction="agent",  # legacy metadata must not control runtime STT
    )
    transcriber = WhisperTranscriber(STTConfig(), profile=profile)
    transcriber.model = FakeWhisper("Thank you.")

    assert transcriber._transcribe_sync(utterance()) is None


def test_courteous_opener_before_real_question_survives() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper(
        "Thank you. So, tell me about your experience with Kubernetes."
    )
    result = transcriber._transcribe_sync(utterance())
    assert result is not None
    assert "Kubernetes" in result.text


@pytest.mark.parametrize(
    "hallucination",
    [
        # The canonical whole-utterance silence hallucinations. Exact matching
        # made the old prefix stems ("subtitles by") dead entries, so the
        # defaults must carry the full forms Whisper actually emits.
        "Subtitles by the Amara.org community.",
        "Thank you very much.",
        "Thank you for watching.",
        "Thanks for watching!",
    ],
)
def test_default_blocklist_covers_canonical_whole_utterances(
    hallucination: str,
) -> None:
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper(hallucination)
    assert transcriber._transcribe_sync(utterance()) is None


def test_blocked_phrase_matches_whole_utterance_not_prefix() -> None:
    # An unlisted credit line passes through untouched -- matching is exact,
    # never by prefix -- and blocking it takes its own whole-utterance entry.
    transcriber = WhisperTranscriber(STTConfig())
    transcriber.model = FakeWhisper("Subtitles by Example Studio")
    assert transcriber._transcribe_sync(utterance()) is not None

    exact = WhisperTranscriber(
        STTConfig(hallucination_blocklist=["Subtitles by Example Studio"])
    )
    exact.model = FakeWhisper("Subtitles by Example Studio")
    assert exact._transcribe_sync(utterance()) is None


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


def test_cuda_oom_warning_explains_what_to_close() -> None:
    config = STTConfig(cpu_compute_type="int8")
    transcriber = WhisperTranscriber(config)

    warning = transcriber._cuda_fallback_warning(
        "CUDA Whisper initialization failed", "CUDA failed with error out of memory"
    )

    assert "GPU memory is exhausted" in warning
    assert "games" in warning
    assert "Whisper/dictation" in warning
    assert "Ollama" in warning
    assert "CPU int8" in warning


def test_non_oom_cuda_warning_does_not_guess_at_gpu_pressure() -> None:
    transcriber = WhisperTranscriber(STTConfig())
    warning = transcriber._cuda_fallback_warning(
        "CUDA Whisper unavailable", "cublas is missing"
    )
    assert "GPU memory is exhausted" not in warning


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


def test_macos_translates_shared_cuda_config_to_cpu(monkeypatch) -> None:
    import faster_whisper

    monkeypatch.setattr("ambientqa.stt.sys.platform", "darwin")

    def cuda_must_not_run() -> None:
        raise AssertionError("CUDA setup ran on macOS")

    monkeypatch.setattr("ambientqa.stt.register_cuda_dll_dirs", cuda_must_not_run)
    created: list[tuple[str, dict[str, str]]] = []

    def model(name: str, **kwargs):
        created.append((name, kwargs))
        return object()

    monkeypatch.setattr(faster_whisper, "WhisperModel", model)
    transcriber = WhisperTranscriber(
        STTConfig(device="cuda", compute_type="float16", cpu_compute_type="int8")
    )
    assert transcriber.device == "cpu"
    transcriber._load_model()
    assert created == [("large-v3-turbo", {"device": "cpu", "compute_type": "int8"})]


def test_cuda_dll_dirs_are_registered_and_on_path() -> None:
    import os
    from ambientqa.stt import register_cuda_dll_dirs

    added = register_cuda_dll_dirs()
    if os.name != "nt" or not added:
        pytest.skip("no pip-installed CUDA libraries present")
    # add_dll_directory alone does not help CTranslate2's dynamic cuBLAS load.
    for directory in added:
        assert directory.lower() in os.environ["PATH"].lower()


def test_warmup_loads_the_model_once_and_swallows_failure() -> None:
    # The first utterance otherwise pays the ~10s model load at the moment the
    # user is testing whether the app hears them; a failed warmup must fall
    # back to the lazy path rather than kill the worker.
    transcriber = WhisperTranscriber(STTConfig())
    calls = []

    def fake_load() -> None:
        calls.append(1)
        transcriber.model = FakeWhisper("warm")

    transcriber._load_model = fake_load  # type: ignore[method-assign]
    asyncio.run(transcriber.warmup())
    assert transcriber.model is not None and calls == [1]
    asyncio.run(transcriber.warmup())
    assert calls == [1]  # already loaded: no second load

    failing = WhisperTranscriber(STTConfig())

    def broken_load() -> None:
        raise RuntimeError("no CUDA")

    failing._load_model = broken_load  # type: ignore[method-assign]
    asyncio.run(failing.warmup())  # must not raise
    assert failing.model is None
