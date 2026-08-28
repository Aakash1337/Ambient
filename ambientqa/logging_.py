"""Session JSONL logging."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def prepare_log_directory(log_dir: str | Path) -> Path:
    """Create a private real directory, rejecting a final-component symlink."""
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or not directory.is_dir():
        raise OSError(f"Log path must be a real directory, not a symlink: {directory}")
    if os.name != "nt":
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(directory, flags)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    return directory


class PrivateFileHandler(logging.FileHandler):
    """A FileHandler whose final path cannot be a symlink on POSIX."""

    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        elif Path(self.baseFilename).is_symlink():  # pragma: no cover - Windows
            raise OSError(f"Log file must not be a symlink: {self.baseFilename}")
        descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            return os.fdopen(
                descriptor,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
            )
        except BaseException:
            os.close(descriptor)
            raise


class SessionLogger:
    def __init__(self, log_dir: str | Path = "logs") -> None:
        # Session transcripts are sensitive even though they are git-ignored.
        # Do not inherit a permissive umask or follow a pre-planted logs symlink.
        directory = prepare_log_directory(log_dir)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        started = datetime.now()
        for offset in range(60):
            stamp = (started + timedelta(seconds=offset)).strftime("%Y%m%d-%H%M%S")
            self.path = directory / f"session-{stamp}.jsonl"
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except FileExistsError:
                continue
            os.close(descriptor)
            break
        else:
            raise FileExistsError("Unable to allocate a unique session log name")
        if os.name != "nt":
            self.path.chmod(0o600)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=False, default=str)
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        with self._lock:
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
