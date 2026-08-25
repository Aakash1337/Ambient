from __future__ import annotations

from pathlib import Path

from ambientqa.knowledge import (
    KnowledgeIndex,
    _content_tokens,
    _parse_entries,
    load_pack,
)


def _index(raw: str, source: str = "doc") -> KnowledgeIndex:
    return KnowledgeIndex(entries=_parse_entries(raw, source), doc_count=1)


IAM_DOC = """# IAM & Identity

## What is the difference between an IAM user and an IAM role?
Aliases: iam user vs role | when do you use a role instead of a user
Tags: iam, identity, roles, sts

A user is a permanent identity with long-lived keys; a role is assumed
temporarily and hands out short-lived credentials through STS.

• Users: people, static keys
• Roles: workloads and federation, temp creds
• Prefer roles, avoid long-lived keys

## What is a permission boundary?
Tags: iam, permissions, boundary

It's a managed policy that caps the maximum permissions an identity can ever
have, even if its own policies grant more.

• Ceiling, not a grant
• Delegates safe self-service IAM
• Effective perms = boundary ∩ policy
"""


def test_parses_title_question_aliases_tags_and_answer() -> None:
    entries = _parse_entries(IAM_DOC, "iam-identity")
    assert len(entries) == 2
    first = entries[0]
    assert first.topic == "IAM & Identity"
    assert first.source == "iam-identity"
    assert first.question.startswith("What is the difference")
    assert first.aliases == (
        "iam user vs role",
        "when do you use a role instead of a user",
    )
    assert first.tags == ("iam", "identity", "roles", "sts")
    assert first.answer.startswith("A user is a permanent identity")
    assert "• Prefer roles" in first.answer


def test_paraphrase_matches_above_threshold() -> None:
    index = _index(IAM_DOC)
    hit = index.lookup("Can you explain IAM roles versus users?", threshold=0.5)
    assert hit is not None
    assert hit.entry.question.startswith("What is the difference")
    assert hit.score >= 0.5


def test_unrelated_question_does_not_match() -> None:
    index = _index(IAM_DOC)
    assert index.lookup("How does GuardDuty detect threats?", threshold=0.5) is None


def test_min_words_guard_counts_raw_words_and_rejects_short_queries() -> None:
    index = _index(IAM_DOC)
    # Two raw words: too thin to safely resolve from cache.
    assert index.lookup("iam roles", threshold=0.4, min_words=4) is None
    # A full question clears the guard and still matches.
    assert (
        index.lookup(
            "what is an iam role versus a user", threshold=0.5, min_words=4
        )
        is not None
    )


def test_alias_drives_a_match_the_canonical_would_miss() -> None:
    index = _index(IAM_DOC)
    hit = index.lookup("when do you use a role instead of a user", threshold=0.6)
    assert hit is not None
    assert hit.entry.question.startswith("What is the difference")


def test_ambiguous_near_tie_between_different_answers_is_rejected() -> None:
    raw = """# Duplicated topic

## Explain encryption at rest options
Encrypt data on disk with KMS-managed keys.

## Explain encryption at rest choices
Use SSE-KMS or SSE-S3 depending on control needs.
"""
    index = _index(raw)
    # Both entries score almost identically for this query but carry different
    # answers, so the safe move is to fall through to the live model.
    assert index.lookup("explain encryption at rest", threshold=0.5) is None


def test_identical_answers_do_not_trigger_ambiguity_rejection() -> None:
    raw = """# Same answer twice

## What is least privilege?
Grant only the permissions a workload actually needs.

## Explain the principle of least privilege
Grant only the permissions a workload actually needs.
"""
    index = _index(raw)
    hit = index.lookup("what does least privilege mean", threshold=0.5)
    assert hit is not None
    assert hit.answer.startswith("Grant only the permissions")


def test_note_lines_inside_the_answer_are_not_parsed_as_metadata() -> None:
    raw = """# Topic

## What is Amazon Macie?
Macie discovers and classifies sensitive data in S3.

Note: it is region-scoped.
"""
    entries = _parse_entries(raw, "doc")
    assert entries[0].tags == ()
    assert "Note: it is region-scoped." in entries[0].answer


def test_grounding_returns_top_k_reference_blocks() -> None:
    index = _index(IAM_DOC)
    grounded = index.grounding("iam role permission boundary", k=2)
    assert len(grounded) == 2
    assert all(block.startswith("Q: ") and "\nA: " in block for block in grounded)


def test_malformed_entries_are_skipped_not_fatal() -> None:
    raw = """# Topic

## A heading with no answer body

## What is CloudTrail?
It records API activity across the account for audit.
"""
    entries = _parse_entries(raw, "doc")
    assert [entry.question for entry in entries] == ["What is CloudTrail?"]


def test_content_tokens_strip_filler_but_keep_domain_words() -> None:
    tokens = _content_tokens("What is the difference between a role and a user?")
    assert "difference" in tokens
    assert "between" in tokens
    assert "role" in tokens
    assert "what" not in tokens
    assert "the" not in tokens


def test_load_pack_missing_directory_degrades_to_empty(tmp_path: Path) -> None:
    warnings: list[str] = []
    index = load_pack(tmp_path / "nope", warnings.append)
    assert not index
    assert index.lookup("anything", threshold=0.1) is None
    assert "not found" in warnings[0].lower()


def test_load_pack_reads_every_markdown_document(tmp_path: Path) -> None:
    (tmp_path / "iam.md").write_text(IAM_DOC, encoding="utf-8")
    (tmp_path / "detect.md").write_text(
        "# Detection\n\n## What is GuardDuty?\n"
        "Managed threat detection over CloudTrail, VPC, and DNS logs.\n",
        encoding="utf-8",
    )
    messages: list[str] = []
    index = load_pack(tmp_path, messages.append)
    assert index.doc_count == 2
    assert len(index.entries) == 3
    assert index.lookup("what is guardduty", threshold=0.5) is not None
    assert any("loaded" in message for message in messages)


def test_load_pack_with_no_entries_degrades_to_empty(tmp_path: Path) -> None:
    (tmp_path / "empty.md").write_text("# Just a title, no entries\n", encoding="utf-8")
    warnings: list[str] = []
    assert not load_pack(tmp_path, warnings.append)
    assert any("no usable entries" in message for message in warnings)
