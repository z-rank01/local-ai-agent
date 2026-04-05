"""
Skill: Runner — sandboxed code and shell execution service.

Endpoints:
  POST /tool/code_exec    — run Python code snippet
  POST /tool/shell_exec   — run a shell command
  POST /tool/pip_install   — install Python packages dynamically
  POST /tool/skill_list   — list registered skills in /workspace/skills/
  POST /tool/skill_run    — run a registered skill by name
  GET  /skills            — same as skill_list (convenience)
  GET  /health
"""

import logging
import os
import re as _re

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sandbox import run_python, run_shell, run_pip_install
from skill_registry import (
    list_skills, run_skill, register_skill, unregister_skill,
    skill_info, update_skill, init_registry,
)
from converter_registry import convert_file, list_converters

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("skill-runner")

_PYTHON_TIMEOUT = int(os.environ.get("PYTHON_EXEC_TIMEOUT", "30"))
_SHELL_TIMEOUT = int(os.environ.get("SHELL_EXEC_TIMEOUT", "15"))

app = FastAPI(title="Skill: Runner")


@app.on_event("startup")
async def startup_event():
    """Initialize skill registry on startup."""
    init_registry()
    logger.info("Skill registry initialized")


# ── Request models ────────────────────────────────────────────────────────────

class CodeExecRequest(BaseModel):
    code: str = Field(..., description="Python source code to execute")
    timeout: int = Field(default=30, ge=1, le=120)


class ShellExecRequest(BaseModel):
    command: str = Field(..., description="Shell command to run in /workspace")
    timeout: int = Field(default=15, ge=1, le=60)


class PipInstallRequest(BaseModel):
    packages: list[str] = Field(..., description="List of pip package names to install")
    timeout: int = Field(default=120, ge=1, le=300)


class SkillRunRequest(BaseModel):
    skill_name: str = Field(..., description="Name of the skill (filename without .py)")
    params: dict = Field(default_factory=dict, description="Parameters to pass to the skill")
    timeout: int = Field(default=30, ge=1, le=120)


class SkillRegisterRequest(BaseModel):
    skill_name: str = Field(..., description="技能名（不含 .py 后缀）")
    code: str | None = Field(default=None, description="完整的技能 Python 代码")
    auto_install_deps: bool = Field(default=True, description="是否自动安装依赖")


class SkillUnregisterRequest(BaseModel):
    skill_name: str = Field(..., description="要删除的技能名")


class SkillInfoRequest(BaseModel):
    skill_name: str = Field(..., description="技能名")


class SkillUpdateRequest(BaseModel):
    skill_name: str = Field(..., description="技能名")
    code: str | None = Field(default=None, description="新的技能代码")
    auto_install_deps: bool = Field(default=True, description="是否重新安装依赖")


class FileConvertRequest(BaseModel):
    path: str = Field(..., description="文件绝对路径，以 /workspace 开头")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "workspace": "/workspace"}


@app.post("/tool/code_exec")
async def code_exec(req: CodeExecRequest):
    """Execute Python code in a sandboxed subprocess."""
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="code must not be empty")

    timeout = min(req.timeout, _PYTHON_TIMEOUT)
    logger.info("code_exec: %d chars, timeout=%ds", len(req.code), timeout)
    result = run_python(req.code, timeout=timeout)

    if result["exit_code"] != 0 and not result["stdout"] and result["stderr"]:
        logger.warning("code_exec failed: exit=%d stderr=%s", result["exit_code"], result["stderr"][:200])

    return result


@app.post("/tool/shell_exec")
async def shell_exec(req: ShellExecRequest):
    """Execute a shell command in /workspace."""
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="command must not be empty")

    timeout = min(req.timeout, _SHELL_TIMEOUT)
    logger.info("shell_exec: %r, timeout=%ds", req.command[:100], timeout)
    result = run_shell(req.command, timeout=timeout)

    if result["exit_code"] != 0 and not result["stdout"] and result["stderr"]:
        logger.warning("shell_exec failed: exit=%d stderr=%s", result["exit_code"], result["stderr"][:200])

    return result


_PKG_NAME_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._\-]*(\[[\w,]+\])?([<>=!~]+[\w.*]+)?$')

