# Live Speech Coaching Feasibility and Implementation Plan

**Project assessed:** Ambient Q&A  
**Assessment date:** 2026-08-06  
**Conclusion:** This repository is a strong foundation for a Windows live speech-coaching MVP. Its audio capture, voice-activity detection, transcription, asynchronous pipeline, channel separation, profiles, UI, and session logging are reusable. The main new work is a coaching-analysis branch, a rate-limited coaching UI, session-level metrics, and privacy controls.

## Executive summary

Ambient Q&A currently listens to microphone and system audio, segments speech into utterances, transcribes it locally, detects questions, and streams generated answers into a side pane. A speech coach needs nearly the same infrastructure but a different decision and feedback layer:

```text
Current:
audio -> VAD -> transcription -> question gate -> answer generator -> Q&A card

Proposed:
audio -> VAD -> transcription -> coaching analyzers -> coaching state/UI
       \-> acoustic analyzers --------------------^
       \-> optional Q&A assistant path (unchanged)
```

The product should use three latency lanes:

1. **Immediate acoustic feedback (about 25-250 ms):** input level, clipping, speaking state, pause length, energy, and pitch trends. This path must not use an LLM.
2. **Turn-level feedback (roughly 1.5-3 seconds after speech ends):** pace, fillers, repetitions, verbal restarts, sentence length, and transcript-confidence proxies.
3. **Slow reflective feedback (several seconds or session end):** structure, concision, audience fit, answer quality, suggested phrasing, and a session summary.

The first useful version should focus on mic-only pace, filler, pause, volume, clipping, and talk/listen feedback. More subjective capabilities such as pronunciation, confidence, charisma, or accent-related scoring should wait until there is reliable validation data and careful product framing.

## Existing capabilities that can be reused

### Audio acquisition

- Separate microphone and Windows WASAPI loopback capture.
- Configurable device selection using case-insensitive name matching.
- Automatic loopback endpoint selection and handover.
- Conversion to 16 kHz mono `float32` audio.
- Live RMS/source-health monitoring.
- Capture threads never block on downstream consumers.
- Graceful degradation when a device is unavailable.

Primary code: `ambientqa/audio.py`, `ambientqa/audio_devices.py`.

### Voice-activity detection and segmentation

- Per-channel Silero VAD.
- Pre-roll preservation so word onsets are not clipped.
- Configurable trailing-silence threshold.
- Minimum-duration rejection for clicks and very short noise.
- Maximum-duration force flush for long monologues.
- Energy-based fallback if Silero cannot load.

Primary code: `ambientqa/segmenter.py`.

### Local speech-to-text

- `faster-whisper` transcription.
- CUDA primary path and CPU fallback.
- Domain vocabulary and topic prompting through profiles.
- Hallucination filtering.
- Transcription latency measurement.
- Channel and utterance timestamps.

Primary code: `ambientqa/stt.py`, `ambientqa/profile.py`.

### Runtime orchestration

- Typed event objects.
- Bounded, drop-oldest queues.
- Thread-safe handoff from capture threads to `asyncio`.
- Ordered context updates with concurrent slow downstream work.
- Bounded question and answer concurrency.
- Pause/resume and clean shutdown behavior.

Primary code: `ambientqa/bus.py`, `ambientqa/__main__.py`.

### Conversation context

- Separate mic and system channels.
- Cross-channel echo suppression.
- Recent transcript context.
- Fragment merging for thoughts split by pauses.
- Switchable profiles containing topic, background, and vocabulary.

Primary code: `ambientqa/context.py`, `ambientqa/continuity.py`, `ambientqa/profile.py`.

### User interface

- Passive Textual side pane.
- Scrolling transcript feed.
- Independently updated streaming cards.
- Status and warning display.
- Pause, clear, transcript visibility, device selection, and profile controls.
- Live audio-device meters.

Primary code: `ambientqa/ui.py`.

### Logging and evaluation foundation

- Per-utterance JSONL records.
- Channel, transcript, gate decision, answer, and latency data.
- A broad automated test suite.
- Question-gate evaluation tooling and recorded-session evidence.

At the time of this assessment, all **250 tests passed**.

## Measured latency baseline

The 18 saved session logs contained 1,444 utterance records. Their positive latency measurements were:

