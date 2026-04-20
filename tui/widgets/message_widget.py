"""Message display widgets — user, assistant, thinking, tool calls."""

from __future__ import annotations

import re

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Markdown, Static


class UserMessage(Static):
    """Displays a user message."""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 0 0 0;
        padding: 1 2;
        background: #151a20;
        border: round #2b3540;
        height: auto;
        width: 1fr;
    }

    UserMessage .msg-label {
        color: #8cb4ff;
        text-style: bold;
        margin-bottom: 1;
    }

    UserMessage .msg-body {
        color: #eef2f7;
        height: auto;
        width: 1fr;
    }
    """

    def __init__(self, content: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content

    def compose(self) -> ComposeResult:
        yield Static("You", classes="msg-label")
        yield Static(self._content, classes="msg-body")


class AssistantMessage(Vertical):
    """Displays an assistant message with Markdown rendering."""

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 0 0 0;
        padding: 1 2;
        background: #101317;
        border: round #242c36;
        height: auto;
        width: 1fr;
    }

    AssistantMessage .msg-label {
        color: #d3d7de;
        text-style: bold;
        margin-bottom: 1;
    }

    AssistantMessage Markdown {
        color: #f1f3f5;
        background: transparent;
        width: 1fr;
        height: auto;
    }
    """

    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content
        self._md_widget: Markdown | None = None

    def compose(self) -> ComposeResult:
        yield Static("Assistant", classes="msg-label")
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


class ThinkingWidget(Static):
    """Compact foldable widget showing the model's thinking process."""

    DEFAULT_CSS = """
    ThinkingWidget {
        margin: 0 0 0 0;
        padding: 0 0 0 2;
        border-left: tall #3a4048;
        height: auto;
        width: 1fr;
        color: #9aa3ad;
    }
    """

    def __init__(self, content: str = "", collapsed: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content
        self._collapsed = collapsed
        self.add_class("thinking-block")

    def on_mount(self) -> None:
        self._refresh_view()

    def on_click(self, event: events.Click) -> None:
        self._collapsed = not self._collapsed
        self._refresh_view()
        event.stop()

    def append_content(self, text: str) -> None:
        self._content += text
        self._refresh_view()

    def _refresh_view(self) -> None:
        marker = ">" if self._collapsed else "v"
        body = self._content.strip()
        if self._collapsed or not body:
            self.update(f"{marker} thinking")
        else:
            self.update(f"{marker} thinking\n  {body}")


class ToolCallWidget(Static):
    """Displays a foldable tool call with status."""

    DEFAULT_CSS = """
    ToolCallWidget {
        margin: 0 0 0 0;
        padding: 0 0 0 2;
        border-left: tall #3a4048;
        height: auto;
        width: 1fr;
        color: #c4cad1;
    }

    ToolCallWidget.tool-running {
        color: #9aa3ad;
    }

    ToolCallWidget.tool-ok {
        color: #8fbf8f;
    }

    ToolCallWidget.tool-error {
        color: #d28b8b;
    }
    """

    def __init__(
        self,
        tool_name: str,
        params_brief: str = "",
        status: str = "running",
        status_text: str = "",
        collapsed: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._params_brief = params_brief
        self._status = status
        self._status_text = status_text
        self._collapsed = collapsed
        self.add_class("tool-call-block")

    def on_mount(self) -> None:
        self._refresh_view()

    def on_click(self, event: events.Click) -> None:
        if not self._detail_text():
            return
        self._collapsed = not self._collapsed
        self._refresh_view()
        event.stop()

    def _refresh_view(self) -> None:
        if self._status == "running":
            status = "running"
            status_class = "tool-running"
        elif self._status == "ok":
            status = "ok"
            status_class = "tool-ok"
        else:
            status = "error"
            status_class = "tool-error"

        self.remove_class("tool-running")
        self.remove_class("tool-ok")
        self.remove_class("tool-error")
        self.add_class(status_class)

        details = self._detail_text()
        marker = ">" if self._collapsed and details else "v"
        if self._collapsed or not details:
            self.update(f"{marker} {self._tool_name} {status}" if details else f"· {self._tool_name} {status}")
        else:
            self.update(f"{marker} {self._tool_name} {status}\n  {details}")

    def _detail_text(self) -> str:
        details = self._params_brief.strip()
        if self._status_text:
            details = self._status_text.strip()
        details = re.sub(r"\s+", " ", details).strip()
        if len(details) > 240:
            details = details[:237] + "..."
        return details

    def set_status(self, status: str, text: str = "") -> None:
        self._status = status
        self._status_text = text
        self._refresh_view()
