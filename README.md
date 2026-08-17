# Ambient Q&A

Ambient Q&A is a passive Windows side pane that listens to the microphone and system audio,
transcribes speech locally, identifies real questions, and answers only those questions. It never
prompts or interrupts you.

## Documentation

| Document | What it covers |
|---|---|
| This README | Usage, tuning, configuration reference, troubleshooting |
| [SPEC.md](SPEC.md) | The implementation spec — verified environment facts and stage-by-stage requirements |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Deep dive: every module, the concurrency model, and *why* each design decision was forced — with all measured facts consolidated in one table (§15) |
| [docs/REBUILD-GUIDE.md](docs/REBUILD-GUIDE.md) | Staged guide to building the whole system from scratch, the traps at each stage, a porting checklist for a new machine, and standalone learning exercises |

Moving development to a new machine? Follow the porting checklist at the end of
[docs/REBUILD-GUIDE.md](docs/REBUILD-GUIDE.md).

## Quickstart

Requirements: Windows 11, Python 3.11 through the Python launcher, Ollama with `gemma4:e2b`,
the Claude CLI signed in, and an NVIDIA GPU for the preferred Whisper configuration.

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
python -m ambientqa
```

`setup.ps1` deliberately uses `py -3.11`; do not use the unrelated `python` environment already
on `PATH`. The first Ollama warmup can take about 67 seconds. Once warm, question classification
is normally well under a second.

Use `python scripts/list_devices.py` to see input and WASAPI loopback endpoint names. Set
`audio.mic_device` or `audio.output_device` to a unique, case-insensitive substring when the
Windows default is not the desired source.

Run the test suite with:

```powershell
python -m pytest -q
```

## Tuning the question gate

Deciding what counts as a real question is the core of this tool, so it is measured rather than
guessed. `scripts/eval_gate.py` runs a labelled set of utterances — explicit questions, implicit
information needs, referential questions, rhetorical tags, trailing-off fragments, speech aimed at
other people, and plain self-narration — through the real gate:

```powershell
python scripts/eval_gate.py --mode balanced
```

It reports accuracy, false positives, false negatives, and latency, and exits non-zero if any
false positive appears. **Run it after changing any heuristic or Stage B prompt.** Current results:

| Mode | Accuracy | False positives | False negatives |
|---|---|---|---|
| `strict` | 25/26 | 0 | 1 (rejects an implicit request — by design) |
| `balanced` | **26/26** | 0 | 0 |
| `eager` | 25/26 | 1 (by design) | 0 |

False positives are weighted as the worst failure: an unasked answer interrupts you, whereas a
missed question is silent and recoverable with the `a` key.

Two rules do the heavy lifting and are easy to break:

- **Stage A `no_content_words`** rejects utterances whose tokens are all fillers or stopwords.
  Without it, a fragment like `"uh, um, so, the thing is"` reaches the classifier, which will
  invent a question out of the surrounding transcript and answer something never asked.
- **The Stage B prompt** states that context exists *only* to resolve referents, and that the
  speaker must be expressing that they do not know something. Merely mentioning a technical topic
  is not a question — stated plans and asserted facts are not requests.

## Hearing the other speaker

`audio.output_device` should normally be **blank**. Blank opens *every* WASAPI loopback endpoint at
once and forwards whichever one is actually carrying speech, so it does not matter whether today's
call plays through the headset, the monitor or the desktop speakers.

Naming an endpoint pins it, and that is how a whole conversation gets lost. A loopback opened on an
endpoint the call is not playing through does not error — it opens perfectly happily and captures
silence. The status bar reads `sys:on`, your own transcripts keep scrolling, and you find out
afterwards that the other speaker was never heard at all. That happened, for a full interview.

Two defences, both on by default:

- Every endpoint is watched, so there is nothing to guess. Opening an idle endpoint is nearly free.
- Any source open but below the noise floor for `audio.silent_source_warn_s` (45 s) reads
  `sys:SILENT 60s ⚠` instead of `sys:on`.

Only one endpoint feeds the pipeline at a time — one segmenter cannot be fed two interleaved
conversations. Handover is instant once the incumbent has been quiet for ~1.5 s, so switching
devices between sessions costs nothing, while noise elsewhere cannot steal a live utterance.

## Which channel gets answered

`gate.channel_policy` sets how freely the gate may treat each channel. Both are always transcribed
and always feed the context window; this only decides what may become an answer.

| Policy | Behaviour |
|---|---|
| `full` | Heuristics **plus** the semantic gate, which may rewrite an indirect request into a question. Default for `sys`. |
| `explicit` | Only speech actually shaped like a question. Default for `mic`. |
| `off` | Context only, never answered. |

Default: `channel_policy = { mic = "explicit", sys = "full" }`.

**Why your own channel is not simply `full`.** You already know what you are saying, so letting the
semantic gate mine your speech does not surface answers — it manufactures questions out of
narration. That gate is *asked to rewrite speech as a query*, and it will happily oblige for a plain
statement: `"...so I built a RAG system where"` became *"What is a RAG system?"*, `"I use Gemma 4"`
became *"How does Gemma 4 perform for smaller tasks?"* Around thirty such cards in forty minutes,
none asked by anyone, all of it moving text in your peripheral vision while you were trying to talk.

**Why it is not `off` either.** Blocking the channel outright also swallows the questions you really
do ask out loud. `"Okay, what do you mean, how, how do I truncate it?"` produced nothing at all and
had to be force-answered by hand.

`explicit` is the split that satisfies both. An utterance is accepted when it is a well-formed
interrogative (Stage A, free and instant — **no Ollama call at all**), or when Whisper heard it end
in `?`, which is the one signal that survives disfluency. Anything declarative is rejected before
the semantic gate ever sees it, so it cannot be rewritten into a question.

Replayed over all 690 recorded mic utterances: **221 were answered under the old gate, 66 under
`explicit`** — 38 of them instantly via Stage A, 28 through the semantic gate. 662 utterances never
reach Ollama at all, which is also why the channel got faster.

Set `mic = "full"` to restore the old flood, or `mic = "off"` to never answer yourself. Press `a`
to force-answer your last utterance under any policy.

## Answering several questions at once

Questions are answered in parallel, not one after another. Two limits control it:

| Key | Default | What it bounds |
|---|---|---|
| `answer.max_concurrent` | 4 | Questions answered simultaneously — one `claude -p` process each |
| `gate.max_concurrent` | 3 | Utterances judged simultaneously |

Measured with four real questions: **6.0 s wall clock against 22.2 s if serialised, 3.7×**, all four
in flight at once. The status bar shows `answers:N active/M done`.

Gating runs *off* the consumer loop. It used to run inline, which was the hidden serialiser: a
~900 ms Ollama call held the ordered path, so a question arriving while the previous one was being
judged could not reach the answer queue until that call returned. Its answer started a full gate
late every time, with the answerer sitting completely idle. Two things are still ordered and must
stay that way — adding to the context window, and snapshotting the context each question is judged
and answered against — so every question sees the conversation as it stood when it was asked, no
matter what order the gate tasks finish in.

Answers may therefore complete out of order. Each is bound to its question's id, so cards resolve
independently and never cross-contaminate.

Raising `answer.max_concurrent` costs one Node process per slot. The floor is unchanged: a single
`claude -p` invocation still takes 6–9 s, so concurrency removes queueing, not per-answer latency.

## When it looks things up

`answer.web_lookup` is `"off"`, `"auto"` (default) or `"always"`.

Most questions are answered from the model's own knowledge in ~3.5 s. But for facts that *moved*
since it was trained — product names, versions, pricing, availability — the model is not merely
unsure, it is **confidently wrong**, which reads like a real answer and is worse than a hedge. Asked
*"What is Vertex AI called now again?"* it replied that Vertex AI had not been renamed; it had, to
the Gemini Enterprise Agent Platform.

`auto` looks those up and nothing else. The trigger is naming, versioning, pricing and availability
— not any use of "now" or "current", which are usually discourse filler. Across all 569 recorded
questions it fires on **3 (0.5%)**.

The cost is why it must stay rare: **15–17 s with a lookup against 3.5–7 s without**. Answers that
looked something up are flagged `web_lookup: true` in the session JSONL, which is what explains an
outlier latency there.

One measured subtlety: *permitting* `WebSearch` is not enough. With the tool allowed but not
demanded, the model answered from memory in 3.6 s and was still wrong. The prompt has to tell it
that its memory is the problem for this class of question.

## Answer style

`answer.style` in `config.toml` selects how answers are written:

- **`cue`** (default) — a cue card, not an answer. One sentence you can say word-for-word, a blank
  line, then two or three `•` keyword prompts of ≤6 words each. Built for the real constraint: you
  get roughly *one glance* while already speaking, and a paragraph is unreadable in that moment.
  Pair with `max_words = 45`.
- **`interview`** — what a person actually *says* when asked out loud: one plain sentence answering
  the question, then two or three concrete specifics named but not elaborated, then stop. Two to
  four sentences, ~70 words. Better for reviewing a transcript after the fact than during a call.
- **`terse`** — one or two compressed sentences. Pair it with a lower `answer.max_words`.

`max_words` defaults to 45 (`cue`). For `interview`, 70 is the tuned value. That setting has two
failure modes in opposite directions, both hit during development:

- Too low (60 with an "answer directly" prompt) collapsed into a comma-jammed keyword list.
- Too high (200 with "explain each point") produced a 219-word, multi-paragraph essay — accurate,
  but nothing anyone would say in an interview.

The prompt therefore caps sentence count *and* explicitly forbids a closing summary, and carries a
worked example, which anchors the target length far more reliably than any adjective. If you retune
this, change the example rather than only the number.

Answers are flattened through `plain_text()` before display, because the pane renders into a plain
`Static` — without it, any Markdown the model emits shows its literal syntax (`**Path operations**`)
on screen.

## Tuning pause handling

Speech splits into separate utterances at every pause, so a thought spoken with a mid-sentence
pause arrives as fragments. `[merge]` stitches them back together before gating.

| Key | Default | Meaning |
|---|---|---|
| `merge_gap_s` | 4.5 | Max **silence** between two utterances for the second to continue the first |
| `merge_window_s` | 9.0 | Max **wall-clock** time an unfinished utterance is held awaiting a continuation |
| `max_merge_parts` | 5 | Fragments combined into one utterance |
| `max_merge_s` | 25.0 | Total spoken length of a merged utterance |

`merge_window_s` must be comfortably larger than `merge_gap_s` — it has to outlast the silence
**plus** the spoken length of the continuation **plus** its transcription latency. If it only
matches the gap, the hold expires before the continuation is transcribed and merging silently
never happens. Raise both if you pause for longer than ~4s mid-sentence.

Only utterances that look *unfinished* are held, so complete questions are still gated instantly
and these values add no latency to them.

## Code in answers

Interview style forbids markdown because answers are meant to be spoken. Code is the single
exception: asked for an example, the model gives one short spoken lead-in, a real fenced block,
and at most one sentence after. The prose word cap explicitly does **not** apply to the code, so
examples are never truncated to fit.

Two things had to change together, and either alone leaves the bug in place:

- **The prompt.** Under a blanket no-markdown rule the model flattens code into a sentence —
  `def wrapper(*args, **kwargs): print "before"; result = func(...)` — which is unreadable and
  drifts into invalid syntax (that example is Python 2).
- **`plain_text()`.** Fenced blocks are now stashed *before* the prose rules run. This is not
  cosmetic: in Python, `*args, **kwargs` is an exact match for the bold and italic patterns, and
  the bullet rule eats any line starting with `*`, `-`, or `+`. Running those over code silently
  corrupts it, and indentation would not survive either.

## Rehearsing answers aloud

Reading an answer back to practise it is not a new question. Without suppression this feeds back
badly: one question, rehearsed aloud, produced **five** duplicate answers in a real session,
because each fragment of the recital was gated independently and the semantic gate inferred a
fresh information need from the surrounding context.

Two signals decide it, and both are required — either alone gives false rejections:

1. **Containment** — the fraction of the utterance's *content* words that came from a recent
   answer. This is one-directional on purpose: a symmetric similarity ratio is dragged toward zero
   because the answer is far longer than the spoken fragment.
2. **No need-marker** — rehearsal is purely declarative. A genuine follow-up on the same topic
   almost always carries a word like `understand`, `wonder`, `remind`, `explain`, `what`, `how`.
   This is what separates *"Pydantic does validation using type hints"* (rehearsal) from
   *"I don't understand the dependency injection part"* (a real question).

Explicit interrogatives are deliberately **exempt** — if you actually ask, you get an answer even
if it echoes. Verbatim re-asking is already handled by `dedupe_ratio`.

| Key | Default | Meaning |
|---|---|---|
| `answer_echo_ratio` | 0.5 | Containment above which a declarative utterance counts as rehearsal. `0` disables it. |
| `answer_echo_window_s` | 300 | How long an answer stays suppressible |

Raise the ratio if it ever swallows a real question; lower it if rehearsal still gets answered.
Suppression happens *before* the Ollama call, so it also saves ~700ms per rehearsed line.

## How it works

Two non-blocking PyAudioWPatch capture threads feed per-channel Silero VAD segmenters. One
faster-whisper worker transcribes utterances serially. Free heuristics reject obvious
non-questions and fast-accept explicit interrogatives; remaining speech goes to the local
`gemma4:e2b` Ollama gate. Confirmed questions each launch their own bounded, one-shot
`claude -p` process and stream output into that question's card as it is generated. There is
intentionally no persistent Claude stream session.

Keys:

- `p` — pause or resume listening
- `c` — clear the visible feed
- `t` — show or hide raw transcript lines
- `a` — force-answer the most recent utterance
- `s` — cycle strict, balanced, and eager gate modes
- `x` — choose or disable a standing context profile
- `d` — compare every microphone and loopback endpoint with live meters, then select one
- `q` — quit

The audio-device picker pauses the main capture streams while it is open, probes every WASAPI
endpoint in shared mode, and restarts capture immediately when it closes. Devices held by another
application remain visible as unavailable without affecting the other meters. For the same
workflow outside the TUI:

```powershell
.\.venv\Scripts\python.exe scripts\pick_mic.py
.\.venv\Scripts\python.exe scripts\pick_mic.py --seconds 6 --list
```

Every completed utterance is appended to `logs/session-<timestamp>.jsonl` with its channel,
transcript, gate decision, reason, answer, and measured stage latencies. Records are written
live, one line per utterance as it completes — nothing is buffered until session end, so the
log survives a crash and can be tailed from another terminal while a session runs.

## Session replay

Render any session log into a self-contained HTML page styled like the live pane — dim
timestamped transcript lines, orange Q&A cards, per-answer timing, and badges for forced
answers, web lookups, and errors:

```powershell
python scripts\render_session.py
```

With no argument it renders the newest session; pass a `logs\session-*.jsonl` path for a
specific one, and `-o` to choose the output location (default: the `.html` next to the
`.jsonl`). Run it from the project root — the no-argument form looks for `logs\` relative to
the working directory. The replay also shows what the live pane never had room for: every
rejection reason, which is where gate-tuning signal comes from.

## Configuration reference

All settings are in the fully commented `config.toml`.

| Section | Key | Meaning |
|---|---|---|
| `audio` | `mic_device`, `output_device` | Optional device-name substring |
| | `sample_rate`, `frame_ms`, `queue_size` | Capture format and bounded frame queue |
| | `silence_ms`, `pre_roll_ms` | Trailing silence and onset preservation |
| | `min_utterance_ms`, `max_utterance_s` | Segment discard and force-flush limits |
| | `silent_source_warn_s` | Flag an open-but-inaudible capture source in the status bar |
| `stt` | `model` | faster-whisper model name |
| | `device`, `compute_type`, `cpu_compute_type` | CUDA primary and CPU fallback |
| | `queue_size`, `language` | Bounded utterance queue and optional language |
| | `hallucination_blocklist` | Normalized exact phrases discarded after STT |
| `context` | `profile` | Markdown profile path; empty disables profile context |
| | `enabled` | Master switch for profile influence |
| `gate` | `model`, `ollama_url`, `request_timeout_s` | Local classifier connection |
| | `channel_policy` | Per-channel `full` / `explicit` / `off` gating freedom |
| | `max_concurrent` | Utterances judged at once, off the ordered consumer path |
| | `mode` | `strict`, `balanced`, or `eager` prompt policy |
| | `min_words`, `context_turns` | Heuristic minimum and semantic context |
| | `dedupe_window_s`, `dedupe_ratio` | Recent answered-question suppression |
| | `echo_window_s`, `echo_ratio` | Cross-channel transcript echo suppression |
| | `queue_size` | Bounded transcript queue |
| `merge` | `enabled` | Coalesce likely mid-sentence VAD fragments before gating |
| | `merge_gap_s`, `merge_window_s` | Maximum audio gap and continuation hold window |
| | `max_merge_parts`, `max_merge_s` | Safety caps on one accumulated thought |
| `answer` | `answer_model`, `max_words` | Claude model and prose word budget |
| | `style` | `cue`, `interview`, or `terse` answer shape |
| | `web_lookup` | `off` / `auto` / `always` — search for facts that move |
| | `stream` | Stream partial output per one-shot process; `false` restores buffering |
| | `max_concurrent`, `answer_timeout_s` | Process semaphore and per-answer timeout |
| | `context_turns`, `queue_size` | Background context and configured capacity |
| `ui` | `show_transcripts` | Initial raw-transcript visibility |
| | `log_dir`, `status_interval_s` | Session output directory and status refresh |

All bounded queues drop the oldest item on overflow. Capture producers never wait on a consumer.
The default `audio.silence_ms` is 900 ms. Raising it further can reduce splitting during longer
thinking pauses, but delays transcription and answers by the same additional silence.

Context profiles live in `profiles/*.md` and may contain optional `## Topic`,
`## Background`, and `## Vocabulary` sections. Vocabulary biases Whisper spelling, Topic helps
the gate resolve words already present in an utterance, and Topic plus Background tune answer
depth. Profile context never filters topics or turns contextless speech into a question. Press
`x` to switch profiles live; the selection is saved to `context.profile`.

## Troubleshooting

**Status bar shows `sys:SILENT 60s ⚠`:** the loopback opened fine but is carrying nothing. If you
have pinned `audio.output_device`, clear it — blank watches every endpoint and follows the one with
speech on it, which removes this failure entirely. If it is already blank, nothing is playing
through *any* output endpoint: check that the call is not muted and that Windows is routing it
somewhere. Press `d` for live meters; the right endpoint is the one that moves when the other
person speaks. Tune the delay with `audio.silent_source_warn_s` (default 45 s).

**No loopback device:** run `python scripts/list_devices.py` to see the endpoints. A pinned name
that matches nothing no longer drops you to microphone-only — it falls back to the default output
and warns, since mic-only silently loses the side of the conversation worth answering. If no
endpoint can be opened at all, the status bar warns and the app continues microphone-only.

**Status bar shows `mic:off` / every microphone fails to open (`-9996 Invalid device`,
`-9999 Unanticipated host error`):** almost always the **Microsoft Store build of Python**.

The Store Python runs inside an app container with its own microphone capability, tracked
separately from the normal Windows privacy settings and often set to `Deny`:

```
HKCU:\...\CapabilityAccessManager\ConsentStore\microphone\
    PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0 = Deny
```

The signature is distinctive: **every** microphone fails across **all three** host APIs (MME,
DirectSound, WASAPI), while loopback capture works perfectly — because loopback reads a *render*
endpoint, which the microphone capability does not gate. Meanwhile Windows reports every device as
`OK`, and both global and desktop-app microphone privacy read `Allow`, so the settings UI looks
completely fine. The app starts, shows `mic:off` in the status bar, and silently never hears you.

Toggling the Windows setting usually does **not** fix it — the Store Python often isn't listed as
its own entry, and the setting may not apply to the venv's child process. Install a normal
python.org build and rebuild instead:

```powershell
winget install --id Python.Python.3.11 --scope user
Remove-Item -Recurse -Force .venv
.\setup.ps1
```

`setup.ps1` now refuses to proceed if the venv's `sys.base_prefix` is under `WindowsApps`.
Verify with `python -c "import sys; print(sys.base_prefix)"` — it must not contain `WindowsApps`.

**It hears system audio but not me:** check *which* microphone it picked. The status bar names the
active device, and the app follows the Windows default, which is often not the one you speak into.
Set `audio.mic_device` in `config.toml` to any case-insensitive substring of the device you want
(for example `"Logitech"`), using the names from `python scripts/list_devices.py`.

**Whisper says `cpu`:** CUDA/model initialization failed, so faster-whisper fell back to CPU
`int8`. Check the NVIDIA driver and the installed `nvidia-cublas-cu12`/`nvidia-cudnn-cu12`
packages. The status bar deliberately makes this fallback prominent because it is slower.

**Ollama is not running:** start Ollama, confirm `ollama list` includes `gemma4:e2b`, then restart
the app. Until the local gate is available, fast heuristic accepts still work and uncertain
utterances are rejected safely.

**Gate responses are empty:** `gemma4:e2b` is a reasoning model. Every Ollama request must contain
top-level `"think": false`; otherwise `message.content` can be empty. Both application requests
and the PowerShell warmup include it. If you put Ollama behind a proxy, ensure that field is not
removed.

**Claude answers time out or fail:** run `claude -p "hello"` to confirm the CLI is installed and
authenticated. Each question intentionally uses a separate one-shot process and is killed after
`answer.answer_timeout_s`; concurrent questions are limited by `answer.max_concurrent`.
