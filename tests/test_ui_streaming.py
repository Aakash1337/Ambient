from __future__ import annotations

import asyncio

from textual.widgets import Static

from ambientqa.bus import AnswerResult
from ambientqa.ui import AmbientQAApp, QACard


class _FakeController:
    paused = False

    def status_text(self) -> str:
        return "test"


def _answer_text(card: QACard) -> str:
    return str(card.query_one(".answer", Static).render())


def test_streamed_code_fence_is_rendered_from_whole_accumulated_answer() -> None:
    app = AmbientQAApp(_FakeController(), status_interval_s=60)
    first = "Here is the wrapper:\n\n```python\ndef wrapper(*args, **"
    second = "kwargs):\n    return func(*args, **kwargs)\n```\n"

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_question("q1", "Show the wrapper")
            app.append_answer_delta("q1", first)
            await pilot.pause()
            card = app.query_one("#qa-q1", QACard)
            partial = _answer_text(card)
            assert "answering" not in partial
            assert "```" not in partial
            assert "def wrapper(*args, **" in partial

            app.append_answer_delta("q1", second)
            await pilot.pause(0.15)
            complete = _answer_text(card)
            assert "```" not in complete
            assert "def wrapper(*args, **kwargs):" in complete
            assert "    return func(*args, **kwargs)" in complete.splitlines()

    asyncio.run(drive())


def test_fast_deltas_are_coalesced_to_about_ten_updates_per_second() -> None:
    app = AmbientQAApp(_FakeController(), status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_question("q1", "Question?")
            card = app.query_one("#qa-q1", QACard)
            render_times: list[float] = []
            card._rendered_callback = lambda: render_times.append(
                asyncio.get_running_loop().time()
            )

            app.append_answer_delta("q1", "first")
            for index in range(20):
                app.append_answer_delta("q1", f" {index}")
            assert len(render_times) == 1

            await pilot.pause(0.15)
            assert len(render_times) == 2
            assert " 19" in _answer_text(card)

    asyncio.run(drive())


def test_concurrent_question_cards_never_cross_contaminate() -> None:
    app = AmbientQAApp(_FakeController(), status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_question("q1", "First?")
            await app.add_question("q2", "Second?")
            app.append_answer_delta("q1", "alpha ")
            app.append_answer_delta("q2", "bravo ")
            app.append_answer_delta("q1", "one")
            app.append_answer_delta("q2", "two")
            # Deltas are coalesced onto a timer, so wait for the render to land
            # rather than for a fixed duration. A fixed pause makes this fail
            # under load, reporting "cross-contamination" when the second delta
            # had merely not been flushed yet.
            for _ in range(60):
                await pilot.pause(0.05)
                if "one" in _answer_text(
                    app.query_one("#qa-q1", QACard)
                ) and "two" in _answer_text(app.query_one("#qa-q2", QACard)):
                    break

            first = _answer_text(app.query_one("#qa-q1", QACard))
            second = _answer_text(app.query_one("#qa-q2", QACard))
            assert "alpha one" in first and "bravo" not in first
            assert "bravo two" in second and "alpha" not in second

    asyncio.run(drive())


def test_final_result_replaces_partial_and_cancels_pending_render() -> None:
    app = AmbientQAApp(_FakeController(), status_interval_s=60)

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_question("q1", "Question?")
            app.append_answer_delta("q1", "partial")
            app.append_answer_delta("q1", " pending")
            app.resolve_answer(
                AnswerResult("q1", "Question?", "Final answer.", "ok", 12.0)
            )
            await pilot.pause(0.15)
            rendered = _answer_text(app.query_one("#qa-q1", QACard))
            assert "Final answer." in rendered
            assert "partial" not in rendered
            assert "answering" not in rendered

    asyncio.run(drive())
