import copy,json,unittest
from types import SimpleNamespace
from plugins.finance.source_coverage import (canonical_span,snapshot_hash,validated_source_coverage,
                                       coverage_index,coverage_guidance,merge_read_records)
from plugins.finance.source_quotes import complete_sources
from tests import test_stock_source_quotes as quote_tests

REF='a'*64;EVENT='b'*16;URL='https://pdf.dfcfw.com/a.pdf'
def span(i,text=None,url=URL):
    return {'excerpt_id':str(i),'text':text or '来源段落'+str(i),'kind':'ORIGINAL_TEXT','url':url,'location':'位置'+str(i)}
def page(ids=(1,),all_ids=(1,2),ref=REF,event=EVENT):
    all_rows=[canonical_span(span(i)) for i in all_ids]
    return {'kind':'public_evidence_read','source_reference':ref,'event_id':event,
            'excerpts':[span(i) for i in ids],'total_excerpts':3529,'next_cursor':'legacy',
            'source_coverage':[{'version':'saved-source-coverage-v1','url':URL,'snapshot_hash':snapshot_hash(all_rows),
                                'saved_excerpts':len(all_rows),'saved_chars':sum(len(s['text']) for s in all_rows)}]}


class SourceCoverageTests(unittest.TestCase):
    def test_legacy_reads_bind_only_with_full_same_task_snapshot_fingerprint(self):
        old=page((1,2));old.pop('source_coverage');new=page((1,))
        original=copy.deepcopy([old,new]);row=coverage_index([old,new],{REF})[0]
        self.assertEqual(row['state'],'ALL_SAVED_EXCERPTS_READ')
        self.assertEqual(row['legacy_read_binding'],'FULL_SNAPSHOT_MATCH')
        self.assertEqual(row['read_excerpts'],2);self.assertEqual([old,new],original)
        for change in ('reference','event','text','incomplete','version','conflict'):
            old=page((1,2));old.pop('source_coverage');new=page((1,));extra=[]
            if change=='reference':old['source_reference']='c'*64
            elif change=='event':old['event_id']='c'*16
            elif change=='text':old['excerpts'][1]['text']='另一快照的反证'
            elif change=='incomplete':old['excerpts']=old['excerpts'][:1]
            elif change=='version':old['source_coverage']=page()['source_coverage'];old['source_coverage'][0]['snapshot_hash']='e'*64
            else:
                other=copy.deepcopy(old);other['excerpts'][1]['text']='冲突';extra=[other]
            row=coverage_index([old]+extra+[new],{REF})[0]
            self.assertEqual(row['read_excerpts'],1,change)
            self.assertNotEqual(row['state'],'ALL_SAVED_EXCERPTS_READ',change)

    def test_file_coverage_separate_from_aggregate_eof_and_not_website_complete(self):
        rows=[page((1,)),page((2,))];before=copy.deepcopy(rows)
        result=coverage_index(rows,{REF})
        self.assertEqual(result[0]['state'],'ALL_SAVED_EXCERPTS_READ')
        self.assertEqual(result[0]['saved_excerpts'],2)
        self.assertEqual(complete_sources(rows),[])  # Does not relax the old absence proof.
        self.assertEqual(rows,before)
        self.assertIn('不是网站或PDF完整采集证明',coverage_guidance(rows,REF,[]))

    def test_repeated_pages_or_cross_reference_cannot_supply_missing_counterevidence(self):
        rows=[page((1,)),page((1,)),page((2,),ref='c'*64)]
        result=coverage_index(rows,{REF})
        self.assertEqual(result[0]['state'],'PARTIAL_SAVED_EXCERPTS_READ')
        self.assertEqual(result[0]['read_excerpts'],1)
        self.assertIsNone(coverage_guidance(rows,'d'*64,[]))

    def test_source_version_changes_do_not_merge_old_fragments(self):
        old=page((1,));new=page((2,));new['source_coverage'][0]['snapshot_hash']='e'*64
        result=coverage_index([old,new],{REF})
        self.assertEqual(result[0]['state'],'PARTIAL_SAVED_EXCERPTS_READ')
        self.assertEqual(result[0]['read_excerpts'],1)

    def test_fingerprint_conflict_never_certifies_complete(self):
        wrong=page((1,2));wrong['excerpts'][1]['text']='修订但未重新绑定'
        # Increase declared size to exercise fingerprint checking separately.
        wrong['source_coverage'][0]['saved_chars']=sum(len(s['text']) for s in wrong['excerpts'])
        result=coverage_index([wrong],{REF})
        self.assertEqual(result[0]['state'],'SNAPSHOT_MISMATCH')

    def test_bad_counts_url_kind_or_duplicate_scope_is_locally_omitted(self):
        for change in ({'saved_excerpts':0},{'saved_chars':False},{'snapshot_hash':'bad'},
                       {'url':'https://127.0.0.1/private'}):
            value=page();value['source_coverage'][0].update(change);before=copy.deepcopy(value)
            self.assertEqual(validated_source_coverage(value),[]);self.assertEqual(value,before)
        value=page();value['excerpts'][0]['kind']='AI_COMPARABILITY_INFERENCE'
        self.assertEqual(validated_source_coverage(value),[])
        value=page();value['excerpts'][0]['text']='  '
        self.assertEqual(validated_source_coverage(value),[])
        value=page();value['source_coverage']*=2;self.assertEqual(validated_source_coverage(value),[])

    def test_identical_record_merge_preserves_distinct_snapshots_and_originals(self):
        a=page();b=copy.deepcopy(a);b['source_coverage'][0]['snapshot_hash']='f'*64
        before=copy.deepcopy([a,b]);result=merge_read_records([a],[a,b])
        self.assertEqual(len(result),2);self.assertEqual([a,b],before)

    def test_legacy_absence_of_metadata_does_not_invent_missing_data(self):
        value=page();value.pop('source_coverage')
        self.assertEqual(validated_source_coverage(value),[])
        self.assertEqual(coverage_index([value],{REF}),[])


