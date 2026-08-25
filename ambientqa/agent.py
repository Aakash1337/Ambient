"""Deterministic foundations for direct, courteous Agent conversations.

Question detection deliberately stays elsewhere.  Agent mode has a different
contract: brief social turns should receive an immediate local reply, while a
meaningful statement should reach the answer model even when it is not phrased
as a question.  Keeping these small decisions local avoids spending a model
call on "hello" and prevents the missed-question sweeper from resurrecting
social noise later.
"""

from __future__ import annotations

import re

_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9']+")

_GREETINGS = (
    re.compile(
        r"^(?:hi|hello|hey)(?: there)?(?: ambient)?(?: how are you(?: doing)?)?$"
    ),
    re.compile(r"^good (?:morning|afternoon|evening)(?: ambient)?$"),
    re.compile(r"^(?:how are you|how's it going|how are you doing)$"),
    re.compile(r"^(?:are you there|can you hear me|is anyone there)$"),
)
_THANKS = (
    re.compile(
        r"^(?:(?:okay|ok|great|perfect)[ ]+)?(?:thanks|thank you)"
        r"(?: very much| so much| a lot)?(?: that's all)?$"
    ),
    re.compile(r"^(?:i )?(?:really )?appreciate it$"),
    re.compile(r"^(?:that's|that was) (?:great|helpful),? (?:thanks|thank you)$"),
)
_GOODBYES = (
    re.compile(
        r"^(?:(?:okay|ok|thanks|thank you)[ ]+)?"
        r"(?:bye|goodbye|bye bye|good night|talk to you later|see you later)$"
    ),
    re.compile(r"^(?:have a|have a good|have a great) day$"),
    re.compile(r"^(?:that's|that is) all(?: for now)?(?: thanks)?$"),
)
_HOLDS = (
    re.compile(r"^(?:please )?(?:hold on|wait|wait a moment|just a moment)$"),
    re.compile(r"^(?:give me|one) (?:a )?(?:second|sec|minute|moment)$"),
    re.compile(r"^(?:i'll|i will) be right back$"),
)

_FILLER_WORDS = frozenset(
    {
        "ah",
        "alright",
        "er",
        "erm",
        "hm",
        "hmm",
        "like",
        "okay",
        "ok",
        "right",
        "so",
        "uh",
        "um",
        "well",
        "yeah",
    }
)

# This is intentionally narrow and fail-closed.  The model prompt carries the
# main courtesy policy; this last line of defence prevents a plainly hostile
# sentence from ever being spoken if a model or profile nevertheless emits it.
_RUDE_PATTERNS = (
    re.compile(
        r"\b(?:shut up|not my problem|figure it out|can't you read|"
        r"i (?:do not|don't) care|stop wasting (?:my|our) time)\b",
        re.I,
    ),
    re.compile(r"\b(?:you(?:'re| are)|customer is) (?:an? )?"
               r"(?:idiot|moron|stupid|dumb|incompetent|annoying)\b", re.I),
    re.compile(r"\b(?:that's|that is) your (?:fault|problem)\b", re.I),
    re.compile(r"\b(?:calm down|i already told you|you should have known)\b", re.I),
    re.compile(
        r"\b(?:that(?:'s| is)|what a) (?:an? )?(?:ridiculous|stupid|dumb) "
        r"(?:question|idea|request)\b",
        re.I,
    ),
    re.compile(
        r"\byou(?:'re| are) being (?:unreasonable|difficult|ridiculous)\b",
        re.I,
    ),
    re.compile(r"\bobviously\b", re.I),
    re.compile(r"\b(?:damn|damned|hell) you\b", re.I),
)

_SAFE_FALLBACK = (
    "I'm sorry, but I couldn't provide an appropriate response. I'm here to "
    "help. Could you tell me a little more about what you need?"
)


def _compact(text: str) -> str:
    return _SPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", text.casefold())).strip()


def classify_agent_turn(text: str) -> str:
    """Classify one completed speaker turn without a network or model call.

    The return value is one of ``greeting``, ``thanks``, ``goodbye``, ``hold``,
    ``filler``, or ``content``.  Only whole-turn social phrases are captured:
    "Hello, my account is locked" remains content so the actual problem is not
    lost behind a canned greeting.
    """
    compact = _compact(text)
    if not compact:
        return "filler"
    # A courteous goodbye can contain "thanks", so it must win first.
    if any(pattern.fullmatch(compact) for pattern in _GOODBYES):
        return "goodbye"
    if any(pattern.fullmatch(compact) for pattern in _GREETINGS):
        return "greeting"
    if any(pattern.fullmatch(compact) for pattern in _THANKS):
        return "thanks"
    if any(pattern.fullmatch(compact) for pattern in _HOLDS):
        return "hold"
    tokens = compact.split()
    if tokens and all(token in _FILLER_WORDS for token in tokens):
        return "filler"
    return "content"


def local_agent_reply(kind: str, greeting: str = "") -> str | None:
    """Return the immediate spoken reply for a local social-turn kind."""
    if kind == "greeting":
        return guard_agent_answer(
            greeting
            or "Hello! I'm Ambient, an AI assistant. "
            "What would you like to work through today?"
        )
    if kind == "thanks":
        return "You're very welcome. Is there anything else you'd like to work through?"
    if kind == "goodbye":
        return "Take care, and have a great day!"
    if kind == "hold":
        return "Of course. Take your time; I'll be here when you're ready."
    return None


def guard_agent_answer(text: str) -> str:
    """Return TTS-safe courteous prose, replacing plainly hostile output.

    This is a backstop, not a general moderation classifier.  It intentionally
    catches only direct, unambiguous rudeness so useful troubleshooting content
    is not rewritten merely because it discusses an unpleasant situation.
    """
    cleaned = _SPACE_RE.sub(" ", (text or "").strip())
    if not cleaned:
        return _SAFE_FALLBACK
    if any(pattern.search(cleaned) for pattern in _RUDE_PATTERNS):
        return _SAFE_FALLBACK
    return cleaned
