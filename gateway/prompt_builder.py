"""
Prompt Builder — assembles system prompt from modular text files.

Modules are stored in ``prompts/modules/`` as numbered ``.txt`` files
(e.g. ``01_base.txt``, ``02_analysis.txt``).  They are concatenated in
filename order to form the complete system prompt.

Falls back to the monolithic ``prompts/system.txt`` when no modules exist.

Supports excluding modules by key and appending dynamic runtime sections
(workspace context, memory, etc.).
"""

import logging
from pathlib import Path

logger = logging.getLogger("gateway.prompt")

_MODULES_DIR = "/app/prompts/modules"
_LEGACY_PROMPT = "/app/prompts/system.txt"
_SECTION_SEPARATOR = "\n\n"


class PromptBuilder:
    """Assembles system prompt from modular files + dynamic context."""

    def __init__(
        self,
        modules_dir: str = _MODULES_DIR,
        legacy_path: str = _LEGACY_PROMPT,
    ):
        self._modules_dir = Path(modules_dir)
        self._legacy_path = Path(legacy_path)
        self._modules: dict[str, str] = {}
        self._load_modules()

    def _load_modules(self) -> None:
        """Load all .txt modules from the modules directory."""
        if not self._modules_dir.is_dir():
            logger.info(
                "No modules dir %s — will use legacy prompt", self._modules_dir
            )
            return

        for txt_file in sorted(self._modules_dir.glob("*.txt")):
            try:
                content = txt_file.read_text(encoding="utf-8").strip()
                if content:
                    key = txt_file.stem  # e.g. "01_base"
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
        """Return sorted list of available module keys."""
        return sorted(self._modules.keys())

    def build(
        self,
        *,
        exclude: set[str] | None = None,
        extra_sections: list[str] | None = None,
    ) -> str:
        """Assemble the full system prompt.

        Args:
            exclude: Module keys to skip (e.g. ``{"03_files"}``).
            extra_sections: Additional text sections appended after all
                modules (e.g. workspace context, memory index).
        """
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
        """Fallback: read the monolithic system.txt."""
        try:
            return self._legacy_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return (
                "You are a helpful local AI assistant "
                "with access to a sandboxed file workspace."
            )

    def get_module(self, key: str) -> str | None:
        """Return a single module's content, or ``None``."""
        return self._modules.get(key)
