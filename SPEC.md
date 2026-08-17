# Ambient Q&A — Implementation Spec

## Goal

An always-on listener that hears everything (user's mic + system audio), transcribes it
continuously, decides which utterances are **actual questions worth answering**, and shows
answers as text in a **separate live pane**. It must never block, prompt, or interrupt the
user's flow — it is a passive side-channel display only.

Name: `ambientqa`. Python 3.11. Windows 11 (primary target).

---

## VERIFIED ENVIRONMENT FACTS — do not re-litigate these, they were measured

These were benchmarked on this exact machine. Build to them; do not redesign around them.

| Fact | Measured value | Consequence for design |
|---|---|---|
| `claude -p` one-shot latency | **6–9 s**, regardless of `--system-prompt`, `--strict-mcp-config`, `--exclude-dynamic-system-prompt-sections`, or model (haiku ≈ sonnet) | Fixed CLI/node/network overhead. **Never** use `claude` for per-utterance gating. Use it only to answer confirmed questions. 6–9 s is acceptable there because it is async. |
| `claude -p` persistent `--input-format stream-json` session | Messages sent while a turn is in flight get **merged into a single turn**; 3 sends produced 2 results, one containing two answers | Do **not** build a persistent stream-json RPC session. Use **one-shot `claude -p` per question**, in a bounded worker pool. |
| Ollama `gemma4:e2b` classification | **8/8 accuracy, median 532 ms**, warm | This is the question gate. |
| `gemma4:e2b` is a **reasoning model** | With default settings it emits thinking tokens and returns **empty `message.content`** (`done_reason: "length"`) | **Must** pass `"think": false` in the request body. Without it the gate returns nothing. This is the single most important implementation detail. |
| `gemma4:e2b` confidence field | Always returns `0.95` regardless of input — uncalibrated | Do **not** threshold on model-reported confidence. Tune strictness via the **prompt**, not a numeric cutoff. |
| Ollama cold model load | ~67 s first load, ~0.7 s warm | Send a warmup request at startup and set `keep_alive: "30m"` on every call. |
| GPU | RTX 4080, 16 GB VRAM, driver 610.74 | faster-whisper `large-v3-turbo` fp16 (~1.5 GB) + gemma4:e2b (7.2 GB) fit together comfortably. |
| Python | 3.11.9 via `py -3.11`. The `python` on PATH is a **hermes venv — do not install into it**. | Create a dedicated `.venv` in the project. |
| Installed Ollama models | `gemma4:e2b`, `gemma4:e4b`, `gemma4:26b`, `gemma4:31b` | Default to `e2b`; make it configurable. |
| Audio devices present | Logitech C930e mic, NVIDIA Broadcast, USB Advanced Audio, DualSense headset, several speaker endpoints | Device must be selectable by substring match, not hardcoded index. |

---

## Architecture

Five stages connected by bounded `asyncio` queues. Every stage drops-oldest on overflow rather
than blocking, so audio capture can never stall.

```
 mic ────┐
         ├─▶ capture ─▶ segmenter ─▶ STT ─▶ gate ─▶ answerer ─▶ UI pane
 loopback┘   (WASAPI)   (silero VAD) (whisper) (heuristic   (claude -p
                                               + gemma4)     pool)
```

### 1. `audio.py` — capture

- Use **`pyaudiowpatch`** (PyAudio fork with WASAPI loopback support) for *both* sources.
- Two independent capture threads:
  - **mic**: default input device, or config substring match.
  - **loopback**: the WASAPI loopback endpoint of the default *output* device
    (`get_default_wasapi_loopback()` / iterate `get_loopback_device_info_generator()`).
- Each thread pushes `(channel, pcm_float32_16k_mono, timestamp)` frames of 20–30 ms.
- Resample to 16 kHz mono with **`soxr`**. Downmix multichannel by averaging.
- **A blank `output_device` opens EVERY loopback endpoint**, not just the Windows default. Which
  endpoint a call renders to is not knowable in advance and changes between sessions. A
  `LoopbackArbiter` forwards frames from only the endpoint currently carrying speech, since one
  segmenter cannot be fed two interleaved conversations. Handover is immediate once the incumbent
  has been quiet for ~1.5 s; the hold only stops another endpoint stealing a live utterance.
  The arbiter applies to loopback endpoints **only** — passing it to the mic thread mutes the
  microphone outright, because the mic's index can never win a contest it was never entered in.
- If `output_device` names no existing loopback endpoint, fall back to the current default output
  and warn. Dropping to mic-only here loses the side of the conversation worth answering.
- If no loopback endpoint can be opened at all, log a warning and continue **mic-only** — never crash.
- Track per-source RMS. A source that has been open but below the noise floor for
  `silent_source_warn_s` reads `SILENT <n>s ⚠` in the status bar, not `on`. A loopback pinned to an
  idle endpoint opens without error and captures nothing; without this the status bar reads `on`
  for the entire session while the other speaker is never heard. This is a **measured** failure —
  it cost a full 40-minute interview.
- `scripts/list_devices.py` prints all input/loopback devices with indices and names.

### 2. `segmenter.py` — utterance segmentation

- **`silero-vad`** (ONNX via `onnxruntime`, CPU — it is tiny and must not contend with the GPU).
- One independent segmenter instance per channel.
- Maintain a pre-roll ring buffer of ~300 ms so utterance onsets are not clipped.
- Emit an utterance when: speech detected, then **≥ 900 ms of trailing silence** (`silence_ms`).
- Force-flush any utterance exceeding **20 s** (`max_utterance_s`) so long monologues still surface.
- Discard segments shorter than **400 ms** (`min_utterance_ms`) — these are coughs/clicks.

### 3. `stt.py` — transcription

- **`faster-whisper`**, model `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`.
- Fall back to `device="cpu", compute_type="int8"` if CUDA init raises; log the fallback loudly
  in the UI status bar so the user knows why it is slow.
- Single worker (one GPU), processing utterances serially from a bounded queue.
- `condition_on_previous_text=False` — prevents hallucination loops on ambient noise.
- Keep Whisper's punctuation. **The trailing `?` is a strong gate signal — do not strip it.**
- Drop transcripts that are empty, pure punctuation, or match a configurable
  hallucination blocklist (Whisper emits "Thank you.", "Thanks for watching!", subtitle credits
  on silence — include a default list of these).

### 4. `gate.py` — question detection (the core quality requirement)

**Stage 0 — channel policy.** `gate.channel_policy` (default `{mic = "explicit", sys = "full"}`)
sets how freely each channel is judged. Everything is transcribed, shown, and added to context
regardless; this decides only what may become an answer.

- `full` — heuristics plus the semantic gate.
- `explicit` — Stage A's interrogative fast-accept, **or** the semantic gate but only if the text
  ends in `?`. A declarative sentence is rejected before the semantic gate sees it.
- `off` — never answered; short-circuits before any heuristic.

This is a **two-sided** constraint and both sides were measured:

- The wearer already knows what they are saying, and the semantic gate is asked to *rewrite speech
  as a query*, so it obliges for statements: `"...so I built a RAG system where"` → *"What is a RAG
  system?"*, ~30 unasked cards in 40 minutes.
- But blocking the channel entirely swallows real asks: `"Okay, what do you mean, how, how do I
  truncate it?"` produced nothing and had to be force-answered by hand.

Replay over 690 recorded mic utterances: 221 answered under an unrestricted gate, 66 under
`explicit` (38 via free Stage A, 28 via the semantic gate). 662 never reach Ollama.

Corollary: the `_pending_system` echo hold is **skipped** unless `mic` is `full`. That hold exists
so a mic copy wins the echo contest, and it costs ~2.5 s on every question from the other speaker.
Under `explicit` the mic copy is either accepted identically or dropped, and near-duplicate dedupe
covers the overlap — so the delay buys nothing.

**Gating runs OFF the ordered consumer path**, bounded by `gate.max_concurrent` (default 3).
Awaiting the ~900 ms Ollama call inline stalls every later transcript behind it, so a question
arriving mid-call could not reach the answer queue until the previous one had been judged — its
answer started a full gate late while the answerer sat idle. Only two things stay ordered: adding
to the context window, and snapshotting the gate/answer context each question is judged against.
Snapshot on the ordered path, never inside the task, or a question gets judged against a
conversation that moved on without it.

Two further stages. Stage A is free and rejects most speech; only survivors reach stage B.

**Stage A — local heuristics (~0 ms), pure functions, unit-tested:**

Reject outright:
- fewer than `min_words` (default 3) real words
- filler-only content (`uh`, `um`, `hmm`, `yeah`, `okay`, `right`)
- tag/rhetorical endings: `right?`, `you know?`, `isn't it?`, `innit?`, `yeah?`, `okay?`,
  `know what I mean?`, `am I right?`
- second-person imperatives aimed at a human: leading vocative
  (`hey <Name>`, `<Name>, can you ...`) — detect a capitalised token in vocative position
- near-duplicate of a question answered in the last `dedupe_window_s` (default 300 s),
  using a normalised token-set ratio ≥ 0.85

Fast-accept (skip stage B, saves ~530 ms):
- ends with `?` **and** starts with an interrogative
  (`what/why/how/when/where/who/which/whose/can/could/would/should/do/does/did/is/are/was/were/will/have/has/am/may/might/shall`)

Everything else → stage B.

**Stage B — local LLM (`gemma4:e2b` via Ollama HTTP, ~530 ms):**

- `POST http://127.0.0.1:11434/api/chat`
- Body **must** include `"think": false`, `"stream": false`, `"keep_alive": "30m"`,
  `"options": {"temperature": 0, "num_predict": 64}`, and a JSON schema in `"format"`.
- Send the last `context_turns` (default 6) transcript lines as context so referential questions
  ("what about the second one?") resolve correctly.
- Ask the model to return `{"q": bool, "query": "<self-contained rewrite>"}`. The rewrite is what
  gets sent to `claude` — it must inline the referent from context.
- **Ignore any confidence value** the model returns (uncalibrated — see verified facts).
- Strictness is set by swapping the prompt's TRUE/FALSE definition, selected by
  `mode = strict | balanced | eager` in config. Ship all three prompt variants.
  **Default `balanced`:**
  - TRUE: direct questions; clear implicit information requests
    (`I wonder what X is`, `remind me how Y works`, `no idea what the syntax is`)
  - FALSE: rhetorical questions, filler, self-narration, commands to other people,
    incomplete fragments, questions clearly aimed at another human
- On Ollama connection failure: fall back to heuristics-only, surface a warning in the status
  bar, and keep running.

**Cross-channel echo suppression:** if mic and loopback produce near-identical transcripts within
2 s, keep only the mic one (the speakers are re-capturing the room, or vice versa).

### 5. `answer.py` — answering

- **One-shot `claude -p` per confirmed question** (never a persistent session — see verified facts).
- Command:
  ```
  claude -p <query> --model <config.answer_model> --system-prompt <terse persona>
         --allowed-tools "" --strict-mcp-config --mcp-config {"mcpServers":{}}
         --output-format stream-json --include-partial-messages --verbose
  ```
- Read the one-shot process's JSON events line by line and display assistant text deltas
  incrementally. This is output streaming only: never add `--input-format stream-json`,
  reuse a process, or keep a process warm. `answer.stream = false` restores buffered stdout.
- Default `answer_model = claude-sonnet-5`; configurable.
- System prompt: terse, no preamble, answer in ≤ `max_words`, say "not sure" rather
  than guessing. (During benchmarking a model confidently gave a **wrong** answer about Node's
  fetch timeout — explicitly instruct against confident guessing.)
- **`answer.web_lookup`** (`off` | `auto` | `always`, default `auto`). Instructing the model to
  hedge is not sufficient for facts that changed after training: asked what Vertex AI is called
  now, it stated it had not been renamed, when it had become the Gemini Enterprise Agent Platform.
  `auto` grants `--allowed-tools WebSearch` **and** an explicit search directive for questions about
  names, versions, pricing or availability only — measured at 0.5% of 569 recorded questions.
  Two measured facts drive this design: permitting the tool without demanding a search left the
  model answering from memory (3.6 s, still wrong), and a real lookup costs 15–17 s against 3.5 s,
  which is unusable as a blanket policy. Record the flag in the JSONL to explain latency outliers.
- `answer.style` selects the shape. Default **`cue`**: one sentence sayable verbatim, blank line,
  then 2–3 `•` keyword fragments of ≤6 words. The binding constraint is that the reader is
  *already speaking* and gets one glance — prose measured at 60–90 words in live sessions was
  unreadable in that moment regardless of how well written it was. `interview` and `terse` remain
  for reviewing after the fact.
- Prepend the recent transcript as context, clearly delimited and labelled as background.
- **`asyncio.Semaphore(max_concurrent)`** (default 4) so a talkative stretch cannot spawn
  unbounded processes. Questions are answered **in parallel** — measured at 6.0 s wall clock for
  four questions against 22.2 s serialised (3.7×), all four in flight.
- Per-call timeout `answer_timeout_s` (default 45) → kill process, mark the card `timed out`.
- Answers may arrive out of order; each answer card is bound to its question's id, not appended
  blindly.

### 6. `ui.py` — the live pane

**Textual** app, read-only, never prompts.

- Scrolling feed, newest at bottom, auto-follow unless the user has scrolled up.
- Two card types:
  - **transcript line** — dim, `HH:MM:SS  [mic|sys]  text`
  - **Q&A card** — question in accent colour, then `answering…` spinner, replaced by the answer
- Status bar: listening indicator, mic + loopback state, whisper device (`cuda`/`cpu`),
  gate mode, queue depths, answers-in-flight, session token/answer count.
- Keys: `p` pause/resume listening · `c` clear feed · `t` toggle raw transcript lines ·
  `a` force-answer the last utterance (override the gate) · `s` cycle strict/balanced/eager ·
  `q` quit.
- Everything also appended to `logs/session-<ts>.jsonl` — one record per utterance with
  channel, text, gate decision, reason, answer, latencies.

---

## Config

`config.toml` at project root, loaded at startup, with every value above as a key. Ship a
fully-commented default. Structure: `[audio] [stt] [gate] [merge] [answer] [ui]`.

---

## Deliverables

```
Q&A/
  SPEC.md  README.md  requirements.txt  config.toml  setup.ps1
  ambientqa/  __init__.py __main__.py config.py audio.py segmenter.py
              stt.py gate.py answer.py context.py ui.py bus.py logging_.py
  scripts/list_devices.py
  tests/  test_gate_heuristics.py test_segmenter.py test_context.py test_config.py
```

- `requirements.txt`: `pyaudiowpatch soxr numpy silero-vad onnxruntime faster-whisper textual tomli`
  plus `nvidia-cublas-cu12 nvidia-cudnn-cu12` for CUDA.
- `setup.ps1`: creates `.venv` with **`py -3.11`** (not the PATH python — that is a hermes venv),
  installs requirements, warms the Ollama model, prints the device list.
- `README.md`: quickstart, config reference, troubleshooting (no loopback device, CUDA fallback,
  Ollama not running, empty gate responses → `think:false`).
- Run with `python -m ambientqa`.

## Non-negotiables

1. `"think": false` on every Ollama call.
2. No persistent `claude` stream-json session — one-shot per question only.
3. No `claude` call in the gate path.
4. Capture threads must never block on a full queue.
5. Any missing dependency (loopback device, CUDA, Ollama) degrades gracefully with a status-bar
   warning; it never crashes the app.

## Acceptance

- `pytest` passes; heuristic gate tests cover every reject/fast-accept rule above.
- App starts, shows `listening`, transcribes both channels, and leaves non-questions unanswered
  while answering real ones.
- Long monologue containing one embedded question → exactly one answer card.
