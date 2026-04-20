"""Chat screen — main TUI screen combining all panels."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from textual import events
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
from tui.utils import ingest_local_file_paths

if TYPE_CHECKING:
    from core.agent import Agent, AgentEvent
    from core.conversation_store import ConversationStore


logger = logging.getLogger("tui.chat")


class ChatScreen(Screen):
    """The main chat interface screen."""

    BINDINGS = [
        Binding("pageup", "chat_page_up", "上翻", priority=True),
        Binding("pagedown", "chat_page_down", "下翻", priority=True),
        Binding("home", "chat_home", "顶部", priority=True),
        Binding("end", "chat_end", "底部", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "侧栏", priority=True),
        Binding("ctrl+e", "toggle_explorer", "文件管理器", priority=True),
        Binding("ctrl+n", "new_conversation", "新对话", priority=True),
        Binding("ctrl+d", "delete_conversation", "删除对话", priority=True),
    ]

    _COMPACT_WIDTH = 100

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
        self._sidebar_visible = True
        self._compact_mode = False

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
        self._apply_layout_mode(force_reset=True)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout_mode()

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
        messages = [
            {
                "role": message.role,
                "content": message.content,
                "thinking": message.thinking,
                "tool_name": message.tool_name,
            }
            for message in self._store.get_messages(conv_id)
        ]
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
        user_text, imported = ingest_local_file_paths(event.text, self._workspace_path)
        if imported:
            imported_names = ", ".join(__import__("pathlib").Path(path).name for path in imported)
            self._update_status(f"已导入文件: {imported_names}")
        else:
            user_text = event.text

        # Display user message
        chat_view.add_user_message(user_text)

        # Persist user message
        self._store.add_message(
            self._current_conv_id, role="user", content=user_text
        )

        # Auto-title on first message
        conv = self._store.get_conversation(self._current_conv_id)
        if conv and conv.title == "新对话" and len(conv.messages) <= 1:
            title = user_text[:40]
            self._store.update_conversation_title(self._current_conv_id, title)
            self._refresh_sidebar()

        # Disable input while agent is running
        input_bar.set_disabled(True)
        self._update_status("思考中...")

        # Start agent worker
        self._run_agent(user_text)

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

        try:
            async for event in self._agent.run(
                messages,
                session_id=self._current_conv_id,
                conversation_key=self._current_conv_id,
            ):
                if event.kind == "token":
                    pending = event.text
                    while pending:
                        if chat_view._in_thinking:
                            if "</think>" in pending:
                                before, pending = pending.split("</think>", 1)
                                if before:
                                    thinking_content += before
                                    chat_view.append_token(before)
                                chat_view.end_thinking()
                                pending = pending.lstrip("\n")
                                continue

                            thinking_content += pending
                            chat_view.append_token(pending)
                            pending = ""
                            continue

                        if "<think>" in pending:
                            before, pending = pending.split("<think>", 1)
                            if before:
                                if not chat_view._current_assistant:
                                    chat_view.add_assistant_message("")
                                full_response += before
                                chat_view.append_token(before)
                            chat_view.start_thinking()
                            self._update_status("thinking")
                            pending = pending.lstrip("\n")
                            continue

                        if not chat_view._current_assistant:
                            chat_view.add_assistant_message("")
                        full_response += pending
                        chat_view.append_token(pending)
                        pending = ""

                elif event.kind == "tool_start":
                    self._current_tool_widget = chat_view.add_tool_call(
                        event.data.get("name", ""),
                        event.text.split("`")[-1] if "`" in event.text else "",
                        collapsed=True,
                    )
                    self._update_status(f"tool {event.data.get('name', '')} running")

                elif event.kind == "tool_end":
                    if self._current_tool_widget:
                        status = "ok" if event.data.get("status") == "ok" else "error"
                        self._current_tool_widget.set_status(status, event.text)
                        self._current_tool_widget = None
                    if self._current_conv_id:
                        clean_status = "ok" if event.data.get("status") == "ok" else "error"
                        self._store.add_message(
                            self._current_conv_id,
                            role="tool",
                            content=f"[{clean_status}] {event.text}",
                            tool_name=event.data.get("name", "tool"),
                        )

                elif event.kind == "done":
                    if event.text and not full_response:
                        full_response = event.text
                        if chat_view._current_assistant and not chat_view._current_assistant.content:
                            chat_view._current_assistant.append_content(event.text)
                        elif not chat_view._current_assistant:
                            chat_view.add_assistant_message(event.text)
                    chat_view.finalize_response()

                    if full_response and self._current_conv_id:
                        self._store.add_message(
                            self._current_conv_id,
                            role="assistant",
                            content=full_response,
                            thinking=thinking_content,
                        )

                    self._refresh_sidebar()
                    self._update_status("ready")

                elif event.kind == "error":
                    chat_view.add_assistant_message(f"[错误] {event.text}")
                    self._update_status("error")

        except Exception as exc:
            logger.exception("Chat stream render failed")
            chat_view.add_assistant_message(f"[错误] {exc}")
            self._update_status("error")
        finally:
            input_bar = self.query_one("#input-area", InputBar)
            input_bar.set_disabled(False)
            input_bar.focus_input()

    # ── Actions ─────────────────────────────────────────────────────────

    def action_toggle_explorer(self) -> None:
        explorer = self.query_one("#file-explorer-container", FileExplorer)
        explorer.toggle_visible()

    def action_chat_page_up(self) -> None:
        self.query_one("#chat-scroll", ChatView).scroll_page_up(animate=False)

    def action_chat_page_down(self) -> None:
        self.query_one("#chat-scroll", ChatView).scroll_page_down(animate=False)

    def action_chat_home(self) -> None:
        self.query_one("#chat-scroll", ChatView).scroll_home(animate=False)

    def action_chat_end(self) -> None:
        self.query_one("#chat-scroll", ChatView).scroll_end(animate=False)

    def action_toggle_sidebar(self) -> None:
        if not self._compact_mode:
            self._update_status("sidebar always visible")
            return
        self._sidebar_visible = not self._sidebar_visible
        self._apply_sidebar_visibility()
        self._update_status("sidebar open" if self._sidebar_visible else "sidebar hidden")

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

    def _apply_layout_mode(self, force_reset: bool = False) -> None:
        try:
            sidebar = self.query_one("#sidebar", ConversationList)
        except Exception:
            return

        compact_mode = self.size.width < self._COMPACT_WIDTH
        if compact_mode != self._compact_mode or force_reset:
            self._compact_mode = compact_mode
            if compact_mode:
                self._sidebar_visible = False
            else:
                self._sidebar_visible = True

        sidebar.set_class(compact_mode, "narrow")
        self._apply_sidebar_visibility()

    def _apply_sidebar_visibility(self) -> None:
        try:
            sidebar = self.query_one("#sidebar", ConversationList)
        except Exception:
            return

        sidebar.set_class(self._compact_mode and not self._sidebar_visible, "collapsed")
