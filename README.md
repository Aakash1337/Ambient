# Ambient

Ambient is a passive side pane for Windows, Linux, and macOS 14+ on Apple Silicon that listens to
the microphone and system audio, transcribes speech locally, identifies real questions, and
answers only those questions. It never prompts or interrupts you.

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

All platforms need Python 3.11, Ollama, and an authenticated Claude CLI. Before launching
Ambient, install Ollama, pull the model named in `[gate] model` (`gemma4:e2b` by default), and
confirm both external prerequisites work:

```bash
ollama pull gemma4:e2b
ollama run gemma4:e2b "reply ok"
claude -p "say ok"
```

The gate prompt was engineered against `gemma4:e2b`; do not silently substitute another model.
The setup scripts install Ambient's Python dependencies, but they do **not** install Ollama or
Claude, pull the gate model, or authenticate Claude. Windows and Linux use an NVIDIA GPU for the
preferred Whisper configuration; macOS runs faster-whisper on the CPU because CTranslate2 does
not expose a Metal/MPS backend.

**Windows** (Windows 11, Python 3.11 through the Python launcher):

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
python -m ambientqa
```

`setup.ps1` deliberately uses `py -3.11`; do not use the unrelated `python` environment already
on `PATH`.

**Linux** (PipeWire with `pipewire-pulse`, which provides the `pactl`/`parec` tools the capture
backend runs on):

```bash
./run.sh
```

`run.sh` is the whole setup. On first run it creates its own `.venv-linux` (the Windows-only
`pyaudiowpatch` is skipped by its `sys_platform` marker in `requirements.txt`), and it re-runs
pip whenever `requirements.txt` is newer than its install stamp. On every run it starts Ollama
if it is down, loads PipeWire's `module-echo-cancel` to provide the processed `ec_mic`
microphone source, points CTranslate2 at the pip-installed CUDA libraries, and launches the
pane. Because it bootstraps everything itself, it is also the right `Exec` target for a desktop
app-menu entry (the pane is a TUI).

**macOS 14 (Sonoma) or newer on Apple Silicon** (Intel and Rosetta Python are explicitly blocked;
the OS floor comes from
[Ollama's current macOS requirement](https://docs.ollama.com/macos)):

```bash
brew install python@3.11
./setup-macos.sh
./run-macos.sh
```

`setup-macos.sh` creates an isolated `.venv-macos` from the hash-locked
`requirements-macos-arm64.txt`, downloads the ~1.6 GB faster-whisper snapshot pinned to an
immutable Hugging Face commit and verifies every runtime file (including the weight SHA-256),
installs and smoke-tests `espeak-ng`, and rejects macOS below 14, Intel, or a Rosetta/x86_64
Python. Allow roughly 2 GB of free disk plus the Python environment and an uninterrupted HTTPS
connection for first setup; later runs reuse and re-verify the local snapshot. `run-macos.sh`
refreshes the environment when the lock digest or setup script changes, starts an
Ambient-owned Ollama server on a random loopback port when the CLI is available, verifies that
the spawned PID owns that listener before every transcript-bearing gate request, and launches the
CoreAudio backend. It never trusts a service merely because it answered on Ollama's conventional
shared port; if the private child is unavailable or exits, semantic gating fails closed. Neither
script installs Ollama/Claude, pulls the gate model, or authenticates Claude, so run the
prerequisite checks above first. The user-pulled Ollama tag remains a mutable external
prerequisite; the Python and Whisper pins do not claim reproducibility for that separate model.
The first live capture may make macOS ask for **Microphone** access for Terminal, iTerm, or the
terminal application used to launch Ambient. Grant it under **System Settings → Privacy &
Security → Microphone**, then relaunch.

Microphone capture works with no extra audio driver. To hear the other side of a call, macOS
needs a recordable system-audio input:

1. Install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole):
   `brew install --cask blackhole-2ch`, then restart the Mac so CoreAudio loads the driver.
2. Open **Audio MIDI Setup**, create a **Multi-Output Device**, and enable both the physical
   headphones/speakers and **BlackHole 2ch**. Keep the physical output as clock source and enable
   drift correction for BlackHole.
3. Select that Multi-Output Device under **System Settings → Sound → Output**.
4. Launch Ambient, press `d`, select the real microphone for `mic` and **BlackHole 2ch** for
   `sys`, then play call audio and confirm both meters move.

Without a loopback input, Ambient stays usable in microphone-only mode and shows an actionable
system-audio warning. Web and voice launches are `./run-macos.sh --web` and
`./run-macos.sh --voice`. The launcher uses `config.macos.toml`, which inherits every shared
setting from `config.toml` but starts with blank device selections, CPU transcription, and no
active profile. Press `d` once to choose this Mac's devices; those choices remain in the macOS
overlay and do not overwrite Windows/Linux selections. It still inherits shared answer and
knowledge settings; press `x` to select a profile deliberately when one is appropriate.

**macOS validation status:** the CoreAudio backend has automated lifecycle/enumeration tests
using injected `sounddevice` fakes, but this repository does not include a recorded live
acceptance run on Apple Silicon hardware. Treat this build as a macOS release
candidate until the real-hardware checklist in
[docs/REBUILD-GUIDE.md](docs/REBUILD-GUIDE.md#porting-checklist-for-a-new-machine) passes on the
machine being shipped. That checklist must include the first setup download and an offline
relaunch using the verified local Whisper snapshot. Whisper is CPU-only; Ollama can use Apple
Silicon's GPU. Intel is not a
release target because the required PyTorch stack has no current security-supported x86_64 wheel.

**Linux/KDE desktop launcher:** on the configured Linux installation, opening **Ambient** from the
app menu launches a pre-start splash
with four guarded choices: **Assist** (answers stay on screen), **Voice** (screen plus spoken
answers), **Web Console** (then **Web Assist** or **Web Voice** in the browser), or **Emergency
Fallback** (the pinned pre-voice build, behind a second confirmation). Web Assist is focused by
default; Web Voice launches the same single pipeline with independent Q&A/Agent interaction
and Normal/Conversational delivery controls.
Cancel starts nothing. The chooser runs before capture, models, Ollama, or the application lock,
so it never creates a second pipeline merely to select a mode. Terminal launches remain direct:
`./run.sh` or `./run.sh --assist` selects Assist, and `./run.sh --voice` selects Voice. The app
menu's right-click actions can also bypass the splash and start either launch role directly.

`ec_mic` matters for transcription quality: it runs WebRTC noise suppression and automatic gain
control over the raw microphone. The raw USB mic at full PipeWire volume sits ~34 dB above its
hardware-neutral level — loud speech clipped 1.7% of samples and garbled Whisper — so
`config.toml` pins `audio.mic_device` to the processed source instead.

On the original development host, the first Ollama warmup took about 67 seconds and warm question
classification was normally under a second. Those are observations, not Mac guarantees; record
warmup/gate latency on the actual Apple-Silicon target.

Use the interpreter belonging to the platform environment to see input and system-audio endpoint
names (WASAPI loopback on Windows, PipeWire monitor sources on Linux, CoreAudio inputs on macOS):

```bash
# Linux
.venv-linux/bin/python scripts/list_devices.py
# macOS
.venv-macos/bin/python scripts/list_devices.py
```

On activated Windows PowerShell, use `python scripts\list_devices.py`. Set
`audio.mic_device` or `audio.output_device` to a unique, case-insensitive substring when the
platform default is not the desired source.

Run the test suite with:

```bash
# Linux
.venv-linux/bin/python -m pytest -q
# macOS
.venv-macos/bin/python -m pytest -q
```

On activated Windows PowerShell, use `python -m pytest -q`.

## Data handling, privacy, and consent

Ambient's audio path is local, but Ambient is **not an all-local transcript system**. The exact
boundary is:

| Path | Data handling |
|---|---|
| Capture, VAD, Whisper STT, and the primary Ollama gate | Run locally. Ambient does not send raw audio to Claude. On macOS the launcher uses a per-run Ollama child on a random loopback port, with PID/listener ownership checks; gate HTTP also bypasses proxy environment variables and accepts only a literal loopback URL. |
| Primary answer or Agent reply | Sends the accepted or rewritten question/speaker turn to Claude, together with a bounded recent transcript window, recent Q&A/dialogue history, active profile topic/background, and any retrieved knowledge-pack excerpts used for grounding. |
| Missed-question sweep | Enabled by default. Periodically sends rejected transcript candidates, a wider recent transcript window, and answered/in-flight question text to Claude. A rejection by the local gate therefore does not guarantee that its text stays local. |
| Answer verification | Off by default. If enabled, sends a wider transcript window, the raw transcribed utterance, rewritten question, delivered answer, Q&A history, profile context, and grounding to Claude. |
| Web lookup | When enabled and triggered, a separate tool-enabled one-shot receives **only the current question**—not transcript context, history, profile, grounding, repository files, or local logs. Its model-generated search queries and resulting traffic leave the machine. A context-dependent current-fact question may therefore fail rather than export unrelated conversation data. |
| UI and logs | The web server binds to loopback and requires its printed unguessable access URL for transcript/session/API routes. JSONL session logs stay on the local filesystem, but logs are plaintext and contain transcript/answer data. Git-ignore is not encryption, access control, or a retention policy. |

Claude-side storage, retention, and training treatment depend on the account, organization, and
provider terms used by the installed CLI; verify those terms before processing sensitive calls.
Every Ambient Claude invocation disables session persistence and project/user customizations,
uses an empty private working directory, supplies an empty MCP configuration, runs in restricted
non-interactive permission mode, and exposes an exact tool set (none except `WebSearch` for the
isolated lookup above). Conversation prompts travel over standard input, while system/profile
prompts use a mode-`0600` temporary file that is deleted after the child exits; their contents are
not placed in process arguments. The `espeak-ng` fallback likewise receives spoken answer text on
standard input. These controls reduce local process, tool, and configuration exposure; they do
not change the remote provider boundary described in the table.
The `1`/`2` input controls stop new muted-channel audio from entering transcript/context/log paths,
but they do not retract data already logged or sent.

Before enabling capture, obtain informed consent from **every participant** for audio capture,
transcription, local logging, and the external text processing described above. Provide any
required recording/AI notices and comply with applicable law, contracts, employer policy, and
meeting-platform rules. Do not use Ambient for covert transcription.

## Voice mode

```bash
./run.sh --voice
# macOS: ./run-macos.sh --voice
```

On the configured Linux/KDE desktop, the app-menu splash selects this flag; the macOS path is the
explicit `./run-macos.sh --voice` command above.

Voice mode is a per-launch role, not a config switch. Start exactly one copy with `--voice`:
that same pane shows the answer card and speaks it aloud. Do not run a silent copy beside it;
each process would independently capture, transcribe, gate, and call Claude for the same
question. The app refuses a second live pipeline by default. The first voice launch downloads
the local Kokoro voice files (~340 MB into `models/`); synthesis runs on the CPU so Whisper and
the gate keep the GPU. If Kokoro cannot load, the app selects the `espeak-ng` fallback and reports
it. On macOS, setup installs `espeak-ng` through Homebrew when needed and refuses to finish unless
a WAV synthesis probe succeeds, so the fallback is validated before launch. A later runtime
playback failure still leaves visual answers available. Press `m` to mute or unmute speech. Press `r` to switch
between **Normal** and **Conversational** delivery at runtime; the status bar shows the voice
state and selected delivery together (for example `voice:on/conversational`). Playback runs
through PipeWire's `paplay` on Linux and CoreAudio on macOS.

Normal is the safe startup default: answers keep the glanceable cue-card format and voice reads
only the opening sentence. Conversational mode asks for short, natural spoken prose and reads the
complete answer, including the substance of bullet/options lines (code blocks remain visual
only). It also understands narrowly scoped requests about a recent answer such as “continue
reading the answer,” “read the rest,” and “repeat that” without another Claude call. The choice is
runtime-only and resets to Normal on relaunch, so it cannot change the emergency baseline or the
known demo default. It deliberately keeps the microphone's conservative question gate; making all
mic narration semantic input previously produced dozens of unsolicited cards.

Repeating an answer is not limited to Conversational delivery: a recent mic request such as
“Can you repeat what you just said?” immediately reuses the exact previous answer in every
surface and delivery mode. It creates a normal card, follows the current speech setting, and
never asks Claude to interpret “repeat” as an audio-replay capability question.

### Agent interaction with any profile

Voice mode has an independent runtime **Interaction** control: **Q&A** keeps the selective
question/request gate, while **Agent** participates directly in a natural conversation. Press
`g` in the terminal or use the Q&A/Agent buttons in the browser. The selected knowledge profile
is a separate axis, so combinations such as **Cybersecurity analytics + Agent + Conversational**
and **Cybersecurity analytics + Q&A + Normal** are both valid. Every launch starts in Q&A; the
choice is runtime-only and does not modify the profile or emergency baseline.

Agent introduces itself once as an AI assistant, handles greetings, thanks, hold requests, and
goodbyes locally, and sends complete meaningful statements directly to the answer worker instead
of the question-only gate. Model replies use short speech-shaped prose plus layered prompt and
deterministic courtesy safeguards before they are displayed or spoken. The active profile supplies
domain knowledge, vocabulary, and framing rather than deciding whether Agent is on.

`profiles/customer-service-agent.md` supplies a call-specific greeting and support context, while
`profiles/cybersecurity-analytics.md` makes the same Agent behavior a cybersecurity conversation.
An optional `## Customer Channel` selects exactly one driving speaker input: `mic` is convenient
for an in-room role-play, while `sys` is the remote side of a real call. The other channel stays
visible but cannot steer Agent answers. A remote caller still needs the call application's audio
routing to hear local voice output; Ambient does not silently inject itself into Teams or a
telephony device.

