"""
Policy engine — path whitelist/blacklist, execution restrictions.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("core.policy")

_PATH_KEYS = frozenset({"path", "src", "dst", "directory"})


class PolicyEngine:
    def __init__(self, policy_path: str | Path):
        with open(policy_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._allowed = [p.rstrip("/") for p in cfg["paths"]["allowed_prefixes"]]
        self._denied = [p.rstrip("/") for p in cfg["paths"]["denied_prefixes"]]
        self._write_only = [p.rstrip("/") for p in cfg["paths"].get("write_only_prefixes", [])]
        self._allow_delete = cfg["operations"].get("allow_delete", True)
        self._allow_shell_exec = cfg["operations"].get("allow_shell_exec", False)
        self._max_size = cfg["files"].get("max_size_bytes", 10 * 1024 * 1024)
        self._denied_ext = frozenset(
            e.lower() for e in cfg["files"].get("denied_extensions", [])
        )
        execution = cfg.get("execution", {})
        self._max_code_chars = execution.get("max_code_chars", 40_000)
        self._max_command_chars = execution.get("max_command_chars", 2_000)
        self._max_timeout_seconds = execution.get("max_timeout_seconds", 30)
        self._denied_code_fragments = tuple(
            fragment.lower() for fragment in execution.get("denied_code_fragments", [])
        )
        self._denied_shell_fragments = tuple(
            fragment.lower() for fragment in execution.get("denied_shell_fragments", [])
        )

    def check(self, tool: str, params: dict[str, Any]) -> None:
        if tool == "file_delete" and not self._allow_delete:
            raise PermissionError("Delete operations are disabled by policy")
        if tool == "shell_exec" and not self._allow_shell_exec:
            raise PermissionError("Shell execution is disabled by policy")

        if tool in {"file_delete", "file_rename"}:
            protected_key = "path" if tool == "file_delete" else "src"
            target = params.get(protected_key, "")
            if isinstance(target, str):
                absolute = os.path.normpath("/" + target.lstrip("/"))
                for wo in self._write_only:
                    w = wo.rstrip("/")
                    if absolute == w or absolute.startswith(w + "/"):
                        raise PermissionError(
                            f"Path {target!r} is in a write-only directory"
                            f" ({wo!r}): delete and rename-away are not permitted"
                        )

        for key, value in params.items():
            if key in _PATH_KEYS and isinstance(value, str):
                self._check_path(value)

        if "content" in params and isinstance(params["content"], str):
            size = len(params["content"].encode())
            if size > self._max_size:
                raise PermissionError(
                    f"Content size {size} bytes exceeds policy maximum {self._max_size} bytes"
                )

        if "path" in params and isinstance(params["path"], str):
            ext = os.path.splitext(params["path"])[1].lower()
            if ext in self._denied_ext:
                raise PermissionError(f"File extension {ext!r} is not permitted by policy")

        if "timeout" in params:
            timeout = params["timeout"]
            if not isinstance(timeout, int) or timeout < 1 or timeout > self._max_timeout_seconds:
                raise PermissionError(
                    f"Execution timeout must be between 1 and {self._max_timeout_seconds} seconds"
                )

        if tool == "code_exec":
            code = params.get("code")
            if not isinstance(code, str) or not code.strip():
                raise PermissionError("code must be a non-empty string")
            if len(code) > self._max_code_chars:
                raise PermissionError(
                    f"Code length exceeds policy maximum {self._max_code_chars} characters"
                )
            lowered = code.lower()
            for fragment in self._denied_code_fragments:
                if fragment in lowered:
                    raise PermissionError(
                        f"code contains forbidden fragment {fragment!r}"
                    )

        if tool == "shell_exec":
            command = params.get("command")
            if not isinstance(command, str) or not command.strip():
                raise PermissionError("command must be a non-empty string")
            if len(command) > self._max_command_chars:
                raise PermissionError(
                    f"Command length exceeds policy maximum {self._max_command_chars} characters"
                )
            lowered = command.lower()
            for fragment in self._denied_shell_fragments:
                if fragment in lowered:
                    raise PermissionError(
                        f"command contains forbidden fragment {fragment!r}"
                    )

    def _check_path(self, path: str) -> None:
        if not isinstance(path, str) or not path.strip():
            raise PermissionError("Path must be a non-empty string")

        absolute = os.path.normpath("/" + path.lstrip("/"))

        for denied in self._denied:
            d = denied.rstrip("/")
            if absolute == d or absolute.startswith(d + "/"):
                raise PermissionError(f"Access denied: {path!r} matches a denied prefix")

        for allowed in self._allowed:
            a = allowed.rstrip("/")
            if absolute == a or absolute.startswith(a + "/"):
                return

        raise PermissionError(f"Path {path!r} is outside all allowed prefixes")
