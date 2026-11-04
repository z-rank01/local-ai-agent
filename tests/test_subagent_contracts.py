import unittest
from harness.contracts.subagents import RoleTask
from harness.runtime.subagents.results import structured_result
from harness.runtime.subagents.spec import role_spec, role_tasks, validate_dependencies
from tests.subagent_fixtures import role


class SubagentContractTests(unittest.TestCase):
    def test_local_qwen_extra_closing_array_is_a_real_failure_not_fence_repair(self):
        # Syntax-only anonymisation of 9e3052672070: one complete JSON fence,
        # valid top-level artifacts/issues followed by an extra closing array.
        task=RoleTask(role=role('evidence',kind='source_excerpts'),text='取证')
        text='说明\n```json\n{"artifacts":[],"issues":["材料缺口"]}]\n```'
        with self.assertRaisesRegex(ValueError,'Extra data'):
            structured_result(task,text,status='COMPLETE')

    def test_role_permissions_and_dependencies_are_narrowed(self):
        selected = role_spec({'role_id':'reader','prompt':'读取','tools':['file_read'],
            'depends_on':['prepare'], 'output':{'artifact_kind':'source','recipe_namespace':'evidence'}},
            known_tools={'file_read'})
        self.assertEqual(selected.tools, frozenset({'file_read'}))
        with self.assertRaisesRegex(ValueError, '超出父运行授权'):
            role_spec({'role_id':'writer','prompt':'写入','tools':['file_write'],
                'output':{'artifact_kind':'x','recipe_namespace':'x'}}, known_tools={'file_read'})
        with self.assertRaisesRegex(ValueError, '递归委派'):
            role_spec({'role_id':'nested','prompt':'嵌套','tools':['agent_batch_create'],
                'output':{'artifact_kind':'x','recipe_namespace':'x'}},
                known_tools={'agent_batch_create'})
        with self.assertRaisesRegex(ValueError, '循环'):
            validate_dependencies({'a':role('a',depends_on=('b',)), 'b':role('b',depends_on=('a',))})

    def test_builtin_role_config_loads_without_plugin_code(self):
        from pathlib import Path
        from harness.runtime.subagents.spec import load_role_specs
        roles = load_role_specs(Path('.'), {'file_read'}, Path('config/subagents.json'))
        self.assertEqual(set(roles), {'assistant','file_reader'})
        self.assertEqual(roles['assistant'].tools, frozenset())
        self.assertEqual(roles['file_reader'].tools, frozenset({'file_read'}))

    def test_batch_requires_registered_roles_and_dependencies(self):
        roles = {'evidence':role('evidence'), 'analysis':role('analysis',depends_on=('evidence',))}
        tasks = role_tasks([{'role_id':'analysis','task':'分析'},
                            {'role_id':'evidence','task':'取证','evidence_ids':['e1']}], roles)
        self.assertEqual([task.role.role_id for task in tasks], ['analysis','evidence'])
        with self.assertRaisesRegex(ValueError, '未登记'):
            role_tasks([{'role_id':'unknown','task':'x'}], roles)
        with self.assertRaisesRegex(ValueError, '缺少依赖'):
            role_tasks([{'role_id':'analysis','task':'分析'}], roles)
        with self.assertRaisesRegex(ValueError, '不能重复'):
            role_tasks([{'role_id':'evidence','task':'一'}, {'role_id':'evidence','task':'二'}], roles)

    def test_structured_result_preserves_public_artifacts_only(self):
        task = RoleTask(role=role('analysis'), text='分析')
        result = structured_result(task, '''{"artifacts":[{"kind":"analysis","content":{"value":1,
            "reasoning_content":"hidden"},"evidence_refs":["e1"]}],"issues":[]}''', status='COMPLETE')
        self.assertEqual(result.artifacts[0].content, {'value':1})
        self.assertEqual(result.artifacts[0].evidence_refs, ('e1',))
        self.assertEqual(len(result.artifacts[0].sha256), 64)
        empty = structured_result(task, '{"artifacts":[],"issues":"无"}', status='COMPLETE')
        self.assertEqual(empty.issues, ())
        with self.assertRaisesRegex(ValueError, '产物类型'):
            structured_result(task, '{"artifacts":[{"kind":"other","content":{}}]}', status='COMPLETE')
        failed = structured_result(task, 'not-json', status='UNKNOWN', error='timeout')
        self.assertEqual(failed.status, 'UNKNOWN')
        self.assertEqual(failed.raw_text, 'not-json')

    def test_safe_output_shape_variants_preserve_original_content(self):
        task = RoleTask(role=role('evidence', kind='source_excerpts'), text='取证')
        text = '{"artifacts":{"source-1":{"kind":"source_excerpts","content":{"excerpts":[{"id":"E1"}]}}},"issues":["缺付款条款"]}'
        result = structured_result(task, text, status='COMPLETE')
        self.assertEqual(result.status, 'COMPLETE')
        self.assertEqual(result.artifacts[0].content, {'excerpts':[{'id':'E1'}]})
        self.assertEqual(result.issues, ({'reason':'缺付款条款'},))
        self.assertEqual(result.raw_text, text)
        with self.assertRaisesRegex(ValueError, '对象或文字数组'):
            structured_result(task, '{"artifacts":[],"issues":[1]}', status='COMPLETE')


if __name__ == '__main__':
    unittest.main()
