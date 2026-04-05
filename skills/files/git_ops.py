import logging
from pathlib import Path
from typing import Any

from git import GitCommandNotFound, InvalidGitRepositoryError, Repo

logger = logging.getLogger("skill-files.git")


class GitOps:
    def __init__(self, workspace_root: str):
        self._root = Path(workspace_root).resolve()
        self._repo: Repo | None = None
        self._try_open()

    def _try_open(self) -> None:
        try:
            self._repo = Repo(str(self._root))
        except (InvalidGitRepositoryError, GitCommandNotFound) as exc:
            logger.warning("Git unavailable for workspace %s: %s", self._root, exc)
            self._repo = None

    def status(self) -> dict[str, Any]:
        if self._repo is None:
            return {"initialized": False}
        repo = self._repo
        try:
            branch = repo.active_branch.name
        except TypeError:
            branch = "HEAD (detached)"

        try:
            staged = [item.a_path for item in repo.index.diff("HEAD")]
        except Exception:
            staged = []

        return {
            "initialized": True,
            "branch": branch,
            "dirty": repo.is_dirty(untracked_files=True),
            "untracked": repo.untracked_files,
            "modified": [item.a_path for item in repo.index.diff(None)],
            "staged": staged,
        }

    def auto_commit(self, message: str) -> dict[str, Any]:
        if self._repo is None:
            return {"committed": False, "reason": "no git repository"}
        repo = self._repo
        try:
            repo.git.add(A=True)
            # On repos with existing commits check the index; on a brand-new repo
            # (no HEAD yet) check that there are staged entries.
            if repo.head.is_valid():
                if not repo.is_dirty(index=True):
                    return {"committed": False, "reason": "nothing to commit"}
            elif not repo.index.entries:
                return {"committed": False, "reason": "nothing to commit"}

            commit = repo.index.commit(message)
            return {"committed": True, "sha": commit.hexsha, "message": message}
        except Exception as exc:
            logger.error("git auto-commit failed: %s", exc)
            return {"committed": False, "reason": str(exc)}

    def commit(self, message: str) -> dict[str, Any]:
        return self.auto_commit(message)