Keys `1` and `2` independently stop or resume microphone and system-audio transcription. The
Web UI exposes the same **Mic** and **System** buttons. These are input privacy controls: they do
not mute spoken output, alter gate policy, or replace `m` (voice-output mute). Both inputs may be
off; Pause remains the global master. Devices stay open for health metering, but buffered or
in-flight audio from a muted interval is discarded before transcript/context and redacted from
the session log.

The hard problem voice mode solves is that this app listens to its own speakers: playback
lands verbatim in the `sys` loopback and acoustically in the microphone. Audio-level echo
cancellation was measured at only 6–9 dB on this path — far too shallow — so the defence is
deterministic instead: while any instance is speaking, every instance drops capture frames
for the muted channels (`tts.mute_channels`, default both). The speaking window is a file
in a shared per-user directory, so muting is cross-instance by construction, and the exact
spoken text is also recorded as answer-echo prose before playback starts, so anything that
leaks past the window's tail is rejected by the gate rather than answered. The cost is
honest deafness: for the few seconds an answer plays, the app is not listening, so
Conversational mode is turn-taking rather than barge-in (headphone
users can set `tts.mute_channels = ["sys"]` to keep the mic live).

By default only your own (`mic`) questions get spoken answers — speaking answers to the
other side's questions would broadcast them into the room and into any live call. Set
`tts.speak_channels = ["mic", "sys"]` to voice everything, and `tts.speak = "full"` with
`answer.style = "interview"` to hear whole prose answers instead of the cue headline.
Cross-process speaking leases remain as a defensive guard for deliberate `--allow-multiple`
testing, but that mode duplicates GPU and Claude work and is unsafe for a demo.

