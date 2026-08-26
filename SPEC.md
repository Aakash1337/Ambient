# Ambient — Implementation Spec

## Goal

An always-on listener that hears everything (user's mic + system audio), transcribes it
continuously, decides which utterances are **actual questions worth answering**, and shows
answers as text in a **separate live pane**. It must never block, prompt, or interrupt the
user's flow — it is a passive side-channel display only.

Name: `ambientqa`. Python 3.11. **Windows 11, Linux, and macOS are first-class targets**: one
codebase, with every platform difference confined to `ambientqa/backends/` plus a launch
wrapper (`run.sh` on Linux, `setup.ps1` + `.venv` on Windows, and the macOS shell scripts).

---

## VERIFIED ENVIRONMENT FACTS — do not re-litigate these, they were measured

These were benchmarked on the actual target machines. Build to them; do not redesign around them.

| Fact | Measured value | Consequence for design |
|---|---|---|
| `claude -p` one-shot latency | **6–9 s**, regardless of `--system-prompt`, `--strict-mcp-config`, `--exclude-dynamic-system-prompt-sections`, or model (haiku ≈ sonnet) | Fixed CLI/node/network overhead. **Never** use `claude` for per-utterance gating. Use it only to answer confirmed questions. 6–9 s is acceptable there because it is async. |
| `claude -p` persistent `--input-format stream-json` session | Messages sent while a turn is in flight get **merged into a single turn**; 3 sends produced 2 results, one containing two answers | Do **not** build a persistent stream-json RPC session. Use **one-shot `claude -p` per question**, in a bounded worker pool. |
| Ollama `gemma4:e2b` classification | **8/8 accuracy**, ~0.5–0.6 s warm | This is the question gate. |
| The gate prompt is model-specific | Engineered against `gemma4:e2b`; `qwen2.5:3b` measurably **flips on real questions** when the transcript context block is present | The model is part of the prompt contract. Do not swap `gate.model` casually. |
| `gemma4:e2b` is a **reasoning model** | With default settings it emits thinking tokens and returns **empty `message.content`** (`done_reason: "length"`) | **Must** pass `"think": false` in the request body. Without it the gate returns nothing. This is the single most important implementation detail. |
| `gemma4:e2b` confidence field | Always returns `0.95` regardless of input — uncalibrated | Do **not** threshold on model-reported confidence. Tune strictness via the **prompt**, not a numeric cutoff. |
| Ollama cold model load | ~67 s first load, ~0.7 s warm | Send a warmup request at startup and set `keep_alive: "30m"` on every call. The warmup must be **cancellable** (see gate). |
| GPU | RTX 4080, 16 GB VRAM | faster-whisper `large-v3-turbo` fp16 (~1.5 GB) + gemma4:e2b (7.2 GB) fit together comfortably. |
| Python on Windows | 3.11.9 via `py -3.11`. The `python` on PATH is a **hermes venv — do not install into it**. | Dedicated `.venv` on Windows, `.venv-linux` on Linux, and `.venv-macos` on macOS (never share them). |
| `parec` with server-default buffering | ~2 s to the first byte, then **~1 s bursts** | Useless for live segmentation. `--latency-msec=<frame_ms>` is mandatory on every parec spawn; with it the first frame lands in ~50 ms, one frame per read. |
| Raw USB mic at 100% PipeWire volume | Sits **~34 dB above hardware-neutral**; loud speech clipped **1.7% of samples** | Clipping and boosted room noise both garble Whisper. `run.sh` loads PipeWire `module-echo-cancel` (WebRTC noise suppression + AGC) exposing an `ec_mic` source; config pins `mic_device` to it. |
| Audio devices | Several endpoints per machine, names differ per OS | Devices must be selectable by case-insensitive substring, never a hardcoded index. |

---

## Architecture

Staged pipeline connected by bounded `asyncio` queues. Every stage drops-oldest on overflow
rather than blocking, so audio capture can never stall.

```
 mic ────┐
         ├─▶ capture ─▶ segmenter ─▶ STT ─▶ continuity ─▶ gate ─▶ answerer ─▶ UI pane
 loopback┘  (backends/) (silero VAD) (whisper) (merge)   (heuristic (claude -p     │
                                                          + gemma4)  pool)         │
                                              second passes: sweep ──▶ ┘  verify ──┘
```

Queues: frames (256) → utterances (12) → transcripts (24) → answer jobs (16).

### 0. `backends/` — platform capture backends

`base.py` defines the whole platform contract; **nothing above it may import a platform
library**:

- `CaptureDevice` — frozen dataclass: `id` (backend-stable opaque identifier: stringified
  PortAudio index on Windows/macOS, PipeWire source name on Linux), `name` (the only thing ever
  shown or written to config), `kind` (`"mic" | "loopback"`), `channels`, native
  `sample_rate` (informational — what `read()` delivers is described by the opened stream).
- `SourceStream` (Protocol) — `rate`, `channels`, blocking `read(frames) -> float32
  ndarray` (raises on device failure/EOF — that is how a dead device reaches the
  orchestrator's fallback), `stop()` (unblocks a reader stuck in `read()` from any thread;
  idempotent; **must run before** `close()` — closing under a live reader is a native
  use-after-free), `close()`.
- `BackendSession` (Protocol) — per-run resource holder (a PyAudio instance on Windows,
  nothing on Linux). `mic_candidates(substring)` (empty substring: default device first,
  then fallbacks; a pinned substring that matches nothing **raises** — guessing a
  microphone records the wrong room), `loopback_candidates(substring)` (empty: default
  output's endpoint first then ALL the rest; pinned-but-missing warns and falls back to the
  default rather than going mic-only), `open(device)`, `close()`.
- `AudioBackend` (Protocol) — `name`, `has_system_audio`, `list_devices()` (mics then
  loopbacks; powers the pickers), `open_session()`.

`__init__.py: get_backend(audio_config)` selects by `[audio] backend =
"auto" | "wasapi" | "pipewire" | "coreaudio"`; `auto` maps Windows to WASAPI,
Darwin to CoreAudio, and the existing Linux/default path to PipeWire. Concrete backends
import lazily so importing `ambientqa` never requires a platform audio dependency elsewhere.

**`windows.py` — WASAPI via `pyaudiowpatch`.** Enumerate WASAPI host-API devices;
`isLoopbackDevice` marks the loopback endpoints. Clamp advertised channel counts to 1–2
(ALSA plug devices advertise 32–128 channels; trusting them drowns the mic in a downmix and
hits PortAudio's crash-prone mmap path). The PortAudio constants (`paFloat32`=1,
`paWASAPI`=13) are ABI-fixed literals so injected test fakes never force the real import —
which is how the suite passes on Linux.

**`linux.py` — native PipeWire via `pactl`/`parec`. No PortAudio at all.**

- Enumerate with `pactl --format=json list sources`. A source whose
  `properties["device.class"] == "monitor"` is a monitor source ("Monitor of <sink>") —
  PipeWire's equivalent of WASAPI loopback, one per output device. **The sys channel works
  natively on Linux.** Defaults come from `pactl get-default-source` /
  `get-default-sink` + `.monitor`. Substring match tests BOTH the human description and the
  PipeWire source name (a config written on Windows pins the label; a Linux user may pin
  `alsa_input....`).
- Capture is **one `parec` subprocess per stream**:
  `parec --device=<name> --format=float32le --rate=16000 --channels=1 --raw
  --latency-msec=<frame_ms>` — parec resamples and downmixes in-process, so Linux streams
  arrive already in the pipeline's native format and skip the orchestrator's resampler.
  `--latency-msec` is required (see verified facts).
- parec **stderr goes to an anonymous temp file, never a pipe**: nothing drains stderr
  while audio streams, so a chatty parec (PULSE_LOG, repeated client warnings) would fill
  the 64 KiB pipe, block, and silently stop producing audio — a deaf channel with no
  error. The file's tail is read back into the EOF error message.
- `stop()` terminates the child; EOF on stdout unblocks any blocked reader, so shutdown is
  structurally instant, and a crashing capture process can never take the app down — its
  EOF surfaces as a stream error the orchestrator already routes around.
- PipeWire multiplexes every source, so streams do not conflict with other applications.
  A second copy of this app is different: it duplicates Whisper, gating, and paid answer
  calls, so startup must acquire a process-lifetime per-user OS lock and refuse it by
  default before loading models. An explicit unsafe override may exist for diagnostics.

**`macos.py` — CoreAudio via `sounddevice`.** Enumerate input-capable Core Audio devices,
classify established virtual loopback drivers (BlackHole, Soundflower, Loopback Audio,
Background Music, VB-Audio), prefer the default physical microphone, and open blocking
native-rate float32 `RawInputStream`s. `abort()` unblocks cross-thread reads before close.
CoreAudio physical outputs are not recordable, so system audio requires a virtual input;
missing loopback is an actionable warning and the pipeline continues microphone-only.

### 1. `audio.py` — capture orchestration (backend-neutral)

- Opens one `BackendSession`, walks `mic_candidates` (first that opens wins) and
  `loopback_candidates` (keep ALL when more than one — see arbiter), one capture thread per
  stream. Off-rate streams resample to 16 kHz with `soxr` (lazy import — parec streams are
  already 16 kHz mono); multichannel downmixes by averaging.
- **A blank `output_device` opens EVERY loopback endpoint.** Which endpoint a call renders
  to is not knowable in advance and changes between sessions. A `LoopbackArbiter` forwards
  frames from only the endpoint currently carrying speech (RMS > 0.004, chosen under the
  energy-VAD threshold), since one segmenter cannot be fed two interleaved conversations.
  Handover is immediate once the incumbent has been quiet for ~1.5 s; the hold only stops
  another endpoint stealing a live utterance. The arbiter applies to loopback **only** —
  passing it to the mic thread mutes the microphone outright, because the mic can never win
  a contest it was never entered in.
- If `output_device` names no existing endpoint, fall back to the current default and warn.
  If no loopback opens at all, warn and continue **mic-only** — never crash.
- Track per-source RMS. A source open but below the noise floor for `silent_source_warn_s`
  reads `SILENT <n>s ⚠` in the status bar, not `on`. A loopback pinned to an idle endpoint
  opens without error and captures nothing; this **measured** failure cost a full 40-minute
  interview.
- **Lifecycle rules** (the device picker stops and restarts capture at runtime, from
  executor threads whose ordering the callers cannot guarantee):
  - `start()`/`stop()` are serialized by a lifecycle lock — an abandoned `stop()` racing a
    recovery `start()` must not close the new session's streams under their readers.
  - Every capture thread carries a **generation token**, bumped by each start and stop. A
    zombie thread that outlived the join timeout must neither push its stale device's
    frames into the live session's queue nor decrement the new session's runner counts.
  - Stop ordering is fixed: stop every stream → join reader threads → close streams →
    close session.

### 2. `segmenter.py` — utterance segmentation

- **`silero-vad`** (ONNX via `onnxruntime`, CPU — it is tiny and must not contend with the
  GPU). Energy-VAD fallback if Silero cannot initialize.
- One independent segmenter instance per channel.
- Pre-roll ring buffer of ~300 ms so utterance onsets are not clipped.
- Emit on speech + **≥ 900 ms trailing silence** (`silence_ms`); force-flush past **20 s**
  (`max_utterance_s`); discard segments under **400 ms** (`min_utterance_ms`).

### 3. `stt.py` — transcription

- **`faster-whisper`**, `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`;
  fall back to `cpu`/`int8` with a loud status-bar warning. CUDA failure surfaces at the
  **first inference**, not model construction (`segments` is a lazy generator; a missing
  cuDNN DLL only shows up on consumption) — materialise inside the try block and recover to
  CPU there too, instead of dying.
- An out-of-memory fallback says what happened and names the safe remedies (close a
  GPU-heavy game, another Whisper/dictation process, or an unused Ollama model). It must
  not terminate or unload another application on the user's behalf.
- Single serial worker (one GPU), bounded queue. `condition_on_previous_text=False`
  (prevents hallucination loops on ambient noise).
- Keep Whisper's punctuation. **The trailing `?` is a strong gate signal — do not strip it.**
- The active profile feeds `hotwords` (vocabulary) and a shortened `initial_prompt` (topic).
- Hallucination blocklist: **exact match on the normalised whole transcript, never a
  prefix.** Prefix matching silently ate real speech — interviewers open with a courtesy
  ("Thank you. So, tell me about your experience with Kubernetes.") and the entire question
  vanished before gating, with no log record. Defaults are therefore whole utterances:
  "Thank you.", "Thank you very much.", "Thanks for watching!", "Subtitles by the Amara.org
  community", etc.

### 3.5. `continuity.py` — thought coalescing (the merge layer)

People trail off, think ~5 s, then ask the question that continues the setup. Gating the
halves separately loses the constraint. Per-channel stateful merger, between STT and gate:

- `is_open_utterance`: a terminal `?` or `!` **closes** a thought no matter what the last
  word is (English questions legitimately strand a preposition — "What are you working
  on?"; treating those as open parked complete questions for the whole hold, or glued them
  onto the next sentence, destroying the `?` fast-accept downstream). A terminal `.` earns
  **no** such trust: Whisper invents a period at every VAD boundary, so "so tell me about."
  stays open. Also open: trailing fragment word, trailing comma/dash, no terminal
  punctuation. Exception: a complete command-form request of at most six words is closed
  even without punctuation, so Whisper's `EXPLAIN RAG` reaches the gate immediately instead
  of waiting the full 13-second hold.
- `join_fragments` strips Whisper's artificial boundary periods **and ellipses** at the
  seam before joining.
- A continuation merges when the audio-time gap ≤ `merge_gap_s` (**6.5** — measured miss at
  4.5: a ~5 s think-pause between a trailed-off setup and its question stayed unmerged) and
  the pending text is open or the new text starts like a continuation (leading
  conjunction/lowercase). The wall-clock hold is `merge_window_s` (**13.0**) — it must
  outlast the gap PLUS the continuation's spoken length PLUS its transcription latency, or
  the hold expires before the continuation is even transcribed.
- Safety caps: `max_merge_parts` 5, `max_merge_s` 25. Timeout flushes are deterministic;
  pause and shutdown flush everything held (logged `paused` /
  `shutdown_before_merge_window`).

### 4. `gate.py` — question detection (the core quality requirement)

**Stage 0 — channel policy.** `gate.channel_policy` (default `{mic = "explicit",
sys = "full"}`) sets how freely each channel is judged. Everything is transcribed, shown,
and added to context regardless; this decides only what may become an answer.

- `full` — heuristics plus the semantic gate.
- `explicit` — only speech actually shaped like a question: Stage A's fast-accepts
  (interrogative or imperative request), the reask acceptance below, **or** the semantic
  gate but only if the text ends in `?`. A declarative sentence never reaches the semantic
  gate.
- `off` — never answered; short-circuits before any heuristic.

This is a **two-sided** constraint and both sides were measured:

- The wearer already knows what they are saying, and the semantic gate is asked to *rewrite
  speech as a query*, so it obliges for statements: `"...so I built a RAG system where"` →
  *"What is a RAG system?"*, ~30 unasked cards in 40 minutes.
- But blocking the channel entirely swallows real asks: `"Okay, what do you mean, how, how
  do I truncate it?"` produced nothing and had to be force-answered by hand.

Replay over 690 recorded mic utterances: 221 answered under an unrestricted gate, 66 under
`explicit`. 662 never reach Ollama.

Corollary: the `_pending_system` echo hold is **skipped** unless `mic` is `full`. That hold
exists so a mic copy wins the echo contest, and it costs ~2.5 s on every question from the
other speaker. Under `explicit` the mic copy is either accepted identically or dropped, and
near-duplicate dedupe covers the overlap — so the delay buys nothing.

**Gating runs OFF the ordered consumer path**, bounded by `gate.max_concurrent` (default 3).
Awaiting the ~900 ms Ollama call inline stalls every later transcript behind it. Only two
things stay ordered: adding to the context window, and snapshotting the gate/answer context
each question is judged against. Snapshot on the ordered path, never inside the task, or a
question gets judged against a conversation that moved on without it.

**Stage A — local heuristics (~0 ms), pure functions, unit-tested. The order is
load-bearing; run exactly this sequence:**

1. reject `too_few_words` — fewer than `min_words` (3) words, except a complete
   command-form request with an object (`Explain RAG`); incomplete forms (`Tell me`,
   `Talk about`, `Explain about`) still reject.
2. reject `filler_only` — every word is filler (`uh um hmm hm yeah okay ok right`).
3. reject `near_duplicate` — token-set ratio ≥ `dedupe_ratio` (0.85) against a question
   answered within `reask_cooldown_s` (**8 s**) — *time-scoped on purpose*: inside the
   window a near-duplicate is a mechanical dupe (Whisper re-emission, merge artifact); past
   it, an almost-identical question is a human deliberately re-asking because the first
   answer missed, and gets a fresh answer. Must precede the fast-accept: the fast-accept is
   exempt from answer-echo suppression but NOT from verbatim re-ask dedupe.
4. reject `tag_or_rhetorical` — a tag-pattern match (`right?`, `you know?`, `isn't it?`,
   `innit?`, `yeah?`, `okay?`, `ok?`, `know what I mean?`, `am I right?`) counts **only**
   when the tag follows a comma-closed statement, or when everything before it is function
   words. The tag words legitimately end genuine interrogatives — "Is my understanding of
   the GIL right?" answers; "Am I right?" does not. Judged before the fast-accept because a
   pure tag is interrogative-shaped and would sail through it.
5. **fast-accept `explicit_interrogative`** (skips Stage B): ends with `?` **and**, after
   stripping `QUESTION_PREFIXES` (`so well okay ok then and but` plus conversational and acknowledgment
   lead-ins interviewers habitually attach: `great alright right sure now yes yeah cool
   perfect good nice fine again wait hello please`), starts with an interrogative
   (`what/why/how/when/where/who/which/whose/can/could/would/should/do/does/did/is/are/was/were/will/have/has/am/may/might/shall`),
   **and** is not a vocative — the vocative guard is what keeps names that casefold into
   interrogatives from slipping through ("Will, can you review this?" is addressed to Will).
   Deliberately precedes the vocative reject and the content-word rule: "Okay, can you
   explain the CAP theorem?" keys on its first token, and "How are you?" is almost entirely
   stopwords.
6. **accept `imperative_request`** — command-form asks carry no `?` and no interrogative,
   yet "Talk about evaluation metrics." is as direct as a request gets. Accept when any
   sentence starts (after prefixes/"please") with a request verb (`explain describe talk
   tell walk give list compare elaborate define discuss summarize summarise outline`), or — for
   punctuation-less transcriptions ("Evaluation matrix talk about them") — when a request
   bigram (`talk about` / `tell me` / `walk me`) appears anywhere. Guards: whole-utterance
   idioms ("tell me about it", "give me a second"); a plan marker (`I/we/i'll/we'll/gonna/
   going/later/tomorrow`) kills the bigram fallback ("we'll talk about that later" asks
   nothing); vocatives ("Sarah, tell me…"); and a trailing fragment word disqualifies the
   stub — "So, tell me about" is a request cut off mid-sentence and belongs to the merge
   layer. A generic honorific plus a clear information command (`Sir, talk to me about
   RAG`) is allowed, but the honorific is not a general question prefix: `Sir, can you
   close the window?` remains a human vocative.
7. reject `human_vocative` — `hey <Name> …`, or `<Name>, ` followed by modal-you (`can/
   could/would/will/do/did/are/have you`) **or** an imperative verb (`tell talk explain
   walk describe give`, optionally after "please"). First tokens in `QUESTION_PREFIXES` are
   never names (Whisper capitalizes every sentence start).
8. reject `trailing_fragment` — the last word is a dangling function word (prepositions,
   conjunctions, articles, determiners, auxiliaries; deliberately NOT
   `this/that/these/those/like/when`, each of which can end a complete sentence — listing
   them rejected real questions and added ~9 s of merge latency).
9. reject `no_content_words` — fewer than 2 non-filler, non-stopword tokens. Without this
   the semantic gate invents a question out of the surrounding context.
10. Everything else → Stage B.

**Routing around Stage A (in `evaluate`):**

- A `human_vocative` reject on a **full-policy channel is DEMOTED to Stage B**, not hard
  rejected: there a vocative is usually the interviewer addressing the candidate by name —
  "Aakash, can you explain decorators?" is the exact question this tool exists to answer.
  The Stage B prompt already returns FALSE for questions aimed at another human. On the mic
  channel the hard reject stands: the user hailing someone by name is definitionally
  talking to another human.
- Stage A accepts are answered with the transcript text as the query, and are exempt from
  answer-echo suppression (verbatim re-asks were already caught by rule 3).
- **Statement retries** (`explicit` policy, Stage A said "llm", no terminal `?`): if the
  statement is ≥ 0.5 token-set-similar to a question answered ≤ 90 s ago, accept
  **outright** as `reask_of_recent` — the first answer missed (mishearing, wrong angle) and
  retries rarely carry fresh question intonation ("no, prompt engineering…"). Not sent to
  the semantic gate on purpose: a retry is phrased as a correction or plan, which that
  prompt is trained to call narration. The history-aware answerer is the judge that can
  resolve what changed. Otherwise reject `not_a_direct_question`.
- **Answer echo**: before Stage B, reject `answer_echo` when ≥ `answer_echo_ratio` (0.5) of
  the utterance's content words came from an answer shown within `answer_echo_window_s`
  (300 s) and no need-marker word is present — the user is rehearsing an answer aloud, not
  asking again. Also saves the LLM call.

**Stage B — local LLM (`gemma4:e2b` via Ollama HTTP, ~0.5–0.9 s warm):**

- `POST http://127.0.0.1:11434/api/chat`
- Body **must** include `"think": false`, `"stream": false`, `"keep_alive": "30m"`,
  `"options": {"temperature": 0, "num_predict": 64}`, and a JSON schema in `"format"`.
- Send the last `context_turns` (default 6) transcript lines, labelled *for referent
  resolution only*; the prompt's decisive test is that the information need must be present
  in the current utterance itself. An active profile contributes its topic as referent
  disambiguation data only.
- The model returns `{"q": bool, "query": "<self-contained rewrite>"}`. Accept only the
  literal JSON boolean `true` (`"false"` is truthy in Python); an acceptance without a
  non-empty rewrite is treated as a reject. **Ignore any confidence value.**
- Strictness by prompt swap, `mode = strict | balanced | eager` (default `balanced`); ship
  all three.
- The ~67 s cold load is covered by a **cancellable startup warmup**: a daemon thread
  signalling back into the loop, never `to_thread` — `Task.cancel` cannot interrupt a
  running executor future and `asyncio.run` joins the default executor on exit, so quitting
  during a cold load would keep the process alive up to 90 s after the UI is gone.
  `classify` awaits an in-flight warmup before posting.
- On Ollama failure: heuristics-only, status-bar warning, keep running
  (reason `ollama_unavailable`).

**Cross-channel echo suppression:** if mic and loopback produce near-identical transcripts
within `echo_window_s` (2 s), keep only the mic one. The sys-side hold applies only when
`mic = "full"` (see the corollary above).

### 5. `answer.py` — answering

- **One-shot `claude -p` per confirmed question** (never a persistent session — see
  verified facts). Command: `claude -p <prompt> --model <answer_model> --system-prompt …
  --allowed-tools "" --strict-mcp-config --mcp-config {"mcpServers":{}}`, plus
  `--output-format stream-json --include-partial-messages --verbose` when
  `answer.stream = true` (default). Assistant text deltas stream onto the card as they
  arrive; each delta routes by question id. If nothing extractable was parsed, fall back to
  the raw bytes losslessly (the CLI changed shape) — but one stray non-JSON line must not
  replace a fully assembled answer. `answer.stream = false` restores buffered stdout.
- Default `answer_model = claude-sonnet-5`; configurable.
- **Every answer prompt carries, in order:**
  1. **Q&A history** — up to `history_turns` (8) completed question/answer pairs, oldest
     first. This — not the raw transcript — is what lets "elaborate on the second method"
     resolve: the methods exist only in the answer prose. Instructed to use an earlier
     answer ONLY when the current question refers back (ordinal, pronoun, explicit
     callback); a self-contained question gets a fresh answer. History is read at **answer
     time**, not enqueue time, so a follow-up sees an answer that completed after it was
     queued.
  2. The recent transcript as delimited background, with **setup-awareness**: the final
     transcript line(s) before a question are often its premise — a scenario the speaker
     trailed off from ("So if the content you're searching keeps changing…" → "Which method
     would you use?") — and the answer must honor it, not answer in the abstract.
  3. A **channel stance**. `sys` questions coach the user's own first-person spoken answer
     (that is what the cue card is for). `mic` questions may be addressed to another person
     or another assistant whose replies the transcript does NOT carry: the model must
     **never** answer in first person as that addressee or invent its state ("No, I don't
     auto-launch" reads as authoritative and is pure fabrication); when only the addressee
     could know, say so plainly.
  4. Via the system prompt, the standing profile — explicitly **subordinate to the recent
     transcript thread**: it pitches the level, it never substitutes its domain as the
     topic of a question that continues the conversation.
- System prompt: terse, answer in ≤ `max_words`, "not sure" over guessing (a model
  confidently gave a **wrong** answer about Node's fetch timeout — instruct against it).
- **`answer.web_lookup`** (`off | auto | always`, default `auto`). Hedging is not enough
  for facts that changed after training (Vertex AI → Gemini Enterprise Agent Platform:
  memory said "not renamed"). `auto` grants `--allowed-tools WebSearch` **and** an explicit
  search directive, for naming/version/pricing/availability questions only — measured at
  0.5% of 569 recorded questions. Permitting the tool without demanding a search left the
  model answering from memory (3.6 s, still wrong); a real lookup costs 15–17 s against
  3.5 s, unusable as a blanket policy. The `searched` flag is recorded in the JSONL **on
  every path including timeout and error** — a timed-out web lookup is precisely the record
  that needs its latency explained.
- `answer.style` selects the shape. Default **`cue`**: one sentence sayable verbatim, blank
  line, then 2–3 `•` keyword fragments of ≤6 words — the reader is *already speaking* and
  gets one glance; prose measured at 60–90 words was unreadable in that moment. `interview`
  (2–4 spoken sentences, no markdown) and `terse` remain. Every style carries a CODE
  EXCEPTION: code goes in a real fenced block, never flattened into a sentence, exempt from
  the word budget.
- Style is snapshotted per queued answer. A runtime Voice-mode delivery toggle therefore
  cannot turn an already-queued cue answer into a different prompt or make its bullets get
  read under a later mode.
- **`asyncio.Semaphore(max_concurrent)`** (default 4). Parallel answering measured 6.0 s
  wall clock for four questions against 22.2 s serialised. Per-call timeout
  `answer_timeout_s` (45) → kill process, mark card `timed out`. Answers bind to question
  id, never appended blindly.

### 5.5. Two-pass system (both passes are best-effort: every failure path is a no-op)

**Answer audit — `[answer] verify = "always" | "off"` (default off).** When enabled, after each
delivered `ok` answer, an auditor re-reads it with what the fast path did not have: a wider
transcript window (`verify_context_turns` = 18), the full Q&A history, and the **raw
transcription** (which may contain mishearings the rewritten query inherited). It replies
`OK` — or a full replacement answer — and replaces the card (status `"revised"`) **only**
when the delivered answer is materially wrong: missed constraint, misheard question,
factual error, first-person impersonation of another party, topic wrongly sourced from the
standing profile, dropped enumeration item. Style, phrasing, ordering, added depth are
NEVER grounds — a correction lands ~8 s after the user may already be speaking from the
first card, so it must earn the distraction. Audits run under a semaphore of 1 and share
the aggregate Claude process limit with primary answers, strictly after the answer is on
screen (first-answer latency untouched). A revision also **replaces the Q&A-history
entry** — otherwise "elaborate on that" expands the answer the audit just retracted — and
is logged as `gate_reason: "verify_revision"`, `answer_status: "revised"`.

**Missed-question sweep — `[answer] sweep = "always" | "off"` (default always).** The audit
only reviews answers that exist; a wrongly rejected question produces nothing to audit.
Every `sweep_interval_s` (25 s) a sweeper hands the recent **judgment-stage rejections
only** — `not_a_direct_question`, `ollama_reject`, `ollama_unavailable`,
`human_vocative`; mechanical rejections (filler, dedupe, echo, tags, pause) are not
misses and never enter — to
`sweep_model` (default `claude-haiku-4-5`; a small classification wants a fast cheap model)
along with wide transcript context and the answered/in-flight list. Strict-JSON reply, at
most 2 recoveries per sweep, never resurrecting anything already answered. Genuine asks
come back as late cards through the **normal answer path** (streaming, audit and all) with
`gate_reason: "second_pass_recovery"`. The `human_vocative` case is included because
sentence-initial capitalization can make a discourse marker look like a person's name;
the sweep independently rejects speech genuinely addressed to another human.

### 6. `ui.py` — the live pane

**Textual** app, read-only, never prompts.

- Feed of transcript lines (dim, `HH:MM:SS  [mic|sys]  text`) and Q&A cards (question in
  accent colour, streaming answer below). `feed_direction = "top"` (newest first, default)
  or `"bottom"`. Auto-follow unless the user has scrolled away. A card can be re-resolved
  after delivery — the verify pass replaces its answer with status `revised`; recovered
  questions appear as normal cards.
- Status bar: listening/paused, mic + loopback state (including `SILENT <n>s ⚠`), whisper
  device, gate mode, active profile, queue depths, answers in flight/done, token estimate,
  last warning. Pause additionally recolours the bar and shows a docked banner — a paused
  app must not look like a silent one.
- Keys (top-level BINDINGS): `p` pause/resume · `c` clear feed · `t` toggle transcript
  lines · `l` session browser (read-only replay of a recorded JSONL over the live pane) ·
  `a` force-answer the last utterance (bypasses the gate; reason `forced_by_user`) · `s`
  cycle strict/balanced/eager · `m` mute/unmute voice · `g` toggle Q&A/Agent interaction ·
  `r` toggle Normal/Conversational delivery (the three voice-only bindings are hidden in
  non-Voice launches) · `x` context-profile
  picker · `1` mute/resume mic input · `2` mute/resume system input · `d` audio device picker (live
  level meters; selection persists to config.toml via `config_write` and restarts capture)
  · `q` quit. Modal pickers close on Esc or their opening key and navigate with j/k/Enter.
- `action_devices` / `action_profiles` / `action_sessions` each run in their **own
  exclusive worker group**: in a shared group, pressing one key while another picker was
  stopping/restarting capture would cancel that worker mid-flight and strand the restart.
- Everything is appended to `logs/session-<ts>.jsonl` — one record per utterance with
  channel, text, gate decision, `gate_reason`, query, answer, `answer_status`,
  `web_lookup`, latencies. Gate reasons include the Stage A reasons above plus
  `ollama_accept/ollama_reject/ollama_unavailable`, `channel_not_answered`,
  `cross_channel_echo`, `answer_echo`, `imperative_request`, `reask_of_recent`,
  `second_pass_recovery`, `verify_revision`, `forced_by_user`, `paused`,
  `paused_during_gate`, `transcript_queue_overflow`, and the shutdown flush reasons.
  Answer statuses: `ok`, `error`, `timed_out`, `dropped`, `cancelled`, `revised`.

### 6.5. `tts.py` — Voice delivery

- Voice is a launch role (`--voice`), never a second companion process. The per-user
  application lock prevents duplicate capture/Whisper/Claude pipelines.
- Every launch starts in **Normal** delivery: configured answer style (normally `cue`) and
  configured speech selection (normally `first_line`). Pressing `r` opts into
  **Conversational** delivery for future work: `interview` prose plus `full` speech. The
  choice is runtime-only and switching back restores configured behavior without writing
  `config.toml`.
- `speakable(full)` includes all non-code answer lines, removes Markdown decoration, and
  gives cue fragments sentence boundaries. Fenced code is never spoken.
- In Conversational mode only, a narrow local control recognises “continue reading,” “read
  the rest,” and “repeat that” against a mic-channel answer completed within 90 seconds.
  This bypasses Claude and the missed-question sweep. It also tolerates the observed ASR
  inversion `I'm not going to continue reading out the whole answer`, because both model
  passes rationally see that corrupted transcript as narration. System audio, stale/no
  answer, bare “continue,” and unrelated reading narration never activate it.
- Playback is serial and capture is muted for the configured channels while speech is
  audible, so default Conversational mode is turn-taking, not safe barge-in. Cross-process
  claims/election, bounded player teardown, and capture-time mute windows prevent TTS echo
  from becoming a new question.

### 6.6. Runtime Agent conversation

- Voice exposes two independent runtime axes. **Interaction** is Q&A or Agent (`g` in the TUI;
  explicit browser buttons), while **Delivery** is Normal or Conversational (`r`). A knowledge
  profile is orthogonal: cybersecurity, interview, and support profiles work in either role.
  Every launch starts in Q&A; legacy `## Interaction` profile metadata is parsed but ignored.
- Entering or leaving Agent, or changing profile while Agent is active, starts a clean conversation
  boundary: prior context/history, pending cards, gate work, and queued speech cannot leak across it.
- Enabling Agent defaults Delivery to Conversational but leaves the Delivery control available;
  leaving Agent restores the pre-Agent delivery preference.
- The selected speaker channel bypasses the question-only gate. Complete statements and terse
  replies are actionable; local greetings, thanks, hold requests, and goodbyes answer without
  an LLM call. Filler, echoes, the non-speaker channel, and muted input never reach the Agent
  model or missed-question sweep.
- Agent answer work is serialized to preserve turn order and Q&A history. Replies use short
  TTS-shaped prose and layered courtesy safeguards; unguarded streaming deltas are withheld.
  Operational failures produce a courteous recovery prompt, not raw CLI diagnostics.
- Mic and system listening switches are independent from global Pause, voice-output mute, gate
  policy, and automatic playback echo windows. A toggle discards that channel's not-yet-admitted
  frames, utterances, transcripts, continuity fragments, and late STT results without touching
  the other channel. Muted transcript text is not persisted.
- Profiles may optionally provide `## Customer Channel` (`mic` or `sys`) and `## Greeting`; absent
  settings use the microphone and a generic AI-assistant greeting. They configure an Agent session
  but never activate the Agent role.
- The safe default still mutes both capture channels during playback, so this is turn-taking,
  not full-duplex barge-in. Local `paplay` output also needs explicit call-device routing before
  a remote customer can hear it.

---

## Runtime hardening (`__main__.py`) — each of these is a measured crash, not a nicety

1. On POSIX, call `multiprocessing.resource_tracker.ensure_running()` **before Textual
   starts**. Whisper's lazy model load creates a multiprocessing lock at *first
   transcription*, which spawns the resource-tracker with `sys.stderr.fileno()` — by then
   Textual's stderr capture has `fileno() == -1`, the spawn dies with "bad value(s) in
   fds_to_keep", and **every** transcription fails, CUDA and CPU fallback alike.
2. Set `HF_HUB_DISABLE_PROGRESS_BARS=1` (a download bar renders as garbage into Textual's
   capture, and this skips most of the tqdm machinery that wanted the lock above).
3. Seed Textual's `active_app` ContextVar in the controller's `run()`. Controller tasks are
   not descendants of Textual's message pump, so a widget timer they trigger (the streaming
   card's flush throttle) cannot resolve `active_app`, dies with `LookupError`, and crashes
   quit when shutdown awaits the dead timer.
4. Root logging goes to `logs/ambientqa.log`, **never stderr** — once Textual owns the
   terminal, anything on stderr is painted raw over the UI and one component's traceback
   makes the whole app look dead.

---

## Config

`config.toml` at project root, loaded at startup into typed dataclasses; unknown sections
and keys are hard errors, as are invalid values (`validate_config`). Ship a fully-commented
default. A platform overlay may set top-level `extends` to a relative base config; nested
sections merge recursively before the same strict validation. Structure and keys (defaults
in parentheses):

- `[audio]` — `backend` ("auto" | "wasapi" | "pipewire" | "coreaudio"), `mic_device` (""),
  `output_device` (""), `sample_rate` (16000, enforced), `frame_ms` (25, must be 20–30),
  `queue_size` (256), `silence_ms` (900), `pre_roll_ms` (300), `min_utterance_ms` (400),
  `max_utterance_s` (20.0), `silent_source_warn_s` (45.0)
- `[stt]` — `model` ("large-v3-turbo"), `device` ("cuda"), `compute_type` ("float16"),
  `cpu_compute_type` ("int8"), `queue_size` (12), `language` ("" = autodetect),
  `hallucination_blocklist` (whole-utterance defaults above)
- `[context]` — `enabled` (true), `profile` ("" = none; relative paths resolve from the
  config file; profiles are free-form Markdown under `profiles/`; Agent sessions may use optional
  `Customer Channel` and `Greeting` sections, while role selection remains runtime state)
- `[gate]` — `model` ("gemma4:e2b"), `ollama_url`, `mode` ("balanced"),
  `channel_policy` ({mic = "explicit", sys = "full"}; only mic/sys keys; not all "off"),
  `max_concurrent` (3), `min_words` (3), `context_turns` (6), `dedupe_window_s` (300.0),
  `dedupe_ratio` (0.85), `reask_cooldown_s` (8.0), `echo_window_s` (2.0), `echo_ratio`
  (0.85), `answer_echo_window_s` (300.0), `answer_echo_ratio` (0.5; 0 disables),
  `queue_size` (24), `request_timeout_s` (8.0)
- `[merge]` — `enabled` (true), `merge_gap_s` (6.5), `merge_window_s` (13.0),
  `max_merge_parts` (5), `max_merge_s` (25.0)
- `[answer]` — `answer_model` ("claude-sonnet-5"), `stream` (true), `style` ("cue" |
  "interview" | "terse"), `max_words` (45), `max_concurrent` (4), `web_lookup` ("auto"),
  `answer_timeout_s` (45.0), `context_turns` (6), `history_turns` (8; 0 disables history),
  `verify` ("always" | "off", default "off"), `verify_context_turns` (18),
  `sweep` ("always" | "off", default "always"),
  `sweep_interval_s` (25.0), `sweep_model` ("claude-haiku-4-5"; empty falls back to
  `answer_model`), `queue_size` (16)
- `[ui]` — `show_transcripts` (true), `log_dir` ("logs"), `status_interval_s` (0.5),
  `feed_direction` ("top" | "bottom")

The device and profile pickers write back through `config_write.py`, which performs small,
**comment-preserving** updates — never a full serialize that would destroy the shipped
commentary.

---

## Deliverables

```
Q&A/
  SPEC.md  README.md  requirements.txt  config.toml  setup.ps1  run.sh
  run-emergency.sh
  ambientqa/
    __init__.py __main__.py config.py config_write.py bus.py logging_.py
    audio.py audio_devices.py segmenter.py stt.py continuity.py context.py
    gate.py answer.py profile.py ui.py mode_picker.py
    backends/  __init__.py base.py windows.py linux.py
  profiles/   free-form Markdown standing-context profiles (picked with x)
  scripts/    list_devices.py pick_mic.py eval_gate.py render_session.py
  tests/      test_answer.py test_answer_channels.py test_answer_echo.py
              test_answer_style.py test_audio_devices.py test_audio_health.py
              test_backends.py test_macos_backend.py test_code_answers.py test_config.py
              test_config_write.py test_context.py test_continuity.py
              test_gate_heuristics.py test_mode_picker.py
              test_pause_and_fragments.py test_profile.py
              test_segmenter.py test_stt.py test_ui_audio_devices.py
              test_ui_profiles.py test_ui_sessions.py test_ui_streaming.py
              test_ui_transcripts.py test_ui_voice_mode.py test_tts.py
              test_voice_controller.py test_web_lookup.py
```

- `requirements.txt`: `pyaudiowpatch` gated to Windows, `sounddevice` gated to macOS;
  shared packages are `soxr`, `numpy`, `silero-vad`, `onnxruntime`, `faster-whisper`,
  `textual`, `tomli`, and `pytest`; NVIDIA CUDA packages are excluded on macOS.
- `setup.ps1` (Windows): creates `.venv` with **`py -3.11`** (not the PATH python — that is
  a hermes venv), installs requirements, warms the Ollama model, prints the device list.
- `run.sh` (Linux): bootstraps `.venv-linux` gated on a `.deps-installed` **stamp written
  only after pip succeeds** (gating on `bin/python` alone mistakes an interrupted install
  for a finished one; a stamp older than `requirements.txt` re-runs pip); loads PipeWire
  `module-echo-cancel` exposing the `ec_mic` source config pins (see verified facts);
  when passed `--choose` by the desktop entry, runs the standalone Textual mode picker before
  touching audio/models and maps its fixed Assist/Voice/Web Assist/Web Voice/Emergency/Cancel
  results (Web Voice is the single-process `--web --voice --open-browser` combination); starts
  Ollama if absent; exports `LD_LIBRARY_PATH` pointing CTranslate2 at the
  pip-installed CUDA libraries; `exec`s `python -m ambientqa`. The application claims a
  process-lifetime per-user lock before model load, retains heartbeats for status/legacy
  detection, and refuses a second full pipeline unless the diagnostic `--allow-multiple`
  escape hatch is explicitly supplied.
- `setup-macos.sh` / `run-macos.sh`: create and maintain `.venv-macos`, load the
  `config.macos.toml` overlay, omit NVIDIA packages, start Ollama when available, and launch
  the CoreAudio path. Voice playback uses a sounddevice RawOutputStream; system audio uses a
  separately installed virtual input.
- `scripts/`: `list_devices.py` prints inputs/loopbacks per backend; `pick_mic.py`
  interactively meters and selects a device; `eval_gate.py` replays labelled cases against
  the gate (run it after touching heuristics or Stage B prompts); `render_session.py`
  renders a session JSONL into a self-contained HTML replay of the pane.
- `README.md`: quickstart per platform, config reference, troubleshooting (no loopback
  device, CUDA fallback, Ollama not running, empty gate responses → `think:false`,
  missing `ec_mic`).
- Run with `python -m ambientqa`, `./run.sh` on Linux, or `./run-macos.sh` on macOS.
- **Opt-in web console** (`webui.py` + `webstatic/`, launched via `--web` /
  `run-web.sh`, rehearsed offline with `scripts/webui_demo.py`, tested by
  `tests/test_webui.py`): a browser rendering of the same pipeline behind the
  controller's `app_factory` seam. Stdlib-only (no requirements.txt change), binds
  127.0.0.1 only, defaults to port 8802, and identifies itself at `/api/health`.
  An unpinned launch scans a short next-port range when another local service owns
  the default, then opens the actual healthy URL; an explicit `--web-port` fails
  cleanly instead of silently moving. Its status tick doubles as the instance
  heartbeat, and the Textual pane remains the default surface — see
  docs/ARCHITECTURE.md §13b.

## Non-negotiables

1. `"think": false` on every Ollama call.
2. No persistent `claude` stream-json session — one-shot per question only.
3. No `claude` call in the gate path (the two second passes run detached, off the live
   path, and every failure of theirs is a no-op).
4. Capture threads must never block on a full queue.
5. Any missing dependency (loopback device, CUDA, Ollama) degrades gracefully with a
   status-bar warning; it never crashes the app.
6. parec stderr never attaches to a pipe.
7. Stream teardown order is stop → join readers → close → session close; `stop()` must be
   callable from any thread and unblock a blocked `read()`.
8. Refuse a second full app pipeline by default; multiple copies require an explicit unsafe
   diagnostic override.
9. The STT hallucination blocklist matches the exact normalised whole transcript, never a
   prefix.
10. Nothing writes to stderr once Textual owns the terminal; the resource tracker starts
    before Textual does.

## Acceptance

- `pytest` covers all three backend selectors; the CoreAudio suite uses injected
  sounddevice fakes so enumeration, stream lifecycle, and stop-unblocks-read behavior are
  verified from any host. Heuristic gate tests cover every Stage A rule above **and their ordering**.
- App starts, shows `listening`, transcribes both channels on the current platform's
  backend, and leaves non-questions unanswered while answering real ones.
- Long monologue containing one embedded question → exactly one answer card.
- "Aakash, can you explain decorators?" on `sys` is answered; the same shape spoken into
  the mic is not.
- A materially wrong first answer is replaced on its card as `revised`; a question the gate
  wrongly rejected comes back as a late card with `second_pass_recovery`.
