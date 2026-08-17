"""Session JSONL logging."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class SessionLogger:
    def __init__(self, log_dir: str | Path = "logs") -> None:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"session-{stamp}.jsonl"
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")

