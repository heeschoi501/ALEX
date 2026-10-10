import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage, StorageError


class TaskStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "jarvis.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_task_roundtrip_and_priority_order(self):
        normal = self.storage.add_task("normal", 2)
        high = self.storage.add_task("high", 1)
        low = self.storage.add_task("low", 3)
        items = self.storage.list_tasks()
        self.assertEqual([x.task_id for x in items], [high, normal, low])

    def test_done_hidden_by_default_and_reopen(self):
        task = self.storage.add_task("finish me")
        self.assertTrue(self.storage.set_task_status(task, "done"))
        self.assertEqual(self.storage.list_tasks(), [])
        self.assertEqual(self.storage.list_tasks(include_done=True)[0].status, "done")
        self.assertTrue(self.storage.set_task_status(task, "open"))
        self.assertEqual(self.storage.list_tasks()[0].status, "open")

    def test_delete(self):
        task = self.storage.add_task("remove")
        self.assertTrue(self.storage.delete_task(task))
        self.assertFalse(self.storage.delete_task(task))

    def test_task_validation(self):
        with self.assertRaises(StorageError):
            self.storage.add_task("")
        with self.assertRaises(StorageError):
            self.storage.add_task("x", 9)
        with self.assertRaises(StorageError):
            self.storage.set_task_status(1, "bad")

if __name__ == "__main__":
    unittest.main()

class AppStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "jarvis.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_state_roundtrip(self):
        self.assertIsNone(self.storage.get_state("active_project_relative"))
        self.storage.set_state("active_project_relative", "sub/project")
        self.assertEqual(self.storage.get_state("active_project_relative"), "sub/project")
