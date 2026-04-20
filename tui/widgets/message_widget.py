"""Message display widgets — user, assistant, thinking, tool calls."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Markdown, Static


class UserMessage(Static):
    """Displays a user message."""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 0 0 4;
        padding: 1 2;
        background: $primary-darken-2;
        border: round $primary;
    }
    """

    def __init__(self, content: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content

    def compose(self) -> ComposeResult:
        yield Static("[b green]👤 You[/b green]", markup=True, classes="msg-label")
        yield Static(self._content)


class AssistantMessage(Vertical):
    """Displays an assistant message with Markdown rendering."""

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 4 0 0;
        padding: 1 2;
        background: $surface-darken-1;
        border: round $secondary;
    }
    """

    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content
        self._md_widget: Markdown | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b cyan]🤖 Assistant[/b cyan]", markup=True, classes="msg-label")
        self._md_widget = Markdown(self._content)
        yield self._md_widget

    def append_content(self, text: str) -> None:
        """Append streaming text to the message."""
        self._content += text
        if self._md_widget:
            self._md_widget.update(self._content)

    @property
    def content(self) -> str:
        return self._content


class ThinkingWidget(Collapsible):
    """Collapsible widget showing the model's thinking process."""

    DEFAULT_CSS = """
    ThinkingWidget {
        margin: 0 4 0 0;
        padding: 0 1;
        border-left: tall $warning;
    }
    """

    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(title="💭 思考过程", collapsed=True, **kwargs)
        self._content = content
        self._label: Static | None = None

    def compose(self) -> ComposeResult:
        self._label = Static(self._content, classes="thinking-text")
        yield self._label

    def append_content(self, text: str) -> None:
        self._content += text
        if self._label:
            self._label.update(self._content)


class ToolCallWidget(Static):
    """Displays a tool call with status."""

    DEFAULT_CSS = """
    ToolCallWidget {
        margin: 0 4 0 0;
        padding: 0 2;
        border-left: tall $accent;
        height: auto;
    }
    """

    def __init__(
        self,
        tool_name: str,
        params_brief: str = "",
        status: str = "running",
        status_text: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._params_brief = params_brief
        self._status = status
        self._status_text = status_text
        self._render()

    def _render(self) -> None:
        if self._status == "running":
            icon = "⏳"
            status = "运行中..."
        elif self._status == "ok":
            icon = "✅"
            status = self._status_text or "成功"
        else:
            icon = "❌"
            status = self._status_text or "失败"

        text = f"🔧 [b]{self._tool_name}[/b] {self._params_brief} — {icon} {status}"
        self.update(text)

    def set_status(self, status: str, text: str = "") -> None:
        self._status = status
        self._status_text = text
        self._render()
