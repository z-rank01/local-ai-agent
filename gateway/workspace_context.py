"""
Workspace context — fetches workspace overview + memory index at conversation start.

Provides dynamic context sections that are injected into the system prompt so
the LLM knows what files exist and what the user has stored in memory.
"""

import logging
from typing import Any

logger = logging.getLogger("gateway.workspace_ctx")

# Directories to list for workspace overview
_OVERVIEW_DIRS = ["/workspace/data"]

_MEMORY_INDEX = "/workspace/.memory/MEMORY.md"

# Max chars for directory listing in the prompt
_MAX_DIR_CHARS = 1500
# Max chars for memory index in the prompt
_MAX_MEMORY_CHARS = 2000


async def fetch_workspace_context(
    router: Any,
    session_id: str = "workspace-ctx",
) -> list[str]:
    """Fetch workspace directory overview and memory index.

    Returns a list of text sections suitable for ``PromptBuilder.build(extra_sections=...)``.
    Each section is a pre-formatted string ready for injection.
    """
    sections: list[str] = []

    # 1. Directory overview
    dir_text = await _fetch_dir_overview(router, session_id)
    if dir_text:
        sections.append(dir_text)

    # 2. Memory index
    mem_text = await _fetch_memory_index(router, session_id)
    if mem_text:
        sections.append(mem_text)

    return sections


async def _fetch_dir_overview(router: Any, session_id: str) -> str | None:
    """Fetch file listing for workspace data directories."""
    listings: list[str] = []
    for dir_path in _OVERVIEW_DIRS:
        try:
            result = await router.dispatch("file_list", {"directory": dir_path}, session_id)
            if isinstance(result, dict):
                entries = result.get("entries") or result.get("content") or result.get("files")
                if isinstance(entries, list):
                    lines = _format_entries(entries)
                    listings.append(f"📂 {dir_path}/\n{lines}")
                elif isinstance(entries, str):
                    truncated = entries[:_MAX_DIR_CHARS]
                    listings.append(f"📂 {dir_path}/\n{truncated}")
        except Exception as exc:
            logger.debug("Cannot list %s: %s", dir_path, exc)

    if not listings:
        return None

    text = "\n".join(listings)
    if len(text) > _MAX_DIR_CHARS:
        text = text[:_MAX_DIR_CHARS] + "\n...(更多文件省略)"

    return f"## Workspace 当前文件概况\n\n{text}"


def _format_entries(entries: list) -> str:
    """Format file_list entries into compact display lines."""
    lines: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name", "?")
            is_dir = entry.get("is_dir") or entry.get("type") == "directory"
            size = entry.get("size")
            if is_dir:
                lines.append(f"  📁 {name}/")
            elif size is not None:
                size_kb = size / 1024
                if size_kb > 1024:
                    lines.append(f"  📄 {name}  ({size_kb/1024:.1f} MB)")
                else:
                    lines.append(f"  📄 {name}  ({size_kb:.0f} KB)")
            else:
                lines.append(f"  📄 {name}")
        elif isinstance(entry, str):
            lines.append(f"  {entry}")
    return "\n".join(lines[:50])  # cap at 50 entries


async def _fetch_memory_index(router: Any, session_id: str) -> str | None:
    """Read the workspace memory index file."""
    try:
        result = await router.dispatch(
            "file_read", {"path": _MEMORY_INDEX}, session_id
        )
        if isinstance(result, dict):
            content = result.get("content", "")
            if content and not result.get("error"):
                if len(content) > _MAX_MEMORY_CHARS:
                    content = content[:_MAX_MEMORY_CHARS] + "\n...(记忆索引已截断)"
                return f"## Workspace 记忆\n\n{content}"
    except FileNotFoundError:
        logger.debug("No memory index at %s", _MEMORY_INDEX)
    except Exception as exc:
        logger.debug("Cannot read memory index: %s", exc)

    return None
