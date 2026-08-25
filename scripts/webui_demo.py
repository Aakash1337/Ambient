"""Drive the web console with a scripted conversation — no audio, no models.

    .venv-linux/bin/python scripts/webui_demo.py [--port 8802] [--once]

This exists for two reasons:
  - rehearsing/inspecting the web console without a microphone, Whisper,
    Ollama, or Claude anywhere near it;
  - giving the demo an offline fallback surface that cannot be broken by
    audio or model problems.

It uses the real WebUIApp against a stub controller, so what you see is the
real console rendering the real event protocol. Session logs are written to a
temp directory, never into logs/. The scripted conversation loops until
Ctrl+C (or plays once with --once).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ambientqa.config import load_config  # noqa: E402
from ambientqa.logging_ import SessionLogger  # noqa: E402
from ambientqa.bus import AnswerResult, Transcript  # noqa: E402
from ambientqa.webui import WebUIApp  # noqa: E402


class _Queue(SimpleNamespace):
    def qsize(self) -> int:  # noqa: D401
        return 0


class _FakeMeterSession:
    """Fake all-endpoints meter session so the device modal works offline."""

    def __init__(self) -> None:
        import math

        from ambientqa.backends.base import CaptureDevice
        from ambientqa.audio_devices import MeterReading

        self._math = math
        self._reading_cls = MeterReading
        self.devices = [
            CaptureDevice("ec_mic", "Echo-cancelled Microphone (ec_mic)", "mic", 1, 16000),
            CaptureDevice("usb_raw", "USB Headset Mono (raw)", "mic", 1, 48000),
            CaptureDevice("webcam", "Webcam Array (C920)", "mic", 2, 32000),
            CaptureDevice("mon_main", "Monitor of Starship/Matisse — Analog Stereo", "loopback", 2, 48000),
            CaptureDevice("mon_usb", "Monitor of USB Headset", "loopback", 2, 48000),
            CaptureDevice("mon_hdmi", "Monitor of HDMI Output", "loopback", 2, 48000),
        ]
        self.active_mic = "Echo-cancelled Microphone (ec_mic)"
        self.active_loopback = "Monitor of Starship/Matisse — Analog Stereo"

    def snapshot(self, width: int = 18):
        now = time.time()
        readings = {}
        for index, device in enumerate(self.devices):
            if device.id == "mon_hdmi":
                readings[device.key] = self._reading_cls(unavailable="endpoint busy")
                continue
            wobble = (self._math.sin(now * 2.2 + index * 1.7) + 1) / 2
            quiet = device.id in ("webcam", "mon_usb")
            peak = (0.02 if quiet else 0.35) + wobble * (0.02 if quiet else 0.45)
            rms = peak * 0.55
            readings[device.key] = self._reading_cls(
                peak=peak, rms=rms, bar=max(0, min(width, int(peak * width * 1.6)))
            )
        return readings

    def close(self) -> None:
        pass


class DemoController:
    """Just enough controller surface for the console, with canned reactions."""

    def __init__(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config.toml"
        try:
            self.config = load_config(config_path)
        except Exception:
            from ambientqa.config import default_config

            self.config = default_config()
        self.config.ui.log_dir = tempfile.mkdtemp(prefix="ambientqa-webui-demo-")
        self.logger = SessionLogger(self.config.ui.log_dir)
        self.paused = False
        self.voice_enabled = False
        self.interaction_mode = "normal"
        self.agent_mode = False
        self._agent_customer_channel = "mic"
        self._input_channels_enabled = {"mic": True, "sys": True}
        self.warnings: list[str] = []
        self.status_note = "demo mode — scripted conversation, no audio"
        self.profile = SimpleNamespace(name="tier2-platform")
        self.answer_count = 0
        self.estimated_tokens = 0
        self.capture = SimpleNamespace(
            mic=SimpleNamespace(active=True, detail="Echo-cancelled Microphone (ec_mic)", silent_for=lambda: None),
            loopback=SimpleNamespace(active=True, detail="Monitor of Starship/Matisse", silent_for=lambda: None),
        )
        self.transcriber = SimpleNamespace(device="cuda")
        self.answerer = SimpleNamespace(in_flight=0)
        self.instances = SimpleNamespace(heartbeat_and_count=lambda: 1)
        self.frames = _Queue()
        self.utterances = _Queue()
        self.transcripts = _Queue()
        self.answers = _Queue()
        self.app: WebUIApp | None = None

    def _source_status(self, state: SimpleNamespace) -> str:
        return "on" if state.active else "off"

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def cycle_gate_mode(self) -> str:
        modes = ["strict", "balanced", "eager"]
        index = (modes.index(self.config.gate.mode) + 1) % len(modes)
        self.config.gate.mode = modes[index]
        return modes[index]

    def toggle_voice(self) -> str:
        return "voice mode is off (relaunch with --voice)"

    def toggle_interaction_mode(self) -> str:
        if not self.voice_enabled:
            return "conversation mode requires Voice mode"
        self.interaction_mode = (
            "conversational"
            if self.interaction_mode == "normal"
            else "normal"
        )
        return f"{self.interaction_mode.title()} delivery"

    def toggle_agent_mode(self) -> str:
        if not self.voice_enabled:
            return "Agent interaction requires Voice mode"
        self.agent_mode = not self.agent_mode
        return "Agent interaction" if self.agent_mode else "Q&A interaction"

    def input_channel_enabled(self, channel: str) -> bool:
        if channel not in self._input_channels_enabled:
            raise ValueError(f"unknown input channel: {channel}")
        return self._input_channels_enabled[channel]

    def toggle_input_channel(self, channel: str) -> bool:
        enabled = not self.input_channel_enabled(channel)
        self._input_channels_enabled[channel] = enabled
        return enabled

    async def force_answer_last(self) -> None:
        if self.app is None:
            return
        qid = f"forced-{int(time.time() * 1000)}"
        await self.app.add_question(qid, "Can the executables behind this be called through a UI?")
        await _stream_answer(
            self,
            qid,
            "Yes — it already runs as an app, so a UI calls the same functions.\n"
            "• API over the existing functions\n"
            "• Same endpoints the terminal calls\n"
            "• No migration, no data moved",
            reason="forced_by_user",
        )

    def profile_choices(self) -> tuple[list[str], str]:
        root = Path(__file__).resolve().parent.parent / "profiles"
        choices = sorted(p.as_posix().split("/", -1)[-1] for p in root.glob("*.md")) if root.is_dir() else []
        return [f"profiles/{c}" for c in choices], ""

    async def select_profile(self, value: str) -> str:
        if not value:
            self.profile = None
            return "none"
        self.profile = SimpleNamespace(name=Path(value).stem)
        return self.profile.name

    async def open_audio_devices(self):
        return _FakeMeterSession()

    async def close_audio_devices(self, session, selected):
        if selected is not None:
            self.status_note = f"demo: would select {selected.name}"


async def _stream_answer(
    controller: DemoController,
    qid: str,
    answer: str,
    reason: str = "explicit_interrogative",
    status: str = "ok",
    latency: float = 3900.0,
    web_lookup: bool = False,
) -> None:
    app = controller.app
    assert app is not None
    controller.answerer.in_flight += 1
    await asyncio.sleep(0.6)
    words = answer.split(" ")
    for index in range(0, len(words), 3):
        app.append_answer_delta(qid, " ".join(words[index : index + 3]) + " ")
        await asyncio.sleep(0.12)
    controller.answerer.in_flight -= 1
    controller.answer_count += 1
    controller.estimated_tokens += int(len(words) * 1.35)
    app.resolve_answer(AnswerResult(qid, "", answer, status, latency, searched=web_lookup))
    controller.logger.append(
        {
            "id": qid,
            "timestamp": time.time(),
            "channel": "sys",
            "text": "",
            "gate": True,
            "gate_reason": reason,
            "answer": answer,
            "answer_status": status,
            "web_lookup": web_lookup,
            "latencies_ms": {"stt": 240.0, "gate": 0.0 if "heuristic" in reason or reason.startswith("explicit") else 880.0, "answer": latency},
        }
    )


SCRIPT: list[tuple[str, ...]] = [
    ("say", "sys", "Hi — yeah, sorry, one second, I'm just finding the ticket number."),
    ("reject", "filler_only"),
    ("say", "mic", "No rush. I've got 4412 open in front of me already."),
    ("reject", "not_a_direct_question"),
    ("say", "sys", "Right, that's the one. It's been a rough week with this, honestly."),
    ("reject", "not_a_direct_question"),
    ("say", "mic", "Understood — give me the short version of where you're stuck."),
    ("reject", "human_vocative"),
    ("say", "sys", "I haven't typed a single command through this whole thing — I thought you said it was an app."),
    ("reject", "not_a_direct_question"),
    ("say", "sys", "Can the executables running behind the scenes be called through a UI?"),
    (
        "answer",
        "Can the executables running behind the scenes be called through a UI?",
        "Yes — it already runs as an app, so we put a proper interface on the same functions. Nothing changes underneath.\n"
        "• API over the existing functions\n"
        "• Same endpoints the terminal calls\n"
        "• No migration, no data moved",
        "explicit_interrogative",
    ),
    ("say", "mic", "Let me confirm exactly how that's wired."),
    ("reject", "not_a_direct_question"),
    ("say", "sys", "Our warehouse floor drops off the network twice a day, so if it loses connection mid-call, does the thing still work?"),
    (
        "answer",
        "Does it still work if the network drops mid-call?",
        "If the floor drops off the network, transcription keeps running locally — only the suggested answers pause, and they catch up.\n"
        "• Speech + gating are on-device\n"
        "• Answers queue, then resume",
        "ollama_accept",
    ),
    ("say", "sys", "mumbled — and what's this going to run us for forty seats"),
    ("reject", "ollama_reject"),
    ("late",
        "What does this cost for forty seats?",
        "Forty seats sits in the volume band — exact numbers come per-seat monthly with no platform fee.\n"
        "• Volume band 25–99 seats\n"
        "• Quote must come from Sales"),
    ("say", "sys", "That's helpful, thanks — much better than last time."),
    ("reject", "not_a_direct_question"),
]


async def run_script(controller: DemoController, once: bool) -> None:
    app = controller.app
    assert app is not None
    while True:
        for step in SCRIPT:
            kind = step[0]
            if kind == "say":
                _, channel, text = step
                tid = f"t{int(time.time() * 1000)}"
                await app.add_transcript(Transcript(channel, text, time.time(), tid, 240.0))
                controller._last = (tid, channel, text)  # type: ignore[attr-defined]
                await asyncio.sleep(1.4)
            elif kind == "reject":
                _, reason = step
                tid, channel, text = getattr(controller, "_last", ("x", "sys", ""))
                controller.logger.append(
                    {
                        "id": tid,
                        "timestamp": time.time(),
                        "channel": channel,
                        "text": text,
                        "gate": False,
                        "gate_reason": reason,
                        "answer": None,
                        "latencies_ms": {"stt": 240.0},
                    }
                )
                await asyncio.sleep(0.4)
            elif kind == "answer":
                _, question, answer, reason = step
                tid = getattr(controller, "_last", ("q",))[0]
                await app.add_question(tid, question)
                await _stream_answer(controller, tid, answer, reason)
                await asyncio.sleep(1.2)
            elif kind == "late":
                _, question, answer = step
                qid = f"late-{int(time.time() * 1000)}"
                await asyncio.sleep(2.5)
                await app.add_question(qid, question)
                await _stream_answer(controller, qid, answer, "second_pass_recovery", latency=1400.0)
                await asyncio.sleep(1.0)
        if once:
            app.notify("Scripted demo finished — press Q in the console to quit.")
            return
        app.notify("Scripted demo loops — press Q in the console to quit.")
        await asyncio.sleep(4)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8802)
    parser.add_argument("--once", action="store_true", help="play the script once instead of looping")
    args = parser.parse_args()

    controller = DemoController()
    app = WebUIApp(controller, port=args.port)
    controller.app = app
    script_task = asyncio.ensure_future(run_script(controller, args.once))
    try:
        await app.run_async()
    finally:
        script_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