| Stage | Samples | Median | P90 | Maximum |
|---|---:|---:|---:|---:|
| Speech-to-text | 1,444 | 0.68 s | 2.25 s | 9.22 s |
| Semantic gate | 1,367 | 0.83 s | 1.13 s | 8.04 s |
| Generated answer | 569 | 5.93 s | 12.61 s | 45.10 s |

These figures exclude the configured 900 ms of trailing silence required before an utterance reaches transcription. They confirm that deterministic acoustic feedback can feel immediate, transcript feedback can arrive shortly after a turn, and generated feedback should be asynchronous or deferred.

## Coaching feature feasibility

| Feature | Feasibility | Implementation approach | Important limitations |
|---|---|---|---|
| Speaking pace/WPM | High | Words divided by speech-active minutes, calculated per turn and over a rolling window | Utterance duration currently includes pre-roll and trailing silence; retain speech-active duration separately |
| Long pauses | High | Emit VAD speech-start, speech-stop, and silence-duration events | Capture before continuity merging, which can hide original boundaries |
| Filler words | High/medium | Normalize transcripts and count configured lexical patterns | Whisper can omit acoustic fillers such as “um” and “uh” |
| Volume | High | Rolling mic RMS calibrated to the user's device and environment | Absolute amplitude is device-dependent; avoid universal thresholds |
| Clipping | High | Count samples near full scale over a rolling window | Distinguish isolated peaks from persistent clipping |
| Talk/listen balance | High | Aggregate mic versus system VAD-active time | System audio may contain several remote speakers |
| Speaking streak length | High | Measure uninterrupted mic turns | Do not treat longer turns as inherently bad in all coaching modes |
| Repetitions | Medium | Detect repeated n-grams and semantic restatements | Legitimate emphasis should not always be penalized |
| Verbal restarts | Medium | Combine transcript patterns with short VAD gaps | ASR punctuation can make classification noisy |
| Concision/rambling | Medium | Turn duration, sentence length, idea repetition, and periodic LLM review | Requires task- and audience-specific targets |
| Answer structure | Medium | Detect frameworks and content coverage; use an LLM for reflective review | Too slow and subjective for continuous live scoring |
| Vocal variety/monotony | Medium | Pitch range, energy variation, and emphasis patterns | Requires personal calibration and careful handling of vocal differences |
| Interruption detection | Medium | Detect overlapping mic/system speech intervals | Crosstalk and loopback delay can produce false overlap |
| Clarity proxy | Medium | SNR, clipping, ASR confidence, restarts, repetition, and incomplete phrases | A proxy is not the same as listener comprehension |
| Pronunciation | Low/advanced | Forced alignment or pronunciation-specific acoustic model | General Whisper confidence is insufficient for phoneme-level scoring |
| Speaker identification | Low/advanced | Add diarization to system audio | More compute, latency, and consent complexity |

## Recommended architecture

### Do not add a second consumer to the existing frame queue

The current `DropOldestQueue` is a work queue, not a broadcast bus. If segmentation and an acoustic analyzer both call `get()` on the same queue, each will receive only some frames and effectively steal frames from the other.

Use explicit fan-out:

```text
AudioCapture
   |
   v
FrameRouter
   |----------------------|
   v                      v
segmentation queue     metrics queue
   |                      |
   v                      v
VAD + utterances       acoustic analyzer
```

Each output queue should remain bounded. Metrics may discard stale frames, but the system should count and surface drops because missing data can bias session statistics.

### Proposed typed events

Add types such as:

```python
@dataclass(slots=True)
class SpeechStateEvent:
    channel: str
    speaking: bool
    timestamp: float
    silence_s: float = 0.0

@dataclass(slots=True)
class AcousticMetrics:
    timestamp: float
    rms_db: float
    peak: float
    clipping_ratio: float
    pitch_hz: float | None
    voiced_probability: float

@dataclass(slots=True)
class TurnMetrics:
    utterance_id: str
    words: int
    speech_duration_s: float
    wpm: float
    filler_counts: dict[str, int]
    repetition_count: int
    confidence: float | None

@dataclass(slots=True)
class CoachingCue:
    kind: str
    message: str
    severity: str
    created_at: float
    expires_at: float
    evidence: dict[str, float | int | str]
```

