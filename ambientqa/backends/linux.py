"""Native Linux capture backend: PipeWire sources via pactl and parec.

Monitor sources ("Monitor of <sink>") are PipeWire's equivalent of WASAPI
loopback: one exists per output device and carries whatever the machine plays
through it, which is how the other side of a call is heard. PipeWire
multiplexes every source natively, so streams here never conflict with other
applications -- or with a second copy of this app.

Capture is one `parec` subprocess per stream rather than PortAudio: parec
resamples and downmixes in-process to the pipeline's native 16kHz mono
float32, and a crashing capture process can never take the app down -- its
stdout simply hits EOF, which the reading thread surfaces as an error the
orchestrator already knows how to route around.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from contextlib import suppress
from typing import Any, Callable

import numpy as np

from .base import CaptureDevice, SourceStream

_MISSING_TOOLS = "PipeWire tools not found: install pipewire-pulse (pactl/parec)"

# pactl prints e.g. "s32le 2ch 48000Hz"; only the channel count and rate matter
# here (informational -- parec converts regardless of the native format).
_SAMPLE_SPEC_RE = re.compile(r"(\d+)ch (\d+)Hz")


def _run_command(argv: list[str]) -> str:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(_MISSING_TOOLS) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"{argv[0]} failed: {detail}")
    return completed.stdout


def _spawn_parec(device_id: str, latency_ms: int = 25) -> subprocess.Popen[bytes]:
    argv = [
        "parec",
        f"--device={device_id}",
        "--format=float32le",
        "--rate=16000",
        "--channels=1",
        "--raw",
        # Without an explicit latency parec accepts the server default, which
        # measured here as ~2s to the first byte and ~1s bursts thereafter --
        # useless for live segmentation. Requesting the pipeline's own frame
        # cadence delivers the first frame in ~50ms and one frame per read.
        f"--latency-msec={latency_ms}",
        "--client-name=ambientqa",
    ]
    # stderr goes to an anonymous temp file, never a pipe: nothing drains
    # stderr while audio streams, so a chatty parec (PULSE_LOG in the
    # environment, repeated client warnings) would fill a 64KiB pipe, block
    # writing, and silently stop producing audio -- a deaf channel with no
    # error. A file cannot backpressure the child, and the diagnostic tail
    # stays readable for the EOF message.
    stderr_file = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
        )
    except FileNotFoundError as exc:
        stderr_file.close()
        raise RuntimeError(_MISSING_TOOLS) from exc
    except Exception:
        stderr_file.close()
        raise
    # Popen leaves .stderr as None for non-PIPE targets; reattach the file so
    # ParecStream reads the diagnostics through the same seam fake processes
    # provide in tests.
    process.stderr = stderr_file
    return process


def _parse_sample_spec(spec: str) -> tuple[int, int]:
    match = _SAMPLE_SPEC_RE.search(spec)
    if match is None:
        # Stereo 48kHz is what virtually every PipeWire node runs at; a source
        # odd enough to omit its spec still lists and still opens (parec does
        # the conversion), so a guess here costs nothing.
        return 2, 48000
    return int(match.group(1)), int(match.group(2))


def _default_first(
    devices: list[CaptureDevice], default_id: str
) -> list[CaptureDevice]:
    preferred = [device for device in devices if device.id == default_id]
    rest = [device for device in devices if device.id != default_id]
    return preferred + rest


def _matches(device: CaptureDevice, substring: str) -> bool:
    # Match against BOTH the human description and the PipeWire name: a config
    # written on Windows pins the human label, while a Linux user may pin
    # either the label or the stable "alsa_input...." source name.
    wanted = substring.casefold()
    return wanted in device.name.casefold() or wanted in device.id.casefold()


class ParecStream:
    """SourceStream over one parec subprocess.

    parec delivers the pipeline's native format directly, so the orchestrator's
    resampler and downmix are naturally skipped for every Linux stream.
    """

    rate = 16000
    channels = 1

    def __init__(self, process: Any) -> None:
        self._process = process

    def read(self, frames: int) -> np.ndarray:
        needed = frames * 4  # float32le
        data = b""
        stdout = self._process.stdout
        while len(data) < needed:
            chunk = stdout.read(needed - len(data))
            if not chunk:
                raise RuntimeError(self._eof_message())
            data += chunk
        return np.frombuffer(data, dtype=np.float32)

    def _eof_message(self) -> str:
        # EOF means parec exited (invalid device, PipeWire restart, or our own
        # stop()); its stderr is the only place the reason exists.
        detail = ""
        stderr = self._process.stderr
        if stderr is not None:
            with suppress(Exception):
                # stderr is a temp file whose offset the child advanced; rewind
                # to the last few KB (the tail carries the fatal message).
                try:
                    stderr.seek(-4096, os.SEEK_END)
                except Exception:
                    with suppress(Exception):
                        stderr.seek(0)
                raw = stderr.read()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                detail = raw.strip()
        if detail:
            return f"parec stream ended: {detail.splitlines()[-1]}"
        return "parec stream ended"

    def stop(self) -> None:
        # Killing the child is what unblocks a reader: EOF on stdout returns
        # immediately, so shutdown is structurally instant and needs no
        # cooperation from a thread wedged in read().
        with suppress(ProcessLookupError, OSError):
            self._process.terminate()

    def close(self) -> None:
        try:
            self._process.wait(timeout=2)
        except Exception:
            with suppress(Exception):
                self._process.kill()
            with suppress(Exception):
                self._process.wait(timeout=1)
        for pipe in (self._process.stdout, self._process.stderr):
            if pipe is not None:
                with suppress(Exception):
                    pipe.close()


class PipewireSession:
    """No per-session OS resource exists on PipeWire; each stream owns its own
    parec child. The session object only satisfies the shared contract."""

    def __init__(self, backend: "PipewireBackend") -> None:
        self._backend = backend

    def mic_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        mics = [d for d in self._backend._sources() if d.kind == "mic"]
        if substring:
            matches = [d for d in mics if _matches(d, substring)]
            if not matches:
                raise RuntimeError(f"No input device name contains {substring!r}")
            return [matches[0]]
        return _default_first(mics, self._backend._default_source_id())

    def loopback_candidates(
        self,
        substring: str,
        on_warn: Callable[[str], None] | None = None,
    ) -> list[CaptureDevice]:
        monitors = [d for d in self._backend._sources() if d.kind == "loopback"]
        if not monitors:
            raise RuntimeError("No PipeWire monitor source is available")
        ordered = _default_first(monitors, self._backend._default_monitor_id())
        if not substring:
            # Every monitor, default sink's first: which output a call plays
            # through is not knowable ahead of time, and the arbiter forwards
            # only the one actually carrying speech.
            return ordered
        matches = [d for d in monitors if _matches(d, substring)]
        if matches:
            return [matches[0]]
        # A stale pinned name in config is the most common way this breaks.
        # Falling back to the current default beats raising, because raising
        # means mic-only, which silently loses the other half of the
        # conversation -- exactly the half worth answering.
        fallback = ordered[0]
        if on_warn is not None:
            on_warn(
                f"No loopback device matches {substring!r}; "
                f"falling back to {fallback.name!r}"
            )
        return [fallback]

    def open(self, device: CaptureDevice) -> SourceStream:
        return ParecStream(self._backend._process_factory(device.id))

    def close(self) -> None:
        pass


class PipewireBackend:
    name = "pipewire"
    has_system_audio = True

    def __init__(
        self,
        *,
        run_command: Callable[[list[str]], str] | None = None,
        process_factory: Callable[[str], Any] | None = None,
        latency_ms: int = 25,
    ) -> None:
        self._run = run_command or _run_command
        self._process_factory = process_factory or (
            lambda device_id: _spawn_parec(device_id, latency_ms)
        )

    def _sources(self) -> list[CaptureDevice]:
        raw = json.loads(self._run(["pactl", "--format=json", "list", "sources"]) or "[]")
        devices: list[CaptureDevice] = []
        for source in raw:
            source_name = str(source.get("name", ""))
            if not source_name:
                continue
            properties = source.get("properties") or {}
            is_monitor = properties.get("device.class") == "monitor"
            channels, rate = _parse_sample_spec(
                str(source.get("sample_specification", ""))
            )
            devices.append(
                CaptureDevice(
                    id=source_name,
                    name=str(source.get("description") or source_name),
                    kind="loopback" if is_monitor else "mic",
                    channels=channels,
                    sample_rate=rate,
                )
            )
        return devices

    def _default_source_id(self) -> str:
        return self._run(["pactl", "get-default-source"]).strip()

    def _default_monitor_id(self) -> str:
        sink = self._run(["pactl", "get-default-sink"]).strip()
        return f"{sink}.monitor" if sink else ""

    def list_devices(self) -> list[CaptureDevice]:
        devices = self._sources()
        return [d for d in devices if d.kind == "mic"] + [
            d for d in devices if d.kind == "loopback"
        ]

    def open_session(self) -> PipewireSession:
        return PipewireSession(self)
