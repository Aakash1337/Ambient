"""Count concurrently running app instances via heartbeat files.

Process scanning is the obvious approach and the wrong one: it needs
platform-specific tooling, and matching command lines catches the matcher
itself (a shell whose command line merely CONTAINS "python -m ambientqa"
counts as an instance). Heartbeats sidestep all of it: every instance touches
its own file each status tick, the counter counts files that are still fresh,
and a file gone stale -- a crash, a SIGKILL -- is pruned by whichever
instance sees it next.
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Callable

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


class InstanceRegistry:
    def __init__(
        self,
        root: str | Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root) if root is not None else _default_root()
        self._clock = clock
        self._own = self.root / str(os.getpid())

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
                    if now - entry.stat().st_mtime <= HEARTBEAT_TTL_S:
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
