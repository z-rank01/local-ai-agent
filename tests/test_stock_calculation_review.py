import copy
import json
import unittest
from decimal import Decimal, localcontext
from types import SimpleNamespace

from core.stock_calculation_review import validate_calculations, calculation_index, repair_scale_divisions
from tests import test_stock_bridge as bridge_tests, test_stock_source_quotes as quote_tests
make_broker, ok_step, REF = bridge_tests.make_broker, bridge_tests.ok_step, bridge_tests.REF

REV = 'c' * 64

def receipt():
    with localcontext() as ctx:
        ctx.prec = 28
        ratio = Decimal('114001200') / Decimal('500764121.06')
        return dict(kind='EVENT_SCALE', version='event-scale-calculation-v1', symbol='688178',
                    annual_period='20251231', amount_yuan='114001200', annual_revenue_yuan='500764121.06',
                    ratio=str(ratio), percent=str(ratio * 100), reader_revision=REV)

def observation():
    return dict(kind='local_report_page', source_reference=REF, body='旧过程页',
                answer_pages=[dict(kind='local_report_page', reader_revision=REV, requested_page=1,
                                   page_count=1, body='114001200元 / 500764121.06元，20251231，22.77%')],
                program_calculations=[receipt()])

def history(value=None, call_id='report'):
    return [dict(role='assistant', tool_calls=[dict(id=call_id, function=dict(name='stock_report_read',
                arguments=json.dumps(dict(reference=REF))))]),
            dict(role='tool', tool_name='stock_report_read', tool_call_id=call_id,
                 content=json.dumps(value or observation()))]

