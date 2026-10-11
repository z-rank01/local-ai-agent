"""Execute real candidate batch/preflight functions with offline boundary doubles."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

DRIVER=os.environ.get('QUALITY_REPLAY_DRIVER')


@unittest.skipUnless(DRIVER, 'requires candidate driver')
class ReplayBatchTests(unittest.TestCase):
    def setUp(self):
        import httpx
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ready={'settings':{'state_dir':'simulation/in45-method-acceptance-20261010'},
                    'online':True,'worker':{'research_only':True},
                    'research_model':{'model':'qwen3.8-flash'}}
        self.response=Mock()
        self.response.json.return_value=self.ready
        transport=SimpleNamespace(get=Mock(return_value=self.response),HTTPError=httpx.HTTPError)
        def save(path, value):path.write_text(json.dumps(value),encoding='utf-8')
        self.policy=Mock()
        self.policy.execute_fixed_case.return_value={'run_status':'COMPLETED','data':{}}
        self.ns={'root':self.root,'httpx':transport,'headers':{},'snapshot':{'model':'qwen3.8-flash'},
                 'json':json,'time':time,'stamp':lambda:'now','atomic_json':save,
                 'digest':lambda value:json.dumps(value,sort_keys=True),'policy':self.policy,
                 'ReplayRunner':lambda *a:SimpleNamespace(calls=[])}
        tree=ast.parse(Path(DRIVER).read_text(encoding='utf-8-sig'))
        nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))
               and n.name in ('ReplayPause','preflight','execute')]
        self.assertEqual(len(nodes),3)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),DRIVER,'exec'),self.ns)
        material={'public':'fixed'}
        case={'material':material,'input_hash':self.ns['digest'](material)}
        save(self.root/'Q01.json',case)
        self.sample={'blind_id':'blind-a','case_id':'Q01','arm':'quality','repetition':1}

    def test_unavailable_service_yields_without_terminal_result_or_model_call(self):
        import httpx
        self.ns['httpx'].get.side_effect=httpx.ConnectError('offline')
        self.ns['execute'](self.sample)
        self.assertFalse((self.root/'result-blind-a.json').exists())
        self.policy.execute_fixed_case.assert_not_called()

    def test_wrong_state_or_mode_or_model_yields_without_consuming_sample(self):
        for section,key,value in [('settings','state_dir','production'),('worker','research_only',False),
                                  ('research_model','model','different')]:
            with self.subTest(section=section):
                bad=deepcopy(self.ready);bad[section][key]=value
                self.response.json.return_value=bad
                self.ns['execute'](self.sample)
                self.assertFalse((self.root/'result-blind-a.json').exists())
        self.policy.execute_fixed_case.assert_not_called()

    def test_pause_before_and_during_case_preserves_resume(self):
        (self.root/'PAUSE').touch()
        self.ns['execute'](self.sample)
        self.policy.execute_fixed_case.assert_not_called()
        (self.root/'PAUSE').unlink()
        self.policy.execute_fixed_case.side_effect=self.ns['ReplayPause']('step boundary')
        self.ns['execute'](self.sample)
        self.assertFalse((self.root/'result-blind-a.json').exists())
        self.policy.execute_fixed_case.side_effect=None
        self.ns['execute'](self.sample)
        self.assertEqual(json.loads((self.root/'result-blind-a.json').read_text())['output']['run_status'],'COMPLETED')

    def test_unknown_dependency_does_not_block_independent_sample(self):
        self.ns['execute']({**self.sample,'blocked_by_unknown':['old-call']})
        self.policy.execute_fixed_case.assert_not_called()
        self.ns['execute']({**self.sample,'blind_id':'blind-b'})
        self.assertEqual(self.policy.execute_fixed_case.call_count,1)
        first=json.loads((self.root/'result-blind-a.json').read_text())
        self.assertEqual(first['output']['run_status'],'UNKNOWN')
        self.assertEqual(first['calls'],[])

    def test_completed_sample_reentry_does_not_execute_again(self):
        self.ns['execute'](self.sample)
        self.ns['execute'](self.sample)
        self.assertEqual(self.policy.execute_fixed_case.call_count,1)

    def test_http_rejection_is_pause_not_quality_failure(self):
        import httpx
        self.response.raise_for_status.side_effect=httpx.HTTPStatusError('unauthorized',request=Mock(),response=Mock())
        self.ns['execute'](self.sample)
        self.policy.execute_fixed_case.assert_not_called()
        self.assertFalse((self.root/'result-blind-a.json').exists())
