"""Recent transcript context, similarity, and cross-channel echo suppression."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from .bus import Transcript

TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


def normalised_tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


def token_set_ratio(left: str, right: str) -> float:
    """Sørensen-Dice token-set similarity, in the inclusive range 0..1."""
    a, b = normalised_tokens(left), normalised_tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


@dataclass(slots=True)
class ContextLine:
    channel: str
    text: str
    timestamp: float

    def render(self) -> str:
        return f"[{self.channel}] {self.text}"


class TranscriptContext:
    def __init__(
        self,
        max_lines: int = 100,
        echo_window_s: float = 2.0,
        echo_ratio: float = 0.85,
    ) -> None:
        self._lines: deque[ContextLine] = deque(maxlen=max_lines)
        self.echo_window_s = echo_window_s
        self.echo_ratio = echo_ratio

    def is_cross_channel_echo(self, transcript: Transcript) -> bool:
        for line in reversed(self._lines):
            age = transcript.timestamp - line.timestamp
            if age > self.echo_window_s:
                break
            if line.channel != transcript.channel and abs(age) <= self.echo_window_s:
                if token_set_ratio(line.text, transcript.text) >= self.echo_ratio:
                    # If sys arrived first, mic is still the preferred copy.
                    return transcript.channel != "mic"
        return False

    def remove_matching_system_echo(self, transcript: Transcript) -> None:
        if transcript.channel != "mic":
            return
        kept = [
            line
            for line in self._lines
            if not (
                line.channel == "sys"
                and abs(transcript.timestamp - line.timestamp) <= self.echo_window_s
                and token_set_ratio(line.text, transcript.text) >= self.echo_ratio
            )
        ]
        self._lines = deque(kept, maxlen=self._lines.maxlen)

    def add(self, transcript: Transcript) -> bool:
        if self.is_cross_channel_echo(transcript):
            return False
        self.remove_matching_system_echo(transcript)
        self._lines.append(
            ContextLine(transcript.channel, transcript.text, transcript.timestamp)
        )
        return True

    def clear(self) -> None:
        """Start a fresh conversation without replacing the context object."""
        self._lines.clear()

    def recent(self, count: int = 6, exclude_latest: bool = False) -> list[ContextLine]:
        lines = list(self._lines)
        if exclude_latest and lines:
            lines = lines[:-1]
        return lines[-count:]

    def rendered(self, count: int = 6, exclude_latest: bool = False) -> list[str]:
        return [line.render() for line in self.recent(count, exclude_latest)]

    @property
    def last(self) -> ContextLine | None:
        return self._lines[-1] if self._lines else None
