from __future__ import annotations

import pytest

from ambientqa.agent import (
    classify_agent_turn,
    guard_agent_answer,
    local_agent_reply,
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Hello!", "greeting"),
        ("Good morning.", "greeting"),
        ("Hi, how are you?", "greeting"),
        ("Are you there?", "greeting"),
        ("Can you hear me?", "greeting"),
        ("Thank you very much.", "thanks"),
        ("I really appreciate it.", "thanks"),
        ("Thanks, goodbye.", "goodbye"),
        ("Give me a second.", "hold"),
        ("Um, okay.", "filler"),
        ("Yes.", "content"),
        ("No, that didn't fix it.", "content"),
        ("Hello, my account is locked.", "content"),
        ("Thank you for checking, but it still fails.", "content"),
    ],
)
def test_classifies_complete_agent_turns(text: str, kind: str) -> None:
    assert classify_agent_turn(text) == kind


def test_local_replies_are_immediate_and_courteous() -> None:
    assert local_agent_reply("greeting") == (
        "Hello! I'm Ambient, an AI assistant. "
        "What would you like to work through today?"
    )
    assert local_agent_reply("greeting", "Welcome to Acme support. How can I help?") == (
        "Welcome to Acme support. How can I help?"
    )
    assert "very welcome" in (local_agent_reply("thanks") or "")
    assert "Take care" in (local_agent_reply("goodbye") or "")
    assert "Take your time" in (local_agent_reply("hold") or "")
    assert local_agent_reply("filler") is None
    assert local_agent_reply("content") is None


@pytest.mark.parametrize(
    "hostile",
    [
        "Obviously, you should know that already.",
        "Calm down and figure it out.",
        "That's your problem.",
        "You're an idiot.",
        "I don't care. Stop wasting my time.",
        "That's a stupid question.",
        "You're being unreasonable.",
    ],
)
def test_output_guard_replaces_plainly_hostile_answers(hostile: str) -> None:
    guarded = guard_agent_answer(hostile)
    assert guarded != hostile
    assert "I'm here to help" in guarded


def test_output_guard_preserves_useful_neutral_answer_and_flattens_for_tts() -> None:
    answer = "I can help with that.\n\nFirst, let's check the email on the account."
    assert guard_agent_answer(answer) == (
        "I can help with that. First, let's check the email on the account."
    )


def test_empty_output_fails_closed_to_a_polite_reply() -> None:
    guarded = guard_agent_answer("   ")
    assert "sorry" in guarded.casefold()
    assert "help" in guarded.casefold()