## Web console (opt-in)

```bash
# Linux only (run-web.sh delegates to run.sh)
./run-web.sh
./run-web.sh --voice

# macOS
./run-macos.sh --web
./run-macos.sh --web --voice
```

On the configured Linux/KDE desktop, the launch splash (`--choose`) offers **Web
console** alongside Assist, Voice, and the emergency fallback (`w` or `3`
selects it), then asks for **Web Assist** or **Web Voice**. Picked that way it
also opens your default browser on the console URL, since the app-menu launch
has no terminal to read it from. Assist remains the first, default-focused
choice at both safety boundaries.

The same pipeline normally listens on `127.0.0.1:8802`, but bare `/` is intentionally
unauthorized. Ambient prints (or opens) a per-run capability URL such as
`http://127.0.0.1:8802/?access=...`; use that exact URL and keep it private. It is styled after
the design exploration in `docs/UI/` and has larger,
more readable type than the terminal pane. It shows the live transcript with both
channels labelled, the question queue, streaming cue cards with REVISED / LATE /
FORCED / WEB LOOKUP badges, a gate-decisions panel listing every rejection *and its
reason* live, the status bar, and a read-only sessions replay built from the same
JSONL logs. The TUI keys work unchanged in the browser (`p a s t c x d l q`, plus
`m` for voice, `g` for Q&A/Agent, `r` for delivery, and `1`/`2` for independent
Mic/System listening); the audio device picker has the same all-endpoints live
meters and auto-closes if the page stops responding, so an abandoned tab can never
leave capture stopped. Web Voice also exposes clickable Mute/Unmute, Q&A/Agent, and
Normal/Conversational controls, with the same controller behavior as those keys.

