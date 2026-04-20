"""
Prompt Builder — assembles system prompt from modular text files.
"""

import logging
from pathlib import Path

logger = logging.getLogger("core.prompt")

_SECTION_SEPARATOR = "\n\n"


class PromptBuilder:
    """Assembles system prompt from modular files + dynamic context."""

    def __init__(
        self,
        modules_dir: str | Path | None = None,
        legacy_path: str | Path | None = None,
    ):
        self._modules_dir = Path(modules_dir) if modules_dir else None
        self._legacy_path = Path(legacy_path) if legacy_path else None
        self._modules: dict[str, str] = {}
        self._load_modules()

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
    ) -> str:
        if not self._modules:
            return self._load_legacy()

        parts: list[str] = []
        for key in sorted(self._modules):
            if exclude and key in exclude:
                continue
            parts.append(self._modules[key])

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
