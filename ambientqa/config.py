"""Typed configuration loading for Ambient."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


@dataclass(slots=True)
class AudioConfig:
    # Which capture stack feeds the pipeline. "auto" picks the platform's
    # native one -- WASAPI (pyaudiowpatch) on Windows, PipeWire (pactl/parec)
    # on Linux, and CoreAudio (sounddevice) on macOS. Explicit values exist for
    # diagnostics and unusual setups, not for day-to-day use.
    backend: str = "auto"
    mic_device: str = ""
    output_device: str = ""
    sample_rate: int = 16000
    frame_ms: int = 25
    queue_size: int = 256
    silence_ms: int = 900
    pre_roll_ms: int = 300
    min_utterance_ms: int = 400
    max_utterance_s: float = 20.0
    # A capture stream can open successfully and still carry nothing -- pinning
    # output_device to an endpoint the meeting is not playing through opens a
    # perfectly healthy loopback on silence. `sys:on` then lies for the whole
    # session. Warn once a live source has been below the noise floor this long.
    silent_source_warn_s: float = 45.0


@dataclass(slots=True)
class STTConfig:
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    cpu_compute_type: str = "int8"
    queue_size: int = 12
    language: str = ""
    # Faster-Whisper's own VAD is a second line of defence after the streaming
    # segmenter. It trims residual silence/hold music before decoding, where
    # Whisper is otherwise most likely to hallucinate fluent text.
    vad_filter: bool = True
    # Domain hotwords can improve proper nouns, but a mismatched profile can
    # also bias ordinary speech into those terms. Keep them explicitly opt-in.
    profile_hints: bool = False
    hallucination_blocklist: list[str] | None = None

    def __post_init__(self) -> None:
        # Matched EXACTLY against the normalised whole transcript, never by
        # prefix -- prefix matching silently ate real speech ("Thank you. So,
        # tell me about..."), so every entry must be the full utterance
        # Whisper hallucinates on silence.
        if self.hallucination_blocklist is None:
            self.hallucination_blocklist = [
                "thank you",
                "thank you very much",
                "thank you so much",
                "thank you for watching",
                "thanks for watching",
                "please subscribe",
                "subtitles by the amara org community",
            ]


@dataclass(slots=True)
class ContextConfig:
    profile: str = ""
    enabled: bool = True


@dataclass(slots=True)
class KnowledgeConfig:
    """A pre-answered knowledge pack for near-instant, grounded answers.

    Opt-in and off by default so existing sessions are untouched. When enabled,
    a gated question is first matched against the pack: a strong match is
    answered verbatim in milliseconds with no model call. A miss goes live; an
    entry is grounded only when an authored phrasing has the same normalized
    subject and compatible intent, never from merely nearby lexical overlap.
    """

    enabled: bool = False
    # Directory of *.md knowledge documents, resolved relative to this config
    # file (like context.profile). Empty or missing simply disables the cache.
    path: str = ""
    # Minimum lexical match score (0..1) to answer verbatim from the pack. Set
    # deliberately high: serving the wrong cached answer confidently is worse
    # than taking the slower, correct live path. Raised to 0.66 after a dense
    # pack produced a borderline 0.62 false positive.
    hit_threshold: float = 0.66
    # A query with fewer words than this is never answered from cache -- a short
    # fragment matches too much by accident to trust. Counts raw words, so a
    # three-word "What is GuardDuty?" still qualifies.
    min_query_words: int = 3
    # On a miss, inject up to this many intent-compatible exact-subject entries
    # into the live prompt as authoritative reference. 0, or
    # ground_on_miss = false, disables grounding.
    ground_on_miss: bool = True
    retrieve_k: int = 3
    # Secondary score floor after exact-subject and intent checks.
    grounding_threshold: float = 0.30


@dataclass(slots=True)
class GateConfig:
    model: str = "gemma4:e2b"
    ollama_url: str = "http://127.0.0.1:11434/api/chat"
    mode: str = "balanced"
    min_words: int = 3
    context_turns: int = 6
    dedupe_window_s: float = 300.0
    dedupe_ratio: float = 0.85
    # A near-duplicate of an already-answered question inside this window is a
    # mechanical duplicate (Whisper re-emission, a merge artifact) and is
    # dropped. Beyond it, an almost-identical question is a human deliberately
    # re-asking -- usually because the first answer missed (mishearing, wrong
    # angle) -- and must be ANSWERED, not deduped. Mechanical dupes arrive
    # within ~5s; a human needs a few seconds to read an answer and try again.
    reask_cooldown_s: float = 8.0
    echo_window_s: float = 2.0
    echo_ratio: float = 0.85
    # Reading a recent answer back aloud (rehearsing it) must not be treated as a
    # new question. Ratio is the fraction of the utterance's content words that
    # came from a recent answer. Set answer_echo_ratio = 0 to disable.
    answer_echo_window_s: float = 300.0
    answer_echo_ratio: float = 0.5
    # How much freedom the gate has per channel. Everything is transcribed and
    # feeds the context window regardless; this only decides what may become an
    # answer.
    #
    #   "full"     heuristics plus the semantic gate, which may rewrite an
    #              indirect request into a question. Right for the other speaker.
    #   "explicit" only speech that is actually shaped like a question: a
    #              well-formed interrogative, or anything Whisper heard ending in
    #              "?". A declarative sentence never reaches the semantic gate,
    #              so it cannot be rewritten into a question nobody asked.
    #   "off"      context only; never answered.
    #
    # Your own channel defaults to "explicit". You know what you are saying, so
    # narration must not be mined for questions -- but when you genuinely ask
    # something out loud it should be answered at once, with no LLM call at all.
    channel_policy: dict[str, str] = field(
        default_factory=lambda: {"mic": "explicit", "sys": "full"}
    )
    # Utterances judged at once. Gating is a ~900ms network call, and running it
    # inline on the consumer loop makes every later utterance queue behind it --
    # so a second question could not even start answering until the first had
    # been judged. Ollama serves these in parallel up to OLLAMA_NUM_PARALLEL.
    max_concurrent: int = 3
    queue_size: int = 24
    request_timeout_s: float = 8.0


@dataclass(slots=True)
class MergeConfig:
    enabled: bool = True
    # People pause for longer than feels intuitive when thinking mid-sentence.
    # These only delay utterances that look UNFINISHED; a complete question is
    # still gated immediately, so raising them costs nothing in the common case.
    # Measured miss at 4.5/9.0: a trailed-off premise ("So if the content
    # you're searching keeps changing...") followed ~5s later by its question
    # stayed unmerged, so the gate saw only the second half.
    merge_gap_s: float = 6.5
    # How long to HOLD an unfinished utterance in wall-clock time. This must
    # outlast merge_gap_s PLUS the spoken length of the continuation PLUS its
    # transcription latency -- otherwise the hold expires before the continuation
    # is even transcribed. A 3.5s pause really costs ~4.2s of wall clock, so a
    # window merely equal to the gap silently never merges.
    merge_window_s: float = 13.0
    max_merge_parts: int = 5
    max_merge_s: float = 25.0


@dataclass(slots=True)
class AnswerConfig:
    answer_model: str = "claude-sonnet-5"
    # Stream partial output from each still-one-shot Claude process.
    stream: bool = True
    # "cue"       - a headline you can say verbatim plus a few keyword prompts.
    # "interview" - a spoken answer you could say aloud in a technical interview.
    # "terse"     - one or two compressed sentences.
    style: str = "cue"
    max_words: int = 45
    # Questions answered simultaneously. Each is its own `claude -p` process, so
    # this is the number of concurrent CLI invocations. A burst of questions --
    # a compound ask, or a follow-up landing while the first is still running --
    # otherwise serialises, and the second answer arrives a full answer late.
    max_concurrent: int = 4
    # Look facts up instead of recalling them: "off" | "auto" | "always".
    # A lookup takes ~17s against ~3.5s from memory, so "always" is unusable
    # live. "auto" searches only for questions whose answer has moved since
    # training -- names, versions, pricing, availability -- which measured at
    # 0.5% of all recorded questions. Being confidently wrong on those is worse
    # than being slow: asked what Vertex AI is called now, memory said it had
    # not been renamed, when it had become the Gemini Enterprise Agent Platform.
    web_lookup: str = "auto"
    answer_timeout_s: float = 45.0
    context_turns: int = 6
    # Completed question/answer pairs carried into every new answer prompt, so
    # follow-ups resolve against what was actually said: "elaborate on the
    # second method" needs the answer that listed the methods, which the raw
    # transcript never contains. 0 disables the history block entirely.
    history_turns: int = 8
    # Second pass over every delivered answer: an auditor with a wider
    # transcript window and the full Q&A history re-reads it AFTER it is on
    # screen (the first answer's latency is untouched) and replaces it on the
    # card only when it is materially wrong -- a missed constraint, a
    # misheard question, a dropped enumeration item. Style is never grounds:
    # a correction that lands ~8s late is only worth the distraction when the
    # first answer would have misled. "always" | "off".
    # Expensive and deliberately opt-in.  Running a second Sonnet request for
    # every answer doubled paid traffic and exhausted the demo account without
    # making that extra work visible in the status bar.
    verify: str = "off"
    # The auditor sees more transcript than the first pass deliberately: what
    # it exists to catch is context the fast path missed.
    verify_context_turns: int = 18
    # The verify audit above only reviews answers that EXIST. A question the
    # gate wrongly rejected produces nothing to audit, so a sweeper
    # periodically hands the recent judgment-stage rejections plus wide
    # transcript context to a model and asks which were genuine asks; the
    # catches come back as late answer cards through the normal answer path
    # (streaming, audit and all). "always" | "off".
    # Enabled as the recovery backstop. It batches recent rejected candidates
    # into one small-model call per interval; unlike verify, it does not add a
    # second Sonnet call to every successful answer. Emergency mode overrides
    # this to "off" for its minimum-dependency baseline.
    sweep: str = "always"
    sweep_interval_s: float = 25.0
    # A recovered question that is this old is no longer conversationally
    # useful and can interrupt a completely different part of the discussion.
    sweep_max_age_s: float = 60.0
    # The sweep is a small classification, so a fast cheap model is the right
    # default; empty falls back to answer_model.
    sweep_model: str = "claude-haiku-4-5"
    queue_size: int = 16


@dataclass(slots=True)
class TtsConfig:
    """Voice mode (launched with --voice). The section only tunes HOW answers
    are spoken; WHETHER an instance speaks is decided per launch, never here,
    so several instances can share this file with different roles."""

    # "kokoro" is the neural voice (local ~310 MB model, CPU inference);
    # "espeak" is the instant robotic fallback. Kokoro degrades to espeak by
    # itself when its model or dependencies are unavailable.
    engine: str = "kokoro"
    voice: str = "af_heart"
    speed: float = 1.0
    # "first_line" speaks only the sayable opening line (cue answers are
    # designed with exactly one); "full" speaks the whole answer with code
    # blocks dropped -- pair it with answer.style = "interview".
    speak: str = "first_line"
    # Channels whose accepted questions get spoken answers. Default is mic
    # only: speaking answers to the OTHER side's questions broadcasts them
    # into the room -- and into any live call's microphone.
    speak_channels: list[str] | None = None
    # Channels every instance drops while ANY instance is speaking. The sys
    # loopback always hears playback verbatim; the mic hears it acoustically
    # (the ec_mic module does not cancel app playback -- measured 6-9 dB at
    # best). Headphone users can shrink this to ["sys"] to keep talking
    # while the answer plays.
    mute_channels: list[str] | None = None
    # Unspoken answers waiting behind the single serial voice. Small on
    # purpose: a burst should drop stale speech, not build a backlog.
    queue_size: int = 2
    # Mute hold after playback ends: sink latency plus room decay.
    gate_tail_s: float = 0.7
    # An answer older than this when its turn comes is shown, not spoken.
    max_age_s: float = 30.0
    # Resolved relative to this config file, like context.profile.
    model_path: str = "models/kokoro-v1.0.onnx"
    voices_path: str = "models/voices-v1.0.bin"

    def __post_init__(self) -> None:
        if self.speak_channels is None:
            self.speak_channels = ["mic"]
        if self.mute_channels is None:
            self.mute_channels = ["mic", "sys"]


@dataclass(slots=True)
class UIConfig:
    show_transcripts: bool = True
    log_dir: str = "logs"
    status_interval_s: float = 0.5
    # "top" mounts each new entry above the rest and pins the view to it;
    # "bottom" appends chronologically like a chat log.
    feed_direction: str = "top"


@dataclass(slots=True)
class Config:
    audio: AudioConfig
    stt: STTConfig
    context: ContextConfig
    gate: GateConfig
    answer: AnswerConfig
    ui: UIConfig
    merge: MergeConfig = field(default_factory=MergeConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)


T = TypeVar("T")


def _section(cls: type[T], values: dict[str, Any], name: str) -> T:
    valid = {field.name for field in fields(cls)}
    unknown = sorted(set(values) - valid)
    if unknown:
        raise ValueError(f"Unknown key(s) in [{name}]: {', '.join(unknown)}")
    return cls(**values)


def validate_ollama_url(url: str) -> None:
    """Require the semantic gate's documented direct loopback HTTP endpoint."""
    try:
        parsed = urlsplit(url)
        host = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "gate.ollama_url must be an HTTP URL on a literal loopback address"
        ) from exc
    if (
        parsed.scheme != "http"
        or not host.is_loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/api/chat"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "gate.ollama_url must be an HTTP /api/chat URL on a literal "
            "loopback address"
        )


