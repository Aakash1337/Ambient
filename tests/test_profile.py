from __future__ import annotations

from pathlib import Path

from ambientqa.answer import ClaudeAnswerer
from ambientqa.config import AnswerConfig, GateConfig, STTConfig
from ambientqa.gate import OllamaGate
from ambientqa.profile import Profile, load_profile
from ambientqa.stt import WhisperTranscriber


def test_parses_all_sections_case_insensitively(tmp_path: Path) -> None:
    path = tmp_path / "profile.md"
    raw = """# Platform interview

## TOPIC
Distributed systems

## background
Python backend engineer

## Vocabulary
FastAPI, Starlette
"""
    path.write_text(raw, encoding="utf-8")
    profile = load_profile(path)
    assert profile is not None
    assert profile.name == "Platform interview"
    assert profile.topic == "Distributed systems"
    assert profile.background == "Python backend engineer"
    assert profile.vocabulary == ["FastAPI", "Starlette"]
    assert profile.raw == raw


def test_missing_sections_are_optional(tmp_path: Path) -> None:
    path = tmp_path / "topic-only.md"
    path.write_text("## Topic\nDatabases\n", encoding="utf-8")
    profile = load_profile(path)
    assert profile is not None
    assert profile.name == "topic-only"
    assert profile.topic == "Databases"
    assert profile.background == ""
    assert profile.vocabulary == []


def test_unknown_sections_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "extra.md"
    path.write_text(
        "## Topic\nNetworking\n## Secret Sauce\nignore me\n## Background\nSRE\n",
        encoding="utf-8",
    )
    profile = load_profile(path)
    assert profile is not None
    assert profile.topic == "Networking"
    assert profile.background == "SRE"
    assert "ignore me" not in profile.topic


def test_empty_file_degrades_to_no_profile_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    warnings: list[str] = []
    assert load_profile(path, warnings.append) is None
    assert "empty" in warnings[0].lower()


def test_missing_file_degrades_to_no_profile_with_warning(tmp_path: Path) -> None:
    warnings: list[str] = []
    assert load_profile(tmp_path / "missing.md", warnings.append) is None
    assert "unavailable" in warnings[0].lower()


def test_no_recognised_sections_degrades_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "unknown.md"
    path.write_text("# Title\n## Notes\nNothing actionable\n", encoding="utf-8")
    warnings: list[str] = []
    assert load_profile(path, warnings.append) is None
    assert "no recognised sections" in warnings[0].lower()


def test_unicode_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "unicode.md"
    path.write_text(
        "# Café prep\n## Topic\nArquitectura en São Paulo ☁\n"
        "## Vocabulary\nPydantic, Überprüfung\n",
        encoding="utf-8",
    )
    profile = load_profile(path)
    assert profile is not None
    assert profile.name == "Café prep"
    assert "São Paulo" in profile.topic
    assert profile.vocabulary == ["Pydantic", "Überprüfung"]


def test_vocabulary_splits_on_commas_and_newlines(tmp_path: Path) -> None:
    path = tmp_path / "vocab.md"
    path.write_text(
        "## Vocabulary\nBedrock, Guardrails\nPrivateLink\n- KMS, IAM\n",
        encoding="utf-8",
    )
    profile = load_profile(path)
    assert profile is not None
    assert profile.vocabulary == [
        "Bedrock",
        "Guardrails",
        "PrivateLink",
        "KMS",
        "IAM",
    ]


def test_clearing_profile_removes_influence_from_all_three_stages() -> None:
    profile = Profile(
        "AWS",
        "Unique Amazon Bedrock topic",
        "Unique FastAPI background",
        ["Bedrock"],
        "",
    )
    transcriber = WhisperTranscriber(STTConfig(), profile=profile)
    gate = OllamaGate(GateConfig(), profile=profile)
    answerer = ClaudeAnswerer(AnswerConfig(), profile=profile)

    transcriber.set_profile(None)
    gate.set_profile(None)
    answerer.set_profile(None)

    assert transcriber.profile is None
    assert profile.topic not in gate.system_prompt
    assert profile.topic not in answerer.system_prompt
    assert profile.background not in answerer.system_prompt


def test_agent_profile_parses_explicit_interaction_channel_and_greeting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "customer-agent.md"
    path.write_text(
        "# Customer support\n"
        "## Interaction\nAgent\n"
        "## Customer Channel\nSYS\n"
        "## Greeting\nHello!\nThanks for calling.\n"
        "## Topic\nAccount support\n",
        encoding="utf-8",
    )

    profile = load_profile(path)

    assert profile is not None
    assert profile.interaction == "agent"
    assert profile.is_agent is True
    assert profile.customer_channel == "sys"
    assert profile.greeting == "Hello! Thanks for calling."


def test_ordinary_profile_defaults_to_assist_on_mic(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.md"
    path.write_text("## Topic\nNetworking\n", encoding="utf-8")

    profile = load_profile(path)

    assert profile is not None
    assert profile.interaction == "assist"
    assert profile.is_agent is False
    assert profile.customer_channel == "mic"
    assert profile.greeting == ""


def test_invalid_agent_metadata_fails_safe_with_warnings(tmp_path: Path) -> None:
    path = tmp_path / "invalid.md"
    path.write_text(
        "## Interaction\nautonomous\n## Customer Channel\nboth\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    profile = load_profile(path, warnings.append)

    assert profile is not None
    assert profile.interaction == "assist"
    assert profile.customer_channel == "mic"
    assert len(warnings) == 2
    assert "interaction" in warnings[0].casefold()
    assert "customer channel" in warnings[1].casefold()


def test_scope_defaults_to_open_and_parses_lens(tmp_path: Path) -> None:
    default_path = tmp_path / "default.md"
    default_path.write_text("## Topic\nNetworking\n", encoding="utf-8")
    assert load_profile(default_path).scope == "open"

    lens_path = tmp_path / "lens.md"
    lens_path.write_text("## Scope\nlens\n## Topic\nCybersecurity\n", encoding="utf-8")
    assert load_profile(lens_path).scope == "lens"


def test_invalid_scope_fails_safe_to_open_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "bad-scope.md"
    path.write_text("## Scope\nnarrow\n## Topic\nNetworking\n", encoding="utf-8")
    warnings: list[str] = []

    profile = load_profile(path, warnings.append)

    assert profile is not None
    assert profile.scope == "open"
    assert any("scope" in message.casefold() for message in warnings)


def test_knowledge_section_parses_pack_path(tmp_path: Path) -> None:
    path = tmp_path / "with-pack.md"
    path.write_text(
        "## Knowledge\nknowledge/aws-security-architect\n## Topic\nAWS\n",
        encoding="utf-8",
    )
    assert load_profile(path).knowledge == "knowledge/aws-security-architect"

    no_pack = tmp_path / "no-pack.md"
    no_pack.write_text("## Topic\nAWS\n", encoding="utf-8")
    assert load_profile(no_pack).knowledge == ""


def test_existing_five_argument_profile_constructor_stays_compatible() -> None:
    profile = Profile("Legacy", "Topic", "Background", ["term"], "raw")

    assert profile.interaction == "assist"
    assert profile.customer_channel == "mic"
    assert profile.greeting == ""
    assert profile.scope == "open"
    assert profile.knowledge == ""
