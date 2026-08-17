from __future__ import annotations

import pytest

from ambientqa.answer import ClaudeAnswerer
from ambientqa.config import AnswerConfig, default_config, validate_config
from ambientqa.profile import Profile
from ambientqa.ui import plain_text


def test_interview_prompt_forbids_markdown_and_lists() -> None:
    prompt = ClaudeAnswerer(AnswerConfig(style="interview", max_words=70)).system_prompt
    lowered = prompt.lower()
    assert "interview" in lowered
    assert "bullet" in lowered and "markdown" in lowered
    assert "70" in prompt
    # Must not degenerate into the terse keyword-list behaviour.
    assert "terse" not in lowered


def test_interview_prompt_demands_brevity_not_an_essay() -> None:
    """The style is a spoken answer, not a written explainer.

    Both failure modes have been hit in practice: a hard word cap produced a
    comma-jammed keyword list, and 'explain each point' produced a 219-word,
    multi-paragraph essay. The prompt must rule out the essay explicitly.
    """
    lowered = ClaudeAnswerer(AnswerConfig(style="interview", max_words=70)).system_prompt.lower()
    assert "two to four sentences" in lowered
    # No trailing wrap-up paragraph, which is what made answers read as essays.
    assert "no closing summary" in lowered
    # A worked example anchors the target length far better than adjectives.
    assert "bedrock" in lowered
    assert "speaks" in lowered or "speech" in lowered


def test_terse_style_still_available() -> None:
    prompt = ClaudeAnswerer(AnswerConfig(style="terse", max_words=60)).system_prompt
    assert "terse" in prompt.lower()


def test_profile_topic_and_background_do_not_change_interview_style_rules() -> None:
    profile = Profile(
        "AWS",
        "Amazon Bedrock and GenAI services",
        "Backend engineer with 3 years of Python and FastAPI",
        ["Bedrock"],
        "",
    )
    prompt = ClaudeAnswerer(
        AnswerConfig(style="interview", max_words=70),
        profile=profile,
    ).system_prompt
    lowered = prompt.lower()
    assert profile.topic in prompt
    assert profile.background in prompt
    assert "two to four sentences" in lowered
    assert "no closing summary" in lowered
    assert "markdown" in lowered


def test_invalid_style_rejected() -> None:
    config = default_config()
    config.answer.style = "chatty"
    with pytest.raises(ValueError, match="answer.style"):
        validate_config(config)


# --- the reported display bug: literal ** appearing in the pane ---


def test_bold_markers_are_removed() -> None:
    raw = "Core components: **Path operations** (routes), **Pydantic models** for validation."
    out = plain_text(raw)
    assert "**" not in out
    assert "Path operations" in out and "Pydantic models" in out


def test_inline_code_backticks_removed() -> None:
    assert plain_text("Use `AbortSignal.timeout(ms)` here.") == "Use AbortSignal.timeout(ms) here."


def test_headings_bullets_and_links_flattened() -> None:
    raw = "# Title\n- first point\n* second point\nSee [the docs](https://example.com)."
    out = plain_text(raw)
    assert "#" not in out
    assert not any(line.startswith(("-", "*")) for line in out.splitlines())
    assert "the docs" in out and "example.com" not in out


def test_plain_prose_is_untouched() -> None:
    prose = "FastAPI is built on Starlette, Pydantic, and Python type hints."
    assert plain_text(prose) == prose


def test_multiplication_and_underscores_survive() -> None:
    # Italic regex must not eat ordinary asterisks/underscores.
    assert plain_text("cost is 3 * 4 and snake_case_name stays") == (
        "cost is 3 * 4 and snake_case_name stays"
    )
