# Ambient — Call-Center Agent-Assist Prototype

Demo package: feature list, talking points, and a step-by-step live demo script.
The slide deck lives next to this file: open `callcenter-deck.html` in a browser
(arrow keys / space to navigate, `F` for fullscreen).

---

## The pitch in three sentences

Agents put customers on hold to search knowledge bases; new agents take months
to ramp. Ambient listens to both sides of a live call, ignores everything
that is not a real question, and puts a glanceable answer card in front of the
agent about four seconds after the customer finishes asking. All speech
processing runs on the agent's own machine — no call audio ever leaves it.

---

## Feature list

### Hearing the call
- **Both sides captured, zero telephony integration.** The customer's voice is
  whatever the machine plays (softphone, WebRTC, any dialer) — captured via
  PipeWire monitor sources on Linux and WASAPI loopback on Windows. The agent's
  own voice comes from the mic. Two channels, treated differently end to end.
- **Follows the audio wherever it goes.** With no device pinned, every output
  endpoint is watched at once and the one actually carrying speech wins —
  headset, speakers, or monitor, handover is automatic.
- **Deaf-channel tripwire.** A channel that is open but silent too long shows
  `SILENT Ns ⚠` in the status bar instead of lying with "on".
- **Production-grade mic path.** WebRTC noise suppression + automatic gain
  control in front of transcription (a raw over-boosted mic clips loud speech —
  measured 1.7% of samples — and garbles the transcript).
- **Crash-isolated capture.** Each audio stream is its own subprocess; a dying
  capture process can never take the app down, and shutdown is instant.

### Deciding what deserves an answer
- **A two-stage question gate, measured not guessed.** Free heuristics
  fast-accept explicit questions instantly and reject filler, rhetorical tags
  ("...right?"), and speech aimed at other people; uncertain utterances go to a
  local LLM gate (~0.5–0.6 s). Labelled eval set: **26/26, zero false
  positives** in balanced mode — an unasked answer interrupting the agent is
  treated as the worst failure.
- **Per-channel policy.** The customer channel is mined freely — "I was
  wondering whether my plan covers roaming" becomes an answer. The agent's own
  channel only answers what is explicitly asked, so narration is never mined.
- **Command-form asks count.** "Talk me through the cancellation policy." needs
  no question mark.
- **Re-asks recover.** If the first answer missed (a mishearing), asking again —
  even as a statement — produces a fresh answer instead of being deduplicated
  away.

### Answering
- **Glanceable cue cards, ~4 s after the question.** First line is sayable
  verbatim; below it, two or three keyword bullets. Built to be read in one
  glance mid-sentence, streamed word by word as it generates.
- **Conversation-aware.** Follow-ups resolve against previous answers
  ("elaborate on the second option"), a scenario stated just before a question
  constrains the answer, and things said minutes ago are used only when the
  question actually refers back.
- **Knowledge profiles per queue.** A markdown profile (product names, policy
  vocabulary, background) tunes transcription hotwords, gating, and answer
  pitch. Swap profiles per campaign/queue with one keypress.
- **Current-facts lookup.** Questions about prices, versions, or availability
  trigger a web search instead of trusting model memory — being confidently
  wrong is worse than being slow, so this is reserved for exactly the facts
  that move.

### Self-correction (the two-pass system)
- **Answer audit.** After every delivered answer, an auditor with a wider
  transcript window re-reads it and replaces the card — marked `revised` — only
  if it was materially wrong: missed constraint, misheard question, factual
  error. Style is never grounds; a correct card never flickers.
- **Missed-question sweeper.** Every 25 s, rejected utterances get a second
  look from a fast model with full context; genuinely missed asks come back as
  late answer cards. Nothing the customer asks stays silently dropped.

### Privacy & compliance posture
- **All speech stays on the machine.** Whisper (transcription) and the question
  gate (Ollama) run locally on the agent's GPU. Only the final answer
  generation calls an external model — and receives only the gated question
  plus a short context window, never raw audio.
- **Local session logs, replayable.** Every utterance and decision lands in a
  local JSONL; a session browser replays any call for QA review and coaching.
  Logs are git-ignored by design.

### Operations
- **Runs on Windows and Linux from one codebase** (platform-selected audio
  backends). One launcher script bootstraps everything on Linux.
- **Multiple instances are supported** and the status bar shows a live
  `instances:N` counter.
- **Degrades, never dies.** Gate model down → heuristics-only. Audit or sweeper
  failing → the live path is untouched. Whisper CUDA failure → CPU fallback.
- **345 automated tests**, plus a labelled gate-accuracy eval that fails CI on
  any false positive.

### Measured numbers (this machine: RTX 4080)
| Stage | Latency |
|---|---|
| Transcription (warm, GPU) | ~0.2–0.3 s per utterance |
| Question gate (LLM stage, warm) | ~0.5–0.6 s |
| Answer card (question → readable) | ~3.5–4.5 s |
| Answer audit correction, when needed | ~8 s behind the card |
| Missed-question recovery | ≤ ~25 s |
| Model preload at launch | ~10 s, in the background |

---

## Live demo script (~8 minutes)

**Setup beforehand:** close other instances; pick or write a call-center
profile (`profiles/`, `x` key swaps); have a "customer call" audio ready — a
recorded support call, a colleague on an actual softphone call, or a phone
video played through the speakers. Anything the machine plays is the customer.

1. **Launch.** `./run.sh`. Point at the status bar: `whisper:cuda`,
   `instances:1`, mic and sys both `on`. Mention the model preloading in the
   background — by the time you finish this sentence it's hot.
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
   If the gate misses it, either press `a` (instant force-answer of the last
   utterance) or wait — the sweeper resurrects genuine asks within ~25 s.
7. **Show the QA story.** Press `l`: replay a recorded session, decision by
   decision — every rejection has a logged reason. This is the
   compliance/coaching surface.
8. **Close on posture.** Pull the network cable if you're feeling theatrical:
   transcription and gating keep running — only answer generation needs the
   network, and that component is swappable.

**Honest framing for Q&A:** this is a single-seat desktop prototype. The
production path is: per-queue knowledge profiles backed by the real KB,
CRM/ticket integration on the answer cards, speaker diarization for
speakerphone scenarios, and swapping the answer model for a
compliance-approved endpoint. The hard parts — hearing both sides, deciding
what deserves an answer, and correcting itself — are the parts already built
and measured.
