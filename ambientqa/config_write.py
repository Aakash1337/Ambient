"""Small, comment-preserving updates to ``config.toml``."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Literal

AudioDeviceKey = Literal["mic_device", "output_device"]

_TABLE_HEADER = re.compile(r"^[ \t]*\[[^\]\r\n]+\][ \t]*(?:#.*)?(?:\r?\n|\r)?$")


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
        matches = [
            index
            for index in range(section_start + 1, section_end)
            if pattern.match(lines[index])
        ]
        if len(matches) > 1:
            raise ValueError(f"Duplicate {section}.{key} assignments")
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


def _set_string(path: str | Path, section: str, key: str, value: str) -> None:
    config_path = Path(path)
    original = config_path.read_bytes() if config_path.exists() else b""
    has_bom = original.startswith(b"\xef\xbb\xbf")
    body = original[3:] if has_bom else original
    text = body.decode("utf-8")
    candidate = _updated_text(text, section, key, value)
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
