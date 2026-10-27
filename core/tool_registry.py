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
from harness.capabilities.tool_sources import tool_sources

from . import config

logger = logging.getLogger("core.tool_registry")

# Fail-safe: a tool must declare `cloud: read` to be reachable from a cloud model.
_DEFAULT_CLOUD_TIER = "write"

class ToolRegistry:
    """Loads core tools; lifecycle owners activate package contributions."""

    def __init__(self, tools_dir: str | Path, *, enabled_backends=()) -> None:
        enabled_backends = tuple(enabled_backends)
        self._tools: dict[str, dict[str, Any]] = {}
        self._owned = OwnedTools()
        self._load(str(tools_dir))
        for backend in enabled_backends:
            self.load_backend(tools_dir, backend)
        from harness.runtime.call_policies import CallPolicies
        packages = config._PROJECT_ROOT / 'plugins' if Path(tools_dir).resolve() == Path(config.TOOLS_DIR).resolve() else None
        self._call_policies = CallPolicies(packages)
        core_count = sum(1 for t in self._tools.values() if t.get("tier", "core") == "core")
        logger.info("ToolRegistry loaded %d tools (%d core, %d extended) from %s (enabled=%s)",
                     len(self._tools), core_count, len(self._tools) - core_count,
                     tools_dir, enabled_backends)

    def _load(self, tools_dir: str) -> None:
        tools_path = Path(tools_dir)
        if not tools_path.is_dir():
            logger.warning("tools_dir %r does not exist — no tools loaded", tools_dir)
            return
        for yaml_file in sorted(tools_path.glob('*.yaml')):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    tool = yaml.safe_load(f)
                name = tool.get("name")
                backend = tool.get("backend")
                if not name or not backend:
                    logger.warning("Skipping %s: missing 'name' or 'backend'", yaml_file.name)
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
        for path in self._sources(Path(tools_dir)):
            tool = yaml.safe_load(path.read_text(encoding='utf-8'))
            if isinstance(tool, dict) and tool.get('backend') == backend:
                definitions.append(tool)
        self.replace_owner(backend, definitions)

    @staticmethod
    def _sources(directory):
        packages = config._PROJECT_ROOT / 'plugins' if directory.resolve() == Path(config.TOOLS_DIR).resolve() else None
        return tool_sources(directory, packages)

    def ownership(self) -> dict[str, str]:
        return self._owned.owners()

    def execution_policy(self, tool_name):
        """Policies follow actual registration ownership, never a tool-name prefix."""
        return self._call_policies.for_owner(self._owned.owners().get(tool_name))

    def tools_for_role(self, role):
        """Resolve declarations against currently registered tools owned by that package."""
        return {name for name, owner in self.ownership().items()
                if name in self._call_policies.roles.get(owner, {}).get(role, [])}

    def tool_group(self, name):
        owner = self._owned.owners().get(name)
        return (self._tools.get(name, {}).get('group') or self._call_policies.groups.get(owner)
                or ('文件' if owner == 'skill-files' else '执行与技能'))

    def tool_purposes(self):
        owners = self.ownership()
        return {name: self._call_policies.reply_purposes.get(owner, {})
                for name, owner in owners.items() if self._call_policies.reply_purposes.get(owner)}

    def capability_catalog(self):
        from harness.capabilities.catalog import package_catalog
        keys = ('id', 'title', 'description', 'version', 'required', 'optional', 'permissions', 'tools')
        return [{key: row.get(key, []) for key in keys}
                for row in package_catalog(config._PROJECT_ROOT / 'plugins', self.ownership())
                if row['registration'] == 'registered']

    def create_turn(self):
        return self._call_policies.create_turn(set(self.ownership().values()),
            getattr(self, 'turn_context', {}))

    def hidden_research_methods(self):
        active = set(self.ownership().values())
        visible = set(self._call_policies.research_methods(active))
        return {family for families in self._call_policies.method_families.values()
                for family in families if family not in visible}

    def create_research(self, router, **kwargs):
        return self._call_policies.create_research(router,
            active_owners=set(self.ownership().values()), **kwargs)

    def call_limits(self):
        owners = self.ownership()
        limits = {}
        for name, owner in owners.items():
            declared = self._call_policies.limits.get(owner, {}).get(name)
            if declared is None:
                continue
            value = getattr(config, declared) if isinstance(declared, str) else declared
            if type(value) is not int or value < 0:
                raise ValueError('能力调用限额配置无效：' + str(declared))
            limits[name] = value
        return limits

    def history_projectors(self, history):
        tools = self.tools_for_role('source_read')
        owners = {self.get_owner(row['tool_name']) for row in history
                  if row.get('role') == 'tool' and row.get('tool_name') in tools}
        return [provider for owner in sorted(owners)
                if (provider := self._call_policies.for_owner(owner)) is not None
                and callable(getattr(provider, 'project_history', None))]

    def phase_guidance(self, phase, tool_names):
        owners = {owner for name, owner in self.ownership().items() if name in tool_names}
        sections = []
        for owner in sorted(owners):
            provider = self._call_policies.for_owner(owner)
            guidance = getattr(provider, 'phase_guidance', None)
            if guidance and (text := guidance(phase)) and text not in sections:
                sections.append(text)
        return sections

    @property
    def known_tools(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get_backend(self, tool_name: str) -> str:
        return self._tools[tool_name]["backend"]

    def get_owner(self, tool_name: str) -> str:
        return self._owned.owners()[tool_name]

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
