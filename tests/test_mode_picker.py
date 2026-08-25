from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ambientqa.mode_picker import EmergencyConfirm, ModePickerApp, WebModePicker


def test_picker_uses_the_ambient_brand() -> None:
    app = ModePickerApp()

    async def inspect() -> None:
        async with app.run_test(size=(80, 24)):
            assert app.TITLE == "Ambient"
            assert str(app.query_one("#brand").render()) == "AMBIENT"

    asyncio.run(inspect())


def test_default_focus_and_enter_choose_assist() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.focused is not None
            assert app.focused.id == "assist"
            await pilot.press("enter")

    asyncio.run(drive())
    assert app.return_value == "assist"


@pytest.mark.parametrize("key", ["v", "2"])
def test_voice_shortcuts_choose_voice(key: str) -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press(key)

    asyncio.run(drive())
    assert app.return_value == "voice"


def test_voice_button_can_be_clicked() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            assert await pilot.click("#voice") is True

    asyncio.run(drive())
    assert app.return_value == "voice"


@pytest.mark.parametrize("key", ["w", "3"])
def test_web_shortcuts_choose_web(key: str) -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, WebModePicker)
            assert app.focused is not None
            assert app.focused.id == "web-assist"
            await pilot.press("enter")

    asyncio.run(drive())
    assert app.return_value == "web"


def test_web_button_can_be_clicked() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            assert await pilot.click("#web") is True
            await pilot.pause()
            assert isinstance(app.screen, WebModePicker)
            assert await pilot.click("#web-assist") is True

    asyncio.run(drive())
    assert app.return_value == "web"


def test_web_voice_shortcut_can_be_chosen_from_web_dialog() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WebModePicker)
            await pilot.press("v")

    asyncio.run(drive())
    assert app.return_value == "web_voice"


def test_web_voice_button_can_be_clicked() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            assert await pilot.click("#web") is True
            await pilot.pause()
            assert isinstance(app.screen, WebModePicker)
            assert await pilot.click("#web-voice") is True

    asyncio.run(drive())
    assert app.return_value == "web_voice"


def test_web_dialog_escape_returns_to_launcher_without_starting() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WebModePicker)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, WebModePicker)
            assert app.return_value is None
            await pilot.press("q")

    asyncio.run(drive())
    assert app.return_value is None


@pytest.mark.parametrize("key", ["escape", "q"])
def test_cancel_shortcuts_launch_nothing(key: str) -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press(key)

    asyncio.run(drive())
    assert app.return_value is None


def test_emergency_requires_confirmation_and_cancel_is_safe() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            assert await pilot.click("#emergency") is True
            await pilot.pause()
            assert isinstance(app.screen, EmergencyConfirm)
            assert app.focused is not None
            assert app.focused.id == "cancel-emergency"
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, EmergencyConfirm)
            await pilot.press("q")

    asyncio.run(drive())
    assert app.return_value is None


def test_exit_codes_keep_the_run_sh_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run.sh maps these numbers to launch flags; they must never drift."""
    from ambientqa import mode_picker

    for selection, code in [
        ("assist", 0),
        ("voice", 10),
        (None, 20),
        ("emergency", 30),
        ("web", 40),
        ("web_voice", 50),
    ]:
        monkeypatch.setattr(
            mode_picker.ModePickerApp,
            "run",
            lambda self, _selection=selection: _selection,
        )
        with pytest.raises(SystemExit) as excinfo:
            mode_picker.main()
        assert excinfo.value.code == code


def test_run_sh_maps_web_picker_roles_to_one_browser_pipeline() -> None:
    script = (Path(__file__).parents[1] / "run.sh").read_text(encoding="utf-8")

    assert "40) set -- --web --open-browser ;;" in script
    assert "50) set -- --web --voice --open-browser ;;" in script


def test_confirmed_emergency_returns_fallback_choice() -> None:
    app = ModePickerApp()

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            assert await pilot.click("#emergency") is True
            await pilot.pause()
            assert await pilot.click("#confirm-emergency") is True

    asyncio.run(drive())
    assert app.return_value == "emergency"
