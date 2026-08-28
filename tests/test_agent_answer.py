from __future__ import annotations

import asyncio

from ambientqa.answer import ClaudeAnswerer
from ambientqa.config import AnswerConfig
from ambientqa.profile import Profile


def agent_profile() -> Profile:
    return Profile(
        "Customer service",
        "Account and billing support",
        "No live account tools are connected.",
        [],
        "",
        interaction="agent",
        customer_channel="mic",
        greeting="Hello! How can I help?",
    )


def test_agent_style_is_direct_courteous_and_tts_friendly() -> None:
    answerer = ClaudeAnswerer(
        AnswerConfig(style="cue", max_words=70),
        profile=agent_profile(),
    )

    prompt = answerer.system_prompt_for("agent")
    lowered = prompt.casefold()

    assert "active participant" in lowered
    assert "warm, patient, respectful" in lowered
    assert "never mock" in lowered
    assert "never claim to be human" in lowered
    assert "one clear question at a time" in lowered
    assert "no more than 55 words" in lowered
    assert "never use headings, bullets, markdown" in lowered
    assert "no live account tools are connected" in lowered


def test_agent_prompt_treats_short_reply_as_active_dialogue_on_either_channel() -> None:
    answerer = ClaudeAnswerer(AnswerConfig())
    history = [
        (
            "The app keeps signing me out.",
            "I'm sorry about that. Does it happen immediately after sign-in?",
        )
    ]

    mic = answerer._prompt(
        "Yes.",
        ["[mic] The app keeps signing me out."],
        history=history,
        channel="mic",
        style="agent",
    )
    sys = answerer._prompt(
        "Yes.",
        ["[sys] The app keeps signing me out."],
        history=history,
        channel="sys",
        style="agent",
    )

    for prompt in (mic, sys):
        assert "RECENT SPEAKER/AMBIENT TURNS" in prompt
        assert "Does it happen immediately" in prompt
        assert "Resolve short replies" in prompt
        assert "active AI conversational agent" in prompt
        assert "SPEAKER'S LATEST TURN:\nYes." in prompt
        assert "QUESTION TO ANSWER" not in prompt
        assert "Coach your user's" not in prompt
        assert "NEVER answer in first person as that addressee" not in prompt


def test_agent_profile_does_not_silently_change_assist_answer_style() -> None:
    answerer = ClaudeAnswerer(AnswerConfig(style="cue"), profile=agent_profile())

    assert "cue card" in answerer.system_prompt.casefold()
    assert "active participant" not in answerer.system_prompt.casefold()


def test_agent_style_adapts_to_cyber_profile_without_support_role_wording() -> None:
    cyber = Profile(
        "Cybersecurity analytics",
        "Defensive cybersecurity analytics",
        "Rank behavioral hypotheses and identify the next telemetry to inspect.",
        ["MITRE ATT&CK", "EDR"],
        "",
    )
    answerer = ClaudeAnswerer(AnswerConfig(max_words=70), profile=cyber)

    system = answerer.system_prompt_for("agent")
    prompt = answerer._prompt(
        "Word spawned PowerShell and created a scheduled task.",
        [],
        style="agent",
        channel="mic",
    )

    assert "Defensive cybersecurity analytics" in system
    assert "active knowledge profile" in system
    assert "customer-support" not in system.casefold()
    assert "active AI conversational agent" in prompt
    assert "SPEAKER'S LATEST TURN" in prompt


def test_agent_answer_is_run_through_courtesy_guard(monkeypatch) -> None:
    class Process:
        returncode = 0

        async def communicate(self, input: bytes | None = None):
            return b"Obviously, that's your problem.", b""

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode

    async def fake_create(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    answerer = ClaudeAnswerer(AnswerConfig(stream=False))

    result = asyncio.run(
        answerer.answer("turn-1", "My order never arrived.", [], style="agent")
    )

    assert result.status == "ok"
    assert "your problem" not in result.answer.casefold()
    assert "I'm here to help" in result.answer
