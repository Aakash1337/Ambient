"""Coalesce VAD-split speech before it reaches the question gate."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from .bus import Transcript
from .config import MergeConfig
from .gate import (
    TRAILING_FRAGMENT_WORDS,
    is_complete_imperative_request,
    is_question_shaped,
    words,
)

_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_CONTINUATION_RE = re.compile(
    r"^(?:and|but|or|so|then|to|which|that|because|plus|also)\b",
    re.IGNORECASE,
)
_CLOSING_MARKS = "\"')]}»”’"
_TERMINAL_PUNCTUATION = ".?!;:"
_OPEN_DASHES = "-–—"


def is_open_utterance(text: str) -> bool:
    """Return whether *text* probably stops in the middle of a thought."""
    stripped = text.strip()
    if not stripped:
        return False
    probe = stripped.rstrip(_CLOSING_MARKS).rstrip()
    # A terminal '?' or '!' means the thought is finished no matter what the
    # last word is: English questions legitimately strand a preposition ("What
    # are you working on?"), and treating those as open parked a complete
    # question in the merge window for the whole hold -- or glued it onto the
    # interviewer's next sentence, destroying the '?' fast-accept downstream.
    # '.' earns no such trust: Whisper invents a period at every VAD boundary,
    # so "so tell me about." must stay open.
    if probe.endswith(("?", "!")):
        return False
    # Whisper frequently omits punctuation on terse commands. These are
    # semantically closed and should reach the gate immediately; holding
    # "EXPLAIN RAG" for the full merge window made a clear retry look dead.
    tokens = words(stripped)
    if tokens and tokens[-1].lower() in TRAILING_FRAGMENT_WORDS:
        return True
    # Whisper can preserve a clear interrogative while losing its final '?'
    # and ending the result with a comma instead.  Once the question shape is
    # coherent, the semantic gate should judge it now; a punctuation artifact
    # must not add the full continuity window first.  Keep this after the
    # dangling-word check above so genuinely unfinished questions such as
    # "How do you connect it to," still wait for their continuation.
    if is_question_shaped(stripped):
        return False
    if probe.endswith(",") or probe.endswith(tuple(_OPEN_DASHES)):
        return True
    # Keep this exception deliberately terse. Once several VAD fragments have
    # accumulated, an early request verb does not prove the speaker is done;
    # the original merge regression was a long "tell me ..." setup that kept
    # going. Six words covers direct retries such as "EXPLAIN RAG" and
    # "Tell me about RAG" without flushing long merged setups prematurely.
    if len(tokens) <= 6 and is_complete_imperative_request(stripped):
        return False
    return not probe.endswith(tuple(_TERMINAL_PUNCTUATION))


def starts_continuation(text: str) -> bool:
    """Return whether *text* begins like a continuation of prior speech."""
    stripped = text.lstrip()
    if not stripped:
        return False
    if _LEADING_CONTINUATION_RE.match(stripped):
        return True
    first_letter = next((char for char in stripped if char.isalpha()), "")
    return bool(first_letter and first_letter.islower())


def join_fragments(left: str, right: str) -> str:
    """Join STT fragments without retaining Whisper's artificial boundary period."""
    left_clean = _WHITESPACE_RE.sub(" ", left).strip()
    right_clean = _WHITESPACE_RE.sub(" ", right).strip()
    if not left_clean:
        return right_clean
    if not right_clean:
        return left_clean

    # Whisper commonly invents a full stop -- or a trailing-off ellipsis -- at
    # every VAD boundary. A pending fragment has already been identified as
    # open, so that punctuation is not semantic.
    left_clean = re.sub(r"[.…]+$", "", left_clean).rstrip()
    right_clean = re.sub(r"^[.…]+\s*", "", right_clean)
    if not left_clean:
        return right_clean
    if not right_clean:
        return left_clean
    return f"{left_clean} {right_clean}"


@dataclass(slots=True)
class _Pending:
    transcript: Transcript
    parts: int
    first_started_at: float
    deadline: float


