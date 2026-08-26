"""Backend contract tests: PipeWire enumeration/streams and platform selection.

The PipeWire fixtures mirror real `pactl --format=json list sources` output --
monitor sources are distinguished only by properties["device.class"], and the
default sink's monitor is named "<sink>.monitor".
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from typing import Any

import numpy as np
import pytest

from ambientqa.backends import get_backend
from ambientqa.backends.linux import (
    ParecStream,
    PipewireBackend,
    _parse_sample_spec,
    _run_command,
)
from ambientqa.backends.macos import CoreAudioBackend
from ambientqa.backends.windows import WasapiBackend
from ambientqa.config import AudioConfig

ONBOARD_MIC = "alsa_input.pci-0000_0d_00.4.analog-stereo"
USB_MIC = "alsa_input.usb-C-Media_Electronics_Inc._USB_Advanced_Audio_Device-00.analog-stereo"
HDMI_MONITOR = "alsa_output.pci-0000_0b_00.1.hdmi-stereo.monitor"
ONBOARD_MONITOR = "alsa_output.pci-0000_0d_00.4.analog-stereo.monitor"
USB_MONITOR = (
    "alsa_output.usb-C-Media_Electronics_Inc._USB_Advanced_Audio_Device-00"
    ".analog-stereo.monitor"
)
DEFAULT_SINK = "alsa_output.pci-0000_0d_00.4.analog-stereo"

PACTL_SOURCES = [
    {
        "index": 40,
        "name": HDMI_MONITOR,
        "description": "Monitor of AD103 High Definition Audio Controller Digital Stereo (HDMI)",
        "sample_specification": "s32le 2ch 48000Hz",
        "state": "SUSPENDED",
        "monitor_source": "alsa_output.pci-0000_0b_00.1.hdmi-stereo",
        "properties": {"device.class": "monitor"},
    },
    {
        "index": 41,
        "name": ONBOARD_MONITOR,
        "description": "Monitor of Starship/Matisse HD Audio Controller Analog Stereo",
        "sample_specification": "s32le 2ch 48000Hz",
        "state": "IDLE",
        "monitor_source": DEFAULT_SINK,
        "properties": {"device.class": "monitor"},
    },
    {
        "index": 42,
        "name": ONBOARD_MIC,
        "description": "Starship/Matisse HD Audio Controller Analog Stereo",
        "sample_specification": "s32le 2ch 48000Hz",
        "state": "SUSPENDED",
        "monitor_source": "",
        "properties": {"device.class": "sound"},
    },
    {
        "index": 43,
        "name": USB_MONITOR,
        "description": "Monitor of USB Advanced Audio Device Analog Stereo",
        "sample_specification": "s16le 2ch 48000Hz",
        "state": "RUNNING",
        "monitor_source": "alsa_output.usb-C-Media_Electronics_Inc._USB_Advanced_Audio_Device-00.analog-stereo",
        "properties": {"device.class": "monitor"},
    },
    {
        "index": 44,
        "name": USB_MIC,
        "description": "USB Advanced Audio Device Analog Stereo",
        "sample_specification": "s16le 1ch 44100Hz",
        "state": "RUNNING",
        "monitor_source": "",
        "properties": {"device.class": "sound"},
    },
]


def _fake_run(argv: list[str]) -> str:
    if argv == ["pactl", "--format=json", "list", "sources"]:
        return json.dumps(PACTL_SOURCES)
    if argv == ["pactl", "get-default-source"]:
        return USB_MIC + "\n"
    if argv == ["pactl", "get-default-sink"]:
        return DEFAULT_SINK + "\n"
    raise AssertionError(f"unexpected command: {argv}")


def _backend(**kwargs: Any) -> PipewireBackend:
    return PipewireBackend(run_command=_fake_run, **kwargs)


# --- enumeration and candidate ordering ---


def test_monitor_sources_classify_as_loopback_and_real_inputs_as_mic() -> None:
    devices = {device.id: device for device in _backend().list_devices()}
    assert devices[ONBOARD_MIC].kind == "mic"
    assert devices[USB_MIC].kind == "mic"
    for monitor in (HDMI_MONITOR, ONBOARD_MONITOR, USB_MONITOR):
        assert devices[monitor].kind == "loopback"


def test_list_devices_orders_mics_before_monitors() -> None:
    kinds = [device.kind for device in _backend().list_devices()]
    assert kinds == ["mic", "mic", "loopback", "loopback", "loopback"]


def test_sample_specification_parses_channels_and_rate() -> None:
    devices = {device.id: device for device in _backend().list_devices()}
    assert (devices[USB_MIC].channels, devices[USB_MIC].sample_rate) == (1, 44100)
    assert (devices[ONBOARD_MIC].channels, devices[ONBOARD_MIC].sample_rate) == (2, 48000)


def test_sample_specification_defaults_when_absent_or_malformed() -> None:
    assert _parse_sample_spec("") == (2, 48000)
    assert _parse_sample_spec("weird") == (2, 48000)


def test_description_is_the_display_name_and_source_name_is_the_id() -> None:
    devices = {device.id: device for device in _backend().list_devices()}
    assert devices[USB_MIC].name == "USB Advanced Audio Device Analog Stereo"
    assert devices[USB_MIC].display_name == "USB Advanced Audio Device Analog Stereo"


def test_blank_mic_puts_the_default_source_first_then_fallbacks() -> None:
    session = _backend().open_session()
    assert [d.id for d in session.mic_candidates("")] == [USB_MIC, ONBOARD_MIC]


def test_blank_loopback_puts_default_sink_monitor_first_then_all_the_rest() -> None:
    session = _backend().open_session()
    assert [d.id for d in session.loopback_candidates("")] == [
        ONBOARD_MONITOR,
        HDMI_MONITOR,
        USB_MONITOR,
    ]


def test_pinned_substring_matches_description_or_pipewire_name() -> None:
    session = _backend().open_session()
    # Human label, as a Windows-written config would pin it.
    assert [d.id for d in session.mic_candidates("usb advanced")] == [USB_MIC]
    # Stable PipeWire source name, as a Linux user might pin it.
    assert [d.id for d in session.mic_candidates("alsa_input.pci")] == [ONBOARD_MIC]
    assert [d.id for d in session.loopback_candidates("HDMI")] == [HDMI_MONITOR]


def test_pinned_mic_without_match_raises() -> None:
    session = _backend().open_session()
    with pytest.raises(RuntimeError, match="Odyssey"):
        session.mic_candidates("Odyssey G80SD")


def test_stale_pinned_monitor_warns_and_falls_back_to_default() -> None:
    """Raising here would mean mic-only, silently losing the answerable side."""
    warnings: list[str] = []
    session = _backend().open_session()
    found = session.loopback_candidates("Odyssey G80SD", warnings.append)
    assert [d.id for d in found] == [ONBOARD_MONITOR]
    assert len(warnings) == 1
    assert "Odyssey G80SD" in warnings[0]


def test_missing_pactl_surfaces_an_install_hint() -> None:
    with pytest.raises(RuntimeError, match="pipewire-pulse"):
        _run_command(["ambientqa-definitely-not-installed"])


def test_failing_pactl_reports_the_exit() -> None:
    with pytest.raises(RuntimeError, match="false failed"):
        _run_command(["false"])


# --- parec stream reads ---


class FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_read_assembles_exactly_the_requested_float32_frames() -> None:
    samples = np.arange(800, dtype=np.float32)
    process = FakeProcess(stdout=samples.tobytes())
    backend = _backend(process_factory=lambda device_id: process)
    session = backend.open_session()
    stream = session.open(session.mic_candidates("")[0])
    assert (stream.rate, stream.channels) == (16000, 1)
    first = stream.read(400)
    second = stream.read(400)
    assert np.array_equal(first, samples[:400])
    assert np.array_equal(second, samples[400:])


def test_eof_raises_with_the_stderr_tail() -> None:
    process = FakeProcess(
        stdout=np.zeros(100, dtype=np.float32).tobytes(),
        stderr=b"Connection failure: Connection refused\n",
    )
    stream = ParecStream(process)
    with pytest.raises(RuntimeError, match="Connection refused"):
        # More frames than the process will ever deliver: the short payload
        # exercises the assembly loop before EOF surfaces.
        stream.read(400)


def test_stream_stop_and_close_are_idempotent_on_a_dead_process() -> None:
    stream = ParecStream(FakeProcess())
    stream.stop()
    stream.stop()
    stream.close()


class PipeProcess:
    """stdout backed by a real OS pipe so read() genuinely blocks; terminate()
    closes the write end, which is exactly what killing parec does."""

    def __init__(self, stderr: bytes = b"") -> None:
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb")
        self.stderr = io.BytesIO(stderr)
        self._write_open = True

    def feed(self, data: bytes) -> None:
        os.write(self._write_fd, data)

    def terminate(self) -> None:
        if self._write_open:
            self._write_open = False
            os.close(self._write_fd)

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_stop_unblocks_a_reader_blocked_in_read() -> None:
    process = PipeProcess(stderr=b"Connection terminated\n")
    # Half of what read() wants: the reader must enter its assembly loop and
    # then genuinely block on the pipe.
    process.feed(np.zeros(200, dtype=np.float32).tobytes())
    stream = ParecStream(process)
    errors: list[BaseException] = []

    def blocked_read() -> None:
        try:
            stream.read(400)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=blocked_read, daemon=True)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive(), "reader should be blocked mid-frame"
    stream.stop()
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "stop() must unblock the blocked reader"
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "Connection terminated" in str(errors[0])
    stream.close()


# --- platform selection and the WASAPI factory seam ---


def test_get_backend_honors_explicit_choices() -> None:
    assert isinstance(get_backend(AudioConfig(backend="pipewire")), PipewireBackend)
    assert isinstance(get_backend(AudioConfig(backend="wasapi")), WasapiBackend)


def test_get_backend_auto_selects_the_platform_native_stack() -> None:
    expected = (
        WasapiBackend
        if sys.platform == "win32"
        else CoreAudioBackend
        if sys.platform == "darwin"
        else PipewireBackend
    )
    assert isinstance(get_backend(AudioConfig(backend="auto")), expected)


class _EnumFakeAudio:
    """Enough of pyaudiowpatch's PyAudio for enumeration, with none installed."""

    _devices = [
        {
            "name": "Microphone (USB)",
            "hostApi": 2,
            "maxInputChannels": 1,
            "defaultSampleRate": 48000,
        },
        {
            "name": "Voicemeeter Out (plug)",
            "hostApi": 2,
            "maxInputChannels": 128,
            "defaultSampleRate": 48000,
        },
        {
            "name": "Speakers [Loopback]",
            "hostApi": 2,
            "maxInputChannels": 2,
            "defaultSampleRate": 48000,
            "isLoopbackDevice": True,
        },
    ]

    def __init__(self) -> None:
        self.terminated = False

    def get_host_api_info_by_type(self, host_api_type: int) -> dict[str, Any]:
        # 13 == paWASAPI; reaching here without pyaudiowpatch installed proves
        # the constant is resolved without importing it.
        assert host_api_type == 13
        return {"index": 2}

    def get_device_count(self) -> int:
        return len(self._devices)

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        return dict(self._devices[index])

    def terminate(self) -> None:
        self.terminated = True


def test_wasapi_injected_factory_enumerates_without_pyaudiowpatch() -> None:
    audio = _EnumFakeAudio()
    devices = WasapiBackend(audio_factory=lambda: audio).list_devices()
    assert [(device.kind, device.id) for device in devices] == [
        ("mic", "0"),
        ("mic", "1"),
        ("loopback", "2"),
    ]
    # The 128-channel plug device is clamped to real capture-hardware counts.
    assert devices[1].channels == 2
    assert audio.terminated
