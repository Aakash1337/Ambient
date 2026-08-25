"""Standing domain context loaded from free-form Markdown profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_TITLE_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_RECOGNISED = {
    "topic",
    "background",
    "vocabulary",
    "interaction",
    "customer channel",
    "greeting",
    "scope",
    "knowledge",
}
_INTERACTIONS = {"assist", "agent"}
_CUSTOMER_CHANNELS = {"mic", "sys"}
# How tightly answers are bound to the profile's domain.
#   "open" - the domain only sets the answer's level and experience angle; an
#            off-topic question gets a straight off-topic answer (the default,
#            and the historical behaviour).
#   "lens" - answer through the domain: a question that is ambiguous or merely
#            adjacent is resolved within the domain and its real use cases
#            rather than drifting to a generic reading.
_SCOPES = {"open", "lens"}


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    topic: str
    background: str
    vocabulary: list[str]
    raw: str
    # These defaults deliberately follow ``raw`` so older code and tests that
    # construct Profile with five positional arguments remain source-compatible.
    interaction: str = "assist"
    customer_channel: str = "mic"
    greeting: str = ""
    scope: str = "open"
    # Directory of a pre-answered knowledge pack that belongs to this profile,
    # resolved relative to the config file. Loaded when the profile becomes
    # active so a pack travels with its profile instead of being pinned at
    # startup. Empty means "use the global knowledge.path fallback, if any".
    knowledge: str = ""

    @property
    def is_agent(self) -> bool:
        """Legacy recommendation; the runtime role is selected separately."""
        return self.interaction == "agent"


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


def _profile_choice(
    sections: dict[str, str],
    name: str,
    allowed: set[str],
    default: str,
    report: Callable[[str], None],
    profile_path: Path,
) -> str:
    """Read one small enum from a Markdown section, failing safely."""
    raw = sections.get(name, "").strip().casefold()
    if not raw:
        return default
    if raw in allowed:
        return raw
    report(
        f'Profile {name} must be one of {", ".join(sorted(allowed))} '
        f'({profile_path}); using "{default}"'
    )
    return default


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
    interaction = _profile_choice(
        sections,
        "interaction",
        _INTERACTIONS,
        "assist",
        report,
        profile_path,
    )
    customer_channel = _profile_choice(
        sections,
        "customer channel",
        _CUSTOMER_CHANNELS,
        "mic",
        report,
        profile_path,
    )
    scope = _profile_choice(
        sections,
        "scope",
        _SCOPES,
        "open",
        report,
        profile_path,
    )
    knowledge_section = sections.get("knowledge", "").strip()
    knowledge = knowledge_section.splitlines()[0].strip() if knowledge_section else ""
    return Profile(
        name=display_name,
        topic=sections.get("topic", ""),
        background=sections.get("background", ""),
        vocabulary=_vocabulary(sections.get("vocabulary", "")),
        raw=raw,
        interaction=interaction,
        customer_channel=customer_channel,
        greeting=" ".join(sections.get("greeting", "").split()),
        scope=scope,
        knowledge=knowledge,
    )