class ArithmeticTests(unittest.TestCase):
    def test_invalid_calculation_cannot_authorise_correction(self):
        for key, value in [('ratio', '2.28'), ('percent', '22.77'), ('amount_yuan', 'NaN'),
                           ('annual_period', '20260630'), ('reader_revision', 'bad'), ('symbol', 'other')]:
            row=receipt(); row[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_calculations([row])

    def test_current_paired_revision_only_and_missing_new_receipt_clears_old(self):
        original=history(); before=copy.deepcopy(original)
        self.assertEqual(len(calculation_index(original)[REF]),1)
        self.assertEqual(original,before)
        for value in [dict(observation(), program_calculations=[]),
                      dict(observation(), answer_pages=[dict(observation()['answer_pages'][0], reader_revision='d'*64)]),
                      dict(observation(), program_calculations=[dict(receipt(), ratio='2.28')])]:
            self.assertEqual(calculation_index(original+history(value,'new'))[REF],[])
        forged=history(); forged[1]['tool_call_id']='unknown'
        self.assertFalse(calculation_index(forged))
        mismatch=history(dict(observation(),source_reference='d'*64))
        self.assertFalse(calculation_index(mismatch))

    def test_failed_read_does_not_replace_latest_success(self):
        self.assertEqual(len(calculation_index(history()+history(dict(observation(),error='FAILED'),'bad'))[REF]),1)

    def test_source_matched_units_correct_without_forecasting(self):
        rows=calculation_index(history())[REF]
        for text, expected in [('114001200元 / 500764121.06元 ≈ **2.28**倍','**0.23**倍'),
                               ('11,400.12万元 ÷ 50,076.41万元 = 228%','22.77%')]:
            result, edits=repair_scale_divisions(text,rows)
            self.assertIn(expected,result); self.assertEqual(len(edits),1)

    def test_correct_rounding_and_unbound_denominator_not_changed(self):
        rows=calculation_index(history())[REF]
        for text in ['114001200元 / 500764121.06元 ≈ 0.23倍',
                     '114001200元 / 500764121.06元 ≈ 23%',
                     '114001200元 / 70000000元 ≈ 2.28倍',
                     '114001200万元 / 500764121.06元 ≈ 2.28倍']:
            self.assertEqual(repair_scale_divisions(text,rows),(text,[]))
        self.assertEqual(repair_scale_divisions('1元 / 2元 = 5倍',[]),('1元 / 2元 = 5倍',[]))

    def test_dependent_false_assessment_isolated_and_other_reasoning_kept(self):
        text=('114001200元 / 500764121.06元 ≈ 2.28倍\n\n'
              '项目总额为年度营收2.28倍，因此经营规模很大。\n\n'
              '履约与回款仍待核。利润同比2.28倍尚无依据。\n\n项目营收规模不是2.28倍。')
        result, edits=repair_scale_divisions(text,calculation_index(history())[REF])
        self.assertEqual(len(edits),2); self.assertNotIn('因此经营规模很大',result)
        self.assertIn('利润同比2.28倍尚无依据',result)
        self.assertIn('不是2.28倍',result)
        self.assertIn('不代表当年新增收入',result)

class CalculationBridgeTests(unittest.IsolatedAsyncioTestCase):
    bridge = bridge_tests.BridgeTests.bridge
    async def test_calc_and_body_share_grant_and_version(self):
        for allowed in (True,False):
            bridge=self.bridge(make_broker([],step_responses=[ok_step(observation())]))
            bridge.set_turn('calc','calc-turn','读取已保存报告',allow_report_body=allowed)
            value=(await bridge.call_tool('stock_report_read',{'reference':REF},'calc'))['model_observation']
            self.assertEqual('program_calculations' in value,allowed)
            if allowed:self.assertEqual(value['program_calculations'],[receipt()])
            self.assertEqual(value['body'],'旧过程页')

    async def test_bad_optional_math_preserves_valid_pages(self):
        for change in [dict(ratio='2.28'),dict(reader_revision='d'*64),dict(annual_period='20241231')]:
            value=observation();value['program_calculations']=[dict(receipt(),**change)]
            bridge=self.bridge(make_broker([],step_responses=[ok_step(value)]))
            bridge.set_turn('bad','bad-turn','读取报告',allow_report_body=True)
            result=(await bridge.call_tool('stock_report_read',{'reference':REF},'bad'))['model_observation']
            self.assertNotIn('program_calculations',result)
            self.assertEqual(result['answer_pages'],value['answer_pages'])

class CalculationLoopTests(unittest.IsolatedAsyncioTestCase):
    agent = quote_tests.SourceQuoteLoopTests.agent
    async def test_arithmetic_corrected_in_visible_and_final_text_without_more_calls(self):
        calls=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,*args):
                calls.append(1)
                text='114001200元 / 500764121.06元 ≈ 2.28倍\n\n合同总额为营收2.28倍，规模很大。\n\n履约及利润仍待核。'
                yield text[:25],None; yield text[25:],None
                yield '',dict(role='assistant',content=text)
        messages=history()+[dict(role='user',content='请解释报告'+REF+'，不创建或恢复研究。')]
        events=[e async for e in self.agent(Model(),SimpleNamespace(),['stock_report_read']).run(messages)]
        self.assertEqual(len(calls),1)
        self.assertFalse(any(e.kind=='tool_start' for e in events))
        self.assertEqual(''.join(e.text for e in events if e.kind=='token'),events[-1].text)
        self.assertIn('0.23倍',events[-1].text);self.assertNotIn('2.28倍',events[-1].text)
        repaired=[e for e in events if e.kind=='quality_repaired']
        self.assertEqual(len(repaired),1);self.assertIn('2.28倍',repaired[0].data['original_answer'])

    async def test_other_task_receipt_cannot_correct_current_task(self):
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,*args):
                text='114001200元 / 500764121.06元 ≈ 2.28倍'
                yield text,None;yield '',dict(role='assistant',content=text)
        events=[e async for e in self.agent(Model(),SimpleNamespace(),['stock_report_read']).run(
            history()+[dict(role='user',content='解释另一份报告'+'d'*64)])]
        self.assertFalse(any(e.kind=='quality_repaired' for e in events))
        self.assertIn('2.28倍',events[-1].text)
