from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

from ambientqa.audio_devices import (
    CaptureDevice,
    DeviceMeterPool,
    amplitude_to_db,
    calculate_levels,
    level_to_bar,
    short_error,
)
from ambientqa.backends.windows import WasapiBackend, classify_capture_devices


def test_device_grouping_keeps_only_wasapi_capture_endpoints() -> None:
    raw = [
        {
            "index": 0,
            "name": "MME duplicate",
            "hostApi": 0,
            "maxInputChannels": 1,
            "defaultSampleRate": 44100,
        },
        {
            "index": 4,
            "name": "Microphone (NVIDIA Broadcast)",
            "hostApi": 2,
            "maxInputChannels": 1,
            "defaultSampleRate": 48000,
        },
        {
            "index": 5,
            "name": "Speakers",
            "hostApi": 2,
            "maxInputChannels": 0,
            "defaultSampleRate": 48000,
        },
        {
            "index": 7,
            "name": "Speakers [Loopback]",
            "hostApi": 2,
            "maxInputChannels": 2,
            "defaultSampleRate": 48000,
            "isLoopbackDevice": True,
        },
    ]
    devices = classify_capture_devices(raw, wasapi_host_index=2)
    assert [(device.kind, device.id) for device in devices] == [
        ("mic", "4"),
        ("loopback", "7"),
    ]
    assert "noise removal" in devices[0].display_name


def test_level_math_handles_silence_and_full_scale() -> None:
    silence = np.zeros(480, dtype=np.float32)
    full_scale = np.ones(480, dtype=np.float32)
    assert calculate_levels(silence) == (0.0, 0.0)
    assert amplitude_to_db(0.0) == -60.0
    assert level_to_bar(0.0, width=18) == 0
    assert calculate_levels(full_scale) == (1.0, 1.0)
    assert amplitude_to_db(1.0) == pytest.approx(0.0)
    assert level_to_bar(1.0, width=18) == 18


def test_level_math_reports_peak_and_rms() -> None:
    samples = np.array([1.0, 0.0, -1.0, 0.0], dtype=np.float32)
    peak, rms = calculate_levels(samples)
    assert peak == 1.0
    assert rms == pytest.approx(2**-0.5)


def test_short_error_survives_exceptions_with_empty_messages() -> None:
    """An error raised with no args must still describe itself, not IndexError."""
    assert short_error(OSError()) == "OSError"
    assert short_error(ValueError("")) == "ValueError"
    assert short_error(RuntimeError("device is busy")) == "device is busy"


class _FakeStream:
    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.closed = False

    def read(self, frames: int, exception_on_overflow: bool) -> bytes:
        if self.stopped.wait(0.002):
            raise RuntimeError("stopped")
        return np.zeros(frames, dtype=np.float32).tobytes()

    def stop_stream(self) -> None:
        self.stopped.set()

    def close(self) -> None:
        self.closed = True


class _FakeAudio:
    def __init__(self) -> None:
        self.streams: list[_FakeStream] = []
        self.open_calls: list[dict[str, Any]] = []
        self.terminated = False

    def open(self, **kwargs: Any) -> _FakeStream:
        self.open_calls.append(kwargs)
        if kwargs["input_device_index"] == 2:
            raise OSError("device is busy")
        stream = _FakeStream()
        self.streams.append(stream)
        return stream

    def terminate(self) -> None:
        self.terminated = True


def test_failing_device_becomes_unavailable_and_others_keep_metering() -> None:
    devices = [
        CaptureDevice("1", "Working mic", "mic", 1, 48000),
        CaptureDevice("2", "Busy physical mic", "mic", 1, 48000),
    ]
    audio = _FakeAudio()
    session = WasapiBackend(audio_factory=lambda: audio).open_session()
    pool = DeviceMeterPool(devices, session)
    pool.start()
    try:
        time.sleep(0.01)
        readings = pool.snapshot()
        assert readings[devices[0].key].unavailable is None
        assert readings[devices[1].key].unavailable == "device is busy"
        assert [call["input_device_index"] for call in audio.open_calls] == [1, 2]
        assert all("host_api_specific_stream_info" not in call for call in audio.open_calls)
    finally:
        pool.close()
    assert audio.terminated
    assert all(stream.closed for stream in audio.streams)
