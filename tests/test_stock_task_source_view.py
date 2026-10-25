import copy,hashlib,json,unittest
from plugins.finance.task_source_view import current_task_source_view,VERSION
from plugins.finance.source_quotes import original_records
from plugins.finance.source_coverage import coverage_index
from tests import test_stock_source_coverage as fixtures

REF=fixtures.REF;OLD='c'*64


def receipt(ref,call='current',kind='public_evidence_read'):
    tool='stock_public_evidence_read' if kind=='public_evidence_read' else 'stock_public_evidence_find'
    value=fixtures.page((1,2),ref=ref)
    if kind=='public_evidence_find':value={'kind':kind,'source_reference':ref,'items':[]}
    return [{'role':'assistant','content':'','tool_calls':[{'id':call,'function':{'name':tool,'arguments':{'reference':ref}}}]},
            {'role':'tool','tool_name':tool,'tool_call_id':call,'content':json.dumps({'model_observation':value},ensure_ascii=False)}]


class CurrentTaskSourceViewTests(unittest.TestCase):
    def setUp(self):self.saved={}
    def archive(self,row):
        eid=hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.saved[eid]=copy.deepcopy(row);return eid

    def test_explicit_current_task_keeps_originals_other_task_losslessly_readable(self):
        rows=[{'role':'system','content':'完整方法、权限和反证要求'}]+receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':REF}]
        before=copy.deepcopy(rows);view,index=current_task_source_view(rows,self.archive)
        self.assertEqual(rows,before);self.assertEqual(view[0],rows[0]);self.assertEqual(view[-3:],rows[-3:])
        self.assertEqual(view[1],rows[1]) # Tool arguments remain unchanged.
        self.assertEqual(len(index),1)
        pointer=json.loads(view[2]['content'])['model_observation']
        self.assertEqual(pointer['source_reference'],OLD);self.assertEqual(pointer['view_version'],VERSION)
        self.assertNotIn('excerpts',pointer);self.assertNotIn('next_cursor',pointer)
        archived=self.saved[index[0]['evidence_id']]
        self.assertEqual(archived['content'],rows[2]['content'])
        self.assertEqual(original_records(view),original_records(rows[-3:]))
        recovered={'role':'tool','tool_name':'context_evidence_read','content':json.dumps({
            'metadata':{'role':'tool','tool_name':'stock_public_evidence_read'},'evidence_id':index[0]['evidence_id'],
            'cursor':0,'total_chars':len(archived['content']),'content':archived['content']})}
        self.assertEqual(original_records([recovered]),original_records(rows[1:3]))
        self.assertEqual(coverage_index(original_records(view),{REF}),coverage_index(original_records(rows),{REF}))

    def test_no_explicit_ref_unknown_ref_or_no_archive_keeps_all_sources(self):
        for question in ('比较两个版本', 'd'*64,REF+' '+'d'*64):
            rows=receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':question}]
            self.assertEqual(current_task_source_view(rows,self.archive),(rows,[]))
        rows=receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':REF}]
        self.assertEqual(current_task_source_view(rows,None),(rows,[]));self.assertFalse(self.saved)

    def test_multiple_explicit_refs_preserve_both_and_current_turn_tools(self):
        rows=receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':REF+' '+OLD}]
        self.assertEqual(current_task_source_view(rows,self.archive),(rows,[]))
        rows[-1]['content']=REF
        fresh=receipt(OLD,'fresh');rows+=fresh
        view,index=current_task_source_view(rows,self.archive)
        self.assertEqual(view[-2:],fresh);self.assertEqual(len(index),1)

    def test_failed_unpaired_and_reference_mismatch_not_archived(self):
        for variant in ('failed','unpaired','mismatch','wrong_kind'):
            other=receipt(OLD,'old');v=json.loads(other[1]['content'])
            if variant=='failed':v['error']='RULE_BLOCKED'
            elif variant=='unpaired':other[1]['tool_call_id']='unpaired'
            elif variant=='mismatch':v['model_observation']['source_reference']='d'*64
            else:v['model_observation']['kind']='public_evidence_find'
            other[1]['content']=json.dumps(v)
            rows=other+receipt(REF)+[{'role':'user','content':REF}]
            self.assertEqual(current_task_source_view(rows,self.archive),(rows,[]))
        self.assertFalse(self.saved)

    def test_other_find_archived_and_repeat_projection_stable(self):
        rows=receipt(OLD,'find','public_evidence_find')+receipt(REF)+[{'role':'user','content':REF}]
        view,index=current_task_source_view(rows,self.archive)
        self.assertEqual(len(index),1)
        self.assertEqual(current_task_source_view(view,self.archive),(view,[]))

    def test_archive_failure_does_not_mutate_original_history(self):
        rows=receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':REF}];before=copy.deepcopy(rows)
        def fail(row):raise OSError('evidence unavailable')
        with self.assertRaises(OSError):current_task_source_view(rows,fail)
        self.assertEqual(rows,before)


class CurrentTaskSourceLoopTests(unittest.IsolatedAsyncioTestCase):
    agent=fixtures.SourceCoverageLoopTests.agent
    async def test_actual_agent_view_indexes_other_task_and_registers_recovery(self):
        captured=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kwargs):
                captured.append(copy.deepcopy(messages))
                yield '',{'role':'assistant','content':'当前任务保存范围已核对；研究仍部分完成。'}
        class Router:pass
        agent=self.agent(Model(),Router(),[])
        saved={}
        def archive(row):
            eid=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest();saved[eid]=copy.deepcopy(row);return eid
        agent.context_mgr.archive=archive;agent.context_mgr.reader=lambda *a,**kw:None
        agent.context_mgr.evidence_ids=set()
        rows=receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':REF}];before=copy.deepcopy(rows)
        events=[e async for e in agent.run(rows)]
        self.assertTrue(any(e.kind=='done' for e in events));self.assertEqual(rows,before)
        pointers=[json.loads(m['content']).get('model_observation',{}) for m in captured[0] if m.get('role')=='tool']
        self.assertTrue(any(p.get('view_version')==VERSION for p in pointers))
        self.assertTrue(any(eid in agent.context_mgr.evidence_ids for eid in saved))

    async def test_agent_without_cloud_read_grant_or_recovery_keeps_receipts(self):
        for granted,recovery in ((False,True),(True,False)):
            captured=[];archived=[]
            class Model:
                cloud=True
                async def chat_stream_with_tools(self,messages,tools,**kwargs):
                    captured.append(copy.deepcopy(messages))
                    yield '',{'role':'assistant','content':'当前范围仍需核对。'}
            class Router:pass
            agent=self.agent(Model(),Router(),[])
            agent.workspace_cloud_allowed=granted
            agent.context_mgr.archive=lambda row:archived.append(row) or 'e'*64
            agent.context_mgr.reader=(lambda *a,**kw:None) if recovery else None
            agent.context_mgr.evidence_ids=set()
            rows=receipt(OLD,'old')+receipt(REF)+[{'role':'user','content':REF}]
            events=[e async for e in agent.run(rows)]
            self.assertTrue(any(e.kind=='done' for e in events))
            self.assertFalse(archived)
            self.assertIn(rows[1],captured[0])
