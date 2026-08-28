"""Pre-answered knowledge packs for near-instant, grounded answers.

A knowledge pack is a directory of human-editable Markdown documents, one per
topic. Each ``##`` heading is one entry: a canonical question, optional alias
phrasings and tags, and a ready-to-read answer written in the same cue-card
shape the live answerer produces. At runtime a gated question is matched against
every entry by fast lexical similarity -- no model call, no network -- so an
anticipated question resolves in milliseconds instead of a full ``claude -p``
round trip. A weak match falls through to the live model, optionally with the
closest entries injected as authoritative reference so even the miss is faster
and better grounded.

The whole module is deliberately dependency-free and deterministic: it must load
during startup alongside the profile, run on the ordered consumer path, and never
be the thing that makes a live answer slow or flaky.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
_APOSTROPHE_TRANSLATION = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\uff07": "'"}
)
# Only these keys are honoured, and only as the first lines of an entry (before
# any answer prose). That keeps an ordinary "Note: ..." or "Tip: ..." line
# inside an answer from being silently eaten as metadata.
_META_RE = re.compile(r"^(aliases|tags)[ \t]*:[ \t]*(.*)$", re.IGNORECASE)

# Filler and question scaffolding carry no topic signal, so matching on them
# only invites false positives ("What is X?" matching "How do I do X?" purely on
# the shared "what/how/do"). Domain words -- including "difference", "between",
# "versus" -- are deliberately kept.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "to", "is", "are", "am", "be", "was", "were",
        "do", "does", "did", "you", "your", "yours", "i", "we", "they", "it",
        "in", "on", "at", "for", "and", "or", "me", "my", "our", "that", "this",
        "these", "those", "there", "here", "what", "whats", "how", "when",
        "why", "where", "who", "which", "can", "could", "would", "should",
        "will", "shall", "may", "might", "please", "so", "tell", "about",
        "give", "explain", "describe", "walk", "through", "us", "some", "any",
        "with", "as", "if", "then", "just", "kind", "sort", "s", "mean",
        "means", "work", "works", "working",
    }
)

# Comparison scaffolding helps rank canonical questions, but it is not evidence
# that a knowledge entry matches the user's subject. Grounding removes it before
# counting overlap so "What is the difference between these two?" cannot pull
# arbitrary comparison entries into a prompt.
_GROUNDING_SCAFFOLD = frozenset(
    {
        "another",
        "between",
        "choice",
        "different",
        "difference",
        "first",
        "former",
        "fourth",
        "from",
        "latter",
        "one",
        "option",
        "other",
        "same",
        "second",
        "third",
        "two",
        "use",
        "used",
        "using",
        "versus",
        "vs",
        "work",
        "working",
    }
)


# Cheap phrasing normalisation so a spoken paraphrase still matches a written
# canonical: "roles versus users" -> {role, vs, user}. Full stemming is overkill
# and risks merging distinct terms, so this only folds naive plurals and one
# common synonym, and leaves short acronyms (kms, sts, aws) untouched.
_SYNONYMS = {"versus": "vs"}

# These words refer to alternatives supplied by prior conversation rather than
# identifying a self-contained question.  A knowledge lookup has no reliable
# way to resolve them, and a wrong verbatim cue card is worse than the live-model
# fallback.  Keep this deliberately conservative; named comparisons still use
# explicit ``vs``/``versus``/``difference`` phrasing and remain cacheable.
_UNRESOLVED_REFERENCE_WORDS = frozenset(
    {
        "another",
        "either",
        "first",
        "former",
        "fourth",
        "latter",
        "one",
        "ones",
        "other",
        "second",
        "third",
        "which",
    }
)


def _intent(text: str) -> str | None:
    """Return a conservative question-intent class for cache safety.

    Content-token equality intentionally removes question scaffolding for
    ranking, but that makes ``What is HIPAA?`` look identical to a personal
    ``Tell me about your HIPAA work`` alias.  Intent is therefore checked as a
    separate dimension before an answer or authoritative reference is used.
    """
    compact = re.sub(
        r"\s+", " ", text.translate(_APOSTROPHE_TRANSLATION).strip().casefold()
    )
    words = [token.casefold() for token in _TOKEN_RE.findall(compact)]
    word_set = set(words)

    # Comparison markers are semantic, even when wrapped in "what is" or
    # "explain". They must win over those generic lead-ins.
    if word_set & {"compare", "comparison", "difference", "vs", "versus"}:
        return "comparison"
    if "between" in word_set and len(_content_tokens(text)) >= 2:
        return "comparison"

    if re.match(r"^(?:please )?(?:how|what|why|when) did you\b", compact):
        return "personal"
    if re.match(r"^(?:please )?(?:tell me about yourself|who are you)\b", compact):
        return "personal"
    if re.match(
        r"^(?:please )?(?:tell me about|describe|walk me through)\b.*\byou\b",
        compact,
    ):
        return "personal"
    if "yourself" in word_set or "your" in word_set:
        # A possessive technical ask can safely miss the cache and go live. The
        # strict classification prevents a personal story from answering a
        # generic definition merely because both share one subject word.
        return "personal"

    if re.match(
        r"^(?:please )?(?:what(?:'s| is| are)\b|what (?:does|do)\b.*\bmean\b|"
        r"define\b|(?:can|could|would) you (?:explain|tell me (?:about|what))\b|"
        r"explain\b|tell me (?:about|what)\b)",
        compact,
    ):
        return "definition"
    if re.match(r"^(?:please )?why\b", compact):
        return "reason"
    if re.match(r"^(?:please )?who\b", compact):
        return "actor"
    if re.match(r"^(?:please )?where\b", compact):
        return "location"
    if re.match(r"^(?:please )?when\b", compact):
        return "timing"
    if re.match(r"^(?:please )?(?:how\b|walk me through\b)", compact):
        return "procedure"
    if re.match(
        r"^(?:please )?(?:is|are|am|was|were|do|does|did|can|could|would|"
        r"should|will|shall|may|might|have|has|had|what|how|why|when|where|"
        r"who|which)\b",
        compact,
    ):
        # An interrogative whose semantics are not one of the safe classes
        # above must not inherit a personal/story answer through a shorthand
        # alias. It can still match an explicitly authored question alias.
        return "question"
    return None


def _has_unresolved_reference(text: str) -> bool:
    words = {
        token.casefold()
        for token in _TOKEN_RE.findall(text.translate(_APOSTROPHE_TRANSLATION))
    }
    return bool(words & _UNRESOLVED_REFERENCE_WORDS)


def _compatible_exact_phrasing(
    query: str,
    query_tokens: frozenset[str],
    entry: "KnowledgeEntry",
    *,
    grounding: bool = False,
) -> bool:
    """Require one exact subject match whose intent agrees with the query."""
    query_intent = _intent(query)
    canonical_intent = _intent(entry.question)
    phrasings = (
        (entry.question, entry._question_tokens),
        *zip(entry.aliases, entry._alias_tokens),
    )
    for phrase, tokens in phrasings:
        comparable = tokens - _GROUNDING_SCAFFOLD if grounding else tokens
        if query_tokens != comparable:
            continue
        if query_intent is None:
            # The gate can pass topic-first spoken questions whose intent words
            # arrive late ("HIPAA, what does that mean?"). Treating an unknown
            # intent as a wildcard lets those collide with personal-story
            # aliases. Cache performance is optional; an unclassified form must
            # take the safe live path.
            if _surface_form(query) == _surface_form(phrase):
                return True
            continue
        phrase_intent = _intent(phrase)
        if query_intent == canonical_intent:
            return True
        if (
            query_intent == phrase_intent
            and _surface_form(query) == _surface_form(phrase)
        ):
            # An alias may deliberately offer a different framing from its
            # canonical (for example, a procedure-shaped interview prompt for
            # a personal incident story). Trust that cross-intent bridge only
            # for the exact authored alias, never for a same-subject paraphrase.
            return True
    return False


def _normalize_token(token: str) -> str:
    token = token.replace("'", "")
    token = _SYNONYMS.get(token, token)
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _content_tokens(text: str) -> frozenset[str]:
    """Lowercase, lightly-normalised content words, minus filler."""
    tokens: set[str] = set()
    for match in _TOKEN_RE.findall(text):
        lower = match.lower()
        if lower in _STOPWORDS:
            continue
        normalized = _normalize_token(lower)
        if normalized and normalized not in _STOPWORDS:
            tokens.add(normalized)
    return frozenset(tokens)


def _surface_form(text: str) -> tuple[str, ...]:
    """Normalized ordered words without dropping intent/scaffolding."""
    return tuple(
        _normalize_token(token.casefold())
        for token in _TOKEN_RE.findall(text.translate(_APOSTROPHE_TRANSLATION))
    )


def _phrasing_score(query: frozenset[str], phrasing: frozenset[str]) -> float:
    """Similarity of a query to one canonical/alias phrasing, in 0..1.

    Dice alone under-scores a short natural question against a fuller canonical
    ("KMS?" vs "What is AWS KMS and when do you use it?"), so it is blended with
    query coverage -- the fraction of the asker's content words the phrasing
    accounts for. The blend still lets a two-word query fully contained in a
    long entry rank sensibly instead of scoring a spurious 1.0.
    """
    if not query or not phrasing:
        return 0.0
    intersection = len(query & phrasing)
    if not intersection:
        return 0.0
    dice = 2.0 * intersection / (len(query) + len(phrasing))
    coverage = intersection / len(query)
    return max(dice, 0.5 * dice + 0.5 * coverage)


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """One pre-answered question and its ready-to-read answer."""

    question: str
    answer: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    source: str
    topic: str
    # Precomputed at load time so scoring on the hot path is set math only.
    _question_tokens: frozenset[str]
    _alias_tokens: tuple[frozenset[str], ...]
    _tag_tokens: frozenset[str]

    def score(self, query_tokens: frozenset[str]) -> float:
        """Best match of the query against this entry's phrasings and tags."""
        best = self._question_tokens
        base = _phrasing_score(query_tokens, best)
        for alias_tokens in self._alias_tokens:
            candidate = _phrasing_score(query_tokens, alias_tokens)
            if candidate > base:
                base = candidate
        # Tags catch a synonym the phrasings happen not to spell out. The nudge
        # is small and capped so tags refine ranking without ever, on their own,
        # dragging an off-topic entry over the answer threshold.
        tag_hits = len(query_tokens & self._tag_tokens)
        if tag_hits:
            base = min(1.0, base + min(0.09, 0.03 * tag_hits))
        return base

    def as_reference(self) -> str:
        """Render for injection into a live prompt as authoritative material."""
        return f"Q: {self.question}\nA: {self.answer}"


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    entry: KnowledgeEntry
    score: float

    @property
    def answer(self) -> str:
        return self.entry.answer


