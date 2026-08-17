"""Typed configuration loading for Ambient Q&A."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


@dataclass(slots=True)
class AudioConfig:
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
    hallucination_blocklist: list[str] | None = None

    def __post_init__(self) -> None:
        if self.hallucination_blocklist is None:
            self.hallucination_blocklist = [
                "thank you",
                "thank you.",
                "thanks for watching",
                "thanks for watching!",
                "please subscribe",
                "subtitles by",
                "captioning by",
            ]


@dataclass(slots=True)
class ContextConfig:
    profile: str = ""
    enabled: bool = True


@dataclass(slots=True)
class GateConfig:
    model: str = "gemma4:e2b"
    ollama_url: str = "http://127.0.0.1:11434/api/chat"
    mode: str = "balanced"
    min_words: int = 3
    context_turns: int = 6
    dedupe_window_s: float = 300.0
    dedupe_ratio: float = 0.85
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
    merge_gap_s: float = 4.5
    # How long to HOLD an unfinished utterance in wall-clock time. This must
    # outlast merge_gap_s PLUS the spoken length of the continuation PLUS its
    # transcription latency -- otherwise the hold expires before the continuation
    # is even transcribed. A 3.5s pause really costs ~4.2s of wall clock, so a
    # window merely equal to the gap silently never merges.
    merge_window_s: float = 9.0
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
    queue_size: int = 16


@dataclass(slots=True)
class UIConfig:
    show_transcripts: bool = True
    log_dir: str = "logs"
    status_interval_s: float = 0.5


@dataclass(slots=True)
class Config:
    audio: AudioConfig
    stt: STTConfig
    context: ContextConfig
    gate: GateConfig
    answer: AnswerConfig
    ui: UIConfig
    merge: MergeConfig = field(default_factory=MergeConfig)


T = TypeVar("T")


def _section(cls: type[T], values: dict[str, Any], name: str) -> T:
    valid = {field.name for field in fields(cls)}
    unknown = sorted(set(values) - valid)
    if unknown:
        raise ValueError(f"Unknown key(s) in [{name}]: {', '.join(unknown)}")
    return cls(**values)


def validate_config(config: Config) -> Config:
    if config.audio.sample_rate != 16000:
        raise ValueError("audio.sample_rate must be 16000 for Silero VAD and Whisper")
    if not 20 <= config.audio.frame_ms <= 30:
        raise ValueError("audio.frame_ms must be between 20 and 30")
    if config.gate.mode not in {"strict", "balanced", "eager"}:
        raise ValueError("gate.mode must be strict, balanced, or eager")
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
    )


def load_config(path: str | Path = "config.toml") -> Config:
    config_path = Path(path)
    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    allowed = {"audio", "stt", "context", "gate", "merge", "answer", "ui"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown config section(s): {', '.join(unknown)}")
    config = Config(
        audio=_section(AudioConfig, raw.get("audio", {}), "audio"),
        stt=_section(STTConfig, raw.get("stt", {}), "stt"),
        context=_section(ContextConfig, raw.get("context", {}), "context"),
        gate=_section(GateConfig, raw.get("gate", {}), "gate"),
        merge=_section(MergeConfig, raw.get("merge", {}), "merge"),
        answer=_section(AnswerConfig, raw.get("answer", {}), "answer"),
        ui=_section(UIConfig, raw.get("ui", {}), "ui"),
    )
    return validate_config(config)
