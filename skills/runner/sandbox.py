"""
Sandboxed code / shell executor.

Security model:
- Each execution runs in its own process group (start_new_session), so a
  timeout or stop kills the whole tree, not just the direct child.
- Working directory is confined to /workspace (already volume-scoped).
- Environment is stripped to a minimal safe set.
- stdout + stderr are capped at MAX_OUTPUT_BYTES.
- Hard wall-clock timeout enforced via killpg on expiry.
- The container itself is the primary security boundary:
    - non-root user       (Dockerfile: USER runner)
    - read-only root fs   (compose: read_only + tmpfs /tmp)
    - resource limits     (compose: cpus / mem_limit / pids_limit)
    - no host project, credential or docker-socket mounts
- Outbound network IS available (pip installs need it): this module is
  not a network sandbox, and string-level rules are only supplementary.
"""

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_OUTPUT_BYTES = 51_200   # 50 KB per stream
DEFAULT_PYTHON_TIMEOUT = 30
DEFAULT_SHELL_TIMEOUT = 15

_PACKAGES_DIR = "/packages"
_PIP_MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
_WORKSPACE_ROOT = "/workspace"

_SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "PYTHONPATH": f"{_PACKAGES_DIR}:/workspace",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


def validate_workspace_cwd(cwd: str | None) -> str:
    """Confine the working directory to a real directory inside /workspace."""
    root = Path(_WORKSPACE_ROOT).resolve()
    workdir = Path(cwd or _WORKSPACE_ROOT).resolve()
    if not workdir.is_relative_to(root) or not workdir.is_dir():
        raise ValueError("工作目录必须是 /workspace 内的实际目录")
    return str(workdir)


def _collect_with_timeout(proc: subprocess.Popen, timeout: int, timeout_message: str) -> dict:
    """Wait for the process group with a hard deadline, keeping partial output."""
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        stdout, stderr = proc.communicate()
    stdout = stdout or ""
    stderr = stderr or ""
    truncated = len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES
    if timed_out:
        stderr = f"{stderr}\n{timeout_message}" if stderr else timeout_message
    return {
        "exit_code": -1 if timed_out else proc.returncode,
        "stdout": stdout[:MAX_OUTPUT_BYTES],
        "stderr": stderr[:MAX_OUTPUT_BYTES],
        "truncated": truncated,
        "timed_out": timed_out,
    }


def _spawn_collect(args, *, timeout: int, cwd: str | None, timeout_message: str, shell: bool = False) -> dict:
    proc = subprocess.Popen(
        args,
        shell=shell,
        executable="/bin/sh" if shell else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=_SAFE_ENV,
        start_new_session=True,
    )
    return _collect_with_timeout(proc, timeout, timeout_message)


def run_argv(argv: list[str], timeout: int, cwd: str = _WORKSPACE_ROOT) -> dict:
    """Run an argv command in a confined process group and collect bounded output."""
    workdir = validate_workspace_cwd(cwd)
    return _spawn_collect(
        argv,
        timeout=timeout,
        cwd=workdir,
        timeout_message=f"Execution timed out after {timeout}s; process group terminated",
    )


def run_python(code: str, timeout: int = DEFAULT_PYTHON_TIMEOUT) -> dict:
    """Execute arbitrary Python code in a subprocess and return results."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False, dir="/tmp"
        ) as f:
            f.write(code)
            tmp_path = f.name

        return run_argv([sys.executable, tmp_path], timeout=timeout)

    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Sandbox error: {exc}",
            "truncated": False,
            "timed_out": False,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def run_shell(command: str, timeout: int = DEFAULT_SHELL_TIMEOUT, cwd: str = _WORKSPACE_ROOT) -> dict:
    """Execute a shell command inside /workspace and return results."""
    workdir = validate_workspace_cwd(cwd)
    try:
        return _spawn_collect(
            command,
            timeout=timeout,
            cwd=workdir,
            timeout_message=f"Command timed out after {timeout}s; process group terminated",
            shell=True,
        )
    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Shell error: {exc}",
            "truncated": False,
            "timed_out": False,
        }


def run_pip_install(packages: list[str], timeout: int = 120) -> dict:
    """Install Python packages to /packages using pip."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", _PACKAGES_DIR,
        "--no-cache-dir",
        "--quiet",
        "-i", _PIP_MIRROR,
        "--trusted-host", "mirrors.aliyun.com",
    ] + packages

    try:
        result = _spawn_collect(
            cmd,
            timeout=timeout,
            cwd=None,
            timeout_message=f"pip install timed out after {timeout}s; process group terminated",
        )
        result["installed"] = packages if result["exit_code"] == 0 else []
        return result
    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"pip install error: {exc}",
            "truncated": False,
            "timed_out": False,
            "installed": [],
        }
