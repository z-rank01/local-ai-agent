"""Functional host/runner version pinning across edits, deletion and corruption."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

RUNNER = Path(__file__).resolve().parents[1] / 'skills/runner'
sys.path.insert(0, str(RUNNER))
from skill_versions import SkillVersions
import skill_version_api
from harness.capabilities.skill_session import SkillSession
from harness.runtime.tool_context import describe_tools


class SkillVersionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_pins_old_version_next_run_updates_and_deletion_retains_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'skills'
            package = source / 'meeting'
            package.mkdir(parents=True)
            mark = package / 'SKILL.md'
            prefix = '---\nname: meeting\ndescription: 整理会议\n---\n'
            mark.write_text(prefix + '旧方法', encoding='utf-8')
            store = SkillVersions(source, root / 'versions')
            app = FastAPI()
            app.include_router(skill_version_api.router)
            with patch.object(skill_version_api, 'storage', return_value=store):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://runner') as client:
                    first = await SkillSession.prepare(client, 'http://runner', {})
                    self.assertIn('整理会议', first.discovery_context())
                    self.assertNotIn('旧方法', first.discovery_context())
                    definitions = [{'type': 'function', 'function': {'name': 'skill_info',
                        'description': '原始说明', 'parameters': {'type': 'object'}}}]
                    offered = describe_tools(definitions, {'skill_info': first.discovery_context()})
                    self.assertIn('整理会议', offered[0]['function']['description'])
                    self.assertNotIn('旧方法', offered[0]['function']['description'])
                    self.assertEqual(definitions[0]['function']['description'], '原始说明')
                    self.assertEqual(offered[0]['function']['parameters'], definitions[0]['function']['parameters'])
                    mark.write_text(prefix + '新方法', encoding='utf-8')
                    self.assertEqual((await first.info('meeting'))['body'], '旧方法')
                    second = await SkillSession.prepare(client, 'http://runner', {})
                    self.assertEqual((await second.info('meeting'))['body'], '新方法')
                    self.assertNotEqual(first.snapshot['versions']['meeting']['revision'],
                                        second.snapshot['versions']['meeting']['revision'])
                    generated = {'path': '/workspace/skills/meeting/SKILL.md'}
                    old_step = await first.pin_generated_step(generated)
                    new_step = await second.pin_generated_step(generated)
                    self.assertIn('旧方法', Path(old_step['path']).read_text(encoding='utf-8'))
                    self.assertIn('新方法', Path(new_step['path']).read_text(encoding='utf-8'))
                    self.assertEqual(generated['path'], '/workspace/skills/meeting/SKILL.md')
                    with self.assertRaises(ValueError):
                        await first.pin_generated_step({'code': "open('/workspace/skills/missing/scripts/run.py')"})
                    diff = await client.post('/capabilities/skills/diff', json={'name': 'meeting',
                        'before': first.snapshot['versions']['meeting']['revision'],
                        'after': second.snapshot['versions']['meeting']['revision']})
                    self.assertEqual(diff.status_code, 200)
                    self.assertEqual(diff.json()['files'][0]['change'], 'modified')
                    self.assertIn('旧方法', diff.json()['files'][0]['diff'])
                    self.assertIn('新方法', diff.json()['files'][0]['diff'])
                    mark.unlink()
                    resumed = await SkillSession.prepare(client, 'http://runner', {}, first.snapshot)
                    self.assertEqual((await resumed.info('meeting'))['body'], '旧方法')
                    third = await SkillSession.prepare(client, 'http://runner', {})
                    self.assertEqual(third.snapshot['versions'], {})
                    self.assertEqual(third.discovery_context(), '')
                    self.assertIn('error', await third.adapt('skill_info', {'skill_name': 'meeting'}, {'kind': 'skill_md'}))
                    retained = Path(first.snapshot['versions']['meeting']['path']) / 'SKILL.md'
                    retained.write_text(prefix + '被改写', encoding='utf-8')
                    with self.assertRaises(httpx.HTTPStatusError):
                        await resumed.info('meeting')
                    rejected = await client.post('/capabilities/skills/diff', json={'name': 'meeting',
                        'before': first.snapshot['versions']['meeting']['revision'],
                        'after': second.snapshot['versions']['meeting']['revision']})
                    self.assertEqual(rejected.status_code, 409)

    async def test_invalid_package_isolated_and_unavailable_service_never_falls_back_to_mutable_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / 'skills/broken'
            broken.mkdir(parents=True)
            (broken / 'SKILL.md').write_text('bad', encoding='utf-8')
            frozen = SkillVersions(root / 'skills', root / 'versions').freeze()
            self.assertEqual(frozen['versions'], {})
            self.assertEqual(frozen['invalid'][0]['name'], 'broken')
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(404))) as client:
            session = await SkillSession.prepare(client, 'http://runner', {})
            self.assertIn('error', await session.adapt('skill_info', {'skill_name': 'new'},
                {'kind': 'skill_md', 'body': '不允许回退'}))
            listing = session.listing({'skills': [{'name': 'new', 'kind': 'skill_md'}, {'name': 'legacy'}]})
            self.assertEqual(listing['skills'], [])

    async def test_legacy_revision_survives_edit_delete_and_blocks_mutable_fallback(self):
        import skill_execution
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'skills'
            source.mkdir()
            code = "SKILL_METADATA = {'description': '确定性计算', 'dependencies': []}\ndef run(params):\n    return {'value': params['value'] + 1}\n"
            path = source / 'calculate.py'
            path.write_text(code, encoding='utf-8')
            store = SkillVersions(source, root / 'versions')
            first = SkillSession(None, '', {}, store.freeze())
            revision = first.execution_revision('calculate')
            path.write_text(code.replace('+ 1', '+ 2'), encoding='utf-8')
            second = store.freeze()
            self.assertNotEqual(second['versions']['calculate']['revision'], revision)
            with patch.object(skill_execution, 'storage', return_value=store):
                retained = skill_execution.resolve_execution('calculate', revision)
                async def stream(code, timeout, arguments, **kwargs):
                    result = subprocess.run([sys.executable, '-c', code, *arguments], capture_output=True, text=True, timeout=10)
                    yield {'event': 'result', 'result': {'stdout': result.stdout, 'exit_code': result.returncode}}
                with patch.object(skill_execution, 'stream_python', stream), patch.object(skill_execution, '_load_skill_config', return_value=None):
                    events = [e async for e in skill_execution.execute('calculate', retained, {'value': 4}, 10, 'fixture')]
                self.assertEqual(events[-1]['result']['result'], {'value': 5})
                path.unlink()
                self.assertEqual(skill_execution.resolve_execution('calculate', revision), retained)
                with self.assertRaisesRegex(ValueError, '缺少宿主'):
                    skill_execution.resolve_execution('calculate', None)
                retained.write_text(code + '\n# tamper', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '内容已变化'):
                    skill_execution.resolve_execution('calculate', revision)
            empty = SkillSession(None, '', {}, store.freeze())
            with self.assertRaisesRegex(ValueError, '未激活|已激活'):
                empty.execution_revision('calculate')

    async def test_legacy_discovery_never_imports_code_or_installs_missing_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'skills'
            source.mkdir()
            (source / 'untrusted.py').write_text("raise RuntimeError('must not import')\nSKILL_METADATA = {'description': 'fixture', 'dependencies': ['harness-missing-fixture-package==0.0.0']}\ndef run(params): return params\n", encoding='utf-8')
            snapshot = SkillVersions(source, root / 'versions').freeze()
            entry = snapshot['versions']['untrusted']
            self.assertEqual(entry['kind'], 'python')
            self.assertIn('harness-missing-fixture-package', entry['environment_errors'][0])
