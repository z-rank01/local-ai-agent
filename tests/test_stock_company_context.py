import copy,json,unittest
import httpx
from plugins.finance.bridge import StockBridge
from plugins.finance.company_context import validate_context,context_index,current_guidance
from tests.test_stock_calculation_review import history
from tests.test_stock_bridge import make_broker,ok_step,REF

REV='c'*64
def context():
    subjects=[]
    for i,(name,code,value) in enumerate([('万德斯','688178','100'),('碧水源','300070','200')]):
        metrics=[dict(metric=m,label=m,status='SOURCE_BOUND',unit='CNY',value_yuan=value,raw_value=value+'.0000',
            report_period='20260630',field=m,location='来源/'+m,url='https://vip.stock.finance.sina.com.cn/x',
            kind='SOURCE_FIELD' if i==0 else 'PEER_SOURCE_FIELD',event_id='a'*16,read_cursor='20:'+'b'*16)
            for m in ('revenue','parent_profit','ocf')]
        subjects.append(dict(name=name,symbol=code,role='primary' if i==0 else 'comparison',report_period='20260630',metrics=metrics))
    return dict(version='comparison-source-context-v1',reader_revision=REV,comparable_period='YES',common_period='20260630',subjects=subjects)

def observation():
    return dict(kind='local_report_page',source_reference=REF,body='旧过程页',comparison_context=context(),
        answer_pages=[dict(kind='local_report_page',body='万德斯688178 碧水源300070 20260630 100 200',
            reader_revision=REV,requested_page=1,page_count=1)])

class ComparisonContextTests(unittest.IsolatedAsyncioTestCase):
    def test_identity_period_source_and_negative_values_validated(self):
        obs=observation();validate_context(obs['comparison_context'],obs['answer_pages'])
        self.assertIn('碧水源',current_guidance(context_index(history(obs)),REF,[]))
        row=obs['comparison_context']['subjects'][0]['metrics'][1]
        row.update(value_yuan='-100',raw_value='-100.000');obs['answer_pages'][0]['body']+=' -100'
        validate_context(obs['comparison_context'],obs['answer_pages'])

    def test_wrong_identity_period_number_and_locator_cannot_certify_context(self):
        for mutation in ('code','period','raw','url','revision','cursor','consolidated'):
            obs=observation();c=obs['comparison_context'];row=c['subjects'][1]['metrics'][1]
            if mutation=='code':c['subjects'][1]['symbol']='300072'
            elif mutation=='period':row['report_period']='20251231'
            elif mutation=='raw':row['raw_value']='201'
            elif mutation=='url':row['url']='http://localhost/private'
            elif mutation=='revision':c['reader_revision']='d'*64
            elif mutation=='cursor':row['read_cursor']='wrong'
            else:row['metric']='profit'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_context(c,obs['answer_pages'])

    def test_invalid_new_version_does_not_inherit_old_context_and_ref_cannot_cross(self):
        first=history(observation());bad=observation();bad['comparison_context']['reader_revision']='d'*64
        result=context_index(first+history(bad,call_id='new'))
        self.assertIsNone(result[REF]);self.assertIsNone(current_guidance(result,REF,[]))
        obs=observation();obs['source_reference']='d'*64
        self.assertEqual(context_index(history(obs)),{})

    async def test_bridge_gate_and_bad_optional_context_preserve_body(self):
        def bridge(value,allowed):
            client=httpx.AsyncClient(transport=httpx.MockTransport(make_broker([],step_responses=[ok_step(value)])))
            self.addAsyncCleanup(client.aclose)
            result=StockBridge('http://paper.test','test-token',client=client)
            result.set_turn('comparison','comparison-turn','读取报告',allow_report_body=allowed)
            return result
        for allow in (True,False):
            obs=observation();obs.pop('source_reference')
            broker=bridge(obs,allow)
            result=(await broker.call_tool('stock_report_read',{'reference':REF},'comparison'))['model_observation']
            self.assertEqual('comparison_context' in result,allow)
        obs=observation();obs['comparison_context']['subjects'][1]['symbol']='300072'
        broker=bridge(obs,True)
        result=(await broker.call_tool('stock_report_read',{'reference':REF},'comparison'))['model_observation']
        self.assertNotIn('comparison_context',result);self.assertIn('answer_pages',result)
