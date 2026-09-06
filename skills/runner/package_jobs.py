"""Observable pip jobs. No installation wall-clock deadline; explicit cancellation."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name
from sandbox import _SAFE_ENV, _PACKAGES_DIR, _PIP_MIRROR

_ROOT = Path(_PACKAGES_DIR) / '.install-jobs'
_LOCK = threading.RLock()
_JOBS = {}
_PROCESSES = {}

def inventory():
    script = "import importlib.metadata as m,json; d={}; [(d.setdefault(x.metadata['Name'].lower().replace('_','-'),x.version)) for x in m.distributions() if x.metadata['Name']]; print(json.dumps(d))"
    result = subprocess.run([sys.executable, '-c', script], env=_SAFE_ENV, capture_output=True, text=True, timeout=30)
    if result.returncode: raise RuntimeError('无法读取 Python 包清单：' + result.stderr[-1000:])
    return json.loads(result.stdout)

def _save(job):
    _ROOT.mkdir(parents=True, exist_ok=True)
    target = _ROOT / (job['job_id'] + '.json')
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    temp.replace(target)

def _load():
    with _LOCK:
        if not _ROOT.exists(): return
        for path in _ROOT.glob('*.json'):
            if path.stem in _JOBS: continue
            job = json.loads(path.read_text(encoding='utf-8'))
            if job['status'] in ('queued', 'running'):
                job.update(status='interrupted', ended_at=time.time(), error='执行服务重启，安装已中断，请检查包状态后重试。')
                _save(job)
            _JOBS[path.stem] = job

def snapshot(job):
    data = dict(job)
    data['elapsed'] = round((job.get('ended_at') or time.time()) - job['started_at'], 1)
    data['log'] = job.get('log', '')[-8000:]
    data['completed'] = job['status'] not in ('queued', 'running')
    data['next_action'] = ('安装仍在后台运行；可先向用户说明进度或处理其他任务。需要这些包前用 package_status 查询，不能声称已安装成功。' if not data['completed'] else '核对结果后继续原任务；失败时报告具体日志，不把安装失败当作整个任务已完成。')
    return data

def list_jobs():
    _load()
    with _LOCK:
        return [snapshot(j) for j in sorted(_JOBS.values(),key=lambda j:j['started_at'],reverse=True)[:20]]

def status(job_id, wait_seconds=0):
    _load()
    deadline=time.monotonic()+min(max(wait_seconds,0),20)
    while True:
        with _LOCK:
            if job_id not in _JOBS: raise ValueError('安装任务不存在')
            data=snapshot(_JOBS[job_id])
        if data['completed'] or time.monotonic()>=deadline: return data
        time.sleep(.2)

def _command(packages):
    return [sys.executable,'-u','-m','pip','install','--target',_PACKAGES_DIR,'--upgrade','--progress-bar','off','-i',_PIP_MIRROR,*packages]

def _worker(job_id):
    with _LOCK:
        job=_JOBS[job_id]
        if job['status']=='cancelled': return
        try:
            proc=subprocess.Popen(_command(job['pending']), stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                text=True,encoding='utf-8',errors='replace',env=_SAFE_ENV,start_new_session=True)
            _PROCESSES[job_id]=proc
            job['status']='running'; _save(job)
        except Exception as exc:
            job.update(status='failed',error=str(exc),ended_at=time.time()); _save(job); return
    try:
        for line in proc.stdout:
            with _LOCK:
                job['log']=(job.get('log','')+line)[-64000:]; _save(job)
        code=proc.wait()
        with _LOCK:
            if job['status']!='cancelled':
                job.update(status='succeeded' if code==0 else 'failed',exit_code=code,ended_at=time.time())
                if code: job['error']='pip 安装失败，请查看日志'
            _save(job)
    except Exception as exc:
        with _LOCK:
            job.update(status='failed',error=str(exc),ended_at=time.time()); _save(job)
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait()
    finally:
        proc.stdout.close()
        with _LOCK: _PROCESSES.pop(job_id,None)

def start(packages):
    _load()
    requested=[Requirement(p) for p in packages]
    if not requested or any(r.url or r.marker for r in requested): raise ValueError('只支持包名、extras 和版本约束')
    with _LOCK:
        active=next((j for j in _JOBS.values() if j['status'] in ('queued','running')),None)
        if active:
            return {**snapshot(active),'request_started':False,'message':'已有安装正在运行，避免并发修改包目录。待其完成后检查所需包。'}
        installed=inventory()
        skipped=[str(r) for r in requested if not r.extras and canonicalize_name(r.name) in installed and installed[canonicalize_name(r.name)] in r.specifier]
        pending=[str(r) for r in requested if str(r) not in skipped]
        job={'job_id':uuid.uuid4().hex,'packages':packages,'pending':pending,'skipped':skipped,
             'started_at':time.time(),'status':'queued' if pending else 'succeeded','log':'','location':_PACKAGES_DIR}
        if not pending: job.update(ended_at=time.time(),exit_code=0,log='所有请求的包已满足要求，无需安装。')
        _JOBS[job['job_id']]=job; _save(job)
        if pending: threading.Thread(target=_worker,args=(job['job_id'],),daemon=True).start()
        return snapshot(job)

def cancel(job_id):
    _load()
    with _LOCK:
        if job_id not in _JOBS: raise ValueError('安装任务不存在')
        job=_JOBS[job_id]
        if job['status'] in ('queued','running'):
            job.update(status='cancelled',ended_at=time.time(),error='用户已停止安装，可能存在部分安装内容；下次先查询包清单。')
            proc=_PROCESSES.get(job_id)
            if proc:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            _save(job)
        return snapshot(job)