class SourceCoverageLoopTests(unittest.IsolatedAsyncioTestCase):
    agent=quote_tests.SourceQuoteLoopTests.agent
    async def test_actual_read_scope_enters_next_model_step_without_waiting_for_repair(self):
        seen=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kwargs):
                seen.append(copy.deepcopy(messages))
                if len(seen)==1:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'scope-read','function':{
                        'name':'stock_public_evidence_read','arguments':{'reference':REF,'event_id':EVENT}}}]}
                else:
                    yield '',{'role':'assistant','content':'已有公告显示合同尚未签订，不据此判断法律效力。'}
        class Router:
            async def dispatch(self,*args):return page((1,2))
        history=[{'role':'user','content':'只核对已有任务'+REF+'，不新建研究。'}]
        events=[e async for e in self.agent(Model(),Router(),['stock_public_evidence_read']).run(history)]
        self.assertEqual(len(seen),2)
        self.assertIn('ALL_SAVED_EXCERPTS_READ',''.join(m.get('content','') for m in seen[1]))
        self.assertEqual(sum(e.kind=='tool_start' for e in events),1)
        self.assertFalse(any(e.kind=='quality_rejected' for e in events))

    async def test_bridge_keeps_original_page_cursor_and_current_authorized_reference(self):
        import httpx
        from plugins.finance.bridge import StockBridge
        from tests.test_stock_bridge import make_broker,ok_step
        value=page();value.pop('source_reference');before=copy.deepcopy(value)
        client=httpx.AsyncClient(transport=httpx.MockTransport(make_broker([],step_responses=[ok_step(value)])))
        self.addAsyncCleanup(client.aclose)
        bridge=StockBridge('http://paper.test','token',client=client)
        bridge.set_turn('scope','turn','只读已保存原文',allow_report_body=True)
        result=(await bridge.call_tool('stock_public_evidence_read',{'reference':REF,'event_id':EVENT},'scope'))['model_observation']
        self.assertEqual(result['excerpts'],before['excerpts']);self.assertEqual(result['next_cursor'],before['next_cursor'])
        self.assertEqual(result['source_reference'],REF);self.assertEqual(result['source_coverage'],before['source_coverage'])
