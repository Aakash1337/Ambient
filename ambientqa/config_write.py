"""Small, comment-preserving updates to ``config.toml``."""

from __future__ import annotations

import contextlib
import copy
import errno
import os
import re
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

AudioDeviceKey = Literal["mic_device", "output_device"]

_TABLE_HEADER = re.compile(r"^[ \t]*\[[^\]\r\n]+\][ \t]*(?:#.*)?(?:\r?\n|\r)?$")

_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


@contextlib.contextmanager
def _process_file_lock(path: Path):
    """Serialize config replacement across app workers and helper processes."""
    lock_path = path.with_name(f".{path.name}.ambientqa.lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI/users
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _basic_string(value: str) -> str:
    escaped: list[str] = []
    replacements = {
        "\\": "\\\\",
        '"': '\\"',
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }
    for character in value:
        if character in replacements:
            escaped.append(replacements[character])
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _quoted_value(value: str, preferred_quote: str = '"') -> str:
    if preferred_quote == "'" and "'" not in value and all(
        character not in "\r\n" and ord(character) >= 0x20 for character in value
    ):
        return f"'{value}'"
    return _basic_string(value)


def _table_pattern(section: str) -> re.Pattern[str]:
    return re.compile(
        rf"^[ \t]*\[{re.escape(section)}\][ \t]*(?:#.*)?(?:\r?\n|\r)?$"
    )


def _assignment_pattern(key: str) -> re.Pattern[str]:
    key_token = rf"(?:{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')"
    return re.compile(
        rf"^(?P<prefix>[ \t]*{key_token}[ \t]*=[ \t]*)"
        r"(?P<value>\"(?:\\.|[^\"\\])*\"|'[^']*')"
        r"(?P<suffix>[ \t]*(?:#.*)?)(?P<newline>\r?\n|\r)?$"
    )


def _assignment_start_pattern(key: str) -> re.Pattern[str]:
    """Recognize the key even when its TOML value spans multiple lines.

    The updater intentionally rewrites only simple, single-line strings so it
    can preserve formatting exactly.  Still, a valid multiline/array/inline
    value for the same key must not be mistaken for a missing assignment: that
    would insert a duplicate key and corrupt an otherwise valid config.
    """
    key_token = rf"(?:{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')"
    return re.compile(rf"^[ \t]*{key_token}[ \t]*=")


def _updated_text(text: str, section: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    section_start: int | None = None
    section_end = len(lines)
    header_pattern = _table_pattern(section)
    for index, line in enumerate(lines):
        if header_pattern.match(line):
            section_start = index
            break
    if section_start is not None:
        for index in range(section_start + 1, len(lines)):
            if _TABLE_HEADER.match(lines[index]):
                section_end = index
                break

        pattern = _assignment_pattern(key)
        assignment_start = _assignment_start_pattern(key)
        assignments = [
            index
            for index in range(section_start + 1, section_end)
            if assignment_start.match(lines[index])
        ]
        if len(assignments) > 1:
            raise ValueError(f"Duplicate {section}.{key} assignments")
        matches = [
            index
            for index in range(section_start + 1, section_end)
            if pattern.match(lines[index])
        ]
        if matches:
            index = matches[0]
            match = pattern.match(lines[index])
            assert match is not None
            preferred = match.group("value")[0]
            lines[index] = (
                match.group("prefix")
                + _quoted_value(value, preferred)
                + match.group("suffix")
                + (match.group("newline") or "")
            )
            return "".join(lines)
        if assignments:
            raise ValueError(
                f"Cannot safely rewrite non-single-line {section}.{key} assignment"
            )

        newline = "\r\n" if "\r\n" in text else "\n"
        insertion = f"{key} = {_quoted_value(value)}{newline}"
        if section_end > 0 and not lines[section_end - 1].endswith(("\n", "\r")):
            insertion = newline + insertion
        lines.insert(section_end, insertion)
        return "".join(lines)

    newline = "\r\n" if "\r\n" in text else "\n"
    prefix = ""
    if text:
        prefix = "" if text.endswith(("\n", "\r")) else newline
        if not text.endswith((newline + newline, "\r\n\r\n")):
            prefix += newline
    return text + prefix + f"[{section}]{newline}{key} = {_quoted_value(value)}{newline}"


def _set_string_unlocked(
    config_path: Path,
    section: str,
    key: str,
    value: str,
) -> None:
    original = config_path.read_bytes() if config_path.exists() else b""
    has_bom = original.startswith(b"\xef\xbb\xbf")
    body = original[3:] if has_bom else original
    text = body.decode("utf-8")
    candidate = _updated_text(text, section, key, value)
    try:
        parsed_original = tomllib.loads(text)
        parsed_candidate = tomllib.loads(candidate)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Cannot safely update invalid TOML: {exc}") from exc
    expected_candidate = copy.deepcopy(parsed_original)
    expected_section = expected_candidate.setdefault(section, {})
    if not isinstance(expected_section, dict):
        raise ValueError(f"Cannot safely locate {section}.{key} assignment")
    expected_section[key] = value
    if parsed_candidate != expected_candidate:
        # Text scanning is deliberately formatting-preserving, but table-like
        # text can legally occur inside an unrelated multiline TOML string.
        # Parse the finished candidate and prove the requested semantic change
        # is its ONLY semantic change before creating or publishing a temp file.
        raise ValueError(f"Cannot safely locate {section}.{key} assignment")
    encoded = (b"\xef\xbb\xbf" if has_bom else b"") + candidate.encode("utf-8")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        suffix=".tmp",
        dir=config_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if config_path.exists():
            os.chmod(temporary_path, stat.S_IMODE(config_path.stat().st_mode))
        os.replace(temporary_path, config_path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _set_string(path: str | Path, section: str, key: str, value: str) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # The controller has separate profile/device lifecycle locks because those
    # operations own different runtime state. Persistence is one shared TOML
    # document, so it needs its own path-wide transaction lock as well.
    with _thread_lock(config_path), _process_file_lock(config_path):
        _set_string_unlocked(config_path, section, key, value)


def set_audio_device(
    path: str | Path,
    key: AudioDeviceKey,
    value: str,
) -> None:
    """Atomically update one device assignment inside the ``[audio]`` table.

    The file is edited as text rather than serialized from TOML, preserving its
    comments, ordering, whitespace, newline convention, and unrelated values.
    """

    if key not in {"mic_device", "output_device"}:
        raise ValueError("key must be mic_device or output_device")
    _set_string(path, "audio", key, value)


def set_context_profile(path: str | Path, value: str) -> None:
    """Atomically update ``context.profile`` while preserving file comments."""
    _set_string(path, "context", "profile", value)
