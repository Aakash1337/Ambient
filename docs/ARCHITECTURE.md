# Ambient — Architecture Deep Dive

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
- [5. audio.py and backends/ — capture](#5-audiopy-and-backends)
- [6. segmenter.py — utterance boundaries](#6-segmenterpy)
- [7. stt.py — transcription](#7-sttpy)
- [8. continuity.py — fragment merging](#8-continuitypy)
- [9. context.py — shared transcript memory](#9-contextpy)
- [10. gate.py — question detection](#10-gatepy)
- [11. answer.py — answering](#11-answerpy)
- [12. The second pass — audit and sweep](#12-the-second-pass--audit-and-sweep)
- [13. ui.py — the live pane](#13-uipy)
- [14. __main__.py — the controller and runtime traps](#14-mainpy--the-controller-and-runtime-traps)
- [15. Config, profiles, logging](#15-config-profiles-logging)
- [16. The empirical foundation](#16-the-empirical-foundation)

---

## 1. What this system actually is

An always-on listener for **live conversations the user is a participant in** — originally
and primarily technical job interviews. It hears both sides (user's mic + the other
speaker via system-audio loopback), transcribes continuously, decides which utterances are
questions actually worth answering, and displays answers in a read-only terminal pane.
Windows, Linux, and macOS are first-class: one codebase, one pipeline, with everything
platform-specific behind the capture backend contract in §5.

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

Hanging off the right edge of this diagram, and deliberately absent from it, is an
optional **second pass** (§12): an audit task that re-reads each delivered answer with
wider context, and a sweeper that re-judges recent gate rejections. The costly per-answer
audit defaults off; the batched recovery sweep defaults on. Both are asynchronous and
best-effort, and neither blocks the live path.

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

**1. OS threads (capture only).** The backend's blocking `SourceStream.read()` lives in
dedicated daemon threads — one per open device stream (one mic + potentially many
loopback endpoints). On Windows/macOS that read blocks in PortAudio; on Linux it blocks on a
`parec` child's stdout — the thread neither knows nor cares (§5). Threads never touch
asyncio objects directly; they hand frames across via
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
wall clock, peak concurrency 4/4. The second pass (§12) follows the same pattern one
step further out: audit tasks (at most one at a time) and the periodic sweep worker are
additional detached tasks whose every failure path is swallowed — they can improve the
record after the fact but structurally cannot disturb it.

The subtle invariant making detachment safe: context snapshots are taken *on the ordered
path before detaching*. A gate task that runs late still judges its utterance against
the conversation as it stood when the words were spoken, not whenever the task got
scheduled. Answers can complete out of order; each answer card is bound to its
question's utterance id, never to "the latest card".

There is also a `.stop` asyncio.Event for shutdown; every worker loop polls its queue
with a short timeout so it can notice `stop` within 250 ms. On shutdown the controller
cancels all tasks (including gate, audit and UI tasks), then writes terminal JSONL
records for anything in flight (`answer_status: "cancelled"`, plus
`shutdown_before_echo_window` / `shutdown_before_merge_window` for held transcripts), so
the log never ends with a dangling accepted question.

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

## 5. audio.py and backends/

Capture is split in two: `ambientqa/backends/` owns everything platform-specific
(device enumeration, opening streams, unblocking readers), and `audio.py` is the
platform-neutral orchestrator (threads, the loopback arbiter, lifecycle, health
tripwires). The split exists because the Windows-only stack (pyaudiowpatch) used to be
welded directly into `audio.py`; Linux support forced the seam, and the seam turned out
to fix a whole crash class along the way.

### The contract (backends/base.py)

Three protocols, deliberately tiny:

- **`CaptureDevice`** — a backend-stable id (stringified PortAudio index on Windows/macOS,
  PipeWire source name on Linux; opaque above the backend), a human name, a
  `mic`/`loopback` kind, and the native format. What `read()` actually delivers is
  described by the opened stream, which may differ — parec resamples in-process.
- **`SourceStream`** — `read(frames)` blocks until float32 frames arrive and *raises* on
  device failure or end-of-stream (that is how a dead device surfaces to the
  per-candidate fallback). `stop()` unblocks any reader wedged in `read()`, from any
  thread, idempotently — and **must run before `close()`**. Closing under a live reader
  is the native use-after-free class of crash this contract exists to prevent: PortAudio
  forbids `close()`/`terminate()` while another thread sits in `Pa_ReadStream`.
- **`BackendSession`** — holds whatever per-run resource the platform needs (a PyAudio
  instance on Windows; lightweight factories on Linux/macOS) and produces mic/loopback candidate lists,
  best-first. A pinned mic substring that matches nothing raises — guessing a microphone
  records the wrong room. A pinned *loopback* that matches nothing warns and falls back
  to the default instead: raising means mic-only, which silently loses the other half of
  the conversation — exactly the half worth answering.

`get_backend()` selects by `sys.platform` when `[audio] backend = "auto"` (the
default); `"wasapi"`, `"pipewire"`, and `"coreaudio"` can be forced for diagnostics.
Concrete backends are imported lazily so importing `ambientqa` never requires a
platform audio package on the wrong OS. `requirements.txt` gates pyaudiowpatch to
Windows and sounddevice to macOS; Linux involves no PortAudio at all.

### Windows — WASAPI via pyaudiowpatch (backends/windows.py)

Moved intact from the old `audio.py`. pyaudiowpatch is a PyAudio fork exposing loopback
endpoints — Windows renders audio per *output* device, and each output has a matching
loopback capture endpoint named like `Speakers (...) [Loopback]`.

**Mic:** find candidates matching the configured substring (preferring the WASAPI host
API duplicate — every physical device appears once per host API: MME, DirectSound,
WASAPI, and only WASAPI is trustworthy here). With no pinned name, the default input
comes first and every other WASAPI input is a fallback tried in order until one opens.
Streams open in shared mode (no host-API stream-info supplied) so capture coexists with
NVIDIA Broadcast and everything else.

**Loopback — the part that cost a whole interview [measured]:** a loopback stream on an
endpoint that nothing is playing through opens *without error* and delivers silence
forever. There is no API-level way to distinguish "healthy but idle" from "wrong
endpoint". A session was recorded with `output_device` pinned to the desktop speakers
while the call played through the headset: status bar said `sys:on` for 40 minutes and
the interviewer was never heard. Nothing errored. The defences (open every endpoint, the
arbiter, the SILENT tripwire) live in the orchestrator below because the failure shape
is not Windows-specific — a PipeWire monitor of a sink nothing plays through is exactly
as silently deaf.

### Linux — PipeWire via pactl/parec (backends/linux.py)

**Enumeration** is `pactl --format=json list sources`. Monitor sources — those with
`properties["device.class"] == "monitor"`, described as "Monitor of <sink>" — are
PipeWire's native equivalent of WASAPI loopback: one exists per output device and
carries whatever the machine plays through it. The `sys` channel therefore works on
Linux with no driver tricks at all. Pinned names match against *both* the human
description and the PipeWire source name, so a config written on Windows (human label)
and a Linux-native pin (`alsa_input....`) both resolve.

**Capture is one `parec` subprocess per stream**, not a PortAudio stream:

```
parec --device=<source> --format=float32le --rate=16000 --channels=1 --raw \
      --latency-msec=<frame_ms>
```

Three deliberate properties:

- **parec converts in-process.** It delivers the pipeline's native 16 kHz mono float32
  directly, so the orchestrator's soxr resampler and downmix are naturally skipped for
  every Linux stream (soxr is only even imported when a stream arrives off-rate).
- **`--latency-msec` is required, not an optimisation [measured].** With the
  server-default buffering, the first byte arrived after ~2 s and audio then came in
  ~1 s bursts — useless for live segmentation. Requesting the pipeline's own frame
  cadence (25 ms) delivers the first frame in ~50 ms and one frame per read.
- **stderr goes to an anonymous temp file, never a pipe.** Nothing drains stderr while
  audio streams, so a chatty parec (`PULSE_LOG` set, repeated client warnings) would
  fill the 64 KiB pipe, block writing, and silently stop producing audio — a deaf
  channel with no error, the exact failure class §5 exists to kill. A file cannot
  backpressure the child, and its tail stays readable for the EOF diagnostic.

The subprocess boundary is what makes shutdown *structurally* correct rather than
carefully correct: `stop()` terminates the child, its stdout hits EOF, and any reader
blocked in `read()` returns immediately — no cooperation needed from a wedged thread.
Symmetrically, a crashing capture process cannot take the app down; the reader surfaces
EOF (with the stderr tail as the reason) as an ordinary stream error the orchestrator
already routes around.

PipeWire multiplexes every source natively, so devices are never "busy" and streams do
not conflict with other applications. Multiple full app processes are nevertheless unsafe:
they duplicate capture, Whisper, and paid answer work. Startup therefore holds a shared
process-lifetime OS lock (with heartbeats retained for status and legacy detection) and
refuses a second pipeline by default; `--allow-multiple` is diagnostic.

### macOS — CoreAudio via sounddevice (backends/macos.py)

`sounddevice` enumerates only input-capable devices on the Core Audio host API and opens
blocking native-rate float32 `RawInputStream`s. The default physical input is tried first;
`abort()` is the cross-thread unblock used before `close()`. CTranslate2's macOS wheels are
CPU-only, so a shared `stt.device = "cuda"` configuration is translated to CPU `int8` before
model construction rather than failing once on every launch.

CoreAudio does not expose physical speaker outputs as recordable inputs. System audio comes
from a virtual input: BlackHole, Soundflower, Loopback Audio, Background Music, and VB-Audio
names are recognized automatically, while an explicitly pinned output name can select any
other CoreAudio input. With no virtual loopback the `sys` channel reports an actionable error
and the established orchestrator path continues mic-only. BlackHole 2ch plus a Multi-Output
Device is the documented setup, so the user can hear and capture the same call audio.

### The orchestrator (audio.py)

Per-channel candidate handling: the mic opens the first candidate that works; loopback
opens **every** endpoint when `output_device` is blank (which output a call renders to
is unknowable in advance and changes between sessions — headset today, speakers
tomorrow). One dead endpoint among many is normal (virtual devices refuse to open) and
only a total failure is reported.

**`LoopbackArbiter`** — all endpoints feed the single `sys` channel, but one segmenter
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

**Lifecycle — the thread-zombie problem.** `start()` and `stop()` run on executor
threads and their callers cannot guarantee ordering: cancelling the device-picker worker
abandons a `stop()` that keeps running while the recovery path immediately calls
`start()`. Two mechanisms make this safe:

- A **lifecycle lock** held for the whole of either call turns overlapping start/stop
  into strict sequences. Unserialised, the abandoned stop closed the *new* session's
  streams under their readers, and its generation bump landed after the new start's,
  deadening the fresh session's accounting. (`stop()` sets the stop event *before*
  taking the lock, so a stop racing a long start begins winding the new session down the
  moment start releases it.)
- A **generation token**, bumped by every start and stop, is carried by each capture
  thread. A thread that outlives stop's 2 s join timeout would otherwise error out of
  its stale stream *after* the next start, decrement the new session's runner count, and
  falsely mark a healthy channel dead with a stale error — or, worse, resume pushing its
  stale device's frames into the live session's queue once the next start cleared the
  stop flag. Stale-generation threads stay silent and die.

Stop ordering is fixed and load-bearing: **stop every stream → join threads → close
streams → invalidate generation → close session**. Streams are stopped first because a
thread wedged in a blocking `read()` never observes the stop flag on its own, and
closing (or tearing down the backend) under a live reader is the native crash the
contract forbids. A read error observed after stop *is* the shutdown, not a device
failure, and is logged at debug — warning about it would cry wolf on every restart.

**Silence tripwire:** each `SourceState` tracks `last_signal_at` (RMS measured
pre-resample, in float64 — **[measured]** some host APIs deliver frames that are not
valid float32 and squaring them in float32 overflows to `inf`, poisoning every later
comparison). A source open but inaudible for `silent_source_warn_s` (45 s) is displayed
as `sys:SILENT 60s ⚠` instead of `on`. "Off" and "open but deaf" are different states
and the UI must not conflate them. Since multiple threads share one `SourceState` when N
endpoints feed `sys`, active/inactive transitions are reference-counted
(`_enter_source`/`_exit_source`) — otherwise the first endpoint thread to die would mark
the whole channel dead while five others still ran.

### run.sh and the ec_mic chain

Linux launches through `run.sh` (Windows stays `python -m ambientqa` in `.venv`), which
does five things:

- **Bootstraps `.venv-linux`**, gated on a `.deps-installed` stamp written only *after*
  pip succeeds, and re-runs pip when `requirements.txt` is newer than the stamp. Gating
  on `bin/python` alone mistakes an interrupted install for a finished one: venv
  creation makes that file first, so a Ctrl-C during the multi-hundred-MB wheel
  downloads would leave a dependency-less venv that every later run execs into a
  `ModuleNotFoundError`.
- **Optionally runs the pre-launch Textual mode picker** when the desktop entry passes
  `--choose`. The picker imports no audio/model/controller code and returns Assist, Voice,
  Web Assist, Web Voice, Emergency, or Cancel before any pipeline exists. Those four
  current-build roles map to ordinary launch arguments; Emergency requires an in-picker
  confirmation and execs the still-independent pinned fallback with `--takeover`. Direct
  `run.sh` / `run.sh --voice` behavior is unchanged.
- **Loads PipeWire's `module-echo-cancel`** (WebRTC: noise suppression + automatic gain
  control — the class of processing Windows applies in its own audio stack) exposing an
  `ec_mic` source, which `config.toml` pins as `mic_device`. This exists because of a
  clipping incident **[measured]**: the raw USB mic at 100% PipeWire volume sits ~34 dB
  above its hardware-neutral level — loud speech clipped 1.7% of samples and quiet
  rooms drowned in boosted noise, both of which garbled Whisper. If the module fails to
  load, the pinned mic reports unavailable and `d` picks the raw device.
- **Brings up Ollama** quietly if `/api/version` doesn't answer.
- **Exports `LD_LIBRARY_PATH`** pointing CTranslate2 at the pip-installed CUDA
  libraries (the Linux counterpart of the Windows DLL-path trap in §7), then execs
  `python -m ambientqa`.

macOS uses `setup-macos.sh` and `run-macos.sh` with a separate `.venv-macos`. The
launcher loads `config.macos.toml`, a small overlay that inherits shared tuning from
`config.toml` while keeping Mac device choices and CPU STT separate. It omits Linux
PipeWire/CUDA setup, starts Ollama when available, downloads Kokoro models for `--voice`,
and plays synthesized PCM through a CoreAudio RawOutputStream.

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

**faster-whisper** (CTranslate2), model `large-v3-turbo`, one worker. Windows/Linux use
CUDA fp16; macOS uses CPU int8 because CTranslate2 has native Mac wheels but no Metal/MPS
device. Utterances are transcribed serially off a bounded queue; parallel STT would either
thrash VRAM or contend for the same CPU cores.

Windows CUDA-specific traps, both **[measured]** and both handled in
`register_cuda_dll_dirs()` (a no-op off Windows — Linux gets the equivalent via
`run.sh`'s `LD_LIBRARY_PATH`, while macOS deliberately uses CPU, §5):

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
- A blocklist drops classic silence hallucinations — matched **exactly** against the
  normalised *whole* transcript, never by prefix. Prefix matching ate real speech
  **[measured]**: interviewers routinely open with a courtesy — "Thank you. So, tell me
  about your experience with Kubernetes." — and the entire question vanished here,
  before gating, with no log record at all. Every entry is therefore the full utterance
  Whisper invents ("Thank you very much", "Subtitles by the Amara.org community", …).
- Whisper's punctuation is preserved **exactly** — the trailing `?` is the single
  strongest gate signal (it encodes rising intonation, which survives disfluency).
- Profiles (see §15) inject `hotwords` (vocabulary spelling bias) and a ≤120-char
  `initial_prompt` (domain context, kept short to avoid prompt-echo hallucinations).

## 8. continuity.py

People pause mid-sentence for longer than feels intuitive, and the VAD splits there.
`ContinuityMerger` coalesces probable fragments *before* gating so "how do you manage
context in …(pause)… Amazon Bedrock" is judged as one thought.

An utterance is **held** (not gated yet) if it looks *unfinished* — `is_open_utterance`.
The very first test is terminal punctuation: a `?` or `!` **closes the thought no matter
what the last word is**. English questions legitimately strand a preposition ("What are
you working on?"), and treating those as open parked a complete question in the merge
window for the whole hold — or glued it onto the interviewer's *next* sentence,
destroying the `?` fast-accept downstream. A `.` earns no such trust: Whisper invents a
period at every VAD boundary, so "so tell me about." must stay open. Past that test, an
utterance is open if it ends in a function word that cannot end a sentence
(preposition/article/conjunction — a curated list; see the trap below), ends with a
comma or dash, or lacks terminal punctuation. A held fragment merges with the next
same-channel utterance if the *audio gap* is ≤ `merge_gap_s` (6.5 s) and either the held
text is open or the new text starts like a continuation (leading conjunction or
lowercase letter). Joins strip Whisper's invented boundary punctuation on both sides of
the seam — the period *and* the trailing-off ellipsis it sometimes emits instead.

A terse, complete command-form request (at most six words) is also closed without terminal
punctuation. This narrow exception sends `EXPLAIN RAG` straight to gating while leaving
unfinished `Tell me` / `Talk about` fragments—and long merged setups—inside continuity.

Two safety rails: a **wall-clock deadline** `merge_window_s` (13 s — must exceed the gap
*plus* the continuation's spoken length *plus* its STT latency, or merging silently
never happens **[measured]**), and caps (`max_merge_parts` 5, `max_merge_s` 25) so noisy
speech cannot merge forever. Complete questions are never held — zero added latency in
the common case. The 6.5/13 values are themselves a correction **[measured]**: at the
original 4.5/9.0, a trailed-off premise ("So if the content you're searching keeps
changing…") followed ~5 s later by its question stayed unmerged, so the gate saw only
the second half. A think-pause between a setup and its question is routinely ~5 s.

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
question* — Stage A's fast-accepts (free, instant), or anything Whisper heard ending in
`?` (rising intonation survives disfluency; a bare "now" or restarts do not defeat it).
Declaratives are rejected *before* the semantic gate can see them, so they cannot be
rewritten. Replayed over all 690 recorded mic utterances: 221 answered under `full`, 66
under `explicit` (38 instantly via Stage A, 28 via the semantic gate on the `?` signal),
662 never reach Ollama at all.

One carve-out softens the hard edge — **`reask_of_recent`**. A statement on an
`explicit` channel that overlaps a question answered moments ago (token-set ratio ≥ 0.5
against anything answered within the last 90 s) is the user *re-asking*: the first
answer missed — a mishearing, the wrong angle — and retries rarely carry fresh question
intonation ("no, prompt engineering…"). It is accepted **outright**, not sent to the
semantic gate: a retry is usually phrased as a correction or a plan, exactly what the
gate prompt is trained to call narration and reject. The answerer sees the prior
exchange through its Q&A history (§11) and is the only judge that can actually resolve
what changed. The ratio is deliberately far below `dedupe_ratio` — the *correction* is
the part that differs. Statements past both tests reject as `not_a_direct_question`.

### Stage A — deterministic heuristics (~0 ms, pure functions, unit-tested)

Ordered; first match wins:

1. `too_few_words` — under 3 real words, except a complete command-form request with an
   object (`Explain RAG`). Incomplete two-word forms still reject.
2. `filler_only` — every token in {uh, um, hmm, yeah, okay, right…}.
3. `near_duplicate` — token-set ratio ≥ 0.85, but **time-scoped**: only questions
   answered within `gate.reask_cooldown_s` (8 s) count as duplicates. Mechanical dupes
   (a Whisper re-emission, a merge artifact) arrive within ~5 s; past the cooldown an
   almost-identical question is a human deliberately re-asking and gets a fresh answer.
   This rule must stay *ahead* of the fast-accept: the fast-accept is deliberately
   exempt from answer-echo suppression but not from verbatim re-ask dedupe.
4. `tag_or_rhetorical` — via `_is_tag_question`, and the predicate matters. The tag
   words themselves ("right?", "okay?", "am I right?") legitimately end genuine
   interrogatives — "Is my understanding of the GIL right?" — so a `TAG_PATTERN` match
   alone is not enough. It counts only when what precedes the tag is a finished
   statement set off by a comma ("Should we deploy on Friday, okay?") or nothing of
   substance at all ("Am I right?" — pure function words). Judged *before* the
   fast-accept because a pure tag is interrogative-shaped and would sail through it,
   yet answering the interviewer's rhetorical check-in is exactly the noise this rule
   exists to stop.
5. **Fast-accept** `explicit_interrogative` — ends with `?` AND (after skipping leading
   `QUESTION_PREFIXES`) starts with an interrogative (what/why/how/…/can/could/is/do…).
   The prefix list covers discourse markers *and* acknowledgment lead-ins
   (so/well/okay/ok/then/and/but plus great/alright/right/sure/now/yes/yeah/cool/
   perfect/good/nice/fine) — interviewers habitually attach one before the actual question
   ("Great, could you walk me through your project?"). Guarded by `not _is_vocative`:
   names that casefold into interrogatives must not slip through — "Will, can you
   review this?" is addressed to Will, not the assistant. This rule must run **before**
   the vocative reject (which keys on the first token — "Okay, can you explain the CAP
   theorem?" would otherwise be rejected without the question ever being read) and
   before the content-word check ("How are you?" is almost all stopwords).
6. **Accept** `imperative_request` — command-form asks. "Talk about evaluation
   metrics." carries no `?` and no interrogative, so on an `explicit` channel it was
   structurally unanswerable — yet interviews *open* with imperatives ("Tell me about
   yourself."), and a deliberate re-articulation ("Evaluation metrics. Talk about
   them.") must not die the same death twice. A sentence beginning with a
   `REQUEST_VERBS` entry (explain/describe/talk/tell/walk/give/…, after stripping
   prefixes and "please") is a direct ask; a request bigram ("talk about", "tell me",
   "walk me") anywhere in the text is the fallback for punctuation-less transcriptions
   where Whisper dropped the sentence boundary. Because this accept skips the semantic
   gate entirely, it is fenced by four guards: everyday idioms sharing the shape but
   requesting nothing ("tell me about it", "give me a second") are exempted by exact
   match; plan markers (I/we/'ll/gonna/later…) disqualify the bigram path — "we'll talk
   about that later" asks nothing; a vocative wins — "Sarah, tell me…" is aimed at
   Sarah; and a trailing fragment word disqualifies the whole thing — "So, tell me
   about" is a request *cut off* mid-sentence, and answering the stub would answer a
   question with no object (the merge layer holds it until the rest arrives). Generic
   honorifics are allowed only before a clear information command (`Sir, talk...`), not as
   general question prefixes; human-directed actions remain vocatives.
7. `human_vocative` — "Hey Sarah, can you…" (aimed at a person, not the assistant). The
   pattern covers both modal-you forms ("Sarah, can you…") and name-addressed
   imperatives ("Sarah, tell me…"). Whisper capitalizes every sentence start, so a
   leading discourse marker is indistinguishable from a name in vocative position —
   known `QUESTION_PREFIXES` are therefore never treated as names. See also the
   policy-dependent demotion below.
8. `trailing_fragment` — ends in a word that cannot end a sentence (see §8 trap).
9. `no_content_words` — fewer than 2 non-filler non-stopword tokens. Without this,
   "uh, um, so, the thing is" reaches the LLM, which invents a question from
   surrounding context. This rule is the single most important false-positive guard.

Everything surviving goes to Stage B — with two `evaluate()`-level wrinkles first:

- **Vocative demotion.** On a `full`-policy channel, a `human_vocative` reject is
  *demoted* to the semantic gate rather than upheld: a vocative there is usually the
  interviewer addressing the **candidate** by name — "Aakash, can you explain
  decorators?" is the exact question this tool exists to answer, and a hard reject
  would eat the core scenario. The semantic gate's prompt already returns FALSE for
  questions aimed at another human, so it is the right judge. On the mic channel the
  hard reject stands: the *user* hailing someone by name is definitionally talking to
  another human.
- **`answer_echo`** — the user reading a recent *answer* aloud (rehearsing it) must not
  be re-gated as a question. Checked just before the LLM call (saving the ~700 ms):
  one-directional containment — fraction of the utterance's content words appearing in
  a recent answer ≥ 0.5, unless the utterance carries a "need marker"
  (wonder/explain/confused/what/how…). One-directional because answers are much longer
  than utterances; symmetric similarity would never fire. Stage-A fast-accepts are
  deliberately exempt: if the user really asks a question, answer it even when it
  echoes a recent answer.

### Stage B — local LLM (gemma4:e2b via Ollama)

The model choice is itself a finding **[measured]**: the gate prompt was engineered
against gemma4:e2b, and the earlier stand-in (qwen2.5:3b) measurably *flips on real
questions when the transcript context block is present* — the context designed to
resolve referents talked it out of accepting. The prompt and model are a matched pair;
swap one and re-run `scripts/eval_gate.py`.

`POST /api/chat` with — every field the product of a measurement:

- **`"think": false`** — gemma4 is a reasoning model; without this it spends its token
  budget on thinking and returns **empty content** (`done_reason: "length"`). The
  single most important line in the integration. **[measured]**
- `"format": <JSON schema>` — forces `{"q": bool, "query": str}`. The code additionally
  requires `q is True` (the JSON boolean, not truthiness — a proxy returning the string
  `"false"` must not create a question) and a non-empty rewrite.
- `keep_alive: "30m"`, plus a warmup request at startup with a 90 s timeout —
  **[measured]** cold model load is ~67 s, warm calls ~0.5–1 s. Normal calls keep an 8 s
  timeout and *await the warmup task first* if it is still running. The warmup must be
  **cancellable**: run via `asyncio.to_thread`, `Task.cancel` cannot interrupt the
  running executor future, and `asyncio.run` joins the default executor on exit — so
  quitting during a cold Ollama load kept the process alive for up to the full 90 s
  after the UI was gone. The warmup therefore runs on a daemon thread that signals back
  into the loop: the await is cancellable, and a daemon cannot block interpreter exit.
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

**What the prompt carries** — four pieces of framing, each bought by a failure:

- **Q&A history.** Up to `answer.history_turns` (8) completed question/answer pairs,
  oldest first, answers clipped to 700 chars (the substance survives; time-to-first-token
  does not pay for code blocks). This — not the raw transcript — is what lets a
  follow-up like "elaborate on the second method" resolve: *the methods exist only in
  the answer prose*, which the transcript never contains. The prompt restricts its use
  to questions that actually refer back (an ordinal, a pronoun, an explicit callback);
  a self-contained question gets a fresh answer with no earlier topics dragged in.
  History is read at *answer* time, not enqueue time, so a follow-up asked seconds
  after an answer completes sees that answer even if it was queued first.
- **Setup-awareness.** Speakers routinely state a scenario, trail off, think, and only
  then ask — so the constraint that decides the answer often sits one transcript line
  *above* the question ("So if the content you're searching keeps changing…" → "Which
  method would you use?"). The prompt says so explicitly: when the question plausibly
  continues that setup, answer *as constrained by it*, not in the abstract. Ignoring it
  produces a generic answer to a specific question.
- **A channel stance.** `sys` questions get the coaching stance: the other speaker
  asked the *user*, so first person is the user's voice — that is what the cue card is
  for. `mic` questions get the opposite: the user may be talking to another person or
  another assistant *whose replies the transcript does not carry* (they arrive as
  silent text). Without a stance, the model inferred it WAS the addressee and answered
  in its voice, inventing that party's state — "No, I don't auto-launch" — which reads
  as authoritative and is pure fabrication. The mic stance forbids answering in first
  person as the addressee or inventing anything only the addressee could know; when
  only they could know, say so in the first line and give what general knowledge safely
  covers.
- **Profile subordination.** The standing profile (§15) pitches the answer's level, but
  the prompt marks it explicitly subordinate: **the recent transcript outranks it**.
  When the lines before the question establish what is being discussed, the answer
  stays in that thread — "What kind of system would I implement?" asked right after
  discussing speaker separation is about speaker separation, not about the profile's
  standing project.

**Streaming:** `--output-format stream-json --include-partial-messages` and the stdout
JSONL is parsed line by line; `text_delta` events stream into the UI card via a
callback keyed by question id. Full raw bytes are retained as a fallback — if the CLI's
event shape ever changes or emits malformed JSON, the answer degrades to the raw dump
rather than vanishing (but only when *nothing* was extracted: one stray non-JSON line
must not replace a fully assembled answer).

**Concurrency:** `asyncio.Semaphore(max_concurrent=4)`, per-call timeout 45 s → kill
the subprocess and mark the card `timed out`. In-flight count is surfaced in the status
bar. `AnswerResult.searched` is bound *before* the try block so every exit path —
timeout and CLI failure included — records whether a lookup ran; a timed-out web lookup
is precisely the log record that needs the explanation.

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
The runtime `agent` style is orthogonal to the knowledge profile: it produces one to
three short, direct spoken sentences, uses active dialogue history, and applies the
courtesy guard before final text is displayed or spoken. Raw Agent streaming deltas
are withheld because they have not passed that guard.

## 12. The second pass — audit and sweep

The live path optimises for latency, which means it judges and answers with narrow
context and a fast local gate — and sometimes gets it wrong in both directions: a
delivered answer that missed the real question, and a real question that never became an
answer. The second pass covers both, asymmetrically, because the two failures have
different shapes: a bad answer *exists* and can be re-read; a wrongly-rejected question
produced nothing and must be re-found.

The shared design stance: **both passes are best-effort and structurally unable to hurt
the live path.** `verify()` returns `None` on failure; `detect_missed()` returns `None`
and leaves its candidate batch queued for retry (a successful no-miss verdict is `[]`).
A broken auditor must never disturb an already-delivered answer, and a broken sweeper
must never disturb the pipeline. Both run as detached tasks, log failures, and do not
delay anything the user is waiting on.

### The audit (`[answer] verify = "always" | "off"`)

After each successfully delivered answer (and only then — the card is already resolved,
so the first answer's latency is untouched), an auditor re-reads it with strictly more
information than the first responder had:

- a **fresh, wider** transcript snapshot (`verify_context_turns`, 18 vs the answerer's
  6) — fresh on purpose, breaking the §3 snapshot rule, because catching context the
  fast path missed is the audit's entire job;
- the full Q&A history;
- the **raw transcription** of what the speaker literally said — the query the answer
  addressed may have inherited a mishearing that only the raw text reveals;
- the same channel stance as the original answer.

The bar is deliberately high, and stated in the persona: a correction lands ~8 s after
the user may already be speaking from the first card, so it is only worth the
distraction when the delivered answer *would have misled*. The auditor replies `OK`
unless the answer is materially wrong: it contradicts a constraint or scenario in the
transcript, it answers a mishearing, it is factually incorrect, it impersonates another
party or asserts that party's unknowable state, it sources its topic from the standing
profile when the transcript establishes a different referent, or it drops part of an
explicit enumeration. Style, phrasing, ordering and added depth are **never** grounds.

When a replacement comes back, three things update: the card re-resolves with status
`"revised"`, the Q&A-history entry is replaced (otherwise "elaborate on that" expands
the answer the audit just retracted), and a second JSONL record is appended for the
same utterance id with `gate_reason: "verify_revision"` / `answer_status: "revised"`.
Audits run at most one at a time (their own semaphore) and share the answerer's aggregate
Claude process semaphore with primary answers, under the same 45 s timeout.

### The sweep (`[answer] sweep = "always" | "off"`)

The audit only reviews answers that exist. Every `sweep_interval_s` (25 s) a sweeper
re-judges the recent **judgment-stage** rejections —
`not_a_direct_question` / `ollama_reject` / `ollama_unavailable` / `human_vocative`, the
reasons meaning "this reached judgment and got voted down". Mechanical rejections
(filler, dedupe, echo, tags, pause) are not misses and never enter the buffer (a deque of 24, cleared after a
successful sweep; nothing runs while paused). The vocative case is included because
Whisper's sentence-initial capitalization made "Again, describe RAG pipelines" look like
a request addressed to a person named Again; the sweep independently rejects genuine
human-directed speech.

The sweeper (`sweep_model`, default `claude-haiku-4-5` — it is a small classification,
so a fast cheap model is right; empty falls back to `answer_model`) sees the candidates,
wide transcript context, and the **answered/in-flight list**, with three standing
orders: never resurrect anything already answered or in flight, never invent an ask the
candidates do not contain, and when in doubt leave a candidate rejected. It returns at
most two `{index, question}` entries as strict JSON.

Genuine catches come back **through the normal answer path**: a recovered transcript
(id suffixed `-recovered`) gets an ordinary card and an ordinary answer job with
`gate_reason: "second_pass_recovery"` — so streaming, the JSONL record, and the audit
itself all apply to it. A late answer beats no answer; an inconsistent side-channel for
late answers would not.

## 13. ui.py

**Textual** app, read-only. Scrolling feed (`feed_direction = "top"` mounts the newest
entry first and pins the view to it; `"bottom"` appends like a chat log), auto-follow
unless the user scrolled away; dim transcript lines; `QACard` widgets (question in
accent, spinner → streaming answer). Keys: `p` pause, `c` clear, `t` toggle
transcripts, `l` browse past session logs in-app, `a` force-answer last utterance
(bypasses the gate entirely — the manual override for anything the gate missed), `s`
cycle gate mode, `x` profiles, `d` device picker with live meters, `q` quit.

Details worth knowing before touching it:

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
  finish out of order by design. A card can also resolve *twice*: once with the
  delivered answer and again with status `"revised"` when the audit (§12) replaces it —
  non-`ok` statuses are shown as the card's prefix.
- **Each picker action runs in its own exclusive worker group** (`devices`, `sessions`,
  `profiles`). In the shared default exclusive group they silently cancel each other —
  and cancelling the device worker while it is stopping/restarting capture strands the
  capture restart (the failure the §5 lifecycle lock then has to absorb; the group
  split keeps it from happening at all).

The device picker (`d`) deserves a mention: it stops capture, opens *every* endpoint
with live level meters, and the correct endpoint is self-evident — it is the one whose
meter moves when the other person talks. Selection is written back to `config.toml`.

### 13b. webui.py — the opt-in web console

`--web` swaps the Textual pane for a browser console served on `127.0.0.1` (default
port 8802, with automatic next-port fallback when unpinned). It is *not* a second UI layer inside the pipeline: `WebUIApp` duck-types
the same application interface the controller already calls (`add_transcript`,
`add_question`, `append_answer_delta`, `resolve_answer`, `notify`, `run_async`, …), and
the only seam in the main path is an optional `app_factory` argument on
`AmbientController` that defaults to the Textual app. The console is stdlib-only
(`ThreadingHTTPServer` + Server-Sent Events) so it adds nothing to `requirements.txt`
— run.sh's pip stamp, the emergency baseline, and the Windows path are all untouched.

Design points that mirror hard-won pipeline rules:

- **The gate-decision panel taps the session logger**, not a new reporting path: the
  logger's `append` is wrapped so every rejection (with its reason) that reaches the
  JSONL also reaches the browser. One record, two sinks, no drift.
- **The web status tick doubles as the instance heartbeat**, exactly like the TUI's
  status refresh — the emergency launcher's PID checks keep working under `--web`.
- **The device picker self-closes** (30 s ping deadline) because it stops main capture
  while open; a browser tab that navigated away must not leave the app deaf.
- **Web Voice is a launch-time combination**, not a second process or a runtime model
  bootstrap: the picker maps it to `--web --voice --open-browser`, while visible web
  controls call the same voice, Q&A/Agent interaction, and Normal/Conversational
  delivery controller methods as M/G/R.
- **Quit is acknowledged before tab closure**: the page only calls `window.close()`
  after the server confirms shutdown. If browser security blocks self-closing an
  externally opened tab, the page becomes a persistent stopped-state notice.
- **Streaming deltas carry a running length** so a page that connects mid-answer
  detects the gap and resyncs from the snapshot instead of showing spliced text.
- The server binds loopback only; the session-log endpoint whitelists
  `session-*.jsonl` names rather than joining paths.

`scripts/webui_demo.py` drives the real console against a scripted stub controller —
no audio, models, or Claude — for rehearsal and offline demo fallback.

The mode picker (`--choose`, the desktop shortcut's path) offers the console as a
fourth option, followed by a delivery choice: Web Assist maps exit code 40 to
`--web --open-browser`; Web Voice maps exit code 50 to
`--web --voice --open-browser`. Both remain one controller/capture pipeline. `--open-browser`
exists because an app-menu launch has no terminal showing the URL; the open runs on
a throwaway thread (`webbrowser` shells out and can block) and a failure costs only
the convenience. The picker's splash is deliberately sized to fit all four options
in a stock 80×24 terminal — a clipped splash leaves the bottom row unclickable.

## 14. \_\_main\_\_.py — the controller and runtime traps

`AmbientController` wires everything: builds queues, capture (over the shared backend
instance — capture and the device picker must agree on which platform stack they talk
to), transcriber, gate, answerer, logger, UI; spawns the worker tasks (including the
sweep worker when enabled); owns pause/resume (drain everything, reset segmenters,
ignore transcripts that started before the resume boundary), device switching
(serialised by a lock, capture restarted after), profile switching, the status line,
and shutdown bookkeeping (terminal JSONL records for all in-flight work).

The transcript consumer's exact order of operations matters and is easy to get wrong:

```
sys transcript:  echo-check → (hold for echo window, only if mic policy is "full")
                 → continuity merge → process
mic transcript:  settle any pending sys duplicates → continuity merge → process
process:         context.add (echo-suppressed) → UI transcript line
                 → if Agent: selected-speaker/filter → local social reply or
                   serialized direct Agent answer (no question gate/sweep)
                 → channel policy check (Stage 0)
                 → snapshot gate context + answer context     ← ordered path ends here
                 → detach bounded gate task
gate task:       gate.evaluate(policy) → reject: log (judgment-stage rejections
                     also feed the sweep buffer)
                 → accept: UI card + enqueue answer job (with the snapshot)
answer worker:   claude -p (Q&A history read now, channel stance) → stream deltas
                 → resolve card → mark_answered + mark_answer_text + append history
                 → spawn audit task (verify="always") → JSONL record
audit task:      wider fresh context + raw text + history → OK (the usual case)
                 or replacement → card "revised", history patched, verify_revision log
sweep worker:    every 25 s → re-judge buffered rejections → recovered questions
                 re-enter at "UI card + enqueue answer job" like any accept
```

### Textual runtime traps (institutional memory)

Three startup/teardown decisions in `__main__.py` exist because Textual owns the
terminal and replaces the standard streams, which breaks bystanders in non-obvious
ways. All three were paid for:

- **The resource-tracker must start before Textual does [measured].** Whisper's lazy
  model load (huggingface_hub → tqdm) creates a `multiprocessing` lock at *first
  transcription*, which on POSIX spawns the resource-tracker process and passes
  `sys.stderr.fileno()` into its `fds_to_keep`. By then Textual has replaced stderr
  with a capture whose `fileno()` is **-1**; the spawn dies with "bad value(s) in
  fds_to_keep", and **every transcription fails — CUDA and the CPU fallback alike**
  (both paths hit the same lock). `resource_tracker.ensure_running()` is therefore
  called in `main()`, while stderr is still the real terminal; later lock creation only
  writes to the already-running tracker's pipe. `HF_HUB_DISABLE_PROGRESS_BARS=1` is set
  alongside it — a download progress bar could only render as garbage into Textual's
  capture, and skipping tqdm removes most of the machinery that wanted the lock in the
  first place.
- **`active_app` must be seeded in `AmbientController.run()` [measured].** Textual
  resolves the running app through the `active_app` ContextVar, including inside every
  `Timer` it starts. The controller's tasks are *not* descendants of Textual's message
  pump, so a widget method they reach (the answer-delta callback → `QACard`'s flush
  throttle) creates a timer whose task cannot resolve `active_app`: it dies instantly
  with `LookupError`, and shutdown re-raises it when awaiting dead timers — crashing
  quit. `active_app.set(self.app)` at the top of `run()` makes every task created below
  inherit a context in which Textual timers work.
- **Root logging goes to a file, never stderr.** Once Textual owns the terminal,
  anything logged to stderr is painted raw over the UI — one component's traceback
  makes the whole app look dead. `_main()` swaps the root handler for
  `logs/ambientqa.log`; the UI surfaces what matters through the status bar and warning
  toasts.

## 15. Config, profiles, logging

**config.py** — typed dataclasses per section (`[audio] [stt] [context] [gate] [merge]
[answer] [ui]`), loaded from `config.toml`, unknown keys and invalid values rejected at
startup with named errors. Every tunable mentioned in this document lives here,
including the backend selector (`audio.backend`), the dedupe cooldown
(`gate.reask_cooldown_s`) and the second-pass switches (`answer.verify`,
`answer.sweep`, `answer.sweep_model`, `answer.verify_context_turns`,
`answer.history_turns`).

**profile.py / profiles/*.md** — optional standing context (Topic / Background /
Vocabulary sections). Vocabulary → Whisper hotwords; Topic → gate referent
disambiguation (with explicit prompt language forbidding its use as a relevance filter
or topic-injector); Topic+Background → answer pitch, explicitly subordinate to the
recent transcript (§11). Profiles are data, never instructions — the prompts say so
explicitly, which is the injection defence. Profiles may also supply an Agent greeting
and selected speaker channel, but never activate the role: Q&A/Agent is runtime state,
independent of profile and delivery. Changing profile during Agent starts a clean
conversation boundary.

**logging_.py** — one JSONL line per utterance, written live (open-append-close per
record, lock-guarded): channel, text, gate decision + reason, query, answer, status,
`web_lookup`, per-stage latencies. Crash-safe by construction; the file is the ground
truth every analysis and replay in this project's history was mined from. The
second pass added to the vocabulary: `gate_reason` now also carries
`imperative_request`, `reask_of_recent`, `second_pass_recovery` and `verify_revision`
(the last with `answer_status: "revised"`, as a second record for the same utterance
id). Render any session into a styled HTML replay with `scripts/render_session.py`, or
browse it in-app with `l`. Runtime diagnostics go separately to `logs/ambientqa.log`
(§14).

## 16. The empirical foundation

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
| 22 | parec with server-default buffering | ~2 s to first byte, then ~1 s bursts | `--latency-msec=<frame_ms>`; first frame in ~50 ms |
| 23 | parec stderr into a pipe | 64 KiB fills, child blocks: deaf channel, no error | stderr to a temp file; tail read at EOF |
| 24 | raw USB mic, 100% PipeWire volume | ~34 dB hot; loud speech clipped 1.7% of samples | WebRTC `ec_mic` module; config pins it |
| 25 | merge at 4.5 s gap / 9 s window | ~5 s think-pause before the question never merged | 6.5 / 13.0 |
| 26 | prefix-matched hallucination blocklist | ate "Thank you. So, tell me about…" whole | exact whole-transcript match only |
| 27 | qwen2.5:3b as gate model | flips on real questions when context is present | prompt+model are a pair: gemma4:e2b |
| 28 | Ollama warmup via `to_thread` | quit during cold load hangs the process (executor join) | daemon thread signalling into the loop |
| 29 | resource-tracker spawned under Textual | stderr `fileno()` −1 → fds_to_keep crash; ALL transcription dead | `ensure_running()` before the TUI starts |
| 30 | widget timer from a controller task | `active_app` LookupError; quit crashed | seed `active_app` in `run()` |
