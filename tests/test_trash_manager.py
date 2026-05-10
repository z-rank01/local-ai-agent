import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


_SKILL_FILES_DIR = Path(__file__).resolve().parents[1] / "skills" / "files"
sys.path.insert(0, str(_SKILL_FILES_DIR))

from trash import TrashManager  # noqa: E402


class TrashManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        base = Path(self._temp_dir.name)
        self.workspace = base / "workspace"
        self.trash = base / "trash"
        (self.workspace / "docs").mkdir(parents=True)
        self.manager = TrashManager(str(self.workspace), str(self.trash))

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_list_items_includes_deleted_file_metadata(self) -> None:
        source = self.workspace / "docs" / "report.txt"
        source.write_text("hello", encoding="utf-8")

        result = self.manager.move_to_trash("docs/report.txt")
        items = self.manager.list_items()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["operation_id"], result["operation_id"])
        self.assertEqual(items[0]["workspace_path"], "/workspace/docs/report.txt")
        self.assertEqual(items[0]["relative_path"], "docs/report.txt")
        self.assertTrue(items[0]["exists_in_trash"])

    def test_restore_from_trash_restores_file_and_cleans_operation_dir(self) -> None:
        source = self.workspace / "docs" / "report.txt"
        source.write_text("hello", encoding="utf-8")

        result = self.manager.move_to_trash("docs/report.txt")
        restore_result = self.manager.restore_from_trash(result["operation_id"])

        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "hello")
        self.assertEqual(restore_result["workspace_path"], "/workspace/docs/report.txt")
        self.assertEqual(self.manager.list_items(), [])
        self.assertEqual(list(self.trash.rglob("*")), [])

    def test_cleanup_expired_removes_only_old_operations(self) -> None:
        old_source = self.workspace / "docs" / "old.txt"
        new_source = self.workspace / "docs" / "new.txt"
        old_source.write_text("old", encoding="utf-8")
        new_source.write_text("new", encoding="utf-8")

        old_result = self.manager.move_to_trash("docs/old.txt")
        new_result = self.manager.move_to_trash("docs/new.txt")

        manifest_path = Path(old_result["manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["deleted_at"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        cleanup = self.manager.cleanup_expired(30, now=datetime.now(timezone.utc))
        items = self.manager.list_items()

        self.assertEqual(cleanup["removed"], 1)
        self.assertIn(old_result["operation_id"], cleanup["operation_ids"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["operation_id"], new_result["operation_id"])


if __name__ == "__main__":
    unittest.main()
