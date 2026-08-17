from __future__ import annotations

import asyncio
from pathlib import Path

from ambientqa.__main__ import AmbientController
from ambientqa.config import default_config, load_config
from ambientqa.ui import AmbientQAApp, ProfilesScreen


class _ProfileConsumer:
    def __init__(self) -> None:
        self.profile = None

    def set_profile(self, profile) -> None:
        self.profile = profile


def test_profile_modal_selects_persists_and_clears_all_stages(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile_path = profiles / "aws.md"
    profile_path.write_text(
        "# AWS prep\n"
        "## Topic\nAmazon Bedrock\n"
        "## Background\nPython backend engineer\n"
        "## Vocabulary\nBedrock, FastAPI\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "# keep this comment\n"
        "[context]\n"
        "enabled = true\n"
        'profile = "" # active selection\n',
        encoding="utf-8",
    )

    controller = AmbientController.__new__(AmbientController)
    controller.paused = False
    controller.config = default_config()
    controller.config_path = config_path
    controller.profile = None
    controller.transcriber = _ProfileConsumer()
    controller.gate = _ProfileConsumer()
    controller.answerer = _ProfileConsumer()
    reports: list[str] = []
    controller._report = reports.append
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ProfilesScreen)
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert controller.config.context.profile == "profiles/aws.md"
            assert controller.profile is not None
            assert controller.transcriber.profile is controller.profile
            assert controller.gate.profile is controller.profile
            assert controller.answerer.profile is controller.profile

            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ProfilesScreen)
            await pilot.press("up")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(drive())

    assert controller.config.context.profile == ""
    assert controller.profile is None
    assert controller.transcriber.profile is None
    assert controller.gate.profile is None
    assert controller.answerer.profile is None
    assert load_config(config_path).context.profile == ""
    assert "# keep this comment" in config_path.read_text(encoding="utf-8")
    assert reports == ["Profile active: AWS prep", "Profile disabled"]

