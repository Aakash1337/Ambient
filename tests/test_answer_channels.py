"""The wearer's own speech must not manufacture questions.

From a real interview session: with the loopback pointed at a silent endpoint,
every utterance reaching the gate was the wearer's own narration, and the gate
rewrote plain statements into questions and answered them --
"...so I built a RAG system where" became "What is a RAG system?", and about
thirty such cards appeared during a forty-minute conversation. None had been
asked by anyone. Their own speech must still reach the context window, because
it is what resolves referents in the other speaker's questions.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from ambientqa.__main__ import AmbientController
from ambientqa.bus import DropOldestQueue, GateResult, Transcript
from ambientqa.config import MergeConfig, default_config
from ambientqa.context import TranscriptContext
from ambientqa.continuity import ContinuityMerger


def transcript(text: str, channel: str, timestamp: float = 100.0) -> Transcript:
    return Transcript(
        channel=channel,
        text=text,
        timestamp=timestamp,
        utterance_id=f"{channel}-{timestamp}",
        latency_ms=10.0,
        started_at=timestamp - 1.0,
    )


class Recorder:
    """Captures every decision the pipeline makes about one transcript."""

    def __init__(self, policy: dict[str, str]) -> None:
        self.config = default_config()
        self.config.gate.channel_policy = policy
        self.rejections: list[tuple[str, str]] = []
        self.gated: list[str] = []
        self.answered: list[str] = []
        self.shown: list[str] = []

    def build(self) -> AmbientController:
        controller = AmbientController.__new__(AmbientController)
        controller.config = self.config
        controller.paused = False
        controller.context = TranscriptContext()
        controller.last_transcript = None
        outer = self

        class App:
            async def add_transcript(self, item: Transcript) -> None:
                outer.shown.append(item.text)

            async def add_question(self, _id: str, query: str) -> None:
                outer.answered.append(query)

        class Gate:
            async def evaluate(
                self,
                item: Transcript,
                _background: list[str],
                policy: str = "full",
            ) -> GateResult:
                if policy == "off":
                    return GateResult(item, False, "channel_not_answered", "", 0.0)
                outer.gated.append(item.text)
                return GateResult(item, True, "ollama_accept", item.text, 1.0)

        controller.app = App()  # type: ignore[assignment]
        controller.gate = Gate()  # type: ignore[assignment]

        async def log_rejection(item: Transcript, reason: str) -> None:
            outer.rejections.append((item.text, reason))

        async def enqueue(_job: Any) -> None:
            return None

        controller._log_rejection = log_rejection  # type: ignore[method-assign]
        controller._enqueue_answer = enqueue  # type: ignore[method-assign]
        controller._gate_tasks = set()
        controller._recent_rejections = deque(maxlen=24)
        controller._gate_semaphore = asyncio.Semaphore(
            self.config.gate.max_concurrent
        )
        return controller


def process(controller: AmbientController, item: Transcript) -> None:
    """Run one transcript through the pipeline and settle its detached gate task."""

    async def drive() -> None:
        await controller._process_transcript(item)
        while controller._gate_tasks:
            await asyncio.gather(*list(controller._gate_tasks))

    asyncio.run(drive())


def test_an_off_channel_never_reaches_the_gate() -> None:
    recorder = Recorder({"mic": "off", "sys": "full"})
    controller = recorder.build()
    own_speech = "two systems to make my job easier with AI. So I built a RAG system where"

    process(controller, transcript(own_speech, "mic"))

    assert recorder.answered == []
    # Not merely unanswered -- the ~800ms Ollama call is skipped entirely.
    assert recorder.gated == []
    assert recorder.rejections == [(own_speech, "channel_not_answered")]


def test_unanswered_speech_still_feeds_the_context_window() -> None:
    """Suppressing the answer must not suppress the context it provides."""
    recorder = Recorder({"mic": "off", "sys": "full"})
    controller = recorder.build()
    own_speech = "I use Bedrock knowledge bases for the retrieval layer."

    process(controller, transcript(own_speech, "mic"))

    assert recorder.shown == [own_speech], "transcript pane must still show it"
    assert controller.context.rendered() == [f"[mic] {own_speech}"]
    assert controller.last_transcript is not None


def test_the_other_speaker_is_answered() -> None:
    recorder = Recorder({"mic": "explicit", "sys": "full"})
    controller = recorder.build()
    question = "How do you evaluate a RAG pipeline?"

    process(controller, transcript(question, "sys"))

    assert recorder.gated == [question]
    assert recorder.answered == [question]
    assert recorder.rejections == []


def test_full_policy_answers_that_channel_freely() -> None:
    recorder = Recorder({"mic": "full", "sys": "full"})
    controller = recorder.build()
    text = "What is the default timeout?"

    process(controller, transcript(text, "mic"))

    assert recorder.answered == [text]
    assert recorder.rejections == []


def test_judgment_rejection_reaches_the_optional_sweep_buffer() -> None:
    """The ordinary rejected-gate path must feed second-pass recovery."""
    recorder = Recorder({"mic": "explicit", "sys": "full"})
    controller = recorder.build()
    item = transcript("Tell me how the fallback works.", "sys")

    class RejectingGate:
        async def evaluate(
            self,
            candidate: Transcript,
            _background: list[str],
            policy: str = "full",
        ) -> GateResult:
            return GateResult(candidate, False, "ollama_reject", "", 1.0)

    async def discard_log(_record: dict[str, Any]) -> None:
        return None

    controller.gate = RejectingGate()  # type: ignore[assignment]
    controller._log = discard_log  # type: ignore[method-assign]

    process(controller, item)

    assert list(controller._recent_rejections) == [item]


def test_vocative_judgment_reaches_the_sweep_safety_net() -> None:
    controller = Recorder({"mic": "explicit", "sys": "full"}).build()
    item = transcript("Again, describe RAG pipelines.", "mic")

    controller._remember_sweep_rejection(item, "human_vocative")

    assert list(controller._recent_rejections) == [item]


def test_rhetorical_tag_judgment_reaches_the_sweep_safety_net() -> None:
    """A clarification ending in ``right?`` can be a real follow-up."""
    controller = Recorder({"mic": "explicit", "sys": "full"}).build()
    item = transcript(
        "You said Firefox has limited support, right? But full-screen "
        "sharing should include system audio, right?",
        "mic",
    )

    controller._remember_sweep_rejection(item, "tag_or_rhetorical")

    assert list(controller._recent_rejections) == [item]


class TestExplicitPolicy:
    """A question you ask out loud must be answered; narration must not be.

    Both halves are load-bearing and pull against each other. Blocking the whole
    mic channel stopped the invented questions but also silently swallowed real
    ones -- "Okay, what do you mean, how, how do I truncate it?" produced nothing
    until it was force-answered by hand.
    """

    @staticmethod
    def decide(text: str, policy: str = "explicit") -> GateResult:
        from ambientqa.config import GateConfig
        from ambientqa.gate import QuestionGate

        gate = QuestionGate(GateConfig())

        async def refuse(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
            raise AssertionError(
                "the semantic gate must not be consulted for this utterance"
            )

        gate.ollama.classify = refuse  # type: ignore[method-assign]
        return asyncio.run(
            gate.evaluate(transcript(text, "mic"), [], policy)
        )

    def test_the_reported_question_is_accepted_with_no_llm_call(self) -> None:
        result = self.decide("Okay, what do you mean, how, how do I truncate it?")
        assert result.accepted, "a direct spoken question was not treated as one"
        assert result.reason == "explicit_interrogative"
        # Free and deterministic: it never touches Ollama, so it is answered at
        # once rather than ~900ms later.
        assert result.latency_ms < 50

    def test_narration_is_rejected_without_reaching_the_semantic_gate(self) -> None:
        result = self.decide(
            "two systems to make my job easier with AI. So I built a RAG system where"
        )
        assert not result.accepted
        assert result.reason == "not_a_direct_question"

    def test_a_statement_naming_a_topic_is_still_rejected(self) -> None:
        result = self.decide("I have like a slightly tuned version, I use Gemma 4.")
        assert not result.accepted
        assert result.reason == "not_a_direct_question"

    def test_question_intonation_reaches_the_semantic_gate(self) -> None:
        """Real questions that do not start with an interrogative still count."""
        from ambientqa.config import GateConfig
        from ambientqa.gate import QuestionGate

        gate = QuestionGate(GateConfig())
        seen: list[str] = []

        async def classify(text: str, _context: list[str]) -> tuple[bool, str]:
            seen.append(text)
            return True, "How do I make new profiles?"

        gate.ollama.classify = classify  # type: ignore[method-assign]
        text = "I can see two pre-made profiles, but how do I make new profiles?"
        result = asyncio.run(gate.evaluate(transcript(text, "mic"), [], "explicit"))

        assert seen == [text]
        assert result.accepted
        assert result.query == "How do I make new profiles?"

    def test_full_policy_still_lets_the_semantic_gate_see_statements(self) -> None:
        from ambientqa.config import GateConfig
        from ambientqa.gate import QuestionGate

        gate = QuestionGate(GateConfig())
        seen: list[str] = []

        async def classify(text: str, _context: list[str]) -> tuple[bool, str]:
            seen.append(text)
            return False, ""

        gate.ollama.classify = classify  # type: ignore[method-assign]
        statement = "I built a RAG system where the retrieval is hybrid."
        asyncio.run(gate.evaluate(transcript(statement, "sys"), [], "full"))
        assert seen == [statement]

    def test_off_policy_short_circuits_before_any_heuristic(self) -> None:
        result = self.decide("What is retrieval augmented generation?", "off")
        assert not result.accepted
        assert result.reason == "channel_not_answered"


def test_a_slow_gate_does_not_delay_the_next_question() -> None:
    """Gating must not run inline on the ordered consumer path.

    It used to. Gating is a ~900ms call, so a question arriving while the
    previous one was still being judged could not reach the answer queue until
    that call returned -- its answer started a full gate late, every time, even
    though the answerer was completely idle.
    """
    recorder = Recorder({"mic": "explicit", "sys": "full"})
    controller = recorder.build()
    controller._gate_semaphore = asyncio.Semaphore(4)
    release = asyncio.Event()
    entered: list[str] = []

    class SlowGate:
        async def evaluate(
            self, item: Transcript, _background: list[str], policy: str = "full"
        ) -> GateResult:
            entered.append(item.text)
            await release.wait()
            return GateResult(item, True, "ollama_accept", item.text, 1.0)

    controller.gate = SlowGate()  # type: ignore[assignment]

    async def drive() -> None:
        await controller._process_transcript(transcript("First question?", "sys", 100.0))
        await controller._process_transcript(transcript("Second question?", "sys", 101.0))
        # Both are being judged concurrently while neither has returned.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(entered) == 2:
                break
        assert entered == ["First question?", "Second question?"], (
            "second question was blocked behind the first gate call"
        )
        release.set()
        await asyncio.gather(*list(controller._gate_tasks))

    asyncio.run(drive())
    assert sorted(recorder.answered) == ["First question?", "Second question?"]


def test_gate_concurrency_is_bounded_by_config() -> None:
    """Unbounded detached tasks would let a talkative stretch flood Ollama."""
    recorder = Recorder({"mic": "explicit", "sys": "full"})
    controller = recorder.build()
    controller._gate_semaphore = asyncio.Semaphore(2)
    release = asyncio.Event()
    concurrent = 0
    peak = 0

    class CountingGate:
        async def evaluate(
            self, item: Transcript, _background: list[str], policy: str = "full"
        ) -> GateResult:
            nonlocal concurrent, peak
            concurrent += 1
            peak = max(peak, concurrent)
            await release.wait()
            concurrent -= 1
            return GateResult(item, True, "ollama_accept", item.text, 1.0)

    controller.gate = CountingGate()  # type: ignore[assignment]

    async def drive() -> None:
        for index in range(6):
            await controller._process_transcript(
                transcript(f"Question {index}?", "sys", 100.0 + index)
            )
        for _ in range(50):
            await asyncio.sleep(0.01)
            if peak >= 2:
                break
        release.set()
        while controller._gate_tasks:
            await asyncio.gather(*list(controller._gate_tasks))

    asyncio.run(drive())
    assert peak == 2, f"gate ran {peak} at once against a limit of 2"
    assert len(recorder.answered) == 6


def _drive_consume(controller: AmbientController, item: Transcript) -> None:
    """Run one iteration of the transcript consumer over a single transcript."""

    async def drive() -> None:
        controller.transcripts.put_drop_oldest(item)
        task = asyncio.create_task(controller._consume_transcripts())
        for _ in range(20):
            await asyncio.sleep(0.02)
            if controller.ingested or controller._pending_system:
                break
        controller.stop.set()
        task.cancel()
        # Surface a crash in the consumer loop instead of reporting it as "the
        # transcript was never processed", which is what swallowing it looks like.
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(outcome, BaseException) and not isinstance(
            outcome, asyncio.CancelledError
        ):
            raise outcome

    asyncio.run(drive())


def _consumer(hold: bool) -> AmbientController:
    controller = Recorder({"mic": "explicit", "sys": "full"}).build()
    controller.stop = asyncio.Event()
    controller.transcripts = DropOldestQueue(8)
    controller._pending_system = deque()
    controller._hold_system_for_echo = hold
    controller._ignore_before = 0.0
    controller.continuity = ContinuityMerger(MergeConfig())
    controller.ingested = []  # type: ignore[attr-defined]

    async def ingest(item: Transcript) -> None:
        controller.ingested.append(item.text)  # type: ignore[attr-defined]

    controller._ingest_transcript = ingest  # type: ignore[method-assign]
    return controller


def test_system_transcripts_bypass_the_echo_hold_when_mic_cannot_answer() -> None:
    """Holding sys so a mic copy can win costs ~2.5s and discards the answer.

    The hold exists purely to let a mic copy beat the sys copy in the echo
    contest. When mic is context-only, mic winning means nothing is answered at
    all -- so the delay buys a strictly worse outcome.
    """
    controller = _consumer(hold=False)
    question = "What does your evaluation harness measure?"

    _drive_consume(controller, transcript(question, "sys"))

    assert controller.ingested == [question]  # type: ignore[attr-defined]
    assert not controller._pending_system


def test_system_transcripts_are_still_held_when_mic_answers_too() -> None:
    controller = _consumer(hold=True)
    question = "What does your evaluation harness measure?"

    _drive_consume(controller, transcript(question, "sys"))

    assert controller.ingested == []  # type: ignore[attr-defined]
    assert [p.transcript.text for p in controller._pending_system] == [question]
