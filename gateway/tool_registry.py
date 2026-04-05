"""
Tool registry — loads tool definitions from config/tools/*.yaml at startup.

Adding a new static tool requires only a new YAML file; no Python changes needed.
Each YAML must have: name, backend (skill-files | skill-runner | skill-websearch), description, parameters.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("gateway.tool_registry")

_WEBSEARCH_BACKEND = "skill-websearch"


class ToolRegistry:
    """Loads tool metadata from a directory of YAML files."""

    def __init__(self, tools_dir: str, *, enable_websearch: bool = False) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._load(tools_dir, enable_websearch)
        logger.info("ToolRegistry loaded %d tools from %s (websearch=%s)",
                     len(self._tools), tools_dir, enable_websearch)

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
                # Skip websearch tools when feature is disabled
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
        """Return 'skill-files' or 'skill-runner' for a named tool."""
        return self._tools[tool_name]["backend"]

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return tool definitions in OpenAI function-calling format."""
        defs = []
        for tool in self._tools.values():
            params = tool.get("parameters", {"type": "object", "properties": {}})
            # Normalise: YAML may store required as a string "[...]" — coerce to list
            if isinstance(params.get("required"), str):
                import json
                try:
                    params["required"] = json.loads(params["required"])
                except Exception:
                    params.pop("required", None)
            defs.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": params,
                },
            })
        return defs
