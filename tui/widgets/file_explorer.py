"""File explorer — workspace file tree (bottom panel, toggle with Ctrl+E)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DirectoryTree, Static


class FileSelected(Message):
    """Posted when a file is double-clicked in the explorer."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path


class FileExplorer(Vertical):
    """Collapsible workspace file tree panel."""

    DEFAULT_CSS = """
    FileExplorer {
        height: 25%;
        min-height: 5;
        border-top: solid $primary-darken-2;
        display: none;
    }

    FileExplorer.visible {
        display: block;
    }
    """

    def __init__(self, workspace_path: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._workspace_path = Path(workspace_path)

    def compose(self) -> ComposeResult:
        yield Static("workspace", id="file-explorer-title")
        if self._workspace_path.is_dir():
            yield DirectoryTree(str(self._workspace_path), id="file-explorer-tree")
        else:
            yield Static(f"[dim]目录不存在: {self._workspace_path}[/dim]", markup=True)

    def toggle_visible(self) -> None:
        self.toggle_class("visible")

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self.post_message(FileSelected(str(event.path)))
