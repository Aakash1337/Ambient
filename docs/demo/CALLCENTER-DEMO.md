# Ambient — Call-Center Agent-Assist Prototype

Demo package: feature list, talking points, and a step-by-step live demo script.
The slide deck lives next to this file: open `callcenter-deck.html` in a browser
(arrow keys / space to navigate, `F` for fullscreen).

---

## The pitch in three sentences

Agents put customers on hold to search knowledge bases; new agents take months
to ramp. Ambient listens to both sides of a live call, ignores everything
that the gate does not classify as a real question, and streams a glanceable answer card as the
external answer model responds. Raw audio, transcription, and primary gating stay on the agent's
machine; transcript-derived question/context data is sent to Claude, and response time varies
substantially by host and network.

---

## Feature list

### Hearing the call
- **Both sides captured, zero telephony integration.** The customer's voice is
  whatever the machine plays (softphone, WebRTC, any dialer) — captured via
  PipeWire monitor sources on Linux, WASAPI loopback on Windows, or a configured virtual
  CoreAudio input such as BlackHole on supported Apple-Silicon macOS. The agent's own voice comes
  from the mic. Two
  channels, treated differently end to end.
- **Follows the audio wherever it goes.** With no device pinned, every output
  endpoint is watched at once and the one actually carrying speech wins —
  headset, speakers, or monitor, handover is automatic.
- **Deaf-channel tripwire.** A channel that is open but silent too long shows
  `SILENT Ns ⚠` in the status bar instead of lying with "on".
- **Measured Linux mic path.** The configured Linux host adds WebRTC noise suppression + automatic
  gain control before transcription (its raw over-boosted mic clipped 1.7% of samples). That is not
  a Mac claim: CoreAudio capture does not add this Linux launch-time processing.
- **Platform-specific capture lifecycle.** Linux uses one `parec` subprocess per stream. Windows
  and macOS use blocking PortAudio/CoreAudio streams on capture threads; stream failures are routed
  through the orchestrator, but they do not have Linux's subprocess isolation guarantee.

### Deciding what deserves an answer
- **A two-stage question gate, measured not guessed.** Free heuristics
  fast-accept explicit interrogatives instantly and reject filler, rhetorical tags
  ("...right?"), and speech aimed at other people; uncertain utterances go to a
  local LLM gate. The 0.5–0.6 s observation and **26/26, zero false positives** result came from a
  small controlled development eval, not a production-call guarantee; Mac gate latency remains
  unmeasured on the supported Apple-Silicon release.
- **Per-channel policy.** The customer channel is mined freely — "I was
  wondering whether my plan covers roaming" becomes an answer. The agent's own
  channel only answers what is explicitly asked, so narration is never mined.
- **Command-form asks count.** "Talk me through the cancellation policy." needs
  no question mark. On the operator's `explicit` channel it is a zero-call heuristic accept; on
  the permissive other-speaker `full` channel it requires Ollama's semantic approval and fails
  closed if that gate is unavailable.
- **Re-asks recover.** If the first answer missed (a mishearing), asking again —
  even as a statement — produces a fresh answer instead of being deduplicated
  away.

### Answering
- **Glanceable streamed cue cards.** First line is sayable verbatim; below it, two or three keyword
  bullets. Cards stream as Claude generates them, but there is no four-second SLA: a recent live
  session observed 3.8–21.8 s first-pass end-to-end latency, with 6 of 12 answers over 10 s.
- **Conversation-aware.** Follow-ups resolve against previous answers
  ("elaborate on the second option"), a scenario stated just before a question
  constrains the answer, and things said minutes ago are used only when the
  question actually refers back.
- **Knowledge profiles per queue.** A markdown profile (product names, policy
  vocabulary, background) tunes gating and answer pitch. Transcription hotwords are a deliberate
  `stt.profile_hints` opt-in and are off by default because the wrong profile can bias unrelated
  speech. Swap profiles per campaign/queue with one keypress.
- **Current-facts lookup.** Questions about prices, versions, or availability
  trigger a web search instead of trusting model memory — being confidently
  wrong is worse than being slow, so this is reserved for exactly the facts
  that move.

### Self-correction (the two-pass system)
- **Optional answer audit.** The shipped default is `verify = "off"`. When enabled, an auditor with
  a wider transcript window re-reads every delivered answer and replaces a materially wrong card;
  it adds another Claude call and is best-effort.
- **Missed-question sweeper.** Every 25 s, rejected utterances get a second
  look from a model with wider context; likely misses can return as late cards. The interval is
  scheduling cadence, not a delivery bound. A historical audit, before the current freshness cap,
  saw recoveries roughly 44–210 s after the original utterance; that result helped motivate the
  current 60 s candidate lifetime. The release discards older candidates before recovery, but 60 s
  is not a delivery SLA and false negatives can still remain missed.

