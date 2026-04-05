"""
Sandboxed code / shell executor.

Security model:
- Each execution runs in a subprocess (process isolation).
- Working directory is /workspace (already volume-scoped).
- Environment is stripped to a minimal safe set.
- stdout + stderr are capped at MAX_OUTPUT_BYTES.
- Hard wall-clock timeout enforced via subprocess.run(timeout=).
- The container itself is the primary security boundary:
    - no host networking  (docker-compose: network_mode none)
    - non-root user       (Dockerfile: USER runner)
    - resource limits     (docker-compose: mem_limit / cpus)
"""

import os
import subprocess
import sys
import tempfile

MAX_OUTPUT_BYTES = 51_200   # 50 KB per stream
DEFAULT_PYTHON_TIMEOUT = 30
DEFAULT_SHELL_TIMEOUT = 15

_PACKAGES_DIR = "/packages"
_PIP_MIRROR = "https://mirrors.aliyun.com/pypi/simple/"

_SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "PYTHONPATH": f"{_PACKAGES_DIR}:/workspace",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


def run_python(code: str, timeout: int = DEFAULT_PYTHON_TIMEOUT) -> dict:
    """Execute arbitrary Python code in a subprocess and return results."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False, dir="/tmp"
        ) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/workspace",
            env=_SAFE_ENV,
        )

        stdout = result.stdout[:MAX_OUTPUT_BYTES]
        stderr = result.stderr[:MAX_OUTPUT_BYTES]
        truncated = (
            len(result.stdout) > MAX_OUTPUT_BYTES
            or len(result.stderr) > MAX_OUTPUT_BYTES
        )

        return {
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
        }

    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout}s",
            "truncated": False,
        }
    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Sandbox error: {exc}",
            "truncated": False,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def run_shell(command: str, timeout: int = DEFAULT_SHELL_TIMEOUT) -> dict:
    """Execute a shell command in /workspace and return results."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/workspace",
            env=_SAFE_ENV,
            executable="/bin/sh",
        )

        stdout = result.stdout[:MAX_OUTPUT_BYTES]
        stderr = result.stderr[:MAX_OUTPUT_BYTES]
        truncated = (
            len(result.stdout) > MAX_OUTPUT_BYTES
            or len(result.stderr) > MAX_OUTPUT_BYTES
        )

        return {
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
        }

    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "truncated": False,
        }
    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Shell error: {exc}",
            "truncated": False,
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
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_SAFE_ENV,
        )

        stdout = result.stdout[:MAX_OUTPUT_BYTES]
        stderr = result.stderr[:MAX_OUTPUT_BYTES]

        return {
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "installed": packages if result.returncode == 0 else [],
        }

    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"pip install timed out after {timeout}s",
            "installed": [],
        }
    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"pip install error: {exc}",
            "installed": [],
        }
