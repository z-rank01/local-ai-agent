"""Finance task projections delegate to the existing stock service and its gates."""
from urllib.parse import quote
import httpx
from harness.contracts.tasks import TaskUnavailable


class FinanceTasks:
    namespace = 'stock'

    @staticmethod
    def present(row):
        return {**row, 'source_label': '金融研究任务'}

    @property
    def service(self):
        from .service import stock_service
        return stock_service

    async def list(self, workspace_id):
        status = await self.service.status()
        rows = []
        if status['online'] and (not workspace_id or workspace_id == 'stock'):
            try:
                rows = [self.present(row) for row in await self.service.request('/api/control/runs')]
            except (ValueError, httpx.HTTPError, OSError) as exc:
                status['message'] = str(exc) if isinstance(exc, ValueError) else '股票任务列表暂时不可读'
        worker = status.get('worker') or {}
        summary = {k: status.get(k) for k in ('online', 'worker', 'message')}
        summary.update(title='金融研究服务', details=[
            '领取任务：' + ('开启' if worker.get('enabled') else '暂停'),
            '实际执行：' + ('有任务' if worker.get('busy') else '空闲')])
        return {'runs': rows, 'status': summary,
                'compatibility_key': 'stock_service'}

    async def get(self, run_id):
        if not (await self.service.status())['online']:
            raise TaskUnavailable('股票服务离线，无法确认任务当前状态')
        rows = await self.service.request('/api/control/runs')
        row = next((r for r in rows if r['id'] == run_id), None)
        if row is None:
            raise KeyError('股票任务不存在')
        return self.present(row)

    async def control(self, run_id, action):
        if not (await self.service.status())['online']:
            raise TaskUnavailable('股票服务离线；请通过唯一入口开启后操作')
        # The owner still validates allowed operations, model lineage and account gates.
        return await self.service.request('/api/control/jobs/' + quote(run_id.partition(':')[2], safe=''),
                                          {'action': action}, timeout=40)
