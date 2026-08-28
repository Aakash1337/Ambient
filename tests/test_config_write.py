from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from ambientqa.config import load_config
from ambientqa.config_write import set_audio_device, set_context_profile


def test_rewrites_only_audio_entry_and_preserves_comments(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = (
        "# heading\r\n"
        "[audio]\r\n"
        "mic_device = 'Old mic' # keep this\r\n"
        "output_device = \"Speakers\"\r\n"
        "# audio note\r\n"
        "[other]\r\n"
        "mic_device = \"untouched\"\r\n"
    )
    path.write_bytes(original.encode())
    set_audio_device(path, "mic_device", "Logitech C930e")
    updated = path.read_text(encoding="utf-8")
    assert "mic_device = 'Logitech C930e' # keep this" in updated
    assert '[other]\nmic_device = "untouched"' in updated
    assert updated.replace("'Logitech C930e'", "'Old mic'") == original.replace(
        "\r\n", "\n"
    )
    assert b"\r\n" in path.read_bytes()


def test_inserts_missing_key_inside_audio_table(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[audio]\nmic_device = \"Mic\"\n\n[gate]\nmode = \"balanced\"\n")
    set_audio_device(path, "output_device", "Speakers")
    text = path.read_text()
    assert text.index('output_device = "Speakers"') < text.index("[gate]")
    assert load_config(path).audio.output_device == "Speakers"


def test_inserts_missing_audio_table(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# keep\n[gate]\nmode = \"strict\"\n")
    set_audio_device(path, "mic_device", "Broadcast")
    assert path.read_text().endswith('[audio]\nmic_device = "Broadcast"\n')
    assert load_config(path).audio.mic_device == "Broadcast"


@pytest.mark.parametrize(
    ("initial", "value"),
    [
        ('mic_device = ""', ""),
        ("mic_device = ''", r"C:\Audio\Mic"),
        ('mic_device = ""', 'Mic "quoted" \\ path'),
    ],
)
def test_empty_and_escaped_values_round_trip(
    tmp_path: Path,
    initial: str,
    value: str,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[audio]\n{initial}\n")
    set_audio_device(path, "mic_device", value)
    assert load_config(path).audio.mic_device == value


def test_failed_atomic_replace_leaves_original_and_no_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    original = b'[audio]\nmic_device = "before"\n'
    path.write_bytes(original)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr("ambientqa.config_write.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        set_audio_device(path, "mic_device", "after")
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".config.toml.*.tmp")) == []


def test_rejects_unsupported_key_without_touching_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = '[audio]\nmic_device = ""\n'
    path.write_text(original)
    with pytest.raises(ValueError, match="mic_device or output_device"):
        set_audio_device(path, "sample_rate", "48000")  # type: ignore[arg-type]
    assert path.read_text() == original


def test_updates_valid_quoted_key_without_inserting_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[audio]\n"mic_device" = "Old"\n')
    set_audio_device(path, "mic_device", "New")
    assert path.read_text() == '[audio]\n"mic_device" = "New"\n'
    assert load_config(path).audio.mic_device == "New"


@pytest.mark.parametrize(
    "assignment",
    [
        'mic_device = """Old\nMic"""',
        "mic_device = '''Old\nMic'''",
        'mic_device = ["Old", "Mic"]',
    ],
)
def test_non_single_line_device_assignment_fails_without_corrupting_config(
    tmp_path: Path,
    assignment: str,
) -> None:
    path = tmp_path / "config.toml"
    original = f"[audio]\n{assignment}\noutput_device = \"Speakers\"\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot safely rewrite"):
        set_audio_device(path, "mic_device", "New")

    assert path.read_text(encoding="utf-8") == original


def test_mixed_simple_and_multiline_device_assignments_are_rejected_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    original = '[audio]\nmic_device = "Old"\nmic_device = """Other\nMic"""\n'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate audio.mic_device"):
        set_audio_device(path, "mic_device", "New")

    assert path.read_text(encoding="utf-8") == original


def test_table_header_inside_unrelated_multiline_value_cannot_misdirect_update(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    original = (
        '[audio]\noutput_device = """\n[gate]\n"""\nmic_device = "Old"\n'
    )
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot safely locate audio.mic_device"):
        set_audio_device(path, "mic_device", "New")

    assert path.read_text(encoding="utf-8") == original
    assert load_config(path).audio.mic_device == "Old"


def test_idempotent_target_cannot_hide_unrelated_multiline_value_corruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    original = (
        '[audio]\noutput_device = """\n[gate]\n"""\nmic_device = "New"\n'
    )
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot safely locate audio.mic_device"):
        set_audio_device(path, "mic_device", "New")

    assert path.read_text(encoding="utf-8") == original
    config = load_config(path)
    assert config.audio.mic_device == "New"
    assert config.audio.output_device == "[gate]\n"


def test_device_and_profile_writes_share_one_config_transaction_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ambientqa.config_write as config_write

    path = tmp_path / "config.toml"
    path.write_text(
        '[audio]\nmic_device = "Old"\n[context]\nprofile = "old.md"\n',
        encoding="utf-8",
    )
    original_update = config_write._updated_text
    device_inside = threading.Event()
    release_device = threading.Event()
    profile_inside = threading.Event()

    def delayed_update(text: str, section: str, key: str, value: str) -> str:
        if key == "mic_device":
            device_inside.set()
            assert release_device.wait(2)
        elif key == "profile":
            profile_inside.set()
        return original_update(text, section, key, value)

    monkeypatch.setattr(config_write, "_updated_text", delayed_update)
    device = threading.Thread(
        target=set_audio_device,
        args=(path, "mic_device", "New"),
    )
    profile = threading.Thread(
        target=set_context_profile,
        args=(path, "new.md"),
    )
    device.start()
    assert device_inside.wait(2)
    profile.start()
    assert not profile_inside.wait(0.1), "profile write bypassed device transaction"
    release_device.set()
    device.join(2)
    profile.join(2)

    assert not device.is_alive() and not profile.is_alive()
    config = load_config(path)
    assert config.audio.mic_device == "New"
    assert config.context.profile == "new.md"


def test_context_profile_round_trip_preserves_comments(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = (
        "# heading\n"
        "[context]\n"
        "enabled = true # keep enabled comment\n"
        "profile = '' # selected profile\n"
        "\n"
        "[gate]\n"
        "mode = \"balanced\" # untouched\n"
    )
    path.write_text(original, encoding="utf-8")
    set_context_profile(path, "profiles/aws-bedrock-interview.md")
    updated = path.read_text(encoding="utf-8")
    assert "profile = 'profiles/aws-bedrock-interview.md' # selected profile" in updated
    assert "enabled = true # keep enabled comment" in updated
    assert 'mode = "balanced" # untouched' in updated
    assert load_config(path).context.profile == "profiles/aws-bedrock-interview.md"
