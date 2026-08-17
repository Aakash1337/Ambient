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
