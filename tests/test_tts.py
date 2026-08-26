from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time

import numpy as np
import pytest

from ambientqa import tts as tts_module
from ambientqa.bus import AudioFrame, DropOldestQueue, Utterance
from ambientqa.config import AudioConfig, TtsConfig, default_config, validate_config
from ambientqa.segmenter import UtteranceSegmenter, segment_worker
from ambientqa.tts import (
    SpeakWindows,
    SpeechOutput,
    _CoreAudioPlayer,
    _SpeakJob,
    speakable,
    voice_followup_intent,
)


class AmplitudeVAD:
    def __call__(self, audio: np.ndarray) -> float:
        return float(np.max(np.abs(audio)) > 0.5)


def frame(channel: str, speech: bool, number: int, frame_ms: int = 25) -> AudioFrame:
    samples = int(16000 * frame_ms / 1000)
    audio = (
        np.ones(samples, dtype=np.float32)
        if speech
        else np.zeros(samples, dtype=np.float32)
    )
    return AudioFrame(channel, audio, number * frame_ms / 1000)


# --- speakable -------------------------------------------------------------

CUE_ANSWER = (
    "Use a bounded queue and drop the oldest item.\n"
    "• backpressure\n"
    "• producer never blocks\n"
    "```python\nqueue.put_nowait(item)\n```"
)


def test_speakable_first_line_takes_only_the_opening_line() -> None:
    assert (
        speakable(CUE_ANSWER, "first_line")
        == "Use a bounded queue and drop the oldest item."
    )


def test_speakable_full_drops_code_and_terminates_fragments() -> None:
    spoken = speakable(CUE_ANSWER, "full")
    assert "put_nowait" not in spoken
    assert "```" not in spoken
    assert "backpressure." in spoken
    assert "producer never blocks." in spoken


def test_speakable_flattens_markdown() -> None:
    spoken = speakable(
        "**Bold** and *italic* with `code` and [a link](http://x)", "full"
    )
    assert spoken == "Bold and italic with code and a link."


def test_speakable_code_only_answer_is_empty() -> None:
    assert speakable("```python\nx = 1\n```", "full") == ""


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Continue reading the answer.", "continue"),
        ("Please read the rest of the bullets.", "continue"),
        ("Repeat that.", "repeat"),
        ("Say the answer again.", "repeat"),
        ("Can you repeat what you just said?", "repeat"),
        ("Could you please repeat what you said?", "repeat"),
        ("Would you repeat your previous answer again?", "repeat"),
        ("Weren't you going to continue reading out the whole answer?", "continue"),
        ("Can you read the rest?", "continue"),
        ("Could you please continue reading?", "continue"),
        ("Would you read all of the options?", "continue"),
        # Exact live ASR corruption of "weren't you going to continue...?"
        ("I'm not going to continue reading out the whole answer.", "continue"),
        ("We'll continue reading the contract tomorrow.", None),
        ("Why did you continue reading the answer?", None),
        ("Can you continue reading the contract?", None),
        ("Can you not continue reading the answer?", None),
        ("Don't continue reading the answer.", None),
        ("I don't want you to read the rest.", None),
        ("I was going to continue reading the answer myself.", None),
        ("Continue.", None),
        ("The answer has three options.", None),
        ("Can you repeat what I just said?", None),
        ("Can you repeat what Alex just said?", None),
    ],
)
def test_voice_followup_intent_is_narrow(text: str, intent: str | None) -> None:
    assert voice_followup_intent(text) == intent


# --- SpeakWindows ----------------------------------------------------------


