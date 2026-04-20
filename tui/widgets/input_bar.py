"""Input bar — text input with Enter-to-send, Shift+Enter for newline."""

from __future__ import annotations

from textual import events
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


class SubmitRequested(Message):
    """Internal message posted by ChatTextArea when Enter is pressed."""


class ChatTextArea(TextArea):
    """TextArea that sends on Enter and inserts newline on Shift+Enter.

    TextArea inserts a newline directly in ``_on_key`` for Enter, so we must
    intercept plain Enter there and leave Shift+Enter to the normal flow.
    """

    BINDINGS = TextArea.BINDINGS + [Binding("shift+enter", "newline", "Newline", show=False)]

    def action_submit(self) -> None:
        self.post_message(SubmitRequested())

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
            return
        await super()._on_key(event)


class InputBar(Vertical):
    """Bottom input area with text input and hint."""

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
        yield ChatTextArea(id="user-input")
        yield Static(
            "[dim]Enter 发送 · Shift+Enter 换行 · Ctrl+E 文件管理器[/dim]",
            markup=True,
            classes="input-hint",
        )

    def on_mount(self) -> None:
        ta = self.query_one("#user-input", ChatTextArea)
        ta.focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        pass

    def on_submit_requested(self, event: SubmitRequested) -> None:
        event.stop()
        self._submit()

    def _submit(self) -> None:
        if self._disabled:
            return
        ta = self.query_one("#user-input", ChatTextArea)
        text = ta.text.strip()
        if not text:
            return
        ta.clear()
        self.post_message(UserSubmitted(text))

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = disabled
        ta = self.query_one("#user-input", ChatTextArea)
        ta.disabled = disabled

    def focus_input(self) -> None:
        ta = self.query_one("#user-input", ChatTextArea)
        ta.focus()
