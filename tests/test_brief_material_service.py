import unittest
from fastapi import HTTPException
from tests import test_brief_delivery
from unittest.mock import patch
from tests.test_brief_material import history, original, REF


class BriefMaterialServiceTests(unittest.TestCase):
    def setUp(self):
        fixture = test_brief_delivery.BriefDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.service, self.store, self.conv = fixture.service, fixture.store, fixture.conv
        self.allowed = False
        patched = patch('core.workspaces.model_settings.workspace_allowed', side_effect=lambda: self.allowed)
        patched.start(); self.addCleanup(patched.stop)

    def allow(self, allowed=True):
        self.allowed = allowed
        record = self.service.workspaces.get(self.conv.workspace_id)
        self.service.workspaces.save({**record, 'cloud_read': allowed})

    def seed(self, cloud_safe=True):
        for row in history(original()):
            self.store.add_message(self.conv.id, role='protocol',
                                   metadata={'cloud_safe': cloud_safe, 'message': row})

    def test_only_same_conversation_cloud_safe_protocol_no_writes(self):
        self.allow(); self.seed()
        before = self.store.get_messages(self.conv.id)
        result = self.service.prepare_brief_material(self.conv.id, [REF])
        self.assertEqual(result['workspace_id'], self.conv.workspace_id)
        self.assertEqual(result['packet']['tasks'][0]['original_sources'][0]['text'], original()['excerpts'][0]['text'])
        self.assertEqual(self.store.get_messages(self.conv.id), before)
        other = self.store.create_conversation()
        with self.assertRaises(HTTPException) as caught:
            self.service.prepare_brief_material(other.id, [REF])
        self.assertEqual(caught.exception.status_code, 422)

    def test_permission_and_private_protocol_denied(self):
        self.allow(False); self.seed()
        with self.assertRaises(HTTPException) as caught:
            self.service.prepare_brief_material(self.conv.id, [REF])
        self.assertEqual(caught.exception.status_code, 403)
        self.allow(); other = self.store.create_conversation(); self.conv = other; self.seed(False)
        with self.assertRaises(HTTPException) as caught:
            self.service.prepare_brief_material(other.id, [REF])
        self.assertEqual(caught.exception.status_code, 422)

    def test_busy_denied(self):
        self.allow(); self.seed(); self.service._active_conversations.add(self.conv.id)
        with self.assertRaises(HTTPException) as caught:
            self.service.prepare_brief_material(self.conv.id, [REF])
        self.assertEqual(caught.exception.status_code, 409)

    def test_named_workspace_uses_own_permission_not_default(self):
        self.allow(True)
        manager = self.service.workspaces
        named = {**manager.get(), 'id': 'aabbccddeeff', 'name': '独立目录', 'cloud_read': False}
        manager.save(named)
        self.conv = self.store.create_conversation(workspace_id=named['id'])
        self.seed()
        with self.assertRaises(HTTPException) as caught:
            self.service.prepare_brief_material(self.conv.id, [REF])
        self.assertEqual(caught.exception.status_code, 403)
        manager.save({**named, 'cloud_read': True})
        self.allowed = False
        result = self.service.prepare_brief_material(self.conv.id, [REF])
        self.assertEqual(result['workspace_id'], named['id'])
