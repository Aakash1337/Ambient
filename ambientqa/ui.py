"""Textual live side-channel pane."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Callable, Protocol

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from .audio_devices import AudioDevice, MeterReading, MeterSession
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

    def toggle_pause(self) -> bool: ...
    async def force_answer_last(self) -> None: ...
    def cycle_gate_mode(self) -> str: ...
    def status_text(self) -> str: ...
    async def open_audio_devices(self) -> MeterSession: ...
    async def close_audio_devices(
        self,
        session: MeterSession,
        selected: AudioDevice | None,
    ) -> None: ...
    def profile_choices(self) -> tuple[list[str], str]: ...
    async def select_profile(self, value: str) -> str: ...


def _matches_active(device: AudioDevice, active_name: str) -> bool:
    if not active_name:
        return False
    device_name = device.name.casefold()
    active = active_name.casefold()
    return active in device_name or device_name in active


class DeviceRow(ListItem):
    def __init__(
        self,
        device: AudioDevice,
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


class AudioDevicesScreen(ModalScreen[AudioDevice | None]):
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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.question = question
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
        yield Label("Q  " + self.question, classes="question")
        yield Static("⠋  answering…", classes="answer")

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self._streaming or self._resolved:
            return
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner)
        self.query_one(".answer", Static).update(
            f"{self._spinner[self._spinner_index]}  answering…"
        )

    def _render_stream(self) -> None:
        if self._resolved:
            return
        # Re-render the entire raw answer every time. Flattening individual deltas
        # corrupts fenced code split across events, including *args/**kwargs.
        # Sample and schedule auto-follow before the update changes scroll bounds.
        self._rendered_callback()
        self.query_one(".answer", Static).update(
            "A  " + plain_text(self._raw_answer)
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
        prefix = "A  " if status == "ok" else f"{status.replace('_', ' ')}  "
        self._rendered_callback()
        self.query_one(".answer", Static).update(prefix + plain_text(answer))

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.pause()
        if self._flush_timer is not None:
            self._flush_timer.pause()


class AmbientQAApp(App[None]):
    TITLE = "Ambient Q&A"
    SUB_TITLE = "passive question side-channel"
    CSS = """
    Screen { layout: vertical; }
    #feed { height: 1fr; scrollbar-gutter: stable; }
    .transcript { color: $text-muted; height: auto; margin: 0 1; }
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
        ("p", "pause", "Pause"),
        ("c", "clear", "Clear"),
        ("t", "transcripts", "Transcripts"),
        ("a", "force_answer", "Answer last"),
        ("s", "strictness", "Strictness"),
        ("x", "profiles", "Context profile"),
        ("d", "devices", "Audio devices"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        controller: UIController,
        show_transcripts: bool = True,
        status_interval_s: float = 0.5,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.show_transcripts = show_transcripts
        self.status_interval_s = status_interval_s
        self._cards: dict[str, QACard] = {}
        self._transcript_rows: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="feed")
        yield Static("", id="paused-banner")
        yield Static("starting…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(self.status_interval_s, self._refresh_status)
        self._apply_paused()

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

    async def _mount_following(self, widget: Static) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        was_following = feed.scroll_y >= max(0, feed.max_scroll_y - 1)
        await feed.mount(widget)
        if was_following:
            feed.scroll_end(animate=False)

    async def add_transcript(self, transcript: Transcript) -> None:
        if not self.show_transcripts:
            return
        stamp = datetime.fromtimestamp(transcript.timestamp).strftime("%H:%M:%S")
        channel = "mic" if transcript.channel == "mic" else "sys"
        content = Text(f"{stamp}  [{channel}]  {transcript.text}", style="dim")
        existing = self._transcript_rows.get(transcript.utterance_id)
        if existing is not None and existing.is_mounted:
            feed = self.query_one("#feed", VerticalScroll)
            was_following = feed.scroll_y >= max(0, feed.max_scroll_y - 1)
            existing.update(content)
            if was_following:
                self.call_after_refresh(feed.scroll_end, animate=False)
            return
        row = Static(content, classes="transcript")
        self._transcript_rows[transcript.utterance_id] = row
        await self._mount_following(row)

    async def add_question(self, question_id: str, question: str) -> None:
        card = QACard(
            question,
            rendered_callback=self._answer_card_rendered,
            id=f"qa-{question_id}",
        )
        self._cards[question_id] = card
        await self._mount_following(card)

    def _answer_card_rendered(self) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        was_following = feed.scroll_y >= max(0, feed.max_scroll_y - 1)
        if was_following:
            self.call_after_refresh(feed.scroll_end, animate=False)

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

    @work(exclusive=True)
    async def action_devices(self) -> None:
        session: MeterSession | None = None
        selected: AudioDevice | None = None
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

    @work(exclusive=True)
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_warning(f"Unable to change context profile: {exc}")