def _split_aliases(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[|;]+", value) if part.strip()]


def _split_tags(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,|;]+", value) if part.strip()]


def _parse_entries(raw: str, source: str) -> list[KnowledgeEntry]:
    """Parse one document's ``##`` sections into entries.

    A malformed section (no question text, or no answer body) is skipped rather
    than raised: a single bad entry in a large pack must not sink the rest.
    """
    title_match = _TITLE_RE.search(raw)
    topic = title_match.group(1).strip() if title_match else source
    headings = list(_HEADING_RE.finditer(raw))
    entries: list[KnowledgeEntry] = []
    for index, heading in enumerate(headings):
        question = heading.group(1).strip()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(raw)
        body = raw[heading.end() : end]
        aliases: list[str] = []
        tags: list[str] = []
        answer_lines: list[str] = []
        in_answer = False
        for line in body.splitlines():
            if not in_answer:
                if not line.strip():
                    # Blank lines before the answer belong to neither metadata
                    # nor prose; skip them without opening the answer body.
                    continue
                meta = _META_RE.match(line.strip())
                if meta is not None:
                    key = meta.group(1).lower()
                    payload = meta.group(2)
                    if key == "aliases":
                        aliases.extend(_split_aliases(payload))
                    else:
                        tags.extend(_split_tags(payload))
                    continue
                in_answer = True
            answer_lines.append(line)
        answer = "\n".join(answer_lines).strip()
        if not question or not answer:
            log.debug("Skipping malformed knowledge entry in %s: %r", source, question)
            continue
        entries.append(
            KnowledgeEntry(
                question=question,
                answer=answer,
                aliases=tuple(aliases),
                tags=tuple(tags),
                source=source,
                topic=topic,
                _question_tokens=_content_tokens(question),
                _alias_tokens=tuple(_content_tokens(alias) for alias in aliases),
                _tag_tokens=_content_tokens(" ".join(tags)),
            )
        )
    return entries


@dataclass(slots=True)
class KnowledgeIndex:
    """An in-memory index over one pack's entries, ranked by lexical match."""

    entries: list[KnowledgeEntry] = field(default_factory=list)
    doc_count: int = 0

    def __bool__(self) -> bool:
        return bool(self.entries)

    def _ranked(self, query: str) -> list[tuple[float, KnowledgeEntry]]:
        query_tokens = _content_tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, float, KnowledgeEntry]] = []
        for entry in self.entries:
            score = entry.score(query_tokens)
            if score <= 0.0:
                continue
            # Tie-break: when overall scores match (e.g. several entries carry a
            # bare "kms" alias), prefer the one whose CANONICAL question most
            # directly matches -- so "What is KMS?" resolves to the glossary
            # definition, not a tangentially KMS-tagged entry. Deterministic and
            # principled rather than alphabetical.
            union = query_tokens | entry._question_tokens
            canonical = len(query_tokens & entry._question_tokens) / len(union)
            scored.append((score, canonical, entry))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].question))
        return [(score, entry) for score, _canonical, entry in scored]

    def lookup(
        self,
        query: str,
        threshold: float,
        min_words: int = 1,
        margin: float = 0.05,
    ) -> KnowledgeHit | None:
        """Return the single best entry to answer verbatim, or None.

        ``min_words`` counts raw words (not content words): a well-formed
        question like "What is GuardDuty?" carries only one content word yet is a
        confident match, whereas a genuine fragment is short however you count
        it. ``margin`` rejects an ambiguous top-two tie unless the leader is
        already comfortably strong, because serving the wrong cached answer
        confidently is worse than taking the slower live path.
        """
        if len(_TOKEN_RE.findall(query)) < max(1, min_words):
            return None
        if _has_unresolved_reference(query):
            return None
        query_tokens = _content_tokens(query)
        if not query_tokens:
            return None
        ranked = self._ranked(query)
        if not ranked:
            return None
        # Verbatim cache answers must be materially safer than prompt
        # grounding. Broad asks used to subset-match much narrower entries at
        # scores as high as 0.99 ("delete a KMS key" selected a tenant-offboard
        # crypto-shredding scenario). Require an exact normalized token set in
        # one human-authored canonical/alias phrasing. Stopword and light plural
        # normalization still accept natural variants; a miss is cheap and safe
        # because it falls through to the live answer path.
        def is_specific(entry: KnowledgeEntry) -> bool:
            return _compatible_exact_phrasing(query, query_tokens, entry)

        ranked = [item for item in ranked if is_specific(item[1])]
        if not ranked:
            return None
        best_score, best_entry = ranked[0]
        if best_score < threshold:
            return None
        if len(ranked) > 1:
            second_score, second_entry = ranked[1]
            # A near-tie between two DIFFERENT answers is genuine ambiguity:
            # serving one confidently would be a coin flip. Even two exact
            # aliases can collide in a large pack, so exactness does not waive
            # this guard; falling through to the live path is always safer.
            if (
                best_entry.answer != second_entry.answer
                and (best_score - second_score) < margin
            ):
                return None
        return KnowledgeHit(best_entry, best_score)

    def grounding(self, query: str, k: int, min_score: float = 0.0) -> list[str]:
        """Top-k entries rendered as reference material for a live answer.

        Ranking is useful only after subject specificity is established. An
        unrelated or conflicting question can otherwise share several generic
        words ("access control", "public access") with a narrow domain entry
        and poison the prompt labelled as authoritative. Grounding therefore
        requires one exact normalized canonical/alias token set. This remains
        useful for non-cue styles and ambiguous exact cache hits, while fuzzy
        misses safely receive no reference rather than the wrong reference.
        """
        if k <= 0:
            return []
        if _has_unresolved_reference(query):
            return []
        query_tokens = _content_tokens(query) - _GROUNDING_SCAFFOLD
        if not query_tokens:
            return []
        references: list[str] = []
        for score, entry in self._ranked(query):
            if score < min_score:
                continue
            if not _compatible_exact_phrasing(
                query, query_tokens, entry, grounding=True
            ):
                continue
            references.append(entry.as_reference())
            if len(references) >= k:
                break
        return references


