from __future__ import annotations

import asyncio
from pathlib import Path

from ambientqa.__main__ import AmbientController
from ambientqa.audio import SourceState
from ambientqa.audio_devices import AudioDevice, MeterReading
from ambientqa.bus import DropOldestQueue
from ambientqa.config import default_config, load_config
from ambientqa.ui import AmbientQAApp, AudioDevicesScreen


class _FakeSession:
    def __init__(self) -> None:
        self.devices = [
            AudioDevice(1, "First microphone", "mic", 1, 48000),
            AudioDevice(2, "Second microphone", "mic", 1, 48000),
            AudioDevice(3, "Speaker loopback", "loopback", 2, 48000),
        ]
        self.active_mic = "First microphone"
        self.active_loopback = "Speaker loopback"
        self.closed = False

    def snapshot(self, width: int = 18) -> dict[tuple[str, int], MeterReading]:
        return {device.key: MeterReading() for device in self.devices}

    def close(self) -> None:
        self.closed = True


class _FakeCapture:
    def __init__(self) -> None:
        self.mic = SourceState("mic", True, "First microphone")
        self.loopback = SourceState("loopback", True, "Speaker loopback")
        self.stops = 0
        self.starts = 0
        self.start_enabled: bool | None = None

    def stop(self) -> None:
        self.stops += 1

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        output: DropOldestQueue[object],
        *,
        enabled: bool,
    ) -> None:
        self.starts += 1
        self.start_enabled = enabled


class _FakeSegmenter:
    def __init__(self) -> None:
        self.resets = 0

    def reset_all(self) -> None:
        self.resets += 1


def test_modal_select_updates_config_and_restarts_capture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[audio]\nmic_device = ""\noutput_device = ""\n')
    config = default_config()
    controller = AmbientController.__new__(AmbientController)
    controller.paused = False
    controller.config = config
    controller.config_path = config_path
    controller.paused = False
    controller.frames = DropOldestQueue(8)
    controller.utterances = DropOldestQueue(8)
    controller.capture = _FakeCapture()
    controller.segmenter = _FakeSegmenter()
    controller._capture_loop = None
    controller._device_lock = asyncio.Lock()
    reported: list[str] = []
    controller._report = reported.append
    session = _FakeSession()
    monkeypatch.setattr(
        "ambientqa.__main__.AudioDeviceSession.open",
        lambda **_kwargs: session,
    )
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        controller._capture_loop = asyncio.get_running_loop()
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, AudioDevicesScreen)
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(drive())

    assert controller.capture.stops == 1
    assert controller.capture.starts == 1
    assert controller.capture.start_enabled is True
    assert controller.segmenter.resets == 1
    assert session.closed
    assert config.audio.mic_device == "Second microphone"
    assert load_config(config_path).audio.mic_device == "Second microphone"
    assert reported == ["Audio device selected: Second microphone"]