def test_window_mutes_listed_channels_until_deadline(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    assert not windows.muted("sys")
    windows.publish(now[0] + 2.0, ["sys"])
    assert windows.muted("sys")
    assert not windows.muted("mic")
    now[0] += 3.0
    assert not windows.muted("sys")


def test_window_uses_capture_time_not_later_dequeue_time(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    windows.publish(1002.0, ["sys"], since=1000.0)

    assert not windows.muted("sys", now=999.9)
    assert windows.muted("sys", now=1001.0)
    now[0] = 1003.0
    # Playback has ended by dequeue time, but a buffered frame captured while
    # it was audible must still be discarded.
    assert windows.muted("sys", now=1001.0)
    assert not windows.muted("sys", now=1002.1)


def test_window_refresh_preserves_the_original_audible_interval(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    windows.publish(1005.0, ["sys"], since=1000.0)
    now[0] = 1001.0
    windows.publish(1006.0, ["sys"], since=1000.0)

    assert windows.muted("sys", now=1000.5)
    assert windows.muted("sys", now=1005.5)


def test_window_from_another_instance_mutes_this_one(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    foreign = tmp_path / "99999999.json"
    foreign.write_text(
        json.dumps({"pid": 99999999, "until": 1002.0, "channels": ["mic", "sys"]})
    )
    assert windows.muted("mic")
    assert windows.foreign_speaker() == 99999999


def test_stale_mtime_window_is_pruned_even_with_future_deadline(tmp_path) -> None:
    """A SIGKILLed speaker must unmute everyone within the TTL, no matter how
    long its published window claimed to run."""
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    foreign = tmp_path / "424242.json"
    foreign.write_text(
        json.dumps({"pid": 424242, "until": 9999.0, "channels": ["sys"]})
    )
    os.utime(foreign, (990.0, 990.0))
    assert not windows.muted("sys")
    assert not foreign.exists()


def test_malformed_and_vanished_windows_fail_open(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    (tmp_path / "1234.json").write_text("{not json")
    assert not windows.muted("sys")
    assert windows.foreign_speaker() is None


@pytest.mark.parametrize(
    "payload",
    [
        {"pid": 1234, "until": "later", "channels": ["sys"]},
        {"pid": 1234, "until": 1002.0, "channels": None},
        {"pid": 1234, "until": 1002.0, "channels": "sys"},
        {"pid": "1234", "until": 1002.0, "channels": ["sys"]},
        {
            "pid": 1234,
            "since": float("nan"),
            "until": 1002.0,
            "channels": ["sys"],
        },
        {"pid": 1234, "until": float("nan"), "channels": ["sys"]},
    ],
)
def test_malformed_window_shapes_fail_open(tmp_path, payload) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    (tmp_path / "1234.json").write_text(json.dumps(payload))
    assert not windows.muted("sys")
    assert windows.foreign_speaker() is None


def test_speaker_claim_participates_in_election_without_muting(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    (tmp_path / "99999999.claim.json").write_text(
        json.dumps({"pid": 99999999, "until": 1002.0, "channels": []})
    )
    assert not windows.muted("mic")
    assert not windows.muted("sys")
    assert windows.foreign_speaker() == 99999999


def test_clear_removes_own_window(tmp_path) -> None:
    now = [1000.0]
    windows = SpeakWindows(tmp_path, clock=lambda: now[0], scan_interval_s=0.0)
    windows.publish(now[0] + 5.0, ["sys"])
    assert windows.muted("sys")
    windows.clear()
    assert not windows.muted("sys")


def test_missing_root_directory_fails_open(tmp_path) -> None:
    windows = SpeakWindows(tmp_path / "never-created", scan_interval_s=0.0)
    assert not windows.muted("sys")


# --- segmenter muting ------------------------------------------------------


def test_discard_abandons_half_built_utterance_but_not_the_channel() -> None:
    config = AudioConfig()
    segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
    for index in range(16):
        assert segmenter.process(frame("sys", True, index)) is None
    segmenter.discard("sys")
    # The buffered speech must not flush as an utterance once silence follows.
    output = None
    for index in range(16, 56):
        output = segmenter.process(frame("sys", False, index))
    assert output is None
    # The channel keeps working afterwards.
    for index in range(56, 76):
        assert segmenter.process(frame("sys", True, index)) is None
    emitted = [
        segmenter.process(frame("sys", False, index)) for index in range(76, 116)
    ]
    assert any(utterance is not None for utterance in emitted)


def test_segment_worker_drops_frames_while_muted() -> None:
    async def scenario() -> list[Utterance]:
        config = AudioConfig()
        segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
        frames: DropOldestQueue[AudioFrame] = DropOldestQueue(256)
        utterances: DropOldestQueue[Utterance] = DropOldestQueue(16)
        stop = asyncio.Event()
        muted = {"active": True}
        worker = asyncio.create_task(
            segment_worker(
                frames,
                utterances,
                segmenter,
                stop,
                mute=lambda channel, timestamp: muted["active"],
            )
        )
        # Speech while muted, then silence after unmuting: nothing may emit.
        for index in range(16):
            frames.put_drop_oldest(frame("sys", True, index))
        await frames.join()
        muted["active"] = False
        for index in range(16, 56):
            frames.put_drop_oldest(frame("sys", False, index))
        await frames.join()
        # Control: normal speech now segments as usual.
        for index in range(56, 76):
            frames.put_drop_oldest(frame("sys", True, index))
        for index in range(76, 116):
            frames.put_drop_oldest(frame("sys", False, index))
        await frames.join()
        stop.set()
        await worker
        return utterances.drain()

    emitted = asyncio.run(scenario())
    assert len(emitted) == 1
    assert emitted[0].started_at >= 56 * 0.025 - 0.301


# --- SpeechOutput ----------------------------------------------------------


class FakeEngine:
    sample_rate = 16000

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        # 0.05s of audio keeps the playback loop to a single poll cycle.
        return b"\x00\x01" * 800


class FakeProc:
    def __init__(self, exit_code: int = 0) -> None:
        self.fed = b""
        self.killed = False
        self.exit_code = exit_code
        self._stdin_open = True
        self.stdin = self

    # File-object face used by SpeechOutput._feed.
    def write(self, data: bytes) -> None:
        self.fed += data

    def close(self) -> None:
        self._stdin_open = False

    # Popen face.
    def poll(self) -> int | None:
        return None if self._stdin_open else self.exit_code

    def terminate(self) -> None:
        self.killed = True
        self._stdin_open = False

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self.exit_code


class BlockingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        self.entered.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("test did not release synthesis")
        return b"\x00\x01" * 800


class EmptyEngine(FakeEngine):
    def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        return b""


class HungProc:
    def __init__(self) -> None:
        self.fed = b""
        self.stdin = self
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def write(self, data: bytes) -> None:
        self.fed += data

    def close(self) -> None:
        # Simulate a player that consumed stdin but never exits.
        pass

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("paplay", timeout)
        return self.returncode


class _RawOutput:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.aborted = False
        self.closed = False
        self.written = b""

    def start(self) -> None:
        self.started = True

    def write(self, data: bytes) -> bool:
        self.written += data
        return False

    def stop(self) -> None:
        self.stopped = True

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


class _OutputSoundDevice:
    def __init__(self) -> None:
        self.streams: list[_RawOutput] = []

    def RawOutputStream(self, **kwargs) -> _RawOutput:
        stream = _RawOutput(**kwargs)
        self.streams.append(stream)
        return stream


def test_coreaudio_player_exposes_the_existing_process_contract() -> None:
    sounddevice = _OutputSoundDevice()
    player = _CoreAudioPlayer(24000, sounddevice)
    raw = sounddevice.streams[0]
    assert raw.started
    assert raw.kwargs == {"samplerate": 24000, "channels": 1, "dtype": "int16"}
    assert player.stdin is not None
    player.stdin.write(b"\x00\x01" * 20)
    player.stdin.close()
    assert raw.written == b"\x00\x01" * 20
    assert raw.stopped and raw.closed
    assert player.poll() == 0
    assert player.wait(timeout=0.1) == 0


def test_coreaudio_player_abort_matches_subprocess_termination() -> None:
    sounddevice = _OutputSoundDevice()
    player = _CoreAudioPlayer(24000, sounddevice)
    player.terminate()
    assert sounddevice.streams[0].aborted
    assert sounddevice.streams[0].closed
    assert player.wait(timeout=0.1) == -15


def test_speech_output_defaults_to_coreaudio_player_on_macos(
    tmp_path, monkeypatch
) -> None:
    created: list[int] = []
    sentinel = object()

    def coreaudio_player(sample_rate: int):
        created.append(sample_rate)
        return sentinel

    monkeypatch.setattr(tts_module.sys, "platform", "darwin")
    monkeypatch.setattr(tts_module, "_CoreAudioPlayer", coreaudio_player)
    speech = SpeechOutput(
        TtsConfig(),
        FakeEngine(),
        SpeakWindows(tmp_path, scan_interval_s=0.0),
        report=lambda _message: None,
    )

    assert speech._player_name == "CoreAudio"
    assert speech._spawn_player(24000) is sentinel
    assert created == [24000]


def make_speech(
    tmp_path,
    *,
    reports: list[str] | None = None,
    player_factory=None,
    engine: FakeEngine | None = None,
    **overrides,
) -> tuple[SpeechOutput, FakeEngine, list[FakeProc]]:
    config = TtsConfig(**overrides)
    engine = engine or FakeEngine()
    windows = SpeakWindows(tmp_path, scan_interval_s=0.0)
    procs: list[FakeProc] = []

    def spawn(sample_rate: int) -> FakeProc:
        proc = player_factory(sample_rate) if player_factory else FakeProc()
        procs.append(proc)
        return proc

    speech = SpeechOutput(
        config,
        engine,
        windows,
        report=(reports if reports is not None else []).append,
        spawn_player=spawn,
    )
    return speech, engine, procs


def test_speak_plays_pcm_and_holds_the_tail_window(tmp_path) -> None:
    speech, engine, procs = make_speech(tmp_path)
    job = _SpeakJob("q1", "hello there", time.time())
    asyncio.run(speech._speak(job))
    assert engine.spoken == ["hello there"]
    assert len(procs) == 1
    assert procs[0].fed == b"\x00\x01" * 800
    # The tail window survives playback: capture stays muted while the room
    # is still ringing.
    assert speech.windows.muted("mic")
    assert speech.windows.muted("sys")


def test_muted_speech_never_enqueues_or_plays(tmp_path) -> None:
    speech, engine, procs = make_speech(tmp_path)
    speech.muted = True
    speech.enqueue("q1", "hello")
    assert speech.queue.qsize() == 0


def test_stale_job_is_skipped(tmp_path) -> None:
    speech, engine, procs = make_speech(tmp_path, max_age_s=1.0)
    job = _SpeakJob("q1", "hello", time.time() - 5.0)
    asyncio.run(speech._speak(job))
    assert engine.spoken == []
    assert procs == []


def test_empty_synthesis_is_reported_instead_of_failing_silently(tmp_path) -> None:
    reports: list[str] = []
    speech, _, procs = make_speech(
        tmp_path,
        reports=reports,
        engine=EmptyEngine(),
    )

    asyncio.run(speech._speak(_SpeakJob("q1", "hello", time.time())))

    assert procs == []
    assert any("synthesis returned no audio" in message for message in reports)


def test_foreign_speaker_takes_priority(tmp_path) -> None:
    speech, engine, procs = make_speech(tmp_path)
    foreign = tmp_path / "1.json"
    foreign.write_text(
        json.dumps({"pid": 1, "until": time.time() + 30.0, "channels": ["sys"]})
    )
    asyncio.run(speech._speak(_SpeakJob("q1", "hello", time.time())))
    assert engine.spoken == []
    assert procs == []


def test_foreign_claim_prevents_synthesis_without_muting_capture(tmp_path) -> None:
    speech, engine, procs = make_speech(tmp_path)
    foreign = tmp_path / "1.claim.json"
    foreign.write_text(
        json.dumps({"pid": 1, "until": time.time() + 30.0, "channels": []})
    )
    assert not speech.windows.muted("sys")
    asyncio.run(speech._speak(_SpeakJob("q1", "hello", time.time())))
    assert engine.spoken == []
    assert procs == []


def test_muting_during_synthesis_vetoes_playback_and_claim_does_not_mute(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine = BlockingEngine()
        speech, _, procs = make_speech(tmp_path, engine=engine)
        task = asyncio.create_task(
            speech._speak(_SpeakJob("q1", "hello", time.time()))
        )
        while not engine.entered.is_set():
            await asyncio.sleep(0.001)
        # The election claim exists before synthesis but is not a capture gate.
        assert list(tmp_path.glob("*.claim.json"))
        assert not speech.windows.muted("mic")
        assert not speech.windows.muted("sys")
        speech.muted = True
        engine.release.set()
        await task
        assert procs == []
        assert not list(tmp_path.glob("*.claim.json"))

    asyncio.run(scenario())


def test_stop_during_synthesis_vetoes_playback(tmp_path) -> None:
    async def scenario() -> None:
        engine = BlockingEngine()
        speech, _, procs = make_speech(tmp_path, engine=engine)
        task = asyncio.create_task(
            speech._speak(_SpeakJob("q1", "hello", time.time()))
        )
        while not engine.entered.is_set():
            await asyncio.sleep(0.001)
        speech.stop_current()
        engine.release.set()
        await task
        assert procs == []
        assert not list(tmp_path.glob("*.claim.json"))

    asyncio.run(scenario())


def test_cancelled_synthesis_never_holds_asyncio_shutdown(tmp_path) -> None:
    engine = BlockingEngine()

    async def scenario() -> None:
        speech, _, _ = make_speech(tmp_path, engine=engine)
        task = asyncio.create_task(
            speech._speak(_SpeakJob("q1", "hello", time.time()))
        )
        while not engine.entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(scenario())
    elapsed = time.monotonic() - started
    # The engine thread is deliberately still blocked here. asyncio.run()
    # must not wait for it as it would for asyncio.to_thread/default executor.
    assert elapsed < 0.5
    engine.release.set()


@pytest.mark.parametrize("cancel_with", ["mute", "stop"])
def test_cancel_during_election_prevents_synthesis_and_playback(
    tmp_path, cancel_with
) -> None:
    async def scenario() -> None:
        speech, engine, procs = make_speech(tmp_path)
        task = asyncio.create_task(
            speech._speak(_SpeakJob("q1", "hello", time.time()))
        )
        while not list(tmp_path.glob("*.claim.json")):
            await asyncio.sleep(0.001)
        if cancel_with == "mute":
            speech.muted = True
        else:
            speech.stop_current()
        await task
        assert engine.spoken == []
        assert procs == []
        assert not list(tmp_path.glob("*.claim.json"))

    asyncio.run(scenario())


def test_hung_player_times_out_from_audio_duration_and_reports(
    tmp_path, monkeypatch
) -> None:
    reports: list[str] = []
    hung = HungProc()
    monkeypatch.setattr(tts_module, "_PLAYER_TIMEOUT_SLACK_S", 0.01)
    monkeypatch.setattr(tts_module, "_PLAYER_POLL_S", 0.005)
    speech, _, procs = make_speech(
        tmp_path,
        reports=reports,
        player_factory=lambda sample_rate: hung,
    )
    started = time.monotonic()
    asyncio.run(speech._speak(_SpeakJob("q1", "hello", time.time())))
    elapsed = time.monotonic() - started
    # FakeEngine emits 0.05 s of PCM, so its deadline is 0.05 + 0.01 s.
    assert elapsed < 0.5
    assert procs == [hung]
    assert hung.terminated
    assert any("playback timed out after 0.06s" in message for message in reports)
    assert not speech.speaking
    assert speech._current is None
    window = json.loads(next(tmp_path.glob("[0-9]*.json")).read_text())
    assert window["until"] <= time.time() + speech.config.gate_tail_s + 0.1


def test_nonzero_player_exit_is_reported(tmp_path) -> None:
    reports: list[str] = []
    speech, _, _ = make_speech(
        tmp_path,
        reports=reports,
        player_factory=lambda sample_rate: FakeProc(exit_code=7),
    )
    asyncio.run(speech._speak(_SpeakJob("q1", "hello", time.time())))
    assert any("paplay exited with status 7" in message for message in reports)


def test_player_start_runtime_error_is_reported_and_fails_open(tmp_path) -> None:
    reports: list[str] = []

    def fail_to_start(sample_rate: int):
        raise RuntimeError("audio server unavailable")

    speech, _, procs = make_speech(
        tmp_path, reports=reports, player_factory=fail_to_start
    )
    asyncio.run(speech._speak(_SpeakJob("q1", "hello", time.time())))
    assert procs == []
    assert any("audio server unavailable" in message for message in reports)
    assert not speech.windows.muted("mic")
    assert not speech.windows.muted("sys")


def test_queue_drops_oldest_on_burst(tmp_path) -> None:
    speech, engine, procs = make_speech(tmp_path, queue_size=2)
    speech.enqueue("q1", "one")
    speech.enqueue("q2", "two")
    speech.enqueue("q3", "three")
    texts = [job.text for job in speech.queue.drain()]
    assert texts == ["two", "three"]


def test_worker_speaks_from_queue_and_stops(tmp_path) -> None:
    async def scenario() -> None:
        speech, engine, procs = make_speech(tmp_path)
        stop = asyncio.Event()
        worker = asyncio.create_task(speech.worker(stop))
        speech.enqueue("q1", "spoken from the queue")
        await speech.queue.join()
        stop.set()
        await worker
        assert engine.spoken == ["spoken from the queue"]

    asyncio.run(scenario())


# --- config ----------------------------------------------------------------


def test_tts_defaults_are_valid() -> None:
    config = validate_config(default_config())
    assert config.tts.speak_channels == ["mic"]
    assert config.tts.mute_channels == ["mic", "sys"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"engine": "festival"},
        {"speak": "everything"},
        {"speak_channels": ["radio"]},
        {"mute_channels": ["radio"]},
        {"queue_size": 0},
        {"gate_tail_s": 9.0},
        {"max_age_s": 0.0},
        {"speed": 5.0},
    ],
)
def test_tts_validation_rejects_bad_values(overrides) -> None:
    config = default_config()
    for key, value in overrides.items():
        setattr(config.tts, key, value)
    with pytest.raises(ValueError):
        validate_config(config)
