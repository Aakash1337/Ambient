from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pytest

from ambientqa.audio import AudioCapture
from ambientqa.backends.base import CaptureDevice
from ambientqa.bus import AudioFrame, DropOldestQueue, Transcript
from ambientqa.config import AudioConfig, MergeConfig
from ambientqa.continuity import ContinuityMerger


class _Stream:
    rate = 16000
    channels = 1

    def __init__(self) -> None:
        self.stopped = threading.Event()

    def read(self, frames: int) -> np.ndarray:
        if self.stopped.wait(0.001):
            raise RuntimeError("stopped")
        return np.full(frames, 0.1, dtype=np.float32)

    def stop(self) -> None:
        self.stopped.set()

    def close(self) -> None:
        pass


class _Session:
    def __init__(self) -> None:
        self.mic = CaptureDevice("mic-1", "Test microphone", "mic", 1, 16000)
        self.sys = CaptureDevice("sys-1", "Test loopback", "loopback", 1, 16000)

    def mic_candidates(self, _substring: str, _report: Callable[[str], None]):
        return [self.mic]

    def loopback_candidates(
        self, _substring: str, _report: Callable[[str], None]
    ):
        return [self.sys]

    def open(self, _device: CaptureDevice) -> _Stream:
        return _Stream()

    def close(self) -> None:
        pass


class _Backend:
    name = "test"
    has_system_audio = True

    def open_session(self) -> _Session:
        return _Session()


class _BlockedFirstReadStream:
    """Hold one captured frame across a complete off/on input cycle."""

    rate = 16000
    channels = 1

    def __init__(self) -> None:
        self.reading = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()
        self._reads = 0

    def read(self, frames: int) -> np.ndarray:
        self._reads += 1
        if self._reads == 1:
            self.reading.set()
            assert self.release.wait(1.0), "test did not release blocked audio read"
            return np.full(frames, 0.1, dtype=np.float32)
        self.stopped.wait(1.0)
        raise RuntimeError("stopped")

    def stop(self) -> None:
        self.stopped.set()
        self.release.set()

    def close(self) -> None:
        pass


async def _wait_for_frames(
    queue: DropOldestQueue[AudioFrame], timeout: float = 1.0
) -> None:
    deadline = time.monotonic() + timeout
    while not queue.qsize() and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert queue.qsize(), "capture produced no frames"


def test_channel_enable_is_independent_and_survives_capture_restart() -> None:
    async def scenario() -> None:
        capture = AudioCapture(AudioConfig(), backend=_Backend())
        frames: DropOldestQueue[AudioFrame] = DropOldestQueue(128)
        loop = asyncio.get_running_loop()

        capture.set_channel_enabled("mic", False)
        assert capture.channel_enabled("mic") is False
        assert capture.channel_enabled("sys") is True

        capture.start(loop, frames)
        try:
            await _wait_for_frames(frames)
            await asyncio.sleep(0.02)
            assert {frame.channel for frame in frames.drain()} == {"sys"}
            # Disabled inputs stay physically open and measured. Muting is a
            # logical pipeline choice, not a device teardown.
            assert capture.mic.active is True
            assert capture.mic.last_signal_at > 0
        finally:
            capture.stop()

        assert capture.channel_enabled("mic") is False
        capture.start(loop, frames)
        try:
            await _wait_for_frames(frames)
            await asyncio.sleep(0.02)
            assert {frame.channel for frame in frames.drain()} == {"sys"}
        finally:
            capture.stop()

    asyncio.run(scenario())


def test_global_and_per_channel_enable_compose_at_runtime() -> None:
    async def scenario() -> None:
        capture = AudioCapture(AudioConfig(), backend=_Backend())
        frames: DropOldestQueue[AudioFrame] = DropOldestQueue(128)
        loop = asyncio.get_running_loop()
        capture.set_channel_enabled("mic", False)
        capture.start(loop, frames, enabled=False)
        try:
            await asyncio.sleep(0.03)
            assert frames.drain() == []

            capture.set_enabled(True)
            await _wait_for_frames(frames)
            await asyncio.sleep(0.02)
            assert {frame.channel for frame in frames.drain()} == {"sys"}

            capture.set_channel_enabled("sys", False)
            # Let any frame that had already passed the thread's Event check
            # land, then establish the steady disabled state.
            await asyncio.sleep(0.02)
            frames.drain()
            await asyncio.sleep(0.02)
            assert frames.drain() == []

            capture.set_channel_enabled("mic", True)
            await _wait_for_frames(frames)
            await asyncio.sleep(0.02)
            assert {frame.channel for frame in frames.drain()} == {"mic"}
        finally:
            capture.stop()

    asyncio.run(scenario())


def test_channel_enable_rejects_unknown_names() -> None:
    capture = AudioCapture(AudioConfig(), backend=_Backend())
    with pytest.raises(ValueError, match="Unknown audio channel"):
        capture.set_channel_enabled("radio", False)
    with pytest.raises(ValueError, match="Unknown audio channel"):
        capture.channel_enabled("radio")


def test_read_spanning_fast_off_on_cycle_is_never_forwarded() -> None:
    async def scenario() -> None:
        capture = AudioCapture(AudioConfig(), backend=_Backend())
        frames: DropOldestQueue[AudioFrame] = DropOldestQueue(8)
        stream = _BlockedFirstReadStream()
        device = CaptureDevice("mic-1", "Test microphone", "mic", 1, 16000)
        thread = threading.Thread(
            target=capture._capture_source,
            args=(
                "mic",
                device,
                stream,
                asyncio.get_running_loop(),
                frames,
                None,
                capture._generation,
            ),
            daemon=True,
        )
        thread.start()
        assert await asyncio.to_thread(stream.reading.wait, 1.0)

        capture.set_channel_enabled("mic", False)
        capture.set_channel_enabled("mic", True)
        stream.release.set()
        await asyncio.sleep(0.05)

        assert frames.drain() == []
        stream.stop()
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    asyncio.run(scenario())


def _transcript(channel: str, text: str, stamp: float) -> Transcript:
    return Transcript(channel, text, stamp, f"{channel}-{stamp}")


def test_continuity_discard_removes_only_the_selected_channel() -> None:
    merger = ContinuityMerger(MergeConfig())
    mic = _transcript("mic", "because the", 1.0)
    system = _transcript("sys", "since the", 2.0)
    assert merger.push(mic, now=10.0) == []
    assert merger.push(system, now=10.0) == []

    assert merger.discard("mic") is mic
    assert merger.discard("mic") is None
    assert merger.flush_all() == [system]


@dataclass
class _Queued:
    channel: str
    value: int


class _HeldLoop:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[], None]] = []

    def call_soon_threadsafe(self, callback: Callable[[], None]) -> None:
        self.callbacks.append(callback)


def test_queue_discard_where_covers_visible_and_thread_pending_items() -> None:
    queue: DropOldestQueue[_Queued] = DropOldestQueue(8)
    queue.put_nowait(_Queued("mic", 1))
    queue.put_nowait(_Queued("sys", 2))
    queue.put_nowait(_Queued("mic", 3))
    loop = _HeldLoop()
    queue.put_from_thread(loop, _Queued("mic", 4))  # type: ignore[arg-type]
    queue.put_from_thread(loop, _Queued("sys", 5))  # type: ignore[arg-type]

    discarded = queue.discard_where(lambda item: item.channel == "mic")
    assert [item.value for item in discarded] == [1, 3, 4]
    assert [item.value for item in queue.drain()] == [2]

    assert len(loop.callbacks) == 1
    loop.callbacks[0]()
    assert [item.value for item in queue.drain()] == [5]
