from __future__ import annotations

from pathlib import Path

import pytest

from ambientqa.config import default_config, load_config, validate_config


def test_defaults_match_spec() -> None:
    config = default_config()
    assert config.audio.sample_rate == 16000
    assert config.audio.frame_ms == 25
    assert config.audio.silence_ms == 900
    assert config.audio.max_utterance_s == 20.0
    assert config.stt.model == "large-v3-turbo"
    assert config.stt.device == "cuda"
    assert config.stt.vad_filter is True
    assert config.stt.profile_hints is False
    assert config.context.profile == ""
    assert config.context.enabled is True
    assert config.gate.model == "gemma4:e2b"
    assert config.gate.mode == "balanced"
    assert config.gate.context_turns == 6
    assert config.merge.enabled is True
    assert config.merge.merge_gap_s == 6.5
    assert config.merge.merge_window_s == 13.0
    assert config.merge.max_merge_parts == 5
    assert config.merge.max_merge_s == 25.0
    assert config.answer.answer_model == "claude-sonnet-5"
    assert config.answer.stream is True
    assert config.answer.max_concurrent == 4
    assert config.answer.verify == "off"
    assert config.answer.sweep == "always"
    assert config.answer.sweep_max_age_s == 60.0
    assert config.gate.max_concurrent == 3
    assert config.answer.answer_timeout_s == 45.0
    # Your own channel answers direct questions but never has its narration
    # mined for them; the other speaker's channel is judged freely.
    assert config.gate.channel_policy == {"mic": "explicit", "sys": "full"}
    assert config.answer.style == "cue"
    assert config.audio.silent_source_warn_s == 45.0


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/api/chat",
        "http://192.168.1.20:11434/api/chat",
        "https://127.0.0.1:11434/api/chat",
        "http://127.0.0.1:11434/other",
        "http://user:pass@127.0.0.1:11434/api/chat",
    ],
)
def test_ollama_url_must_be_literal_loopback_http(url: str) -> None:
    config = default_config()
    config.gate.ollama_url = url
    with pytest.raises(ValueError, match="gate.ollama_url"):
        validate_config(config)


def test_managed_ollama_environment_overrides_file_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "ambient.toml"
    config_path.write_text(
        '[gate]\nollama_url = "http://127.0.0.1:11434/api/chat"\n'
    )
    monkeypatch.setenv(
        "AMBIENTQA_OLLAMA_URL", "http://127.0.0.1:49199/api/chat"
    )

    assert load_config(config_path).gate.ollama_url == (
        "http://127.0.0.1:49199/api/chat"
    )


def test_knowledge_defaults_are_opt_in_and_safe() -> None:
    knowledge = default_config().knowledge
    assert knowledge.enabled is False
    assert knowledge.path == ""
    assert knowledge.hit_threshold == 0.66
    assert knowledge.min_query_words == 3
    assert knowledge.ground_on_miss is True
    assert knowledge.retrieve_k == 3
    assert knowledge.grounding_threshold == 0.30


def test_knowledge_section_loads_over_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[knowledge]\n"
        "enabled = true\n"
        'path = "knowledge/aws-security-architect"\n'
        "hit_threshold = 0.7\n"
        "min_query_words = 5\n"
        "ground_on_miss = false\n"
        "retrieve_k = 2\n"
        "grounding_threshold = 0.3\n",
        encoding="utf-8",
    )
    knowledge = load_config(path).knowledge
    assert knowledge.enabled is True
    assert knowledge.path == "knowledge/aws-security-architect"
    assert knowledge.hit_threshold == 0.7
    assert knowledge.min_query_words == 5
    assert knowledge.ground_on_miss is False
    assert knowledge.retrieve_k == 2
    assert knowledge.grounding_threshold == 0.3


