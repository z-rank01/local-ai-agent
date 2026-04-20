"""Conversation list — sidebar listing all conversations from SQLite."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Label, ListItem, ListView, Static

from core.conversation_store import ConversationStore
from tui.utils import time_ago, truncate


class ConversationSelected(Message):
    """Posted when a conversation is selected."""

    def __init__(self, conversation_id: str) -> None:
        super().__init__()
        self.conversation_id = conversation_id


class ConversationItem(ListItem):
    """A single conversation entry in the sidebar."""

    def __init__(self, conv_id: str, title: str, updated: str) -> None:
        super().__init__()
        self.conv_id = conv_id
        self._title = title
        self._updated = updated
        self.add_class("conversation-item")

    def compose(self) -> ComposeResult:
        display = truncate(self._title, 24)
        ago = time_ago(self._updated)
        yield Static(f"{display}\n[dim]{ago}[/dim]", markup=True)


class ConversationList(Vertical):
    """Sidebar widget listing conversations."""

    selected_id: reactive[str] = reactive("")

    def __init__(self, store: ConversationStore, **kwargs) -> None:
        super().__init__(**kwargs)
        self._store = store

    def compose(self) -> ComposeResult:
        yield Static("📋 对话列表", id="sidebar-title")
        yield ListView(id="conversation-list")

    def on_mount(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        lv = self.query_one("#conversation-list", ListView)
        lv.clear()
        conversations = self._store.list_conversations(limit=100)
        for conv in conversations:
            item = ConversationItem(conv.id, conv.title, conv.updated_at)
            lv.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ConversationItem):
            self.selected_id = item.conv_id
            self.post_message(ConversationSelected(item.conv_id))
