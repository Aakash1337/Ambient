from __future__ import annotations

from pathlib import Path

import pytest

from ambientqa.knowledge import (
    KnowledgeIndex,
    _TOKEN_RE,
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


def test_unclassified_exact_authored_phrasings_remain_cacheable() -> None:
    raw = """# Scenarios

## Design a keyless deployment pipeline.
Aliases: keyless deployment design
Use OIDC federation and short-lived credentials.
"""
    index = _index(raw)
    canonical = index.lookup(
        "Design a keyless deployment pipeline!", threshold=0.5, min_words=3
    )
    alias = index.lookup("keyless deployment design", threshold=0.5, min_words=3)
    assert canonical is not None
    assert alias is not None


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


def test_grounding_returns_exact_reference_blocks() -> None:
    index = _index(IAM_DOC)
    grounded = index.grounding("IAM users versus roles", k=2)
    assert len(grounded) == 1
    assert all(block.startswith("Q: ") and "\nA: " in block for block in grounded)


def test_grounding_excludes_matches_below_the_minimum_score() -> None:
    index = _index(IAM_DOC)
    assert index.grounding("cafeteria role", k=3, min_score=0.5) == []
    assert index.grounding("iam role permission boundary", k=1, min_score=0.2) == []
    assert index.grounding("IAM users versus roles", k=1, min_score=0.2)


@pytest.mark.parametrize(
    "query",
    [
        "What is the role of a product manager?",
        "What is identity in philosophy?",
        "Can you explain retrieval evaluation?",
        "What is an AWS role?",
        "What is a security role?",
        "What is the difference between these two?",
    ],
)
def test_grounding_rejects_single_generic_word_overlap(query: str) -> None:
    index = _index(IAM_DOC)
    assert index.grounding(query, k=3, min_score=0.2) == []


def test_grounding_preserves_one_token_exact_domain_term() -> None:
    index = _index("# IAM\n\n## What is IAM?\nIAM manages identities.\n")
    grounded = index.grounding("What is IAM?", k=1, min_score=0.3)
    assert grounded and "manages identities" in grounded[0]


@pytest.mark.parametrize(
    "query",
    [
        "What is the first one?",
        "What is different about the first one?",
        "How does the first one work?",
        "Which one should I use?",
    ],
)
def test_shipped_pack_does_not_ground_unresolved_referents(query: str) -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    assert pack.grounding(query, k=3, min_score=0.3) == []


def test_shipped_pack_single_term_grounding_does_not_add_tangents() -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    grounded = pack.grounding("What is IAM?", k=3, min_score=0.3)
    assert [block.splitlines()[0] for block in grounded] == ["Q: What is IAM?"]


@pytest.mark.parametrize(
    "query",
    [
        "What is an IAM role?",
        "What is a security group?",
        "What is OIDC?",
        "How do IAM permissions work?",
        "How do IAM policies work?",
        "Explain access control.",
        "Tell me about role based access control.",
    ],
)
def test_shipped_pack_generic_or_conflicting_queries_are_never_cached(
    query: str,
) -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    assert pack.lookup(query, threshold=0.66, min_words=3) is None


@pytest.mark.parametrize(
    "query",
    [
        "How do you block public access?",
        "How do I delete a KMS key?",
    ],
)
def test_shipped_pack_broad_queries_cannot_subset_match_cache(query: str) -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    assert pack.lookup(query, threshold=0.66, min_words=3) is None


@pytest.mark.parametrize(
    "query",
    [
        "What is consulting?",
        "What is an incident?",
        "What is HIPAA?",
        "What is background?",
        "What is the best?",
        "Could you tell me what HIPAA is?",
        "Can you tell me what HIPAA is?",
        "Please tell me what HIPAA is.",
        "Would you tell me about HIPAA?",
        "Does HIPAA work?",
        "Could consulting work?",
        "Can you tell me about consulting?",
        "May HIPAA work?",
        "Might HIPAA work?",
        "Shall HIPAA work?",
        "Was HIPAA?",
        "Were HIPAA?",
        "May we use HIPAA?",
        "Shall we use HIPAA?",
        "HIPAA, can you explain?",
        "HIPAA, what is it?",
        "HIPAA? What does that mean?",
        "Consulting: what does that mean?",
        "Incident, what is that?",
        "Just tell me HIPAA please.",
        "How does an incident work?",
        "How do incidents work?",
        "Does best work?",
        "Which KMS key should I use?",
        "Which role should I use?",
        "Which PrivateLink should I use?",
    ],
)
def test_shipped_pack_rejects_intent_collisions_and_unresolved_choices(
    query: str,
) -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    assert pack.lookup(query, threshold=0.66, min_words=3) is None
    assert pack.grounding(query, k=3, min_score=0.3) == []


@pytest.mark.parametrize(
    "query",
    [
        "What is role based access control?",
        "What is Kubernetes RBAC?",
        "What does public access mean?",
        "What is key based access control?",
    ],
)
def test_shipped_pack_conflicting_or_broad_queries_are_never_grounded(
    query: str,
) -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    assert pack.grounding(query, k=3, min_score=0.3) == []


def test_shipped_pack_abac_alias_grounds_the_correct_entry() -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    grounded = pack.grounding(
        "What is attribute based access control?", k=3, min_score=0.3
    )
    assert len(grounded) == 1
    assert "Attribute-based access control" in grounded[0]


def test_shipped_pack_still_caches_exact_subject_and_alias_phrasings() -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    iam = pack.lookup("What is IAM?", threshold=0.66, min_words=3)
    roles = pack.lookup(
        "IAM users versus roles", threshold=0.66, min_words=3
    )
    assert iam is not None and iam.entry.question == "What is IAM?"
    assert roles is not None and "IAM role instead of an IAM user" in roles.entry.question
    incident = pack.lookup(
        "Walk me through an incident", threshold=0.66, min_words=3
    )
    assert incident is not None
    assert "security incident you led" in incident.entry.question


def test_shipped_authored_phrasings_never_resolve_to_a_different_answer() -> None:
    pack = load_pack(
        Path(__file__).parents[1] / "knowledge" / "aws-security-architect"
    )
    hits = 0
    for entry in pack.entries:
        for phrasing in (entry.question, *entry.aliases):
            if len(_TOKEN_RE.findall(phrasing)) < 3:
                continue
            hit = pack.lookup(phrasing, threshold=0.66, min_words=3)
            if hit is None:
                # Ambiguous or intentionally fail-closed phrasings go live.
                continue
            hits += 1
            assert hit.entry.answer == entry.answer, phrasing
    assert hits > 1000


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
