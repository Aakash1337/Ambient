from __future__ import annotations

import numpy as np

from ambientqa.bus import AudioFrame
from ambientqa.config import AudioConfig
from ambientqa.segmenter import SileroVAD, UtteranceSegmenter


class AmplitudeVAD:
    def __call__(self, audio: np.ndarray) -> float:
        return float(np.max(np.abs(audio)) > 0.5)


def frame(channel: str, speech: bool, number: int, frame_ms: int = 25) -> AudioFrame:
    samples = int(16000 * frame_ms / 1000)
    audio = np.ones(samples, dtype=np.float32) if speech else np.zeros(samples, dtype=np.float32)
    return AudioFrame(channel, audio, number * frame_ms / 1000)


def test_emits_after_trailing_silence_with_pre_roll() -> None:
    config = AudioConfig()
    segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
    output = None
    # Twelve 25 ms frames fill the 300 ms pre-roll.
    for index in range(12):
        assert segmenter.process(frame("mic", False, index)) is None
    for index in range(12, 28):
        assert segmenter.process(frame("mic", True, index)) is None
    # 36 frames is exactly 900 ms trailing silence.
    for index in range(28, 64):
        output = segmenter.process(frame("mic", False, index))
    assert output is not None
    assert output.channel == "mic"
    assert len(output.audio) == 64 * 400
    assert output.started_at == 0


def test_discards_short_click_or_cough() -> None:
    config = AudioConfig(pre_roll_ms=0, silence_ms=100, min_utterance_ms=400)
    segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
    assert segmenter.process(frame("mic", True, 0)) is None
    output = None
    for index in range(1, 5):
        output = segmenter.process(frame("mic", False, index))
    assert output is None


def test_default_pre_roll_and_silence_do_not_make_a_click_long_enough() -> None:
    segmenter = UtteranceSegmenter(AudioConfig(), vad_factory=AmplitudeVAD)
    for index in range(12):
        segmenter.process(frame("mic", False, index))
    segmenter.process(frame("mic", True, 12))
    output = None
    for index in range(13, 41):
        output = segmenter.process(frame("mic", False, index))
    assert output is None


def test_force_flushes_maximum_length() -> None:
    config = AudioConfig(
        pre_roll_ms=0,
        silence_ms=700,
        min_utterance_ms=50,
        max_utterance_s=0.1,
    )
    segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
    output = None
    for index in range(4):
        output = segmenter.process(frame("mic", True, index))
    assert output is not None
    assert output.duration_s == 0.1


def test_channels_have_independent_state() -> None:
    config = AudioConfig(pre_roll_ms=0, silence_ms=50, min_utterance_ms=50)
    segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
    assert segmenter.process(frame("mic", True, 0)) is None
    assert segmenter.process(frame("sys", False, 0)) is None
    assert segmenter.process(frame("sys", True, 1)) is None
    assert segmenter.process(frame("mic", True, 1)) is None
    assert segmenter.process(frame("sys", True, 2)) is None
    assert segmenter.process(frame("mic", False, 2)) is None
    mic = segmenter.process(frame("mic", False, 3))
    assert mic is not None and mic.channel == "mic"
    assert segmenter.process(frame("sys", False, 3)) is None
    system = segmenter.process(frame("sys", False, 4))
    assert system is not None and system.channel == "sys"


def test_manual_flush_returns_active_utterance() -> None:
    config = AudioConfig(pre_roll_ms=0, min_utterance_ms=25)
    segmenter = UtteranceSegmenter(config, vad_factory=AmplitudeVAD)
    segmenter.process(frame("mic", True, 0))
    output = segmenter.flush("mic", 0.025)
    assert output is not None
    assert output.duration_s == 0.025


def test_silero_adapter_batches_capture_frames_into_512_samples() -> None:
    class FakeTorch:
        @staticmethod
        def from_numpy(value):
            return value

    class FakeModel:
        def __init__(self):
            self.window_lengths = []

        def __call__(self, value, sample_rate):
            self.window_lengths.append(len(value))
            assert sample_rate == 16000
            return 0.75

    vad = SileroVAD.__new__(SileroVAD)
    vad._torch = FakeTorch()
    vad._model = FakeModel()
    vad._pending = np.empty(0, dtype=np.float32)
    vad._last_probability = 0.0
    assert vad(np.ones(400, dtype=np.float32)) == 0.0
    assert vad(np.ones(400, dtype=np.float32)) == 0.75
    assert vad._model.window_lengths == [512]
    assert len(vad._pending) == 288
