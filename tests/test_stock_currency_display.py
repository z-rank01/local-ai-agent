import copy
import json
import unittest
from decimal import InvalidOperation
from types import SimpleNamespace

from plugins.finance.financial_receipts import currency_display
from plugins.finance.calculation_review import calculation_index, program_source_guidance
from plugins.finance.company_context import context_index, current_guidance
from tests.test_stock_calculation_review import history, REF
from tests.test_stock_company_context import observation
from tests import test_stock_source_quotes as quote_tests


class CurrencyDisplayTests(unittest.TestCase):
    def test_exact_units_keep_small_fraction_and_large_peer_value(self):
        self.assertEqual(currency_display('500764121.060000'),
            {'元':'500764121.06','万元':'50076.412106','亿元':'5.0076412106'})
        self.assertEqual(currency_display('82386253507.240000')['亿元'],'823.8625350724')
        self.assertEqual(currency_display('-49005498.150000')['万元'],'-4900.549815')
        self.assertEqual(currency_display('0.00'),{'元':'0','万元':'0','亿元':'0'})

    def test_no_float_or_nonfinite_value_accepted(self):
        for value in (500764121.06, 'NaN', 'Infinity', '5元', None):
            with self.subTest(value=value), self.assertRaises((ValueError, InvalidOperation)):
                currency_display(value)

    def test_presentation_preserves_full_precision_beyond_default_decimal_context(self):
        value='123456789012345678901234567890.123456'
        self.assertEqual(currency_display(value)['亿元'],'1234567890123456789012.34567890123456')

    def test_program_view_formats_existing_basis_without_changing_receipt(self):
        rows=calculation_index(history())[REF]; before=copy.deepcopy(rows)
        guide=program_source_guidance(rows)
        view=json.loads(guide[guide.index('[{'):])
        self.assertEqual(view[0]['unit_display']['年度营业收入']['万元'],'50076.412106')
        self.assertEqual(view[0]['annual_period'],'20251231')
        self.assertEqual(rows,before)
        self.assertIn('不代表本轮重新读取',guide)

    def test_comparison_formats_only_existing_bound_metrics_and_keeps_identity(self):
        obs=observation();obs['comparison_context']['subjects'][0]['metrics'][2]={'metric':'ocf','status':'MISSING'}
        index=context_index(history(obs));before=copy.deepcopy(index)
        guide=current_guidance(index,REF,[])
        rows=json.JSONDecoder().raw_decode(guide[guide.index('[{'):])[0]
        self.assertEqual(rows[0]['subjects'][1]['symbol'],'300070')
        self.assertEqual(rows[0]['subjects'][1]['metrics'][0]['unit_display']['亿元'],'0.000002')
        self.assertNotIn('unit_display',rows[0]['subjects'][0]['metrics'][2])
        self.assertEqual(index,before)
        self.assertIsNone(current_guidance(index,'d'*64,[]))


class CurrencyViewLoopTests(unittest.IsolatedAsyncioTestCase):
    agent = quote_tests.SourceQuoteLoopTests.agent
    async def test_actual_model_view_uses_authorized_history_without_new_source_call(self):
        seen=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,*args,**kwargs):
                seen.append(copy.deepcopy(messages))
                yield '',{'role':'assistant','content':'已有资料的2025年度营收约5.01亿元。'}
        messages=history()+[{'role':'user','content':'解释已有报告'+REF+'，不重新研究。'}]
        events=[e async for e in self.agent(Model(),SimpleNamespace(),['stock_report_read']).run(messages)]
        self.assertEqual(len(seen),1)
        self.assertFalse(any(e.kind=='tool_start' for e in events))
        self.assertIn('50076.412106',''.join(m.get('content','') for m in seen[0]))

    async def test_cloud_without_read_grant_gets_no_unit_view(self):
        seen=[]
        class Model:
            cloud=True
            async def chat_stream_with_tools(self,messages,*args,**kwargs):
                seen.append(copy.deepcopy(messages))
                yield '',{'role':'assistant','content':'未重新读取。'}
        agent=self.agent(Model(),SimpleNamespace(),['stock_report_read'])
        agent.workspace_cloud_allowed=False
        messages=history()+[{'role':'user','content':'解释报告'+REF}]
        _=[e async for e in agent.run(messages)]
        self.assertNotIn('unit_display',''.join(m.get('content','') for m in seen[0]))
