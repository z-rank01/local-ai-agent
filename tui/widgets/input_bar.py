"""Input bar — text input with Enter-to-send, Shift+Enter for newline."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea


class UserSubmitted(Message):
    """Posted when the user submits a message."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class InputBar(Vertical):
    """Bottom input area with text input and hint."""

    BINDINGS = [
        Binding("escape", "blur_input", "Blur", show=False),
    ]

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 10;
        padding: 0 1;
        border-top: solid $primary-darken-2;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._disabled = False

    def compose(self) -> ComposeResult:
        yield TextArea(id="user-input")
        yield Static(
            "[dim]Enter 发送 · Shift+Enter 换行 · Ctrl+E 文件管理器[/dim]",
            markup=True,
            classes="input-hint",
        )

    def on_mount(self) -> None:
        ta = self.query_one("#user-input", TextArea)
        ta.focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        pass

    def on_key(self, event) -> None:
        if event.key == "enter" and not event.shift:
            event.prevent_default()
            event.stop()
            self._submit()

    def _submit(self) -> None:
        if self._disabled:
            return
        ta = self.query_one("#user-input", TextArea)
        text = ta.text.strip()
        if not text:
            return
        ta.clear()
        self.post_message(UserSubmitted(text))

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = disabled
        ta = self.query_one("#user-input", TextArea)
        ta.disabled = disabled

    def focus_input(self) -> None:
        ta = self.query_one("#user-input", TextArea)
        ta.focus()
