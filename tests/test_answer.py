from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from ambientqa.answer import ClaudeAnswerer
from ambientqa.config import AnswerConfig


def _delta(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text},
                },
            }
        ).encode()
        + b"\n"
    )


def _event(event: dict[str, object]) -> bytes:
    return json.dumps(event).encode() + b"\n"


class FakeStream:
    def __init__(
        self,
        lines: list[bytes] | None = None,
        *,
        data: bytes = b"",
        delay: float = 0.0,
        hang: bool = False,
    ) -> None:
        self.lines = deque(lines or [])
        self.data = data
        self.delay = delay
        self.hang = hang

    async def readline(self) -> bytes:
        if self.hang:
            await asyncio.Event().wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.lines.popleft() if self.lines else b""

    async def read(self) -> bytes:
        if self.hang:
            await asyncio.Event().wait()
        return self.data


class FakeProcess:
    def __init__(
        self,
        lines: list[bytes] | None = None,
        *,
        stdout: bytes = b"Direct answer.",
        stderr: bytes = b"",
        delay: float = 0.0,
        hang: bool = False,
    ) -> None:
        self.returncode = None
        self.stdout = FakeStream(lines, delay=delay, hang=hang)
        self.stderr = FakeStream(data=stderr)
        self.communicate_stdout = stdout
        self.communicate_stderr = stderr
        self.hang = hang
        self.killed = False
        self.communicate_called = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_called = True
        if self.hang:
            await asyncio.Event().wait()
        self.returncode = 0
        return self.communicate_stdout, self.communicate_stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -1

    async def wait(self) -> int:
        if self.hang and self.returncode is None:
            await asyncio.Event().wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_uses_one_shot_cli_with_required_isolation(monkeypatch) -> None:
    seen = []
    process = FakeProcess([_delta("Direct "), _delta("answer.")])

    async def fake_create(*args, **kwargs):
        seen.append((args, kwargs))
        return process

    deltas: list[tuple[str, str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        ClaudeAnswerer(
            AnswerConfig(),
            delta_callback=lambda question_id, text: deltas.append(
                (question_id, text)
            ),
        ).answer(
            "q1", "What is WASAPI?", ["[mic] We were discussing Windows audio."]
        )
    )

    assert result.question_id == "q1"
    assert result.question == "What is WASAPI?"
    assert result.answer == "Direct answer."
    assert result.status == "ok"
    assert result.latency_ms >= 0
    assert result.timestamp > 0
    assert deltas == [("q1", "Direct "), ("q1", "answer.")]
    assert len(seen) == 1
    args = seen[0][0]
    assert args[0:2] == ("claude", "-p")
    assert "--input-format" not in args
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in args
    assert "--verbose" in args
    assert args[args.index("--allowed-tools") + 1] == ""
    assert args[args.index("--strict-mcp-config") + 1] == "--mcp-config"
    assert json.loads(args[args.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "BACKGROUND TRANSCRIPT" in args[2]


def test_streamed_deltas_match_non_streaming_answer(monkeypatch) -> None:
    processes = deque(
        [
            FakeProcess([_delta("Same "), _delta("final answer.")]),
            FakeProcess(stdout=b"Same final answer."),
        ]
    )

    async def fake_create(*_args, **_kwargs):
        return processes.popleft()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    async def drive():
        streamed = await ClaudeAnswerer(AnswerConfig(stream=True)).answer(
            "stream", "Question?", []
        )
        buffered = await ClaudeAnswerer(AnswerConfig(stream=False)).answer(
            "buffered", "Question?", []
        )
        return streamed, buffered

    streamed, buffered = asyncio.run(drive())
    assert streamed.answer == buffered.answer == "Same final answer."
    assert streamed.status == buffered.status == "ok"


def test_stream_without_parseable_text_falls_back_to_raw_stdout(monkeypatch) -> None:
    raw = b'{"type":"system","subtype":"init"}\n'

    async def fake_create(*_args, **_kwargs):
        return FakeProcess([raw])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        ClaudeAnswerer(AnswerConfig()).answer("q1", "Question?", [])
    )
    assert result.answer == raw.decode().strip()
    assert result.status == "ok"


def test_malformed_json_is_skipped_without_losing_good_deltas(monkeypatch) -> None:
    raw_lines = [
        b"not json at all\n",
        _delta("Still "),
        b"{broken\n",
        _delta("works."),
    ]
    process = FakeProcess(raw_lines)
    deltas: list[str] = []

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        ClaudeAnswerer(
            AnswerConfig(),
            delta_callback=lambda _question_id, text: deltas.append(text),
        ).answer("q1", "Question?", [])
    )
    # Parseable deltas still reach the UI, but final fallback retains every byte
    # because a malformed stream must never silently lose answer content.
    assert "".join(deltas) == "Still works."
    assert result.answer == b"".join(raw_lines).decode().strip()
    assert result.status == "ok"


def test_unknown_event_types_are_ignored(monkeypatch) -> None:
    process = FakeProcess(
        [
            _event({"type": "future_metadata", "text": "do not include me"}),
            _delta("Known text."),
        ]
    )

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        ClaudeAnswerer(AnswerConfig()).answer("q1", "Question?", [])
    )
    assert result.answer == "Known text."