@pytest.mark.parametrize("threshold", [-0.1, 1.5])
def test_knowledge_rejects_out_of_range_threshold(
    tmp_path: Path, threshold: float
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[knowledge]\nhit_threshold = {threshold}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hit_threshold"):
        load_config(path)


def test_knowledge_rejects_zero_min_query_words(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[knowledge]\nmin_query_words = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="min_query_words"):
        load_config(path)


def test_knowledge_rejects_out_of_range_grounding_threshold(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[knowledge]\ngrounding_threshold = 1.1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="grounding_threshold"):
        load_config(path)


def test_answer_rejects_nonpositive_sweep_age(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[answer]\nsweep_max_age_s = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sweep_max_age_s"):
        load_config(path)


def test_knowledge_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[knowledge]\nthreshold = 0.9\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown key"):
        load_config(path)


def test_channel_policy_rejects_unknown_channel(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[gate]\nchannel_policy = { sys = "full", aux = "full" }\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="channel_policy"):
        load_config(path)


def test_channel_policy_rejects_unknown_policy(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[gate]\nchannel_policy = { mic = "sometimes", sys = "full" }\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sometimes"):
        load_config(path)


def test_channel_policy_rejects_silencing_everything(tmp_path: Path) -> None:
    """An all-off config answers nothing at all; that is never intended."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[gate]\nchannel_policy = { mic = "off", sys = "off" }\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="every channel off"):
        load_config(path)


def test_channel_policy_accepts_full_on_both(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[gate]\nchannel_policy = { mic = "full", sys = "full" }\n', encoding="utf-8"
    )
    assert load_config(path).gate.channel_policy == {"mic": "full", "sys": "full"}


def test_rejects_invalid_answer_style(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[answer]\nstyle = "essay"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="answer.style"):
        load_config(path)


def test_loads_partial_config_over_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[audio]
mic_device = "Broadcast"
[gate]
mode = "strict"
min_words = 4
[context]
profile = "profiles/example.md"
enabled = false
[answer]
max_words = 42
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.audio.mic_device == "Broadcast"
    assert config.audio.sample_rate == 16000
    assert config.audio.silence_ms == 900
    assert config.gate.mode == "strict"
    assert config.gate.min_words == 4
    assert config.context.profile == "profiles/example.md"
    assert config.context.enabled is False
    assert config.answer.max_words == 42
    assert config.answer.stream is True
    assert config.answer.verify == "off"
    assert config.answer.sweep == "always"


def test_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[gate]\nconfidence_threshold = 0.95\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown key"):
        load_config(path)


@pytest.mark.parametrize("mode", ["loose", "", "STRICT"])
def test_rejects_invalid_gate_mode(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f'[gate]\nmode = "{mode}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="gate.mode"):
        load_config(path)


def test_project_config_loads() -> None:
    config = load_config(Path(__file__).parents[1] / "config.toml")
    # The gate model is machine-local: config.toml pins whichever Ollama tag is
    # actually pulled on this box, so only its presence is invariant here.
    assert config.gate.model
    assert config.audio.silence_ms == 900
    assert config.merge.enabled is True
    assert config.answer.stream is True
    assert config.answer.verify == "off"
    assert config.answer.sweep == "always"
    assert config.stt.hallucination_blocklist
    assert config.gate.channel_policy == {"mic": "explicit", "sys": "full"}
    # Device fields are machine-local state the in-app device picker rewrites
    # at will; asserting a specific value (or blankness) makes the suite fail
    # whenever the user picks a device. Loading is the contract worth pinning.
    assert isinstance(config.audio.output_device, str)


def test_feed_direction_loads_and_defaults_to_top(tmp_path: Path) -> None:
    assert default_config().ui.feed_direction == "top"
    path = tmp_path / "config.toml"
    path.write_text('[ui]\nfeed_direction = "bottom"\n', encoding="utf-8")
    assert load_config(path).ui.feed_direction == "bottom"


def test_rejects_invalid_feed_direction(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[ui]\nfeed_direction = "sideways"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="feed_direction"):
        load_config(path)


def test_rejects_nonpositive_status_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[ui]\nstatus_interval_s = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="status_interval_s"):
        load_config(path)


def test_audio_backend_defaults_to_auto_and_loads_explicit_values(
    tmp_path: Path,
) -> None:
    assert default_config().audio.backend == "auto"
    path = tmp_path / "config.toml"
    for backend in ("wasapi", "pipewire", "coreaudio"):
        path.write_text(
            f'[audio]\nbackend = "{backend}"\n', encoding="utf-8"
        )
        assert load_config(path).audio.backend == backend


@pytest.mark.parametrize("backend", ["alsa", "WASAPI", ""])
def test_rejects_invalid_audio_backend(tmp_path: Path, backend: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f'[audio]\nbackend = "{backend}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="audio.backend"):
        load_config(path)


def test_platform_overlay_inherits_and_overrides_shared_config(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(
        '[audio]\nmic_device = "Linux mic"\nframe_ms = 30\n'
        '[stt]\nmodel = "small"\ndevice = "cuda"\n',
        encoding="utf-8",
    )
    overlay = tmp_path / "config.macos.toml"
    overlay.write_text(
        'extends = "config.toml"\n'
        '[audio]\nmic_device = ""\noutput_device = "BlackHole"\n'
        '[stt]\ndevice = "cpu"\n',
        encoding="utf-8",
    )

    config = load_config(overlay)

    assert config.audio.mic_device == ""
    assert config.audio.output_device == "BlackHole"
    assert config.audio.frame_ms == 30
    assert config.stt.model == "small"
    assert config.stt.device == "cpu"


def test_shipped_macos_overlay_starts_general_and_cpu_only() -> None:
    config = load_config(Path(__file__).parents[1] / "config.macos.toml")

    assert config.audio.mic_device == ""
    assert config.audio.output_device == ""
    assert config.stt.device == "cpu"
    assert config.stt.profile_hints is False
    assert config.context.profile == ""
    # With no active profile and an empty fallback path no pack loads, but the
    # feature stays enabled so selecting a profile can activate its own pack.
    assert config.knowledge.enabled is True


def test_config_overlay_rejects_missing_base_and_cycles(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    missing.write_text('extends = "nowhere.toml"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="does not exist"):
        load_config(missing)

    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text('extends = "second.toml"\n', encoding="utf-8")
    second.write_text('extends = "first.toml"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="extends cycle"):
        load_config(first)
