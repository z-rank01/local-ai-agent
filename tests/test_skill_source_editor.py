"""Source editing uses real package/version operations, never script execution."""
import sys
import tempfile
import unittest
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import httpx
from fastapi import FastAPI
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/runner'))
from skill_source import list_files, read_file, save_file, SourceConflict, LIMIT
from skill_versions import SkillVersions
import skill_version_api


class SkillSourceEditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.versions = SkillVersions(root / 'skills', root / 'versions')
        self.package = self.versions.source / 'meeting'
        self.package.mkdir(parents=True)
        self.text = '---\nname: meeting\ndescription: 整理讨论\n---\n旧内容\n'
        (self.package / 'SKILL.md').write_text(self.text, encoding='utf-8')

    def test_save_validates_captures_new_revision_and_keeps_existing_run_version(self):
        old = self.versions.capture('meeting')
        source = read_file(self.versions.source, 'meeting', 'SKILL.md')
        result = save_file(self.versions, 'meeting', 'SKILL.md', self.text.replace('旧内容', '新内容'), source['sha256'])
        self.assertNotEqual(old['revision'], result['revision'])
        self.assertIn('旧内容', self.versions.read('meeting', old['revision']).body)
        self.assertIn('新内容', self.versions.read('meeting', result['revision']).body)
        self.assertEqual(result['content'], (self.package / 'SKILL.md').read_text(encoding='utf-8'))

    def test_invalid_package_and_stale_hash_never_overwrite_source(self):
        original = read_file(self.versions.source, 'meeting', 'SKILL.md')
        with self.assertRaisesRegex(ValueError, '未保存'):
            save_file(self.versions, 'meeting', 'SKILL.md', 'missing frontmatter', original['sha256'])
        self.assertEqual((self.package / 'SKILL.md').read_text(encoding='utf-8'), self.text)
        (self.package / 'SKILL.md').write_text(self.text + '外部编辑', encoding='utf-8')
        with self.assertRaises(SourceConflict):
            save_file(self.versions, 'meeting', 'SKILL.md', self.text, original['sha256'])
        self.assertIn('外部编辑', (self.package / 'SKILL.md').read_text(encoding='utf-8'))

    def test_boundaries_binary_limits_and_scripts_are_not_executed(self):
        (self.package / 'assets').mkdir()
        (self.package / 'assets/data.bin').write_bytes(b'\x00\xff')
        (self.package / 'large.txt').write_text('x' * (LIMIT + 1), encoding='utf-8')
        (self.package / 'scripts').mkdir()
        script = self.package / 'scripts/run.py'
        script.write_text('raise RuntimeError("must not run")', encoding='utf-8')
        rows = {row['path']: row for row in list_files(self.versions.source, 'meeting')['files']}
        self.assertFalse(rows['large.txt']['writable'])
        self.assertFalse(rows['assets/data.bin']['writable'])
        data = read_file(self.versions.source, 'meeting', 'scripts/run.py')
        save_file(self.versions, 'meeting', 'scripts/run.py', 'raise RuntimeError("still not run")', data['sha256'])
        for path in ('../SKILL.md', '/workspace/skills/meeting/SKILL.md', 'scripts/../../SKILL.md', '.skill_registry/file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                read_file(self.versions.source, 'meeting', path)
        with self.assertRaises(ValueError):
            read_file(self.versions.source, 'missing', 'SKILL.md')

    def test_source_and_resource_links_are_rejected(self):
        outside = Path(self.temporary.name) / 'outside.txt'
        outside.write_text('private', encoding='utf-8')
        try:
            (self.package / 'link.txt').symlink_to(outside)
        except OSError:
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(ValueError):
            read_file(self.versions.source, 'meeting', 'link.txt')
        with self.assertRaises(ValueError):
            list_files(self.versions.source, 'meeting')


class SkillSourceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_save_reaches_original_runner_and_conflict_keeps_new_source(self):
        from bff.app import app
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'skills' / 'meeting'
            source.mkdir(parents=True)
            text = '---\nname: meeting\ndescription: meeting notes\n---\nold'
            (source / 'SKILL.md').write_text(text, encoding='utf-8')
            versions = SkillVersions(source.parent, root / 'versions')
            runner = FastAPI()
            runner.include_router(skill_version_api.router)
            snapshot = SimpleNamespace(record={'runner_port': 12345})
            workspaces = SimpleNamespace(ensure=AsyncMock(return_value=snapshot),
                acquire=Mock(return_value='lease'), active={'lease': object()})
            service = SimpleNamespace(workspaces=workspaces)
            chat = SimpleNamespace(for_workspace=lambda identity: service)
            catalog = SimpleNamespace(reconcile=lambda snapshot: {'meeting': {'pending': None}})
            client_type = httpx.AsyncClient
            async with client_type(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9510',
                headers={'X-Harness-Session': 'source-test', 'Origin': 'http://127.0.0.1:9510'}) as client:
                with patch.dict(os.environ, {'DAILY_SERVICES': '1'}), \
                     patch('core.ui_session.session_token', return_value='source-test'), \
                     patch('skill_version_api.storage', return_value=versions), \
                     patch('bff.skill_management.get_chat_service', return_value=chat), \
                     patch('bff.skill_management.get_runtime', return_value=Mock()), \
                     patch('bff.skill_management.catalog_for', return_value=catalog), \
                     patch('bff.skill_management.tool_token', return_value='workspace-test'), \
                     patch('bff.skill_management.snapshot_for', new=AsyncMock(return_value=versions.freeze())), \
                     patch('bff.skill_management.httpx.AsyncClient', side_effect=lambda **kw: client_type(
                         transport=httpx.ASGITransport(app=runner), **kw)):
                    base = '/api/workspaces/example/skills/meeting'
                    listing = await client.get(base + '/source')
                    self.assertEqual(listing.status_code, 200, listing.text)
                    read = await client.get(base + '/source-file', params={'path': 'SKILL.md'})
                    payload = {'content': text.replace('old', 'new'), 'sha256': read.json()['sha256']}
                    saved = await client.put(base + '/source-file?path=SKILL.md', json=payload)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertEqual(saved.json()['activation'], 'next_run')
                    conflict = await client.put(base + '/source-file?path=SKILL.md', json=payload)
                    self.assertEqual(conflict.status_code, 409)
                    self.assertEqual((source / 'SKILL.md').read_text(encoding='utf-8'), payload['content'])
                    self.assertNotIn('lease', workspaces.active)