The UI should consume a stable coaching-state model rather than every raw measurement.

### Fast acoustic lane

Run only inexpensive deterministic operations:

- VAD state and pause length.
- RMS and peak level.
- Clipping ratio.
- Rolling energy variation.
- Optional pitch estimate and voiced probability.
- Mic/system speaking overlap.

Update measurements frequently, but rate-limit visible coaching cues. A speaker should see one useful suggestion, not a flashing wall of telemetry.

### Turn-level transcript lane

After each completed mic utterance:

- Retain actual speech-active duration from the segmenter.
- Enable and retain Whisper word timestamps and probabilities.
- Compute per-turn and rolling WPM.
- Count fillers with phrase-aware matching.
- Detect repetitions, restarts, hedging, and excessive sentence length.
- Store evidence for session summaries.

Keep final utterance transcription even if a future streaming-ASR path is added. Streaming text can drive early cues, while the final transcript corrects the record.

### Slow reflective lane

Use an LLM only at a turn boundary, every 30-60 seconds, on demand, or after the session. Good uses include:

- Was the answer direct?
- Did it have a clear opening, supporting details, and a conclusion?
- Was it tailored to the audience or coaching profile?
- Which sentence could be shortened?
- What is one concrete rewrite to practice?

Prefer derived metrics plus a redacted transcript excerpt instead of sending an entire conversation. Structured JSON output will be easier to validate and render than free-form prose.

## Feedback design principles

### One cue at a time

Live coaching competes with the conversation for attention. Rank cues and display only the most actionable one, for example:

- “Slow down slightly”
- “Let the other person finish”
- “Pause before the next point”
- “Your mic is clipping”
- “Answer the question first”

### Use cooldowns and hysteresis

A threshold crossing should not immediately produce repeated alerts. Each cue needs:

- A minimum evidence window.
- Entry and exit thresholds.
- A cooldown after dismissal or resolution.
- A maximum display duration.
- Session-level suppression if the user repeatedly ignores it.

Example: show a pace cue only after at least 10-15 seconds of speech and sustained pace above the configured target, then wait at least 30 seconds before showing it again.

### Calibrate to the user

Volume, pitch, pace, and pause style differ by microphone, environment, language, physical characteristics, and context. Start a session with a short calibration or establish an adaptive baseline over the first few minutes. Use configurable goals rather than presenting one universal speaking style as correct.

### Separate observation from judgment

Prefer “You averaged 186 WPM during that answer” over “You sounded nervous.” Avoid inferring emotion, confidence, competence, or personality from vocal features unless the product has strong evidence and an explicit, well-scoped use case.

## Suggested configuration

Add a section similar to:

```toml
[coach]
enabled = true
channel = "mic"
mode = "interview"

# Pace
target_wpm_min = 120
target_wpm_max = 165
pace_window_s = 20.0

# Fillers and pauses
filler_phrases = ["um", "uh", "like", "you know", "sort of", "kind of"]
long_pause_s = 2.5
max_fillers_per_minute = 4.0

# Audio health
clipping_threshold = 0.98
min_level_db = -38.0

# Feedback behavior
live_cues = true
cue_cooldown_s = 30.0
max_visible_cues = 1
llm_review_interval_s = 0.0 # 0 = only on demand/session end

# Privacy
system_audio = false
store_transcripts = false
store_derived_metrics = true
cloud_review = false
```

Targets should be coaching-profile defaults, not hard-coded product truths.

## UI proposal

### Live view

- Current cue, visually dominant but compact.
- Speaking pace with a target band.
- Pause and filler counters for the current turn.
- Mic level/clipping indicator.
- Talk/listen balance.
- Clear recording/listening state.
- Optional raw transcript.

Avoid showing a constantly changing overall score during speech. It encourages users to optimize the number rather than communicate effectively.

### Turn review

After a substantial turn, show at most:

- One strength.
- One improvement.
- One evidence-backed metric.
- One optional rewrite or practice prompt.

### Session summary

- Speaking and listening time.
- Pace distribution rather than only an average.
- Filler counts by phrase and by minute.
- Pause distribution.
- Longest speaking turns.
- Repetition or restart examples.
- Audio-health incidents.
- Two or three prioritized practice recommendations.
- Comparison with the user's own recent baseline, if history is enabled.