### Privacy & compliance posture
- **Raw audio stays local; transcript-derived text does not always.** Whisper and the primary
  Ollama gate run locally. Claude primary answers receive the question/Agent turn, recent
  transcript, Q&A/dialogue history, active profile context, and any grounding excerpts. The
  default sweep also sends rejected candidates and wider context; optional verification sends the
  raw transcription, delivered answer, history, profile, and grounding. WebSearch adds outbound
  queries. Claude never receives raw audio from Ambient.
- **Local session logs, replayable.** Every utterance and decision lands in a
  local JSONL; a session browser replays any call for QA review and coaching.
  Logs are plaintext and git-ignored; that is not encryption, access control, or retention policy.
- **Consent is required.** Before capture, obtain informed consent from every participant for
  transcription, local logging, and external model processing, provide required notices, and
  comply with applicable law, contracts, employer policy, and meeting-platform rules. Do not use
  the prototype for covert transcription.

### Operations
- **Targets Windows, Linux, and macOS 14+ on Apple Silicon from one codebase**
  (platform-selected audio backends). The Mac release deliberately rejects Intel/Rosetta because
  the required PyTorch stack has no current security-supported x86_64 wheel. macOS is a release
  candidate: fake-backend tests exist, but no recorded live Apple-Silicon acceptance run ships
  with this repository.
- **One live instance is the supported mode.** The app refuses a second pipeline by default;
  `--allow-multiple` is an unsafe diagnostics override that duplicates capture and paid calls.
- **Degrades, never dies.** Gate model down → zero-call accepts still work, while semantic routes
  (including customer-channel commands) fail closed. Audit or sweeper
  failing → the live path is untouched. Whisper CUDA failure → CPU fallback on Windows/Linux;
  macOS intentionally starts Whisper on CPU/int8.
- **Automated tests plus a labelled gate eval.** These are regression checks, not a substitute for
  real audio-device, privacy-permission, or latency acceptance on the release hardware.

### Observed numbers (development RTX 4080 host; not macOS guarantees)
| Stage | Latency |
|---|---|
| Transcription (warm, GPU) | ~0.2–0.3 s per utterance |
| Question gate (LLM stage, warm) | ~0.5–0.6 s |
| Controlled one-shot answer process | ~3.5–9 s in the original benchmark |
| Later live-session first pass, end to end | 3.8–21.8 s; 6/12 over 10 s |
| Answer audit correction, when needed | ~8 s behind the card |
| Historical recovery before the freshness cap | ~44–210 s after the utterance |
| Current sweep candidate lifetime | 60 s maximum; not a delivery SLA |
| Model preload at launch | ~10 s, in the background |

No corresponding Apple-Silicon table has been recorded. Whisper is intentionally CPU-only there;
Ollama can use the Apple GPU. Intel/Rosetta is not a supported release target.

---

## Live demo script (~8 minutes)

**Setup beforehand:** close other instances; pick or write a call-center
profile (`profiles/`, `x` key swaps); have a "customer call" audio ready — a
recorded support call, a colleague on an actual softphone call, or a phone
video played through the speakers. Anything routed into the configured system-capture device is
the customer. Obtain every participant's consent and do not use a real customer recording unless
its use, transcription, external processing, and replay are authorized.

1. **Launch.** Use `./run.sh` on Linux or `./run-macos.sh` on supported Apple-Silicon macOS. Point at
   `instances:1` and verify mic/sys both show `on` with moving meters. Expect
   `whisper:cuda` on the configured NVIDIA host and CPU/int8 on macOS. Wait for actual ready state;
   preload time is hardware/model dependent.
2. **Be the agent.** Talk normally — narrate what you're doing. Point out that
   nothing happens: your narration scrolls as transcript but produces no cards.
   The gate is the product.
3. **Play the customer.** Start the call audio. Watch questions from the
   *customer's* side get transcribed (`[sys]`), gated, and answered as cards
   while small talk and statements pass by silently.
4. **Ask a follow-up out loud** referencing a previous card: "What about the
   second option?" — the answer resolves against what was actually answered.
5. **State a scenario, pause, then ask.** "So if the customer's data keeps
   changing… which approach should I recommend?" — the answer honors the
   scenario, not the abstract question.
6. **Show recovery.** Mumble a question badly or phrase it as a statement.
   If the gate misses it, press `a` to force-answer the last utterance. The default sweep may also
   recover it later, but its 25 s schedule is not a response-time guarantee.
7. **Show the QA story.** Press `l`: replay a recorded session, decision by
   decision — every rejection has a logged reason. This is a prototype QA/coaching surface, not an
   enterprise compliance control.
8. **Close on posture.** Pull the network cable if you're feeling theatrical:
   transcription and gating keep running — Claude-backed answers, sweep/verification, and web
   lookup need the network, and those external components are swappable.

**Honest framing for Q&A:** this is a single-seat desktop prototype, and Mac compatibility remains
pending real-device acceptance. The
production path is: per-queue knowledge profiles backed by the real KB,
CRM/ticket integration on the answer cards, speaker diarization for
speakerphone scenarios, and swapping the answer model for a
compliance-approved endpoint. The capture, gating, and recovery paths are implemented and measured
on the named development environment; quality and latency still require validation on each release
machine and real call path.
