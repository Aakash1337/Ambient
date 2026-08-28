from __future__ import annotations

import hashlib
import errno
import shutil
from pathlib import Path

import pytest

from scripts.fetch_whisper_model import (
    MODEL_ID,
    MODEL_REVISION,
    TARGET_NAME,
    ModelVerificationError,
    ensure_model,
)


def _hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {
        name: hashlib.sha256(contents).hexdigest()
        for name, contents in files.items()
    }


def test_download_is_revision_pinned_verified_and_reused(tmp_path: Path) -> None:
    files = {
        "config.json": b"config",
        "model.bin": b"weights",
        "tokenizer.json": b"tokens",
    }
    calls: list[tuple[str, str]] = []

    def download(model_id: str, *, output_dir: str, revision: str) -> None:
        calls.append((model_id, revision))
        destination = Path(output_dir)
        for name, contents in files.items():
            (destination / name).write_bytes(contents)

    target = ensure_model(
        tmp_path / "models",
        downloader=download,
        expected=_hashes(files),
    )
    assert target.name == TARGET_NAME
    assert (target / ".revision").read_text(encoding="ascii").strip() == MODEL_REVISION
    assert calls == [(MODEL_ID, MODEL_REVISION)]
    assert not list((tmp_path / "models").glob(f".{TARGET_NAME}.*"))

    def should_not_download(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("verified snapshots must be reused")

    assert ensure_model(
        tmp_path / "models",
        downloader=should_not_download,
        expected=_hashes(files),
    ) == target


def test_corrupt_existing_snapshot_fails_closed(tmp_path: Path) -> None:
    files = {"model.bin": b"expected"}
    target = tmp_path / "models" / TARGET_NAME
    target.mkdir(parents=True)
    (target / ".revision").write_text(MODEL_REVISION + "\n", encoding="ascii")
    (target / "model.bin").write_bytes(b"corrupt")

    with pytest.raises(ModelVerificationError, match="SHA-256 mismatch"):
        ensure_model(tmp_path / "models", expected=_hashes(files))

    assert (target / "model.bin").read_bytes() == b"corrupt"


def test_failed_download_never_publishes_partial_model(tmp_path: Path) -> None:
    def fail(_model_id: str, *, output_dir: str, revision: str) -> None:
        del revision
        (Path(output_dir) / "model.bin").write_bytes(b"partial")
        raise OSError("network interrupted")

    with pytest.raises(OSError, match="network interrupted"):
        ensure_model(
            tmp_path / "models",
            downloader=fail,
            expected={"model.bin": hashlib.sha256(b"complete").hexdigest()},
        )

    assert not (tmp_path / "models" / TARGET_NAME).exists()
    assert not list((tmp_path / "models").glob(f".{TARGET_NAME}.*"))


def test_concurrent_publish_loser_verifies_and_reuses_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {"model.bin": b"verified weights"}

    def download(_model_id: str, *, output_dir: str, revision: str) -> None:
        assert revision == MODEL_REVISION
        (Path(output_dir) / "model.bin").write_bytes(files["model.bin"])

    def lose_publish(source: str | Path, destination: str | Path) -> None:
        # Model the other setup atomically winning just before our rename.
        shutil.copytree(source, destination)
        raise OSError(errno.ENOTEMPTY, "winner already published")

    monkeypatch.setattr("scripts.fetch_whisper_model.os.rename", lose_publish)
    target = ensure_model(
        tmp_path / "models",
        downloader=download,
        expected=_hashes(files),
    )

    assert target == tmp_path / "models" / TARGET_NAME
    assert (target / "model.bin").read_bytes() == files["model.bin"]
    assert not list((tmp_path / "models").glob(f".{TARGET_NAME}.*"))
