"""
Tool registry — loads tool definitions from YAML files at startup.

Adding a new static tool requires only a new YAML file; no Python changes needed.
"""

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from harness.capabilities.tools import OwnedTools

from . import config

logger = logging.getLogger("core.tool_registry")

_WEBSEARCH_BACKEND = "skill-websearch"
_STOCK_BRIDGE_BACKEND = "stock-bridge"
# Fail-safe: a tool must declare `cloud: read` to be reachable from a cloud model.
_DEFAULT_CLOUD_TIER = "write"

class ToolRegistry:
    """Loads tool metadata from a directory of YAML files."""

    def __init__(self, tools_dir: str | Path, *, enable_websearch: bool = False, stock_bridge_url: str = "") -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._owned = OwnedTools()
        self._load(str(tools_dir), enable_websearch, bool(stock_bridge_url))
        core_count = sum(1 for t in self._tools.values() if t.get("tier", "core") == "core")
        logger.info("ToolRegistry loaded %d tools (%d core, %d extended) from %s (websearch=%s, stock=%s)",
                     len(self._tools), core_count, len(self._tools) - core_count,
                     tools_dir, enable_websearch, bool(stock_bridge_url))

    def _load(self, tools_dir: str, enable_websearch: bool, stock_enabled: bool) -> None:
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
                if backend == _STOCK_BRIDGE_BACKEND and not stock_enabled:
                    logger.info("Skipping %s: stock bridge not configured", name)
                    continue
                self._tools[name] = tool
            except Exception as exc:
                logger.warning("Failed to load %s: %s", yaml_file.name, exc)
        by_backend: dict[str, list[dict]] = {}
        for tool in self._tools.values():
            by_backend.setdefault(tool['backend'], []).append(tool)
        for owner, definitions in by_backend.items():
            self._owned.replace(owner, definitions)

    def replace_owner(self, owner: str, definitions) -> None:
        """Replace one contribution atomically; unrelated owners remain registered."""
        self._owned.replace(owner, definitions)
        self._tools = self._owned.snapshot()

    def unregister_owner(self, owner: str) -> None:
        self.replace_owner(owner, ())

    def load_backend(self, tools_dir: str | Path, backend: str) -> None:
        """Compatibility adapter for existing YAML tool packages."""
        definitions = []
        for path in sorted(Path(tools_dir).glob('*.yaml')):
            tool = yaml.safe_load(path.read_text(encoding='utf-8'))
            if isinstance(tool, dict) and tool.get('backend') == backend:
                definitions.append(tool)
        self.replace_owner(backend, definitions)

    def ownership(self) -> dict[str, str]:
        return self._owned.owners()

    @property
    def known_tools(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get_backend(self, tool_name: str) -> str:
        return self._tools[tool_name]["backend"]

    def get_cloud_tier(self, tool_name: str) -> str:
        """``read`` or ``write``.  Undeclared tools are treated as ``write`` so a
        newly added tool never becomes cloud-reachable by accident."""
        return self._tools.get(tool_name, {}).get("cloud", _DEFAULT_CLOUD_TIER)

    def get_definitions(self, *, tier: str = "all", use_short_desc: bool = True,
                        allow_write: bool = True) -> list[dict[str, Any]]:
        """Return tool definitions in OpenAI function-calling format.

        ``allow_write=False`` withholds the tools that modify the workspace or
        execute code, so a cloud model can be granted read access without also
        granting writes.
        """
        defs = []
        for tool in self._tools.values():
            tool_tier = tool.get("tier", "core")
            if tier != "all" and tool_tier != tier:
                continue
            if not allow_write and self.get_cloud_tier(tool["name"]) != "read":
                continue

            params = deepcopy(tool.get("parameters", {"type": "object", "properties": {}}))
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
        """Result characters handed back to the model; 0 means unlimited.

        Global 0 disables router capping. With a positive cap, a smaller explicit
        per-tool limit wins. Service pagination remains a separate contract.
        """
        global_cap = config.TOOL_RESULT_MAX_CHARS
        if global_cap <= 0:
            return 0
        override = self._tools.get(tool_name, {}).get('max_result_chars', global_cap)
        return min(global_cap, int(override)) if int(override) > 0 else global_cap