## Privacy, consent, and security

The current application can capture both the user and other meeting participants. Speech coaching increases the likelihood of longer sessions and historical analysis, so privacy needs to be designed rather than added later.

Current concerns:

- Full transcripts and answers are stored in plaintext JSONL.
- System audio may capture people who have not consented.
- Recent transcript context and questions can be sent to the Claude CLI.
- Optional WebSearch broadens external disclosure.
- No visible retention, deletion, redaction, encryption, or provider-disclosure controls exist.

Recommended defaults:

- Mic-only coaching.
- Explicit opt-in for system audio.
- Prominent listening/recording indicator.
- No raw-audio retention.
- Ephemeral transcripts by default.
- Derived-metrics-only storage when possible.
- Configurable retention and a delete-session action.
- PII redaction before optional cloud review.
- A fully local mode.
- Clear disclosure of which components are local and which contact external providers.
- Consent guidance before enabling meeting capture.

## Licensing and distribution

No repository-level `LICENSE`, `COPYING`, or `NOTICE` file was found. If this is the owner's private project, internal modification is straightforward. Before redistribution, collaboration with third parties, or commercial release:

- Add an explicit project license.
- Pin dependency versions.
- Audit every Python package license.
- Review Whisper/model licenses and distribution requirements.
- Review Ollama model terms.
- Review Claude CLI/API and WebSearch terms for the intended product use.
- Add third-party notices where required.

This is an engineering observation, not legal advice.

## Incremental implementation roadmap

### Phase 1: Deterministic coaching MVP

Goal: deliver useful coaching without changing the STT model or adding new cloud calls.

- Add `[coach]` configuration and validation.
- Add typed coaching events and a session aggregator.
- Retain segmenter speech-active duration.
- Compute mic-only per-turn WPM.
- Count lexical fillers.
- Track pause duration and speaking-turn duration.
- Add RMS, clipping, and audio-level cues.
- Count dropped frames/utterances and expose data-quality warnings.
- Add a compact live coach panel.
- Add a session summary written to a separate schema.
- Unit-test all metrics with deterministic synthetic data.

### Phase 2: Better live feedback

- Fan out audio frames safely.
- Publish VAD transition events.
- Add rolling pace estimates.
- Add per-device/noise calibration.
- Implement cue priority, cooldowns, and hysteresis.
- Add talk/listen balance and overlap detection.
- Add optional pitch and energy-variation measures.

### Phase 3: Stronger transcript evidence

- Retain Whisper word timestamps and probabilities.
- Add phrase-aware filler detection.
- Add repetition and verbal-restart analysis.
- Measure incomplete thoughts before continuity merging.
- Evaluate a rolling/streaming ASR path while preserving final transcription.
- Add multilingual filler and pace profiles where supported.

### Phase 4: Reflective coaching

- Add on-demand or end-of-session LLM review.
- Use a versioned structured-output schema.
- Redact transcript content before cloud processing.
- Provide evidence for every recommendation.
- Add coaching profiles such as interview, presentation, sales call, and practice speech.

### Phase 5: Validation and product hardening

- Build a labeled evaluation set covering noise, microphones, accents, languages, and speaking styles.
- Measure p50/p95 cue latency and queue-drop rates.
- Measure filler precision/recall and pause-event accuracy.
- Test whether users notice and act on cues without increased distraction.
- Add retention, export, and deletion controls.
- Package platform dependencies and document hardware expectations.
- Audit accessibility and keyboard-only operation.

## Proposed code ownership by file

| File or module | Proposed change |
|---|---|
| `ambientqa/bus.py` | Add coaching and speech-state event dataclasses; add drop counters or observable queue stats |
| `ambientqa/audio.py` | Fan out mic frames or feed a dedicated frame router without blocking capture |
| `ambientqa/segmenter.py` | Emit VAD transitions and retain speech-active/silence timing |
| `ambientqa/stt.py` | Retain word timestamps, probabilities, and segment confidence |
| `ambientqa/coach.py` | New deterministic acoustic and transcript analyzers |
| `ambientqa/coach_session.py` | New rolling/session aggregation and summary generation |
| `ambientqa/config.py` | Add typed `CoachConfig` and validation |
| `config.toml` | Add documented `[coach]` defaults |
| `ambientqa/__main__.py` | Compose fan-out, analyzers, coaching queues, and lifecycle tasks |
| `ambientqa/ui.py` | Add live cue, metrics, calibration, and session-summary views |
| `ambientqa/logging_.py` | Add privacy-aware retention and a versioned coaching-record schema |
| `tests/test_coach_*.py` | Add metric, timing, cue-policy, queue-drop, and UI tests |

