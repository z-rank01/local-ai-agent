import copy,json,unittest
from plugins.finance.company_context import current_guidance,validate_context
from tests.test_stock_company_context import observation
from tests.test_financial_field_catalogue import directory
from tests.test_stock_bridge import REF

EID='e'*64

def material():
    obs=observation();obs['legacy_material_evidence_id']=EID
    obs['comparison_context']['subjects'][1]['field_catalogue']=directory()
    validate_context(obs['comparison_context'],obs['answer_pages'])
    rows=[{'role':'assistant','tool_calls':[{'id':'r','function':{'name':'stock_report_read','arguments':{'reference':REF}}}]},
          {'role':'tool','tool_name':'stock_report_read','tool_call_id':'r','content':json.dumps(obs,ensure_ascii=False)}]
    return obs['comparison_context'],rows


class VisibleCatalogueGuidanceTests(unittest.TestCase):
    def test_exact_registered_full_receipt_not_copied_into_guidance(self):
        context,rows=material();before=copy.deepcopy((context,rows))
        g=current_guidance({REF:context},REF,[],visible_messages=rows,evidence_ids={EID})
        subjects=json.loads(g.split('\n')[1])[0]['subjects']
        self.assertNotIn('field_catalogue',subjects[1])
        self.assertEqual(subjects[1]['field_catalogue_index']['evidence_id'],EID)
        self.assertEqual(subjects[1]['field_catalogue_index']['field_count'],1)
        self.assertEqual((context,rows),before)
        self.assertEqual(json.loads(rows[1]['content'])['comparison_context']['subjects'][1]['field_catalogue']['fields'],context['subjects'][1]['field_catalogue']['fields'])

    def test_no_registered_reader_or_visible_copy_retains_full_directory(self):
        context,rows=material()
        for visible,registered in ((rows,set()),([], {EID}), (None,None)):
            g=current_guidance({REF:context},REF,[],visible_messages=visible,evidence_ids=registered)
            self.assertIn('financial_metric',g);self.assertNotIn('visible-catalogue-index-v1',g)

    def test_wrong_ref_revision_pair_or_directory_retains_full(self):
        for mode in ('reference','revision','unpaired','field','error'):
            context,rows=material();value=json.loads(rows[1]['content'])
            if mode=='reference':value['source_reference']='d'*64
            if mode=='revision':value['comparison_context']['reader_revision']='d'*64
            if mode=='unpaired':rows[1]['tool_call_id']='wrong'
            if mode=='field':value['comparison_context']['subjects'][1]['field_catalogue']['fields'][0]['read_cursor']='2:'+'c'*16
            if mode=='error':value['error']='RULE_BLOCKED'
            rows[1]['content']=json.dumps(value)
            with self.subTest(mode=mode):
                g=current_guidance({REF:context},REF,[],visible_messages=rows,evidence_ids={EID})
                self.assertIn('financial_metric',g);self.assertNotIn('visible-catalogue-index-v1',g)
