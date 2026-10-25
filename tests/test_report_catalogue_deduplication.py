import copy,json,unittest
from plugins.finance.report_context import report_material_view
from tests import test_stock_report_context as fixtures
from tests import test_financial_field_catalogue as catalogue_fixtures
from plugins.finance.source_quotes import original_records,source_task_index

class CatalogueViewTests(fixtures.ReportMaterialViewTests):
    def test_old_directory_copy_archived_latest_complete_directory_kept(self):
        context=catalogue_fixtures.context();context['subjects'][1]['field_catalogue']=catalogue_fixtures.directory()
        rows=fixtures.report(self.ref,'first','旧页','同版正文',comparison_context=context)+[{'role':'user','content':self.ref}]
        first,_=report_material_view(rows,self.archive)
        first+=fixtures.report(self.ref,'second','旧页','同版正文',comparison_context=context)
        before=copy.deepcopy(first)
        view,index=report_material_view(first,self.archive)
        obs=[json.loads(r['content'])['model_observation'] for r in view if r.get('tool_name')=='stock_report_read']
        self.assertNotIn('comparison_context',obs[0])
        self.assertEqual(obs[1]['comparison_context'],context)
        eid=obs[0]['legacy_material_evidence_id']
        archived=json.loads(self.saved[eid]['content'])['model_observation']
        self.assertEqual(archived['comparison_context'],context)
        self.assertEqual(first,before)
        self.assertEqual(source_task_index(first),source_task_index(view))
        self.assertEqual(original_records(first),original_records(view))
        repeated,_=report_material_view(view,self.archive);self.assertEqual(repeated,view)

    def test_old_already_indexed_without_pages_still_has_duplicate_removed(self):
        context=catalogue_fixtures.context();context['subjects'][1]['field_catalogue']=catalogue_fixtures.directory()
        rows=fixtures.report(self.ref,'first','旧页','同版正文',comparison_context=context)
        first,_=report_material_view(rows+[{'role':'user','content':self.ref}],self.archive)
        old=json.loads(first[1]['content']);old['model_observation'].pop('answer_pages')
        first[1]={**first[1],'content':json.dumps(old)}
        first+=fixtures.report(self.ref,'second','旧页','同版正文',comparison_context=context)
        view,_=report_material_view(first,self.archive)
        self.assertNotIn('comparison_context',json.loads(view[1]['content'])['model_observation'])