def load_pack(
    path: str | Path,
    status_callback: Callable[[str], None] | None = None,
) -> KnowledgeIndex:
    """Load every ``*.md`` document under ``path`` into one index.

    Degrades to an empty index on any problem (missing directory, unreadable
    file, no entries) so an absent or broken pack simply disables the cache
    rather than breaking the pipeline. An empty index answers nothing and
    grounds nothing -- identical to the feature being off.
    """
    report = status_callback or (lambda _message: None)
    pack_path = Path(path)
    if not pack_path.is_dir():
        report(f"Knowledge pack directory not found ({pack_path}); cache disabled")
        return KnowledgeIndex()
    entries: list[KnowledgeEntry] = []
    doc_count = 0
    for doc in sorted(pack_path.glob("*.md"), key=lambda item: item.name.casefold()):
        try:
            raw = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            report(f"Knowledge document unavailable ({doc.name}): {exc}")
            continue
        parsed = _parse_entries(raw, doc.stem)
        if parsed:
            doc_count += 1
            entries.extend(parsed)
    if not entries:
        report(f"Knowledge pack has no usable entries ({pack_path}); cache disabled")
        return KnowledgeIndex()
    report(f"Knowledge pack loaded: {len(entries)} entries from {doc_count} docs")
    return KnowledgeIndex(entries=entries, doc_count=doc_count)
