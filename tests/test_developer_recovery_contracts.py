"""A previous PASS must never survive an interrupted verification as current proof."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from jarvis.developer import DeveloperMode, DeveloperRun
from jarvis.storage import Storage
from jarvis.workspace import SafeWorkspaceEditor


class RecoveryVerificationContracts(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "jarvis").mkdir()
        (root / "tests").mkdir()
        (root / "jarvis/source.py").write_text("value = 1\n", encoding="utf-8")
        (root / "tests/test_source.py").write_text("# Acceptance fixture\n", encoding="utf-8")
        self.storage = Storage(root / "data/app.db")
        self.core = SimpleNamespace(
            storage=self.storage,
            workspace_editor=SafeWorkspaceEditor(root, root / "backups", storage=self.storage),
            current_project_root=lambda: root,
        )
        self.mode = DeveloperMode(self.core)
        self.mode.start("jarvis/source.py", "tests/test_source.py", "Keep verification evidence current")

    def test_interrupted_status_invalidates_a_previous_full_suite_pass(self):
        for phase in ("testing", "proposing", "verifying"):
            with self.subTest(phase=phase):
                run = DeveloperRun.load(self.storage)
                run.phase = phase
                run.suite_result = "PASS"
                run.save(self.storage)
                self.mode.status()
                recovered = DeveloperRun.load(self.storage)
                self.assertEqual(recovered.phase, "needs_test")
                self.assertEqual(recovered.suite_result, "INCOMPLETE")

    def test_a_crashed_suite_is_saved_as_in_progress_and_recovers_without_old_pass(self):
        run = DeveloperRun.load(self.storage)
        run.phase = "passed"
        run.suite_result = "PASS"
        run.save(self.storage)
        observed = []

        def interrupted_suite(_root):
            observed.append(DeveloperRun.load(self.storage).phase)
            raise RuntimeError("simulated suite interruption")

        mode = DeveloperMode(self.core, suite_runner=interrupted_suite)
        with self.assertRaisesRegex(RuntimeError, "simulated suite interruption"):
            mode.verify()
        self.assertEqual(observed, ["verifying"])
        mode.status()
        recovered = DeveloperRun.load(self.storage)
        self.assertEqual(recovered.phase, "needs_test")
        self.assertEqual(recovered.suite_result, "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
