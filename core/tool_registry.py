"""
Tool registry — loads tool definitions from YAML files at startup.

Adding a new static tool requires only a new YAML file; no Python changes needed.
"""

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("core.tool_registry")

_WEBSEARCH_BACKEND = "skill-websearch"

_DEFAULT_MAX_RESULT_CHARS: dict[str, int] = {
    "file_read": 8000,
    "file_list": 4000,
    "code_exec": 6000,
    "shell_exec": 4000,
    "web_search": 4000,
    "web_fetch": 8000,
    "file_convert": 8000,
    "git_status": 2000,
    "skill_run": 6000,
    "conversation_search": 5000,
    "conversation_read": 7000,
}


class ToolRegistry:
    """Loads tool metadata from a directory of YAML files."""

    def __init__(self, tools_dir: str | Path, *, enable_websearch: bool = False) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._load(str(tools_dir), enable_websearch)
        core_count = sum(1 for t in self._tools.values() if t.get("tier", "core") == "core")
        logger.info("ToolRegistry loaded %d tools (%d core, %d extended) from %s (websearch=%s)",
                     len(self._tools), core_count, len(self._tools) - core_count,
                     tools_dir, enable_websearch)

    def _load(self, tools_dir: str, enable_websearch: bool) -> None:
        tools_path = Path(tools_dir)
        if not tools_path.is_dir():
            logger.warning("tools_dir %r does not exist — no tools loaded", tools_dir)
            return
        for yaml_file in sorted(tools_path.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    tool = yaml.safe_load(f)
                name = tool.get("name")
                backend = tool.get("backend")
                if not name or not backend:
                    logger.warning("Skipping %s: missing 'name' or 'backend'", yaml_file.name)
                    continue
                if backend == _WEBSEARCH_BACKEND and not enable_websearch:
                    logger.info("Skipping %s: websearch feature disabled", name)
                    continue
                self._tools[name] = tool
            except Exception as exc:
                logger.warning("Failed to load %s: %s", yaml_file.name, exc)

    @property
    def known_tools(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get_backend(self, tool_name: str) -> str:
        return self._tools[tool_name]["backend"]

    def get_definitions(self, *, tier: str = "all",
                        use_short_desc: bool = False) -> list[dict[str, Any]]:
        """Return tool definitions in OpenAI function-calling format."""
        defs = []
        for tool in self._tools.values():
            tool_tier = tool.get("tier", "core")
            if tier != "all" and tool_tier != tier:
                continue

            params = tool.get("parameters", {"type": "object", "properties": {}})
            if isinstance(params.get("required"), str):
                try:
                    params["required"] = json.loads(params["required"])
                except Exception:
                    params.pop("required", None)

            if use_short_desc:
                desc = tool.get("short_description") or tool.get("description", "")
            else:
                desc = tool.get("description", "")

            defs.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": desc,
                    "parameters": params,
                },
            })
        return defs

    def get_max_result_chars(self, tool_name: str) -> int:
        tool = self._tools.get(tool_name, {})
        return tool.get(
            "max_result_chars",
            _DEFAULT_MAX_RESULT_CHARS.get(tool_name, 0),
        )
