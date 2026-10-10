"""전체 검사를 파일 단위로 동시에 돌리고 하나의 결과로 합친다(2026-10-05) — 판정은 한 번에 돌린 것과 같아야 한다.

한 줄로 돌리면 ALEX 1086개에 약 197초, 앞쪽 5개 파일이 70%였다. 합친 결과는 기존 판독기(_suite_report)가 그대로 읽어야 하고,
한 파일이라도 읽을 수 없거나 끝나지 못하면 전체도 통과로 보지 않는다.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import developer  # noqa: E402
from jarvis.developer import _default_suite_runner, _merge_suite_outputs, _run_parallel_suite, _suite_report  # noqa: E402

PASS = "import unittest\n\nclass P(unittest.TestCase):\n    def test_one(self): pass\n    def test_two(self): pass\n"
FAIL = ("import unittest\n\nclass F(unittest.TestCase):\n    def test_bad(self): self.assertEqual(1, 2)\n"
        "    def test_err(self): raise RuntimeError('x')\n    def test_ok(self): pass\n")
SKIP = "import unittest\n\nclass S(unittest.TestCase):\n    @unittest.skip('없음')\n    def test_skip(self): pass\n"


def project(files):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "tests").mkdir()
    for name, body in files.items():
        (root / "tests" / name).write_text(body, encoding="utf-8")
    return tmp, root


class ParallelSuiteTests(unittest.TestCase):
    def test_redirected_unicode_output_is_utf8_in_every_child(self):
        body = 'print("한글 출력")\n' + PASS
        tmp, root = project({"test_a.py": body, "test_b.py": body})
        self.addCleanup(tmp.cleanup)
        with patch.dict(os.environ, {"PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii"}), \
                patch.object(developer, "SUITE_WORKERS", 2):
            result = _default_suite_runner(root)
        self.assertEqual(result[0], 0, result[1])
        self.assertIn("Ran 4 tests", result[1])

    def run_both(self, files):
        tmp, root = project(files)
        self.addCleanup(tmp.cleanup)
        parallel = _default_suite_runner(root)
        workers = developer.SUITE_WORKERS
        developer.SUITE_WORKERS = 1
        try:
            serial = _default_suite_runner(root)
        finally:
            developer.SUITE_WORKERS = workers
        return _suite_report(*parallel), _suite_report(*serial), parallel

    def test_failures_from_different_files_are_read_the_same_as_one_run(self):
        parallel, serial, raw = self.run_both({"test_pass.py": PASS, "test_fail.py": FAIL, "test_skip.py": SKIP})
        self.assertEqual(parallel[0], "failed")
        self.assertEqual(parallel[:2], serial[:2])  # 같은 실패 ID
        self.assertIn("test_bad (test_fail.F.test_bad)", parallel[1])
        self.assertRegex(raw[1], r"Ran 6 tests in [\d.]+s\n\nFAILED \(failures=1, errors=1, skipped=1\)")

    def test_all_passing_is_ok_and_an_all_skipped_file_does_not_spoil_it(self):
        parallel, serial, raw = self.run_both({"test_pass.py": PASS, "test_skip.py": SKIP})
        self.assertEqual((parallel[0], serial[0]), ("ok", "ok"))
        self.assertIn("OK (skipped=1)", raw[1])

    def test_a_file_that_cannot_be_read_makes_the_whole_run_unreadable(self):
        merged = _merge_suite_outputs([("test_a.py", 0, "Ran 2 tests in 0.1s\n\nOK\n"),
                                       ("test_b.py", 1, "갑자기 끝남(요약 없음)")], 1.0)
        self.assertEqual(_suite_report(*merged)[0], "unreadable")

    def test_a_file_that_did_not_finish_keeps_its_exit_code(self):
        merged = _merge_suite_outputs([("test_a.py", 0, "Ran 2 tests in 0.1s\n\nOK\n"),
                                       ("test_b.py", 124, "시간 초과")], 1.0)
        self.assertEqual(merged[0], 124)
        self.assertIn("test_b.py", merged[1])

    def test_custom_discovery_keeps_its_original_pattern_and_regression_test(self):
        hook = ('def load_tests(loader, tests, pattern):\n'
                '    return loader.discover("checks", pattern=pattern, top_level_dir=".")\n')
        tmp, root = project({"test_pass.py": PASS, "test_suite.py": hook})
        self.addCleanup(tmp.cleanup)
        (root / "checks").mkdir()
        (root / "checks" / "__init__.py").write_text("", encoding="utf-8")
        (root / "checks" / "test_regression.py").write_text(FAIL, encoding="utf-8")
        with patch.object(developer, "SUITE_WORKERS", 2):
            result = _default_suite_runner(root)
        self.assertEqual(result[0], 1)
        self.assertIn("test_bad (checks.test_regression.F.test_bad)", _suite_report(*result)[1])
        self.assertIn("Ran 5 tests", result[1])

    def test_no_executed_tests_never_returns_a_passing_exit_code(self):
        for body in ("import unittest\n", SKIP):
            with self.subTest(body=body):
                tmp, root = project({"test_a.py": body, "test_b.py": body})
                self.addCleanup(tmp.cleanup)
                with patch.object(developer, "SUITE_WORKERS", 2):
                    result = _default_suite_runner(root)
                self.assertEqual(result[0], 125)
                self.assertEqual(_suite_report(*result)[0], "unreadable")

    def test_load_tests_in_comments_and_strings_does_not_disable_parallel_runs(self):
        body = '# load_tests is documented here, not installed\nexample = "def load_tests(): pass"\n' + PASS
        tmp, root = project({"test_a.py": body, "test_b.py": PASS})
        self.addCleanup(tmp.cleanup)
        with patch.object(developer, "SUITE_WORKERS", 2), \
                patch.object(developer, "_run_parallel_suite", wraps=_run_parallel_suite) as parallel:
            result = _default_suite_runner(root)
        self.assertEqual(result[0], 0)
        parallel.assert_called_once()

    def test_queued_files_share_the_whole_suite_deadline(self):
        now = [0.0]
        limits = []

        def bounded(command, workspace, cancel_event, label, limit_seconds, **kwargs):
            limits.append(limit_seconds)
            now[0] += min(0.4, limit_seconds)
            if limit_seconds < 0.4:
                return 124, "시간 초과"
            return 0, "Ran 1 test in 0.4s\n\nOK\n"

        with patch.object(developer, "SUITE_WORKERS", 1), \
                patch.object(developer, "SUITE_LIMIT_SECONDS", 0.75), \
                patch.object(developer.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(developer, "_run_bounded", side_effect=bounded):
            result = _run_parallel_suite(Path("."), [f"test_{i}.py" for i in range(4)], None)
        self.assertEqual(result[0], 124)
        self.assertEqual(len(limits), 2)  # no new subprocess after the suite deadline
        self.assertAlmostEqual(limits[0], 0.75)
        self.assertAlmostEqual(limits[1], 0.35)


if __name__ == "__main__":
    unittest.main()
