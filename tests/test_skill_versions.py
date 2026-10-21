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
                    mark.write_text(prefix + '新方法', encoding='utf-8')
                    self.assertEqual((await first.info('meeting'))['body'], '旧方法')
                    second = await SkillSession.prepare(client, 'http://runner', {})
                    self.assertEqual((await second.info('meeting'))['body'], '新方法')
                    self.assertNotEqual(first.snapshot['versions']['meeting']['revision'],
                                        second.snapshot['versions']['meeting']['revision'])
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
            self.assertEqual(listing['skills'], [{'name': 'legacy'}])
