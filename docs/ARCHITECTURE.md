# Ambient Q&A — Architecture Deep Dive

This document explains how the system works internally, module by module, and — more
importantly — *why* it is shaped the way it is. Nearly every non-obvious decision here was
forced by something measured on real hardware or paid for with a real failed session. Those
measurements are called out inline as **[measured]**; treat them as the load-bearing walls.
If you rebuild this from scratch (see `REBUILD-GUIDE.md`), the code is the easy part — the
measurements are what you'd otherwise have to rediscover the hard way.

- [1. What this system actually is](#1-what-this-system-actually-is)
- [2. The pipeline at a glance](#2-the-pipeline-at-a-glance)
- [3. Concurrency model](#3-concurrency-model)
- [4. bus.py — queues and events](#4-buspy)
- [5. audio.py — capture](#5-audiopy)
- [6. segmenter.py — utterance boundaries](#6-segmenterpy)
- [7. stt.py — transcription](#7-sttpy)
- [8. continuity.py — fragment merging](#8-continuitypy)
- [9. context.py — shared transcript memory](#9-contextpy)
- [10. gate.py — question detection](#10-gatepy)
- [11. answer.py — answering](#11-answerpy)
- [12. ui.py — the live pane](#12-uipy)
- [13. __main__.py — the controller](#13-mainpy)
- [14. config, profiles, logging](#14-config-profiles-logging)
- [15. The empirical foundation](#15-the-empirical-foundation)

---

## 1. What this system actually is

An always-on listener for **live conversations the user is a participant in** — originally
and primarily technical job interviews. It hears both sides (user's mic + the other
speaker via system-audio loopback), transcribes continuously, decides which utterances are
questions actually worth answering, and displays answers in a read-only terminal pane.

Three properties dominate every design decision:

1. **It must never block or interrupt.** It is a side channel. No prompts, no focus
   stealing, no modal anything. Every stage degrades gracefully; audio capture can never
   stall on a slow consumer.
2. **Latency is correctness.** The user is mid-conversation. An answer that arrives 10
   seconds after the question is worthless even if perfect. This is why the gate is local
   (Ollama, ~0.5–1 s) and the expensive model (Claude) is reserved for confirmed
   questions only.
3. **One-glance readability.** The user reads answers in peripheral vision while already
   speaking. This produced the `cue` answer style (a sayable opening line + keyword
   bullets) after prose answers measured 60–90 words proved unreadable in the moment.

The asymmetry between channels is fundamental, not incidental: the user already knows
what *they* are saying, so their channel must not be mined for questions — but the
questions they ask aloud must still be answered. See §10 (channel policy).

## 2. The pipeline at a glance

```
 mic ──────┐
           ├─► capture ─► segmenter ─► STT ─► [consumer loop] ─► gate ─► answerer ─► UI
 loopback ─┘   (threads)  (Silero VAD) (Whisper)   │              (Ollama)  (claude -p │ pane
  (× N endpoints,          CPU/ONNX     CUDA        │  ordered:    detached   pool)    │ +
   arbiter picks one)                               │  context,    tasks               │ JSONL
                                                    │  echo, merge                     │ log
```

Five processing stages joined by bounded `asyncio` queues. Every queue is
**drop-oldest**: a producer never waits; when a queue is full the oldest item is
discarded. This is non-negotiable — audio capture runs at wall-clock speed and cannot be
back-pressured. Losing the oldest un-transcribed utterance under overload is strictly
better than stalling capture and losing *everything* after it.

Data types flowing through (all in `bus.py`):

| Type | Produced by | Contents |
|---|---|---|
| `AudioFrame` | capture threads | channel, 25 ms of float32 16 kHz mono, timestamp |
| `Utterance` | segmenter | channel, full utterance audio, start/end times, uuid |
| `Transcript` | STT | channel, text, timestamp, utterance id, STT latency |
| `GateResult` | gate | transcript, accepted?, reason, rewritten query, latency |
| `AnswerResult` | answerer | question id, answer text, status, latency, searched? |

Latency budget for a question from the *other* speaker, end of speech → answer text
starts appearing (all **[measured]**):

| Stage | Cost |
|---|---|
| trailing-silence wait (segmenter) | 900 ms (configured) |
| Whisper large-v3-turbo on CUDA | 300–700 ms typical |
| gate stage A (heuristics) | ~0 ms |
| gate stage B (gemma4:e2b, warm) | ~530–950 ms |
| `claude -p` one-shot | 3.5–9 s (6–9 s floor is CLI+network overhead) |
| with `web_lookup` triggered | 15–17 s |

## 3. Concurrency model

Three worlds coexist; understanding their boundaries explains most of the code's shape.

**1. OS threads (capture only).** PyAudio's blocking `stream.read()` lives in dedicated
daemon threads — one per open device stream (one mic + potentially six loopback
endpoints). Threads never touch asyncio objects directly; they hand frames across via
`DropOldestQueue.put_from_thread()`, which appends to a thread-side `deque` under a lock
and schedules a single drain callback on the event loop with `call_soon_threadsafe`. The
deque itself is bounded, so even if the loop stalls, memory cannot grow without bound and
the *callback count* cannot grow without bound either (one scheduled drain at a time).

**2. The ordered asyncio path.** One consumer task (`_consume_transcripts`) owns
everything that must observe transcripts in arrival order: cross-channel echo
suppression, the continuity merger, appending to the shared `TranscriptContext`, and —
critically — **snapshotting the context** that each question will be judged and answered
against. Ordering ends there.

**3. Detached bounded tasks.** Gating and answering are network calls (~900 ms and 3.5 s+
respectively) and run as fire-and-forget `asyncio.Task`s bounded by semaphores
(`gate.max_concurrent`, `answer.max_concurrent`). **[measured]** When gating ran inline
on the ordered path, a second question arriving mid-gate could not even start until the
first finished — answers serialised invisibly. Moving gating off-path and raising the
answer pool from 2 to 4 took four simultaneous questions from 22.2 s (serial) to 6.0 s
wall clock, peak concurrency 4/4.

The subtle invariant making detachment safe: context snapshots are taken *on the ordered
path before detaching*. A gate task that runs late still judges its utterance against
the conversation as it stood when the words were spoken, not whenever the task got
scheduled. Answers can complete out of order; each answer card is bound to its
question's utterance id, never to "the latest card".

There is also a `.stop` asyncio.Event for shutdown; every worker loop polls its queue
with a short timeout so it can notice `stop` within 250 ms. On shutdown the controller
cancels all tasks, then writes terminal JSONL records for anything in flight
(`answer_status: "cancelled"`), so the log never ends with a dangling accepted question.

## 4. bus.py

`DropOldestQueue(asyncio.Queue)` adds:

- `put_drop_oldest(item)` — evict oldest on overflow, never block, return the evicted
  item (callers log it as `transcript_queue_overflow` / a `dropped` answer, so loss is
  visible rather than silent).
- `put_from_thread(loop, item)` / `_drain_thread_pending()` — the thread→loop bridge
  described above. The scheduling flag ensures at most one pending `call_soon_threadsafe`
  regardless of how fast frames arrive.
- `drain()` — synchronously empty the queue (used by pause and device-switching).

Design note: frames are ~25 ms each, so the frame queue (256 deep) holds ~6 s of audio.
That is the buffer absorbing a Whisper hiccup or a UI stall.

## 5. audio.py

WASAPI capture via **`pyaudiowpatch`** (a PyAudio fork exposing loopback endpoints —
Windows renders audio per *output* device, and each output has a matching loopback
capture endpoint named like `Speakers (...) [Loopback]`).

**Mic:** find candidates matching the configured substring (preferring the WASAPI host
API duplicate — every physical device appears once per host API: MME, DirectSound,
WASAPI, and only WASAPI is trustworthy here). Open the first that works.

**Loopback — the part that cost a whole interview [measured]:** a loopback stream on an
endpoint that nothing is playing through opens *without error* and delivers silence
forever. There is no API-level way to distinguish "healthy but idle" from "wrong
endpoint". A session was recorded with `output_device` pinned to the desktop speakers
while the call played through the headset: status bar said `sys:on` for 40 minutes and
the interviewer was never heard. Nothing errored. Two defences resulted:

1. **Open every loopback endpoint** when `output_device` is blank (the default). Which
   endpoint a call renders to is unknowable in advance and changes between sessions
   (headset today, speakers tomorrow — the user confirmed "all the options"). Idle
   endpoints cost almost nothing to hold open.
2. **`LoopbackArbiter`** — all endpoints feed the single `sys` channel, but one segmenter
   cannot be fed two interleaved conversations, so exactly one endpoint's frames are
   forwarded at a time. Election rule: any endpoint with RMS above `SIGNAL_RMS` (0.004,
   deliberately below the energy-VAD threshold of 0.012) claims the channel if the
   incumbent has been quiet for `hold_s` (1.5 s). The hold prevents flapping while the
   incumbent is mid-utterance; the instant-takeover-when-quiet means switching devices
   between sessions costs zero words. Before anything has ever spoken, *all* endpoints
   forward (silence is harmless, and this avoids clipping the first word).
   **Trap [measured]:** the arbiter must apply to loopback threads *only* — passing it to
   the mic thread muted the mic entirely, because the mic's device index can never win a
   contest it was never entered in.

**Silence tripwire:** each `SourceState` tracks `last_signal_at` (RMS measured
pre-resample, in float64 — **[measured]** some host APIs deliver frames that are not
valid float32 and squaring them in float32 overflows to `inf`, poisoning every later
comparison). A source open but inaudible for `silent_source_warn_s` (45 s) is displayed
as `sys:SILENT 60s ⚠` instead of `on`. "Off" and "open but deaf" are different states
and the UI must not conflate them.

Since multiple threads share one `SourceState` when N endpoints feed `sys`, active/
inactive transitions are reference-counted (`_enter_source`/`_exit_source`) — otherwise
the first endpoint thread to die would mark the whole channel dead while five others
still ran.

Each capture thread: read native-rate frames → downmix multichannel by mean → measure
RMS → resample to 16 kHz mono with `soxr` (streaming resampler, one per thread, kept
consistent even for frames that end up dropped, so a later arbiter handover starts
clean) → accumulate into exact 25 ms frames → `put_from_thread`.

## 6. segmenter.py

**Silero VAD** via ONNX Runtime on **CPU** — it is tiny, and it must not contend with
Whisper for the GPU. One independent segmenter instance per channel (mic and sys speech
overlap constantly; they cannot share silence-tracking state).

Mechanics per channel:

- A **pre-roll ring buffer** of 300 ms is always maintained; when speech starts, the
  ring is prepended so onsets are not clipped (VADs detect speech a few frames late).
- Speech accumulates until **900 ms of trailing silence** (`silence_ms`), then the
  utterance is emitted. 900 ms is a tuned compromise: shorter splits thoughts at every
  breath; longer delays every answer by the same amount.
- Utterances longer than 20 s are force-flushed (a monologue still surfaces), and
  segments under 400 ms are discarded (coughs, clicks).
- If Silero fails to initialise, an RMS energy gate (`_EnergyVAD`, threshold 0.012)
  substitutes — much worse, but the app must run.

## 7. stt.py

**faster-whisper** (CTranslate2), model `large-v3-turbo`, CUDA fp16, single worker.
One worker because there is one GPU; utterances are transcribed serially off a bounded
queue and parallel STT would just thrash VRAM.

Windows CUDA-specific traps, both **[measured]** and both handled in
`register_cuda_dll_dirs()`:

- Pip-installed `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` drop DLLs in
  `site-packages/nvidia/*/bin`, which Python ≥3.8 does **not** search. Worse, CTranslate2
  resolves cuBLAS lazily at *first inference*, not at model construction — so the model
  "loads on cuda" happily and dies minutes later at the first real utterance. Fix needs
  **both** `os.add_dll_directory()` *and* prepending to `PATH` (the lazy resolution
  consults only PATH).
- Because `model.transcribe()` returns a lazy generator, CUDA errors surface on
  *consumption*. The generator is therefore materialised inside the `try`, and a runtime
  CUDA failure triggers a live fallback to CPU int8 (loudly, in the status bar) rather
  than a crash.

Hygiene, all of which exists because Whisper hallucinates on silence and ambient noise:

- `condition_on_previous_text=False` — prevents feedback loops where a hallucination
  seeds the next window.
- A normalised-prefix blocklist drops classic silence hallucinations ("Thank you.",
  "Thanks for watching!", subtitle credits).
- Whisper's punctuation is preserved **exactly** — the trailing `?` is the single
  strongest gate signal (it encodes rising intonation, which survives disfluency).
- Profiles (see §14) inject `hotwords` (vocabulary spelling bias) and a ≤120-char
  `initial_prompt` (domain context, kept short to avoid prompt-echo hallucinations).

## 8. continuity.py

People pause mid-sentence for longer than feels intuitive, and the VAD splits there.
`ContinuityMerger` coalesces probable fragments *before* gating so "how do you manage
context in …(pause)… Amazon Bedrock" is judged as one thought.

An utterance is **held** (not gated yet) if it looks *unfinished* — `is_open_utterance`:
ends in a function word that cannot end a sentence (preposition/article/conjunction — a
curated list; see the trap below), ends with a comma or dash, or lacks terminal
punctuation. A held fragment merges with the next same-channel utterance if the *audio
gap* is ≤ `merge_gap_s` (4.5 s) and either the held text is open or the new text starts
like a continuation (leading conjunction or lowercase letter). Joins strip Whisper's
invented boundary period.

Two safety rails: a **wall-clock deadline** `merge_window_s` (9 s — must exceed the gap
*plus* the continuation's spoken length *plus* its STT latency, or merging silently
never happens **[measured]**), and caps (`max_merge_parts` 5, `max_merge_s` 25) so noisy
speech cannot merge forever. Complete questions are never held — zero added latency in
the common case.

**Trap [measured]:** `this/that/these/those/like/when` were originally in the
"cannot-end-a-sentence" list. They *can* end sentences ("How would you fix this."), and
listing them both rejected real questions and added ~9 s of merge-hold latency to them.
When ambiguous, let the semantic gate decide — a wrongly-held fragment is caught there,
a wrongly-rejected question is lost silently.

## 9. context.py

`TranscriptContext` is the rolling shared memory (deque of 100 lines) that (a) renders
the last N lines as background for the gate and the answerer, and (b) suppresses
cross-channel echo.

Echo: with mic and loopback both live, one voice can appear on both channels (speakers
re-captured by mic, or mic monitoring on the output). Near-identical texts (token-set
Dice ≥ 0.85) within 2 s across channels are collapsed, **mic copy wins** (it is the
user's own voice; the sys copy is the artifact). Similarity is `token_set_ratio` —
Sørensen–Dice on lowercased token *sets*, order-insensitive, which is the right
tolerance for two STT passes over the same audio producing slightly different word
order/punctuation.

The controller adds a timing wrinkle: if the *sys* copy arrives first, it is held in
`_pending_system` for `echo_window_s` + STT latency so a matching mic copy can still
win. **[measured]** That hold costs ~2.5 s on every real question from the other
speaker, so it is enabled *only* when the mic channel's policy is `full` (see §10) —
under `explicit`, the mic copy of a bled-through question is either accepted identically
or dropped, and near-duplicate dedupe covers the overlap, so the hold buys nothing.

## 10. gate.py

The core quality component. Three stages, cheapest first.

### Stage 0 — channel policy

`gate.channel_policy`, default `{mic = "explicit", sys = "full"}`. Everything is always
transcribed and always feeds context; policy only decides what may *become an answer*.

This is a **two-sided constraint and both failure modes were paid for [measured]**:

- *Mic gated freely* (`full`): the semantic gate is instructed to rewrite speech as a
  query, and it obliges for plain statements. "...so I built a RAG system where" became
  "What is a RAG system?" — ~30 unasked answer cards in a 40-minute interview, scrolling
  while the user tried to talk.
- *Mic blocked* (`off`): the user's genuine spoken questions ("Okay, what do you mean,
  how, how do I truncate it?") produced nothing and had to be force-answered by hand.

`explicit` is the split that satisfies both: accept only speech *shaped like a
question* — Stage A's interrogative fast-accept (free, instant), or anything Whisper
heard ending in `?` (rising intonation survives disfluency; a bare "now" or restarts do
not defeat it). Declaratives are rejected *before* the semantic gate can see them, so
they cannot be rewritten. Replayed over all 690 recorded mic utterances: 221 answered
under `full`, 66 under `explicit` (38 instantly via Stage A, 28 via the semantic gate on
the `?` signal), 662 never reach Ollama at all.

### Stage A — deterministic heuristics (~0 ms, pure functions, unit-tested)

Ordered; first match wins:

1. `too_few_words` — under 3 real words.
2. `filler_only` — every token in {uh, um, hmm, yeah, okay, right…}.
3. `tag_or_rhetorical` — ends with `right?`, `you know?`, `isn't it?`, `am I right?`…
   (questions in form, not in intent).
4. `human_vocative` — "Hey Sarah, can you…" (aimed at a person, not the assistant).
5. `near_duplicate` — token-set ratio ≥ 0.85 against anything answered in the last
   5 minutes.
6. **Fast-accept** `explicit_interrogative` — ends with `?` AND (after skipping leading
   discourse prefixes: so/well/okay/then/and/but) starts with an interrogative
   (what/why/how/when/…/can/could/is/do…). Skipping the prefixes is what makes
   "Okay, what do you mean, how, how do I truncate it?" an instant accept. This rule
   must run **before** the content-word check: "How are you?" is almost all stopwords.
7. `trailing_fragment` — ends in a word that cannot end a sentence (see §8 trap).
8. `no_content_words` — fewer than 2 non-filler non-stopword tokens. Without this,
   "uh, um, so, the thing is" reaches the LLM, which invents a question from
   surrounding context. This rule is the single most important false-positive guard.
9. `answer_echo` — the user reading a recent *answer* aloud (rehearsing it) must not be
   re-gated as a question. Detector: one-directional containment — fraction of the
   utterance's content words that appear in a recent answer ≥ 0.5, unless the utterance
   carries a "need marker" (wonder/explain/confused/what/how…). One-directional because
   answers are much longer than utterances; symmetric similarity would never fire.

Everything surviving goes to Stage B.

### Stage B — local LLM (gemma4:e2b via Ollama)

`POST /api/chat` with — every field the product of a measurement:

- **`"think": false`** — gemma4 is a reasoning model; without this it spends its token
  budget on thinking and returns **empty content** (`done_reason: "length"`). The
  single most important line in the integration. **[measured]**
- `"format": <JSON schema>` — forces `{"q": bool, "query": str}`. The code additionally
  requires `q is True` (the JSON boolean, not truthiness — a proxy returning the string
  `"false"` must not create a question) and a non-empty rewrite.
- `keep_alive: "30m"`, plus a warmup request at startup with a 90 s timeout —
  **[measured]** cold model load is ~67 s, warm calls ~0.5–1 s. Normal calls keep an 8 s
  timeout and *await the warmup task first* if it is still running.
- Model-reported confidence is **ignored** — **[measured]** it returns 0.95 for
  everything. Strictness is tuned by swapping the TRUE/FALSE definitions in the prompt
  (`strict`/`balanced`/`eager`), never by numeric threshold.

The system prompt's two critical instructions, both regression-tested by
`scripts/eval_gate.py`: (1) context is for **referent resolution only** — never to
supply a topic the current utterance lacks; (2) the decisive test is that the speaker
*does not know something or wants information* — naming a technical topic while
narrating is not asking. The rewrite (`query`) inlines referents from context ("what
about the second one?" → self-contained question) and is what gets sent to Claude.

On any Ollama failure: fall back to heuristics-only, warn in the status bar, keep
running.

## 11. answer.py

One fresh `claude -p <prompt>` subprocess per confirmed question. **Never** a persistent
`--input-format stream-json` session — **[measured]** messages sent while a turn is in
flight get merged into a single turn (3 sends produced 2 results, one containing two
answers). The 6–9 s one-shot overhead is acceptable because answering is async; the
merge bug is not acceptable ever. Isolation flags (`--allowed-tools ""`,
`--strict-mcp-config --mcp-config {"mcpServers":{}}`) keep the CLI from loading the
user's tools/MCP servers — cost and latency.

**Streaming:** `--output-format stream-json --include-partial-messages` and the stdout
JSONL is parsed line by line; `text_delta` events stream into the UI card via a
callback keyed by question id. Full raw bytes are retained as a fallback — if the CLI's
event shape ever changes or emits malformed JSON, the answer degrades to the raw dump
rather than vanishing.

**Concurrency:** `asyncio.Semaphore(max_concurrent=4)`, per-call timeout 45 s → kill
the subprocess and mark the card `timed out`. In-flight count is surfaced in the status
bar.

**`web_lookup` (default `auto`) — the stale-fact defence [measured, twice]:** asked
"What is Vertex AI called now again?", the model answered *"it hasn't been renamed"* —
confidently wrong (it is now the Gemini Enterprise Agent Platform). Two findings shaped
the fix: (1) merely *allowing* WebSearch is useless — the model still answered from
memory in 3.6 s and was still wrong; the prompt must say "your training data is stale
for this, search FIRST, do not answer from memory". (2) A lookup costs 15–17 s vs
3.5–7 s, so it must be rare: `needs_current_facts()` is a narrow regex family
(renamed/rebranded/"called now"/latest/current version/pricing…) that fires on **3 of
569** recorded questions (0.5%). A bare "now" is discourse filler and must not trigger.
The `searched` flag is threaded into the JSONL to explain latency outliers.

**Answer styles** (`cue` default): the prompt formats are in the code with worked
examples, because **[measured]** examples anchor length far better than word counts —
a bare cap degenerates into comma-jammed keyword lists, a generous one into essays.
`cue` = one sentence sayable verbatim (≤25 words) + 2–3 `•` fragments of ≤6 words.
The one formatting exception: code. The no-markdown rule would make the model inline
code into sentences (invalid and unreadable), so fenced blocks are explicitly demanded
for code, exempt from the word budget, and preserved verbatim by the UI's flattener.

## 12. ui.py

**Textual** app, read-only. Scrolling feed, auto-follow unless the user scrolled up;
dim transcript lines; `QACard` widgets (question in accent, spinner → streaming answer).
Keys: `p` pause, `c` clear, `t` toggle transcripts, `a` force-answer last utterance
(bypasses the gate entirely — the manual override for anything the gate missed), `s`
cycle gate mode, `x` profiles, `d` device picker with live meters, `q` quit.

Three details worth knowing before touching it:

- **`plain_text()`** flattens the model's markdown for display in a plain `Static`
  (answers are meant to be *spoken*, so `**bold**` etc. is noise) — but fenced code is
  stashed behind sentinels first and restored verbatim afterwards, because Python code
  is full of `*args, **kwargs` and indentation that the markdown regexes would destroy.
- **Streaming re-renders the whole accumulated answer** per flush, coalesced to ~10 Hz —
  flattening individual deltas would corrupt code fences split across events.
  **Trap [measured]:** the coalescing timer must never be scheduled with delay 0 —
  Textual's `Timer` divides by its interval, and deltas arriving >100 ms apart (the
  *normal* case) hit exactly that, exploding with `ZeroDivisionError` on card teardown.
  Past-due flushes render synchronously instead.
- **Answer routing is by question id**, never "append to the latest card" — answers
  finish out of order by design.

The device picker (`d`) deserves a mention: it stops capture, opens *every* endpoint
with live level meters, and the correct endpoint is self-evident — it is the one whose
meter moves when the other person talks. Selection is written back to `config.toml`.

## 13. \_\_main\_\_.py

`AmbientController` wires everything: builds queues, capture, transcriber, gate,
answerer, logger, UI; spawns the worker tasks; owns pause/resume (drain everything,
reset segmenters, ignore transcripts that started before the resume boundary), device
switching (serialised by a lock, capture restarted after), profile switching, the
status line, and shutdown bookkeeping (terminal JSONL records for all in-flight work).

The transcript consumer's exact order of operations matters and is easy to get wrong:

```
sys transcript:  echo-check → (hold for echo window, only if mic policy is "full")
                 → continuity merge → process
mic transcript:  settle any pending sys duplicates → continuity merge → process
process:         context.add (echo-suppressed) → UI transcript line
                 → channel policy check (Stage 0)
                 → snapshot gate context + answer context     ← ordered path ends here
                 → detach bounded gate task
gate task:       gate.evaluate(policy) → reject: log
                 → accept: UI card + enqueue answer job (with the snapshot)
answer worker:   claude -p → stream deltas to card → resolve card
                 → mark_answered + mark_answer_text (feeds dedupe & echo)
                 → JSONL record
```

## 14. Config, profiles, logging

**config.py** — typed dataclasses per section (`[audio] [stt] [context] [gate] [merge]
[answer] [ui]`), loaded from `config.toml`, unknown keys and invalid values rejected at
startup with named errors. Every tunable mentioned in this document lives here.

**profile.py / profiles/*.md** — optional standing context (Topic / Background /
Vocabulary sections). Vocabulary → Whisper hotwords; Topic → gate referent
disambiguation (with explicit prompt language forbidding its use as a relevance filter
or topic-injector); Topic+Background → answer pitch. Profiles are data, never
instructions — the prompts say so explicitly, which is the injection defence.

**logging_.py** — one JSONL line per utterance, written live (open-append-close per
record, lock-guarded): channel, text, gate decision + reason, query, answer, status,
`web_lookup`, per-stage latencies. Crash-safe by construction; the file is the ground
truth every analysis and replay in this project's history was mined from. Render any
session into a styled HTML replay with `scripts/render_session.py`.

## 15. The empirical foundation

Everything above, compressed into the table you would otherwise rediscover by pain:

| # | Fact | Measured value | Consequence |
|---|---|---|---|
| 1 | `claude -p` one-shot latency | 6–9 s regardless of flags/model | never in the gate path; async answering only |
| 2 | persistent claude stream-json session | merges concurrent messages into one turn | one-shot per question, always |
| 3 | gemma4 without `"think": false` | returns empty content | mandatory on every Ollama call |
| 4 | gemma4 self-reported confidence | always 0.95 | tune via prompt, never threshold |
| 5 | Ollama cold vs warm load | ~67 s vs ~0.7 s | startup warmup + `keep_alive: 30m` |
| 6 | loopback on idle endpoint | opens fine, captures silence, no error | open all endpoints + arbiter + SILENT tripwire |
| 7 | arbiter applied to mic thread | mutes the mic completely | arbiter is loopback-only |
| 8 | some host-API frames | invalid float32; squaring overflows | RMS in float64, non-finite → 0 |
| 9 | CUDA DLLs via pip on Windows | found only at first inference, via PATH | add_dll_directory + PATH prepend, materialise generator in try |
| 10 | Microsoft Store Python | blocks mic (per-app consent), loopback fine | use python.org / `py -3.11` build |
| 11 | gating inline on consumer loop | serialises all answers invisibly | detached bounded gate tasks; snapshot context first |
| 12 | 4 questions, answer pool | 22.2 s serial → 6.0 s parallel (4/4) | `max_concurrent` 4 gate 3 |
| 13 | mic gated freely | ~30 invented questions / 40 min | channel policy `explicit` |
| 14 | mic blocked outright | real spoken questions silently lost | (same — `explicit` is the split) |
| 15 | 690-utterance mic replay | 221 answered full / 66 explicit / 662 skip LLM | validates the policy |
| 16 | sys echo hold | ~2.5 s per interviewer question | enabled only when mic is `full` |
| 17 | stale-fact question from memory | confidently wrong, 3.6 s even with tool allowed | forced-search prompt, narrow trigger |
| 18 | web lookup cost / trigger rate | 15–17 s; fires 3/569 (0.5%) | `auto` default is safe |
| 19 | zero-delay Textual timer | ZeroDivisionError on teardown | render past-due flushes synchronously |
| 20 | merge_window vs merge_gap | window ≈ gap ⇒ merging never happens | window must exceed gap + speech + STT |
| 21 | prose answers, 60–90 words | unreadable mid-conversation | `cue` style; examples anchor length |