@app.post("/tool/pip_install")
async def pip_install(req: PipInstallRequest):
    """Install Python packages to /packages for use in code_exec."""
    if not req.packages:
        raise HTTPException(status_code=400, detail="packages list must not be empty")

    for pkg in req.packages:
        if not _PKG_NAME_RE.match(pkg):
            raise HTTPException(status_code=400, detail=f"Invalid package name: {pkg}")

    logger.info("pip_install: %s (timeout=%ds)", req.packages, req.timeout)
    result = run_pip_install(req.packages, timeout=req.timeout)

    if result["exit_code"] != 0:
        logger.warning("pip_install failed: exit=%d stderr=%s", result["exit_code"], result["stderr"][:200])
    else:
        logger.info("pip_install success: %s", req.packages)

    return result


@app.post("/tool/file_convert")
async def file_convert(req: FileConvertRequest):
    """Convert a non-text file to plain text using converter plugins."""
    if not req.path.strip():
        raise HTTPException(status_code=400, detail="path must not be empty")
    if not req.path.startswith("/workspace"):
        raise HTTPException(status_code=403, detail="path must start with /workspace")

    logger.info("file_convert: %s", req.path)
    result = convert_file(req.path)

    if "error" in result:
        logger.warning("file_convert %s error: %s", req.path, result["error"])

    return result


@app.get("/skills")
async def get_skills():
    """List all registered skills in /workspace/skills/."""
    return {"skills": list_skills()}


@app.post("/tool/skill_list")
async def skill_list():
    """List registered skills (tool-router compatible endpoint)."""
    return {"skills": list_skills()}


@app.post("/tool/skill_run")
async def skill_run_endpoint(req: SkillRunRequest):
    """Execute a registered skill by name."""
    if not req.skill_name.strip():
        raise HTTPException(status_code=400, detail="skill_name must not be empty")

    timeout = min(req.timeout, _PYTHON_TIMEOUT)
    logger.info("skill_run: name=%r params=%s", req.skill_name, list(req.params.keys()))
    result = run_skill(req.skill_name, req.params, timeout=timeout)

    if "error" in result:
        logger.warning("skill_run %r error: %s", req.skill_name, result["error"])

    return result


@app.post("/tool/skill_register")
async def skill_register_endpoint(req: SkillRegisterRequest):
    """注册新技能：验证格式、创建配置、安装依赖。"""
    if not req.skill_name.strip():
        raise HTTPException(status_code=400, detail="skill_name must not be empty")

    logger.info("skill_register: name=%r has_code=%s", req.skill_name, bool(req.code))
    try:
        result = register_skill(req.skill_name, code=req.code, auto_install_deps=req.auto_install_deps)
    except Exception as exc:
        logger.exception("skill_register %r unexpected error", req.skill_name)
        return {"success": False, "error": f"注册异常: {exc}"}

    if not result.get("success"):
        logger.warning("skill_register %r failed: %s", req.skill_name, result.get("error"))

    return result


@app.post("/tool/skill_unregister")
async def skill_unregister_endpoint(req: SkillUnregisterRequest):
    """删除技能：移除脚本和配置文件。"""
    if not req.skill_name.strip():
        raise HTTPException(status_code=400, detail="skill_name must not be empty")

    logger.info("skill_unregister: name=%r", req.skill_name)
    try:
        result = unregister_skill(req.skill_name)
    except Exception as exc:
        logger.exception("skill_unregister %r unexpected error", req.skill_name)
        return {"success": False, "error": f"删除异常: {exc}"}

    if not result.get("success"):
        logger.warning("skill_unregister %r failed: %s", req.skill_name, result.get("error"))

    return result


@app.post("/tool/skill_info")
async def skill_info_endpoint(req: SkillInfoRequest):
    """查看技能详细信息。"""
    if not req.skill_name.strip():
        raise HTTPException(status_code=400, detail="skill_name must not be empty")

    logger.info("skill_info: name=%r", req.skill_name)
    try:
        return skill_info(req.skill_name)
    except Exception as exc:
        logger.exception("skill_info %r unexpected error", req.skill_name)
        return {"error": f"查询异常: {exc}"}


@app.post("/tool/skill_update")
async def skill_update_endpoint(req: SkillUpdateRequest):
    """更新技能代码或配置。"""
    if not req.skill_name.strip():
        raise HTTPException(status_code=400, detail="skill_name must not be empty")

    logger.info("skill_update: name=%r has_code=%s", req.skill_name, bool(req.code))
    try:
        result = update_skill(req.skill_name, code=req.code, auto_install_deps=req.auto_install_deps)
    except Exception as exc:
        logger.exception("skill_update %r unexpected error", req.skill_name)
        return {"success": False, "error": f"更新异常: {exc}"}

    if not result.get("success"):
        logger.warning("skill_update %r failed: %s", req.skill_name, result.get("error"))

    return result
