"""Shared input helpers for path detection and workspace file imports."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ImportedFile:
    """Represents a local file imported into the workspace."""

    source_path: str
    local_path: str
    workspace_path: str
    display_name: str


_PATH_RE = re.compile(
    r'(?:[A-Za-z]:[\\/](?:[^\\/:*?"<>|\r\n]+[\\/])*[^\\/:*?"<>|\r\n]+\.\w{1,6}|/[^\s<>"\']+\.\w{1,6})'
)
_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)


def detect_file_paths(text: str) -> list[str]:
    """Extract absolute file paths from free-form text."""
    # A URL's //host/path used to match a POSIX absolute path. On Windows
    # pathlib then probes it as an SMB share, before any chat/model call.
    # Exclude overlapping URL spans, preserving real paths elsewhere.
    urls = [(match.start(), match.end()) for match in _URL_RE.finditer(text)]
    return [match.group() for match in _PATH_RE.finditer(text)
            if not any(match.start() < end and start < match.end()
                       for start, end in urls)]


def detect_urls(text: str) -> list[str]:
    """Extract URLs from free-form text."""
    return _URL_RE.findall(text)


def ingest_local_file_paths(
    text: str,
    workspace_root: str | Path,
) -> tuple[str, list[ImportedFile]]:
    """Normalize absolute file paths to /workspace paths and import external files.

    Files already inside the workspace are only rewritten to their /workspace form.
    External files are copied into workspace/data and reported back as imports.
    """
    workspace_root = Path(workspace_root)
    data_dir = workspace_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    imported: list[ImportedFile] = []
    rewritten_text = text

    for raw_path in detect_file_paths(text):
        source = Path(raw_path)
        try:
            accessible_file = source.is_absolute() and source.is_file()
        except OSError:
            # Keep the user's text; an unavailable file is not an import.
            accessible_file = False
        if not accessible_file:
            continue

        try:
            source_resolved = source.resolve()
            workspace_resolved = workspace_root.resolve()
            if workspace_resolved == source_resolved or workspace_resolved in source_resolved.parents:
                relative = source_resolved.relative_to(workspace_resolved).as_posix()
                rewritten_text = rewritten_text.replace(raw_path, f"/workspace/{relative}")
                continue
        except Exception:
            pass

        target = data_dir / source.name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            index = 1
            while True:
                candidate = data_dir / f"{stem}_{index}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                index += 1

        shutil.copy2(source, target)
        workspace_path = f"/workspace/data/{target.name}"
        imported.append(
            ImportedFile(
                source_path=raw_path,
                local_path=str(target),
                workspace_path=workspace_path,
                display_name=target.name,
            )
        )
        rewritten_text = rewritten_text.replace(raw_path, workspace_path)

    return rewritten_text, imported
