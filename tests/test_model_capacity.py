import unittest
from unittest.mock import patch
from core.model_capacity import context_capacity


class ModelCapacityTests(unittest.TestCase):
    def test_declared_capacity_is_not_replaced_by_generic_setting(self):
        with patch('core.model_capacity.config.CONTEXT_WINDOW', 32768):
            self.assertEqual(context_capacity({'context_length': 131072}), (131072, 'model_catalog'))

    def test_unknown_capacity_uses_the_configured_window(self):
        # The fallback follows CONTEXT_WINDOW. Capping it at 32768 used to
        # silently floor every model that did not declare a window, even when the
        # environment asked for a larger one.
        with patch('core.model_capacity.config.CONTEXT_WINDOW', 262144):
            for spec in ({}, {'context_window': True}, {'context_length': '131072'}):
                self.assertEqual(context_capacity(spec), (262144, 'conservative_fallback'))

    def test_unknown_capacity_without_a_configured_window_stays_conservative(self):
        with patch('core.model_capacity.config.CONTEXT_WINDOW', 0):
            self.assertEqual(context_capacity({}), (32768, 'conservative_fallback'))


class DeclaredResearchContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_chat_model_keeps_large_report_and_late_counterevidence(self):
        import json
        from pathlib import Path
        from core.context_manager import ContextManager
        from unittest.mock import AsyncMock
        items = json.loads((Path(__file__).resolve().parents[1]/'config/models.json').read_text(encoding='utf-8'))
        spec = next(item for item in items if item['model']=='qwen3.5-flash')
        capacity, source = context_capacity(spec)
        self.assertEqual((capacity,source),(1000000,'model_catalog'))
        messages = [{'role':'user','content':'已保存公开报告与原文。'*23000+'末尾反证：历史资金不能证明当前需求。'}]
        manager = ContextManager(context_window=capacity)
        manager.auto_compact = AsyncMock(side_effect=AssertionError('unnecessary compression'))
        self.assertGreater(manager.estimate_tokens(messages),110000)
        self.assertEqual(await manager.process(messages),messages)
        manager.auto_compact.assert_not_awaited()

    async def test_verified_qwen_capacity_preserves_report_beyond_old_fallback(self):
        import json
        from pathlib import Path
        from core.context_manager import ContextManager
        from unittest.mock import AsyncMock
        items=json.loads((Path(__file__).resolve().parents[1]/'config/models.json').read_text(encoding='utf-8'))
        spec=next(item for item in items if item['model']=='qwen3.8-flash')
        capacity,source=context_capacity(spec)
        self.assertEqual((capacity,source),(1000000,'model_catalog'))
        messages=[{'role':'user','content':'公开研究原文与反证。'*15000+'末尾修订：原订单已取消。'}]
        manager=ContextManager(context_window=capacity)
        manager.auto_compact=AsyncMock(side_effect=AssertionError('incorrect early compaction'))
        self.assertGreater(manager.estimate_tokens(messages),32768)
        result=await manager.process(messages)
        self.assertEqual(result,messages)
        manager.auto_compact.assert_not_awaited()
