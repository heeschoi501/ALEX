"""CPU-only release CLI checks: failed or absent tests must not become PASS."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import verify_release  # noqa: E402


class ReleaseSuiteTests(unittest.TestCase):
    def run_project(self, body: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            tests = project / "tests"
            tests.mkdir()
            (tests / "test_case.py").write_text(body, encoding="utf-8")
            return subprocess.run([sys.executable, str(ROOT / "scripts/run_suite.py"),
                                   "--workspace", str(project), "--workers", "2"],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_test_failure_reaches_cli_exit_code(self):
        result = self.run_project("import unittest\nclass T(unittest.TestCase):\n"
                                  "    def test_bad(self): self.assertEqual(1, 2)\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAILED (failures=1)", result.stdout)

    def test_no_tests_does_not_pass_release(self):
        result = self.run_project("# no tests\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Suite was not verified", result.stderr)

    def test_release_serial_option_is_forwarded(self):
        with patch.object(verify_release, "run", return_value=True) as run:
            self.assertEqual(verify_release.main(["--test-workers", "1"]), 0)
        calls = [call.args[1] for call in run.call_args_list if "scripts/run_suite.py" in call.args[1]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ["--workers", "1"])


if __name__ == "__main__":
    unittest.main()
