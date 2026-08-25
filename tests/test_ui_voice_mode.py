from __future__ import annotations

import asyncio

from ambientqa.ui import AmbientQAApp


class _Controller:
    paused = False

    def __init__(self, voice_enabled: bool, agent_mode: bool = False) -> None:
        self.voice_enabled = voice_enabled
        self.agent_mode = agent_mode
        self._agent_customer_channel = "mic"
        self.toggles = 0
        self.inputs = {"mic": True, "sys": True}

    def status_text(self) -> str:
        return "test"

    def toggle_interaction_mode(self) -> str:
        self.toggles += 1
        return "Conversation mode"

    def toggle_agent_mode(self) -> str:
        self.agent_mode = not self.agent_mode
        return "Agent role" if self.agent_mode else "Assist role"

    def toggle_input_channel(self, channel: str) -> bool:
        self.inputs[channel] = not self.inputs[channel]
        return self.inputs[channel]

    def input_channel_enabled(self, channel: str) -> bool:
        return self.inputs[channel]


def test_talk_mode_binding_is_hidden_in_assist_mode() -> None:
    app = AmbientQAApp(_Controller(False), status_interval_s=60)
    assert app.check_action("conversation", ()) is False
    assert app.check_action("agent_mode", ()) is False


def test_talk_mode_key_is_available_in_voice_mode() -> None:
    controller = _Controller(True)
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            assert app.check_action("conversation", ()) is True
            await pilot.press("r")
            await pilot.pause()

    asyncio.run(drive())
    assert controller.toggles == 1


def test_agent_role_and_delivery_toggle_are_independent() -> None:
    controller = _Controller(True, agent_mode=True)
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            assert app.check_action("conversation", ()) is True
            await pilot.press("r")
            await pilot.pause()

    asyncio.run(drive())
    assert controller.toggles == 1


def test_agent_role_has_a_dedicated_non_conflicting_key() -> None:
    controller = _Controller(True)
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            assert app.check_action("agent_mode", ()) is True
            await pilot.press("g")
            await pilot.pause()

    asyncio.run(drive())
    assert controller.agent_mode is True


def test_input_channel_keys_are_independent_and_visible() -> None:
    controller = _Controller(True)
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test(size=(110, 16)) as pilot:
            await pilot.press("1")
            await pilot.press("2")
            await pilot.pause()

    asyncio.run(drive())
    assert controller.inputs == {"mic": False, "sys": False}


def test_agent_mode_renders_live_banner_and_speaker_wording() -> None:
    controller = _Controller(True, agent_mode=True)
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test(size=(110, 16)) as pilot:
            await app.add_question("turn-1", "Hello, I need help with my account.")
            await pilot.pause()
            screen = "\n".join(
                "".join(segment.text for segment in strip)
                for strip in app.screen._compositor.render_strips()
            )
            assert "AGENT LIVE" in screen
            assert "SPEAKER" in screen
            assert "responding" in screen

    asyncio.run(drive())


def test_agent_banner_reports_waiting_when_speaker_input_is_muted() -> None:
    controller = _Controller(True, agent_mode=True)
    controller.inputs["mic"] = False
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test(size=(110, 16)) as pilot:
            await pilot.pause()
            screen = "\n".join(
                "".join(segment.text for segment in strip)
                for strip in app.screen._compositor.render_strips()
            )
            assert "AGENT WAITING" in screen
            assert "speaker microphone is muted" in screen

    asyncio.run(drive())
