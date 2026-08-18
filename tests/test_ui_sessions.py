from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ambientqa.bus import Transcript
from ambientqa.ui import (
    AmbientQAApp,
    QACard,
    SessionCard,
    SessionsScreen,
    SessionViewerScreen,
    load_session_records,
)


class _FakeController:
    paused = False

    def status_text(self) -> str:
        return "test"


def _write_session(path: Path) -> None:
    records = [
        {
            "id": "answered",
            "timestamp": 2.0,
            "channel": "sys",
            "text": "What is Docker?",
            "gate": True,
            "gate_reason": "explicit_interrogative",
            "query": "What is Docker?",
            "answer": "A container runtime.",
            "answer_status": "ok",
            "latencies_ms": {"stt": 1.0},
        },
        {
            "id": "ignored",
            "timestamp": 1.0,
            "channel": "mic",
            "text": "Hello there.",
            "gate": False,
            "gate_reason": "not_question",
            "answer": None,
            "latencies_ms": {"stt": 1.0},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\nnot json\n",
        encoding="utf-8",
    )


def test_load_session_records_sorts_by_time_and_skips_garbage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session-20260817-120000.jsonl"
    _write_session(path)
    records = load_session_records(path)
    assert [record["id"] for record in records] == ["ignored", "answered"]


def test_feed_direction_top_mounts_newest_first() -> None:
    app = AmbientQAApp(_FakeController(), status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_transcript(Transcript("mic", "older line", 1.0, "u1"))
            await app.add_question("q1", "Newest question?")
            await pilot.pause()
            feed = app.query_one("#feed")
            assert isinstance(feed.children[0], QACard)
            assert feed.scroll_y == 0

    asyncio.run(drive())


def test_feed_direction_bottom_appends_chronologically() -> None:
    app = AmbientQAApp(
        _FakeController(), status_interval_s=60, feed_direction="bottom"
    )

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_transcript(Transcript("mic", "older line", 1.0, "u1"))
            await app.add_question("q1", "Newest question?")
            await pilot.pause()
            feed = app.query_one("#feed")
            assert isinstance(feed.children[-1], QACard)

    asyncio.run(drive())


def test_session_browser_opens_viewer_and_leaves_live_feed_untouched(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_session(logs / "session-20260817-120000.jsonl")
    app = AmbientQAApp(_FakeController(), status_interval_s=60, log_dir=logs)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_transcript(Transcript("mic", "live line", 1.0, "u1"))
            await pilot.press("l")
            await pilot.pause()
            assert isinstance(app.screen, SessionsScreen)

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, SessionViewerScreen)
            cards = list(app.screen.query(SessionCard))
            assert len(cards) == 1
            rendered = str(cards[0].query_one(".answer").render())
            assert "A container runtime." in rendered

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SessionViewerScreen)
            live_rows = list(app.query(".transcript"))
            assert len(live_rows) == 1
            assert "live line" in str(live_rows[0].render())

    asyncio.run(drive())


def test_sessions_key_with_no_logs_only_notifies(tmp_path: Path) -> None:
    app = AmbientQAApp(
        _FakeController(), status_interval_s=60, log_dir=tmp_path / "missing"
    )

    async def drive() -> None:
        async with app.run_test() as pilot:
            await pilot.press("l")
            await pilot.pause()
            assert not isinstance(app.screen, SessionsScreen)

    asyncio.run(drive())
