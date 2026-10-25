import json,copy,unittest
from plugins.finance.company_context import validate_context,current_guidance
from tests.test_stock_company_context import context,observation,REV

def directory():
    return {'version':'saved-financial-fields-v1','unbound_count':0,'fields':[dict(financial_metric='assets',
        report_period='20260630',field='资产总计',unit='CNY',kind='PEER_SOURCE_FIELD',
        excerpt_id='300070:20260630:assets',event_id='a'*16,read_cursor='7662:'+'b'*16,
        url='https://vip.stock.finance.sina.com.cn/x',location='资产负债表/20260630/资产总计')]}

class CatalogueTests(unittest.TestCase):
    def test_directory_does_not_claim_read_and_preserves_historical_read_receipts(self):
        c=context();c['subjects'][1]['field_catalogue']=directory();before=copy.deepcopy(c)
        pages=observation()['answer_pages'];validate_context(c,pages)
        ref='a'*64
        g=current_guidance({ref:c},ref,[]);self.assertIn('NO_MATCH_IN_AVAILABLE_RECEIPTS',g)
        entry=c['subjects'][1]['field_catalogue']['fields'][0]
        read={'source_reference':ref,'event_id':entry['event_id'],'excerpts':[{**entry,'text':'真实来源原值'}]}
        g=current_guidance({ref:c},ref,[],[read]);self.assertIn('HISTORICAL_READ_RECEIPT_PRESENT',g)
        self.assertNotIn('receipt_status',c['subjects'][1]['field_catalogue']['fields'][0])
        self.assertIn('assets',g);self.assertIn('metrics只列',g)
        other=copy.deepcopy(read);other['source_reference']='d'*64
        self.assertIn('NO_MATCH_IN_AVAILABLE_RECEIPTS',current_guidance({ref:c},ref,[],[other]))

    def test_bad_optional_directory_leaves_existing_core_metrics(self):
        c=context();c['subjects'][1]['field_catalogue']={'version':'unknown'}
        validate_context(c,observation()['answer_pages'])
        self.assertNotIn('field_catalogue',c['subjects'][1]);self.assertEqual(len(c['subjects'][1]['metrics']),3)

    def test_invalid_catalogue_entry_is_not_source_existence_or_new_permission(self):
        for change in ({'url':'http://localhost/private'},{'kind':'SOURCE_FIELD'},{'report_period':'20260631'},
                       {'read_cursor':'invented'},{'financial_metric':'DCF'}):
            c=context();cat=directory();cat['fields'][0].update(change);c['subjects'][1]['field_catalogue']=cat
            validate_context(c,observation()['answer_pages'])
            self.assertEqual(c['subjects'][1]['field_catalogue']['fields'],[])
            self.assertEqual(c['subjects'][1]['field_catalogue']['unbound_count'],1)
