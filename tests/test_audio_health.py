"""A capture stream that opens successfully can still carry nothing.

Pinning `output_device` to an endpoint the call is not playing through opens a
perfectly healthy WASAPI loopback on silence. Nothing errors, the status bar
reads `sys:on`, and the entire other half of the conversation is lost for the
whole session. These cover the two defences against that.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

from ambientqa.audio import SIGNAL_RMS, LoopbackArbiter, SourceState
from ambientqa.backends.base import CaptureDevice
from ambientqa.backends.windows import _find_loopback, _loopback_candidates

LOUD = SIGNAL_RMS * 10
QUIET = SIGNAL_RMS / 10


class FakeAudio:
    """Minimal stand-in for the pyaudiowpatch handle used by _find_loopback."""

    def __init__(
        self,
        loopbacks: list[dict[str, Any]],
        default: dict[str, Any] | None = None,
        default_output: dict[str, Any] | None = None,
    ) -> None:
        self._loopbacks = loopbacks
        self._default = default
        self._default_output = default_output

    def get_loopback_device_info_generator(self):
        return iter(self._loopbacks)

    def get_default_wasapi_loopback(self):
        if self._default is None:
            raise RuntimeError("no default loopback")
        return self._default

    def get_default_output_device_info(self):
        if self._default_output is None:
            raise RuntimeError("no default output")
        return self._default_output


HEADSET = {"index": 33, "name": "Speakers (2- USB Advanced Audio Device) [Loopback]"}
ONBOARD = {"index": 32, "name": "Speakers (High Definition Audio Device) [Loopback]"}


def test_pinned_substring_selects_that_endpoint() -> None:
    audio = FakeAudio([ONBOARD, HEADSET])
    found = _find_loopback(audio, "USB Advanced Audio Device")
    assert found["index"] == 33


def test_stale_pinned_name_falls_back_instead_of_going_mic_only() -> None:
    """Raising here would mean mic-only, silently losing the answerable side."""
    warnings: list[str] = []
    audio = FakeAudio([ONBOARD, HEADSET], default=HEADSET)
    found = _find_loopback(audio, "Odyssey G80SD", warnings.append)
    assert found["index"] == 33
    assert len(warnings) == 1
    assert "Odyssey G80SD" in warnings[0]


def test_fallback_prefers_default_output_when_no_default_loopback() -> None:
    audio = FakeAudio(
        [ONBOARD, HEADSET],
        default_output={"name": "Speakers (2- USB Advanced Audio Device)"},
    )
    assert _find_loopback(audio, "")["index"] == 33


def test_no_loopback_endpoints_still_raises() -> None:
    with pytest.raises(RuntimeError, match="No WASAPI loopback"):
        _find_loopback(FakeAudio([]), "")


# --- watching every endpoint, following the one with the call on it ---


def test_blank_device_opens_every_endpoint_default_first() -> None:
    audio = FakeAudio([ONBOARD, HEADSET], default=HEADSET)
    found = _loopback_candidates(audio, "")
    assert [item["index"] for item in found] == [33, 32]


def test_pinned_device_opens_only_that_endpoint() -> None:
    audio = FakeAudio([ONBOARD, HEADSET], default=HEADSET)
    found = _loopback_candidates(audio, "High Definition")
    assert [item["index"] for item in found] == [32]


def test_arbiter_follows_whichever_endpoint_has_speech() -> None:
    arbiter = LoopbackArbiter(hold_s=1.5)
    # Silence everywhere: everyone forwards, so a first word is never clipped.
    assert arbiter.observe(32, QUIET, 0.0) is True
    assert arbiter.observe(33, QUIET, 0.0) is True
    # The headset starts carrying the call and takes the channel.
    assert arbiter.observe(33, LOUD, 1.0) is True
    assert arbiter.observe(32, QUIET, 1.0) is False
    assert arbiter.winner == 33


def test_arbiter_does_not_flap_while_the_incumbent_is_talking() -> None:
    """One segmenter cannot be fed two interleaved conversations."""
    arbiter = LoopbackArbiter(hold_s=1.5)
    arbiter.observe(33, LOUD, 1.0)
    # Noise on the speakers a moment later must not steal a live utterance.
    assert arbiter.observe(32, LOUD, 1.4) is False
    assert arbiter.winner == 33


def test_arbiter_hands_over_instantly_once_incumbent_has_gone_quiet() -> None:
    """Switching devices between sessions must not cost a word."""
    arbiter = LoopbackArbiter(hold_s=1.5)
    arbiter.observe(33, LOUD, 1.0)
    # Next session, hours later, the call is on the speakers instead.
    assert arbiter.observe(32, LOUD, 9999.0) is True
    assert arbiter.winner == 32


def test_arbiter_keeps_incumbent_through_pauses_in_its_own_speech() -> None:
    arbiter = LoopbackArbiter(hold_s=1.5)
    arbiter.observe(33, LOUD, 1.0)
    for tick in (1.2, 1.4, 1.6, 1.8):
        assert arbiter.observe(33, QUIET, tick) is True
    assert arbiter.observe(33, LOUD, 2.0) is True
    assert arbiter.winner == 33


class FakeStream:
    """Delivers `native_frames` of near-silent interleaved float32, then blocks."""

    def __init__(self, frames: int, channels: int, stop: "threading.Event") -> None:
        self.rate = 16000
        self.channels = channels
        self.payload = np.full(frames * channels, 1e-6, dtype=np.float32)
        self._stop = stop

    def read(self, _frames: int) -> np.ndarray:
        if self._stop.is_set():
            raise RuntimeError("stopped")
        time.sleep(0.001)
        return self.payload

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        pass


def test_a_winning_loopback_endpoint_never_mutes_the_microphone() -> None:
    """The mic loses every contest it was never entered in.

    Passing the loopback arbiter to the mic thread silenced the microphone
    completely the moment any output endpoint won: the mic's own index is never
    the winner, so every frame it produced was discarded.
    """
    import asyncio

    from ambientqa.audio import AudioCapture
    from ambientqa.bus import DropOldestQueue
    from ambientqa.config import AudioConfig

    capture = AudioCapture(AudioConfig())
    arbiter = LoopbackArbiter()
    arbiter.observe(32, LOUD, 1.0)  # a loopback endpoint holds the channel
    assert arbiter.winner == 32

    device = CaptureDevice("28", "Microphone (USB)", "mic", 2, 16000)

    async def drive() -> list[str]:
        queue: DropOldestQueue = DropOldestQueue(256)
        loop = asyncio.get_running_loop()
        stream = FakeStream(400, 2, capture._stop)
        thread = threading.Thread(
            target=capture._capture_source,
            args=("mic", device, stream, loop, queue, arbiter, capture._generation),
            daemon=True,
        )
        thread.start()
        for _ in range(50):
            await asyncio.sleep(0.02)
            if queue.qsize():
                break
        capture._stop.set()
        thread.join(timeout=2.0)
        return [frame.channel for frame in queue.drain()]

    channels = asyncio.run(drive())
    assert channels, "microphone produced no frames while a sys endpoint was winning"
    assert set(channels) == {"mic"}


def test_inactive_source_reports_no_silence_duration() -> None:
    """`off` and `open but deaf` are different problems; do not conflate them."""
    assert SourceState("sys", active=False).silent_for() is None


def test_open_source_measures_silence_from_open_time_until_first_signal() -> None:
    state = SourceState("sys", active=True, detail="Speakers", opened_at=1000.0)
    assert state.silent_for() == pytest.approx(
        __import__("time").time() - 1000.0, abs=2.0
    )


def test_signal_resets_the_silence_clock() -> None:
    import time as _time

    state = SourceState("sys", active=True, opened_at=1000.0)
    state.last_signal_at = _time.time()
    assert state.silent_for() < 1.0
