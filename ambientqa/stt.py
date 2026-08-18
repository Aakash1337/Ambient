"""Serial faster-whisper transcription with CUDA-to-CPU fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import site
import textwrap
import time
from pathlib import Path
from typing import Awaitable, Callable

from .bus import DropOldestQueue, Transcript, Utterance
from .config import STTConfig
from .profile import Profile

log = logging.getLogger(__name__)
REAL_CONTENT_RE = re.compile(r"[A-Za-z0-9]")


def register_cuda_dll_dirs() -> list[str]:
    """Put the pip-installed CUDA DLLs on the Windows DLL search path.

    `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` drop their DLLs in
    site-packages/nvidia/*/bin, which Python 3.8+ does NOT search for extension
    dependencies. Without this, CTranslate2 constructs the CUDA model happily and
    only fails at the first inference with "Library cublas64_12.dll is not found",
    long after any load-time fallback could react.
    """
    if os.name != "nt":
        return []
    added: list[str] = []
    for base in site.getsitepackages() + [site.getusersitepackages()]:
        root = Path(base) / "nvidia"
        if not root.is_dir():
            continue
        for bindir in sorted(root.glob("*/bin")):
            try:
                os.add_dll_directory(str(bindir))
            except OSError:  # pragma: no cover - directory vanished
                continue
            added.append(str(bindir))
    if added:
        # add_dll_directory alone is NOT enough: CTranslate2 resolves cuBLAS
        # dynamically at first inference rather than as a linked import, and that
        # path consults PATH only. Both mechanisms are needed.
        existing = os.environ.get("PATH", "")
        missing = [d for d in added if d.lower() not in existing.lower()]
        if missing:
            os.environ["PATH"] = os.pathsep.join([*missing, existing])
    return added


def normalise_phrase(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


class WhisperTranscriber:
    def __init__(
        self,
        config: STTConfig,
        status_callback: Callable[[str], None] | None = None,
        profile: Profile | None = None,
    ) -> None:
        self.config = config
        self.status_callback = status_callback or (lambda _message: None)
        self.model = None
        self.device = config.device
        self.profile = profile
        self._blocked = {
            normalise_phrase(item) for item in (config.hallucination_blocklist or [])
        }

    def set_profile(self, profile: Profile | None) -> None:
        self.profile = profile

    def _fall_back_to_cpu(self, reason: str) -> None:
        from faster_whisper import WhisperModel

        warning = (
            f"CUDA Whisper unavailable ({reason}); FALLING BACK TO CPU "
            f"{self.config.cpu_compute_type}. Transcription will be much slower."
        )
        log.warning(warning)
        self.status_callback(warning)
        self.model = WhisperModel(
            self.config.model, device="cpu", compute_type=self.config.cpu_compute_type
        )
        self.device = "cpu"

    def _load_model(self) -> None:
        if self.model is not None:
            return
        if self.config.device != "cpu":
            register_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        try:
            self.model = WhisperModel(
                self.config.model,
                device=self.config.device,
                compute_type=self.config.compute_type,
            )
            self.device = self.config.device
            self.status_callback(f"Whisper ready on {self.device}")
        except Exception as exc:
            warning = (
                f"CUDA Whisper initialization failed ({exc}); FALLING BACK TO CPU int8. "
                "Transcription will be much slower."
            )
            log.exception(warning)
            self.status_callback(warning)
            self.model = WhisperModel(
                self.config.model,
                device="cpu",
                compute_type=self.config.cpu_compute_type,
            )
            self.device = "cpu"

    def _transcribe_sync(self, utterance: Utterance) -> Transcript | None:
        self._load_model()
        started = time.perf_counter()
        kwargs = {
            "condition_on_previous_text": False,
            "vad_filter": False,
        }
        profile = self.profile
        if profile is not None:
            if profile.vocabulary:
                kwargs["hotwords"] = ", ".join(profile.vocabulary)
            if profile.topic:
                # A short, non-repetitive prompt gives Whisper domain context
                # without encouraging prompt-text hallucinations on silence.
                kwargs["initial_prompt"] = textwrap.shorten(
                    " ".join(profile.topic.split()),
                    width=120,
                    placeholder="…",
                )
        if self.config.language:
            kwargs["language"] = self.config.language
        try:
            segments, _info = self.model.transcribe(utterance.audio, **kwargs)
            # `segments` is a lazy generator: CUDA errors surface on consumption,
            # not on the call above, so materialise it inside the try block.
            segment_texts = [s.text.strip() for s in segments if s.text.strip()]
        except RuntimeError as exc:
            if self.device == "cpu":
                raise
            # A missing CUDA/cuDNN DLL only shows up at the first inference, well
            # after the model claimed to be ready. Recover instead of dying.
            self._fall_back_to_cpu(str(exc))
            segments, _info = self.model.transcribe(utterance.audio, **kwargs)
            segment_texts = [s.text.strip() for s in segments if s.text.strip()]
        # Keep Whisper punctuation exactly; only trim surrounding whitespace.
        text = " ".join(segment_texts).strip()
        if not text or not REAL_CONTENT_RE.search(text):
            return None
        normalised = normalise_phrase(text)
        # Exact match only. Whisper's silence hallucinations ("Thank you.",
        # "Thanks for watching!") are whole-utterance artifacts, so equality is
        # the right test. A prefix match would eat real speech: interviewers
        # routinely open with a courtesy -- "Thank you. So, tell me about your
        # experience with Kubernetes." -- and the entire question would vanish
        # here, before gating, with no log record at all.
        if normalised in self._blocked:
            return None
        return Transcript(
            channel=utterance.channel,
            text=text,
            timestamp=utterance.ended_at,
            utterance_id=utterance.id,
            latency_ms=(time.perf_counter() - started) * 1000,
            started_at=utterance.started_at,
        )

    async def transcribe(self, utterance: Utterance) -> Transcript | None:
        return await asyncio.to_thread(self._transcribe_sync, utterance)


async def stt_worker(
    utterances: DropOldestQueue[Utterance],
    transcripts: DropOldestQueue[Transcript],
    transcriber: WhisperTranscriber,
    stop: asyncio.Event,
    on_drop: Callable[[Transcript], Awaitable[None]] | None = None,
) -> None:
    """One serial worker owns the Whisper model/GPU."""
    while not stop.is_set():
        try:
            utterance = await asyncio.wait_for(utterances.get(), timeout=0.25)
        except asyncio.TimeoutError:
            continue
        try:
            transcript = await transcriber.transcribe(utterance)
            if transcript is not None:
                dropped = transcripts.put_drop_oldest(transcript)
                if dropped is not None and on_drop is not None:
                    await on_drop(dropped)
        except Exception:
            log.exception("Transcription failed")
            transcriber.status_callback("Whisper transcription failed; see log")
        finally:
            utterances.task_done()
