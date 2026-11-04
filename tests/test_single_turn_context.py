"""Long local research keeps its user question and complete tool exchanges."""
import unittest
from copy import deepcopy
from core.context_manager import ContextManager
from core.research_errors import classify_rejection


class SingleTurnContextTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        self.archive = {}
        self.inputs = []
        owner = self
        class Model:
            async def chat(self, system, text):
                owner.inputs.append(text)
                return 'Summary with evidence references.'
        manager = ContextManager(context_window=4096, preserve_recent=2, llm=Model())
        def save(message):
            key = 'e' + str(len(self.archive))
            self.archive[key] = deepcopy(message)
            return key
        manager.archive = save
        manager.reader = lambda key, cursor: self.archive[key]
        return manager

    def history(self):
        return [{'role':'system','content':'rules'}, {'role':'user','content':'original question'}] + [
            m for i in range(4) for m in (
                {'role':'assistant','content':None,'tool_calls':[{'id':str(i),'function':{'name':'read','arguments':{'path':f'/source/{i}'}}}]},
                {'role':'tool','tool_call_id':str(i),'content':('evidence ' * 500) + f' late correction {i}'})]

    async def test_single_turn_archives_complete_pairs_and_preserves_question(self):
        manager = self.manager()
        history = self.history()
        original = deepcopy(history)
        result = await manager.process(history)
        self.assertEqual(history, original)
        self.assertLess(manager.estimate_tokens(result), manager.estimate_tokens(history))
        self.assertIn(history[1], result)
        self.assertEqual(result[-2:], history[-2:])
        self.assertTrue(all(m in self.archive.values() for m in history[2:-2]))
        self.assertIn('/source/0', ''.join(self.inputs))
        self.assertIn('late correction 2', ''.join(self.inputs))

    async def test_outstanding_parallel_call_is_not_split(self):
        manager = self.manager()
        history = self.history()
        history[2]['tool_calls'].append({'id':'pending','function':{'name':'read','arguments':{}}})
        self.assertEqual(await manager.process(history), history)
        self.assertEqual(self.inputs, [])

    def test_exact_template_error_classified_without_guessing_or_storing_text(self):
        result = classify_rejection(500, {'error':'no user query found in messages'}, {})
        self.assertEqual(result['local_error_kind'], 'CHAT_TEMPLATE_MISSING_USER')
        self.assertEqual(result['rejection_body_chars'],31)
        self.assertNotIn('no user query', str(result))
        other = classify_rejection(500, {'error':'x'*31}, {})
        self.assertEqual(other['local_error_kind'], 'UNCLASSIFIED')

    def test_wrapped_context_error_keeps_only_type_and_counts(self):
        import json
        result = classify_rejection(400, {'error':json.dumps({'error':{
            'type':'exceed_context_size_error','message':'private prompt',
            'n_prompt_tokens':60054,'n_ctx':49152}})}, {})
        self.assertEqual(result['local_error_kind'],'CONTEXT_LENGTH_EXCEEDED')
        self.assertEqual(result['n_ctx'],49152)
        self.assertNotIn('private prompt',str(result))

    def test_local_requests_disable_server_history_truncation(self):
        from core.llm_client import LLMClient
        client = LLMClient.__new__(LLMClient)
        client.think = None
        self.assertIs(client.request_parameters()['truncate'],False)

    async def test_large_recent_tool_result_can_be_archived_without_prefix_truncation(self):
        manager = self.manager()
        history = self.history()[:4]
        history[-1]['content'] = 'large evidence ' * 3000 + 'IMPORTANT TAIL'
        result = await manager.process(history)
        self.assertIn(history[1], result)
        self.assertIn(history[-1], self.archive.values())
        self.assertIn('IMPORTANT TAIL', ''.join(self.inputs))
        self.assertLess(manager.estimate_tokens(result), manager.context_window)
