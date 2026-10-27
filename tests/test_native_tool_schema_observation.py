import copy
import json
import unittest
from unittest.mock import patch

import httpx

from core.native_tool_schema import fingerprint, tool_schema_observation
from core.providers import CompatibleClient, CallBudget

PURPOSES = {'stock_report_read': {'analysis', 'calculation'},
            'stock_public_evidence_read': {'analysis', 'source_scope'}}


def observe(payload):
    return tool_schema_observation(payload, PURPOSES)


def definition(enum=None):
    return {'type': 'function', 'function': {'name': 'stock_report_read',
        'description': 'private-definition-text', 'parameters': {'type': 'object',
        'properties': {'reference': {'type': 'string', 'default': 'private-default'},
                       'reply_focus': {'type': 'string', 'enum': enum or ['analysis', 'calculation']}}}}}


class ToolSchemaTests(unittest.TestCase):
    def test_complete_hash_without_definition_text_or_defaults(self):
        payload={'tools': [definition()]};old=copy.deepcopy(payload)
        v=observe(payload)
        self.assertEqual(v['tools_sha256'],fingerprint(payload['tools']))
        self.assertEqual(v['tool_schema_chars'],len(json.dumps(payload['tools'],ensure_ascii=False)))
        self.assertEqual(v['tool_schema_fingerprints'][0]['reply_focus_schema']['enum'],['analysis','calculation'])
        self.assertNotIn('private-definition-text',json.dumps(v))
        self.assertNotIn('private-default',json.dumps(v))
        self.assertEqual(payload,old)
        payload['tools'][0]['function']['description']='different'
        self.assertNotEqual(observe(payload)['tools_sha256'],v['tools_sha256'])

    def test_missing_empty_and_bad_schema_are_distinct(self):
        for p,present in (({},False),({'tools':[]},True)):
            v=observe(p)
            self.assertEqual(v['tool_schema_count'],0);self.assertEqual(v['tools_field_present'],present)
        v=observe({'tools':'private-test-value'})
        self.assertEqual(v['tool_schema_status'],'UNAVAILABLE')
        self.assertNotIn('private-test-value',json.dumps(v))
        v=observe({'tools':[definition(['analysis','private-test-value'])]})
        self.assertEqual(v['tool_schema_fingerprints'][0]['reply_focus_schema']['status'],'UNRECOGNISED')
        self.assertNotIn('private-test-value',json.dumps(v))
        d=definition();del d['function']['parameters']['properties']['reply_focus']
        self.assertEqual(observe({'tools':[d]})['tool_schema_fingerprints'][0]['reply_focus_schema']['status'],'MISSING')


class CompatiblePayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_matches_serialized_request_and_does_not_change_options(self):
        tools=[definition()];old=copy.deepcopy(tools);sent=[];observed=[]
        client=CompatibleClient({'id':'qwen:test','model':'test','api_key_env':'SCHEMA_TEST_KEY',
                                'base_url':'https://example.invalid/v1','options':{'enable_thinking':True}},CallBudget('unused',0))
        await client._client.aclose()
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'done'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler));self.addAsyncCleanup(client.close)
        with patch.dict('os.environ',{'SCHEMA_TEST_KEY':'private-key'}):
            result=[v async for v in client.chat_stream_with_tools([{'role':'user','content':'private-input'}],tools,
                      request_observer=lambda event,data:observed.append((event,data)))]
        request=next(v for event,v in observed if event=='native_model_request')
        self.assertEqual(request['tools_sha256'],fingerprint(sent[0]['tools']))
        self.assertEqual(sent[0]['tools'],old);self.assertEqual(tools,old)
        self.assertEqual(len(sent),1);self.assertTrue(sent[0]['enable_thinking'])
        self.assertNotIn('tool_choice',sent[0]);self.assertNotIn('stream_options',sent[0])
        self.assertNotIn('tool_schema_fingerprints',sent[0])
        for private in ('private-key','private-input','private-default','private-definition-text'):
            self.assertNotIn(private,json.dumps(observed))
        self.assertEqual(result[-1][1]['content'],'done')

