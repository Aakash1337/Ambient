"""Platform-neutral capture contract every audio backend implements.

The pipeline in audio.py does not care where samples come from -- only that it
can enumerate devices, open a blocking float32 stream per device, and unblock a
reader from another thread. Everything platform-specific (pyaudiowpatch/WASAPI
on Windows, pactl/parec on Linux, sounddevice/CoreAudio on macOS) lives behind
these three protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol

import numpy as np

DeviceKind = Literal["mic", "loopback"]


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    # Backend-stable identifier: the stringified PyAudio/CoreAudio device index
    # on Windows/macOS, the PipeWire source name on Linux. Opaque to everything
    # above the backend; only `name` is ever shown or written to config.
    id: str
    name: str
    kind: DeviceKind
    channels: int
    # Native rate, informational. What read() actually delivers is described by
    # the opened SourceStream, which may differ (parec resamples in-process).
    sample_rate: int

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.id

    @property
    def display_name(self) -> str:
        if self.kind == "mic" and "nvidia broadcast" in self.name.casefold():
            return f"{self.name} (NVIDIA Broadcast - noise removal)"
        return self.name


class SourceStream(Protocol):
    """One open capture stream. `rate`/`channels` describe what read() returns,
    not the device's native format."""

    rate: int
    channels: int

    def read(self, frames: int) -> np.ndarray:
        """Block until `frames` interleaved float32 frames arrive.

        Raises on device failure or end-of-stream -- that is how a dead device
        surfaces to the per-candidate fallback in the capture orchestrator.
        """
        ...

    def stop(self) -> None:
        """Unblock any read() blocked in another thread. Idempotent, callable
        from any thread. Must run BEFORE close(): closing under a live reader
        is the use-after-free class of crash this contract exists to prevent."""
        ...

    def close(self) -> None:
        """Release resources. Called after stop() and after readers have
        exited (or been given a bounded chance to)."""
        ...


class BackendSession(Protocol):
    """Holds whatever per-run resource the platform needs (a PyAudio instance
    on Windows; lightweight factories on Linux/macOS). Streams opened through
    it must be closed before the session is."""

    def mic_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        """Microphones to try opening, best first. Empty substring: the default
        device first, then fallbacks tried in order until one opens. A pinned
        substring that matches nothing raises -- guessing a microphone records
        the wrong room."""
        ...

    def loopback_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        """System-audio endpoints to open. Empty substring: the default
        output's endpoint first, then ALL the rest -- the arbiter needs every
        endpoint because a call can play through any of them. A pinned
        substring returns its single match, or warns and falls back to the
        default rather than silently going mic-only."""
        ...

    def open(self, device: CaptureDevice) -> SourceStream: ...

    def close(self) -> None: ...


class AudioBackend(Protocol):
    name: str
    has_system_audio: bool

    def list_devices(self) -> list[CaptureDevice]:
        """Microphones followed by loopback endpoints; powers the pickers."""
        ...

    def open_session(self) -> BackendSession: ...
