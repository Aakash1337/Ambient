"""Streaming, per-channel voice activity segmentation."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np
from numpy.typing import NDArray

from .bus import AudioFrame, DropOldestQueue, Utterance
from .config import AudioConfig

log = logging.getLogger(__name__)


class VoiceActivityDetector(Protocol):
    def __call__(self, audio: NDArray[np.float32]) -> float: ...


class SileroVAD:
    """Small ONNX Silero model kept on CPU."""

    def __init__(self) -> None:
        from silero_vad import load_silero_vad
        import torch

        self._torch = torch
        self._model = load_silero_vad(onnx=True)
        self._pending = np.empty(0, dtype=np.float32)
        self._last_probability = 0.0

    def __call__(self, audio: NDArray[np.float32]) -> float:
        # Silero's 16 kHz ONNX model requires exact 512-sample inference windows,
        # whereas capture deliberately emits 20–30 ms frames (320–480 samples).
        self._pending = np.concatenate(
            (self._pending, np.asarray(audio, dtype=np.float32).reshape(-1))
        )
        while len(self._pending) >= 512:
            window = self._pending[:512].copy()
            self._pending = self._pending[512:]
            tensor = self._torch.from_numpy(window)
            value = self._model(tensor, 16000)
            self._last_probability = float(
                value.item() if hasattr(value, "item") else value
            )
        return self._last_probability

    def reset(self) -> None:
        self._pending = np.empty(0, dtype=np.float32)
        self._last_probability = 0.0
        reset = getattr(self._model, "reset_states", None)
        if reset is not None:
            reset()


@dataclass(slots=True)
class _ChannelState:
    pre_roll: deque[NDArray[np.float32]]
    active: bool = False
    chunks: list[NDArray[np.float32]] = field(default_factory=list)
    started_at: float = 0.0
    silence_samples: int = 0
    speech_samples: int = 0


class UtteranceSegmenter:
    def __init__(
        self,
        config: AudioConfig,
        vad_factory: Callable[[], VoiceActivityDetector] = SileroVAD,
        speech_threshold: float = 0.5,
    ) -> None:
        self.config = config
        self.vad_factory = vad_factory
        self.speech_threshold = speech_threshold
        self._states: dict[str, _ChannelState] = {}
        self._vads: dict[str, VoiceActivityDetector] = {}
        # Probe the production VAD eagerly so missing/model-load failures can be
        # surfaced before capture starts. This instance becomes the first channel's.
        self._spare_vad: VoiceActivityDetector | None = self.vad_factory()
        self._pre_roll_samples = int(16000 * config.pre_roll_ms / 1000)
        self._silence_samples = int(16000 * config.silence_ms / 1000)
        self._minimum_samples = int(16000 * config.min_utterance_ms / 1000)
        self._maximum_samples = int(16000 * config.max_utterance_s)

    def _state(self, channel: str) -> _ChannelState:
        if channel not in self._states:
            frame_samples = max(1, int(16000 * self.config.frame_ms / 1000))
            frames = max(1, (self._pre_roll_samples + frame_samples - 1) // frame_samples)
            self._states[channel] = _ChannelState(deque(maxlen=frames))
            if self._spare_vad is not None:
                self._vads[channel] = self._spare_vad
                self._spare_vad = None
            else:
                self._vads[channel] = self.vad_factory()
        return self._states[channel]

    def _finish(self, channel: str, ended_at: float) -> Utterance | None:
        state = self._states[channel]
        if not state.chunks:
            state.active = False
            state.silence_samples = 0
            return None
        audio = np.concatenate(state.chunks).astype(np.float32, copy=False)
        utterance = None
        if state.speech_samples >= self._minimum_samples:
            utterance = Utterance(channel, audio, state.started_at, ended_at)
        state.active = False
        state.chunks = []
        state.silence_samples = 0
        state.speech_samples = 0
        state.pre_roll.clear()
        reset = getattr(self._vads[channel], "reset", None)
        if reset is not None:
            reset()
        return utterance

    def process(self, frame: AudioFrame) -> Utterance | None:
        audio = np.asarray(frame.audio, dtype=np.float32).reshape(-1)
        state = self._state(frame.channel)
        speech = self._vads[frame.channel](audio) >= self.speech_threshold
        frame_duration = len(audio) / 16000.0

        if not state.active:
            if not speech:
                state.pre_roll.append(audio.copy())
                return None
            pre = list(state.pre_roll)
            pre_samples = sum(len(chunk) for chunk in pre)
            state.active = True
            state.started_at = frame.timestamp - pre_samples / 16000.0
            state.chunks = pre + [audio.copy()]
            state.pre_roll.clear()
            state.silence_samples = 0
            state.speech_samples = len(audio)
        else:
            state.chunks.append(audio.copy())
            state.silence_samples = 0 if speech else state.silence_samples + len(audio)
            if speech:
                state.speech_samples += len(audio)

        total_samples = sum(len(chunk) for chunk in state.chunks)
        ended_at = frame.timestamp + frame_duration
        if total_samples >= self._maximum_samples:
            return self._finish(frame.channel, ended_at)
        if state.silence_samples >= self._silence_samples:
            return self._finish(frame.channel, ended_at)
        return None

    def flush(self, channel: str, timestamp: float) -> Utterance | None:
        if channel not in self._states or not self._states[channel].active:
            return None
        return self._finish(channel, timestamp)

    def reset_all(self) -> None:
        for channel, state in self._states.items():
            state.active = False
            state.chunks.clear()
            state.pre_roll.clear()
            state.silence_samples = 0
            state.speech_samples = 0
            reset = getattr(self._vads[channel], "reset", None)
            if reset is not None:
                reset()


async def segment_worker(
    frames: DropOldestQueue[AudioFrame],
    utterances: DropOldestQueue[Utterance],
    segmenter: UtteranceSegmenter,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            frame = await asyncio.wait_for(frames.get(), timeout=0.25)
        except asyncio.TimeoutError:
            continue
        try:
            utterance = segmenter.process(frame)
            if utterance is not None:
                utterances.put_drop_oldest(utterance)
        except Exception:
            log.exception("Segmenter failed for a %s frame", frame.channel)
        finally:
            frames.task_done()
