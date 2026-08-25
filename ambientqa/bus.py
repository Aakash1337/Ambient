"""Shared event types and bounded, drop-oldest queues."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import Callable, Generic, TypeVar
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

T = TypeVar("T")


class DropOldestQueue(asyncio.Queue[T], Generic[T]):
    """A queue whose producer never waits: the oldest item is discarded when full."""

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize)
        self._thread_lock = threading.Lock()
        self._thread_pending: deque[T] = deque(maxlen=maxsize or None)
        self._thread_drain_scheduled = False

    def put_drop_oldest(self, item: T) -> T | None:
        dropped = None
        if self.full():
            try:
                dropped = self.get_nowait()
                self.task_done()
            except asyncio.QueueEmpty:
                dropped = None
        self.put_nowait(item)
        return dropped

    def drain(self) -> list[T]:
        items: list[T] = []
        while True:
            try:
                items.append(self.get_nowait())
                self.task_done()
            except asyncio.QueueEmpty:
                return items

    def discard_where(self, predicate: Callable[[T], bool]) -> list[T]:
        """Remove matching queued and thread-pending items, preserving order.

        Capture producers first stage items in ``_thread_pending`` before an
        event-loop callback moves them into the asyncio queue. Filtering only
        the visible queue therefore leaves a race where an already-captured
        frame appears immediately after a channel is muted. This method covers
        both stores. Call it from the queue's event-loop thread, just like
        ``drain()`` and ``put_drop_oldest()``.
        """
        discarded: list[T] = []
        retained: list[T] = []
        for item in self.drain():
            if predicate(item):
                discarded.append(item)
            else:
                retained.append(item)
        for item in retained:
            self.put_nowait(item)

        with self._thread_lock:
            pending: deque[T] = deque(maxlen=self._thread_pending.maxlen)
            for item in self._thread_pending:
                if predicate(item):
                    discarded.append(item)
                else:
                    pending.append(item)
            self._thread_pending = pending
        return discarded

    def put_from_thread(self, loop: asyncio.AbstractEventLoop, item: T) -> None:
        """Bound both retained items and loop callbacks before the asyncio queue."""
        with self._thread_lock:
            self._thread_pending.append(item)
            if self._thread_drain_scheduled:
                return
            self._thread_drain_scheduled = True
        try:
            loop.call_soon_threadsafe(self._drain_thread_pending)
        except RuntimeError:
            with self._thread_lock:
                self._thread_drain_scheduled = False
                self._thread_pending.clear()

    def _drain_thread_pending(self) -> None:
        while True:
            with self._thread_lock:
                if not self._thread_pending:
                    self._thread_drain_scheduled = False
                    return
                item = self._thread_pending.popleft()
            self.put_drop_oldest(item)


@dataclass(slots=True)
class AudioFrame:
    channel: str
    audio: NDArray[np.float32]
    timestamp: float


@dataclass(slots=True)
class Utterance:
    channel: str
    audio: NDArray[np.float32]
    started_at: float
    ended_at: float
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def duration_s(self) -> float:
        return len(self.audio) / 16000.0


@dataclass(slots=True)
class Transcript:
    channel: str
    text: str
    timestamp: float
    utterance_id: str = field(default_factory=lambda: uuid4().hex)
    latency_ms: float = 0.0
    started_at: float | None = None


@dataclass(slots=True)
class GateResult:
    transcript: Transcript
    accepted: bool
    reason: str
    query: str = ""
    latency_ms: float = 0.0


@dataclass(slots=True)
class AnswerResult:
    question_id: str
    question: str
    answer: str
    status: str
    latency_ms: float
    timestamp: float = field(default_factory=time)
    # Whether this answer was looked up rather than recalled. Worth recording:
    # a lookup runs ~13s slower, so it explains an outlier latency in the log.
    searched: bool = False


def put_threadsafe(
    loop: asyncio.AbstractEventLoop, queue: DropOldestQueue[T], item: T
) -> None:
    """Schedule a non-blocking queue write from a capture thread."""
    queue.put_from_thread(loop, item)
