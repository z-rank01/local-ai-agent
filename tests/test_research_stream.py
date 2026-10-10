import json
import unittest
from types import SimpleNamespace
import httpx
from core.research_stream import read_completion


def response(events, error=None):
    async def lines():
        for event in events:
            yield 'data: ' + json.dumps(event)
        if error:
            raise error
        yield 'data: [DONE]'
    return SimpleNamespace(aiter_lines=lines)


def chunk(text=None, finish=None, **fields):
    return {'id': 'response-1', 'model': 'qwen3.8-flash',
            'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': finish}], **fields}


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_is_never_stored_and_usage_after_finish_is_kept(self):
        hidden = chunk()
        hidden['choices'][0]['delta'] = {'reasoning_content': 'hidden reasoning'}
        progress = []
        result = await read_completion(response([hidden, chunk('{'), chunk('}', 'stop'),
            {'choices': [], 'usage': {'total_tokens': 42}}]), progress.append)
        self.assertEqual(result['choices'][0]['message']['content'], '{}')
        self.assertEqual(result['usage']['total_tokens'], 42)
        self.assertNotIn('hidden reasoning', str(result) + str(progress))
        self.assertEqual(progress[0]['response_id'], 'response-1')

    async def test_explicit_length_survives_lost_optional_usage_chunk(self):
        result = await read_completion(response([chunk('{', 'length')], httpx.ReadTimeout('lost usage')), lambda _: None)
        self.assertEqual(result['choices'][0]['finish_reason'], 'length')
        self.assertEqual(result['usage'], {})

    async def test_incomplete_stream_remains_unknown_with_response_identity(self):
        progress = []
        with self.assertRaises(httpx.ReadTimeout):
            await read_completion(response([chunk('{')], httpx.ReadTimeout('lost')), progress.append)
        self.assertEqual(progress[0]['response_id'], 'response-1')

    async def test_done_without_finish_and_mixed_identity_are_rejected(self):
        for events in ([chunk('{}')], [chunk('{'), chunk('}', 'stop', id='other')]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                await read_completion(response(events), lambda _: None)
