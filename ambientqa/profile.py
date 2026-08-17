"""Standing domain context loaded from free-form Markdown profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_TITLE_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_RECOGNISED = {"topic", "background", "vocabulary"}


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    topic: str
    background: str
    vocabulary: list[str]
    raw: str


def _clean_section(text: str) -> str:
    """Remove template comments while preserving ordinary Markdown prose."""
    return _COMMENT_RE.sub("", text).strip()


def _vocabulary(text: str) -> list[str]:
    terms: list[str] = []
    for item in re.split(r"[,\n]+", text):
        term = item.strip().lstrip("-*+").strip()
        if term:
            terms.append(term)
    return terms


def load_profile(
    path: str | Path,
    status_callback: Callable[[str], None] | None = None,
) -> Profile | None:
    """Load one Markdown profile, degrading invalid inputs to no profile."""
    profile_path = Path(path)
    report = status_callback or (lambda _message: None)
    try:
        raw = profile_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        report(f"Profile unavailable ({profile_path}): {exc}; continuing without profile")
        return None

    if not raw.strip():
        report(f"Profile is empty ({profile_path}); continuing without profile")
        return None

    matches = list(_SECTION_RE.finditer(raw))
    recognised = [
        (match, match.group(1).strip().casefold())
        for match in matches
        if match.group(1).strip().casefold() in _RECOGNISED
    ]
    if not recognised:
        report(
            f"Profile has no recognised sections ({profile_path}); "
            "continuing without profile"
        )
        return None

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).strip().casefold()
        if name not in _RECOGNISED:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        sections[name] = _clean_section(raw[match.end() : end])

    title = _TITLE_RE.search(raw)
    display_name = title.group(1).strip() if title else profile_path.stem
    return Profile(
        name=display_name,
        topic=sections.get("topic", ""),
        background=sections.get("background", ""),
        vocabulary=_vocabulary(sections.get("vocabulary", "")),
        raw=raw,
    )
