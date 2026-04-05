"""
Dynamic skill registry with CRUD operations and dependency management.

Scans /workspace/skills/ for Python files that define SKILL_METADATA + run().
Skills are executed safely via subprocess (same isolation as code_exec).
Registration info is persisted to /workspace/.skill_registry/ for fast lookup.
"""

import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("skill-runner.registry")

_SKILLS_DIR = Path("/workspace/skills")
_REGISTRY_DIR = Path("/workspace/.skill_registry")
_REGISTRY_SKILLS_DIR = _REGISTRY_DIR / "skills"
_REGISTRY_INDEX = _REGISTRY_DIR / "registry.json"
_EXEC_TIMEOUT = int(os.environ.get("PYTHON_EXEC_TIMEOUT", "30"))

_SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "PYTHONPATH": "/packages:/workspace",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}

_WRAPPER_TEMPLATE = """\
import json, sys, importlib.util

spec = importlib.util.spec_from_file_location("skill", {skill_path!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
params = json.loads(sys.argv[1])
result = mod.run(params)
print(json.dumps(result, ensure_ascii=False, default=str))
"""

_SKILL_TEMPLATE = '''\
"""
技能名: {name}
描述: {description}
"""

SKILL_METADATA = {{
    "name": "{name}",
    "description": "{description}",
    "version": "1.0.0",
    "dependencies": [],
    "parameters": {{
        "type": "object",
        "properties": {{
            "input": {{"type": "string", "description": "输入参数"}}
        }},
        "required": ["input"]
    }}
}}


def run(params: dict) -> dict:
    """技能入口函数。"""
    return {{"success": True, "result": params.get("input", "")}}
'''


# ── Registry persistence helpers ─────────────────────────────────────────────

