"""
Converter registry — dynamically loads file converters from /workspace/converters/.

Each converter is a Python file with:
  - CONVERTER_META dict: {"extensions": [".docx"], "dependencies": ["python-docx"], "description": "..."}
  - convert(file_path: str) -> str function

Converters are discovered at call time (hot-reload) so new converters
take effect immediately without restarting the container.
"""

import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("skill-runner.converters")

_CONVERTERS_DIR = Path("/workspace/converters")
_EXEC_TIMEOUT = int(os.environ.get("PYTHON_EXEC_TIMEOUT", "60"))

_SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "PYTHONPATH": "/packages:/workspace",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}

# Track which converters have had their deps installed this session
_deps_installed: set[str] = set()


def _scan_converters() -> dict[str, Path]:
    """Scan the converters directory and return {extension: converter_path} mapping."""
    mapping: dict[str, Path] = {}
    if not _CONVERTERS_DIR.exists():
        return mapping

    for py_file in _CONVERTERS_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_probe", py_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            meta = getattr(mod, "CONVERTER_META", None)
            if isinstance(meta, dict) and "extensions" in meta:
                for ext in meta["extensions"]:
                    ext_lower = ext.lower() if ext.startswith(".") else f".{ext}".lower()
                    mapping[ext_lower] = py_file
        except Exception as exc:
            logger.warning("Failed to load converter %s: %s", py_file.name, exc)

    return mapping


def _install_converter_deps(converter_path: Path) -> bool:
    """Install dependencies declared by a converter. Returns True if successful."""
    key = str(converter_path)
    if key in _deps_installed:
        return True

    try:
        spec = importlib.util.spec_from_file_location("_deps", converter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        meta = getattr(mod, "CONVERTER_META", {})
        deps = meta.get("dependencies", [])
    except Exception as exc:
        logger.warning("Cannot read deps from %s: %s", converter_path.name, exc)
        return False

    if not deps:
        _deps_installed.add(key)
        return True

    logger.info("Installing converter deps for %s: %s", converter_path.name, deps)
    try:
        from sandbox import run_pip_install
        result = run_pip_install(deps, timeout=120)
        if result.get("exit_code", 1) == 0:
            _deps_installed.add(key)
            return True
        logger.warning("Dep install failed for %s: %s", converter_path.name, result.get("stderr", ""))
        return False
    except Exception as exc:
        logger.warning("Dep install error for %s: %s", converter_path.name, exc)
        return False


_CONVERT_WRAPPER = """\
import json, sys, importlib.util

converter_path = sys.argv[1]
file_path = sys.argv[2]

spec = importlib.util.spec_from_file_location("converter", converter_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

result = mod.convert(file_path)
print(json.dumps({"content": result}, ensure_ascii=False))
"""


def convert_file(file_path: str) -> dict:
    """Try to convert a file using a matching converter plugin.

    Returns:
      {"content": "extracted text"}  on success
      {"unsupported": True, ...}     if no converter found
      {"error": "..."}               on converter execution failure
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    ext = path.suffix.lower()
    converters = _scan_converters()

    if ext not in converters:
        return {
            "unsupported": True,
            "extension": ext,
            "path": file_path,
            "size": path.stat().st_size,
            "available_converters": sorted(converters.keys()),
            "hint": f"无匹配转换器。可创建 /workspace/converters/xxx_converter.py 来支持 {ext} 格式",
        }

    converter_path = converters[ext]
    logger.info("Using converter %s for %s", converter_path.name, ext)

    # Install dependencies if needed
    if not _install_converter_deps(converter_path):
        return {"error": f"转换器 {converter_path.name} 的依赖安装失败"}

    # Execute converter in isolated subprocess
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False, dir="/tmp"
        ) as f:
            f.write(_CONVERT_WRAPPER)
            tmp_path = f.name

        result = subprocess.run(
            [sys.executable, tmp_path, str(converter_path), file_path],
            capture_output=True,
            text=True,
            timeout=_EXEC_TIMEOUT,
            cwd="/workspace",
            env=_SAFE_ENV,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()[:500]
            logger.warning("Converter %s failed: %s", converter_path.name, error_msg)
            return {"error": f"转换器执行失败: {error_msg}"}

        stdout = result.stdout.strip()
        if not stdout:
            return {"error": "转换器未返回任何内容"}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # If converter printed plain text instead of JSON, wrap it
            return {"content": stdout}

    except subprocess.TimeoutExpired:
        return {"error": f"转换器执行超时 ({_EXEC_TIMEOUT}s)"}
    except Exception as exc:
        return {"error": f"转换器执行异常: {exc}"}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def list_converters() -> list[dict]:
    """List all available converters and their supported extensions."""
    result = []
    if not _CONVERTERS_DIR.exists():
        return result

    for py_file in sorted(_CONVERTERS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_probe", py_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            meta = getattr(mod, "CONVERTER_META", None)
            if isinstance(meta, dict):
                result.append({
                    "name": py_file.stem,
                    "extensions": meta.get("extensions", []),
                    "dependencies": meta.get("dependencies", []),
                    "description": meta.get("description", ""),
                })
        except Exception as exc:
            result.append({
                "name": py_file.stem,
                "error": str(exc),
            })

    return result
