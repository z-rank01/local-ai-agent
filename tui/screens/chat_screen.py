"""Chat screen — main TUI screen combining all panels."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.worker import Worker, WorkerState

from tui.widgets.chat_view import ChatView
from tui.widgets.conversation_list import ConversationList, ConversationSelected
from tui.widgets.file_explorer import FileExplorer, FileSelected
from tui.widgets.input_bar import InputBar, UserSubmitted
from tui.widgets.message_widget import ToolCallWidget

if TYPE_CHECKING:
    from core.agent import Agent, AgentEvent
    from core.conversation_store import ConversationStore


class ChatScreen(Screen):
    """The main chat interface screen."""

    BINDINGS = [
        Binding("ctrl+e", "toggle_explorer", "文件管理器", priority=True),
        Binding("ctrl+n", "new_conversation", "新对话", priority=True),
        Binding("ctrl+d", "delete_conversation", "删除对话", priority=True),
    ]

    def __init__(
        self,
        agent: Agent,
        store: ConversationStore,
        workspace_path: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._agent = agent
        self._store = store
        self._workspace_path = workspace_path
        self._current_conv_id: str | None = None
        self._agent_worker: Worker | None = None
        self._current_tool_widget: ToolCallWidget | None = None

    def compose(self) -> ComposeResult:
        yield Static("Local AI Agent v2.0", id="app-header")
        with Horizontal(id="main-container"):
            yield ConversationList(self._store, id="sidebar")
            with Vertical(id="chat-area"):
                yield ChatView(id="chat-scroll")
                yield InputBar(id="input-area")
                yield FileExplorer(self._workspace_path, id="file-explorer-container")
        yield Static("就绪", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        # Auto-create a conversation if none exist
        conversations = self._store.list_conversations(limit=1)
        if conversations:
            self._load_conversation(conversations[0].id)
        else:
            self._new_conversation()

    # ── Conversation management ─────────────────────────────────────────

    def _new_conversation(self) -> None:
        conv = self._store.create_conversation(
            title="新对话", model=self._agent.llm.model
        )
        self._current_conv_id = conv.id
        self.query_one("#chat-scroll", ChatView).clear_messages()
        self._refresh_sidebar()
        self._update_status("新对话已创建")

    def _load_conversation(self, conv_id: str) -> None:
        self._current_conv_id = conv_id
        messages = self._store.messages_as_dicts(conv_id)
        chat_view = self.query_one("#chat-scroll", ChatView)
        chat_view.load_history(messages)
        conv = self._store.get_conversation(conv_id)
        if conv:
            self._update_status(f"已加载: {conv.title}")

    def _refresh_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", ConversationList)
        sidebar.refresh_list()

    # ── Event handlers ──────────────────────────────────────────────────

    def on_conversation_selected(self, event: ConversationSelected) -> None:
        self._load_conversation(event.conversation_id)

    def on_user_submitted(self, event: UserSubmitted) -> None:
        if not self._current_conv_id:
            self._new_conversation()

        chat_view = self.query_one("#chat-scroll", ChatView)
        input_bar = self.query_one("#input-area", InputBar)

        # Display user message
        chat_view.add_user_message(event.text)

        # Persist user message
        self._store.add_message(
            self._current_conv_id, role="user", content=event.text
        )

        # Auto-title on first message
        conv = self._store.get_conversation(self._current_conv_id)
        if conv and conv.title == "新对话" and len(conv.messages) <= 1:
            title = event.text[:40]
            self._store.update_conversation_title(self._current_conv_id, title)
            self._refresh_sidebar()

        # Disable input while agent is running
        input_bar.set_disabled(True)
        self._update_status("思考中...")

        # Start agent worker
        self._run_agent(event.text)

    def on_file_selected(self, event: FileSelected) -> None:
        """Insert file path into input bar when double-clicked."""
        try:
            input_bar = self.query_one("#input-area", InputBar)
            ta = input_bar.query_one("#user-input")
            ta.insert(event.path)
        except Exception:
            pass

    # ── Agent integration ───────────────────────────────────────────────

    def _run_agent(self, user_text: str) -> None:
        """Start the agent loop in a background worker."""
        messages = self._store.messages_as_dicts(self._current_conv_id)
        self._agent_worker = self.run_worker(
            self._agent_stream(messages),
            name="agent",
            exclusive=True,
        )

    async def _agent_stream(self, messages: list[dict]) -> None:
        """Consume agent events and update the UI."""
        chat_view = self.query_one("#chat-scroll", ChatView)
        full_response = ""
        thinking_content = ""

        async for event in self._agent.run(
            messages,
            session_id=self._current_conv_id,
            conversation_key=self._current_conv_id,
        ):
            if event.kind == "token":
                token = event.text
                # Handle thinking tags in stream
                if "<think>" in token:
                    chat_view.start_thinking()
                    token = token.replace("<think>", "").replace("\n", "", 1)
                if "</think>" in token:
                    token = token.replace("</think>", "").strip()
                    chat_view.end_thinking()
                    if not chat_view._current_assistant:
                        chat_view.add_assistant_message("")
                    continue

                if chat_view._in_thinking:
                    thinking_content += token
                    chat_view.append_token(token)
                else:
                    if not chat_view._current_assistant:
                        chat_view.add_assistant_message("")
                    full_response += token
                    chat_view.append_token(token)

            elif event.kind == "tool_start":
                self._current_tool_widget = chat_view.add_tool_call(
                    event.data.get("name", ""),
                    event.text.split("`")[-1] if "`" in event.text else "",
                )
                self._update_status(f"🔧 {event.data.get('name', '')}...")

            elif event.kind == "tool_end":
                if self._current_tool_widget:
                    status = "ok" if event.data.get("status") == "ok" else "error"
                    self._current_tool_widget.set_status(status, event.text)
                    self._current_tool_widget = None

            elif event.kind == "done":
                if event.text and not full_response:
                    full_response = event.text
                chat_view.finalize_response()

                # Persist assistant response
                if full_response and self._current_conv_id:
                    self._store.add_message(
                        self._current_conv_id,
                        role="assistant",
                        content=full_response,
                        thinking=thinking_content,
                    )

                self._update_status("就绪")

            elif event.kind == "error":
                chat_view.add_assistant_message(f"[错误] {event.text}")
                self._update_status("错误")

        # Re-enable input
        input_bar = self.query_one("#input-area", InputBar)
        input_bar.set_disabled(False)
        input_bar.focus_input()

    # ── Actions ─────────────────────────────────────────────────────────

    def action_toggle_explorer(self) -> None:
        explorer = self.query_one("#file-explorer-container", FileExplorer)
        explorer.toggle_visible()

    def action_new_conversation(self) -> None:
        self._new_conversation()

    def action_delete_conversation(self) -> None:
        if self._current_conv_id:
            self._store.delete_conversation(self._current_conv_id)
            self._current_conv_id = None
            self.query_one("#chat-scroll", ChatView).clear_messages()
            self._refresh_sidebar()
            # Load next conversation or create new
            conversations = self._store.list_conversations(limit=1)
            if conversations:
                self._load_conversation(conversations[0].id)
            else:
                self._new_conversation()

    # ── Helpers ─────────────────────────────────────────────────────────

    def _update_status(self, text: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(text)
        except Exception:
            pass