def validate_config(config: Config) -> Config:
    if config.audio.backend not in {"auto", "wasapi", "pipewire", "coreaudio"}:
        raise ValueError(
            'audio.backend must be "auto", "wasapi", "pipewire", or "coreaudio"'
        )
    if config.audio.sample_rate != 16000:
        raise ValueError("audio.sample_rate must be 16000 for Silero VAD and Whisper")
    if not 20 <= config.audio.frame_ms <= 30:
        raise ValueError("audio.frame_ms must be between 20 and 30")
    if config.gate.mode not in {"strict", "balanced", "eager"}:
        raise ValueError("gate.mode must be strict, balanced, or eager")
    validate_ollama_url(config.gate.ollama_url)
    unknown_channels = sorted(set(config.gate.channel_policy) - {"mic", "sys"})
    if unknown_channels:
        raise ValueError(
            f'gate.channel_policy may only key "mic" and "sys"; '
            f"got {', '.join(unknown_channels)}"
        )
    for channel, policy in sorted(config.gate.channel_policy.items()):
        if policy not in {"full", "explicit", "off"}:
            raise ValueError(
                f'gate.channel_policy.{channel} must be "full", "explicit", '
                f'or "off"; got "{policy}"'
            )
    if all(policy == "off" for policy in config.gate.channel_policy.values()) and set(
        config.gate.channel_policy
    ) == {"mic", "sys"}:
        raise ValueError("gate.channel_policy cannot switch every channel off")
    for name, value in (
        ("audio.queue_size", config.audio.queue_size),
        ("stt.queue_size", config.stt.queue_size),
        ("gate.queue_size", config.gate.queue_size),
        ("gate.max_concurrent", config.gate.max_concurrent),
        ("merge.max_merge_parts", config.merge.max_merge_parts),
        ("answer.queue_size", config.answer.queue_size),
        ("answer.max_concurrent", config.answer.max_concurrent),
    ):
        if value < 1:
            raise ValueError(f"{name} must be at least 1")
    if config.gate.min_words < 1:
        raise ValueError("gate.min_words must be at least 1")
    if config.answer.history_turns < 0:
        raise ValueError("answer.history_turns must be at least 0")
    if config.answer.verify not in {"off", "always"}:
        raise ValueError('answer.verify must be "off" or "always"')
    if config.answer.verify_context_turns < 1:
        raise ValueError("answer.verify_context_turns must be at least 1")
    if config.answer.sweep not in {"off", "always"}:
        raise ValueError('answer.sweep must be "off" or "always"')
    if config.answer.sweep_interval_s <= 0:
        raise ValueError("answer.sweep_interval_s must be greater than 0")
    if config.answer.sweep_max_age_s <= 0:
        raise ValueError("answer.sweep_max_age_s must be greater than 0")
    if config.gate.reask_cooldown_s < 0:
        raise ValueError("gate.reask_cooldown_s must be at least 0")
    if not 0 <= config.gate.dedupe_ratio <= 1:
        raise ValueError("gate.dedupe_ratio must be between 0 and 1")
    if config.merge.merge_gap_s < 0:
        raise ValueError("merge.merge_gap_s must be at least 0")
    if config.merge.merge_window_s < 0:
        raise ValueError("merge.merge_window_s must be at least 0")
    if config.merge.max_merge_s <= 0:
        raise ValueError("merge.max_merge_s must be greater than 0")
    if config.answer.max_words < 1:
        raise ValueError("answer.max_words must be at least 1")
    if config.answer.style not in {"cue", "interview", "terse"}:
        raise ValueError('answer.style must be "cue", "interview", or "terse"')
    if config.answer.web_lookup not in {"off", "auto", "always"}:
        raise ValueError('answer.web_lookup must be "off", "auto", or "always"')
    if config.audio.silent_source_warn_s <= 0:
        raise ValueError("audio.silent_source_warn_s must be greater than 0")
    if config.ui.status_interval_s <= 0:
        raise ValueError("ui.status_interval_s must be greater than 0")
    if config.ui.feed_direction not in {"top", "bottom"}:
        raise ValueError('ui.feed_direction must be "top" or "bottom"')
    if config.tts.engine not in {"kokoro", "espeak"}:
        raise ValueError('tts.engine must be "kokoro" or "espeak"')
    if config.tts.speak not in {"first_line", "full"}:
        raise ValueError('tts.speak must be "first_line" or "full"')
    for name, channels in (
        ("tts.speak_channels", config.tts.speak_channels or []),
        ("tts.mute_channels", config.tts.mute_channels or []),
    ):
        unknown_tts = sorted(set(channels) - {"mic", "sys"})
        if unknown_tts:
            raise ValueError(
                f'{name} may only contain "mic" and "sys"; '
                f"got {', '.join(unknown_tts)}"
            )
    if config.tts.queue_size < 1:
        raise ValueError("tts.queue_size must be at least 1")
    if not 0 <= config.tts.gate_tail_s <= 5:
        raise ValueError("tts.gate_tail_s must be between 0 and 5")
    if config.tts.max_age_s <= 0:
        raise ValueError("tts.max_age_s must be greater than 0")
    if not 0.25 <= config.tts.speed <= 3:
        raise ValueError("tts.speed must be between 0.25 and 3")
    if not 0 <= config.knowledge.hit_threshold <= 1:
        raise ValueError("knowledge.hit_threshold must be between 0 and 1")
    if not 0 <= config.knowledge.grounding_threshold <= 1:
        raise ValueError("knowledge.grounding_threshold must be between 0 and 1")
    if config.knowledge.min_query_words < 1:
        raise ValueError("knowledge.min_query_words must be at least 1")
    if config.knowledge.retrieve_k < 0:
        raise ValueError("knowledge.retrieve_k must be at least 0")
    return config


