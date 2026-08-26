"""Platform-selected audio capture backends.

The concrete backends are imported lazily inside get_backend so that importing
ambientqa never requires pyaudiowpatch off-Windows, sounddevice off-macOS, or
PipeWire tools off-Linux.
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
        if sys.platform == "win32":
            choice = "wasapi"
        elif sys.platform == "darwin":
            choice = "coreaudio"
        else:
            # Preserve the existing Linux/default path for all other POSIX
            # hosts rather than changing a working PipeWire installation.
            choice = "pipewire"
    if choice == "wasapi":
        from .windows import WasapiBackend

        return WasapiBackend(frame_ms=audio_config.frame_ms)
    if choice == "pipewire":
        from .linux import PipewireBackend

        return PipewireBackend(latency_ms=audio_config.frame_ms)
    if choice == "coreaudio":
        from .macos import CoreAudioBackend

        return CoreAudioBackend(frame_ms=audio_config.frame_ms)
    # validate_config rejects this earlier; kept for direct callers.
    raise ValueError(
        'audio.backend must be "auto", "wasapi", "pipewire", or '
        f'"coreaudio"; got {choice!r}'
    )
