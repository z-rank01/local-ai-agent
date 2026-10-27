"""Finance report pages keep reference ownership and revision validation."""
import hashlib
import re
from harness.contracts.artifacts import ArtifactError
from plugins.finance.bridge import ReportUpdated


class FinanceArtifacts:
    def __init__(self, transport):
        self.transport = transport

    def archive(self, saved, content, snapshot):
        if not re.fullmatch(r'[a-f0-9]+', saved.conversation_id) or not re.fullmatch(r'[a-f0-9]+', saved.identity):
            raise ArtifactError(422, '无效材料定位')
        payload = content.encode('utf-8')
        digest = hashlib.sha256(payload).hexdigest()
        virtual = f'/workspace/reports/_research/{saved.conversation_id}/{saved.identity}-{digest}.md'
        target = snapshot.resolve(virtual)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('xb') as handle:
                handle.write(payload)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise OSError('Research material archive conflict')
        return virtual

    async def read_page(self, saved, request):
        params = saved.metadata.get('params')
        reference = params.get('reference') if isinstance(params, dict) else None
        if not isinstance(reference, str) or not re.fullmatch(r'[0-9a-f]{64}', reference):
            raise ArtifactError(409, '这份历史报告缺少可核对的读取引用，仍可阅读已保存页面')
        bridge = self.transport()
        if bridge is None:
            raise ArtifactError(503, '股票技能未连接，已保存页面仍可阅读；连接后可重试翻页')
        try:
            return await bridge.read_report_page(saved.conversation_id, reference, request.page, request.request_id,
                **({'section': request.section, 'revision': request.revision} if request.section is not None else {}))
        except ReportUpdated as exc:
            raise ArtifactError(409, {'message': str(exc), 'code': 'REPORT_UPDATED'}) from exc
        except ValueError as exc:
            raise ArtifactError(503, str(exc)) from exc
