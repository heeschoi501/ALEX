from datetime import datetime, timezone
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from jarvis.tools import ToolError
from jarvis.workspace import SafeWorkspaceEditor


class WorkspaceEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root / "project"
        self.backups = root / "backups"
        self.root.mkdir()
        (self.root / "demo.py").write_text("x = 1\n", encoding="utf-8")
        self.editor = SafeWorkspaceEditor(self.root, self.backups, max_bytes=10_000)

    def tearDown(self):
        self.temp.cleanup()

    def test_persisted_proposal_rejects_changed_file_and_survives_discard(self):
        from jarvis.storage import Storage
        db = Path(self.temp.name) / 'edits.db'
        first = SafeWorkspaceEditor(self.root, self.backups, storage=Storage(db))
        proposal = first.create_proposal('demo.py', 'x = 2\n')
        reopened = SafeWorkspaceEditor(self.root, self.backups, storage=Storage(db))
        self.assertEqual(reopened.get(proposal.proposal_id).diff, proposal.diff)
        (self.root / 'demo.py').write_text('x = 3\n', encoding='utf-8')
        with self.assertRaisesRegex(ToolError, '오래된 제안'):
            reopened.apply(proposal.proposal_id)
        self.assertEqual((self.root / 'demo.py').read_text(), 'x = 3\n')
        self.assertTrue(reopened.discard(proposal.proposal_id))
        self.assertIsNone(first.get(proposal.proposal_id))
        again = SafeWorkspaceEditor(self.root, self.backups, storage=Storage(db))
        self.assertIsNone(again.get(proposal.proposal_id))

    def test_persisted_proposals_are_isolated_by_project(self):
        from jarvis.storage import Storage
        store = Storage(Path(self.temp.name) / 'edits.db')
        first = SafeWorkspaceEditor(self.root, self.backups, storage=store)
        proposal = first.create_proposal('demo.py', 'x = 2\n')
        other_root = Path(self.temp.name) / 'other'
        other_root.mkdir()
        (other_root / 'demo.py').write_text('x = 1\n', encoding='utf-8')
        other = SafeWorkspaceEditor(other_root, self.backups, storage=store)
        self.assertIsNone(other.get(proposal.proposal_id))
        with self.assertRaises(ToolError):
            other.apply(proposal.proposal_id)
        self.assertIsNotNone(first.get(proposal.proposal_id))

    def test_persisted_proposal_rechecks_path_after_symlink_change(self):
        from jarvis.storage import Storage
        store = Storage(Path(self.temp.name) / 'edits.db')
        editor = SafeWorkspaceEditor(self.root, self.backups, storage=store)
        proposal = editor.create_proposal('demo.py', 'x = 2\n')
        outside = Path(self.temp.name) / 'outside.py'
        outside.write_text('x = 1\n', encoding='utf-8')
        (self.root / 'demo.py').unlink()
        try:
            (self.root / 'demo.py').symlink_to(outside)
        except OSError:
            self.skipTest('symlink unavailable')
        reopened = SafeWorkspaceEditor(self.root, self.backups, storage=store)
        with self.assertRaises(ToolError):
            reopened.apply(proposal.proposal_id)
        self.assertEqual(outside.read_text(), 'x = 1\n')

    def test_proposal_does_not_modify_file(self):
        p = self.editor.create_proposal("demo.py", "x = 2\n")
        self.assertIn("-x = 1", p.diff)
        self.assertIn("+x = 2", p.diff)
        self.assertEqual((self.root / "demo.py").read_text(), "x = 1\n")

    def test_apply_is_backed_up_and_atomic_from_user_view(self):
        p = self.editor.create_proposal("demo.py", "x = 2\n")
        target, backup = self.editor.apply(p.proposal_id)
        self.assertEqual(target.read_text(), "x = 2\n")
        self.assertEqual(backup.read_text(), "x = 1\n")
        self.assertIsNone(self.editor.get(p.proposal_id))

    def test_stale_proposal_is_rejected(self):
        p = self.editor.create_proposal("demo.py", "x = 2\n")
        (self.root / "demo.py").write_text("x = 3\n", encoding="utf-8")
        with self.assertRaises(ToolError):
            self.editor.apply(p.proposal_id)
        self.assertEqual((self.root / "demo.py").read_text(), "x = 3\n")

    def test_path_escape_is_blocked(self):
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("secret = 1\n")
        with self.assertRaises(ToolError):
            self.editor.read("../outside.py")

    def test_empty_and_identical_proposal_rejected(self):
        with self.assertRaises(ToolError):
            self.editor.create_proposal("demo.py", "")
        with self.assertRaises(ToolError):
            self.editor.create_proposal("demo.py", "x = 1\n")

    def test_code_fence_is_normalized(self):
        p = self.editor.create_proposal("demo.py", "```python\nx = 9\n```")
        self.assertEqual(p.proposed_text.replace("\r\n", "\n"), "x = 9\n")

    def test_undo_restores_latest_backup_and_is_itself_reversible(self):
        p = self.editor.create_proposal("demo.py", "x = 2\n")
        self.editor.apply(p.proposal_id)
        target, source = self.editor.restore_latest("demo.py")
        self.assertEqual(target.read_text(), "x = 1\n")
        self.assertTrue(source.exists())
        # undo created a new backup of x=2
        backups = list(self.editor.backup_root.glob("*/demo.py"))
        self.assertGreaterEqual(len(backups), 2)


    def test_backup_stamps_are_unique_when_the_clock_does_not_move(self):
        """Windows 시계 해상도(~15ms) 안의 연속 백업이 서로를 덮어쓰지 않아야 한다.

        시계를 고정해 결정적으로 재현한다. 고정이 없으면 이 회귀는 실행마다 나타났다
        사라졌다 한다(실측 3회 중 1회 실패).
        """
        frozen = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        with mock.patch("jarvis.workspace.datetime") as fake:
            fake.now.return_value = frozen
            stamps = [self.editor._next_backup_stamp() for _ in range(5)]
        self.assertEqual(len(set(stamps)), 5, f"중복된 백업 폴더 이름: {stamps}")
        self.assertEqual(stamps, sorted(stamps), "백업 폴더 이름은 시간 순으로 정렬 가능해야 한다")

    def test_undo_keeps_both_backups_when_clock_does_not_move(self):
        """apply 직후 undo 해도 백업 두 개가 모두 남아야 한다 (덮어쓰기 금지)."""
        frozen = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        with mock.patch("jarvis.workspace.datetime") as fake:
            fake.now.return_value = frozen
            p = self.editor.create_proposal("demo.py", "x = 2\n")
            self.editor.apply(p.proposal_id)
            self.editor.restore_latest("demo.py")
        backups = sorted(self.editor.backup_root.glob("*/demo.py"))
        self.assertEqual(len(backups), 2, f"백업이 덮어써졌다: {backups}")
        self.assertEqual({b.read_text() for b in backups}, {"x = 1\n", "x = 2\n"})

    def test_invalid_python_proposal_is_rejected_before_apply(self):
        with self.assertRaises(ToolError):
            self.editor.create_proposal("demo.py", "def broken(:\n    pass\n")
        self.assertEqual((self.root / "demo.py").read_text(), "x = 1\n")

    def test_discard(self):
        p = self.editor.create_proposal("demo.py", "x = 5\n")
        self.assertTrue(self.editor.discard(p.proposal_id))
        self.assertFalse(self.editor.discard(p.proposal_id))


if __name__ == "__main__":
    unittest.main()
