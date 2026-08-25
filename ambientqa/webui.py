"""Opt-in web console: the live pane rendered in a browser (``--web``).

This is deliberately a separate, secondary surface. The Textual pane stays the
default and the demo baseline; the web console only runs when launched with
``--web``, duck-types the same application interface the controller already
talks to (``add_transcript``, ``add_question``, ``append_answer_delta``,
``resolve_answer``, ``notify``, ``run_async`` …), and touches nothing in the
pipeline. If it misbehaves, launching without ``--web`` — or the pinned
``run-emergency.sh`` build, which predates this file entirely — is unaffected.

It is stdlib-only on purpose: adding a web framework to requirements.txt would
re-run pip for every launcher (run.sh reinstalls whenever requirements.txt is
newer than its stamp) and put a new dependency on the demo's critical path.
One ThreadingHTTPServer serves the static console and a Server-Sent-Events
stream; commands come back as small POSTs that are marshalled onto the
controller's event loop. The server binds 127.0.0.1 only — transcripts are
private and must not be served to the network.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict, deque
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .bus import AnswerResult, Transcript
from .ui import UIController, load_session_records

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "webstatic"
DEFAULT_WEB_PORT = 8802
_PORT_FALLBACK_ATTEMPTS = 20

_SESSION_NAME_RE = re.compile(r"^session-\d{8}-\d{6}\.jsonl$")

# Bounds on what the server remembers for page loads/refreshes. The browser
# only ever replays this snapshot; the JSONL session log remains the record.
_MAX_TRANSCRIPTS = 300
_MAX_CARDS = 80
_MAX_DECISIONS = 100

# The device picker stops main capture while it is open. A browser tab that
# navigates away mid-pick would leave the app deaf, so the picker self-closes
# unless the page keeps pinging.
_DEVICE_PICKER_TTL_S = 30.0


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


class EventHub:
    """Thread-safe fan-out of JSON events to every connected SSE client."""

    def __init__(self, max_client_backlog: int = 2000) -> None:
        self._lock = threading.Lock()
        self._clients: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        self._max_backlog = max_client_backlog

    def subscribe(self) -> tuple[int, queue.Queue[dict[str, Any]]]:
        with self._lock:
            self._next_id += 1
            client_id = self._next_id
            client: queue.Queue[dict[str, Any]] = queue.Queue()
            self._clients[client_id] = client
            return client_id, client

    def unsubscribe(self, client_id: int) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            # A stalled reader must never grow without bound; drop its oldest.
            while client.qsize() >= self._max_backlog:
                with suppress(queue.Empty):
                    client.get_nowait()
            client.put(event)


class WebUIApp:
    """Duck-typed stand-in for AmbientQAApp that renders into a browser.

    The controller calls the same handful of methods it calls on the Textual
    app; every one of them turns into an SSE event plus a bounded in-memory
    snapshot so a fresh page load starts populated.
    """

    def __init__(
        self,
        controller: UIController,
        host: str = "127.0.0.1",
        port: int = DEFAULT_WEB_PORT,
        open_browser: bool = False,
        allow_port_fallback: bool = True,
    ) -> None:
        self.controller = controller
        self.host = host
        self.requested_port = port
        self.port = port
        self.open_browser = open_browser
        self.hub = EventHub()
        self.is_running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._exit_event: asyncio.Event | None = None
        self._status_task: asyncio.Task[None] | None = None

        self._state_lock = threading.Lock()
        self._transcripts: deque[dict[str, Any]] = deque(maxlen=_MAX_TRANSCRIPTS)
        self._cards: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._decisions: deque[dict[str, Any]] = deque(maxlen=_MAX_DECISIONS)
        self._last_status: dict[str, Any] = {}

        self._meter_session: Any | None = None
        self._meter_deadline = 0.0
        self._meter_task: asyncio.Task[None] | None = None

        # Every pipeline decision — accepts and, crucially, every rejection
        # with its reason — flows through the session logger. Tee it so the
        # console's gate-decision panel sees the same record the JSONL gets,
        # without adding a second reporting path to the pipeline.
        logger = getattr(controller, "logger", None)
        if logger is not None:
            original_append = logger.append

            def _tee_append(record: dict[str, Any]) -> None:
                original_append(record)
                try:
                    self._on_log_record(record)
                except Exception:  # never let UI mirroring break the log
                    log.exception("web console could not mirror a log record")

            logger.append = _tee_append  # type: ignore[method-assign]

        # Bind the socket NOW, in the constructor, while stderr is still a
        # readable terminal and before any model loads. Desktop launches may
        # safely move to a nearby port when another local app owns the default;
        # an explicitly pinned --web-port remains fail-fast.
        handler = _build_handler(self)
        candidates = [port]
        if allow_port_fallback and port != 0:
            candidates.extend(
                range(port + 1, min(65535, port + _PORT_FALLBACK_ATTEMPTS) + 1)
            )
        last_busy: OSError | None = None
        for candidate in candidates:
            try:
                self._server = ThreadingHTTPServer((host, candidate), handler)
                break
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE or not allow_port_fallback:
                    raise
                last_busy = exc
        else:
            assert last_busy is not None
            raise OSError(
                errno.EADDRINUSE,
                f"web console ports {candidates[0]}-{candidates[-1]} are in use",
            ) from last_busy
        self._server.daemon_threads = True
        # port=0 asks the OS for an ephemeral port (used by tests); reflect
        # whatever was actually bound.
        self.port = int(self._server.server_address[1])
        if port != 0 and self.port != port:
            message = f"Web console port {port} is busy; using {self.port} instead"
            report = getattr(controller, "_report", None)
            if callable(report):
                report(message)
            else:
                log.warning(message)
        self._server_thread: threading.Thread | None = None

    def _wait_until_ready(self, timeout_s: float = 5.0) -> bool:
        """Confirm this exact service is answering before opening a browser."""
        health_url = f"http://{self.host}:{self.port}/api/health"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=0.25) as response:
                    payload = json.loads(response.read())
                if (
                    response.status == 200
                    and isinstance(payload, dict)
                    and payload.get("service") == "ambientqa"
                ):
                    return True
            except (
                OSError,
                TimeoutError,
                urllib.error.URLError,
                json.JSONDecodeError,
                ValueError,
            ):
                pass
            time.sleep(0.025)
        return False

    # ------------------------------------------------------------------ #
    # The application interface the controller calls.
    # ------------------------------------------------------------------ #

    def exit(self, *args: Any, **kwargs: Any) -> None:
        loop, event = self._loop, self._exit_event
        if loop is None or event is None:
            return
        if threading.get_ident() == self._loop_thread_id:
            event.set()
        else:
            loop.call_soon_threadsafe(event.set)

    def call_from_thread(self, fn: Callable[..., Any], *args: Any) -> None:
        loop = self._loop
        if loop is None:
            fn(*args)
        else:
            loop.call_soon_threadsafe(fn, *args)

    def call_later(self, fn: Callable[..., Any], *args: Any) -> None:
        loop = self._loop
        if loop is None:
            fn(*args)
        elif threading.get_ident() == self._loop_thread_id:
            loop.call_soon(fn, *args)
        else:
            loop.call_soon_threadsafe(fn, *args)

    def notify(self, message: str, **_kwargs: Any) -> None:
        self.hub.publish({"type": "notify", "message": str(message), "ts": time.time()})

    def add_warning(self, message: str) -> None:
        self.hub.publish({"type": "warning", "message": str(message), "ts": time.time()})

    async def add_transcript(self, transcript: Transcript) -> None:
        row = {
            "id": transcript.utterance_id,
            "channel": transcript.channel,
            "text": transcript.text,
            "ts": transcript.timestamp,
            "latency_ms": transcript.latency_ms,
        }
        with self._state_lock:
            # A continuity merge re-emits an utterance id with longer text;
            # replace in place like the TUI does.
            for existing in self._transcripts:
                if existing["id"] == row["id"]:
                    existing.update(row)
                    break
            else:
                self._transcripts.append(row)
        self.hub.publish({"type": "transcript", **row})

    async def add_question(self, question_id: str, question: str) -> None:
        card = {
            "id": question_id,
            "question": question,
            # Snapshot the role when the turn begins. A later profile/mode
            # change must not relabel an older Q&A card as customer dialogue.
            "agent_mode": bool(getattr(self.controller, "agent_mode", False)),
            "answer": "",
            "status": "answering",
            "ts": time.time(),
            "reason": "",
            "web_lookup": False,
            "latency_ms": None,
        }
        with self._state_lock:
            self._cards[question_id] = card
            while len(self._cards) > _MAX_CARDS:
                self._cards.popitem(last=False)
        self.hub.publish({"type": "question", **card})

    def append_answer_delta(self, question_id: str, delta: str) -> None:
        if not delta:
            return
        with self._state_lock:
            card = self._cards.get(question_id)
            if card is None or card["status"] not in ("answering", "streaming"):
                return
            card["status"] = "streaming"
            card["answer"] += delta
            total = len(card["answer"])
        # `len` lets a page that connected mid-stream detect a missed delta
        # and resync from the snapshot instead of showing spliced text.
        self.hub.publish(
            {"type": "delta", "id": question_id, "delta": delta, "len": total}
        )

    def resolve_answer(self, result: AnswerResult) -> None:
        with self._state_lock:
            card = self._cards.get(result.question_id)
            if card is None:
                card = {
                    "id": result.question_id,
                    "question": result.question,
                    "agent_mode": bool(
                        getattr(self.controller, "agent_mode", False)
                    ),
                    "ts": result.timestamp,
                    "reason": "",
                }
                self._cards[result.question_id] = card
            card.update(
                {
                    "answer": result.answer,
                    "status": result.status,
                    "latency_ms": result.latency_ms,
                    "web_lookup": bool(result.searched),
                }
            )
            payload = dict(card)
        self.hub.publish({"type": "answer", **payload})

    async def run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._exit_event = asyncio.Event()
        self.is_running = True
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="ambientqa-web-server",
            daemon=True,
        )
        self._server_thread.start()
        url = f"http://{self.host}:{self.port}"
        # In web mode the terminal is not owned by Textual, so this is
        # actually readable — it is the one line the user needs.
        print(f"Ambient web console: {url}  (Ctrl+C or the console's Quit to stop)")
        log.info("web console listening on %s", url)
        if self.open_browser:
            # Launched from the mode picker / app menu there is no visible
            # terminal to copy the URL from. webbrowser shells out (xdg-open)
            # and can block for a moment, so it runs off the event loop; a
            # failure only costs the convenience, never the console.
            def _open() -> None:
                import webbrowser

                if not self._wait_until_ready():
                    message = (
                        "Web console started but did not become ready; open "
                        f"{url} after checking logs/ambientqa.log"
                    )
                    log.error(message)
                    print(message)
                    return
                try:
                    opened = webbrowser.open(url)
                    if opened is False:
                        message = f"Could not open the browser automatically; open {url}"
                        log.warning(message)
                        print(message)
                except Exception as exc:
                    log.exception("could not open a browser for the web console")
                    print(f"Could not open the browser automatically ({exc}); open {url}")

            threading.Thread(
                target=_open, name="ambientqa-web-open-browser", daemon=True
            ).start()
        self._status_task = asyncio.create_task(self._status_loop())
        try:
            await self._exit_event.wait()
        finally:
            self.is_running = False
            if self._status_task is not None:
                self._status_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._status_task
            await self._devices_close(None)
            self.hub.publish({"type": "shutdown", "ts": time.time()})
            await asyncio.to_thread(self._server.shutdown)
            self._server.server_close()

    # ------------------------------------------------------------------ #
    # Status heartbeat.
    # ------------------------------------------------------------------ #

    async def _status_loop(self) -> None:
        interval = 0.5
        with suppress(Exception):
            interval = float(self.controller.config.ui.status_interval_s)  # type: ignore[attr-defined]
        while True:
            try:
                status = self._build_status()
                with self._state_lock:
                    self._last_status = status
                self.hub.publish({"type": "status", **status})
            except Exception:
                log.exception("web console status tick failed")
            await asyncio.sleep(interval)

    def _build_status(self) -> dict[str, Any]:
        c: Any = self.controller
        status: dict[str, Any] = {"ts": time.time()}

        def grab(key: str, fn: Callable[[], Any], default: Any = None) -> None:
            try:
                status[key] = fn()
            except Exception:
                status[key] = default

        grab("paused", lambda: bool(c.paused), False)
        grab("agent_mode", lambda: bool(c.agent_mode), False)
        grab(
            "agent_customer_channel",
            lambda: str(c._agent_customer_channel),
            "mic",
        )
        grab(
            "mic_enabled",
            lambda: bool(c.input_channel_enabled("mic")),
            True,
        )
        grab(
            "sys_enabled",
            lambda: bool(c.input_channel_enabled("sys")),
            True,
        )
        grab("mic", lambda: c._source_status(c.capture.mic), "?")
        grab("sys", lambda: c._source_status(c.capture.loopback), "?")
        grab("mic_detail", lambda: c.capture.mic.detail or "", "")
        grab("sys_detail", lambda: c.capture.loopback.detail or "", "")
        grab("whisper", lambda: c.transcriber.device, "?")
        grab("gate_mode", lambda: c.config.gate.mode, "?")
        grab("sweep", lambda: "on" if c.config.answer.sweep == "always" else "off", "?")
        grab("verify", lambda: "on" if c.config.answer.verify == "always" else "off", "?")
        # This tick doubles as the instance heartbeat, exactly as the TUI's
        # status refresh does — the emergency launcher checks these files.
        grab("instances", lambda: c.instances.heartbeat_and_count(), 1)
        grab("profile", lambda: c.profile.name if c.profile is not None else "none", "none")
        grab(
            "queues",
            lambda: (
                f"{c.frames.qsize()}/{c.utterances.qsize()}/"
                f"{c.transcripts.qsize()}/{c.answers.qsize()}"
            ),
            "?",
        )
        grab("answers_active", lambda: c.answerer.in_flight, 0)
        grab("answers_done", lambda: c.answer_count, 0)
        grab("tokens", lambda: c.estimated_tokens, 0)
        grab("warning", lambda: c.warnings[-1] if c.warnings else "", "")
        grab("status_note", lambda: c.status_note, "")
        grab("voice_enabled", lambda: bool(c.voice_enabled), False)
        grab(
            "voice",
            lambda: (
                ""
                if c.speech is None
                else (
                    "muted"
                    if c.speech.muted
                    else ("speaking" if c.speech.speaking else "on")
                )
            ),
            "",
        )
        grab("delivery", lambda: getattr(c, "interaction_mode", "normal"), "normal")
        return status

    def _config_summary(self) -> dict[str, Any]:
        c: Any = self.controller
        try:
            cfg = c.config
            return {
                "stt_model": cfg.stt.model,
                "stt_device": cfg.stt.device,
                "gate_model": cfg.gate.model,
                "gate_mode": cfg.gate.mode,
                "channel_policy": dict(cfg.gate.channel_policy),
                "answer_model": cfg.answer.answer_model,
                "answer_style": cfg.answer.style,
                "max_words": cfg.answer.max_words,
                "web_lookup": cfg.answer.web_lookup,
                "verify": cfg.answer.verify,
                "sweep": cfg.answer.sweep,
                "sweep_interval_s": cfg.answer.sweep_interval_s,
                "max_concurrent": cfg.answer.max_concurrent,
                "history_turns": cfg.answer.history_turns,
                "log_dir": str(cfg.ui.log_dir),
                "show_transcripts": bool(cfg.ui.show_transcripts),
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------ #
    # Log-record tee → gate-decision panel + card badges.
    # ------------------------------------------------------------------ #

    def _on_log_record(self, record: dict[str, Any]) -> None:
        if record.get("gate"):
            # An accepted question already has a live card (matched by id);
            # attach the gate reason and lookup flag so the card can show
            # FORCED / LATE / LOOKUP badges and the decision provenance.
            update: dict[str, Any] = {}
            with self._state_lock:
                card = self._cards.get(str(record.get("id")))
                if card is not None:
                    card["reason"] = str(record.get("gate_reason") or "")
                    if record.get("web_lookup"):
                        card["web_lookup"] = True
                    latencies = record.get("latencies_ms")
                    if isinstance(latencies, dict):
                        card["latencies_ms"] = latencies
                    update = dict(card)
            if update:
                self.hub.publish({"type": "card_meta", **update})
            return
        decision = {
            "id": record.get("id"),
            "channel": record.get("channel"),
            "text": record.get("text"),
            "reason": record.get("gate_reason"),
            "ts": record.get("timestamp"),
        }
        with self._state_lock:
            self._decisions.append(decision)
        self.hub.publish({"type": "decision", **decision})

    # ------------------------------------------------------------------ #
    # Snapshot for page loads.
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            state = {
                "transcripts": list(self._transcripts),
                "cards": list(self._cards.values()),
                "decisions": list(self._decisions),
                "status": dict(self._last_status),
            }
        profiles: list[str] = []
        active_profile = ""
        with suppress(Exception):
            profiles, active_profile = self.controller.profile_choices()
        state.update(
            {
                "config": self._config_summary(),
                "profiles": profiles,
                "active_profile": active_profile,
                "voice_enabled": bool(getattr(self.controller, "voice_enabled", False)),
                "paused": bool(getattr(self.controller, "paused", False)),
            }
        )
        return state

    # ------------------------------------------------------------------ #
    # Commands (arrive on HTTP handler threads).
    # ------------------------------------------------------------------ #

    def dispatch_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        loop = self._loop
        if loop is None:
            return {"ok": False, "error": "not running"}
        future = asyncio.run_coroutine_threadsafe(self._do_command(payload), loop)
        return future.result(timeout=60)

    async def _do_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        c = self.controller
        action = str(payload.get("action", ""))
        if action == "pause":
            paused = c.toggle_pause()
            # Push a fresh status immediately: the pause chrome must flip on
            # the keypress, not on the next half-second tick.
            self.hub.publish({"type": "status", **self._build_status()})
            return {"ok": True, "paused": paused}
        if action == "input":
            channel = str(payload.get("channel", ""))
            if channel not in {"mic", "sys"}:
                return {"ok": False, "error": "channel must be mic or sys"}
            enabled = c.toggle_input_channel(channel)
            label = "Microphone" if channel == "mic" else "System audio"
            self.notify(f"{label} input {'listening' if enabled else 'muted'}")
            status = self._build_status()
            self.hub.publish({"type": "status", **status})
            return {
                "ok": True,
                "channel": channel,
                "enabled": enabled,
                "status": status,
            }
        if action == "force_answer":
            await c.force_answer_last()
            return {"ok": True}
        if action == "strictness":
            mode = c.cycle_gate_mode()
            self.notify(f"Gate mode: {mode}")
            return {"ok": True, "mode": mode}
        if action == "voice":
            message = c.toggle_voice()
            self.notify(message)
            status = self._build_status()
            self.hub.publish({"type": "status", **status})
            return {"ok": True, "message": message, "status": status}
        if action == "agent":
            message = c.toggle_agent_mode()
            self.notify(message)
            status = self._build_status()
            self.hub.publish({"type": "status", **status})
            return {"ok": True, "message": message, "status": status}
        if action == "conversation":
            message = c.toggle_interaction_mode()
            self.notify(message)
            status = self._build_status()
            self.hub.publish({"type": "status", **status})
            return {"ok": True, "message": message, "status": status}
        if action == "profile":
            value = str(payload.get("value", ""))
            name = await c.select_profile(value)
            if not value:
                self.notify("Context profile disabled")
            elif name == "none":
                self.add_warning("Context profile is unavailable or globally disabled")
            else:
                self.notify(f"Profile active: {name}")
            status = self._build_status()
            self.hub.publish({"type": "status", **status})
            return {"ok": True, "profile": name, "status": status}
        if action == "quit":
            self.notify("Shutting down")
            self.exit()
            # The browser waits for this acknowledgement before attempting to
            # close its own tab. Keep the response explicit so a failed POST
            # never makes the page disappear while capture is still running.
            return {"ok": True, "close": True}
        if action == "devices_open":
            return await self._devices_open()
        if action == "devices_ping":
            self._meter_deadline = time.monotonic() + _DEVICE_PICKER_TTL_S
            return {"ok": True}
        if action == "devices_close":
            index = payload.get("selected")
            await self._devices_close(index if isinstance(index, int) else None)
            return {"ok": True}
        return {"ok": False, "error": f"unknown action: {action}"}

    # ------------------------------------------------------------------ #
    # Audio device picker. Main capture is stopped while a session is open,
    # so this is guarded by a ping deadline: an abandoned picker closes
    # itself and restarts capture rather than leaving the app deaf.
    # ------------------------------------------------------------------ #

    async def _devices_open(self) -> dict[str, Any]:
        if self._meter_session is not None:
            return {"ok": False, "error": "device picker already open"}
        session = await self.controller.open_audio_devices()
        self._meter_session = session
        self._meter_deadline = time.monotonic() + _DEVICE_PICKER_TTL_S
        self._meter_task = asyncio.create_task(self._meter_loop(session))
        devices = [
            {
                "index": index,
                "kind": device.kind,
                "name": device.name,
                "display_name": getattr(device, "display_name", device.name),
                "active": _matches_active(
                    device,
                    session.active_mic
                    if device.kind == "mic"
                    else session.active_loopback,
                ),
            }
            for index, device in enumerate(session.devices)
        ]
        return {"ok": True, "devices": devices}

    async def _meter_loop(self, session: Any) -> None:
        try:
            while self._meter_session is session:
                if time.monotonic() > self._meter_deadline:
                    await self._devices_close(None)
                    self.add_warning(
                        "Audio device picker closed automatically (page stopped responding)"
                    )
                    return
                readings = session.snapshot()
                payload = []
                for index, device in enumerate(session.devices):
                    reading = readings.get(device.key)
                    if reading is None:
                        payload.append({"index": index})
                        continue
                    payload.append(
                        {
                            "index": index,
                            "bar": reading.bar,
                            "peak_db": round(reading.peak_db, 1),
                            "rms_db": round(reading.rms_db, 1),
                            "unavailable": reading.unavailable,
                        }
                    )
                self.hub.publish({"type": "meters", "readings": payload})
                await asyncio.sleep(0.12)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("device meter loop failed")
            self.add_warning(f"Audio device meters failed: {exc}")

    async def _devices_close(self, selected_index: int | None) -> None:
        session = self._meter_session
        if session is None:
            return
        self._meter_session = None
        selected = None
        if selected_index is not None and 0 <= selected_index < len(session.devices):
            selected = session.devices[selected_index]
        try:
            await self.controller.close_audio_devices(session, selected)
        finally:
            self.hub.publish({"type": "devices_closed", "ts": time.time()})
            if selected is not None:
                self.notify(
                    ("Selected microphone: " if selected.kind == "mic" else "Selected system audio: ")
                    + selected.name
                )

    # ------------------------------------------------------------------ #
    # Session logs.
    # ------------------------------------------------------------------ #

    def _log_dir(self) -> Path:
        with suppress(Exception):
            return Path(self.controller.config.ui.log_dir)  # type: ignore[attr-defined]
        return Path("logs")

    def list_sessions(self) -> list[dict[str, Any]]:
        directory = self._log_dir()
        if not directory.is_dir():
            return []
        sessions = []
        for path in sorted(directory.glob("session-*.jsonl"), reverse=True):
            try:
                stat = path.stat()
            except OSError:
                continue
            sessions.append(
                {"name": path.name, "mtime": stat.st_mtime, "size": stat.st_size}
            )
        return sessions

    def load_session(self, name: str) -> list[dict[str, Any]] | None:
        # The name is matched against the session pattern, never joined
        # freely: this endpoint must not read arbitrary files.
        if not _SESSION_NAME_RE.match(name):
            return None
        path = self._log_dir() / name
        if not path.is_file():
            return None
        return load_session_records(path)


def _matches_active(device: Any, active_name: str) -> bool:
    if not active_name:
        return False
    device_name = device.name.casefold()
    active = active_name.casefold()
    return active in device_name or device_name in active


def _build_handler(app: WebUIApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Silence per-request stderr noise; failures go to the module log.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.debug("web: " + format, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, name: str, content_type: str) -> None:
            path = STATIC_DIR / name
            try:
                body = path.read_bytes()
            except OSError:
                self._send_json({"error": "not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            try:
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    self._send_static("index.html", "text/html; charset=utf-8")
                elif path == "/static/app.css":
                    self._send_static("app.css", "text/css; charset=utf-8")
                elif path == "/static/app.js":
                    self._send_static("app.js", "text/javascript; charset=utf-8")
                elif path == "/events":
                    self._serve_events()
                elif path == "/api/health":
                    self._send_json(
                        {
                            "service": "ambientqa",
                            "status": "ok",
                            "port": app.port,
                        }
                    )
                elif path == "/api/state":
                    self._send_json(app.snapshot())
                elif path == "/api/sessions":
                    self._send_json({"sessions": app.list_sessions()})
                elif path.startswith("/api/session/"):
                    name = path.rsplit("/", 1)[-1]
                    records = app.load_session(name)
                    if records is None:
                        self._send_json({"error": "unknown session"}, 404)
                    else:
                        self._send_json({"name": name, "records": records})
                else:
                    self._send_json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("web request failed: %s", self.path)
                with suppress(Exception):
                    self._send_json({"error": "internal error"}, 500)

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path.split("?", 1)[0] != "/api/command":
                    self._send_json({"error": "not found"}, 404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    self._send_json({"ok": False, "error": "bad json"}, 400)
                    return
                if not isinstance(payload, dict):
                    self._send_json({"ok": False, "error": "bad payload"}, 400)
                    return
                try:
                    result = app.dispatch_command(payload)
                except Exception as exc:
                    log.exception("web command failed: %s", payload)
                    result = {"ok": False, "error": str(exc)}
                self._send_json(result)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("web command request failed")
                with suppress(Exception):
                    self._send_json({"error": "internal error"}, 500)

        def _serve_events(self) -> None:
            client_id, events = app.hub.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                hello = {"type": "hello", **app.snapshot()}
                self.wfile.write(b"data: " + _json_bytes(hello) + b"\n\n")
                self.wfile.flush()
                while app.is_running or not events.empty():
                    try:
                        event = events.get(timeout=15.0)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(b"data: " + _json_bytes(event) + b"\n\n")
                    self.wfile.flush()
                    if event.get("type") == "shutdown":
                        break
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                app.hub.unsubscribe(client_id)
                # SSE responses never carry Content-Length; the connection
                # cannot be reused, so make sure it closes.
                self.close_connection = True

    return Handler
