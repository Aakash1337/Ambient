from __future__ import annotations

from pathlib import Path

import pytest

from ambientqa.config import default_config, load_config


def test_defaults_match_spec() -> None:
    config = default_config()
    assert config.audio.sample_rate == 16000
    assert config.audio.frame_ms == 25
    assert config.audio.silence_ms == 900
    assert config.audio.max_utterance_s == 20.0
    assert config.stt.model == "large-v3-turbo"
    assert config.stt.device == "cuda"
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
    assert config.gate.max_concurrent == 3
    assert config.answer.answer_timeout_s == 45.0
    # Your own channel answers direct questions but never has its narration
    # mined for them; the other speaker's channel is judged freely.
    assert config.gate.channel_policy == {"mic": "explicit", "sys": "full"}
    assert config.answer.style == "cue"
    assert config.audio.silent_source_warn_s == 45.0


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


def test_audio_backend_defaults_to_auto_and_loads_explicit_values(
    tmp_path: Path,
) -> None:
    assert default_config().audio.backend == "auto"
    path = tmp_path / "config.toml"
    path.write_text('[audio]\nbackend = "pipewire"\n', encoding="utf-8")
    assert load_config(path).audio.backend == "pipewire"


@pytest.mark.parametrize("backend", ["alsa", "WASAPI", ""])
def test_rejects_invalid_audio_backend(tmp_path: Path, backend: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f'[audio]\nbackend = "{backend}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="audio.backend"):
        load_config(path)