The browser Quit confirmation waits for the server to acknowledge that capture has
stopped, then asks the browser to close the console tab. Browsers can refuse scripts
permission to close an externally opened tab; when that security rule applies, the
disconnected console is replaced by an unmistakable **Ambient has stopped** page
instead of looking live or silently doing nothing.

The app-menu launch verifies the public, identity-specific Ambient health endpoint before opening
the authorized URL. If another local service owns 8802, it selects the next free loopback port
and opens that actual capability URL; the terminal also prints it. An explicit `--web-port N`
remains pinned and fails with a concise message when occupied. Do not assume a
different service on a familiar port is this console—the health identity prevents
that mix-up. Health and static assets are intentionally public so startup works; index, state,
session replay, events, and commands reject requests without the separate access capability.

The console is deliberately **not** the default surface:

- The terminal pane remains the default (`./run.sh`) and the known demo baseline;
  `--web` is the only way to get the console, and the flag does nothing else.
- It is stdlib-only — nothing was added to `requirements.txt`, so run.sh's install
  stamp, the Windows setup, and the emergency fallback are all untouched.
- The pinned emergency build (`./run-emergency.sh`) is Linux-only and predates the web console.
  On Linux, quit the console before starting it. macOS has no pinned emergency launcher; quit the
  console and return to the normal pane with `./run-macos.sh`.
