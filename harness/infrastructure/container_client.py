"""Authenticated access to registered, lazily started workspace services."""
import asyncio
import json
import httpx
from core.service_supervisor import run_operation
from core.workspaces import tool_token
from .capability_store import CapabilityStore
from .plugin_services import service_spec


class ContainerClient:
    def __init__(self, project, workspaces, plugin_id, service_id):
        self.project, self.workspaces = project, workspaces
        self.plugin_id, self.service_id = plugin_id, service_id
        self.store = CapabilityStore(workspaces.store._connect)
        self.lock = asyncio.Lock()
        self.started = set()

    @property
    def identity(self):
        return self.plugin_id + ':' + self.service_id

    async def operation(self, action, workspace_id='default', timeout=180):
        return await run_operation('plugin_' + action, plugin_id=self.plugin_id,
            service_id=self.service_id, workspace_id=workspace_id, timeout=timeout)

    async def prepared(self):
        result = await self.operation('probe')
        labels = json.loads(result.get('output') or '{}')
        if labels.get('local-ai-agent.revision') != service_spec(self.project, self.plugin_id, self.service_id)['revision']:
            raise ValueError('插件执行环境尚未准备或资源版本已变化，请先准备环境')

    async def prepare(self):
        result = await self.operation('prepare')
        while json.loads(result['output'])['state'] == 'PREPARING':
            await asyncio.sleep(2)
            result = await self.operation('prepare_status')
        state = json.loads(result['output'])
        if state['state'] != 'PREPARED':
            raise ValueError(state.get('error') or '插件环境准备中断')
        await self.prepared()
        return {'state': 'PREPARED'}

    async def request(self, workspace_id, method, path, body=None, *, start=False):
        if start:
            async with self.lock:
                if workspace_id not in self.started:
                    await self.prepared()
                    await self.operation('start', workspace_id)
                    await self.health(workspace_id)
                    self.started.add(workspace_id)
        binding = self.store.list(workspace_id, 'plugin_service').get(self.identity)
        if binding is None:
            raise ValueError('此工作区尚未启动插件服务')
        async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
            response = await client.request(method, f'http://127.0.0.1:{binding["port"]}' + path,
                json=body, headers={'X-Workspace-Token': tool_token(workspace_id)})
        if response.is_error:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = None
            raise ValueError(str(detail or f'插件服务返回 HTTP {response.status_code}'))
        return response.json()

    async def health(self, workspace_id, *, current=True):
        result = await self.request(workspace_id, 'GET', '/health')
        expected = {'workspace_id': workspace_id, 'plugin_id': self.plugin_id, 'service_id': self.service_id}
        if current:
            expected.update(workspace_version=self.workspaces.get(workspace_id)['version'],
                revision=service_spec(self.project, self.plugin_id, self.service_id)['revision'])
        if any(result.get(key) != value for key, value in expected.items()):
            raise ValueError('插件服务身份或资源版本不匹配')
        return result

    async def stop(self):
        # Also reconcile persisted bindings after a BFF restart. An offline container
        # is confirmed stopped by the launcher; an online one must acknowledge drain.
        async with self.lock:
            for record in self.workspaces.records():
                identity = record['id']
                if self.identity not in self.store.list(identity, 'plugin_service'):
                    continue
                try:
                    await self.health(identity, current=False)
                except httpx.ConnectError:
                    pass
                else:
                    drained = await self.request(identity, 'POST', '/drain')
                    if drained.get('drained') is not True:
                        raise ValueError('插件任务收尾未确认')
                await self.operation('stop', identity)
                self.started.discard(identity)

    async def assert_reconfigurable(self, snapshot):
        for record in self.workspaces.records():
            identity = record['id']
            if (self.identity not in self.store.list(identity, 'plugin_service') or
                    not self.workspaces.shared_execution_scope(snapshot, self.workspaces.snapshot(identity))):
                continue
            try:
                await self.health(identity, current=False)
            except httpx.ConnectError:
                continue
            activity = await self.request(identity, 'GET', '/activity')
            if activity.get('inflight') != 0:
                raise RuntimeError('相关目录仍有插件任务，请等待完成或停止后再修改工作区')
