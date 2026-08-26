# Rebuilding Ambient From Scratch

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
- [Stage 1 — Audio backends + raw capture](#stage-1--audio-backends--raw-capture)
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
| Windows 11, Linux with PipeWire, **or macOS** | the three first-class platforms | sys-audio capture is WASAPI loopback on Windows, PipeWire *monitor sources* on Linux, and a virtual CoreAudio input (tested with BlackHole 2ch) on macOS. |
| Python 3.11 | runtime | Windows: `py -3.11`, **never the Microsoft Store Python**. Linux: `run.sh` owns the bootstrap. macOS: install `python@3.11`; `setup-macos.sh` rejects a different interpreter version. |
| A dedicated venv per platform | isolation | `.venv` on Windows, `.venv-linux` on Linux, `.venv-macos` on macOS. Both shell launchers gate pip on a success stamp and rerun it when requirements change. |
| NVIDIA GPU + CUDA packages (Windows/Linux) | Whisper at conversational speed | Windows/Linux use CUDA. CTranslate2 has native Intel/Apple-Silicon wheels but no Metal backend; macOS intentionally uses CPU int8 and excludes NVIDIA-only packages. |
| Ollama + `gemma4:e2b` | the question gate | The gate prompt was *engineered against this model*; qwen2.5:3b (the earlier stand-in) measurably flips on real questions once the transcript context block is present. ~0.6 s warm, ~67 s cold first load. Re-benchmark before trusting any substitute (see Stage 5). |
| Claude CLI, signed in | the answerer | `claude -p "test"` from a terminal must work before anything in Stage 6 will. |

Python packages (`requirements.txt`): `soxr numpy silero-vad onnxruntime faster-whisper
textual` (+ `tomli` on <3.11), `pyaudiowpatch` gated to Windows, and `sounddevice`
gated to macOS.
`pyaudiowpatch` specifically — it is the PyAudio fork that exposes WASAPI loopback
devices; stock PyAudio cannot see them at all. On Linux it never installs: capture is
parec subprocesses, with no PortAudio anywhere in the stack.

**Verify stage 0:** `ollama run gemma4:e2b "hi"` and `claude -p "say ok"` succeed, plus
per platform: Windows — `py -3.11 --version` and
`python -c "import pyaudiowpatch, soxr, faster_whisper, textual"` inside the venv;
Linux — `python -c "import soxr, faster_whisper, textual"` inside `.venv-linux`, and
`pactl --format=json list sources` prints JSON that includes at least one source whose
`device.class` is `"monitor"`; macOS — the equivalent import succeeds inside
`.venv-macos`, `sounddevice.query_hostapis()` includes Core Audio, and device enumeration
shows the microphone plus BlackHole when system-audio capture is configured.

## Stage 1 — Audio backends + raw capture

**Build in three layers: the contract first, then one backend per platform.** Nothing
above the backends is allowed to know where samples come from, and that boundary is what
makes one codebase serve all three platforms.

1. **The contract (`backends/base.py`).** Three protocols and a dataclass:
   `CaptureDevice` (a backend-stable opaque `id` — stringified PortAudio index on Windows/macOS,
   PipeWire source name on Linux — plus name, kind `mic`/`loopback`, native
   channels/rate), `SourceStream` (blocking `read(frames)` returning interleaved
   float32; `stop()` unblocks a reader from any other thread; `close()` releases),
   `BackendSession` (`mic_candidates`/`loopback_candidates` returning devices best
   first, `open`, `close`) and `AudioBackend` (`list_devices`, `open_session`). The one
   ordering rule is written into the contract itself: **stop before close, always** —
   closing a stream under a live blocked reader is the native use-after-free class of
   crash. `get_backend()` selects by `sys.platform`; the config key
   `[audio] backend = "auto" | "wasapi" | "pipewire" | "coreaudio"` exists for odd setups, not
   day-to-day use.
2. **WASAPI (`backends/windows.py`), via pyaudiowpatch.** Enumerate input devices plus
   everything from `get_loopback_device_info_generator()`; open each stream at the
   device's *native* rate and channel count; `stop_stream()` is the cross-thread
   unblock. Clamp advertised channel counts to 1–2 — virtual plug devices advertise
   absurd counts (32–128), and trusting them drowns the mic in a downmix and hits
   PortAudio's crash-prone mmap path.
3. **PipeWire (`backends/linux.py`), via pactl + parec.** Enumerate with
   `pactl --format=json list sources`; a source whose `properties["device.class"]` is
   `"monitor"` is a monitor source — "Monitor of <sink>", one per output device,
   PipeWire's native equivalent of WASAPI loopback, which is why the sys channel works
   on Linux with no driver tricks at all. Capture is one subprocess per stream:
   `parec --device=<name> --format=float32le --rate=16000 --channels=1 --raw
   --latency-msec=<frame_ms>`. parec resamples and downmixes in-process, so Linux
   streams arrive already in pipeline format and the resample/downmix path below is
   naturally skipped. Two facts you will not guess:
   - `--latency-msec` is **required**. Server-default buffering measured ~2 s to the
     first byte and ~1 s bursts thereafter — useless for live segmentation. Requesting
     the pipeline's own frame cadence delivers the first frame in ~50 ms, one frame per
     read.
   - parec's stderr goes to an anonymous **temp file, never a pipe**. Nothing drains
     stderr while audio streams, so a chatty parec (`PULSE_LOG` set, repeated client
     warnings) fills the 64 KiB pipe, blocks writing, and silently stops producing
     audio — a deaf channel with no error. A file cannot backpressure the child, and
     its tail stays readable for the EOF diagnostic.

   `stop()` simply terminates the child: stdout hits EOF, any blocked reader returns
   instantly, and a crashing capture process can never take the app down — the reader
   surfaces the EOF as an error the orchestrator already routes around. And because
   PipeWire multiplexes every source, so other apps keep the mic. Still refuse a second
   copy of this app before model load: it would duplicate capture, Whisper, and paid answer
   calls. Keep an explicit override only for diagnostics.

4. **CoreAudio (`backends/macos.py`), via sounddevice.** Enumerate Core Audio inputs,
   classify known virtual drivers as loopback, and open native-rate float32 blocking
   streams. Prefer the default physical mic. A pinned output may select any input so an
   unfamiliar virtual driver still works. `abort()` must unblock a reader before close.
   If no virtual loopback exists, report how to install BlackHole and continue mic-only.

Then the platform-neutral capture loop on top (`audio.py`): open a stream per candidate
device, read 25 ms buffers, downmix to mono by averaging, resample to 16 kHz with
`soxr.ResampleStream` when a stream arrives off-rate (streaming API, not one-shot — you
are resampling a continuous stream, and chunk-independent resampling clicks at
boundaries), and print an RMS level meter per frame.

Key concepts you must come out of this stage understanding:

- **Windows enumerates every physical device once per host API** (MME, DirectSound,
  WASAPI, WDM-KS). The truncated duplicate names in a device list are not a bug. Only
  the WASAPI entries matter here, and loopback endpoints exist only under WASAPI.
- **A loopback endpoint / monitor source is a capture device that records what an
  *output* renders.** Which output your meeting app renders to is invisible to you and
  changes between sessions — this fact eventually forced the multi-endpoint design
  (Stage 8), and it is shared by all three backends.
- **Capture must live on OS threads.** `stream.read()` blocks; asyncio comes later, and
  the bridge between the two worlds (a lock-guarded deque drained via
  `call_soon_threadsafe`) is worth building carefully once and never touching again.

**Verify:** speak — mic meter moves. Play a video — exactly the loopback/monitor
endpoint of the device it plays through moves, and the others stay flat. *Notice how
easy it is to pick the wrong one; remember that feeling for Stage 8.* On Linux, start a
second copy while the first runs; both must keep capturing.

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

Linux has the same lazy-resolution lesson with different plumbing: the pip CUDA
libraries live in `site-packages/nvidia/*/lib`, and `run.sh` exports `LD_LIBRARY_PATH`
over them before exec'ing the app. Part 3 of the fix — materialise in the try block,
fall back at runtime — is identical everywhere.

Hygiene that makes ambient (as opposed to push-to-talk) STT usable:
`condition_on_previous_text=False` (kills hallucination feedback loops), a blocklist for
silence hallucinations, and **keep Whisper's punctuation untouched** — the trailing `?`
encodes rising intonation and becomes your single strongest question signal downstream.
The blocklist matches the *normalised whole transcript exactly, never by prefix*.
Prefix matching seems safer and is the opposite: interviewers routinely open with a
courtesy — "Thank you. So, tell me about your experience with Kubernetes." — and a
`"Thank you"` prefix rule ate the entire question here, before gating, with no log
record at all. So every entry is the full utterance Whisper hallucinates on silence
("Thank you very much.", "Thanks for watching!", "Subtitles by the Amara.org
community").

**Verify:** speak → text within ~1 s, correctly punctuated. Then deliberately break
CUDA (rename the DLL dir) and confirm the CPU fallback engages with a warning instead
of a crash. Check silence for a minute — no hallucinated text should pass.

## Stage 4 — Heuristic gate (Stage A)

**Build:** pure functions, no I/O, exhaustively unit-tested. This is where test-driven
development actually pays: every rule exists because of a real utterance class, and the
tests are the specification. Rules in order (first match wins): too-few-words →
filler-only → near-duplicate of a *recently* answered question (token-set Dice ≥ 0.85 —
but only questions answered within `gate.reask_cooldown_s` = 8 s count; past that, an
almost-identical question is a human deliberately re-asking because the first answer
missed, and it deserves a fresh answer, not a dedupe) → tag/rhetorical endings
(`right?`, `you know?` — where a tag-pattern match counts *only* when the tag is
comma-appended to a finished statement or the remainder is pure function words: "Am I
right?" rejects, "Is my understanding of the GIL right?" answers) → **fast-accept**
explicit interrogatives (ends `?` + starts with an interrogative after skipping the
lead-in prefixes: so/well/okay/then/and/but *plus* the acknowledgment openers
interviewers habitually attach — great/alright/right/sure/now/yes/yeah/cool/perfect/
good/nice/fine — and guarded by the vocative check so names that casefold into
interrogatives, Will/May, cannot slip through) → **imperative-request accept** ("Tell
me about yourself.", "Talk about evaluation metrics." — a sentence-initial
`REQUEST_VERBS` verb after prefixes/"please", plus a request-bigram fallback
(`talk about`/`tell me`/`walk me`) for transcriptions Whisper left punctuation-less;
guarded four ways: idioms ("tell me about it"), narrated plans ("we'll talk about that
later"), name-addressed commands ("Sarah, tell me…"), and stubs ending on a trailing
fragment word ("So, tell me about" — the merge layer holds those for the rest)) →
vocative ("Hey Sarah, can you…") → trailing-fragment (ends in a word that cannot end a
sentence) → no-content-words (< 2 non-filler non-stopword tokens).

Ordering/curation lessons encoded in the current rule set:

- Dedupe must precede the fast-accept: the fast-accept is deliberately exempt from
  answer-echo suppression but *not* from verbatim re-ask dedupe.
- Tags must precede the fast-accept: "Am I right?" is interrogative-shaped and would
  sail straight through it.
- Fast-accept must precede both the vocative reject and the content-word check. The
  vocative pattern keys on the first token, and Whisper capitalizes every sentence
  start, so "Okay, can you explain the CAP theorem?" would otherwise read as a name in
  vocative position — and "How are you?" is all stopwords.
- The imperative accept must precede the vocative reject too (same first-token
  ambiguity), and exists because on an "explicit"-policy channel command-form asks
  carry no `?` and no interrogative — without this rule they were structurally
  unanswerable.
- The trailing-fragment word list must **exclude** `this/that/these/those/like/when` —
  they *can* end sentences, and listing them rejected real questions ("…how would you
  fix this.") while adding merge-hold latency to them. When ambiguous, pass it on: the
  semantic gate catches a wrongly-kept fragment; a wrongly-rejected question is lost
  silently.
- `no_content_words` is the most important false-positive guard in the system: without
  it, "uh, um, so, the thing is" reaches the LLM, which will *invent* a question from
  surrounding context (it is instructed to rewrite — it will find something to rewrite).

Two rules live one level up, in the channel-aware `QuestionGate.evaluate`, because they
depend on *whose* channel the utterance arrived on:

- On a `full`-policy channel a vocative is **demoted to the semantic gate, not
  hard-rejected**. "Aakash, can you explain decorators?" is the interviewer addressing
  the candidate by name — the exact question this tool exists to answer — and the
  semantic gate's prompt already returns FALSE for questions aimed at another human. On
  the mic the hard reject stands: you hailing someone by name is definitionally talk to
  another human.
- On an `explicit`-policy channel, a non-question *statement* ≥ 0.5 token-set-similar to
  a question answered ≤ 90 s ago is accepted outright as `reask_of_recent`. Retries are
  phrased as corrections ("no, prompt *engineering*…"), which the semantic gate's
  prompt is trained to call narration and reject — the history-aware answerer (Stage 6)
  is the only judge that can resolve what changed.

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

The startup warmup that covers the ~67 s cold load must be **cancellable**: run the
long request on a daemon thread that signals back into the loop, not via `to_thread`.
`Task.cancel` cannot interrupt a running executor future, and `asyncio.run` joins the
default executor on exit — so quitting during a cold Ollama load would otherwise keep a
dead-looking process alive for the full load. A daemon thread cannot block interpreter
exit, so quit never hangs.

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
  fire on 0.5% of recorded questions. Record the `searched` flag on *every* exit path —
  timeout and error included: it exists to explain outlier latency in the log, and a
  timed-out lookup is precisely the record that needs it.

The prompt is layered, and every layer exists because its absence produced a bad
answer:

- **Q&A history** — up to `answer.history_turns` = 8 completed question/answer pairs,
  oldest first, each answer clipped to ~700 chars. This, not the raw transcript, is
  what resolves "elaborate on the second method": the methods exist only in the answer
  prose. Instruct that it is used *only* when the current question refers back (an
  ordinal, a pronoun, an explicit callback) — a self-contained question gets a fresh
  answer. Read the history at answer time, not enqueue time, so a follow-up sees an
  answer that completed after it was queued.
- **Setup-awareness** — the final transcript line(s) before a question are often its
  premise: speakers state a scenario, trail off, think, then ask. "Which method would
  you use?" after "So if the content you're searching keeps changing…" must be answered
  as constrained by the setup, not in the abstract.
- **A channel stance** — sys questions coach the *user's own* first-person answer (that
  is what the cue card is for); mic questions must NEVER be answered in first person as
  the addressee or invent its state. The transcript carries only the audible half of
  the user's world: when they talk to another assistant whose replies are silent text,
  a stanceless model infers it IS that assistant and fabricates its state ("No, I don't
  auto-launch") with total authority.
- **The standing profile is SUBORDINATE to the recent transcript thread** — when the
  lines before a question establish what is being discussed, the profile's domain must
  never be substituted as the topic.

**Second passes — build both; they catch disjoint failure classes:**

- `[answer] verify = "always" | "off"` — after each delivered answer, an auditor with a
  *wider* transcript window (`verify_context_turns` = 18), the full Q&A history, and
  the RAW pre-rewrite transcription re-reads it and replaces the card (status
  `revised`) *only when materially wrong*: a missed constraint, a misheard question, a
  factual error, impersonation of another party, a dropped enumeration item. Style is
  never grounds — the correction lands ~8 s after the user may already be speaking from
  the first card, so it is only worth the distraction when the first answer would have
  misled. A revision must also replace the Q&A-history entry, or "elaborate on that"
  expands the answer the audit just retracted. Run audits one at a time, strictly after
  their answer is on screen. Use one audit at a time and the same aggregate CLI semaphore
  as primary answers.
- `[answer] sweep = "always" | "off"` — the audit only reviews answers that *exist*; a
  wrongly rejected question produces nothing to audit. Every `sweep_interval_s` = 25 s
  a sweeper (`sweep_model`, default `claude-haiku-4-5` — it is a small classification,
  so cheap and fast is right) re-judges the gate's *judgment-stage* rejections only
  (`not_a_direct_question` / `ollama_reject` / `ollama_unavailable` /
  `human_vocative`; mechanical rejections — filler, dedupe, echo, tags — are not misses)
  against wide context and
  the answered/in-flight list. Genuine asks come back as late cards with gate reason
  `second_pass_recovery` through the normal answer path, streaming and audit included.

**Verify:** fire 4 questions concurrently; wall clock should approximate the slowest
single answer (measured 6.0 s vs 22.2 s serial). Ask a "what is X called now" question;
confirm the lookup fires, and that an ordinary question doesn't pay for it. Ask a
question, then follow up with "elaborate on the second point" — it must resolve against
the answer, not the transcript. Ask something the gate will reject as narration and
wait a sweep interval; it should come back as a recovered card only if it was a real
ask.

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

Three runtime facts must be right *before* Textual owns the process, or the app dies in
ways that look nothing like their cause:

- **On POSIX, start multiprocessing's resource tracker before Textual starts**
  (`resource_tracker.ensure_running()` in `main()`). Whisper's lazy model load creates
  a multiprocessing lock at *first transcription*; spawning the tracker then passes
  `sys.stderr.fileno()` along — but Textual has replaced stderr with a capture whose
  `fileno()` is −1, so the spawn dies with "bad value(s) in fds_to_keep" and **every**
  transcription fails, CUDA and CPU fallback alike. Starting the tracker while stderr
  is still the real terminal makes later lock creation a mere pipe write. Set
  `HF_HUB_DISABLE_PROGRESS_BARS=1` in the same breath — a download bar can only render
  as garbage into Textual's capture, and skipping it skips most of the tqdm machinery
  that wanted the lock at all.
- **Seed Textual's `active_app` ContextVar in the controller's `run()`** before
  creating any task. Controller tasks are not descendants of Textual's message pump, so
  a widget method they await (the answer-delta flush throttle) creates a timer that
  cannot resolve `active_app`, dies instantly with `LookupError`, and shutdown
  re-raises it when awaiting dead timers — quit crashes.
- **Root logging goes to a file (`logs/ambientqa.log`), never stderr.** Once Textual
  owns the terminal, anything logged to stderr is painted raw over the UI, and one
  component's traceback makes the whole app look dead.

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
- Give each picker action (devices, profiles, sessions) its **own** exclusive worker
  group. In the shared default exclusive group they silently cancel each other — and
  cancelling the device picker mid-flight while it is stopping/restarting capture
  strands the capture restart.

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
4. **Fragment merging** (`continuity.py`) — three lessons, all paid for:
   - Terminal punctuation is trust-tiered. A `?` or `!` closes a thought *before* the
     trailing-fragment-word test runs: English questions legitimately strand a
     preposition ("What are you working on?"), and treating those as open parked a
     complete question for the whole hold — or glued it onto the interviewer's next
     sentence, destroying the `?` fast-accept downstream. `.` earns no such trust:
     Whisper invents a period at every VAD boundary, so "so tell me about." stays open.
   - At merge joins, strip Whisper's boundary periods AND ellipses from both sides —
     the pending fragment was already judged open, so that punctuation is not semantic.
   - Treat a complete command-form request of at most six words as closed even if Whisper
     omitted punctuation. Otherwise `EXPLAIN RAG` waits the entire hold before gating;
     keep incomplete forms and long accumulated setups open.
   - The wall-clock-window lesson: the hold window (`merge_window_s` = 13.0) must
     exceed the gap (`merge_gap_s` = 6.5 — a ~5 s think-pause between a trailed-off
     setup and its question must merge) *plus* the continuation's spoken length *plus*
     its STT latency, or merging silently never fires.
5. **Channel policy** — the two-sided lesson. Free-gating the user's own mic invents
   questions from narration (~30 in 40 min); blocking it swallows their real spoken
   questions. `explicit` (question-shaped speech only: Stage-A interrogative or
   trailing `?`) is the split, validated by replaying 690 logged utterances: 221 → 66
   answered, all technical asks retained, 662 never touching the LLM.
6. **Capture lifecycle under restarts.** `start()`/`stop()` run on executor threads and
   their callers cannot guarantee ordering — cancelling the device-picker worker
   abandons a `stop()` that keeps running while the recovery path calls `start()`.
   Serialise both under one lifecycle lock; give every capture thread a generation
   token so a zombie that outlived `stop()`'s join timeout can neither push its stale
   device's frames into the live session nor corrupt the new session's runner counts;
   and keep the stop order fixed: stop streams → join threads → close streams → close
   session, because closing under a live blocked reader is the native-crash class the
   backend contract exists to prevent.
7. **Graceful degradation everywhere**: no loopback → warn, mic-only; CUDA dies → CPU +
   warning; Ollama down → heuristics-only + warning; Claude fails → error card. The app
   never crashes because a dependency did — and on Linux a dependency literally *is* a
   separate process (parec), so a capture crash surfaces as EOF, not as yours.
8. **Live JSONL logging** of every utterance with gate reason and latencies — written
   line-by-line at event time (crash-safe), because the log is the instrument that
   found *every* failure above. Log rejections with reasons, not just accepts; the
   misses are where the tuning signal is. The vocabulary has grown with the pipeline:
   `imperative_request`, `reask_of_recent` and `second_pass_recovery` as accept
   reasons, `verify_revision` (with `answer_status: "revised"`) when the audit replaces
   an answer.

## Porting checklist for a new machine

Common, in order, before first run:

1. Install Ollama; `ollama pull gemma4:e2b`; confirm
   `ollama run gemma4:e2b "reply ok"` answers (first call may take ~a minute cold).
2. Install + sign in the Claude CLI; confirm `claude -p "say ok"` (expect 6–9 s).
3. NVIDIA driver present; `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` come from
   requirements. First real utterance proves CUDA (that's where lazy library resolution
   fires) — watch the status bar for `whisper:cuda`, not just startup logs.
4. `python -m pytest -q` — the suite is hardware-independent and must be green. One
   Windows-only CUDA-DLL registration test skips elsewhere; that is the only allowed
   skip.
5. `python scripts/list_devices.py` — prints which backend was selected and every
   capture device it can see, mics first, then loopback/monitor endpoints.
6. Dry run before anything that matters: launch the app, play audio through your
   normal call device, ask one question aloud. Confirm: `sys` transcribes, your
   question gets a card, status bar shows no `SILENT` warning, `whisper:cuda`.
7. If the machine differs materially (no NVIDIA GPU, different Ollama models), re-run
   the benchmarks in `ARCHITECTURE.md` §15 before trusting the defaults — especially
   gate latency (#5) and model behaviours (#3, #4), which are model- and
   hardware-specific.

Windows-specific:

- Install Python 3.11 from python.org (**not** the Store). `py -3.11 --version`.
- Clone the project; run `setup.ps1` (creates `.venv`, installs requirements, warms
  Ollama, prints devices).
- Windows Settings → Privacy → Microphone: global + desktop-apps toggles on.
- Set `audio.mic_device` to a unique substring of your mic; leave
  `audio.output_device` **blank** (auto multi-endpoint).

Linux-specific:

- Install `pipewire-pulse` (that is where `pactl` and `parec` come from).
- Launch via `./run.sh` — it is the whole bootstrap: creates `.venv-linux` gated on the
  `.deps-installed` stamp (and re-runs pip when `requirements.txt` is newer), starts
  Ollama if it is not already up, loads PipeWire's `module-echo-cancel` exposing the
  `ec_mic` source, exports `LD_LIBRARY_PATH` over the pip CUDA libs, and execs
  `python -m ambientqa`. Run one copy; require an explicit unsafe override for diagnostics.
- Why `ec_mic` exists: WebRTC noise suppression + automatic gain control over the raw
  mic — the class of processing Windows applies in its own audio stack. The raw USB mic at
  100% PipeWire volume sits ~34 dB above its hardware-neutral level: loud speech
  clipped 1.7% of samples and garbled Whisper. `config.toml` pins `mic_device` to the
  module's device description (`Echo-cancelled_Microphone`); if the module ever fails
  to load, press `d` in the app and pick the raw mic, or rerun `run.sh`.
- For a Linux app-menu entry, pass `--choose` to `run.sh`. Its standalone Textual splash
  selects Assist or Voice before audio/model construction; Cancel launches nothing. Keep
  explicit `run.sh` and `run.sh --voice` paths so automation and terminal recovery never
  depend on the chooser, and keep the pinned emergency script independently callable even
  if the picker is broken.

macOS-specific:

- Run `./setup-macos.sh`, then `./run-macos.sh`; keep `.venv-macos` separate.
- Grant the launching terminal Microphone permission.
- Install the BlackHole 2ch cask, restart the Mac, create a Multi-Output Device containing BlackHole and the
  physical output, then select BlackHole as Ambient's `sys` input with `d`.
- Keep platform pins in `config.macos.toml`, an `extends = "config.toml"` overlay, so
  shared tuning changes remain inherited without overwriting Windows/Linux devices.
- Expect Whisper to report CPU int8; macOS CTranslate2 does not provide CUDA or Metal.

## Suggested rebuild exercises

If the goal is deep understanding, build these in isolation before assembling anything —
each is one file and an afternoon, and together they cover every hard idea in the system:

1. **The thread→asyncio bridge.** A thread producing 40 items/s, an asyncio consumer,
   bounded drop-oldest handoff, clean shutdown. No audio — integers. You'll internalise
   `call_soon_threadsafe`, the single-scheduled-drain flag, and why producers never wait.
2. **A level-meter TUI.** Enumerate your platform's capture devices (WASAPI via
   pyaudiowpatch, or `pactl --format=json list sources`), open everything, live RMS
   bars. This is Stage 1 *and* the device-picker feature, and it teaches the float64
   lesson.
3. **The heuristic gate, test-first.** Write the test cases from Stage 4 *before* the
   rules; watch the rule order fall out of making them pass.
4. **An Ollama JSON classifier.** Any small local model, schema-forced output,
   `think:false`, warmup, timeout, defensive parsing. Measure your own latency table.
5. **A streaming subprocess consumer.** Spawn `claude -p --output-format stream-json`,
   parse deltas live, handle timeout-kill and malformed lines. Bind output to request
   ids and race three at once.
6. **A subprocess capture stream.** Spawn
   `parec --raw --format=float32le --rate=16000 --channels=1 --latency-msec=25`, read
   exact-size frames from its stdout on a thread, route stderr to a temp file, and
   shut down by terminating the child. This is the entire Linux backend in miniature,
   and it teaches why EOF is the cleanest cross-thread unblock there is — and what the
   server-default latency does to a live pipeline if you omit that one flag.
7. **A replay renderer.** Take this project's real `logs/*.jsonl` and render the session
   as HTML (`scripts/render_session.py` is the reference). Cheapest way to develop
   intuition for what the gate gets right and wrong — you'll be reading the exact data
   every design decision here was mined from.
