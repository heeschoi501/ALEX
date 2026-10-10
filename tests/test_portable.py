import json
import tempfile
import unittest
from pathlib import Path

from jarvis.portable import PortableStateError, export_portable_state, import_portable_state
from jarvis.storage import Storage


class PortableStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.a = Storage(root / "a.db")
        self.b = Storage(root / "b.db")
        self.bundle = root / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_export_import_memory_and_tasks(self):
        self.a.remember("editor", "vscode")
        t = self.a.add_task("run benchmark", 1)
        self.a.set_task_status(t, "done")
        export_portable_state(self.a, self.bundle)
        stats = import_portable_state(self.b, self.bundle)
        self.assertEqual(stats["memories_added"], 1)
        self.assertEqual(stats["tasks_added"], 1)
        self.assertEqual(self.b.recall("editor"), "vscode")
        self.assertEqual(self.b.list_tasks(include_done=True)[0].status, "done")

    def test_existing_memory_wins_and_repeat_task_is_skipped(self):
        self.a.remember("mode", "from-a")
        self.a.add_task("same")
        export_portable_state(self.a, self.bundle)
        self.b.remember("mode", "local-b")
        self.b.add_task("same")
        stats = import_portable_state(self.b, self.bundle)
        self.assertEqual(self.b.recall("mode"), "local-b")
        self.assertEqual(stats["memory_conflicts"], 1)
        self.assertEqual(stats["tasks_skipped"], 1)

    def test_rejects_unknown_format(self):
        self.bundle.write_text(json.dumps({"format": "evil", "version": 1, "memories": {}, "tasks": []}))
        with self.assertRaises(PortableStateError):
            import_portable_state(self.b, self.bundle)

if __name__ == "__main__":
    unittest.main()
