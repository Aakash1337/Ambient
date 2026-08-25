"""Small pre-launch mode chooser used by the desktop shortcut.

It intentionally starts before AmbientController: choosing voice changes model
bootstrap, controller construction, and which background worker exists. Keeping
that decision outside the live application avoids a second capture/Whisper
pipeline and makes Cancel a true no-op.
"""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

ASSIST_EXIT = 0
VOICE_EXIT = 10
CANCEL_EXIT = 20
EMERGENCY_EXIT = 30
WEB_EXIT = 40
WEB_VOICE_EXIT = 50


class EmergencyConfirm(ModalScreen[bool]):
    """Explicit guard before the pinned fallback is allowed to take over."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("q", "cancel", "Cancel", priority=True),
    ]
    DEFAULT_CSS = """
    EmergencyConfirm {
        align: center middle;
        background: #000000 70%;
    }

    EmergencyConfirm > #emergency-dialog {
        width: 68;
        height: 17;
        padding: 1 3;
        border: tall #d9534f;
        background: #14171c;
    }

    EmergencyConfirm #emergency-title {
        height: 3;
        content-align: center middle;
        text-align: center;
        text-style: bold;
        color: #ff6b63;
    }

    EmergencyConfirm #emergency-copy {
        height: 7;
        text-align: center;
        color: #d8dde7;
    }

    EmergencyConfirm Horizontal {
        height: 4;
        align: center middle;
    }

    EmergencyConfirm Button {
        width: 26;
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="emergency-dialog"):
            yield Static("EMERGENCY FALLBACK", id="emergency-title")
            yield Static(
                "Start the pinned pre-voice demo build?\n\n"
                "This leaves your working files untouched, but it may stop a "
                "verified running Ambient process before taking over.",
                id="emergency-copy",
            )
            with Horizontal():
                yield Button("Cancel", id="cancel-emergency", variant="primary")
                yield Button(
                    "Start fallback", id="confirm-emergency", variant="error"
                )

    def on_mount(self) -> None:
        self.query_one("#cancel-emergency", Button).focus()

    @on(Button.Pressed, "#cancel-emergency")
    def _cancel_pressed(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm-emergency")
    def _confirm_pressed(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class WebModePicker(ModalScreen[str | None]):
    """Choose browser delivery without starting either application role yet."""

    BINDINGS = [
        Binding("a", "assist", "Web Assist", priority=True),
        Binding("v", "voice", "Web Voice", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        Binding("q", "cancel", "Back", priority=True),
    ]
    DEFAULT_CSS = """
    WebModePicker {
        align: center middle;
        background: #000000 70%;
    }

    WebModePicker > #web-mode-dialog {
        width: 68;
        height: 19;
        padding: 1 3;
        border: tall #e6a700;
        background: #14171c;
    }

    WebModePicker #web-mode-title {
        height: 2;
        content-align: center middle;
        text-align: center;
        text-style: bold;
        color: #ffbd2e;
    }

    WebModePicker #web-mode-copy {
        height: 3;
        text-align: center;
        color: #d8dde7;
    }

    WebModePicker Button {
        width: 100%;
        height: 4;
        margin: 0;
        text-align: left;
    }

    WebModePicker #web-mode-hint {
        height: 1;
        text-align: center;
        color: #7f8999;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="web-mode-dialog"):
            yield Static("WEB CONSOLE", id="web-mode-title")
            yield Static(
                "Choose how answers should be delivered in the browser.",
                id="web-mode-copy",
            )
            yield Button(
                "WEB ASSIST  ·  answers stay on screen\n"
                "Silent and demo-safe; this is the default",
                id="web-assist",
                variant="primary",
            )
            yield Button(
                "WEB VOICE  ·  answers appear and are spoken\n"
                "Press G for Q&A/Agent interaction · R changes delivery",
                id="web-voice",
            )
            yield Static(
                "Enter selects  •  A/V chooses  •  Esc goes back",
                id="web-mode-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#web-assist", Button).focus()

    @on(Button.Pressed, "#web-assist")
    def _assist_pressed(self) -> None:
        self.action_assist()

    @on(Button.Pressed, "#web-voice")
    def _voice_pressed(self) -> None:
        self.action_voice()

    def action_assist(self) -> None:
        self.dismiss("web")

    def action_voice(self) -> None:
        self.dismiss("web_voice")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModePickerApp(App[str | None]):
    """One-screen launch choice; no audio, models, network, or app lock yet."""

    TITLE = "Ambient"
    SUB_TITLE = "Choose this session's mode"
    BINDINGS = [
        # Keep launcher bindings below modal-screen bindings. Otherwise the
        # launcher's V/Q actions can escape through the Web/Emergency dialog
        # and select or cancel the entire application instead of the dialog.
        Binding("a", "assist", "Assist"),
        Binding("v", "voice", "Voice"),
        Binding("w", "web", "Web"),
        Binding("1", "assist", "Assist", show=False),
        Binding("2", "voice", "Voice", show=False),
        Binding("3", "web", "Web", show=False),
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]
    CSS = """
    Screen {
        align: center middle;
        background: #07090d;
    }

    /* Four options must fit a stock 80x24 terminal: the desktop shortcut
       opens the distribution's default window, and a clipped splash makes
       the bottom row unreachable by mouse. Hence the tighter brand/prompt
       rows and no inter-button margins. */
    #splash {
        width: 72;
        height: 22;
        padding: 0 3;
        border: tall #e6a700;
        background: #101318;
    }

    #brand {
        height: 1;
        content-align: center middle;
        text-align: center;
        color: #ffbd2e;
        text-style: bold;
    }

    #prompt {
        height: 1;
        content-align: center middle;
        text-align: center;
        color: #d8dde7;
    }

    #splash > Button {
        width: 100%;
        height: 4;
        margin: 0;
        text-align: left;
    }

    #splash > Button:focus {
        border: tall #ffbd2e;
        text-style: bold;
    }

    #hint {
        height: 1;
        margin-top: 0;
        text-align: center;
        color: #7f8999;
    }

    Footer {
        background: #101318;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="splash"):
            yield Static("AMBIENT", id="brand")
            yield Static("How should answers be delivered?", id="prompt")
            yield Button(
                "ASSIST MODE  ·  answers stay on screen\n"
                "Best for calls, interviews, and silent coaching",
                id="assist",
                variant="primary",
            )
            yield Button(
                "VOICE MODE  ·  answers appear and are spoken\n"
                "Use G for Agent interaction with any knowledge profile",
                id="voice",
            )
            yield Button(
                "WEB CONSOLE  ·  choose silent or spoken browser answers\n"
                "Opens automatically; next choose Web Assist or Web Voice",
                id="web",
            )
            yield Button(
                "EMERGENCY FALLBACK  ·  pinned pre-voice demo build\n"
                "Only if needed; requires a second confirmation",
                id="emergency",
                variant="error",
            )
            yield Static(
                "Enter selects  •  A/V/W or 1/2/3 chooses  •  Esc cancels",
                id="hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#assist", Button).focus()

    @on(Button.Pressed, "#assist")
    def _assist_pressed(self) -> None:
        self.action_assist()

    @on(Button.Pressed, "#voice")
    def _voice_pressed(self) -> None:
        self.action_voice()

    @on(Button.Pressed, "#web")
    def _web_pressed(self) -> None:
        self.action_web()

    @on(Button.Pressed, "#emergency")
    def _emergency_pressed(self) -> None:
        self.push_screen(EmergencyConfirm(), self._emergency_confirmed)

    def _emergency_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.exit("emergency")

    def action_assist(self) -> None:
        self.exit("assist")

    def action_voice(self) -> None:
        self.exit("voice")

    def action_web(self) -> None:
        self.push_screen(WebModePicker(), self._web_mode_selected)

    def _web_mode_selected(self, selection: str | None) -> None:
        if selection is not None:
            self.exit(selection)

    def action_cancel(self) -> None:
        self.exit(None)


def main() -> None:
    selection = ModePickerApp().run()
    if selection == "assist":
        raise SystemExit(ASSIST_EXIT)
    if selection == "voice":
        raise SystemExit(VOICE_EXIT)
    if selection == "web":
        raise SystemExit(WEB_EXIT)
    if selection == "web_voice":
        raise SystemExit(WEB_VOICE_EXIT)
    if selection == "emergency":
        raise SystemExit(EMERGENCY_EXIT)
    raise SystemExit(CANCEL_EXIT)


if __name__ == "__main__":
    main()