def test_complete_assistant_event_is_used_when_no_deltas_arrive(monkeypatch) -> None:
    process = FakeProcess(
        [
            _event(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Complete fallback."}]
                    },
                }
            )
        ]
    )

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        ClaudeAnswerer(AnswerConfig()).answer("q1", "Question?", [])
    )
    assert result.answer == "Complete fallback."
    assert result.status == "ok"


def test_timeout_kills_process(monkeypatch) -> None:
    process = FakeProcess(hang=True)

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    config = AnswerConfig(answer_timeout_s=0.001)
    result = asyncio.run(ClaudeAnswerer(config).answer("q1", "Question?", []))
    assert result.status == "timed_out"
    assert result.answer == "timed out"
    assert process.killed
    assert process.returncode == -1


def test_cancellation_kills_process_and_reraises(monkeypatch) -> None:
    process = FakeProcess(hang=True)
    created = None

    async def drive() -> None:
        nonlocal created
        created = asyncio.Event()

        async def fake_create(*_args, **_kwargs):
            created.set()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
        task = asyncio.create_task(
            ClaudeAnswerer(AnswerConfig()).answer("q1", "Question?", [])
        )
        await created.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())
    assert process.killed
    assert process.returncode == -1


def test_two_concurrent_streams_do_not_mix_deltas(monkeypatch) -> None:
    async def fake_create(*args, **_kwargs):
        prompt = args[2]
        if "First question?" in prompt:
            return FakeProcess(
                [_delta("First "), _delta("answer.")],
                delay=0.002,
            )
        return FakeProcess(
            [_delta("Second "), _delta("answer.")],
            delay=0.001,
        )

    received: dict[str, list[str]] = {"q1": [], "q2": []}
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    answerer = ClaudeAnswerer(
        AnswerConfig(max_concurrent=2),
        delta_callback=lambda question_id, text: received[question_id].append(text),
    )

    async def drive():
        return await asyncio.gather(
            answerer.answer("q1", "First question?", []),
            answerer.answer("q2", "Second question?", []),
        )

    first, second = asyncio.run(drive())
    assert first.answer == "First answer."
    assert second.answer == "Second answer."
    assert "".join(received["q1"]) == first.answer
    assert "".join(received["q2"]) == second.answer
    assert answerer.in_flight == 0


def test_stream_false_restores_buffered_communicate_path(monkeypatch) -> None:
    process = FakeProcess(stdout=b"Buffered answer.")
    seen = []
    deltas = []

    async def fake_create(*args, **kwargs):
        seen.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    result = asyncio.run(
        ClaudeAnswerer(
            AnswerConfig(stream=False),
            delta_callback=lambda question_id, text: deltas.append(
                (question_id, text)
            ),
        ).answer("q1", "Question?", [])
    )
    args = seen[0][0]
    assert "--output-format" not in args
    assert "--include-partial-messages" not in args
    assert "--verbose" not in args
    assert "--input-format" not in args
    assert process.communicate_called
    assert not deltas
    assert result.answer == "Buffered answer."
    assert result.status == "ok"
