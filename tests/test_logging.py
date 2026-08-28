from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from ambientqa.logging_ import (
    PrivateFileHandler,
    SessionLogger,
    prepare_log_directory,
)


def test_session_log_is_private_and_appendable(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    logger = SessionLogger(log_dir)
    logger.append({"transcript": "private conversation"})

    assert json.loads(logger.path.read_text(encoding="utf-8")) == {
        "transcript": "private conversation"
    }
    if os.name != "nt":
        assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(logger.path.stat().st_mode) == 0o600


def test_two_sessions_started_in_one_second_get_distinct_files(tmp_path: Path) -> None:
    first = SessionLogger(tmp_path / "logs")
    second = SessionLogger(tmp_path / "logs")

    assert first.path != second.path
    assert first.path.is_file()
    assert second.path.is_file()


def test_diagnostic_log_is_private_and_does_not_follow_symlink(tmp_path: Path) -> None:
    log_dir = prepare_log_directory(tmp_path / "logs")
    handler = PrivateFileHandler(log_dir / "ambientqa.log", encoding="utf-8")
    record = logging.LogRecord("ambientqa", logging.INFO, __file__, 1, "private", (), None)
    handler.emit(record)
    handler.close()

    diagnostic = log_dir / "ambientqa.log"
    assert "private" in diagnostic.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600

    outside = tmp_path / "outside.log"
    outside.write_text("untouched", encoding="utf-8")
    diagnostic.unlink()
    diagnostic.symlink_to(outside)
    with pytest.raises(OSError):
        PrivateFileHandler(diagnostic, encoding="utf-8")
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_log_directory_rejects_final_component_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "logs"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(OSError, match="real directory"):
        prepare_log_directory(linked)


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW unavailable")
def test_append_refuses_a_replaced_symlink(tmp_path: Path) -> None:
    logger = SessionLogger(tmp_path / "logs")
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched", encoding="utf-8")
    logger.path.unlink()
    logger.path.symlink_to(outside)

    with pytest.raises(OSError):
        logger.append({"transcript": "must not escape"})

    assert outside.read_text(encoding="utf-8") == "untouched"
