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
from fastapi.responses import StreamingResponse
from contextlib import aclosing
import json
from stream_exec import stream_shell, stream_python
from pydantic import BaseModel, Field

from sandbox import run_python, run_shell, run_pip_install
import package_jobs
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
    timeout: int = Field(default=60, ge=1, le=300)
    cwd: str = '/workspace'


class PipInstallRequest(BaseModel):
    packages: list[str] = Field(..., description="List of pip package names to install")
    # Legacy argument accepted but no longer imposes an installation deadline.
    timeout: int | None = None


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
    try:
        result = run_shell(req.command, timeout=timeout, cwd=req.cwd)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result["exit_code"] != 0 and not result["stdout"] and result["stderr"]:
        logger.warning("shell_exec failed: exit=%d stderr=%s", result["exit_code"], result["stderr"][:200])

    return result


@app.post('/tool/shell_exec/stream')
async def shell_exec_stream(req: ShellExecRequest):
    async def events():
        async with aclosing(stream_shell(req.command, timeout=min(req.timeout, _SHELL_TIMEOUT), cwd=req.cwd)) as stream:
            async for item in stream:
                yield json.dumps(item, ensure_ascii=False) + '\n'
    return StreamingResponse(events(), media_type='application/x-ndjson')


_PKG_NAME_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._\-]*(\[[\w,]+\])?([<>=!~]+[\w.*]+)?$')

@app.post("/tool/pip_install")
def pip_install(req: PipInstallRequest):
    """Install Python packages to /packages for use in code_exec."""
    if not req.packages:
        raise HTTPException(status_code=400, detail="packages list must not be empty")

    for pkg in req.packages:
        if not _PKG_NAME_RE.match(pkg):
            raise HTTPException(status_code=400, detail=f"Invalid package name: {pkg}")

    return package_jobs.start(req.packages)


@app.post("/tool/file_convert")
def file_convert(req: FileConvertRequest):
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


@app.post('/tool/code_exec/stream')
async def code_exec_stream(req: CodeExecRequest):
    async def events():
        async with aclosing(stream_python(req.code, min(req.timeout, _PYTHON_TIMEOUT))) as stream:
            async for item in stream:
                yield json.dumps(item, ensure_ascii=False) + '\n'
    return StreamingResponse(events(), media_type='application/x-ndjson')

@app.post('/tool/skill_run/stream')
async def skill_run_stream(req: SkillRunRequest):
    from skill_registry import _SKILLS_DIR, _WRAPPER_TEMPLATE, _load_skill_config, _save_skill_config, _now_iso
    if not _re.fullmatch(r'[\w-]+', req.skill_name):
        raise HTTPException(422, 'Invalid skill name')
    path = _SKILLS_DIR / (req.skill_name + '.py')
    if not path.is_file() or not path.resolve().is_relative_to(_SKILLS_DIR.resolve()):
        raise HTTPException(404, 'Skill not found')
    cfg = _load_skill_config(req.skill_name)
    if cfg and cfg.get('dependencies') and not cfg.get('dependencies_installed'):
        raise HTTPException(409, '技能依赖未安装，请先安装依赖或重新注册')
    async def events():
        async with aclosing(stream_python(_WRAPPER_TEMPLATE.format(skill_path=str(path)),
            min(req.timeout, _PYTHON_TIMEOUT), [json.dumps(req.params)])) as stream:
            async for item in stream:
                if item.get('event') == 'result':
                    result = item['result']
                    result['model_observation'] = {'exit_code':result['exit_code'],
                        'error':'技能执行失败或输出不完整，原始输出保留在本地回执。'}
                    if result['exit_code'] == 0:
                        try:
                            result['result'] = json.loads(result['stdout'].strip().splitlines()[-1])
                            # Raw stdout can contain local-only result JSON; project the structured result only.
                            result['model_observation'] = {'exit_code':0, 'result': result['result']}
                        except (ValueError, IndexError):
                            result['error'] = '技能返回结果不是完整 JSON，可能被截断；不把原始输出发送给模型'
                    if cfg:
                        cfg['run_count'] = cfg.get('run_count', 0) + 1
                        cfg['last_run_at'] = _now_iso()
                        _save_skill_config(req.skill_name, cfg)
                yield json.dumps(item, ensure_ascii=False) + '\n'
    return StreamingResponse(events(), media_type='application/x-ndjson')


class PackageStatusRequest(BaseModel):
    job_id: str
    wait_seconds: int = Field(default=0, ge=0, le=20)

@app.post('/tool/package_list')
def package_list():
    return {'location':'/packages', 'packages':package_jobs.inventory(), 'jobs':package_jobs.list_jobs()}

@app.post('/tool/package_status')
def package_status(req: PackageStatusRequest):
    try: return package_jobs.status(req.job_id,req.wait_seconds)
    except ValueError as exc: raise HTTPException(404,str(exc))

@app.post('/tool/package_cancel')
def package_cancel(req: PackageStatusRequest):
    try: return package_jobs.cancel(req.job_id)
    except ValueError as exc: raise HTTPException(404,str(exc))

@app.get('/package-jobs')
def package_jobs_list():
    return package_jobs.list_jobs()
