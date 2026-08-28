#!/usr/bin/env python3
"""Fetch the immutable faster-whisper model used by the macOS release."""

from __future__ import annotations

import argparse
import errno
import hashlib
import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path


MODEL_ID = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
MODEL_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
TARGET_NAME = f"faster-whisper-large-v3-turbo-{MODEL_REVISION}"

# The large weight is a Git-LFS object; its oid is its SHA-256.  The remaining
# hashes are the exact blobs at MODEL_REVISION.  Verifying every runtime file
# means a mutable upstream branch, interrupted transfer, or corrupt cache can
# never silently become the release model.
EXPECTED_SHA256 = {
    "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
    "model.bin": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
    "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",  # gitleaks:allow -- public artifact SHA-256
    "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
}

Downloader = Callable[..., object]


class ModelVerificationError(RuntimeError):
    """The local or downloaded model does not match the audited snapshot."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(directory: Path, expected: Mapping[str, str]) -> None:
    for relative_name, wanted in expected.items():
        path = directory / relative_name
        if path.is_symlink() or not path.is_file():
            raise ModelVerificationError(f"missing model file: {relative_name}")
        actual = _sha256(path)
        if actual != wanted:
            raise ModelVerificationError(
                f"SHA-256 mismatch for {relative_name}: expected {wanted}, got {actual}"
            )


def ensure_model(
    output_root: Path,
    *,
    downloader: Downloader | None = None,
    expected: Mapping[str, str] = EXPECTED_SHA256,
) -> Path:
    """Return a verified model directory, downloading it atomically if absent."""
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / TARGET_NAME
    marker = target / ".revision"
    if target.exists():
        if (
            target.is_symlink()
            or not target.is_dir()
            or marker.is_symlink()
            or not marker.is_file()
            or marker.read_text(encoding="ascii").strip() != MODEL_REVISION
        ):
            raise ModelVerificationError(
                f"existing model target is not the audited revision: {target}"
            )
        _verify(target, expected)
        return target

    if downloader is None:
        from faster_whisper.utils import download_model

        downloader = download_model

    # A completed directory is renamed into place in one filesystem operation.
    # Temporary downloads have unique names, so concurrent setup processes
    # cannot delete or publish one another's partial state.
    with tempfile.TemporaryDirectory(
        prefix=f".{TARGET_NAME}.", dir=output_root
    ) as temporary_name:
        temporary = Path(temporary_name)
        downloader(
            MODEL_ID,
            output_dir=str(temporary),
            revision=MODEL_REVISION,
        )
        _verify(temporary, expected)
        (temporary / ".revision").write_text(
            MODEL_REVISION + "\n", encoding="ascii"
        )
        try:
            os.rename(temporary, target)
        except OSError as exc:
            # Another setup may have won the atomic publish race. Trust it only
            # after applying the same complete verification.
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            if (
                target.is_symlink()
                or marker.is_symlink()
                or not marker.is_file()
                or marker.read_text(encoding="ascii").strip() != MODEL_REVISION
            ):
                raise ModelVerificationError(
                    f"concurrent setup published an unexpected revision: {target}"
                )
            _verify(target, expected)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("models"),
        help="directory that will contain the immutable model snapshot",
    )
    args = parser.parse_args(argv)
    try:
        target = ensure_model(args.output_root)
    except (OSError, ModelVerificationError, ValueError) as exc:
        print(f"Whisper model setup failed: {exc}", file=sys.stderr)
        return 2
    print(f"Verified Whisper model: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
