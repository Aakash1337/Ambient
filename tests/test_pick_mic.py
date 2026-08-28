from __future__ import annotations

from pathlib import Path

from scripts.pick_mic import PROJECT_ROOT, _default_config_path, _parse_args


def test_picker_uses_macos_overlay_on_darwin() -> None:
    assert _default_config_path("darwin") == PROJECT_ROOT / "config.macos.toml"


def test_picker_uses_shared_config_on_other_platforms() -> None:
    assert _default_config_path("win32") == PROJECT_ROOT / "config.toml"
    assert _default_config_path("linux") == PROJECT_ROOT / "config.toml"


def test_picker_accepts_an_explicit_config_path() -> None:
    selected = Path("local-overlay.toml")
    assert _parse_args(["--config", str(selected)]).config == selected
