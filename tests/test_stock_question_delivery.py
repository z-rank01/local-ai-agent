import json
import unittest
import httpx
from plugins.finance.bridge import StockBridge
from tests.test_stock_bridge import make_broker


class QuestionDeliveryBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_directions_and_source_binding_reach_native_gateway(self):
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(make_broker(calls))) as client:
            bridge=StockBridge('http://stock','test',client=client)
            await bridge.call_tool('stock_research_submit',{'name':'万德斯','research_focus':'event_impact',
                'research_question':'中标对经营的影响','source_reference':'a'*64,'event_id':'b'*16},'conversation')
            action=calls[-1]['json']['tool_call']
            self.assertEqual(action['research_focus'],'event_impact')
            self.assertEqual(action['source_reference'],'a'*64)
            self.assertEqual(action['reference']['name'],'万德斯')
            await bridge.call_tool('stock_research_submit',{'name':'万德斯','research_focus':'comparison',
                'comparison_targets':['碧水源']},'conversation')
            self.assertEqual(calls[-1]['json']['tool_call']['comparison_targets'],['碧水源'])

    async def test_invalid_focus_source_or_targets_cannot_reach_backend(self):
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(make_broker(calls))) as client:
            bridge=StockBridge('http://stock','test',client=client)
            for change in [{'research_focus':'trade'},{'research_focus':'event_impact'},
                           {'research_focus':'comparison','comparison_targets':[]},
                           {'comparison_targets':'碧水源'}]:
                self.assertIn('error',await bridge.call_tool('stock_research_submit',{'name':'万德斯',**change},'c'))
            self.assertEqual(calls,[])
