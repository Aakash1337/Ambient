from __future__ import annotations

import asyncio

from ambientqa.bus import Transcript
from ambientqa.ui import AmbientQAApp


class _FakeController:
    paused = False

    def status_text(self) -> str:
        return "test"


def test_transcript_with_same_identity_is_coalesced_in_place() -> None:
    app = AmbientQAApp(_FakeController(), status_interval_s=60)
    first = Transcript("mic", "how you manage context in.", 1.0, "same")
    merged = Transcript(
        "mic",
        "how you manage context in Amazon Bedrock.",
        2.0,
        "same",
    )

    async def drive() -> None:
        async with app.run_test() as pilot:
            await app.add_transcript(first)
            await app.add_transcript(merged)
            await pilot.pause()
            rows = list(app.query(".transcript"))
            assert len(rows) == 1
            assert "Amazon Bedrock" in str(rows[0].render())

    asyncio.run(drive())
