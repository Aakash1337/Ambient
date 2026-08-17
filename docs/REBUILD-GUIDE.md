# Rebuilding Ambient Q&A From Scratch

A staged, hands-on guide to building this system yourself — the order to build it in,
what each stage must do, how to *prove* each stage works before moving on, and the traps
that will otherwise cost you hours or (in two documented cases) an entire recorded
session. Read `ARCHITECTURE.md` alongside this; it explains *why* each piece is shaped
the way it is, while this document is the *how* and *in what order*.

The staging principle: **each stage is independently testable against reality** (your
actual mic, your actual GPU, your actual Ollama), and no stage depends on a later one.
Build stage N, verify it with your own voice, only then start N+1. The single biggest
mistake you can make rebuilding this is writing the whole pipeline before testing any of
it — every hard bug in this system's history was found at a stage boundary, not in review.

- [Stage 0 — Environment](#stage-0--environment)
- [Stage 1 — Device discovery + raw capture](#stage-1--device-discovery--raw-capture)
- [Stage 2 — VAD segmentation](#stage-2--vad-segmentation)
- [Stage 3 — Transcription](#stage-3--transcription)
- [Stage 4 — Heuristic gate (Stage A)](#stage-4--heuristic-gate-stage-a)
- [Stage 5 — Semantic gate (Stage B, Ollama)](#stage-5--semantic-gate-stage-b-ollama)
- [Stage 6 — Answering (claude -p)](#stage-6--answering-claude--p)
- [Stage 7 — The pipeline + TUI](#stage-7--the-pipeline--tui)
- [Stage 8 — Hardening (where the real product lives)](#stage-8--hardening-where-the-real-product-lives)
- [Porting checklist for a new machine](#porting-checklist-for-a-new-machine)
- [Suggested rebuild exercises](#suggested-rebuild-exercises)

---

## Stage 0 — Environment

What you need and why each piece is non-negotiable:

| Piece | Purpose | Notes |
|---|---|---|
| Windows 11 | WASAPI loopback is the whole sys-audio story | Linux/macOS need a different capture layer entirely (PulseAudio monitor sources / BlackHole) |
| Python 3.11 via `py -3.11` | runtime | **Never the Microsoft Store Python** — it runs in an app container whose *separate* mic consent defaults to Deny. Symptom: every mic fails on every host API with `-9999`, while loopback works fine (loopback reads a render endpoint, which mic consent doesn't gate), and every Windows privacy setting *looks* correct. Cost hours to diagnose. |
| Dedicated `.venv` in the project | isolation | The machine this was built on had an unrelated venv first on PATH. `setup.ps1` encodes the correct bootstrap. |
| NVIDIA GPU + `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` pip packages | Whisper at conversational speed | See Stage 3 for the DLL trap. CPU int8 fallback works but is several× slower — too slow to feel live. |
| Ollama + `gemma4:e2b` | the question gate | Benchmarked at 8/8 classification accuracy, ~530 ms warm. Any small instruct model *could* work, but re-benchmark before trusting it (see Stage 5). |
| Claude CLI, signed in | the answerer | `claude -p "test"` from a terminal must work before anything in Stage 6 will. |

Python packages: `pyaudiowpatch soxr numpy silero-vad onnxruntime faster-whisper textual`
(+ `tomli` on <3.11). `pyaudiowpatch` specifically — it is the PyAudio fork that exposes
WASAPI loopback devices; stock PyAudio cannot see them at all.

**Verify stage 0:** `py -3.11 --version`, `ollama run gemma4:e2b "hi"`,
`claude -p "say ok"`, and `python -c "import pyaudiowpatch, soxr, faster_whisper, textual"`
all succeed inside the venv.

## Stage 1 — Device discovery + raw capture

**Build:** a script that enumerates devices (input devices + everything from
`get_loopback_device_info_generator()`), then a capture loop: open a stream at the
device's *native* rate and channel count, read 25 ms buffers, downmix to mono by
averaging, resample to 16 kHz with `soxr.ResampleStream` (streaming API, not one-shot —
you are resampling a continuous stream, and chunk-independent resampling clicks at
boundaries), and print an RMS level meter per frame.

Key concepts you must come out of this stage understanding:

- **Windows enumerates every physical device once per host API** (MME, DirectSound,
  WASAPI, WDM-KS). The truncated duplicate names in a device list are not a bug. Only
  the WASAPI entries matter here, and loopback endpoints exist only under WASAPI.
- **A loopback endpoint is a capture device that records what an *output* renders.**
  Which output your meeting app renders to is invisible to you and changes between
  sessions — this fact eventually forced the multi-endpoint design (Stage 8).
- **Capture must live on OS threads.** `stream.read()` blocks; asyncio comes later, and
  the bridge between the two worlds (a lock-guarded deque drained via
  `call_soon_threadsafe`) is worth building carefully once and never touching again.

**Verify:** speak — mic meter moves. Play a video — exactly the loopback endpoint of the
device it plays through moves, and the others stay flat. *Notice how easy it is to pick
the wrong one; remember that feeling for Stage 8.*

**Traps:**
- Frames from some host APIs can contain garbage that is not valid float32. Compute RMS
  with float64 accumulation (`np.multiply(x, x, dtype=np.float64)`) and coerce
  non-finite results to 0, or one bad frame poisons your meter (and later, the arbiter).
- Never let the consumer's speed affect the capture thread. If your print statement is
  slow and `read()` overflows, you'll hear it as gaps. `exception_on_overflow=False` and
  a drop-oldest handoff are the pattern.

## Stage 2 — VAD segmentation

**Build:** per-channel utterance segmentation on top of the frame stream. Silero VAD via
ONNX on **CPU** (it's tiny; the GPU belongs to Whisper). Maintain: a 300 ms pre-roll
ring buffer (prepended when speech starts, because VADs fire late and clip onsets);
accumulate speech; emit when 900 ms of trailing silence passes; force-flush past 20 s;
discard under 400 ms.

The 900 ms number is the latency/coherence tradeoff for the entire system — every answer
is delayed by exactly this much silence, but shorter values split thoughts at breaths
and produce the fragment storm that Stage 8's merger exists to clean up.

**Verify:** speak sentences with natural pauses; print `(channel, duration, time)` per
utterance. Sentences should come out whole; a 2-second thinking pause mid-sentence *will*
split — that's expected and handled later. Coughs and keyboard noise should not emit.

**Trap:** one segmenter instance per channel, no shared state. Mic and sys speech
overlap constantly; shared silence tracking interleaves them into hash.

## Stage 3 — Transcription

**Build:** a single-worker transcriber: `faster-whisper`, `large-v3-turbo`, CUDA fp16,
serial consumption from a bounded queue, CPU int8 fallback.

The Windows CUDA trap, in full, because it *will* bite you: pip's CUDA DLLs land in
`site-packages/nvidia/*/bin`, which Python ≥3.8 does not search for extension-module
dependencies. And CTranslate2 resolves cuBLAS **lazily at first inference** — model
construction succeeds, your app reports "ready on cuda", and minutes later the first
real utterance throws `Library cublas64_12.dll is not found`. The fix has three parts:

1. `os.add_dll_directory()` for every `nvidia/*/bin` dir,
2. **also** prepend them to `PATH` (the lazy resolution path consults only PATH),
3. materialise the transcription generator *inside* the try block (`segments` is lazy;
   errors surface on consumption) and fall back to CPU *at runtime*, loudly.

Hygiene that makes ambient (as opposed to push-to-talk) STT usable:
`condition_on_previous_text=False` (kills hallucination feedback loops), a blocklist for
silence hallucinations ("Thank you.", "Thanks for watching!"), and **keep Whisper's
punctuation untouched** — the trailing `?` encodes rising intonation and becomes your
single strongest question signal downstream.

**Verify:** speak → text within ~1 s, correctly punctuated. Then deliberately break
CUDA (rename the DLL dir) and confirm the CPU fallback engages with a warning instead
of a crash. Check silence for a minute — no hallucinated text should pass.

## Stage 4 — Heuristic gate (Stage A)

**Build:** pure functions, no I/O, exhaustively unit-tested. This is where test-driven
development actually pays: every rule exists because of a real utterance class, and the
tests are the specification. Rules in order (first match wins): too-few-words →
filler-only → tag/rhetorical endings (`right?`, `you know?`) → vocative ("Hey Sarah,
can you…") → near-duplicate of a recently answered question (token-set Dice ≥ 0.85) →
**fast-accept** explicit interrogatives (ends `?` + starts with an interrogative after
skipping so/well/okay/then/and/but) → trailing-fragment (ends in a word that cannot end
a sentence) → no-content-words (< 2 non-filler non-stopword tokens).

Three ordering/curation lessons encoded in the current rule set:

- Fast-accept must precede the content-word check ("How are you?" is all stopwords).
- The trailing-fragment word list must **exclude** `this/that/these/those/like/when` —
  they *can* end sentences, and listing them rejected real questions ("…how would you
  fix this.") while adding merge-hold latency to them. When ambiguous, pass it on: the
  semantic gate catches a wrongly-kept fragment; a wrongly-rejected question is lost
  silently.
- `no_content_words` is the most important false-positive guard in the system: without
  it, "uh, um, so, the thing is" reaches the LLM, which will *invent* a question from
  surrounding context (it is instructed to rewrite — it will find something to rewrite).

Also build here: the **answer-echo** detector (user reading a recent answer aloud must
not be re-answered). Use one-directional containment — |utterance content words ∩ answer
words| / |utterance content words| ≥ 0.5 — not symmetric similarity: answers are far
longer than utterances, so any symmetric score sits near zero. Exempt utterances with
"need markers" (wonder/explain/confused/what/how…) so follow-up questions about the
answer still get through.

**Verify:** the unit suite, plus a labelled evaluation set (see `scripts/eval_gate.py`:
explicit questions, implicit requests, referential questions, rhetorical tags,
fragments, vocatives, self-narration). Weight false positives as the worst failure —
an unasked answer interrupts; a missed question is silently recoverable via the
force-answer key.

## Stage 5 — Semantic gate (Stage B, Ollama)

**Build:** an HTTP client for `POST /api/chat` and a carefully-worded system prompt.
The request body, where every field is a scar:

```json
{
  "model": "gemma4:e2b",
  "messages": [...],
  "think": false,          // ← without this: EMPTY responses (reasoning model
                           //   burns its token budget thinking). #1 integration fact.
  "stream": false,
  "keep_alive": "30m",     // cold load ~67s, warm ~0.7s → warm up at startup,
                           //   keep it warm forever
  "options": {"temperature": 0, "num_predict": 64},
  "format": { ...JSON schema for {"q": bool, "query": str}... }
}
```

Parse defensively: accept only the literal JSON boolean `true` for `q` (a proxy or model
violating the schema can return the *string* `"false"`, which is truthy in Python), and
reject accepts with an empty rewrite. **Ignore any confidence field** — measured
constant at 0.95 regardless of input. Strictness lives in the prompt (three swappable
TRUE/FALSE definition blocks: strict/balanced/eager), never in a numeric threshold.

The prompt's two load-bearing instructions, which your evaluation set from Stage 4 must
regression-test:

1. Context (last 6 transcript lines) is for **referent resolution only** — resolving
   "that one"/"the second one" *inside* the current utterance — never for supplying a
   topic the utterance lacks.
2. The decisive test: the speaker must be expressing that they **don't know** something
   or **want** information. Stating a plan, asserting a fact, narrating work — not
   asking, even when it sounds substantive. Include worked examples in the prompt; they
   constrain the model far better than definitions.

The rewrite is the payoff of Stage B: "what about the second one?" becomes a
self-contained query with the referent inlined, which is what makes the answerer's job
possible.

**Verify:** run the labelled eval set through the *real* model, all three modes.
Balanced should hit zero false positives on your set before you trust it live.
Benchmark warm-call latency; if you swapped models, also re-verify the empty-response
and confidence behaviours — both are model-specific.

## Stage 6 — Answering (claude -p)

**Build:** a bounded pool of one-shot subprocess invocations:

```
claude -p "<context + question>" --model claude-sonnet-5
       --system-prompt "<style prompt>"
       --allowed-tools "" --strict-mcp-config --mcp-config {"mcpServers":{}}
       --output-format stream-json --include-partial-messages --verbose
```

Why one-shot, forever: a persistent `--input-format stream-json` session **merges
messages sent while a turn is in flight** — three sends produced two results, one
containing two answers fused together. Unfixable from outside; do not revisit. The 6–9 s
per-invocation overhead (CLI + node + network, invariant across models and flags) is
acceptable *only* because answering is asynchronous — never put a `claude` call in the
gate path.

Parse the stdout JSONL for `text_delta` events (stream them to the UI keyed by question
id), keep an `assistant`/`result` fallback for the final text, and retain raw bytes so a
CLI format change degrades to an ugly answer instead of no answer. Semaphore-bound the
pool (4), kill on a 45 s timeout, and expect out-of-order completion — bind every answer
to its question's id.

Prompt-design findings that transfer to any answerer you write:

- **Worked examples anchor length; numbers don't.** A bare word cap degenerates into
  comma-jammed keyword lists; "explain each point" into essays. Show the model one
  answer of the target shape.
- The `cue` style (default) optimises for the real constraint — the reader is
  mid-sentence and gets one glance: one ≤25-word sentence sayable verbatim, then 2–3
  bullet fragments of ≤6 words. Forbid markdown (it's spoken text) **except** fenced
  code, which is exempted from the word budget and preserved exactly.
- Instruct "say 'not sure' rather than guessing" — and know its limit: for facts that
  *changed after training* (product renames, versions, pricing) the model is confidently
  wrong, not unsure. Fix: a narrow regex trigger (`needs_current_facts`) grants
  WebSearch *and* a prompt block saying "your memory is stale for this, search FIRST" —
  merely allowing the tool measured as useless (model still answered from memory, still
  wrong). Keep the trigger rare: lookups cost 15–17 s vs 3.5–7 s; the shipped patterns
  fire on 0.5% of recorded questions.

**Verify:** fire 4 questions concurrently; wall clock should approximate the slowest
single answer (measured 6.0 s vs 22.2 s serial). Ask a "what is X called now" question;
confirm the lookup fires, and that an ordinary question doesn't pay for it.

## Stage 7 — The pipeline + TUI

**Build:** the controller that wires stages 1–6 with bounded drop-oldest queues, plus a
Textual read-only pane (transcript lines + Q&A cards + status bar + hotkeys).

The consumer loop's ordering contract (get this right and concurrency stays safe):
everything *ordered* happens inline — echo suppression, fragment merging, context
append, **and snapshotting the context each question will be judged/answered against** —
then gating detaches as a semaphore-bounded task. Measured reason: gating inline
serialised every answer behind a ~900 ms call; snapshotting inside the detached task
would let a late-running gate judge an utterance against a conversation that had moved
on.

UI specifics that will otherwise surprise you:

- Flatten model markdown for display, but stash fenced code behind sentinels first —
  `*args, **kwargs` is valid emphasis syntax and bullet regexes eat `- ` code lines.
- Stream-render by re-rendering the *whole accumulated* answer at ~10 Hz, not by
  appending deltas (fences split across deltas corrupt otherwise). And never schedule a
  0-delay Textual timer — `Timer` divides by its interval; deltas >100 ms apart (normal!)
  produce exactly that and it detonates as `ZeroDivisionError` at teardown. Render
  past-due flushes synchronously.
- The `a` key (force-answer last utterance, gate bypassed) is the escape hatch that
  makes gate false-negatives recoverable. Build it early; it is also your main manual
  testing tool.

**Verify:** run it against a YouTube video with mic live. Both channels transcribe;
questions in the video get cards; your commentary doesn't (unless question-shaped);
pause/resume drops in-flight cleanly; quitting writes terminal JSONL records.

## Stage 8 — Hardening (where the real product lives)

Everything to here is a weekend. This stage is what makes it *usable*, and every item
is a documented production failure:

1. **Multi-endpoint loopback + arbiter.** A loopback on an endpoint nothing plays
   through opens fine and captures silence — no error, `sys:on`, and a 40-minute
   interview recorded with the interviewer entirely missing. Open *every* loopback
   endpoint when unconfigured; elect the one carrying speech (RMS > threshold claims
   the channel if the incumbent has been quiet ≥1.5 s; instant handover between
   sessions, no flapping mid-utterance; all forward during initial silence so the first
   word isn't clipped). Apply the arbiter to loopback threads **only** — given the mic
   thread too, it mutes the mic (its index can never win). Ref-count shared source
   state when N threads feed one channel.
2. **The silence tripwire.** Track last-signal time per source; open-but-inaudible past
   45 s renders `SILENT Ns ⚠`, never `on`. "Off" and "deaf" are different states.
3. **Cross-channel echo + the conditional sys hold.** Same voice on both channels within
   2 s (token-set Dice ≥ 0.85) collapses, mic wins. Hold early-arriving sys copies only
   when the mic channel could answer them anyway (`full` policy) — the hold costs 2.5 s
   per interviewer question and buys nothing under `explicit`.
4. **Fragment merging** (`continuity.py`) with the wall-clock-window lesson: the hold
   window must exceed gap + continuation speech + STT latency, or merging silently never
   fires.
5. **Channel policy** — the two-sided lesson. Free-gating the user's own mic invents
   questions from narration (~30 in 40 min); blocking it swallows their real spoken
   questions. `explicit` (question-shaped speech only: Stage-A interrogative or
   trailing `?`) is the split, validated by replaying 690 logged utterances: 221 → 66
   answered, all technical asks retained, 662 never touching the LLM.
6. **Graceful degradation everywhere**: no loopback → warn, mic-only; CUDA dies → CPU +
   warning; Ollama down → heuristics-only + warning; Claude fails → error card. The app
   never crashes because a dependency did.
7. **Live JSONL logging** of every utterance with gate reason and latencies — written
   line-by-line at event time (crash-safe), because the log is the instrument that
   found *every* failure above. Log rejections with reasons, not just accepts; the
   misses are where the tuning signal is.

## Porting checklist for a new machine

In order, before first run:

1. Install Python 3.11 from python.org (**not** the Store). `py -3.11 --version`.
2. Clone the project; run `setup.ps1` (creates `.venv`, installs requirements, warms
   Ollama, prints devices).
3. Install Ollama; `ollama pull gemma4:e2b`; confirm
   `ollama run gemma4:e2b "reply ok"` answers (first call may take ~a minute cold).
4. Install + sign in the Claude CLI; confirm `claude -p "say ok"` (expect 6–9 s).
5. NVIDIA driver present; `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` come from
   requirements. First real utterance proves CUDA (that's where lazy DLL resolution
   fires) — watch the status bar for `whisper:cuda`, not just startup logs.
6. `python scripts/list_devices.py` — identify your mic; set `audio.mic_device` to a
   unique substring. Leave `audio.output_device` **blank** (auto multi-endpoint).
7. Windows Settings → Privacy → Microphone: global + desktop-apps toggles on.
8. `python -m pytest -q` — the suite is hardware-independent and must be green.
9. Dry run before anything that matters: `python -m ambientqa`, play audio through
   your normal call device, ask one question aloud. Confirm: `sys` transcribes, your
   question gets a card, status bar shows no `SILENT` warning, `whisper:cuda`.
10. If the machine differs materially (no NVIDIA GPU, different Ollama models), re-run
    the benchmarks in `ARCHITECTURE.md` §15 before trusting the defaults — especially
    gate latency (#5) and model behaviours (#3, #4), which are model- and
    hardware-specific.

## Suggested rebuild exercises

If the goal is deep understanding, build these in isolation before assembling anything —
each is one file and an afternoon, and together they cover every hard idea in the system:

1. **The thread→asyncio bridge.** A thread producing 40 items/s, an asyncio consumer,
   bounded drop-oldest handoff, clean shutdown. No audio — integers. You'll internalise
   `call_soon_threadsafe`, the single-scheduled-drain flag, and why producers never wait.
2. **A level-meter TUI.** Enumerate WASAPI devices, open everything, live RMS bars.
   This is Stage 1 *and* the device-picker feature, and it teaches the float64 lesson.
3. **The heuristic gate, test-first.** Write the test cases from Stage 4 *before* the
   rules; watch the rule order fall out of making them pass.
4. **An Ollama JSON classifier.** Any small local model, schema-forced output,
   `think:false`, warmup, timeout, defensive parsing. Measure your own latency table.
5. **A streaming subprocess consumer.** Spawn `claude -p --output-format stream-json`,
   parse deltas live, handle timeout-kill and malformed lines. Bind output to request
   ids and race three at once.
6. **A replay renderer.** Take this project's real `logs/*.jsonl` and render the session
   as HTML (`scripts/render_session.py` is the reference). Cheapest way to develop
   intuition for what the gate gets right and wrong — you'll be reading the exact data
   every design decision here was mined from.
