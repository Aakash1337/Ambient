"""Textual live side-channel pane."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from .audio_devices import CaptureDevice, MeterReading, MeterSession
from .bus import AnswerResult, Transcript

_FENCE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)(?:^\s*```\s*$|\Z)", re.DOTALL | re.MULTILINE)
_MD_FENCE_RE = re.compile(r"^\s*```[^\n]*$", re.MULTILINE)
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_MD_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_MD_ITALIC_RE = re.compile(r"(?<![\w*_])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*_])", re.DOTALL)
_MD_CODE_RE = re.compile(r"`+([^`]+)`+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BLANKS_RE = re.compile(r"\n{3,}")


def plain_text(answer: str) -> str:
    """Flatten Markdown to readable prose, but keep fenced code verbatim.

    Answers render into a plain Static, so any Markdown the model emits would
    otherwise show its literal syntax on screen (`**Path operations**`). Interview
    answers are meant to be spoken aloud, so stripping the markup is the right
    normalisation rather than rendering it as rich text.

    Code is the exception and MUST be stashed before the prose rules run. Python
    especially: `*args, **kwargs` is a perfect match for the bold and italic
    patterns, and the bullet rule eats lines beginning with `*` or `-`. Running
    those over a code block silently corrupts the code, and indentation --
    load-bearing in Python -- would not survive either.
    """
    blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        blocks.append(match.group(1).rstrip("\n"))
        return f"\x00CODE{len(blocks) - 1}\x00"

    text = _FENCE_BLOCK_RE.sub(_stash, answer)
    text = _MD_FENCE_RE.sub("", text)  # orphan fence with no closing pair
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\2", text)
    text = _MD_ITALIC_RE.sub(r"\2", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _BLANKS_RE.sub("\n\n", text).strip()
    for index, block in enumerate(blocks):
        text = text.replace(f"\x00CODE{index}\x00", "\n\n" + block + "\n")
    return _BLANKS_RE.sub("\n\n", text).strip()


class UIController(Protocol):
    paused: bool
    voice_enabled: bool
    agent_mode: bool

    def toggle_pause(self) -> bool: ...
    def toggle_voice(self) -> str: ...
    def toggle_agent_mode(self) -> str: ...
    def toggle_interaction_mode(self) -> str: ...
    def toggle_input_channel(self, channel: str) -> bool: ...
    async def force_answer_last(self) -> None: ...
    def cycle_gate_mode(self) -> str: ...
    def status_text(self) -> str: ...
    async def open_audio_devices(self) -> MeterSession: ...
    async def close_audio_devices(
        self,
        session: MeterSession,
        selected: CaptureDevice | None,
    ) -> None: ...
    def profile_choices(self) -> tuple[list[str], str]: ...
    async def select_profile(self, value: str) -> str: ...


def _matches_active(device: CaptureDevice, active_name: str) -> bool:
    if not active_name:
        return False
    device_name = device.name.casefold()
    active = active_name.casefold()
    return active in device_name or device_name in active


class DeviceRow(ListItem):
    def __init__(
        self,
        device: CaptureDevice,
        active: bool,
    ) -> None:
        super().__init__(Label(""))
        self.device = device
        self.active = active

    def update_meter(self, reading: MeterReading) -> None:
        marker = ""
        if self.active:
            marker = "● MIC ACTIVE  " if self.device.kind == "mic" else "● SYSTEM ACTIVE  "
        name = marker + self.device.display_name
        if reading.unavailable is not None:
            meter = f"unavailable: {reading.unavailable}"
        else:
            meter = (
                f"{'█' * reading.bar}{'░' * (18 - reading.bar)}  "
                f"peak {reading.peak_db:5.1f} dB  RMS {reading.rms_db:5.1f} dB"
            )
        self.query_one(Label).update(Text.assemble((name, "bold"), "\n", meter))


class AudioDevicesScreen(ModalScreen[CaptureDevice | None]):
    """Modal picker whose every row is metered at the same time."""

    DEFAULT_CSS = """
    AudioDevicesScreen {
        align: center middle;
        background: $background 75%;
    }
    AudioDevicesScreen > #device-dialog {
        width: 96%;
        height: 88%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    AudioDevicesScreen #device-title {
        height: 2;
        text-style: bold;
        color: $accent;
    }
    AudioDevicesScreen #device-help {
        height: 2;
        color: $text-muted;
    }
    AudioDevicesScreen ListView {
        height: 1fr;
    }
    AudioDevicesScreen ListItem {
        height: 3;
        padding: 0 1;
    }
    AudioDevicesScreen ListItem.group-heading {
        height: 2;
        color: $accent;
        text-style: bold;
        padding-top: 1;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("d", "cancel", "Cancel", priority=True),
        Binding("j", "down", "Down", show=False, priority=True),
        Binding("k", "up", "Up", show=False, priority=True),
        Binding("enter", "choose", "Select", priority=True),
    ]

    def __init__(self, session: MeterSession) -> None:
        super().__init__()
        self.session = session
        self.rows: list[DeviceRow] = []
        self._items: list[ListItem] = []
        for kind, heading, active_name in (
            ("mic", "MICROPHONES", session.active_mic),
            ("loopback", "SYSTEM AUDIO", session.active_loopback),
        ):
            self._items.append(
                ListItem(Label(heading), classes="group-heading", disabled=True)
            )
            for device in session.devices:
                if device.kind != kind:
                    continue
                row = DeviceRow(device, _matches_active(device, active_name))
                self.rows.append(row)
                self._items.append(row)

    def compose(self) -> ComposeResult:
        with Container(id="device-dialog"):
            yield Label("Audio devices", id="device-title")
            yield Label(
                "Speak once to compare every live meter.  ↑/↓ or j/k move · "
                "Enter selects · Esc/d cancels",
                id="device-help",
            )
            yield ListView(*self._items, id="device-list")

    def on_mount(self) -> None:
        self._refresh_meters()
        self.set_interval(0.1, self._refresh_meters)
        device_list = self.query_one("#device-list", ListView)
        for index, item in enumerate(device_list.children):
            if isinstance(item, DeviceRow) and item.active:
                device_list.index = index
                break

    def _refresh_meters(self) -> None:
        readings = self.session.snapshot()
        for row in self.rows:
            row.update_meter(readings.get(row.device.key, MeterReading()))

    def action_down(self) -> None:
        self.query_one("#device-list", ListView).action_cursor_down()

    def action_up(self) -> None:
        self.query_one("#device-list", ListView).action_cursor_up()

    def action_choose(self) -> None:
        device_list = self.query_one("#device-list", ListView)
        if device_list.index is None:
            return
        item = device_list.children[device_list.index]
        if isinstance(item, DeviceRow):
            self.dismiss(item.device)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProfileRow(ListItem):
    def __init__(self, value: str, active: bool) -> None:
        label = "(none)" if not value else value
        if active:
            label = "● " + label
        super().__init__(Label(label))
        self.value = value
        self.active = active


