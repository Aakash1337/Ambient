"""The opt-in web console: duck-typing, event fan-out, and the HTTP surface.

The web console must be a faithful stand-in for the Textual app (the
controller calls the same methods on both) while never becoming a load-bearing
dependency of the default launch path — that path is covered by the existing
UI tests and must keep passing untouched.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ambientqa.bus import AnswerResult, Transcript
from ambientqa.config import default_config
from ambientqa.logging_ import SessionLogger
from ambientqa.webui import EventHub, WebUIApp


class _Capture:
    def __init__(self) -> None:
        self.enabled = {"mic": True, "sys": True}
        self.mic = SimpleNamespace(active=True, detail="test microphone")
        self.loopback = SimpleNamespace(active=True, detail="test system audio")

    def channel_enabled(self, channel: str) -> bool:
        return self.enabled[channel]


class StubController:
    """The minimum surface the web console reads or invokes."""

    def __init__(self, tmp_path: Path) -> None:
        self.config = default_config()
        self.config.ui.log_dir = str(tmp_path / "logs")
        self.logger = SessionLogger(self.config.ui.log_dir)
        self.paused = False
        self.agent_mode = False
        self._agent_customer_channel = "mic"
        self.voice_enabled = False
        self.speech: SimpleNamespace | None = None
        self.interaction_mode = "normal"
        self.warnings: list[str] = []
        self.status_note = "test"
        self.profile = None
        self.answer_count = 0
        self.estimated_tokens = 0
        self.calls: list[str] = []
        self.capture = _Capture()

    def toggle_pause(self) -> bool:
        self.calls.append("pause")
        self.paused = not self.paused
        return self.paused

    def cycle_gate_mode(self) -> str:
        self.calls.append("strictness")
        return "eager"

    def toggle_voice(self) -> str:
        self.calls.append("voice")
        if self.speech is None:
            return "voice mode is off"
        self.speech.muted = not self.speech.muted
        return "voice muted" if self.speech.muted else "voice on"

    def toggle_interaction_mode(self) -> str:
        self.calls.append("conversation")
        self.interaction_mode = (
            "conversational"
            if self.interaction_mode == "normal"
            else "normal"
        )
        return f"{self.interaction_mode} mode"

    def toggle_agent_mode(self) -> str:
        self.calls.append("agent")
        self.agent_mode = not self.agent_mode
        return "Agent role" if self.agent_mode else "Assist role"

    def toggle_input_channel(self, channel: str) -> bool:
        self.calls.append(f"input:{channel}")
        enabled = not self.input_channel_enabled(channel)
        self.capture.enabled[channel] = enabled
        return enabled

    def input_channel_enabled(self, channel: str) -> bool:
        return self.capture.channel_enabled(channel)

    async def force_answer_last(self) -> None:
        self.calls.append("force")

    def profile_choices(self) -> tuple[list[str], str]:
        return ["profiles/demo.md"], ""

    async def select_profile(self, value: str) -> str:
        self.calls.append(f"profile:{value}")
        return "demo" if value else "none"

    def _report(self, message: str) -> None:
        self.warnings.append(message)


def make_app(tmp_path: Path) -> tuple[WebUIApp, StubController]:
    controller = StubController(tmp_path)
    app = WebUIApp(controller, port=0)  # ephemeral port
    return app, controller


# --------------------------------------------------------------------- #
# Interface conformance
# --------------------------------------------------------------------- #


def test_webui_duck_types_the_app_interface(tmp_path: Path) -> None:
    """Every method AmbientController calls on its app must exist here."""
    app, _ = make_app(tmp_path)
    try:
        for name in (
            "exit",
            "call_from_thread",
            "call_later",
            "notify",
            "add_warning",
            "add_transcript",
            "add_question",
            "append_answer_delta",
            "resolve_answer",
            "run_async",
        ):
            assert callable(getattr(app, name)), name
        assert hasattr(app, "is_running")
    finally:
        app._server.server_close()


def test_call_helpers_work_without_a_loop(tmp_path: Path) -> None:
    """_report may fire during startup, before run_async has a loop."""
    app, _ = make_app(tmp_path)
    try:
        seen: list[int] = []
        app.call_from_thread(seen.append, 1)
        app.call_later(seen.append, 2)
        assert seen == [1, 2]
        app.exit()  # no loop yet: must be a no-op, not a crash
    finally:
        app._server.server_close()


# --------------------------------------------------------------------- #
# Event flow and snapshot
# --------------------------------------------------------------------- #


def test_transcripts_questions_and_answers_reach_snapshot(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    try:
        _, events = app.hub.subscribe()

        async def scenario() -> None:
            await app.add_transcript(
                Transcript("sys", "how does the gate work?", 100.0, "u1", 250.0)
            )
            await app.add_question("u1", "How does the gate work?")
            app.append_answer_delta("u1", "Two stages — ")
            app.append_answer_delta("u1", "heuristics, then a local model.")
            app.resolve_answer(
                AnswerResult("u1", "How does the gate work?", "Two stages.", "ok", 3100.0)
            )

        asyncio.run(scenario())

        snap = app.snapshot()
        assert snap["transcripts"][0]["text"] == "how does the gate work?"
        card = snap["cards"][0]
        assert card["status"] == "ok"
        assert card["answer"] == "Two stages."
        assert card["latency_ms"] == 3100.0

        types = []
        while not events.empty():
            types.append(events.get_nowait()["type"])
        assert types == ["transcript", "question", "delta", "delta", "answer"]
    finally:
        app._server.server_close()


def test_delta_events_carry_running_length(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    try:
        _, events = app.hub.subscribe()

        async def scenario() -> None:
            await app.add_question("q1", "Q?")
            app.append_answer_delta("q1", "abc")
            app.append_answer_delta("q1", "def")

        asyncio.run(scenario())
        events.get_nowait()  # question
        assert events.get_nowait()["len"] == 3
        assert events.get_nowait()["len"] == 6
    finally:
        app._server.server_close()


def test_merged_transcript_replaces_in_place(tmp_path: Path) -> None:
    """A continuity merge re-emits the same utterance id with longer text."""
    app, _ = make_app(tmp_path)
    try:

        async def scenario() -> None:
            await app.add_transcript(Transcript("mic", "so if the index", 1.0, "m1"))
            await app.add_transcript(
                Transcript("mic", "so if the index changes, what then?", 2.0, "m1")
            )

        asyncio.run(scenario())
        snap = app.snapshot()
        assert len(snap["transcripts"]) == 1
        assert snap["transcripts"][0]["text"].endswith("what then?")
    finally:
        app._server.server_close()


def test_logger_tee_mirrors_rejections_and_still_writes_jsonl(tmp_path: Path) -> None:
    app, controller = make_app(tmp_path)
    try:
        controller.logger.append(
            {
                "id": "u9",
                "timestamp": 123.0,
                "channel": "mic",
                "text": "we rolled it out to the team",
                "gate": False,
                "gate_reason": "not_a_direct_question",
                "answer": None,
            }
        )
        snap = app.snapshot()
        assert snap["decisions"][0]["reason"] == "not_a_direct_question"
        # The tee must never eat the actual log write.
        lines = controller.logger.path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["gate_reason"] == "not_a_direct_question"
    finally:
        app._server.server_close()


def test_accepted_record_attaches_reason_to_card(tmp_path: Path) -> None:
    app, controller = make_app(tmp_path)
    try:

        async def scenario() -> None:
            await app.add_question("u2", "What does it cost?")

        asyncio.run(scenario())
        controller.logger.append(
            {
                "id": "u2",
                "timestamp": 124.0,
                "channel": "sys",
                "text": "what does it cost?",
                "gate": True,
                "gate_reason": "second_pass_recovery",
                "answer": "…",
                "web_lookup": True,
                "latencies_ms": {
                    "stt": 824.0,
                    "continuity": 13_000.0,
                    "gate": 0.0,
                    "sweep_wait": 22_000.0,
                    "sweep": 18_000.0,
                    "answer": 900.0,
                },
            }
        )
        card = app.snapshot()["cards"][0]
        assert card["reason"] == "second_pass_recovery"
        assert card["web_lookup"] is True
        assert card["latencies_ms"]["continuity"] == 13_000.0
        assert card["latencies_ms"]["sweep_wait"] == 22_000.0
        assert card["latencies_ms"]["sweep"] == 18_000.0
    finally:
        app._server.server_close()


def test_web_card_exposes_recovery_stage_labels() -> None:
    source = (
        Path(__file__).parents[1] / "ambientqa" / "webstatic" / "app.js"
    ).read_text(encoding="utf-8")

    assert '"merge " + fmtMs(lat.continuity)' in source
    assert '"sweep wait " + fmtMs(lat.sweep_wait)' in source
    assert '"sweep " + fmtMs(lat.sweep)' in source
    assert '"total " + fmtMs(total)' in source


def test_event_hub_bounds_a_stalled_client() -> None:
    hub = EventHub(max_client_backlog=5)
    _, events = hub.subscribe()
    for index in range(50):
        hub.publish({"type": "status", "n": index})
    assert events.qsize() == 5
    drained = [events.get_nowait()["n"] for _ in range(5)]
    assert drained == [45, 46, 47, 48, 49]


# --------------------------------------------------------------------- #
# Session log guardrails
# --------------------------------------------------------------------- #


def test_session_endpoint_rejects_non_session_names(tmp_path: Path) -> None:
    app, controller = make_app(tmp_path)
    try:
        secret = tmp_path / "logs" / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        assert app.load_session("secret.txt") is None
        assert app.load_session("../config.toml") is None
        assert app.load_session("session-..-.jsonl") is None
        controller.logger.append({"id": "x", "timestamp": 1.0, "gate": False})
        name = controller.logger.path.name
        records = app.load_session(name)
        assert records is not None and records[0]["id"] == "x"
    finally:
        app._server.server_close()


# --------------------------------------------------------------------- #
# The live HTTP surface
# --------------------------------------------------------------------- #


@pytest.fixture()
def running_app(tmp_path: Path):
    app, controller = make_app(tmp_path)
    ready = threading.Event()
    loop_holder: dict[str, Any] = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        loop_holder["loop"] = loop

        async def run() -> None:
            task = asyncio.ensure_future(app.run_async())
            await asyncio.sleep(0)  # let run_async start the server
            ready.set()
            await task

        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert ready.wait(5)
    deadline = time.time() + 5
    while not app.is_running and time.time() < deadline:
        time.sleep(0.01)
    yield app, controller
    app.exit()
    thread.join(timeout=5)
    assert not thread.is_alive()


def _get(
    app: WebUIApp, path: str, *, authorized: bool = True
) -> tuple[int, bytes]:
    url = f"http://127.0.0.1:{app.port}{path}"
    request = urllib.request.Request(
        url,
        headers=(
            {"X-Ambient-Access-Token": app._access_token} if authorized else {}
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:  # type: ignore[attr-defined]
        return err.code, err.read()


def _get_url(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def _post(app: WebUIApp, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"http://127.0.0.1:{app.port}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Ambient-Access-Token": app._access_token,
            "X-Ambient-CSRF-Token": app._csrf_token,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_serves_console_and_state(running_app) -> None:
    app, _ = running_app
    capability_path = "/?access=" + urllib.parse.quote(app._access_token, safe="")
    status, body = _get(app, capability_path, authorized=False)
    assert status == 200 and b"AMBIENT" in body
    assert app._access_token.encode() in body
    assert app._csrf_token.encode() in body
    assert app._access_token != app._csrf_token
    assert b"__AMBIENT_ACCESS_TOKEN__" not in body
    assert b"__AMBIENT_CSRF_TOKEN__" not in body
    assert b"Ambient Q&amp;A" not in body
    assert b"Ambient Q&A" not in body
    assert b'id="btn-voice"' in body
    assert b'id="btn-delivery-normal"' in body
    assert b'id="btn-delivery-conversational"' in body
    assert b'id="btn-interaction-qa"' in body
    assert b'id="btn-interaction-agent"' in body
    assert b'id="btn-input-mic"' in body
    assert b'id="btn-input-sys"' in body
    assert b'id="agent-chip"' in body
    status, body = _get(app, "/static/app.js", authorized=False)
    assert status == 200 and b"EventSource" in body
    assert b"X-Ambient-Access-Token" in body
    assert b"X-Ambient-CSRF-Token" in body
    assert app._access_token.encode() not in body
    assert b"quitAndClose" in body and b"window.close()" in body
    assert b"AMBIENT REPLIES" in body
    assert b'toggleInput("mic")' in body
    assert b'case "g": runVoiceCommand("agent")' in body
    assert b'case "r": runVoiceCommand("conversation")' in body
    assert b"Ambient Q&A" not in body
    status, body = _get(app, "/static/app.css", authorized=False)
    assert status == 200 and b"--paper" in body
    status, body = _get(app, "/api/health", authorized=False)
    assert status == 200
    health = json.loads(body)
    assert health == {"service": "ambientqa", "status": "ok", "port": app.port}
    status, body = _get(app, "/api/state")
    assert status == 200
    snap = json.loads(body)
    assert snap["config"]["gate_model"]
    assert snap["profiles"] == ["profiles/demo.md"]
    status, _ = _get(app, "/../ambientqa/webui.py")
    assert status == 404


def test_sensitive_http_surfaces_reject_unauthenticated_local_clients(
    running_app,
) -> None:
    app, controller = running_app
    for path in (
        "/",
        "/?access=wrong",
        "/api/state",
        "/api/sessions",
        "/api/session/session-20260828-120000.jsonl",
        "/events",
    ):
        status, body = _get(app, path, authorized=False)
        assert status == 403, path
        assert json.loads(body) == {"error": "forbidden"}

    request = urllib.request.Request(
        f"http://127.0.0.1:{app.port}/api/command",
        data=b'{"action":"pause"}',
        headers={
            "Content-Type": "application/json",
            # Possessing CSRF alone must not cross the local-user boundary.
            "X-Ambient-CSRF-Token": app._csrf_token,
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as raised:  # type: ignore[attr-defined]
        urllib.request.urlopen(request, timeout=5)
    assert raised.value.code == 403
    assert controller.calls == []


def test_capability_query_is_never_written_to_web_logs(
    running_app, caplog: pytest.LogCaptureFixture
) -> None:
    app, _ = running_app
    caplog.set_level(logging.DEBUG, logger="ambientqa.webui")
    path = "/?access=" + urllib.parse.quote(app._access_token, safe="")
    status, _ = _get(app, path, authorized=False)
    assert status == 200
    assert app._access_token not in caplog.text


def test_command_endpoint_rejects_cross_site_and_untrusted_requests(
    running_app,
) -> None:
    app, controller = running_app
    url = f"http://127.0.0.1:{app.port}/api/command"

    def attempt(headers: dict[str, str]) -> int:
        request = urllib.request.Request(
            url,
            data=b'{"action":"pause"}',
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as err:  # type: ignore[attr-defined]
            return err.code

    access_header = {"X-Ambient-Access-Token": app._access_token}
    token_header = {
        **access_header,
        "X-Ambient-CSRF-Token": app._csrf_token,
    }
    assert attempt({"Content-Type": "text/plain", **access_header}) == 415
    assert attempt({"Content-Type": "application/json", **access_header}) == 403
    assert (
        attempt(
            {
                "Content-Type": "application/json",
                "X-Ambient-CSRF-Token": "not-the-session-token",
                **access_header,
            }
        )
        == 403
    )
    assert (
        attempt(
            {
                "Content-Type": "application/json",
                "Origin": "https://attacker.example",
                **token_header,
            }
        )
        == 403
    )
    assert (
        attempt(
            {
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{app.port + 1}",
                **token_header,
            }
        )
        == 403
    )
    assert (
        attempt(
            {
                "Content-Type": "application/json",
                "Host": f"attacker.example:{app.port}",
                **token_header,
            }
        )
        == 403
    )
    assert controller.calls == []


def test_command_endpoint_accepts_exact_localhost_origin(running_app) -> None:
    app, controller = running_app
    request = urllib.request.Request(
        f"http://127.0.0.1:{app.port}/api/command",
        data=b'{"action":"pause"}',
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Host": f"localhost:{app.port}",
            "Origin": f"http://localhost:{app.port}",
            "X-Ambient-Access-Token": app._access_token,
            "X-Ambient-CSRF-Token": app._csrf_token,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert json.loads(response.read())["paused"] is True
    assert controller.calls == ["pause"]


def test_http_surface_rejects_dns_rebinding_host(running_app) -> None:
    app, _ = running_app
    request = urllib.request.Request(
        f"http://127.0.0.1:{app.port}/api/state",
        headers={
            "Host": f"attacker.example:{app.port}",
            "X-Ambient-Access-Token": app._access_token,
        },
    )
    with pytest.raises(urllib.error.HTTPError) as raised:  # type: ignore[attr-defined]
        urllib.request.urlopen(request, timeout=5)
    assert raised.value.code == 403


def test_console_response_blocks_cross_origin_framing(running_app) -> None:
    app, _ = running_app
    url = (
        f"http://127.0.0.1:{app.port}/?access="
        + urllib.parse.quote(app._access_token, safe="")
    )
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"


def test_commands_reach_the_controller(running_app) -> None:
    app, controller = running_app
    assert _post(app, "/api/command", {"action": "pause"})["paused"] is True
    mic = _post(app, "/api/command", {"action": "input", "channel": "mic"})
    assert mic["enabled"] is False
    assert mic["status"]["mic_enabled"] is False
    assert mic["status"]["sys_enabled"] is True
    system = _post(app, "/api/command", {"action": "input", "channel": "sys"})
    assert system["enabled"] is False
    assert system["status"]["sys_enabled"] is False
    invalid = _post(app, "/api/command", {"action": "input", "channel": "speaker"})
    assert invalid == {"ok": False, "error": "channel must be mic or sys"}
    assert _post(app, "/api/command", {"action": "force_answer"})["ok"] is True
    assert _post(app, "/api/command", {"action": "strictness"})["mode"] == "eager"
    controller.voice_enabled = True
    controller.speech = SimpleNamespace(muted=False, speaking=False)
    voice = _post(app, "/api/command", {"action": "voice"})
    assert voice["status"]["voice_enabled"] is True
    assert voice["status"]["voice"] == "muted"
    conversation = _post(app, "/api/command", {"action": "conversation"})
    assert conversation["status"]["delivery"] == "conversational"
    agent = _post(app, "/api/command", {"action": "agent"})
    assert agent["ok"] is True
    assert agent["status"]["agent_mode"] is True
    # Role and spoken delivery are independent. Agent can use Normal too.
    normal = _post(app, "/api/command", {"action": "conversation"})
    assert normal["ok"] is True
    assert normal["status"]["delivery"] == "normal"
    result = _post(app, "/api/command", {"action": "profile", "value": "profiles/demo.md"})
    assert result["profile"] == "demo"
    assert result["status"]["agent_mode"] is True
    assert _post(app, "/api/command", {"action": "bogus"})["ok"] is False
    assert controller.calls == [
        "pause",
        "input:mic",
        "input:sys",
        "force",
        "strictness",
        "voice",
        "conversation",
        "agent",
        "conversation",
        "profile:profiles/demo.md",
    ]


def test_status_exposes_agent_and_independent_input_state(tmp_path: Path) -> None:
    app, controller = make_app(tmp_path)
    try:
        controller.agent_mode = True
        controller.capture.enabled = {"mic": False, "sys": True}

        status = app._build_status()

        assert status["agent_mode"] is True
        assert status["agent_customer_channel"] == "mic"
        assert status["mic_enabled"] is False
        assert status["sys_enabled"] is True
    finally:
        app._server.server_close()


def test_web_cards_snapshot_agent_role_at_creation(tmp_path: Path) -> None:
    app, controller = make_app(tmp_path)
    try:
        controller.agent_mode = True
        asyncio.run(app.add_question("customer-1", "Hello, I need help."))
        controller.agent_mode = False

        assert app.snapshot()["cards"][0]["agent_mode"] is True
    finally:
        app._server.server_close()


def test_sse_stream_sends_hello_then_events(running_app) -> None:
    app, _ = running_app
    url = (
        f"http://127.0.0.1:{app.port}/events?access="
        + urllib.parse.quote(app._access_token, safe="")
    )
    with urllib.request.urlopen(url, timeout=5) as stream:
        first = stream.readline().decode()
        assert first.startswith("data: ")
        hello = json.loads(first[len("data: "):])
        assert hello["type"] == "hello"
        assert "config" in hello
        # A published event arrives as the next data frame.
        app.hub.publish({"type": "notify", "message": "hi", "ts": 0.0})
        while True:
            line = stream.readline().decode()
            if line.startswith("data: "):
                event = json.loads(line[len("data: "):])
                assert event == {"type": "notify", "message": "hi", "ts": 0.0}
                break


def test_open_browser_flag_opens_the_console_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    controller = StubController(tmp_path)
    app = WebUIApp(controller, port=0, open_browser=True)

    async def run_briefly() -> None:
        task = asyncio.ensure_future(app.run_async())
        deadline = time.time() + 5
        while not opened and time.time() < deadline:
            await asyncio.sleep(0.02)
        app.exit()
        await task

    asyncio.run(run_briefly())
    assert opened == [
        f"http://127.0.0.1:{app.port}/?access="
        + urllib.parse.quote(app._access_token, safe="")
    ]


def test_readiness_probe_bypasses_environment_http_proxies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path)
    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"service": "ambientqa"}).encode()

    class Opener:
        def open(self, url: str, *, timeout: float):
            captured["url"] = url
            captured["timeout"] = timeout
            return Response()

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    try:
        assert app._wait_until_ready(timeout_s=0.1) is True
    finally:
        app._server.server_close()

    handlers = captured["handlers"]
    assert len(handlers) == 1
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    assert captured["url"] == f"http://127.0.0.1:{app.port}/api/health"
    assert captured["timeout"] == 0.25


def test_busy_default_falls_back_and_opens_our_actual_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import webbrowser

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    busy_port = int(blocker.getsockname()[1])
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    controller = StubController(tmp_path)
    app = WebUIApp(
        controller,
        port=busy_port,
        open_browser=True,
        allow_port_fallback=True,
    )

    async def run_briefly() -> None:
        task = asyncio.create_task(app.run_async())
        deadline = time.time() + 5
        while not opened and time.time() < deadline:
            await asyncio.sleep(0.02)
        status, body = await asyncio.to_thread(_get_url, opened[0])
        assert status == 200 and b"AMBIENT" in body
        app.exit()
        await task

    try:
        asyncio.run(run_briefly())
        assert app.port != busy_port
        assert opened == [
            f"http://127.0.0.1:{app.port}/?access="
            + urllib.parse.quote(app._access_token, safe="")
        ]
        assert blocker.getsockname()[1] == busy_port, "foreign service was disturbed"
        assert controller.warnings == [
            f"Web console port {busy_port} is busy; using {app.port} instead"
        ]
    finally:
        blocker.close()


def test_explicit_busy_port_remains_fail_fast(tmp_path: Path) -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    busy_port = int(blocker.getsockname()[1])
    try:
        with pytest.raises(OSError) as excinfo:
            WebUIApp(
                StubController(tmp_path),
                port=busy_port,
                allow_port_fallback=False,
            )
        assert excinfo.value.errno == errno.EADDRINUSE
    finally:
        blocker.close()


def test_browser_does_not_open_without_the_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    controller = StubController(tmp_path)
    app = WebUIApp(controller, port=0)

    async def run_briefly() -> None:
        task = asyncio.ensure_future(app.run_async())
        await asyncio.sleep(0.3)
        app.exit()
        await task

    asyncio.run(run_briefly())
    assert opened == []
    printed = capsys.readouterr().out
    expected = (
        f"http://127.0.0.1:{app.port}/?access="
        + urllib.parse.quote(app._access_token, safe="")
    )
    assert expected in printed


def test_quit_command_stops_run_async(running_app) -> None:
    app, _ = running_app
    assert _post(app, "/api/command", {"action": "quit"}) == {
        "ok": True,
        "close": True,
    }
    deadline = time.time() + 5
    while app.is_running and time.time() < deadline:
        time.sleep(0.02)
    assert not app.is_running


def test_exit_and_schedulers_are_safe_after_event_loop_closes(tmp_path: Path) -> None:
    app, _controller = make_app(tmp_path)
    loop = asyncio.new_event_loop()

    async def run_briefly() -> None:
        task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0)
        app.exit()
        await task

    try:
        loop.run_until_complete(run_briefly())
    finally:
        loop.close()

    called: list[str] = []
    app.exit()
    app.call_from_thread(called.append, "thread")
    app.call_later(called.append, "later")

    assert called == ["thread", "later"]
    assert app._loop is None
    assert app._exit_event is None
