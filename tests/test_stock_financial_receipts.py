import copy
import unittest
from core.stock_financial_receipts import financial_fields_from_read

def observation(peer=True):
    symbol='300070' if peer else '688178'
    value='82386253507.240000' if peer else '2167009679.150000'
    row={'version':'financial-read-field-v1','symbol':symbol,'name':'碧水源' if peer else '万德斯',
        'financial_metric':'assets','field':'资产总计','report_period':'20260630','unit':'CNY','raw_value':value,
        'value_yuan':'82386253507.24' if peer else '2167009679.15',
        'value_wan_yuan':'8238625.350724' if peer else '216700.967915',
        'value_yi_yuan':'823.8625350724' if peer else '21.6700967915',
        'excerpt_id':symbol+':20260630:assets','kind':'PEER_SOURCE_FIELD' if peer else 'SOURCE_FIELD',
        'url':'https://vip.stock.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/'+symbol+'.phtml',
        'location':'资产负债表/20260630/资产总计'}
    text=f"同行{symbol} 资产总计：{value}；期间20260630；单位CNY" if peer else f"资产总计：{value}；期间 20260630；币种/单位 CNY；披露 未确认"
    span={'excerpt_id':row['excerpt_id'],'kind':row['kind'],'text':text,'url':row['url'],'location':row['location']}
    if peer:span['symbol']=symbol
    return {'kind':'public_evidence_read','symbol':'688178','event_id':'a'*16,'source_reference':'b'*64,
        'excerpts':[span],'next_cursor':'original-cursor','financial_fields':[row]}

class FinancialReadTests(unittest.TestCase):
    def test_exact_peer_and_main_read_keep_all_source_and_unit_bindings(self):
        for peer in (True,False):
            v=observation(peer);before=copy.deepcopy(v);rows=financial_fields_from_read(v)
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['value_yi_yuan'],v['financial_fields'][0]['value_yi_yuan'])
            self.assertEqual(v,before)

    def test_bad_optional_values_do_not_rewrite_source_or_cursor(self):
        for change in ({'value_yi_yuan':'82.39'},{'symbol':'300072'},{'report_period':'20251231'},
            {'financial_metric':'DCF'},{'url':'https://pdf.dfcfw.com/a.pdf'}, {'raw_value':'NaN'},
            {'unit':'USD'},{'published':'20260831'}):
            v=observation();v['financial_fields'][0].update(change);before=copy.deepcopy(v)
            self.assertEqual(financial_fields_from_read(v),[]);self.assertEqual(v,before)

    def test_stage_inference_or_offscreen_field_cannot_be_promoted(self):
        for kind in ('AI_COMPARABILITY_INFERENCE','ORIGINAL_TEXT'):
            v=observation();v['excerpts'][0]['kind']=kind;self.assertEqual(financial_fields_from_read(v),[])
        v=observation();v['excerpts']=[];self.assertEqual(financial_fields_from_read(v),[])

    def test_duplicates_or_conflicts_are_locally_omitted(self):
        v=observation();v['financial_fields'].append(copy.deepcopy(v['financial_fields'][0]))
        self.assertEqual(financial_fields_from_read(v),[])
        v=observation();v['excerpts'].append(copy.deepcopy(v['excerpts'][0]));self.assertEqual(financial_fields_from_read(v),[])

    def test_extra_fields_not_added_to_model_basis(self):
        v=observation();v['financial_fields'][0]['api_key']='private-secret'
        self.assertNotIn('private-secret',str(financial_fields_from_read(v)))

    def test_legacy_read_and_missing_fields_remain_readable(self):
        v=observation();v.pop('financial_fields');before=copy.deepcopy(v)
        self.assertEqual(financial_fields_from_read(v),[]);self.assertEqual(v,before)

class FinancialBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_bridge_annotations_keep_existing_gate_and_local_isolation(self):
        import httpx
        from core.stock_bridge import StockBridge
        from tests.test_stock_bridge import make_broker,ok_step
        for allowed,bad in ((True,False),(False,False),(True,True)):
            v=observation();v.pop('source_reference')
            if bad:v['financial_fields'][0]['value_yi_yuan']='82.39'
            before=copy.deepcopy(v)
            client=httpx.AsyncClient(transport=httpx.MockTransport(make_broker([],step_responses=[ok_step(v)])))
            self.addAsyncCleanup(client.aclose)
            bridge=StockBridge('http://paper.test','test-token',client=client)
            bridge.set_turn('financial-read','turn','读取已保存财务字段',allow_report_body=allowed)
            result=(await bridge.call_tool('stock_public_evidence_read',{'reference':'b'*64,'event_id':'a'*16},'financial-read'))['model_observation']
            self.assertEqual(result['excerpts'],before['excerpts']);self.assertEqual(result['next_cursor'],before['next_cursor'])
            self.assertEqual(result['source_reference'],'b'*64)
            if not allowed:self.assertNotIn('financial_fields',result)
            else:self.assertEqual(len(result['financial_fields']),0 if bad else 1)
