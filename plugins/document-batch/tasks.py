"""Task projections read the workspace service's original records."""
import re
import httpx
from harness.contracts.tasks import TaskUnavailable


class BatchTasks:
    namespace = 'documents'

    def __init__(self, client):
        self.client = client

    @staticmethod
    def present(workspace, record):
        files = record['files']
        done = sum(item['state'] == 'SUCCEEDED' for item in files)
        context = record['request']['context']
        return {'id': f'documents:{workspace}:{record["id"]}', 'source': 'documents', 'source_label': '批量文档处理',
            'kind': 'documents', 'conversation_id': context['conversation_id'], 'workspace_id': workspace,
            'request_id': record['id'], 'title': f'文档处理 · {len(files)} 个文件', 'model': context.get('model', ''),
            'status': {'PARTIAL': 'FAILED', 'CANCELLED': 'STOPPED'}.get(record['state'], record['state']),
            'business_status': record['state'], 'phase': f'已生成 {done}/{len(files)} 个文件；原件保留',
            'created_at': record['created_at'], 'updated_at': record.get('updated_at', record['created_at']),
            'last_seq': 0, 'error': record.get('error') or '; '.join(item.get('error', '') for item in files if item.get('error')),
            'resumable': False, 'resume_blocker': '中断任务不自动重发，请核对已完成文件后明确提交新任务',
            'checkpoint_available': False, 'actions': ['stop'] if record['state'] in ('RUNNING', 'QUEUED') else [],
            'artifacts': [{'path': '/workspace/' + item[kind], 'title': kind, 'sha256': item[kind + '_sha256']}
                          for item in files if item['state'] == 'SUCCEEDED' for kind in ('pdf', 'text')]}

    async def list(self, workspace_id):
        if self.client is None:
            raise TaskUnavailable('插件未加载')
        rows, unavailable, online = [], [], False
        for workspace in self.client.workspaces.records():
            identity = workspace['id']
            if workspace_id and workspace_id != identity:
                continue
            if self.client.identity not in self.client.store.list(identity, 'plugin_service'):
                continue
            try:
                await self.client.health(identity)
                tasks = await self.client.request(identity, 'GET', '/tasks')
                rows.extend(self.present(identity, row) for row in tasks['tasks'])
                online = True
            except httpx.ConnectError:
                # Preparation records a binding without starting a container.
                # One offline workspace must not hide another workspace's live jobs.
                unavailable.append(identity)
        return {'runs': rows, 'status': {'online': online, 'title': '批量文档处理',
            'details': ['原件保留；可选环境按需运行'],
            'message': '未启动的工作区：' + '、'.join(unavailable) if unavailable else ''}}

    def split(self, run_id):
        match = re.fullmatch(r'documents:(default|[a-f0-9]{12}):([a-f0-9]{64})', run_id)
        if not match or self.client is None:
            raise TaskUnavailable('无效或未加载的文档任务')
        return match.groups()

    async def get(self, run_id):
        workspace, identity = self.split(run_id)
        await self.client.health(workspace)
        return self.present(workspace, await self.client.request(workspace, 'GET', '/tasks/' + identity))

    async def control(self, run_id, action):
        workspace, identity = self.split(run_id)
        if action != 'stop':
            raise TaskUnavailable('批处理只支持停止；中断后不能自动续发')
        await self.client.health(workspace)
        return self.present(workspace, await self.client.request(workspace, 'POST', '/tasks/' + identity + '/cancel'))
