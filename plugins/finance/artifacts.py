"""Finance report pages keep reference ownership and revision validation."""
import re
from harness.contracts.artifacts import ArtifactError
from core.stock_bridge import ReportUpdated


class FinanceArtifacts:
    def __init__(self, transport):
        self.transport = transport

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
