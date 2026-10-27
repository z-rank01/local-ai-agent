import json
import unittest
from unittest.mock import patch

import httpx

from core.dsml import parse_dsml
from core.providers import CompatibleClient, CallBudget

ENVELOPE = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="code_exec">\n<｜｜DSML｜｜ parameter name="code" string="true">print("hello")</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'


class DsmlTests(unittest.TestCase):
    def test_double_pipe_parameters_and_typed_json(self):
        text = '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="documents_batch"><｜｜DSML｜｜ parameter name="paths" string="false">["/workspace/a.pdf"]</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
        message = parse_dsml(text)
        call = message['tool_calls'][0]
        self.assertEqual(call['function']['name'], 'documents_batch')
        self.assertEqual(json.loads(call['function']['arguments']), {'paths': ['/workspace/a.pdf']})

    def test_malformed_envelope_is_not_a_visible_answer(self):
        with self.assertRaises(ValueError):
            parse_dsml(ENVELOPE.replace('</｜｜DSML｜｜ calls>', ''))


class CompatibleDsmlTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_parses_double_pipe_calls_without_leaking_protocol(self):
        client = CompatibleClient({'id': 'deepseek:test', 'model': 'deepseek-flash',
            'api_key_env': 'DSML_TEST_KEY', 'base_url': 'https://example.invalid/v1'}, CallBudget('unused', 0))
        await client._client.aclose()
        chunks = [ENVELOPE[:13], ENVELOPE[13:63], ENVELOPE[63:]]
        def handler(request):
            rows = []
            for text in chunks:
                rows.append('data: ' + json.dumps({'choices': [{'delta': {'content': text}}]}) + '\n\n')
            rows.append('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}) + '\n\n')
            rows.append('data: [DONE]\n\n')
            return httpx.Response(200, text=''.join(rows))
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        with patch.dict('os.environ', {'DSML_TEST_KEY': 'private-key'}):
            events = [event async for event in client.chat_stream_with_tools([{'role': 'user', 'content': 'test'}])]
        self.assertFalse(any('DSML' in text for text, _ in events))
        message = events[-1][1]
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), {'code': 'print("hello")'})