- The server binds `127.0.0.1` only and sensitive routes also require the per-run access
  capability. Answering and the default missed-question sweep can still send transcript-derived text to Claude as described in
  [Data handling, privacy, and consent](#data-handling-privacy-and-consent).

To rehearse the console with no microphone, models, or Claude at all:

```bash
# Linux
.venv-linux/bin/python scripts/webui_demo.py
# macOS
.venv-macos/bin/python scripts/webui_demo.py
```

which serves the real console fed by a canned call — useful as an offline demo
surface that audio problems cannot break.

## Demo emergency fallback (Linux only)

`run-emergency.sh` depends on the Linux `.venv-linux`, `flock`, `/proc`, and `run.sh`; it is not
a macOS or Windows fallback. On Linux, the escape hatch is independent of the voice implementation and never resets, stashes, or
overwrites the working tree. It extracts the pinned last pre-voice commit into a disposable
directory, reuses the installed environment, disables the optional paid second passes, and
runs with no voice code at all.

It is also available as **Emergency Fallback** on the app-menu splash. That convenience path
requires an explicit confirmation and then invokes the same validated `--takeover` script below;
the standalone script remains independent if the current picker or build itself is damaged.

```bash
./run-emergency.sh --check
# If fallback is needed, first quit the live pane with q, then:
./run-emergency.sh
# If the pane is frozen and cannot quit:
./run-emergency.sh --takeover
```

The launcher shares a process-lifetime application lock with normal startup and also checks
legacy heartbeat PIDs, so it refuses to start beside a live instance. That prevents recreating
the duplicate Whisper/Claude workload even if a UI heartbeat stalls. It also refuses to install
dependencies or accept voice arguments. `--takeover` is the explicit emergency button: it validates
the per-user heartbeat PID's `/proc` command line, asks only confirmed `python -m ambientqa`
processes to terminate, and force-stops one only if it remains frozen after five seconds. Run the
`--check` once before the demo; it exits without opening the application. The fallback cannot bypass
Claude authentication or an exhausted account limit; both versions still need a working
`claude -p` answer call.

## Tuning the question gate

Deciding what counts as a real question is the core of this tool, so it is measured rather than
guessed. `scripts/eval_gate.py` runs a labelled set of utterances — explicit questions, implicit
information needs, referential questions, rhetorical tags, trailing-off fragments, speech aimed at
other people, and plain self-narration — through the real gate:

```powershell
python scripts/eval_gate.py --mode balanced
```

On macOS, run `.venv-macos/bin/python scripts/eval_gate.py --mode balanced` instead.

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

`audio.output_device` should normally be **blank**. Blank opens *every* system-audio endpoint at
once (WASAPI loopback endpoints on Windows, PipeWire monitor sources on Linux, recognized
CoreAudio virtual inputs on macOS) and forwards
whichever one is actually carrying speech, so it does not matter whether today's call plays
through the headset, the monitor or the desktop speakers.

On Linux this needs no loopback driver or virtual cable: every output has a *monitor* source
(`Monitor of <sink>`) that carries whatever the machine plays through it — PipeWire's native
equivalent of WASAPI loopback — so the `sys` channel works out of the box, and blank watches
all the monitors exactly as it watches all the loopback endpoints on Windows.

On macOS, CoreAudio has no built-in recordable view of speaker output. Ambient automatically
classifies BlackHole, Soundflower, Loopback Audio, Background Music, and VB-Audio virtual inputs
as `sys` endpoints. BlackHole 2ch plus a Multi-Output Device is the documented, recommended path. Less-common
drivers can still be selected by explicitly pinning their input name in `audio.output_device`.

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
| `full` | Heuristics **plus** the semantic gate, which may rewrite an indirect request into a question. Command-form candidates also require semantic approval and fail closed if Ollama is unavailable. Default for `sys`. |
| `explicit` | Only speech actually shaped like a question. Explicit interrogatives and clear command-form asks use zero-call Stage A acceptance. Default for `mic`. |
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
in `?`, which is the one signal that survives disfluency. Command-form asks are accepted the same
free way — *"Talk about evaluation metrics."* carries no `?` and no interrogative word, but it is
as direct as a request gets. Terse forms such as *"Explain RAG"* bypass both the three-word noise
floor and the fragment hold, while incomplete *"Tell me"* / *"Talk about"* forms do not. And a
statement that substantially overlaps a question answered in
the last ~90 s is accepted as a deliberate re-ask: the first answer missed, and retries
(*"no, prompt engineering..."*) rarely carry fresh question intonation. Everything else
declarative is rejected before the semantic gate ever sees it, so it cannot be rewritten into a
question.

That zero-call command path is specific to `explicit` policy. On a `full` channel such as the
default other-speaker `sys` input, Stage A still recognizes the command shape but sends it to
Ollama for semantic judgment; incoherent request-shaped STT is rejected, and an unavailable
Ollama fails closed as `ollama_unavailable`. Explicit interrogatives remain zero-call fast accepts
under either policy.

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
The controlled-host `claude -p` test took 6–9 s, while a later live session ranged 3.8–21.8 s end
to end. Concurrency removes queueing; it does not guarantee per-answer latency.

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
| `merge_gap_s` | 6.5 | Max **silence** between two utterances for the second to continue the first |
| `merge_window_s` | 13.0 | Max **wall-clock** time an unfinished utterance is held awaiting a continuation |
| `max_merge_parts` | 5 | Fragments combined into one utterance |
| `max_merge_s` | 25.0 | Total spoken length of a merged utterance |

`merge_window_s` must be comfortably larger than `merge_gap_s` — it has to outlast the silence
**plus** the spoken length of the continuation **plus** its transcription latency. If it only
matches the gap, the hold expires before the continuation is transcribed and merging silently
never happens. The defaults moved from 4.5/9.0 after a measured miss: people pause ~5 s to
think between a trailed-off setup and the question that continues it, so the gate saw only the
second half. Raise both if you pause for longer than ~6 s mid-sentence.

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

Platform audio lives behind a small backend layer (`ambientqa/backends/`):
`[audio] backend = "auto"` selects the platform's native stack — WASAPI (PyAudioWPatch) on Windows,
PipeWire (one `parec` subprocess per stream) on Linux, and CoreAudio (`sounddevice`) on macOS — behind one device/stream contract, so
everything above capture is platform-blind. Non-blocking capture threads — one per open stream —
feed per-channel Silero VAD segmenters. One faster-whisper worker transcribes utterances
serially. Free heuristics reject obvious non-questions and fast-accept explicit interrogatives;
they also fast-accept clear command-form requests on an `explicit`-policy channel. A command-form
candidate on a `full` channel, and other remaining speech, goes to the local Ollama gate model
(`[gate] model`). Confirmed questions each launch their own bounded, one-shot
`claude -p` process and stream output into that question's card as it is generated. Those prompts
carry transcript-derived context, history, profile context, and optional grounding as documented
under [Data handling, privacy, and consent](#data-handling-privacy-and-consent). There is
intentionally no persistent Claude stream session.

Two independent second passes can run behind the live path, one per direction of error. The
per-answer verifier is disabled by default to avoid doubling Sonnet usage; the batched
missed-question sweep is enabled as a recovery backstop. With `answer.verify = "always"`, every
**delivered** answer is audited by a verifier with a wider transcript window
(`verify_context_turns` = 18 against the fast path's 6) plus the full Q&A history re-reads it
after it is already on screen, and replaces the card — marked `revised` — only when it was
materially wrong: a missed constraint, a misheard question, a dropped enumeration item. Style is
never grounds. With `answer.sweep = "always"`, the **missed** side is covered: every
`sweep_interval_s` (25 s) a small
model (`sweep_model`, `claude-haiku-4-5` by default) re-judges the gate's judgment-stage
rejections against the same wide context and the list of questions already answered or in
flight; genuine asks come back as late cards through the normal answer path, logged as
`second_pass_recovery`. Its hard end-to-end deadline starts at the captured transcript timestamp:
old candidates are pruned before classification, rechecked after classification, and any queued or
generating recovery is cancelled instead of being delivered after `sweep_max_age_s` (60 s by
default). The 25 s interval is scheduling cadence, not a promise that a recovery will arrive;
failures and remaining misses are still possible. A missed question
therefore has a ladder of recovery: re-ask it (past the 8 s dedupe cooldown a repeat gets a fresh
answer, and a statement retry of a recent question is accepted outright), wait for the sweep, or
press `a`.

Keys:

- `p` — pause or resume listening
- `c` — clear the visible feed
- `t` — show or hide raw transcript lines
- `l` — browse recorded session logs and open one read-only over the live pane
- `a` — force-answer the most recent utterance
- `s` — cycle strict, balanced, and eager gate modes
- `1` — mute or resume microphone input without changing system audio or voice output
- `2` — mute or resume system-audio input without changing the microphone or voice output
- `x` — choose or disable a standing context profile
- `d` — compare every microphone and loopback endpoint with live meters, then select one
- `q` — quit

The audio-device picker pauses the main capture streams while it is open, probes every
endpoint concurrently in shared mode, and restarts capture immediately when it closes. Devices held by another
application remain visible as unavailable without affecting the other meters. For the same
workflow outside the TUI:

```powershell
.\.venv\Scripts\python.exe scripts\pick_mic.py
.\.venv\Scripts\python.exe scripts\pick_mic.py --seconds 6 --list
```

On macOS, use `.venv-macos/bin/python scripts/pick_mic.py` (with the same optional arguments).
It automatically reads and updates `config.macos.toml`; `--config PATH` can select a different
overlay explicitly.

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

On macOS, use `.venv-macos/bin/python scripts/render_session.py`.

With no argument it renders the newest session; pass a `logs\session-*.jsonl` path for a
specific one, and `-o` to choose the output location (default: the `.html` next to the
`.jsonl`). Run it from the project root — the no-argument form looks for `logs\` relative to
the working directory. The replay also shows what the live pane never had room for: every
rejection reason, which is where gate-tuning signal comes from.

## Running several copies

Use one live copy. PipeWire can technically multiplex the devices, but each application process
is a complete Whisper and Claude pipeline, so two panes duplicate questions, GPU memory, and paid
answer calls. Startup therefore holds a process-lifetime OS lock and refuses a fresh second
instance. `--allow-multiple` exists only for deliberate diagnostics and should not be used during
a demo.

## Configuration reference

All settings normally load from `config.toml` (commented in place); any key left out takes the
default defined in `ambientqa/config.py`. The macOS launcher passes `--config
config.macos.toml`; that small file uses `extends = "config.toml"`, so it overrides only
platform-specific devices/STT while inheriting the shared tuning.

| Section | Key | Meaning |
|---|---|---|
| `audio` | `backend` | `auto` (default) / `wasapi` / `pipewire` / `coreaudio` — capture stack; `auto` picks the platform's native one |
| | `mic_device`, `output_device` | Optional device-name substring |
| | `sample_rate`, `frame_ms`, `queue_size` | Capture format and bounded frame queue |
| | `silence_ms`, `pre_roll_ms` | Trailing silence and onset preservation |
| | `min_utterance_ms`, `max_utterance_s` | Segment discard and force-flush limits |
| | `silent_source_warn_s` | Flag an open-but-inaudible capture source in the status bar |
| `stt` | `model` | faster-whisper model name |
| | `device`, `compute_type`, `cpu_compute_type` | Windows/Linux prefer CUDA with CPU fallback; the Mac overlay deliberately sets CPU/int8 |
| | `queue_size`, `language` | Bounded utterance queue and optional language |
| | `vad_filter` | Retain faster-whisper's secondary residual-silence filter after streaming VAD; `true` by default |
| | `profile_hints` | Opt in to active-profile vocabulary/topic hints for Whisper; `false` by default because hints can bias unrelated speech |
| | `hallucination_blocklist` | Normalized exact phrases discarded after STT |
| `context` | `profile` | Markdown profile path; empty means no active profile or profile-specific knowledge pack. The Mac overlay starts empty; press `x` to choose one |
| | `enabled` | Master switch for profile influence |
| `knowledge` | `enabled`, `path` | Enable the local pre-answered knowledge pack and set its fallback directory. The shared default remains enabled even while the Mac profile starts empty |
| | `hit_threshold`, `min_query_words` | Minimum score and length after cache specificity checks: every verbatim hit requires an exact normalized content-token-set match to one canonical question or alias, and ambiguous matches go live |
| | `ground_on_miss`, `retrieve_k`, `grounding_threshold` | On a cache miss, inject at most this many entries only when one canonical question or alias has the same normalized content-token set and clears the score floor (default 0.30) |
| `gate` | `model`, `ollama_url`, `request_timeout_s` | Local classifier connection |
| | `channel_policy` | Per-channel `full` / `explicit` / `off` gating freedom |
| | `max_concurrent` | Utterances judged at once, off the ordered consumer path |
| | `mode` | `strict`, `balanced`, or `eager` prompt policy |
| | `min_words`, `context_turns` | Heuristic minimum and semantic context |
| | `reask_cooldown_s` | Near-duplicate dedupe horizon: inside it a repeat is a mechanical duplicate and is dropped; past it, re-asking an already-answered question gets a fresh answer |
| | `dedupe_window_s`, `dedupe_ratio` | Recent answered-question suppression |
| | `echo_window_s`, `echo_ratio` | Cross-channel transcript echo suppression |
| | `answer_echo_window_s`, `answer_echo_ratio` | Rehearsal suppression — see "Rehearsing answers aloud" |
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
| | `history_turns` | Completed Q&A pairs carried into each new answer so follow-ups ("elaborate on the second method") resolve against what was actually answered; 0 disables |
| | `verify`, `verify_context_turns` | Opt-in second-pass audit of each delivered answer with a wider transcript window: replaces the card (marked "revised") only when the answer was materially wrong. `"off"` by default; `"always"` adds one Claude call per answer |
| | `sweep`, `sweep_interval_s`, `sweep_max_age_s`, `sweep_model` | Default-on (`"always"`) detection second pass: every 25 s it re-judges recent judgment-stage rejections with a small model. Candidates older than 60 s are discarded; `"off"` disables it |
| `tts` | `engine` | `kokoro` (neural, local model) or `espeak` (fallback selection); if Kokoro cannot load, Ambient selects espeak. macOS setup installs and probes the external `espeak-ng` executable before declaring the environment ready. An instance speaks only when launched with `--voice` |
| | `voice`, `speed` | Kokoro voice name and rate multiplier |
| | `speak` | Normal-mode baseline: `first_line` speaks a cue answer's opening line; `full` speaks the whole code-stripped answer. Voice key `r` temporarily overrides this to `full` together with spoken-prose answer style |
| | `speak_channels` | Channels whose accepted questions are spoken (default `["mic"]` — see "Voice mode") |
| | `mute_channels` | Channels every instance drops while any instance speaks (default both) |
| | `queue_size`, `max_age_s` | Unspoken backlog bound and staleness cutoff — late answers are shown, not spoken |
| | `gate_tail_s` | Mute hold after playback ends (sink latency + room decay) |
| | `model_path`, `voices_path` | Kokoro model file locations, relative to `config.toml` |
| `ui` | `show_transcripts` | Initial raw-transcript visibility |
| | `log_dir`, `status_interval_s` | Session output directory and status refresh |
| | `feed_direction` | `"top"` shows the newest entry first (default); `"bottom"` appends chronologically |

All bounded queues drop the oldest item on overflow. Capture producers never wait on a consumer.
The default `audio.silence_ms` is 900 ms. Raising it further can reduce splitting during longer
thinking pauses, but delays transcription and answers by the same additional silence.

Context profiles live in `profiles/*.md` and may contain optional `## Topic`,
`## Background`, and `## Vocabulary` sections. Agent sessions may additionally use
`## Customer Channel` (`mic` or `sys`) and `## Greeting`; the Agent/Q&A role itself is selected
at runtime and is not controlled by profile metadata.
When the deliberately opt-in `stt.profile_hints = true`, Vocabulary biases Whisper spelling and a
short Topic hint reaches STT. With the safe default `false`, neither is sent to Whisper. Topic helps
the gate resolve words already present in an utterance, and Topic plus Background tune answer
depth. Profile context never filters topics or turns contextless speech into a question. Press
`x` to switch profiles live; the selection is saved to `context.profile`.

## Troubleshooting

**Status bar shows `sys:SILENT 60s ⚠`:** the loopback opened fine but is carrying nothing. If you
have pinned `audio.output_device`, clear it — blank watches every endpoint and follows the one with
speech on it, which removes this failure entirely. If it is already blank, nothing is playing
through *any* output endpoint: check that the call is not muted and that the OS is routing it
somewhere. Press `d` for live meters; the right endpoint is the one that moves when the other
person speaks. Tune the delay with `audio.silent_source_warn_s` (default 45 s).

**No loopback device:** run the platform interpreter's device list command (on macOS,
`.venv-macos/bin/python scripts/list_devices.py`) to see the endpoints. A pinned name
that matches nothing no longer drops you to microphone-only — it falls back to the platform's
automatic loopback candidates and warns. On macOS that means watching every recognized virtual
input, because CoreAudio has no default recordable output mapping. If no
endpoint can be opened at all, the status bar warns and the app continues microphone-only.
On macOS, install BlackHole 2ch and configure the Multi-Output Device described in Quickstart;
physical CoreAudio outputs are not recordable inputs by themselves.

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
active device, and the app follows the platform default, which is often not the one you speak into.
Set `audio.mic_device` in the launcher's config to any case-insensitive substring of the device
you want (for example `"Logitech"`), using the names from the platform device-list command above.
On macOS, prefer the `d` picker so it writes `config.macos.toml` rather than the shared config.

**Mic transcription is garbled (Linux):** the microphone is almost certainly clipping. The raw
USB mic at full PipeWire volume runs ~34 dB above its hardware-neutral level; loud speech clipped
1.7% of samples, and Whisper garbles clipped audio. `run.sh` loads PipeWire's
`module-echo-cancel` to provide `ec_mic` — WebRTC noise suppression plus
automatic gain control, the class of processing Windows applies in its own audio stack — and `config.toml` pins
`audio.mic_device` to it. If the app reports the mic unavailable, the module did not load: rerun
`./run.sh`, or press `d` and pick the raw device (then turn its volume down).

**Whisper says `cpu`:** this is expected on macOS, where `config.macos.toml` deliberately pins CPU
`int8` before model loading. On Windows or Linux it means CUDA/model initialization failed and
faster-whisper fell back to CPU. Read the warning text first. If it says GPU memory is exhausted,
close GPU-heavy games, another Whisper/dictation service, or unused Ollama models and relaunch. On
the measured Windows/Linux GPU-class hosts, CPU fallback was not accepted for demo latency; Mac CPU
latency remains a real-hardware acceptance item. Otherwise check the NVIDIA driver and the installed
`nvidia-cublas-cu12`/`nvidia-cudnn-cu12` packages. The status bar deliberately makes this fallback
prominent because it is slower.

**Ollama is not running:** start Ollama, confirm `ollama list` includes the model named in
`[gate] model` (a model that is configured but not pulled fails the same way), then restart
the app. On macOS, ensure the `ollama` CLI is on `PATH`; the launcher starts its own private
server even if the desktop Ollama service is already running. Until the local gate is available, explicit interrogatives and clear commands on an
`explicit` channel still use their zero-call paths. Uncertain utterances and command-form
candidates on a `full` channel fail closed.

**Gate responses are empty:** `gemma4:e2b` (the default gate model) is a reasoning model. Every Ollama request must contain
top-level `"think": false`; otherwise `message.content` can be empty. Both application requests
and the warmup include it. Remote/proxied gate endpoints are deliberately rejected: `ollama_url`
must be a literal HTTP loopback `/api/chat` URL, and the client ignores proxy environment variables.

**Claude answers time out or fail:** run `claude -p "hello"` to confirm the CLI is installed and
authenticated. Each question intentionally uses a separate one-shot process and is killed after
`answer.answer_timeout_s`; concurrent questions are limited by `answer.max_concurrent`. Account and
rate-limit text emitted only in Claude's stream output is surfaced on the answer card rather than
being collapsed into the generic `answer failed` message.

**An answer appeared, then changed:** that is the optional audit, not a glitch. With
`answer.verify = "always"` every delivered answer is re-read by a second pass with
a wider transcript window and the full Q&A history, and replaced only when it was materially
wrong — the card is prefixed `revised` and the session log records `verify_revision`. Style is
never grounds for a revision. The default is `answer.verify = "off"` to keep first answers
untouched and avoid doubling Claude calls.

**A question was answered late, well after it was asked:** the optional sweeper recovered it. The gate
had rejected it, and the periodic second pass (`answer.sweep`, every `sweep_interval_s` = 25 s)
re-judged the recent judgment-stage rejections against wide context and decided it was a genuine
ask; the card arrives with `gate_reason: second_pass_recovery` in the log. Up to ~25 s of lag is
the design — a late answer beats a missed one. It is enabled by default; the status bar shows
`sweep:on` (set `answer.sweep = "off"` to disable it).

**Everything used to crash the moment you first spoke (Linux — fixed):** Whisper's first model
load spawned the multiprocessing resource tracker after Textual had replaced stderr with a
capture whose `fileno()` is -1; the spawn died with `bad value(s) in fds_to_keep` and every
transcription failed, CUDA and the CPU fallback alike. The tracker is now started before Textual
takes the terminal. Relatedly, nothing logs to stderr any more — inside a TUI a traceback
painted over the interface only makes the app look dead — so when something does go wrong, the
details are in `logs/ambientqa.log`.
