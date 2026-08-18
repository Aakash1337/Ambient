"""Platform-selected audio capture backends.

The concrete backends are imported lazily inside get_backend so that importing
ambientqa never requires pyaudiowpatch off-Windows (or pactl on Windows).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .base import AudioBackend, BackendSession, CaptureDevice, SourceStream

if TYPE_CHECKING:
    from ..config import AudioConfig

__all__ = [
    "AudioBackend",
    "BackendSession",
    "CaptureDevice",
    "SourceStream",
    "get_backend",
]


def get_backend(audio_config: "AudioConfig") -> AudioBackend:
    choice = audio_config.backend
    if choice == "auto":
        choice = "wasapi" if sys.platform == "win32" else "pipewire"
    if choice == "wasapi":
        from .windows import WasapiBackend

        return WasapiBackend(frame_ms=audio_config.frame_ms)
    if choice == "pipewire":
        from .linux import PipewireBackend

        return PipewireBackend(latency_ms=audio_config.frame_ms)
    # validate_config rejects this earlier; kept for direct callers.
    raise ValueError(f'audio.backend must be "auto", "wasapi", or "pipewire"; got {choice!r}')
