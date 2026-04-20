"""Chat view — scrollable container for conversation messages."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from .message_widget import (
    AssistantMessage,
    ThinkingWidget,
    ToolCallWidget,
    UserMessage,
)


class ChatView(VerticalScroll):
    """Main chat area showing the message stream."""

    DEFAULT_CSS = """
    ChatView {
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
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

    def start_thinking(self) -> ThinkingWidget:
        widget = ThinkingWidget()
        self._current_thinking = widget
        self._in_thinking = True
        self.mount(widget)
        return widget

    def end_thinking(self) -> None:
        self._in_thinking = False
        self._current_thinking = None

    def add_tool_call(
        self, tool_name: str, params_brief: str = ""
    ) -> ToolCallWidget:
        widget = ToolCallWidget(tool_name, params_brief)
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
            if role == "user":
                self.add_user_message(content)
            elif role == "assistant":
                # Check for thinking blocks
                import re
                think_match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
                if think_match:
                    tw = self.start_thinking()
                    tw.append_content(think_match.group(1).strip())
                    self.end_thinking()
                    clean = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
                    if clean:
                        self.add_assistant_message(clean)
                else:
                    self.add_assistant_message(content)
            elif role == "tool":
                # Show tool results as collapsed info
                pass  # Tool results are shown via ToolCallWidget during streaming
        self.scroll_end(animate=False)

    def finalize_response(self) -> None:
        """Mark the current response as complete."""
        self._current_assistant = None
        self._current_thinking = None
        self._in_thinking = False