class ProfilesScreen(ModalScreen[str | None]):
    """Small profile picker; an empty string represents the explicit none choice."""

    DEFAULT_CSS = """
    ProfilesScreen {
        align: center middle;
        background: $background 75%;
    }
    ProfilesScreen > #profile-dialog {
        width: 72%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ProfilesScreen #profile-title {
        height: 2;
        text-style: bold;
        color: $accent;
    }
    ProfilesScreen #profile-help {
        height: 2;
        color: $text-muted;
    }
    ProfilesScreen ListView {
        height: auto;
        max-height: 20;
    }
    ProfilesScreen ListItem {
        height: 2;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("x", "cancel", "Cancel", priority=True),
        Binding("j", "down", "Down", show=False, priority=True),
        Binding("k", "up", "Up", show=False, priority=True),
        Binding("enter", "choose", "Select", priority=True),
    ]

    def __init__(self, choices: list[str], active: str) -> None:
        super().__init__()
        self.rows = [
            ProfileRow(value, value.replace("\\", "/") == active.replace("\\", "/"))
            for value in ["", *choices]
        ]

    def compose(self) -> ComposeResult:
        with Container(id="profile-dialog"):
            yield Label("Context profile", id="profile-title")
            yield Label(
                "↑/↓ or j/k move · Enter selects · Esc/x cancels",
                id="profile-help",
            )
            yield ListView(*self.rows, id="profile-list")

    def on_mount(self) -> None:
        profile_list = self.query_one("#profile-list", ListView)
        for index, row in enumerate(self.rows):
            if row.active:
                profile_list.index = index
                break

    def action_down(self) -> None:
        self.query_one("#profile-list", ListView).action_cursor_down()

    def action_up(self) -> None:
        self.query_one("#profile-list", ListView).action_cursor_up()

    def action_choose(self) -> None:
        profile_list = self.query_one("#profile-list", ListView)
        if profile_list.index is None:
            return
        row = profile_list.children[profile_list.index]
        if isinstance(row, ProfileRow):
            self.dismiss(row.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


def load_session_records(path: Path) -> list[dict[str, Any]]:
    """Parse a session JSONL, ordered by timestamp.

    Unparseable lines are skipped rather than failing the whole file: the
    live session's own log is a valid pick, and its last line may be
    mid-write at the moment it is read.
    """
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    records.sort(key=lambda record: record.get("timestamp") or 0)
    return records


def _session_label(path: Path) -> str:
    try:
        stamp = datetime.strptime(path.stem, "session-%Y%m%d-%H%M%S")
    except ValueError:
        return path.stem
    return stamp.strftime("%Y-%m-%d %H:%M:%S")


class SessionRow(ListItem):
    def __init__(self, path: Path) -> None:
        super().__init__(Label(_session_label(path)))
        self.path = path


class SessionsScreen(ModalScreen[Path | None]):
    """Pick a recorded session log, newest first."""

    DEFAULT_CSS = """
    SessionsScreen {
        align: center middle;
        background: $background 75%;
    }
    SessionsScreen > #session-pick-dialog {
        width: 72%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    SessionsScreen #session-pick-title {
        height: 2;
        text-style: bold;
        color: $accent;
    }
    SessionsScreen #session-pick-help {
        height: 2;
        color: $text-muted;
    }
    SessionsScreen ListView {
        height: auto;
        max-height: 20;
    }
    SessionsScreen ListItem {
        height: 1;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("l", "cancel", "Cancel", priority=True),
        Binding("j", "down", "Down", show=False, priority=True),
        Binding("k", "up", "Up", show=False, priority=True),
        Binding("enter", "choose", "Select", priority=True),
    ]

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.rows = [SessionRow(path) for path in paths]

    def on_mount(self) -> None:
        if self.rows:
            self.query_one("#session-pick-list", ListView).index = 0

    def compose(self) -> ComposeResult:
        with Container(id="session-pick-dialog"):
            yield Label("Recorded sessions", id="session-pick-title")
            yield Label(
                "↑/↓ or j/k move · Enter opens read-only · Esc/l cancels",
                id="session-pick-help",
            )
            yield ListView(*self.rows, id="session-pick-list")

    def action_down(self) -> None:
        self.query_one("#session-pick-list", ListView).action_cursor_down()

    def action_up(self) -> None:
        self.query_one("#session-pick-list", ListView).action_cursor_up()

    def action_choose(self) -> None:
        session_list = self.query_one("#session-pick-list", ListView)
        if session_list.index is None:
            return
        row = session_list.children[session_list.index]
        if isinstance(row, SessionRow):
            self.dismiss(row.path)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionCard(Static):
    """Answered question from a recorded session, styled like a live QACard."""

    DEFAULT_CSS = """
    SessionCard {
        border: round $accent;
        margin: 1 1;
        padding: 0 1;
        height: auto;
    }
    SessionCard .question { color: $accent; text-style: bold; height: auto; }
    SessionCard .answer { color: $text; height: auto; margin-top: 1; }
    """

    def __init__(self, record: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.record = record

    def compose(self) -> ComposeResult:
        record = self.record
        stamp = datetime.fromtimestamp(record.get("timestamp") or 0).strftime(
            "%H:%M:%S"
        )
        question = str(record.get("query") or record.get("text") or "")
        suffix = "  · web lookup" if record.get("web_lookup") else ""
        yield Label(f"Q  {question}  ·  {stamp}{suffix}", classes="question")
        status = str(record.get("answer_status") or "ok")
        prefix = "A  " if status == "ok" else f"{status.replace('_', ' ')}  "
        yield Static(
            prefix + plain_text(str(record.get("answer") or "")), classes="answer"
        )


class SessionViewerScreen(ModalScreen[None]):
    """Read-only replay of a recorded session over the live pane.

    The live pipeline keeps running untouched underneath; closing the modal
    returns to it exactly as it was.
    """

    DEFAULT_CSS = """
    SessionViewerScreen {
        align: center middle;
        background: $background 75%;
    }
    SessionViewerScreen > #session-dialog {
        width: 96%;
        height: 88%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    SessionViewerScreen #session-title {
        height: 2;
        text-style: bold;
        color: $accent;
    }
    SessionViewerScreen #session-feed {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    SessionViewerScreen .transcript {
        color: $text-muted;
        height: auto;
        margin: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("l", "close", "Close", priority=True),
        Binding("j", "down", "Down", show=False, priority=True),
        Binding("k", "up", "Up", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        records: list[dict[str, Any]],
        newest_first: bool,
    ) -> None:
        super().__init__()
        self._title = title
        self._records = list(reversed(records)) if newest_first else records

    def compose(self) -> ComposeResult:
        answered = sum(1 for record in self._records if record.get("gate"))
        with Container(id="session-dialog"):
            yield Label(
                f"Session {self._title} — {len(self._records)} utterances, "
                f"{answered} answered  ·  read-only  ·  Esc/l closes",
                id="session-title",
            )
            with VerticalScroll(id="session-feed"):
                for record in self._records:
                    if record.get("gate"):
                        yield SessionCard(record)
                    else:
                        stamp = datetime.fromtimestamp(
                            record.get("timestamp") or 0
                        ).strftime("%H:%M:%S")
                        channel = record.get("channel", "?")
                        yield Static(
                            Text(
                                f"{stamp}  [{channel}]  {record.get('text', '')}",
                                style="dim",
                            ),
                            classes="transcript",
                        )

    def action_down(self) -> None:
        self.query_one("#session-feed", VerticalScroll).scroll_relative(
            y=3, animate=False
        )

    def action_up(self) -> None:
        self.query_one("#session-feed", VerticalScroll).scroll_relative(
            y=-3, animate=False
        )

    def action_close(self) -> None:
        self.dismiss(None)


class QACard(Static):
    DEFAULT_CSS = """
    QACard {
        border: round $accent;
        margin: 1 1;
        padding: 0 1;
        height: auto;
    }
    QACard .question { color: $accent; text-style: bold; height: auto; }
    QACard .answer { color: $text; height: auto; margin-top: 1; }
    """

    def __init__(
        self,
        question: str,
        rendered_callback: Callable[[], None] | None = None,
        agent_mode: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.question = question
        self.agent_mode = agent_mode
        # Agent is an interaction role, not a customer-service profile.  Keep
        # these labels useful for a cybersecurity or technical conversation too.
        self._question_prefix = "SPEAKER  " if agent_mode else "Q  "
        self._answer_prefix = "AMBIENT  " if agent_mode else "A  "
        self._waiting_word = "responding" if agent_mode else "answering"
        self._rendered_callback = rendered_callback or (lambda: None)
        self._spinner_index = 0
        self._spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._timer = None
        self._flush_timer = None
        self._raw_answer = ""
        self._last_stream_render = 0.0
        self._streaming = False
        self._resolved = False

    def compose(self) -> ComposeResult:
        yield Label(self._question_prefix + self.question, classes="question")
        yield Static(f"⠋  {self._waiting_word}…", classes="answer")

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self._streaming or self._resolved:
            return
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner)
        self.query_one(".answer", Static).update(
            f"{self._spinner[self._spinner_index]}  {self._waiting_word}…"
        )

    def _render_stream(self) -> None:
        if self._resolved:
            return
        # Re-render the entire raw answer every time. Flattening individual deltas
        # corrupts fenced code split across events, including *args/**kwargs.
        # Sample and schedule auto-follow before the update changes scroll bounds.
        self._rendered_callback()
        self.query_one(".answer", Static).update(
            self._answer_prefix + plain_text(self._raw_answer)
        )
        self._last_stream_render = time.monotonic()

    def _flush_stream(self) -> None:
        self._flush_timer = None
        self._render_stream()

    def append_answer(self, delta: str) -> None:
        if not delta or self._resolved:
            return
        self._raw_answer += delta
        if not self._streaming:
            self._streaming = True
            if self._timer is not None:
                self._timer.pause()
            # First text replaces the spinner immediately; only later updates
            # are coalesced to roughly 10Hz.
            self._render_stream()
            return
        if self._flush_timer is None:
            remaining = 0.1 - (time.monotonic() - self._last_stream_render)
            if remaining <= 0:
                # Already past the coalescing interval, so render now. Scheduling
                # a zero-delay timer instead builds a degenerate Textual Timer
                # that divides by its own interval and raises ZeroDivisionError
                # when stopped -- which happens on every card teardown. Deltas
                # arriving more than 100ms apart is the normal case, not an edge.
                self._render_stream()
            else:
                self._flush_timer = self.set_timer(remaining, self._flush_stream)

    def set_answer(self, answer: str, status: str = "ok") -> None:
        self._resolved = True
        if self._timer is not None:
            self._timer.pause()
        if self._flush_timer is not None:
            self._flush_timer.pause()
            self._flush_timer = None
        self._raw_answer = answer
        prefix = (
            self._answer_prefix
            if status == "ok"
            else f"{status.replace('_', ' ')}  "
        )
        self._rendered_callback()
        self.query_one(".answer", Static).update(prefix + plain_text(answer))

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.pause()
        if self._flush_timer is not None:
            self._flush_timer.pause()


class AmbientQAApp(App[None]):
    TITLE = "Ambient"
    SUB_TITLE = "passive question side-channel"
    CSS = """
    Screen { layout: vertical; }
    #feed { height: 1fr; scrollbar-gutter: stable; }
    .transcript { color: $text-muted; height: auto; margin: 0 1; }
    #agent-banner {
        height: 1;
        display: none;
        background: $success;
        color: $text;
        text-style: bold;
        text-align: center;
    }
    #agent-banner.visible { display: block; }
    /* NOT docked. Two bottom-docked widgets do not stack here: #status and the
       Footer both resolved to the same row and the Footer drew over it, so the
       whole status line (mic:off, whisper device, warnings) was invisible.
       Left in normal vertical flow it lands directly above the docked Footer. */
    #status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    /* Pausing is easy to forget, and a paused app looks identical to a silent
       one -- the user waits for answers that will never come. Recolour the whole
       bar rather than relying on one word inside a dense status line. */
    #status.paused {
        background: $warning;
        color: $text;
        text-style: bold;
    }
    /* Docked TOP on purpose. Docked to the bottom it landed on the same row as
       the Footer, which drew over it -- the banner existed, had the right class
       and a real size, and was still invisible. */
    #paused-banner {
        dock: top;
        height: 1;
        display: none;
        background: $warning;
        color: $text;
        text-style: bold;
        text-align: center;
    }
    #paused-banner.visible { display: block; }
    """
    BINDINGS = [
        ("1", "mic_input", "Mic listen"),
        ("2", "system_input", "System listen"),
        ("p", "pause", "Pause"),
        ("c", "clear", "Clear"),
        ("t", "transcripts", "Transcripts"),
        ("l", "sessions", "Sessions"),
        ("a", "force_answer", "Answer last"),
        ("s", "strictness", "Strictness"),
        ("m", "voice", "Voice"),
        ("g", "agent_mode", "Q&A / Agent"),
        ("r", "conversation", "Delivery"),
        ("x", "profiles", "Context profile"),
        ("d", "devices", "Audio devices"),
        ("q", "quit", "Quit"),
    ]

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        # The voice key exists only in voice mode (--voice); a silent pane's
        # footer must look exactly as it always has.
        if action in {"voice", "agent_mode", "conversation"} and not getattr(
            self.controller, "voice_enabled", False
        ):
            return False
        return True

    def __init__(
        self,
        controller: UIController,
        show_transcripts: bool = True,
        status_interval_s: float = 0.5,
        feed_direction: str = "top",
        log_dir: str | Path = "logs",
    ) -> None:
        super().__init__()
        self.controller = controller
        self.show_transcripts = show_transcripts
        self.status_interval_s = status_interval_s
        self.feed_direction = feed_direction
        self.log_dir = Path(log_dir)
        self._cards: dict[str, QACard] = {}
        self._transcript_rows: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="agent-banner")
        yield VerticalScroll(id="feed")
        yield Static("", id="paused-banner")
        yield Static("starting…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(self.status_interval_s, self._refresh_status)
        self._apply_paused()
        self._apply_mode_chrome()

    def _apply_mode_chrome(self) -> None:
        """Make autonomous participation impossible to miss at a glance."""
        agent_mode = bool(getattr(self.controller, "agent_mode", False))
        banner = self.query_one("#agent-banner", Static)
        if agent_mode:
            customer = getattr(self.controller, "_agent_customer_channel", "mic")
            getter = getattr(self.controller, "input_channel_enabled", None)
            customer_live = bool(getter(customer)) if callable(getter) else True
            if getattr(self.controller, "paused", False):
                message = "○ AGENT PAUSED — press p to resume"
            elif not customer_live:
                label = "microphone" if customer == "mic" else "system audio"
                key = "1" if customer == "mic" else "2"
                message = f"○ AGENT WAITING — speaker {label} is muted · press {key}"
            else:
                label = "MIC" if customer == "mic" else "SYSTEM"
                message = (
                    f"● AGENT LIVE · SPEAKER={label} — responds to the conversation · "
                    "1 mic · 2 system"
                )
            banner.update(message)
        else:
            banner.update("")
        banner.set_class(agent_mode, "visible")
        self.query_one("#status", Static).set_class(agent_mode, "agent")

    def _apply_paused(self) -> None:
        """Show the pause state. Kept separate from the status line so the key
        press gives instant feedback instead of waiting for the next tick."""
        paused = bool(getattr(self.controller, "paused", False))
        self.query_one("#status", Static).set_class(paused, "paused")
        banner = self.query_one("#paused-banner", Static)
        banner.update(
            "⏸  PAUSED — not listening.  Press p to resume." if paused else ""
        )
        banner.set_class(paused, "visible")

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self.controller.status_text())
        self._apply_paused()
        self._apply_mode_chrome()

    def _following(self, feed: VerticalScroll) -> bool:
        """Whether the view sits at the edge where new entries appear: the top
        in "top" mode (newest first), the bottom in "bottom" mode."""
        if self.feed_direction == "top":
            return feed.scroll_y <= 0
        return feed.scroll_y >= max(0, feed.max_scroll_y - 1)

    def _follow(self, feed: VerticalScroll) -> None:
        if self.feed_direction == "top":
            feed.scroll_home(animate=False)
        else:
            feed.scroll_end(animate=False)

    async def _mount_following(self, widget: Static) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        was_following = self._following(feed)
        if self.feed_direction == "top" and feed.children:
            await feed.mount(widget, before=feed.children[0])
        else:
            await feed.mount(widget)
        if was_following:
            self._follow(feed)

    async def add_transcript(self, transcript: Transcript) -> None:
        if not self.show_transcripts:
            return
        stamp = datetime.fromtimestamp(transcript.timestamp).strftime("%H:%M:%S")
        channel = "mic" if transcript.channel == "mic" else "sys"
        content = Text(f"{stamp}  [{channel}]  {transcript.text}", style="dim")
        existing = self._transcript_rows.get(transcript.utterance_id)
        if existing is not None and existing.is_mounted:
            feed = self.query_one("#feed", VerticalScroll)
            was_following = self._following(feed)
            existing.update(content)
            if was_following:
                self.call_after_refresh(self._follow, feed)
            return
        row = Static(content, classes="transcript")
        self._transcript_rows[transcript.utterance_id] = row
        await self._mount_following(row)

    async def add_question(self, question_id: str, question: str) -> None:
        card = QACard(
            question,
            rendered_callback=self._answer_card_rendered,
            agent_mode=bool(getattr(self.controller, "agent_mode", False)),
            id=f"qa-{question_id}",
        )
        self._cards[question_id] = card
        await self._mount_following(card)

    def _answer_card_rendered(self) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        if self._following(feed):
            self.call_after_refresh(self._follow, feed)

    def append_answer_delta(self, question_id: str, delta: str) -> None:
        card = self._cards.get(question_id)
        if card is not None and card.is_mounted:
            card.append_answer(delta)

    def resolve_answer(self, result: AnswerResult) -> None:
        card = self._cards.get(result.question_id)
        if card is not None:
            card.set_answer(result.answer, result.status)

    def add_warning(self, message: str) -> None:
        self.notify(message, severity="warning", timeout=8)

    def action_pause(self) -> None:
        self.controller.toggle_pause()
        # Full refresh, not just _apply_paused: the status text itself flips
        # between "● listening" and "⏸ PAUSED", and waiting for the next tick
        # would leave it stale next to an already-updated banner.
        self._refresh_status()

    def action_clear(self) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        for child in list(feed.children):
            child.remove()
        self._cards.clear()
        self._transcript_rows.clear()

    def action_transcripts(self) -> None:
        self.show_transcripts = not self.show_transcripts
        state = "shown" if self.show_transcripts else "hidden"
        self.notify(f"Raw transcripts {state}")

    def action_force_answer(self) -> None:
        asyncio.create_task(self.controller.force_answer_last())

    def action_strictness(self) -> None:
        mode = self.controller.cycle_gate_mode()
        self.notify(f"Gate mode: {mode}")

    def action_voice(self) -> None:
        self.notify(self.controller.toggle_voice())
        self._refresh_status()

    def action_agent_mode(self) -> None:
        self.notify(self.controller.toggle_agent_mode())
        self._refresh_status()

    def action_conversation(self) -> None:
        self.notify(self.controller.toggle_interaction_mode())
        self._refresh_status()

    def _toggle_input(self, channel: str, label: str) -> None:
        enabled = self.controller.toggle_input_channel(channel)
        state = "listening" if enabled else "muted"
        self.notify(f"{label} input {state}")
        self._refresh_status()

    def action_mic_input(self) -> None:
        self._toggle_input("mic", "Microphone")

    def action_system_input(self) -> None:
        self._toggle_input("sys", "System audio")

    # Own worker group (as below): in the shared default group, pressing x
    # while the picker is stopping/restarting capture would cancel this worker
    # mid-flight and strand the capture restart.
    @work(exclusive=True, group="devices")
    async def action_devices(self) -> None:
        session: MeterSession | None = None
        selected: CaptureDevice | None = None
        try:
            session = await self.controller.open_audio_devices()
            try:
                selected = await self.push_screen_wait(AudioDevicesScreen(session))
            finally:
                await self.controller.close_audio_devices(session, selected)
            if selected is not None:
                target = "microphone" if selected.kind == "mic" else "system audio"
                self.notify(f"Selected {target}: {selected.name}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_warning(f"Unable to change audio device: {exc}")

    def _session_paths(self) -> list[Path]:
        if not self.log_dir.is_dir():
            return []
        return sorted(self.log_dir.glob("session-*.jsonl"), reverse=True)

    # Own worker group: sharing the default exclusive group would let this
    # action and the device/profile pickers silently cancel each other.
    @work(exclusive=True, group="sessions")
    async def action_sessions(self) -> None:
        try:
            paths = await asyncio.to_thread(self._session_paths)
            if not paths:
                self.notify(f"No recorded sessions in {self.log_dir}")
                return
            selected = await self.push_screen_wait(SessionsScreen(paths))
            if selected is None:
                return
            records = await asyncio.to_thread(load_session_records, selected)
            await self.push_screen_wait(
                SessionViewerScreen(
                    selected.stem, records, self.feed_direction == "top"
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_warning(f"Unable to open session log: {exc}")

    @work(exclusive=True, group="profiles")
    async def action_profiles(self) -> None:
        try:
            choices, active = self.controller.profile_choices()
            selected = await self.push_screen_wait(ProfilesScreen(choices, active))
            if selected is not None:
                name = await self.controller.select_profile(selected)
                if not selected:
                    self.notify("Context profile disabled")
                elif name == "none":
                    self.add_warning("Context profile is unavailable or globally disabled")
                else:
                    self.notify(f"Profile active: {name}")
                self._refresh_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_warning(f"Unable to change context profile: {exc}")
