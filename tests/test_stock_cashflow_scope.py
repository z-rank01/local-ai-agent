import copy
import unittest
from decimal import Decimal
from core.stock_company_context import validate_context, CASHFLOW_SCOPE_READING, context_index, current_cashflow_scope_version
from tests.test_stock_company_context import observation
from tests.test_stock_bridge import make_broker, ok_step, REF
from core.stock_bridge import StockBridge
import httpx


def scoped_observation(net='100'):
    obs=observation();s=obs['comparison_context']['subjects'][0];m=s['metrics'][2]
    m.update(value_yuan=net,raw_value=net)
    obs['answer_pages'][0]['body']+=' '+net
    n=Decimal(net);incoming=max(n,Decimal(0));outgoing=max(-n,Decimal(0))
    m['cashflow_net_scope']={'version':'cashflow-net-scope-v1','scenario_kind':'ARITHMETIC_HYPOTHESIS',
        'symbol':s['symbol'],'report_period':m['report_period'],'unit':'CNY','net_yuan':net,
        'examples':[{'inflow_yuan':str(incoming+d),'outflow_yuan':str(outgoing+d),'net_yuan':net}
                    for d in (Decimal('100000000'),Decimal('200000000'))], 'reading':CASHFLOW_SCOPE_READING}
    return obs


class CashflowScopeTests(unittest.TestCase):
    def test_scope_observation_only_for_selected_valid_receipt(self):
        from tests.test_stock_calculation_review import history
        index=context_index(history(scoped_observation()))
        self.assertEqual(current_cashflow_scope_version(index,REF,[]),'cashflow-net-scope-v1')
        self.assertIsNone(current_cashflow_scope_version(index,'d'*64,[]))
        self.assertIsNone(current_cashflow_scope_version(context_index(history(observation())),REF,[]))
    def test_bound_positive_negative_zero_hypotheses_keep_original_metrics(self):
        for net in ('100','-100','0'):
            obs=scoped_observation(net);before=copy.deepcopy(obs)
            validate_context(obs['comparison_context'],obs['answer_pages'])
            self.assertEqual(obs,before)

    def test_bad_addition_is_locally_removed_without_discarding_base(self):
        for mode in ('symbol','period','actual','unit','net','equation','negative','reading','missing','wrong_metric'):
            with self.subTest(mode=mode):
                obs=scoped_observation();metric=obs['comparison_context']['subjects'][0]['metrics'][2]
                scope=metric['cashflow_net_scope']
                if mode=='symbol':scope['symbol']='300070'
                if mode=='period':scope['report_period']='20251231'
                if mode=='actual':scope['scenario_kind']='ACTUAL_CASH_COMPONENTS'
                if mode=='unit':scope['unit']='USD'
                if mode=='net':scope['net_yuan']='99'
                if mode=='equation':scope['examples'][0]['outflow_yuan']='99'
                if mode=='negative':scope['examples'][0].update(inflow_yuan='-1',outflow_yuan='-101')
                if mode=='reading':scope['reading']='实际客户回款质量已确认'
                if mode=='missing':scope['examples'][0].pop('inflow_yuan')
                if mode=='wrong_metric':
                    target=obs['comparison_context']['subjects'][0]['metrics'][0]
                    target['cashflow_net_scope']=metric.pop('cashflow_net_scope');metric=target
                validate_context(obs['comparison_context'],obs['answer_pages'])
                self.assertNotIn('cashflow_net_scope',metric)
                self.assertEqual(metric['status'],'SOURCE_BOUND');self.assertEqual(metric['value_yuan'],'100')

    def test_unknown_private_fields_are_not_preserved_in_optional_projection(self):
        obs=scoped_observation();scope=obs['comparison_context']['subjects'][0]['metrics'][2]['cashflow_net_scope']
        scope['account']='private';scope['examples'][0]['customer']='private'
        validate_context(obs['comparison_context'],obs['answer_pages'])
        safe=obs['comparison_context']['subjects'][0]['metrics'][2]['cashflow_net_scope']
        self.assertNotIn('account',safe);self.assertNotIn('customer',safe['examples'][0])


class CashflowBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_bridge_branch_obeys_cloud_read_gate(self):
        for allow in (False,True):
            obs=scoped_observation();obs.pop('source_reference')
            client=httpx.AsyncClient(transport=httpx.MockTransport(make_broker([],step_responses=[ok_step(obs)])))
            self.addAsyncCleanup(client.aclose)
            bridge=StockBridge('http://paper.test','test-token',client=client)
            bridge.set_turn('comparison','cash-scope-turn','核对净额含义',allow_report_body=allow)
            value=(await bridge.call_tool('stock_report_read',{'reference':REF},'comparison'))['model_observation']
            self.assertEqual('comparison_context' in value,allow)
            if allow:self.assertEqual(value['comparison_context']['subjects'][0]['metrics'][2]['cashflow_net_scope']['scenario_kind'],'ARITHMETIC_HYPOTHESIS')