class ContinuityMerger:
    """Stateful per-channel coalescer with deterministic timeout flushing."""

    def __init__(
        self,
        config: MergeConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock
        self._pending: dict[str, _Pending] = {}

    @staticmethod
    def _start(transcript: Transcript) -> float:
        return (
            transcript.timestamp
            if transcript.started_at is None
            else transcript.started_at
        )

    def _at_cap(self, pending: _Pending) -> bool:
        duration = pending.transcript.timestamp - pending.first_started_at
        return (
            pending.parts >= self.config.max_merge_parts
            or duration >= self.config.max_merge_s
        )

    def _begin(
        self,
        transcript: Transcript,
        now: float,
        hold_s: float | None = None,
    ) -> list[Transcript]:
        if not is_open_utterance(transcript.text):
            return [transcript]
        pending = _Pending(
            transcript=transcript,
            parts=1,
            first_started_at=self._start(transcript),
            deadline=now + (
                self.config.merge_window_s if hold_s is None else hold_s
            ),
        )
        if self._at_cap(pending):
            return [transcript]
        self._pending[transcript.channel] = pending
        return []

    def _merge(
        self,
        pending: _Pending,
        transcript: Transcript,
        now: float,
        hold_s: float | None = None,
    ) -> Transcript:
        previous = pending.transcript
        merged = Transcript(
            channel=previous.channel,
            text=join_fragments(previous.text, transcript.text),
            timestamp=transcript.timestamp,
            utterance_id=previous.utterance_id,
            latency_ms=previous.latency_ms + transcript.latency_ms,
            started_at=pending.first_started_at,
        )
        pending.transcript = merged
        pending.parts += 1
        pending.deadline = now + (
            self.config.merge_window_s if hold_s is None else hold_s
        )
        return merged

    def flush_expired(self, now: float | None = None) -> list[Transcript]:
        """Emit pending thoughts whose continuation window has elapsed."""
        current = self._clock() if now is None else now
        expired = sorted(
            (
                (pending.deadline, channel, pending.transcript)
                for channel, pending in self._pending.items()
                if current >= pending.deadline
            ),
            key=lambda item: item[0],
        )
        for _deadline, channel, _transcript in expired:
            self._pending.pop(channel, None)
        return [transcript for _deadline, _channel, transcript in expired]

    def push(
        self,
        transcript: Transcript,
        now: float | None = None,
        *,
        complete: bool = False,
        hold_s: float | None = None,
    ) -> list[Transcript]:
        """Consume one STT result and return transcripts ready for routing.

        ``complete`` is used by direct conversational roles after they have
        classified a VAD turn as self-contained. It bypasses the question-mode
        punctuation heuristic, while still joining the turn onto any genuinely
        pending fragment from the same channel.
        """
        if not self.config.enabled:
            return [transcript]
        current = self._clock() if now is None else now
        ready = self.flush_expired(current)
        pending = self._pending.get(transcript.channel)
        if pending is None:
            if complete:
                ready.append(transcript)
            else:
                ready.extend(self._begin(transcript, current, hold_s))
            return ready

        gap = self._start(transcript) - pending.transcript.timestamp
        continues = (
            gap <= self.config.merge_gap_s
            and (
                is_open_utterance(pending.transcript.text)
                or starts_continuation(transcript.text)
            )
        )
        if not continues:
            self._pending.pop(transcript.channel, None)
            ready.append(pending.transcript)
            if complete:
                ready.append(transcript)
            else:
                ready.extend(self._begin(transcript, current, hold_s))
            return ready

        merged = self._merge(pending, transcript, current, hold_s)
        if complete or self._at_cap(pending) or not is_open_utterance(merged.text):
            self._pending.pop(transcript.channel, None)
            ready.append(merged)
        return ready

    def flush_all(self) -> list[Transcript]:
        """Emit and clear every held thought, ordered by its audio timestamp."""
        transcripts = [
            pending.transcript for pending in self._pending.values()
        ]
        self._pending.clear()
        return sorted(transcripts, key=lambda transcript: transcript.timestamp)

    def discard(self, channel: str) -> Transcript | None:
        """Abandon one channel's held thought without disturbing the other."""
        pending = self._pending.pop(channel, None)
        return None if pending is None else pending.transcript
