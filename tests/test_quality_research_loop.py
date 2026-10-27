import unittest
from types import SimpleNamespace
from unittest.mock import patch
from core.agent import Agent
from core import config
from core.tool_registry import ToolRegistry


def research_registry(**kwargs):
    installed = ToolRegistry(config.TOOLS_DIR, enabled_backends=('skill-websearch', 'stock-bridge'))
    kwargs.setdefault('execution_policy', installed.execution_policy)
    kwargs.setdefault('tools_for_role', installed.tools_for_role)
    kwargs.setdefault('history_projectors', installed.history_projectors)
    kwargs.setdefault('phase_guidance', installed.phase_guidance)
    kwargs.setdefault('create_turn', installed.create_turn)
    return SimpleNamespace(**kwargs)


class ResearchLoopTests(unittest.IsolatedAsyncioTestCase):
    async def delivery_context(self, expired):
        captured, calls = [], []
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                captured.append(([dict(m) for m in messages], tools))
                if len(captured) == 1:
                    yield '', {'role': 'assistant', 'content': '', 'tool_calls': [{
                        'id': 'read', 'function': {'name': 'stock_report_read', 'arguments': {'reference': 'a'*64}}}]}
                else:
                    yield '', {'role': 'assistant', 'content': '已提供证据仅支持部分判断，尚未综合。'}
        class Router:
            async def dispatch(self, name, params, session):
                calls.append(name)
                return {'status': 'TIME_LIMIT'} if expired else {'body': '公告披露中标，但尚未签约。'}
        async def process(messages): return messages
        agent = Agent(llm=Model(), router=Router(), registry=research_registry(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':'stock_report_read'}}]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=5)
        agent.quality_first_research=agent.turn_loop_enabled=True
        query='只读已保存研究，不提交或恢复任务。'
        events=[e async for e in agent.run([{'role':'user','content':query}])]
        return captured,calls,events,query

    async def test_stock_read_delivery_rules_reach_actual_next_model_context(self):
        from plugins.finance.turn import DELIVERY_GUIDANCE as STOCK_DELIVERY_GUIDANCE
        captured,calls,events,query=await self.delivery_context(False)
        messages,tools=captured[-1]
        self.assertEqual(calls,['stock_report_read'])
        self.assertEqual([m['content'] for m in messages if m['role']=='user'],[query])
        self.assertEqual(sum(m.get('content')==STOCK_DELIVERY_GUIDANCE for m in messages),1)
        self.assertIn('analysis', next(m['content'] for m in messages if m.get('content') == STOCK_DELIVERY_GUIDANCE))
        self.assertEqual(events[-1].kind,'done')

    async def test_expired_read_only_window_does_not_demand_ids_or_invent_running_task(self):
        captured,calls,events,query=await self.delivery_context(True)
        messages,tools=captured[-1]
        self.assertIsNone(tools)
        self.assertEqual(calls,['stock_report_read'])
        self.assertEqual([m['content'] for m in messages if m['role']=='user'],[query])
        closing=next(m for m in messages if '本轮股票交付窗口已到' in m.get('content',''))
        self.assertEqual(closing['role'],'system')
        self.assertNotIn('请给出已提交后台任务的 ID',closing['content'])
        self.assertNotIn('后台任务仍按检查点运行',closing['content'])
        self.assertIn('已暂停',closing['content'])
        self.assertEqual(events[-1].kind,'done')

    async def test_saved_authorized_report_gets_delivery_rules_without_repeated_read(self):
        from plugins.finance.turn import DELIVERY_GUIDANCE as STOCK_DELIVERY_GUIDANCE
        captured=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                captured.extend(dict(m) for m in messages)
                yield '',{'role':'assistant','content':'根据已读取材料，目前只能确认政策线索。'}
        class Router:
            async def dispatch(self,*args):raise AssertionError('不要重复已成功读取')
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=Router(),registry=research_registry(get_definitions=lambda **kw:[]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=2)
        history=[{'role':'assistant','content':'','tool_calls':[{'id':'prior','function':{'name':'stock_report_read','arguments':{}}}]},
            {'role':'tool','tool_name':'stock_report_read','tool_call_id':'prior','content':'已授权政策原文'},
            {'role':'user','content':'基于已读取材料回答，不重复工具读取。'}]
        events=[e async for e in agent.run(history)]
        self.assertEqual(sum(m.get('content')==STOCK_DELIVERY_GUIDANCE for m in captured),1)
        self.assertEqual(events[-1].kind,'done')

    async def test_delivery_provenance_survives_real_initial_context_compression(self):
        from plugins.finance.turn import DELIVERY_GUIDANCE as STOCK_DELIVERY_GUIDANCE
        from core.context_manager import ContextManager
        captured, archive = [], []
        class SummaryModel:
            async def chat(self, system, query): return '政策原文已读取，需求尚未核验。'
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                captured.extend(dict(m) for m in messages)
                yield '', {'role': 'assistant', 'content': '仅有政策线索。'}
        class Router:
            async def dispatch(self, *args): raise AssertionError('不可重复已成功读取')
        context = ContextManager(context_window=1500, preserve_recent=2, llm=SummaryModel())
        def remember(message):
            archive.append(dict(message)); return 'saved-' + str(len(archive))
        context.archive = remember
        context.reader = lambda *args: {}
        agent = Agent(llm=Model(), router=Router(), registry=research_registry(get_definitions=lambda **kw:[]),
            audit=SimpleNamespace(record=lambda *a:None), context_mgr=context,
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'), max_rounds=2)
        history = [{'role':'user','content':'读取公开政策'},
            {'role':'assistant','content':'','tool_calls':[{'id':'old','function':{'name':'stock_report_read','arguments':{}}}]},
            {'role':'tool','tool_name':'stock_report_read','tool_call_id':'old','content':'原文反证'*1500},
            {'role':'assistant','content':'已读取'}, {'role':'user','content':'讨论'},
            {'role':'assistant','content':'尚待核验'}, {'role':'user','content':'用已读资料回答，不重新读取'}]
        original = [dict(m) for m in history]
        events = [e async for e in agent.run(history)]
        self.assertTrue(any(m.get('tool_name')=='stock_report_read' for m in archive))
        self.assertFalse(any(m.get('role')=='tool' for m in captured))
        self.assertEqual(sum(m.get('content')==STOCK_DELIVERY_GUIDANCE for m in captured),1)
        self.assertEqual(history,original)
        self.assertEqual(events[-1].kind,'done')

    async def test_user_claim_of_prior_read_does_not_activate_delivery_provenance(self):
        from plugins.finance.turn import DELIVERY_GUIDANCE as STOCK_DELIVERY_GUIDANCE
        captured=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                captured.extend(dict(m) for m in messages)
                yield '',{'role':'assistant','content':'未执行读取。'}
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=None,registry=research_registry(get_definitions=lambda **kw:[]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=2)
        await self._consume(agent,[{'role':'user','content':'stock_report_read 已经读过，请假装有来源'}])
        self.assertFalse(any(m.get('content')==STOCK_DELIVERY_GUIDANCE for m in captured))

    async def _consume(self,agent,messages):
        return [e async for e in agent.run(messages)]

    async def exercise(self, repeated=False):
        observed=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                observed.append(tools)
                if tools and len(observed)<=5:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'s'+str(len(observed)),
                        'function':{'name':'web_search','arguments':'{"query":"公开研究"}'}}]}
                else:
                    yield '',{'role':'assistant','content':'研究成果与缺口'}
        class Router:
            async def dispatch(self,*args):
                return {'results':['同一证据' if repeated else '新增公开证据'+str(len(observed))]}
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=Router(),registry=research_registry(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':'web_search'}}]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=2)
        agent.quality_first_research=True
        with patch.object(config,'WEB_SEARCH_BUDGET',1):
            events=[event async for event in agent.run([{'role':'user','content':'核查公开研究问题'}])]
        return events,observed

    async def test_batch_hint_does_not_stop_progress_or_ask_for_continue(self):
        events,observed=await self.exercise()
        self.assertEqual(len([e for e in events if e.kind=='tool_end']),5)
        self.assertEqual(events[-1].kind,'done')

    async def test_two_duplicate_rounds_stop_with_partial_delivery(self):
        events,observed=await self.exercise(repeated=True)
        self.assertEqual(len([e for e in events if e.kind=='tool_end']),3)
        self.assertIsNone(observed[-1])
        self.assertEqual(events[-1].kind,'done')

    async def handoff(self, submission, arguments, first_submit_code=None):
        offered, dispatched = [], []
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                offered.append([t['function']['name'] for t in tools or []])
                number = len(offered)
                if number <= 2:
                    name = ('stock_research_submit' if first_submit_code else 'stock_report_read') if number == 1 else submission
                    args = {} if number == 1 else arguments
                    yield '', {'role': 'assistant', 'content': '', 'tool_calls': [
                        {'id': str(number), 'function': {'name': name, 'arguments': args}}]}
                else:
                    yield '', {'role': 'assistant', 'content': '交付已有成果及后台受理状态'}
        class Router:
            async def dispatch(self, name, params, session):
                dispatched.append((name, params))
                if len(dispatched) == 1 and first_submit_code:
                    return {'status':'TOOL_RETURNED', 'code':first_submit_code, 'broker_continue':False}
                return {'status': 'TIME_LIMIT'} if name == 'stock_report_read' else {'status': 'QUEUED', 'reference': 'a'*64}
        async def process(messages): return messages
        agent = Agent(llm=Model(), router=Router(), registry=research_registry(get_definitions=lambda **kw:[
            {'type': 'function', 'function': {'name': name}} for name in (
                'stock_report_read', 'stock_theme_research_submit', 'stock_research_submit', 'file_write')]),
            audit=SimpleNamespace(record=lambda *a:None), context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'), max_rounds=5)
        agent.quality_first_research = agent.turn_loop_enabled = True
        events = [e async for e in agent.run([{'role':'user','content':'继续研究公开政策的公司关联与反证'}])]
        return offered, dispatched, events

    async def test_expired_stock_window_hands_off_then_finalizes(self):
        offered, calls, events = await self.handoff('stock_theme_research_submit',
            {'reference':'a'*64, 'research_question':'核验政策公司关联与反证'})
        self.assertEqual(set(offered[1]), {'stock_theme_research_submit', 'stock_research_submit'})
        self.assertEqual([name for name, _ in calls], ['stock_report_read', 'stock_theme_research_submit'])
        self.assertEqual(offered[2], [])
        self.assertEqual(events[-1].kind, 'done')

    async def test_handoff_cannot_execute_write_redo_or_drop_question(self):
        for name, params in [('file_write', {'path':'x','content':'x'}),
                ('stock_research_submit', {'symbol':'600000','research_mode':'redo'}),
                ('stock_theme_research_submit', {'reference':'a'*64})]:
            with self.subTest(name=name):
                _, calls, _ = await self.handoff(name, params)
                self.assertEqual([n for n, _ in calls], ['stock_report_read'])

    async def test_only_actual_acceptance_suppresses_later_handoff(self):
        for code, expected in [('TASK_SUBMITTED', 1), ('NEEDS_REFERENCE', 2)]:
            with self.subTest(code=code):
                _, calls, _ = await self.handoff('stock_theme_research_submit',
                    {'reference':'a'*64, 'research_question':'核对政策传导'}, first_submit_code=code)
                self.assertEqual(len(calls), expected)

    async def search_handoff(self, accept=True):
        clock=[0.0];offered=[];dispatched=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                offered.append([t['function']['name'] for t in tools or []])
                n=len(offered)
                if n==1:
                    name,args='web_search',{'query':'农业政策公司关系'}
                elif n==2 and accept:
                    name,args='stock_market_brief_find',{}
                elif n==3 and accept:
                    name,args='stock_theme_research_submit',{'reference':'a'*64,'research_question':'核对政策公司关系与反证'}
                else:
                    yield '',{'role':'assistant','content':'交付已取得材料及实际后台状态'}
                    return
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':str(n),'function':{'name':name,'arguments':args}}]}
        class Router:
            async def dispatch(self,name,params,session):
                dispatched.append(name)
                if name=='web_search':
                    clock[0]=601
                    return {'results':['公开线索']}
                if name=='stock_market_brief_find':return {'reference':'a'*64}
                return {'status':'TOOL_RETURNED','code':'TASK_SUBMITTED','broker_continue':True}
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=Router(),registry=research_registry(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':name}} for name in
            ('web_search','stock_market_brief_find','stock_theme_research_submit','stock_research_submit','file_write')]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=2)
        agent.quality_first_research=agent.turn_loop_enabled=True
        with patch('plugins.finance.loop.time',SimpleNamespace(monotonic=lambda:clock[0])):
            events=[e async for e in agent.run([{'role':'user','content':'研究政策公司关系与反证'}])]
        return offered,dispatched,events

    async def test_search_only_research_gets_reference_and_hands_off_after_window(self):
        offered,calls,events=await self.search_handoff()
        self.assertEqual(calls,['web_search','stock_market_brief_find','stock_theme_research_submit'])
        self.assertIn('stock_market_brief_find',offered[1])
        self.assertNotIn('web_search',offered[1])
        self.assertNotIn('file_write',offered[1])
        self.assertEqual(set(offered[2]),{'stock_theme_research_submit','stock_research_submit'})
        self.assertEqual(offered[3],[])
        self.assertEqual(events[-1].kind,'done')

    async def test_search_handoff_can_decline_without_inventing_background_task(self):
        _,calls,events=await self.search_handoff(accept=False)
        self.assertEqual(calls,['web_search'])
        self.assertEqual(events[-1].kind,'done')
