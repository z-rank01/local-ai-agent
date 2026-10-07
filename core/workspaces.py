"""Workspace metadata lives in the conversation DB; host roots are never copied.

Only the launcher reconciles Docker. Runtime snapshots are immutable per turn.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import uuid
import secrets
import re
from urllib.parse import urlparse
from pathlib import Path

import httpx
from fastapi import HTTPException

from . import config, model_settings


def tool_token(workspace_id: str) -> str:
    if workspace_id != 'default' and not re.fullmatch('[a-f0-9]{12}', workspace_id):
        raise ValueError('Invalid workspace id')
    path = config.PROJECT_ROOT / 'data/private/workspace-tools' / (workspace_id + '.token')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    value = path.read_text(encoding='utf-8').strip()
    if len(value) < 32:
        raise RuntimeError('工作区工具凭据无效，请通过启动.bat重新启动')
    return value


def overlaps(a: str | Path, b: str | Path) -> bool:
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or a in b.parents or b in a.parents


class WorkspaceSnapshot:
    def __init__(self, record: dict, base: Path):
        self.record = copy.deepcopy(record)
        self.id = record['id']
        self.version = record['version']
        self.managed = base if self.id == 'default' else base / '.harness' / self.id
        self.roots = {'/workspace': self.managed}
        self.roots.update({f"/roots/{r['id']}": Path(r['host_path']) for r in record['roots']})
        self.cwd = f"/roots/{record['primary_root_id']}" if record.get('primary_root_id') else '/workspace'

    def resolve(self, path: str) -> Path:
        value = (path or '/workspace').replace('\\', '/')
        if not value.startswith('/'):
            value = self.cwd + '/' + value
        for virtual, host in self.roots.items():
            if value == virtual or value.startswith(virtual + '/'):
                if not host.is_dir():
                    raise HTTPException(404, '工作目录不可用：' + str(host))
                target = (host / value[len(virtual):].lstrip('/')).resolve()
                owner = next((root for root in self.roots.values() if target.is_relative_to(root.resolve())), None)
                if owner is None:
                    raise HTTPException(403, '路径越出工作区目录')
                if any(part == ".git" for part in target.relative_to(owner.resolve()).parts):
                    raise HTTPException(403, "版本库内部文件不可直接修改或预览")
                if target.is_relative_to(self.managed / '.harness'):
                    raise HTTPException(403, '其他工作区的托管目录不可访问')
                return target
        raise HTTPException(403, '路径不属于当前工作区')

    def virtual(self, path: Path) -> str:
        target = path.resolve()
        for virtual, host in sorted(self.roots.items(), key=lambda x: len(str(x[1])), reverse=True):
            if target.is_relative_to(host.resolve()):
                rel = target.relative_to(host.resolve()).as_posix()
                return virtual if rel == '.' else virtual + '/' + rel
        raise HTTPException(403, '路径不属于当前工作区')

    def validate_links(self):
        """Host-side reparse checks cover NTFS junctions hidden by VM file sharing."""
        roots = [p.resolve() for p in self.roots.values()]
        for root in roots:
            if not root.is_dir(): continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                if root == self.managed:
                    dirs[:] = [name for name in dirs if name not in {'.harness', '.git', '.uploads'}]
                for name in list(dirs) + files:
                    entry = Path(directory) / name
                    try:
                        reparse = bool(getattr(entry.lstat(), 'st_file_attributes', 0) & 0x400) or entry.is_symlink()
                        if not reparse: continue
                        if name in dirs: dirs.remove(name)
                        target = entry.resolve()
                        if not any(target.is_relative_to(p) for p in roots) or target.is_relative_to(self.managed / '.harness'):
                            raise HTTPException(403, '目录链接越出工作区，请移除链接或添加其目标目录')
                    except (OSError, RuntimeError) as exc:
                        raise HTTPException(403, '目录链接无法安全解析') from exc

    @property
    def host_roots(self):
        return list(self.roots.values())


class WorkspaceManager:
    def __init__(self, store, base: Path):
        self.store, self.base = store, base.resolve()
        self.active: dict[str, tuple[str, WorkspaceSnapshot]] = {}
        self.changing = {}
        self.lock = asyncio.Lock()
        with store._connect() as conn:
            if not conn.execute("SELECT 1 FROM workspaces WHERE id='default'").fetchone():
                record = dict(id='default', name='默认工作区', version=1, roots=[], primary_root_id=None,
                              cloud_read=model_settings.workspace_allowed(), cloud_write=model_settings.cloud_write_allowed(),
                              files_port=urlparse(config.SKILL_FILES_URL).port or 9101, runner_port=urlparse(config.SKILL_RUNNER_URL).port or 9102)
                conn.execute('INSERT INTO workspaces VALUES (?,?,?)', ('default', record['name'], json.dumps(record)))

    def records(self):
        with self.store._connect() as conn:
            return [json.loads(r['config']) for r in conn.execute('SELECT config FROM workspaces ORDER BY rowid')]

    def get(self, workspace_id='default'):
        record = next((r for r in self.records() if r['id'] == workspace_id), None)
        if record is None:
            raise HTTPException(404, '工作区不存在')
        if workspace_id == 'default':
            record['cloud_read'] = model_settings.workspace_allowed()
            record['cloud_write'] = model_settings.cloud_write_allowed()
        return record

    def snapshot(self, workspace_id='default'):
        return WorkspaceSnapshot(self.get(workspace_id), self.base)

    def save(self, record):
        with self.store._connect() as conn:
            conn.execute('INSERT INTO workspaces VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, config=excluded.config',
                         (record['id'], record['name'], json.dumps(record, ensure_ascii=False)))

    def shared_execution_scope(self, snapshot, other):
        # Managed children are masked in the default containers.
        pairs = [(a, b) for a in snapshot.host_roots for b in other.host_roots
                 if not (a == self.base and b.is_relative_to(self.base / '.harness'))
                 and not (b == self.base and a.is_relative_to(self.base / '.harness'))]
        return snapshot.id == other.id or any(overlaps(a, b) for a, b in pairs)

    def conflict(self, snapshot):
        for _, (conversation_id, running) in self.active.items():
            if self.shared_execution_scope(snapshot, running):
                return conversation_id
        return None

    def assert_idle(self, snapshot):
        owner = self.conflict(snapshot)
        changing = any(self.shared_execution_scope(snapshot, pending) for group in self.changing.values() for pending in group)
        if owner or changing:
            raise HTTPException(409, {'message': '工作区或重叠目录正在执行任务', 'conversation_id': owner if owner and not owner.startswith('__') else None})

    def acquire(self, snapshot, conversation_id):
        self.assert_idle(snapshot)
        key = uuid.uuid4().hex
        self.active[key] = (conversation_id, snapshot)
        return key

    def public(self, record):
        snapshot = WorkspaceSnapshot(record, self.base)
        owner = self.conflict(snapshot)
        return {**{k: v for k, v in record.items() if k not in {'root_history', 'pending_config'}}, 'managed_path': str(snapshot.managed), 'primary_path': snapshot.cwd,
                'roots': [{**r, 'path': '/roots/' + r['id'], 'available': Path(r['host_path']).is_dir()} for r in record['roots']],
                'active_conversation_id': owner if owner and not owner.startswith('__') else None, 'pending': bool(record.get('pending_config') or record.get('last_error'))}

    @staticmethod
    def _port(used):
        while True:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            if port not in used:
                return port

    def prepare(self, payload, old=None):
        name = str((payload.get('name') if 'name' in payload else old['name'] if old else '') or '').strip()
        if not name or len(name) > 80:
            raise HTTPException(422, '工作区名称需要 1–80 个字符')
        old_roots = old['roots'] if old else []
        history = copy.deepcopy(old.get('root_history', {})) if old else {}
        for r in old_roots:
            history[os.path.normcase(r['host_path'])] = r
        roots = []
        for raw in (payload.get('roots') if payload.get('roots') is not None else old_roots):
            host = Path(str(raw.get('host_path', ''))).expanduser()
            if not host.is_absolute() or not host.is_dir():
                raise HTTPException(422, '请选择存在的本机绝对目录')
            host = host.resolve()
            if overlaps(host, self.base) or overlaps(host, config.PROJECT_ROOT / 'data/private'):
                raise HTTPException(422, '托管工作区和应用私密配置不能作为外部目录添加')
            if any(overlaps(host, r['host_path']) for r in roots):
                raise HTTPException(422, '同一工作区不能添加重复或相互包含的目录')
            match = next((r for r in old_roots if Path(r['host_path']) == host), None) or history.get(os.path.normcase(str(host)))
            roots.append(dict(id=match['id'] if match else uuid.uuid4().hex[:12],
                              host_path=str(host), name=str(raw.get('name') or host.name or host.anchor)))
        record = copy.deepcopy(old) if old else dict(id=uuid.uuid4().hex[:12], version=0, cloud_read=False, cloud_write=False)
        record.pop('pending_config', None)
        for r in roots: history[os.path.normcase(r['host_path'])] = r
        record.update(name=name, roots=roots, root_history=history, version=record['version'] + 1)
        valid_ids = {r['id'] for r in roots}
        if 'primary_root_id' in payload:
            primary = payload['primary_root_id']
            if primary is not None and primary not in valid_ids:
                raise HTTPException(422, '主要目录不属于工作区')
        elif old and old.get('primary_root_id') in valid_ids:
            primary = old['primary_root_id']
        elif old and old.get('primary_root_id') is None and (old['id'] == 'default' or old_roots):
            primary = None
        else:
            primary = roots[0]['id'] if roots else None
        if payload.get('primary_host_path'):
            primary = next((r['id'] for r in roots if Path(r['host_path']) == Path(payload['primary_host_path']).resolve()), None)
            if primary is None: raise HTTPException(422, '主要目录不属于工作区')
        record['primary_root_id'] = primary
        record['cloud_read'] = bool(payload.get('cloud_read', record['cloud_read']))
        if payload.get('cloud_write') and not record['cloud_read']:
            raise HTTPException(422, '请先允许云端读取工作区，再开启写入与执行')
        record['cloud_write'] = bool(payload.get('cloud_write', record['cloud_write'])) and record['cloud_read']
        added = any(r['id'] not in {x['id'] for x in old_roots} for r in roots)
        if added and record['cloud_read'] and not payload.get('confirm_cloud_expansion'):
            raise HTTPException(422, '增加目录将扩大云端授权，请确认后再保存')
        if not old:
            used = {p for r in self.records() for p in (r['files_port'], r['runner_port'])} | {9510, 9511}
            record['files_port'] = self._port(used)
            record['runner_port'] = self._port(used | {record['files_port']})
        WorkspaceSnapshot(record, self.base).validate_links()
        return record

    def manifest(self, apply_workspace_id=None):
        """Compose input is structured and fixed; no executable/argument fields."""
        records = [(r.get('pending_config') or r) if r['id'] == apply_workspace_id else r for r in self.records()]
        path = config.PROJECT_ROOT / 'data/private/workspace-containers.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
        for r in records:
            managed = WorkspaceSnapshot(r, self.base).managed
            for folder in ('data', 'docs', 'reports', 'skills', 'converters'):
                (managed / folder).mkdir(parents=True, exist_ok=True)

    async def verify(self, record):
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            for field in ('files_port', 'runner_port'):
                response = await client.get(f"http://127.0.0.1:{record[field]}/health", headers={'X-Workspace-Token': tool_token(record['id'])})
                response.raise_for_status()
                data = response.json()
                if (data.get('workspace_id') != record['id'] or data.get('workspace_version') != record['version']
                        or data.get('workspace_protocol') != 2 or not data.get('token_authenticated')):
                    raise RuntimeError('工具容器工作区身份或版本不匹配，请使用启动.bat -Build 重建')

    async def reconcile(self, workspace_id="default"):
        from .service_supervisor import run_operation
        self.manifest(workspace_id)
        record = self.get(workspace_id)
        desired = record.get('pending_config') or record
        unavailable = [r['host_path'] for r in desired['roots'] if not Path(r['host_path']).is_dir()]
        if unavailable:
            raise RuntimeError('工作目录不可用：' + '、'.join(unavailable))
        await run_operation('workspace_sync', workspace_id=workspace_id)
        await self.verify(desired)
        desired.pop('last_error', None)
        if workspace_id == "default":
            model_settings.update_settings("", workspace=desired["cloud_read"], cloud_write=desired["cloud_write"])
        self.save(desired)

    async def ensure(self, workspace_id, *, apply_pending=False):
        record = self.get(workspace_id)
        try:
            if apply_pending and record.get('pending_config'):
                raise RuntimeError('配置待生效')
            await self.verify(record)
        except (RuntimeError, httpx.HTTPError):
            async with self.lock:
                record = self.get(workspace_id)
                snapshots = [WorkspaceSnapshot(record, self.base), WorkspaceSnapshot(record.get('pending_config') or record, self.base)]
                for snapshot in snapshots: self.assert_idle(snapshot)
                self.changing[workspace_id] = snapshots
                try:
                    await self.reconcile(workspace_id)
                except (RuntimeError, httpx.HTTPError) as exc:
                    raise HTTPException(503, str(exc)) from exc
                finally:
                    self.changing.pop(workspace_id, None)
        current = self.get(workspace_id)
        if not current.get('pending_config') and current.get('last_error'):
            current.pop('last_error', None)
            self.save(current)
        return self.snapshot(workspace_id)

    async def update(self, payload, workspace_id=None):
        async with self.lock:
            old = self.get(workspace_id) if workspace_id else None
            record = self.prepare(payload, old)
            self.assert_idle(WorkspaceSnapshot(record, self.base))
            if old:
                self.assert_idle(WorkspaceSnapshot(old, self.base))
            self.changing[record['id']] = [WorkspaceSnapshot(r, self.base) for r in (record, old) if r]
            self.save({**old, 'pending_config': record} if old else record)
            try:
                await self.reconcile(record["id"])
            except (RuntimeError, httpx.HTTPError) as exc:
                error = str(exc)
                if old:
                    self.save(old)
                    try:
                        await self.reconcile(old['id'])
                    except (RuntimeError, httpx.HTTPError):
                        pass  # Identity checks reject a partially replaced group.
                    self.save({**old, 'pending_config': record, 'last_error': error})
                else:
                    self.save({**record, 'last_error': error})
                self.manifest()
            finally:
                self.changing.pop(record['id'], None)
            return self.public(self.get(record['id']))

    def delete(self, workspace_id):
        self.assert_idle(self.snapshot(workspace_id))
        with self.store._connect() as conn:
            if workspace_id == 'default' or conn.execute('SELECT 1 FROM conversations WHERE workspace_id=?', (workspace_id,)).fetchone():
                raise HTTPException(409, '默认工作区或被会话引用的工作区不能删除')
            conn.execute('DELETE FROM workspaces WHERE id=?', (workspace_id,))
        self.manifest()
