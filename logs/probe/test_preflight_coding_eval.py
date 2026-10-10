"""CPU-only checks for the standalone evaluation preflight tool."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import preflight_coding_eval as preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "jarvis").mkdir()
        (self.root / "tests").mkdir()
        self.source = self.root / "jarvis/source.py"
        self.source.write_bytes(b"value = 1\r\n")
        self.test = self.root / "tests/test_source.py"
        self.test.write_text("raise RuntimeError('must never execute')\n", encoding="utf-8")
        self.tasks = self.root / "tasks.json"
        self.task = {"id": "one", "file": "jarvis/source.py", "test": "tests/test_source.py",
                     "find": "value = 1\n", "replace": "value = 2\n", "split": "test"}

    def inspect(self, tasks=None, split="all"):
        self.tasks.write_text(json.dumps(tasks or [self.task]), encoding="utf-8")
        before = {p: p.read_bytes() for p in (self.source, self.test, self.tasks)}
        report = preflight.inspect_tasks(self.root, self.tasks, split)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        return report

    def test_valid_crlf_source_is_checked_without_execution_or_writes(self):
        report = self.inspect()
        self.assertTrue(report["static_checks_passed"])
        self.assertFalse(report["tests_executed"])
        self.assertFalse(report["model_called"])
        self.assertEqual(report["tasks"][0]["source_sha256"], preflight.sha256(b"value = 1\r\n"))

    def test_combined_edits_use_the_result_of_the_previous_edit(self):
        self.task["edits"] = [["value = 1\n", "value = 2\n"], ["value = 2\n", "value = 3\n"]]
        report = self.inspect()
        self.assertTrue(report["static_checks_passed"])
        self.assertEqual(report["tasks"][0]["edits"], 2)

    def test_multiple_acceptance_files_are_checked_without_execution(self):
        (self.root / "tests/test_second.py").write_text("raise RuntimeError('never run')\n", encoding="utf-8")
        self.task["test"] += ",tests/test_second.py"
        report = self.inspect()
        self.assertTrue(report["static_checks_passed"])
        self.assertEqual(len(report["tasks"][0]["test_hashes"]), 2)
        self.assertFalse(report["tests_executed"])

    def test_missing_and_ambiguous_anchors_are_rejected(self):
        for source, count in [(b"value = 3\n", 0), (b"value = 1\nvalue = 1\n", 2)]:
            with self.subTest(count=count):
                self.source.write_bytes(source)
                report = self.inspect()
                self.assertFalse(report["static_checks_passed"])
                self.assertIn(f"matches {count}", report["tasks"][0]["issues"][0])

    def test_invalid_python_and_no_change_are_rejected(self):
        for replacement in ("value = (\n", "value = 1\n"):
            with self.subTest(replacement=replacement):
                self.task["replace"] = replacement
                self.assertFalse(self.inspect()["static_checks_passed"])

    def test_duplicate_ids_are_not_silently_collapsed(self):
        report = self.inspect([self.task, dict(self.task)])
        self.assertEqual(report["selected"], 2)
        self.assertEqual(report["passed"], 1)
        self.assertFalse(report["static_checks_passed"])

    def test_invalid_paths_and_edit_types_are_rejected(self):
        for changes in ({"file": "../outside.py"}, {"file": "C:/outside.py"},
                        {"edits": []}, {"replace": None}, {"find": ""}):
            with self.subTest(changes=changes):
                report = self.inspect([{**self.task, **changes}])
                self.assertFalse(report["static_checks_passed"])

    def test_split_selection_and_empty_selection(self):
        report = self.inspect([self.task, {**self.task, "id": "train", "split": "train"}], "test")
        self.assertEqual(report["selected"], 1)
        with self.assertRaisesRegex(ValueError, "no tasks selected"):
            self.inspect(split="train")

    def test_source_change_during_inspection_invalidates_the_report(self):
        self.tasks.write_text(json.dumps([self.task]), encoding="utf-8")
        original = Path.read_bytes
        changed = False

        def read(path):
            nonlocal changed
            data = original(path)
            if path == self.test and not changed:
                changed = True
                self.source.write_bytes(b"value = 9\n")
            return data

        with mock.patch.object(Path, "read_bytes", read):
            report = preflight.inspect_tasks(self.root, self.tasks)
        self.assertFalse(report["static_checks_passed"])
        self.assertEqual(report["changed_during_check"], [str(self.source)])

    def test_existing_report_cannot_be_overwritten(self):
        self.tasks.write_text(json.dumps([self.task]), encoding="utf-8")
        out = self.root / "existing.json"
        out.write_bytes(b"keep this report")
        argv = ["preflight", "--root", str(self.root), "--tasks-file", str(self.tasks), "--out", str(out)]
        with mock.patch("sys.argv", argv), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(preflight.main(), 2)
        self.assertEqual(out.read_bytes(), b"keep this report")


if __name__ == "__main__":
    unittest.main()