def _ensure_registry_dirs():
    """Create registry directories if they don't exist."""
    _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> dict:
    """Load the registry index file."""
    if _REGISTRY_INDEX.exists():
        try:
            return json.loads(_REGISTRY_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Registry index corrupted, rebuilding")
    return {"version": "1.0", "skills": {}}


def _save_index(index: dict):
    """Save the registry index file."""
    _ensure_registry_dirs()
    _REGISTRY_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_skill_config(skill_name: str) -> dict | None:
    """Load a single skill's config file."""
    config_path = _REGISTRY_SKILLS_DIR / f"{skill_name}.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_skill_config(skill_name: str, config: dict):
    """Save a single skill's config file."""
    _ensure_registry_dirs()
    config_path = _REGISTRY_SKILLS_DIR / f"{skill_name}.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _install_dependencies(deps: list[str], timeout: int = 120) -> dict:
    """Install pip dependencies to /packages."""
    if not deps:
        return {"installed": [], "status": "no_deps"}
    try:
        from sandbox import run_pip_install
        result = run_pip_install(deps, timeout=timeout)
        if result.get("exit_code", 1) == 0:
            return {"installed": deps, "status": "success"}
        return {"installed": [], "status": "failed", "error": result.get("stderr", "")}
    except Exception as exc:
        return {"installed": [], "status": "failed", "error": str(exc)}


def _validate_skill_file(skill_path: Path) -> tuple[dict | None, str]:
    """Validate that a skill file has proper format.

    Returns (metadata, error_message). If valid, error_message is empty.
    """
    if not skill_path.exists():
        return None, f"技能文件不存在: {skill_path}"

    meta = _load_metadata(skill_path)
    if meta is None:
        return None, "技能文件缺少有效的 SKILL_METADATA 字典（必须包含 'description' 字段）"

    # Check run() function exists
    try:
        spec = importlib.util.spec_from_file_location("_validate", skill_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not callable(getattr(mod, "run", None)):
            return None, "技能文件缺少 run(params) 函数"
    except Exception as exc:
        return None, f"技能文件加载失败: {exc}"

    return meta, ""


# ── Init: sync registry with existing skill files ────────────────────────────

def init_registry():
    """Called at startup to ensure registry is consistent with skill files."""
    _ensure_registry_dirs()
    index = _load_index()

    # Scan for skill files not yet in registry
    if _SKILLS_DIR.exists():
        for skill_file in sorted(_SKILLS_DIR.glob("*.py")):
            if skill_file.name.startswith("_"):
                continue
            name = skill_file.stem
            if name not in index.get("skills", {}):
                meta = _load_metadata(skill_file)
                if meta:
                    now = _now_iso()
                    config = {
                        "name": name,
                        "description": meta.get("description", ""),
                        "version": meta.get("version", "1.0.0"),
                        "author": meta.get("author", "unknown"),
                        "dependencies": meta.get("dependencies", []),
                        "dependencies_installed": False,
                        "parameters": meta.get("parameters", {"type": "object", "properties": {}}),
                        "created_at": now,
                        "updated_at": now,
                        "last_run_at": None,
                        "run_count": 0,
                        "status": "active",
                    }
                    _save_skill_config(name, config)
                    index.setdefault("skills", {})[name] = {
                        "name": name,
                        "description": meta.get("description", ""),
                        "script_path": str(skill_file),
                        "config_path": str(_REGISTRY_SKILLS_DIR / f"{name}.json"),
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                    logger.info("Auto-registered existing skill: %s", name)

    # Remove stale entries for deleted skill files
    stale = [n for n in index.get("skills", {}) if not (_SKILLS_DIR / f"{n}.py").exists()]
    for name in stale:
        index["skills"].pop(name, None)
        cfg_path = _REGISTRY_SKILLS_DIR / f"{name}.json"
        if cfg_path.exists():
            cfg_path.unlink()
        logger.info("Removed stale registry entry: %s", name)

    _save_index(index)
    logger.info("Registry initialized: %d skills", len(index.get("skills", {})))


# ── Public API: list / run (existing) ────────────────────────────────────────

def list_skills() -> list[dict]:
    """Return metadata for all valid skills in the skills directory."""
    if not _SKILLS_DIR.exists():
        return []

    skills = []
    for skill_file in sorted(_SKILLS_DIR.glob("*.py")):
        if skill_file.name.startswith("_"):
            continue
        meta = _load_metadata(skill_file)
        if meta:
            config = _load_skill_config(skill_file.stem)
            entry = {
                "name": skill_file.stem,
                "description": meta.get("description", ""),
                "parameters": meta.get("parameters", {"type": "object", "properties": {}}),
            }
            if config:
                entry["version"] = config.get("version", "1.0.0")
                entry["dependencies"] = config.get("dependencies", [])
                entry["run_count"] = config.get("run_count", 0)
                entry["status"] = config.get("status", "active")
            skills.append(entry)
    return skills


def run_skill(skill_name: str, params: dict, timeout: int = _EXEC_TIMEOUT) -> dict:
    """Execute a named skill file in an isolated subprocess."""
    skill_name = skill_name.replace("/", "").replace("..", "")
    skill_path = _SKILLS_DIR / f"{skill_name}.py"

    if not skill_path.exists():
        return {"error": f"Skill {skill_name!r} not found in /workspace/skills/"}

    # Auto-install dependencies if needed
    config = _load_skill_config(skill_name)
    if config and not config.get("dependencies_installed", False):
        deps = config.get("dependencies", [])
        if deps:
            logger.info("Auto-installing deps for %s: %s", skill_name, deps)
            install_result = _install_dependencies(deps)
            if install_result["status"] == "success":
                config["dependencies_installed"] = True
                _save_skill_config(skill_name, config)
            else:
                logger.warning("Dep install failed for %s: %s", skill_name, install_result)

    wrapper_code = _WRAPPER_TEMPLATE.format(skill_path=str(skill_path))
    params_json = json.dumps(params, ensure_ascii=False)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False, dir="/tmp"
        ) as f:
            f.write(wrapper_code)
            tmp_path = f.name

        result = subprocess.run(
            [sys.executable, tmp_path, params_json],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/workspace",
            env=_SAFE_ENV,
        )

        # Update run stats
        if config:
            config["last_run_at"] = _now_iso()
            config["run_count"] = config.get("run_count", 0) + 1
            _save_skill_config(skill_name, config)

        if result.returncode != 0:
            logger.warning("skill %s failed: %s", skill_name, result.stderr[:200])
            return {
                "exit_code": result.returncode,
                "error": result.stderr.strip(),
            }

        stdout = result.stdout.strip()
        if not stdout:
            return {"exit_code": 0, "result": None}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"exit_code": 0, "output": stdout}

    except subprocess.TimeoutExpired:
        return {"error": f"Skill {skill_name!r} timed out after {timeout}s"}
    except Exception as exc:
        return {"error": f"Skill execution error: {exc}"}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Public API: CRUD operations ──────────────────────────────────────────────

def register_skill(skill_name: str, code: str | None = None, auto_install_deps: bool = True) -> dict:
    """Register a new skill: validate, save config, install dependencies."""
    _ensure_registry_dirs()
    skill_name = skill_name.replace("/", "").replace("..", "").replace(" ", "_")
    skill_path = _SKILLS_DIR / f"{skill_name}.py"

    # Write code if provided
    if code:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(code, encoding="utf-8")

    # Validate skill file
    meta, error = _validate_skill_file(skill_path)
    if error:
        return {"success": False, "error": error}

    # Install dependencies
    deps = meta.get("dependencies", [])
    deps_installed = False
    install_info = {}
    if deps and auto_install_deps:
        install_info = _install_dependencies(deps)
        deps_installed = install_info.get("status") == "success"
    elif not deps:
        deps_installed = True

    # Create config
    now = _now_iso()
    config = {
        "name": skill_name,
        "description": meta.get("description", ""),
        "version": meta.get("version", "1.0.0"),
        "author": meta.get("author", "unknown"),
        "dependencies": deps,
        "dependencies_installed": deps_installed,
        "parameters": meta.get("parameters", {"type": "object", "properties": {}}),
        "created_at": now,
        "updated_at": now,
        "last_run_at": None,
        "run_count": 0,
        "status": "active",
    }
    _save_skill_config(skill_name, config)

    # Update index
    index = _load_index()
    index.setdefault("skills", {})[skill_name] = {
        "name": skill_name,
        "description": meta.get("description", ""),
        "script_path": str(skill_path),
        "config_path": str(_REGISTRY_SKILLS_DIR / f"{skill_name}.json"),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    _save_index(index)

    result = {
        "success": True,
        "skill_name": skill_name,
        "description": meta.get("description", ""),
        "parameters": meta.get("parameters", {}),
        "dependencies": deps,
        "dependencies_installed": deps_installed,
        "script_path": str(skill_path),
    }
    if install_info and install_info.get("status") == "failed":
        result["deps_warning"] = f"依赖安装失败: {install_info.get('error', 'unknown')}"
    return result


def unregister_skill(skill_name: str) -> dict:
    """Remove a skill: delete script and config files."""
    skill_name = skill_name.replace("/", "").replace("..", "")
    skill_path = _SKILLS_DIR / f"{skill_name}.py"
    config_path = _REGISTRY_SKILLS_DIR / f"{skill_name}.json"

    if not skill_path.exists() and not config_path.exists():
        return {"success": False, "error": f"技能 {skill_name!r} 不存在"}

    # Remove files
    removed = []
    if skill_path.exists():
        skill_path.unlink()
        removed.append(str(skill_path))
    if config_path.exists():
        config_path.unlink()
        removed.append(str(config_path))

    # Update index
    index = _load_index()
    index.get("skills", {}).pop(skill_name, None)
    _save_index(index)

    return {"success": True, "skill_name": skill_name, "removed_files": removed}


def skill_info(skill_name: str) -> dict:
    """Get detailed information about a registered skill."""
    skill_name = skill_name.replace("/", "").replace("..", "")
    skill_path = _SKILLS_DIR / f"{skill_name}.py"

    if not skill_path.exists():
        return {"error": f"技能 {skill_name!r} 不存在"}

    meta = _load_metadata(skill_path)
    config = _load_skill_config(skill_name)

    info = {
        "name": skill_name,
        "script_path": str(skill_path),
        "file_exists": True,
    }

    if meta:
        info["description"] = meta.get("description", "")
        info["version"] = meta.get("version", "1.0.0")
        info["author"] = meta.get("author", "unknown")
        info["dependencies"] = meta.get("dependencies", [])
        info["parameters"] = meta.get("parameters", {})
        info["format_valid"] = True
    else:
        info["format_valid"] = False
        info["error"] = "SKILL_METADATA 无效或缺失"

    if config:
        info["dependencies_installed"] = config.get("dependencies_installed", False)
        info["created_at"] = config.get("created_at")
        info["updated_at"] = config.get("updated_at")
        info["last_run_at"] = config.get("last_run_at")
        info["run_count"] = config.get("run_count", 0)
        info["status"] = config.get("status", "active")
        info["registered"] = True
    else:
        info["registered"] = False
        info["note"] = "技能文件存在但未注册，请使用 skill_register 注册"

    return info


def update_skill(skill_name: str, code: str | None = None, auto_install_deps: bool = True) -> dict:
    """Update an existing skill's code and/or re-validate."""
    skill_name = skill_name.replace("/", "").replace("..", "")
    skill_path = _SKILLS_DIR / f"{skill_name}.py"

    if not skill_path.exists() and not code:
        return {"success": False, "error": f"技能 {skill_name!r} 不存在且未提供代码"}

    # Write new code if provided
    if code:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(code, encoding="utf-8")

    # Re-validate
    meta, error = _validate_skill_file(skill_path)
    if error:
        return {"success": False, "error": error}

    # Load existing config or create new
    config = _load_skill_config(skill_name) or {}
    now = _now_iso()

    # Install deps if needed
    deps = meta.get("dependencies", [])
    old_deps = config.get("dependencies", [])
    deps_changed = set(deps) != set(old_deps)

    deps_installed = config.get("dependencies_installed", False)
    if deps_changed and deps and auto_install_deps:
        install_info = _install_dependencies(deps)
        deps_installed = install_info.get("status") == "success"
    elif not deps:
        deps_installed = True

    # Update config
    config.update({
        "name": skill_name,
        "description": meta.get("description", ""),
        "version": meta.get("version", config.get("version", "1.0.0")),
        "author": meta.get("author", config.get("author", "unknown")),
        "dependencies": deps,
        "dependencies_installed": deps_installed,
        "parameters": meta.get("parameters", {"type": "object", "properties": {}}),
        "updated_at": now,
        "status": "active",
    })
    if "created_at" not in config:
        config["created_at"] = now
    _save_skill_config(skill_name, config)

    # Update index
    index = _load_index()
    index.setdefault("skills", {})[skill_name] = {
        "name": skill_name,
        "description": meta.get("description", ""),
        "script_path": str(skill_path),
        "config_path": str(_REGISTRY_SKILLS_DIR / f"{skill_name}.json"),
        "status": "active",
        "created_at": config.get("created_at", now),
        "updated_at": now,
    }
    _save_index(index)

    return {
        "success": True,
        "skill_name": skill_name,
        "description": meta.get("description", ""),
        "dependencies": deps,
        "deps_changed": deps_changed,
        "dependencies_installed": deps_installed,
    }


# ── Internal helpers ─────────────────────────────────────────────────────────

def _load_metadata(skill_file: Path) -> dict | None:
    """Import a skill file in-process just to read its SKILL_METADATA constant."""
    try:
        spec = importlib.util.spec_from_file_location("_probe", skill_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        meta = getattr(mod, "SKILL_METADATA", None)
        if isinstance(meta, dict) and "description" in meta:
            return meta
    except Exception as exc:
        logger.warning("Could not load metadata from %s: %s", skill_file, exc)
    return None