def default_config() -> Config:
    return Config(
        audio=AudioConfig(),
        stt=STTConfig(),
        context=ContextConfig(),
        gate=GateConfig(),
        answer=AnswerConfig(),
        ui=UIConfig(),
        merge=MergeConfig(),
        tts=TtsConfig(),
        knowledge=KnowledgeConfig(),
    )


def _merge_raw_config(
    base: dict[str, Any], override: dict[str, Any]
) -> dict[str, Any]:
    """Recursively merge a small platform overlay into a shared config."""

    merged = dict(base)
    for key, value in override.items():
        inherited = merged.get(key)
        if isinstance(inherited, dict) and isinstance(value, dict):
            merged[key] = _merge_raw_config(inherited, value)
        else:
            merged[key] = value
    return merged


def _load_raw_config(path: Path, stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    if not path.exists():
        return {}
    resolved = path.resolve()
    if resolved in stack:
        chain = " -> ".join(str(item) for item in (*stack, resolved))
        raise ValueError(f"Config extends cycle: {chain}")
    with path.open("rb") as handle:
        raw: dict[str, Any] = tomllib.load(handle)
    extends = raw.pop("extends", None)
    if extends is None:
        return raw
    if not isinstance(extends, str) or not extends.strip():
        raise ValueError('Config "extends" must be a non-empty path string')
    base_path = Path(extends)
    if not base_path.is_absolute():
        base_path = path.parent / base_path
    if not base_path.exists():
        raise ValueError(f"Extended config does not exist: {base_path}")
    base = _load_raw_config(base_path, (*stack, resolved))
    return _merge_raw_config(base, raw)


def load_config(path: str | Path = "config.toml") -> Config:
    config_path = Path(path)
    raw = _load_raw_config(config_path)
    allowed = {
        "audio", "stt", "context", "gate", "merge", "answer", "ui", "tts",
        "knowledge",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown config section(s): {', '.join(unknown)}")
    gate_values = dict(raw.get("gate", {}))
    managed_ollama_url = os.environ.get("AMBIENTQA_OLLAMA_URL")
    if managed_ollama_url:
        gate_values["ollama_url"] = managed_ollama_url
    config = Config(
        audio=_section(AudioConfig, raw.get("audio", {}), "audio"),
        stt=_section(STTConfig, raw.get("stt", {}), "stt"),
        context=_section(ContextConfig, raw.get("context", {}), "context"),
        gate=_section(GateConfig, gate_values, "gate"),
        merge=_section(MergeConfig, raw.get("merge", {}), "merge"),
        answer=_section(AnswerConfig, raw.get("answer", {}), "answer"),
        ui=_section(UIConfig, raw.get("ui", {}), "ui"),
        tts=_section(TtsConfig, raw.get("tts", {}), "tts"),
        knowledge=_section(KnowledgeConfig, raw.get("knowledge", {}), "knowledge"),
    )
    return validate_config(config)
