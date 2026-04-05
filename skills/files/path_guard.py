import os
from pathlib import Path


class PathGuard:
    """Confines all file-system access to a single workspace root directory.

    Uses os.path.realpath to follow symlinks before comparing, which prevents
    symlink-based directory traversal attacks. Works correctly for paths that
    do not yet exist (e.g. write targets) by normalising without following
    non-existent components.
    """

    def __init__(self, workspace_root: str):
        # Resolve symlinks for the root itself so comparisons are stable.
        self._root = os.path.realpath(workspace_root)

    def resolve(self, path: str) -> Path:
        """Resolve a caller-supplied path to an absolute Path inside the workspace.

        Raises PermissionError if the resolved path escapes the workspace root.
        """
        if not isinstance(path, str) or not path.strip():
            raise PermissionError("Path must be a non-empty string")

        # Build the candidate:
        #   – absolute input  → use as-is
        #   – relative input  → treat as relative to workspace root
        raw = path if os.path.isabs(path) else os.path.join(self._root, path)

        # realpath normalises .. / . and follows symlinks for existing components.
        candidate = os.path.realpath(raw)

        # commonpath uses path components, not string prefixes, so
        # /workspace and /workspace2/foo correctly yield "/" as their common path.
        try:
            common = os.path.commonpath([self._root, candidate])
        except ValueError:
            # Different drives on Windows.
            raise PermissionError(f"Access denied: {path!r} escapes the workspace root")

        if common != self._root:
            raise PermissionError(f"Access denied: {path!r} escapes the workspace root")

        return Path(candidate)
