"""CoreAudio backend tests use injected sounddevice fakes on every host."""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

from ambientqa.backends import get_backend
from ambientqa.backends.linux import PipewireBackend
from ambientqa.backends.macos import (
    CoreAudioBackend,
    CoreAudioStream,
    classify_coreaudio_devices,
)
from ambientqa.backends.windows import WasapiBackend
from ambientqa.config import AudioConfig


DEVICES = [
    {
        "name": "MacBook Speakers",
        "hostapi": 0,
        "max_input_channels": 0,
        "default_samplerate": 48000,
    },
    {
        "name": "BlackHole 2ch",
        "hostapi": 0,
        "max_input_channels": 2,
        "default_samplerate": 48000,
    },
    {
        "name": "MacBook Microphone",
        "hostapi": 0,
        "max_input_channels": 1,
        "default_samplerate": 48000,
    },
    {
        "name": "USB Interview Mic",
        "hostapi": 0,
        "max_input_channels": 2,
        "default_samplerate": 44100,
    },
    {
        "name": "Acme Virtual Router",
        "hostapi": 0,
        "max_input_channels": 16,
        "default_samplerate": 96000,
    },
    {
        "name": "Non-Core Test Input",
        "hostapi": 1,
        "max_input_channels": 1,
        "default_samplerate": 16000,
    },
]


class _Default:
    device = (3, 0)


class _RawInput:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.aborted = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def read(self, frames: int):
        channels = int(self.kwargs["channels"])
        samples = np.arange(frames * channels, dtype=np.float32)
        return samples.tobytes(), False

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


class _SoundDevice:
    default = _Default()

    def __init__(self, devices: list[dict[str, Any]] | None = None) -> None:
        self.devices = list(DEVICES if devices is None else devices)
        self.streams: list[_RawInput] = []

    def query_devices(self):
        return list(self.devices)

    def query_hostapis(self):
        return [{"name": "Core Audio"}, {"name": "Test API"}]

    def RawInputStream(self, **kwargs: Any) -> _RawInput:
        stream = _RawInput(**kwargs)
        self.streams.append(stream)
        return stream


def _backend(devices: list[dict[str, Any]] | None = None) -> CoreAudioBackend:
    return CoreAudioBackend(sounddevice_module=_SoundDevice(devices))


def test_coreaudio_classifies_mics_and_known_virtual_loopbacks() -> None:
    raw = [dict(item, index=index) for index, item in enumerate(DEVICES[:5])]
    devices = classify_coreaudio_devices(raw)
    assert [(device.kind, device.id) for device in devices] == [
        ("mic", "2"),
        ("mic", "3"),
        ("mic", "4"),
        ("loopback", "1"),
    ]
    # Large virtual/aggregate channel counts must never reach the downmixer.
    assert devices[2].channels == 2


def test_enumeration_filters_non_coreaudio_and_prefers_default_mic() -> None:
    backend = _backend()
    assert "5" not in {device.id for device in backend.list_devices()}
    session = backend.open_session()
    assert [device.id for device in session.mic_candidates("")] == ["3", "2", "4"]
    assert [device.id for device in session.loopback_candidates("")] == ["1"]


def test_pinned_devices_match_names_and_custom_loopback_drivers() -> None:
    session = _backend().open_session()
    assert [device.id for device in session.mic_candidates("MacBook")] == ["2"]
    custom = session.loopback_candidates("Acme Virtual")
    assert [(device.id, device.kind) for device in custom] == [("4", "loopback")]


def test_missing_pinned_mic_raises_and_stale_loopback_warns() -> None:
    session = _backend().open_session()
    with pytest.raises(RuntimeError, match="Studio Display"):
        session.mic_candidates("Studio Display")
    warnings: list[str] = []
    fallback = session.loopback_candidates("Old Soundflower", warnings.append)
    assert [device.id for device in fallback] == ["1"]
    assert len(warnings) == 1 and "Old Soundflower" in warnings[0]


def test_missing_loopback_explains_blackhole_and_mic_only_fallback() -> None:
    physical_only = [DEVICES[2], DEVICES[3]]
    session = _backend(physical_only).open_session()
    with pytest.raises(RuntimeError, match="BlackHole 2ch"):
        session.loopback_candidates("")


def test_open_uses_native_format_and_stream_lifecycle_is_idempotent() -> None:
    sounddevice = _SoundDevice()
    session = CoreAudioBackend(sounddevice_module=sounddevice, frame_ms=25).open_session()
    device = session.mic_candidates("USB")[0]
    stream = session.open(device)
    raw = sounddevice.streams[0]
    assert raw.started
    assert raw.kwargs == {
        "samplerate": 44100,
        "blocksize": 1102,
        "device": 3,
        "channels": 2,
        "dtype": "float32",
    }
    samples = stream.read(10)
    assert samples.dtype == np.float32
    assert np.array_equal(samples, np.arange(20, dtype=np.float32))
    stream.stop()
    stream.stop()
    stream.close()
    stream.close()
    assert raw.aborted and raw.closed


def test_failed_coreaudio_start_closes_the_native_stream() -> None:
    class BrokenStart(_RawInput):
        def start(self) -> None:
            raise RuntimeError("permission denied")

    class BrokenSoundDevice(_SoundDevice):
        def RawInputStream(self, **kwargs: Any) -> _RawInput:
            stream = BrokenStart(**kwargs)
            self.streams.append(stream)
            return stream

    sounddevice = BrokenSoundDevice()
    session = CoreAudioBackend(sounddevice_module=sounddevice).open_session()
    with pytest.raises(RuntimeError, match="permission denied"):
        session.open(session.mic_candidates("")[0])
    assert sounddevice.streams[0].closed


class _BlockingRawInput:
    def __init__(self) -> None:
        self.released = threading.Event()

    def read(self, _frames: int):
        self.released.wait()
        raise RuntimeError("PortAudio stream aborted")

    def abort(self) -> None:
        self.released.set()

    def close(self) -> None:
        pass


def test_stop_unblocks_a_coreaudio_reader() -> None:
    stream = CoreAudioStream(_BlockingRawInput(), rate=48000, channels=1)
    errors: list[BaseException] = []

    def read() -> None:
        try:
            stream.read(400)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive()
    stream.stop()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert "stopped" in str(errors[0]).casefold()


def test_backend_selection_covers_all_three_platforms(monkeypatch) -> None:
    assert isinstance(get_backend(AudioConfig(backend="coreaudio")), CoreAudioBackend)
    for platform, expected in (
        ("win32", WasapiBackend),
        ("linux", PipewireBackend),
        ("darwin", CoreAudioBackend),
    ):
        monkeypatch.setattr("ambientqa.backends.sys.platform", platform)
        assert isinstance(get_backend(AudioConfig(backend="auto")), expected)
