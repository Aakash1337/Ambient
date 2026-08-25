"""Guarantee one app pipeline and report instances via heartbeat files.

The per-user OS lock is the correctness mechanism: it is held for the process
lifetime and released by the kernel after a crash or SIGKILL. Heartbeats are
the status/legacy layer: every instance touches its own file each UI tick, and
stale dead markers are pruned by whichever instance sees them next.
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Callable, TextIO

# Must comfortably outlive the gap between status ticks (0.5s) plus any
# scheduling hiccup, while staying short enough that a killed instance
# disappears from the count in seconds.
HEARTBEAT_TTL_S = 5.0


def _default_root() -> Path:
    # Per-user: /tmp is shared, and another user's heartbeat files would be
    # counted but never prunable.
    if hasattr(os, "getuid"):
        suffix = str(os.getuid())
    else:  # pragma: no cover - Windows
        suffix = os.environ.get("USERNAME", "shared")
    return Path(tempfile.gettempdir()) / f"ambientqa-instances-{suffix}"


def _default_lock_path() -> Path:
    if hasattr(os, "getuid"):
        suffix = str(os.getuid())
    else:  # pragma: no cover - Windows
        suffix = os.environ.get("USERNAME", "shared")
    return Path(tempfile.gettempdir()) / f"ambientqa-app-{suffix}.lock"


def _ambientqa_pid_alive(value: str) -> bool:
    # Windows does not implement signal 0 as a harmless existence probe;
    # CPython routes os.kill through TerminateProcess there. Fresh heartbeats
    # plus the mandatory byte-range lock are authoritative on that platform.
    if os.name == "nt":  # pragma: no cover - Windows
        return False
    try:
        pid = int(value)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    # A stale marker can outlive its process and that PID can be reused by an
    # unrelated program. Preserve it only when Linux confirms the expected
    # module command; the lifetime lock is authoritative for current builds.
    try:
        arguments = (
            (Path("/proc") / str(pid) / "cmdline")
            .read_bytes()
            .split(b"\0")
        )
    except OSError:
        return False
    return any(
        argument == b"-m"
        and index + 1 < len(arguments)
        and arguments[index + 1] == b"ambientqa"
        for index, argument in enumerate(arguments)
    )


class InstanceRegistry:
    def __init__(
        self,
        root: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        lock_path: str | Path | None = None,
    ) -> None:
        custom_root = Path(root) if root is not None else None
        self.root = custom_root if custom_root is not None else _default_root()
        self._clock = clock
        self._own = self.root / str(os.getpid())
        self.lock_path = (
            Path(lock_path)
            if lock_path is not None
            else (
                custom_root.with_name(custom_root.name + ".lock")
                if custom_root is not None
                else _default_lock_path()
            )
        )
        self._lock_handle: TextIO | None = None

    def claim_exclusive(self) -> bool:
        """Hold the per-user app lock for this registry's entire lifetime.

        Heartbeats make useful status records, but they cannot guarantee
        exclusivity while model loading or a stalled UI delays their refresh.
        The OS releases this advisory lock even after a crash or SIGKILL.
        """
        if self._lock_handle is not None:
            return True
        handle: TextIO | None = None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+", encoding="utf-8")
            if os.name == "nt":  # pragma: no cover - Windows
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write("\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            if handle is not None:
                with suppress(OSError):
                    handle.close()
            return False
        self._lock_handle = handle
        return True

    def heartbeat_and_count(self) -> int:
        """Refresh this instance's heartbeat and count live instances.

        Every failure path returns at least 1: this instance is certainly
        running, and a broken registry must never corrupt the status bar.
        """
        now = self._clock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._own.touch()
            os.utime(self._own, (now, now))
        except OSError:
            return 1
        count = 0
        try:
            for entry in self.root.iterdir():
                try:
                    if (
                        now - entry.stat().st_mtime <= HEARTBEAT_TTL_S
                        or _ambientqa_pid_alive(entry.name)
                    ):
                        count += 1
                    else:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            return 1
        return max(1, count)

    def close(self) -> None:
        with suppress(OSError):
            self._own.unlink(missing_ok=True)
        handle = self._lock_handle
        self._lock_handle = None
        if handle is not None:
            if os.name == "nt":  # pragma: no cover - Windows
                with suppress(OSError):
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                with suppress(OSError):
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            with suppress(OSError):
                handle.close()
