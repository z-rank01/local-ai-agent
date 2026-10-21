"""
Prompt Builder — assembles system prompt from modular text files.
"""

import logging
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

logger = logging.getLogger("core.prompt")

_SECTION_SEPARATOR = "\n\n"


class PromptBuilder:
    """Assembles system prompt from modular files + dynamic context."""

    def __init__(
        self,
        modules_dir: str | Path | None = None,
        legacy_path: str | Path | None = None,
        packages_dir: str | Path | None = None,
        registered_tools: Callable[[], frozenset[str]] | None = None,
    ):
        self._modules_dir = Path(modules_dir) if modules_dir else None
        self._legacy_path = Path(legacy_path) if legacy_path else None
        self._modules: dict[str, str] = {}
        self._load_modules()
        self._registered_tools = registered_tools or (lambda: frozenset())
        from harness.capabilities.prompts import load_prompts
        self._contributions, self.contribution_errors = load_prompts(Path(packages_dir)) if packages_dir else ([], {})
        for identity, error in self.contribution_errors.items():
            logger.warning('Plugin prompt %s unavailable: %s', identity, error)

    def _load_modules(self) -> None:
        if not self._modules_dir or not self._modules_dir.is_dir():
            logger.info(
                "No modules dir %s — will use legacy prompt", self._modules_dir
            )
            return

        for txt_file in sorted(self._modules_dir.glob("*.txt")):
            try:
                content = txt_file.read_text(encoding="utf-8").strip()
                if content:
                    key = txt_file.stem
                    self._modules[key] = content
                    logger.debug(
                        "Loaded prompt module: %s (%d chars)", key, len(content)
                    )
            except Exception as exc:
                logger.warning("Failed to load prompt module %s: %s", txt_file, exc)

        logger.info(
            "PromptBuilder: loaded %d modules from %s",
            len(self._modules),
            self._modules_dir,
        )

    @property
    def module_names(self) -> list[str]:
        return sorted(self._modules.keys())

    def build(
        self,
        *,
        exclude: set[str] | None = None,
        extra_sections: list[str] | None = None,
        available_tools: frozenset[str] | None = None,
    ) -> str:
        runtime_context = (
            '本轮当前时间：' + datetime.now().astimezone().isoformat(timespec='seconds') + '。\n'
            '用户问“最近”或“今年”时以此定位；资料披露日、报告期和价格日仍须核对来源，'
            '当前时间不代表资料已更新。'
        )
        parts: list[str] = [runtime_context]
        if not self._modules:
            parts.append(self._load_legacy())
        for key in sorted(self._modules):
            if exclude and key in exclude:
                continue
            parts.append(self._modules[key])

        active_tools = self._registered_tools() if available_tools is None else available_tools
        parts.extend(item.text for item in self._contributions if item.tools & active_tools)

        if extra_sections:
            parts.extend(s for s in extra_sections if s)

        return _SECTION_SEPARATOR.join(parts)

    def _load_legacy(self) -> str:
        if self._legacy_path:
            try:
                return self._legacy_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                pass
        return (
            "You are a helpful local AI assistant "
            "with access to a sandboxed file workspace."
        )

    def get_module(self, key: str) -> str | None:
        return self._modules.get(key)