## MVP acceptance criteria

### Functional

- The user can enable coach mode without enabling question answering.
- Mic audio produces pace, filler, pause, level, and clipping metrics.
- System audio is disabled by default.
- No coaching analysis blocks capture, segmentation, or transcription.
- Only one live cue is visible at a time.
- Cue cooldowns prevent repeated notifications.
- A session summary is available without requiring a cloud model.
- Existing Q&A behavior remains available as an optional separate mode.

### Performance

- Audio-level and clipping updates appear within 250 ms under normal load.
- Pause cues appear within 250 ms of crossing the configured threshold.
- Turn metrics appear within three seconds of ordinary speech ending on the preferred GPU path.
- Queue drops are counted and visible.
- CPU fallback is clearly reported and does not crash the application.

### Quality

- WPM uses speech-active duration rather than total buffered duration.
- All recommendations expose their supporting observation or metric.
- A metric is marked unavailable rather than guessed when its input data is incomplete.
- Tests cover threshold boundaries, cooldowns, missing audio, queue overflow, and channel separation.
- Coaching does not penalize an accent or infer emotion/personality from voice features.

### Privacy

- The default mode stores no raw audio.
- Transcript retention is explicit and configurable.
- System-audio capture requires an explicit action.
- The UI clearly identifies local versus external processing.
- Users can delete a session and its derived records.

## Testing strategy

Use several layers:

1. **Pure unit tests:** WPM, filler matching, pause classification, clipping, rolling windows, cue priority, cooldowns, and session aggregation.
2. **Synthetic audio tests:** silence, tones, clipped waveforms, controlled energy changes, and timed speech-state patterns.
3. **Recorded-fixture tests:** anonymized or consented samples with labeled fillers, pauses, and speaking intervals.
4. **Pipeline tests:** confirm frame fan-out does not starve segmentation and that overflow counters work.
5. **UI tests:** stable cue replacement, no flicker, pause behavior, and summary rendering.
6. **Human evaluation:** distraction, usefulness, false alerts, and whether recommendations match listener judgments.

Do not use the existing raw session logs as a reusable product dataset without confirming consent and retention expectations.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Serial final-utterance STT | Delayed turn feedback | Keep acoustic feedback independent; evaluate streaming ASR later |
| CPU fallback | High latency | Report degraded mode, support smaller models, document hardware targets |
| Queue overflow | Biased metrics | Count drops, mark incomplete windows, never silently report authoritative summaries |
| Continuity merge delay | Late feedback | Analyze original VAD boundaries before merging |
| Whisper omits fillers | Under-counting | Treat transcript counts as estimates; evaluate an acoustic filled-pause detector |
| Device-dependent amplitude | False volume cues | Calibrate per device/session and use relative thresholds |
| Crosstalk/loopback delay | False interruptions | Prioritize mic, synchronize clocks, require sustained overlap |
| Too many live cues | User distraction | One-cue policy, priorities, cooldowns, and user-selectable categories |
| Subjective scoring | Misleading feedback | Evidence-backed observations, personal baselines, and no universal “good speaker” score |
| Plaintext transcripts | Privacy exposure | Ephemeral defaults, configurable retention, encryption/redaction if stored |

## Recommended first milestone

Build a mic-only coach mode that reuses the existing capture, VAD, Whisper, UI, configuration, and logging infrastructure. Implement five metrics:

1. Rolling and per-turn WPM.
2. Filler counts by phrase and per minute.
3. Pause duration and long-pause count.
4. Input level and clipping.
5. Speaking/listening ratio when system audio is explicitly enabled.

Add one stable live cue plus an end-of-session summary. Keep all scoring deterministic and local in this milestone. This is the smallest scope that demonstrates genuine speech-coaching value while preserving the strongest qualities of the existing architecture.

