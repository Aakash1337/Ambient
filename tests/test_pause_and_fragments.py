from __future__ import annotations

import pytest

from ambientqa.gate import TRAILING_FRAGMENT_WORDS, heuristic_decision


# --- regression: real questions ending in an object pronoun ---
# "Fix this." was rejected as trailing_fragment, which ALSO made the merge layer
# hold it for the full window, adding ~9s of latency before the gate even ran.

@pytest.mark.parametrize(
    "text",
    [
        "The model selected the right tool but passed the wrong argument. How would you fix this.",
        "Explain that.",
        "Walk me through this.",
        "How would you approach that.",
        "Tell me more about what it is like.",
        "Describe when you would use those.",
    ],
)
def test_sentence_final_pronouns_are_not_fragments(text: str) -> None:
    assert heuristic_decision(text).reason != "trailing_fragment"


@pytest.mark.parametrize(
    "word", ["this", "that", "these", "those", "like", "when"]
)
def test_ambiguous_enders_excluded_from_fragment_list(word: str) -> None:
    assert word not in TRAILING_FRAGMENT_WORDS


# --- genuine fragments must still be caught ---

@pytest.mark.parametrize(
    "text",
    [
        "so tell me about",
        "how you manage context in",
        "the main components of",
        "I was thinking we could use the",
        "it depends on whether it is",
    ],
)
def test_real_truncations_still_rejected(text: str) -> None:
    assert heuristic_decision(text).reason == "trailing_fragment"


def _screen_lines(app) -> list[str]:
    """What is actually on screen, not merely what state the widgets hold."""
    return [
        "".join(seg.text for seg in strip)
        for strip in app.screen._compositor.render_strips()
    ]


def test_pause_banner_and_status_actually_render() -> None:
    """Assert on rendered pixels, not CSS classes.

    An earlier version of this test checked `has_class("visible")` and passed
    while nothing was visible: the banner was docked bottom, landed on the same
    row as the Footer, and was drawn over. The status bar had the same collision
    and had never been visible at all.
    """
    import asyncio

    from ambientqa.ui import AmbientQAApp

    class Stub:
        paused = False

        def toggle_pause(self) -> bool:
            self.paused = not self.paused
            return self.paused

        def status_text(self) -> str:
            return "⏸ PAUSED  mic:on" if self.paused else "● listening  mic:on"

    controller = Stub()
    app = AmbientQAApp(controller, status_interval_s=60)

    async def drive() -> None:
        async with app.run_test(size=(80, 14)) as pilot:
            app._refresh_status()
            await pilot.pause()

            lines = _screen_lines(app)
            assert any("listening" in line for line in lines), "status bar not rendered"
            assert not any("PAUSED" in line for line in lines)

            await pilot.press("p")
            await pilot.pause()

            lines = _screen_lines(app)
            assert any("PAUSED" in line for line in lines), "pause banner not rendered"
            assert any("Press p to resume" in line for line in lines)
            # Status text flips immediately, not on the next 60s tick.
            assert any("⏸ PAUSED" in line for line in lines)

            # No widget may share a row with another, or one silently covers it.
            banner = app.query_one("#paused-banner")
            status = app.query_one("#status")
            footer = app.query_one("Footer")
            rows = [banner.region.y, status.region.y, footer.region.y]
            assert len(set(rows)) == 3, f"overlapping rows: {rows}"

            await pilot.press("p")
            await pilot.pause()
            lines = _screen_lines(app)
            assert not any("PAUSED" in line for line in lines)
            assert any("listening" in line for line in lines)

    asyncio.run(drive())
