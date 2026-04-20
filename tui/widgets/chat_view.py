"""Chat view — scrollable container for conversation messages."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll

from .message_widget import (
    AssistantMessage,
    ThinkingWidget,
    ToolCallWidget,
    UserMessage,
)


class ChatView(VerticalScroll):
    """Main chat area showing the message stream."""

    BINDINGS = [
        Binding("pageup", "scroll_page_up", "Up", show=False),
        Binding("pagedown", "scroll_page_down", "Down", show=False),
        Binding("home", "scroll_home", "Top", show=False),
        Binding("end", "scroll_end", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    ChatView {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.can_focus = True
        self.show_vertical_scrollbar = True
        self._current_assistant: AssistantMessage | None = None
        self._current_thinking: ThinkingWidget | None = None
        self._in_thinking = False

    def clear_messages(self) -> None:
        """Remove all messages from the view."""
        self.remove_children()
        self._current_assistant = None
        self._current_thinking = None
        self._in_thinking = False

    def add_user_message(self, content: str) -> UserMessage:
        widget = UserMessage(content)
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def add_assistant_message(self, content: str = "") -> AssistantMessage:
        widget = AssistantMessage(content)
        self._current_assistant = widget
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def start_thinking(self, *, collapsed: bool = False) -> ThinkingWidget:
        widget = ThinkingWidget(collapsed=collapsed)
        self._current_thinking = widget
        self._in_thinking = True
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def end_thinking(self) -> None:
        self._in_thinking = False
        self._current_thinking = None

    def add_tool_call(
        self, tool_name: str, params_brief: str = "", *, collapsed: bool = True
    ) -> ToolCallWidget:
        widget = ToolCallWidget(tool_name, params_brief, collapsed=collapsed)
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def append_token(self, token: str) -> None:
        """Append a streaming token to the current assistant message or thinking."""
        if self._in_thinking and self._current_thinking:
            self._current_thinking.append_content(token)
        elif self._current_assistant:
            self._current_assistant.append_content(token)
        else:
            # Auto-create assistant message if needed
            self.add_assistant_message(token)
        self.scroll_end(animate=False)

    def load_history(self, messages: list[dict]) -> None:
        """Load historical messages into the view."""
        self.clear_messages()
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            thinking = (msg.get("thinking") or "").strip()
            if role == "user":
                self.add_user_message(content)
            elif role == "assistant":
                if thinking:
                    tw = self.start_thinking(collapsed=True)
                    tw.append_content(thinking)
                    self.end_thinking()
                elif "<think>" in content:
                    import re
                    think_match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
                    if think_match:
                        tw = self.start_thinking(collapsed=True)
                        tw.append_content(think_match.group(1).strip())
                        self.end_thinking()
                        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

                if content:
                    self.add_assistant_message(content)
            elif role == "tool":
                tool_name = msg.get("tool_name") or "tool"
                text = content.strip()
                status = "error" if text.lower().startswith("[error]") else "ok"
                details = text.partition("] ")[2] if text.startswith("[") else text
                widget = self.add_tool_call(tool_name, collapsed=True)
                widget.set_status(status, details)
        self.scroll_end(animate=False)

    def finalize_response(self) -> None:
        """Mark the current response as complete."""
        self._current_assistant = None
        self._current_thinking = None
        self._in_thinking = False

    def action_scroll_page_up(self) -> None:
        self.scroll_page_up(animate=False)

    def action_scroll_page_down(self) -> None:
        self.scroll_page_down(animate=False)

    def action_scroll_home(self) -> None:
        self.scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.scroll_end(animate=False)
