"""refresh_mutation_tasks 의 옮기기 규칙(2026-10-26) — 시험·모델 없이 글만 본다."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from refresh_mutation_tasks import refresh_task  # noqa: E402

OLD = "def a():\n    return 1\n\n\ndef b(x):\n    if x:\n        return 2\n    return 3\n\n\ndef c():\n    return 4\n"
FIND = "def b(x):\n    if x:\n        return 2\n    return 3\n"


def task(find, replace):
    return {"id": "t", "file": "m.py", "test": "tests/test_m.py", "edits": [[find, replace]]}


class RefreshTests(unittest.TestCase):
    def test_a_present_anchor_is_kept_as_is(self):
        refreshed, note = refresh_task(task(FIND, FIND.replace("return 2", "return 0")), OLD)
        self.assertEqual((note, refreshed["edits"][0][0]), ("그대로", FIND))
        self.assertNotIn("refreshed", refreshed)

    def test_a_changed_neighbourhood_still_receives_the_same_change(self):
        source = OLD.replace("def b(x):\n", "def b(x):\n    \"\"\"새 설명\"\"\"\n")
        refreshed, note = refresh_task(task(FIND, FIND.replace("return 2", "return 0")), source)
        self.assertEqual(note, "옮김 1곳")
        find, replace = refreshed["edits"][0]
        self.assertIn('"""새 설명"""', replace)
        self.assertIn("return 0", replace)
        self.assertNotIn("return 2", replace)

    def test_a_deleted_block_takes_lines_added_inside_it_later(self):
        source = OLD.replace("    if x:\n        return 2\n", "    if x:\n        x = helper(x)\n        return 2\n")
        refreshed, _ = refresh_task(task("\n\n" + FIND, ""), source)
        mutated = source.replace(*refreshed["edits"][0], 1)
        self.assertNotIn("def b", mutated)
        self.assertNotIn("helper", mutated)
        self.assertIn("def c", mutated)
        compile(mutated, "m.py", "exec")

    def test_a_replaced_line_that_changed_since_is_not_guessed(self):
        source = OLD.replace("        return 2\n", "        return 22\n")
        refreshed, note = refresh_task(task(FIND, FIND.replace("return 2", "return 0")), source)
        self.assertIsNone(refreshed)
        self.assertEqual(note, "3-way 병합 충돌")

    def test_a_mutation_that_breaks_syntax_is_refused(self):
        refreshed, note = refresh_task(task(FIND, "def b(x:\n"), OLD)
        self.assertIsNone(refreshed)
        self.assertTrue(note.startswith("변이 뒤 문법 오류"))


if __name__ == "__main__":
    unittest.main()
