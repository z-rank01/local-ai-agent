import unittest,httpx
from plugins.finance.bridge import StockBridge
from core.tool_registry import ToolRegistry
from core import config
from tests.test_stock_bridge import make_broker,REF

class StructuredBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_tool_forwards_structured_selection_with_authorized_reference(self):
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(make_broker(calls))) as client:
            b=StockBridge('http://paper.test','test-token',client=client)
            b.set_turn('comparison','source-turn','核对双方资产',allow_report_body=True)
            await b.call_tool('stock_public_evidence_find',dict(reference=REF,subject_symbol='300070',financial_metric='assets',report_period='20260630'),'comparison')
        step=next(c['json']['tool_call'] for c in calls if c['path'].endswith('/step'))
        self.assertEqual(step['reference']['token'],REF)
        self.assertEqual((step['subject_symbol'],step['financial_metric'],step['report_period']),('300070','assets','20260630'))
        self.assertEqual(step['action'],'public_evidence_find');self.assertNotIn('search',step)

    def test_schema_is_same_read_tool_without_new_write_access(self):
        r=ToolRegistry(config.TOOLS_DIR, enabled_backends=('stock-bridge',))
        fn=next(x['function'] for x in r.get_definitions(allow_write=False) if x['function']['name']=='stock_public_evidence_find')
        properties=fn['parameters']['properties']
        self.assertIn('assets',properties['financial_metric']['enum']);self.assertNotIn('DCF',properties['financial_metric']['enum'])
        self.assertEqual(r.get_cloud_tier('stock_public_evidence_find'),'read')
