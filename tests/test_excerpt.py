"""큰 파일 발췌 — Coding Mode 4단계 (2026-09-26 claude, ADR-067).

모델 문맥 예산보다 큰 파일도 고칠 곳 주변만 보여 주고 '찾을 부분 → 바꿀 내용' 묶음으로 고치게 한다. 찾을 부분은 파일 전체에서
정확히 한 곳과 맞아야 적용되므로, 보여 주지 않은 곳을 지어낸 수정안은 적용 전에 거부된다.
"""
import re
import tempfile
import unittest
from pathlib import Path

from jarvis.excerpt import GAP, excerpt, fit_files
from jarvis.storage import Storage
from jarvis.tools import ToolError
from jarvis.workspace import SafeWorkspaceEditor


def big_module(functions=60):
    """helper_0 … helper_N 과 여러 줄 상수, 버그가 든 함수 하나가 있는 큰 파이썬 파일."""
    parts = ['"""big module."""\nfrom __future__ import annotations\n\nimport math\n\n',
             'LIMITS = (\n    "alpha",\n    "beta",\n)\n\n']
    for number in range(functions):
        body = "".join(f"    value = value * {k} + {number}  # filler line {k}\n" for k in range(6))
        parts.append(f"\ndef helper_{number}(value):\n{body}    return value\n\n")
    parts.append('\ndef area(width, height):\n    """넓이."""\n    return width + height\n')
    return "".join(parts)


TRACEBACK = '''FAIL: test_area (test_shapes.ShapeTests.test_area)
Traceback (most recent call last):
  File "C:\\\\Temp\\\\alex-dev-auto-x\\\\project\\\\tests\\\\test_shapes.py", line 9, in test_area
    self.assertEqual(area(2, 3), 6)
AssertionError: 5 != 6
'''


class ExcerptTests(unittest.TestCase):
    def test_the_function_named_in_the_failure_is_shown_whole_and_the_rest_is_marked_as_left_out(self):
        text = big_module()
        view = excerpt("shapes.py", text, TRACEBACK, 3000)
        self.assertLessEqual(len(view), 3000)
        self.assertIn('def area(width, height):\n    """넓이."""\n    return width + height', view)
        self.assertIn("import math", view)  # 파일 머리
        self.assertNotIn("filler line 3", view)  # 다른 함수 본문은 빠진다
        self.assertRegex(view, r"⋯ 줄 \d+–\d+ 생략 ⋯")
        self.assertLess(len(view), len(text) // 5)

    def test_a_traceback_line_in_this_file_pulls_in_its_function(self):
        text = big_module()
        line = text.splitlines().index("def helper_40(value):") + 3
        hints = f'  File "C:\\\\x\\\\project\\\\pkg\\\\shapes.py", line {line}, in helper_40\n'
        view = excerpt("pkg/shapes.py", text, hints, 3000)
        self.assertIn("def helper_40(value):\n    value = value * 0 + 40", view)
        self.assertIn("    return value", view)
        self.assertNotIn("value * 0 + 39", view)
        # 다른 파일의 같은 이름 줄 번호는 쓰지 않는다
        other = excerpt("shapes.py", text, f'  File "C:\\\\x\\\\project\\\\other_shapes.py", line {line}, in f\n', 3000)
        self.assertNotIn("value * 0 + 40", other)

    def test_a_named_multi_line_constant_is_shown_whole(self):
        view = excerpt("shapes.py", big_module(), "LIMITS 에 gamma 를 더해라", 3000)
        self.assertIn('LIMITS = (\n    "alpha",\n    "beta",\n)', view)

    def test_the_left_over_room_holds_an_outline_of_the_other_definitions(self):
        view = excerpt("shapes.py", big_module(), TRACEBACK, 6000)
        self.assertIn("def helper_0(value):", view)
        self.assertIn("def helper_59(value):", view)
        self.assertNotIn("filler", view)

    def test_a_function_sharing_rare_words_with_the_failure_comes_before_the_rest(self):
        """실측(2026-09-26): 트레이스백이 테스트 파일에서만 끝나 고칠 함수 이름이 안 나오는 실패가 흔하다."""
        text = big_module() + ('\n\ndef forget_exchange(state, target):\n    """지운 대화의 질문은 잊는다."""\n'
                               '    if state.get("session") == target:\n        state.clear()\n')
        hints = "FAIL: test_deleting_forgets_the_exchange\nAssertionError: 지운 대화의 질문이 남았다"
        view = excerpt("shapes.py", text, hints, 1500)
        self.assertIn('    if state.get("session") == target:', view)
        self.assertNotIn("filler", view)

    def test_non_python_text_still_works(self):
        text = "".join(f"function f{n}() {{\n  return {n};\n}}\n" for n in range(300))
        view = excerpt("app.js", text, "", 800)
        self.assertLessEqual(len(view), 800)
        self.assertIn(GAP.split("{")[0], view)

    def test_fit_files_keeps_small_files_whole_and_refuses_when_there_is_no_room(self):
        small = "def add(a, b):\n    return a - b\n"
        body, excerpted = fit_files({"calc.py": small, "shapes.py": big_module()}, 4000, TRACEBACK)
        self.assertEqual(excerpted, ["shapes.py"])
        self.assertIn(small, body)
        self.assertIn("[발췌 — 전체", body)
        self.assertLessEqual(len(body), 4000)
        self.assertLess(body.index("FILE: calc.py"), body.index("FILE: shapes.py"))  # 원래 순서
        self.assertIsNone(fit_files({"shapes.py": big_module()}, 500, TRACEBACK))


class ExcerptBundleTests(unittest.TestCase):
    """문맥 예산(8,000자)보다 큰 파일도 발췌로 묶음 수정안을 만든다."""

    def setUp(self):
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider, MockLLMProvider

        test = self

        class BlockModel(MockLLMProvider):
            def generate(self, messages, **kwargs):
                test.seen = "".join(m.content for m in messages)
                return test.reply

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "project"
        self.root.mkdir()
        self.text = big_module(120)
        (self.root / "shapes.py").write_text(self.text, encoding="utf-8")
        storage = Storage(base / "alex.db")
        self.core = JarvisCore(
            storage=storage,
            hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("general", "GENERAL"), local_coder=BlockModel("coder", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            workspace_editor=SafeWorkspaceEditor(self.root, base / "backups", storage=storage),
        )
        self.session = self.core.get_or_create_session(False)
        self.assertGreater(len(self.text), self.core.context_char_budget * 2)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_file_bigger_than_the_budget_is_excerpted_and_the_fix_applies(self):
        self.reply = ("FILE: shapes.py\n<<<<<<< SEARCH\n    return width + height\n=======\n"
                      "    return width * height\n>>>>>>> REPLACE\n")
        bundle = self.core.propose_bundle_local(self.session, ["shapes.py"], "테스트 실패를 고쳐라\n" + TRACEBACK)
        self.assertIn("return width + height", self.seen)
        self.assertIn("생략 ⋯", self.seen)  # 발췌 표시와 규칙
        self.assertIn("표시 줄은 파일에 없다", self.seen)
        self.assertNotIn("value * 5 + 100", self.seen)  # 다른 함수 본문은 모델에게 가지 않았다
        self.assertLessEqual(len(self.seen), self.core.context_char_budget + 200)
        self.core.workspace_editor.apply_bundle(bundle.proposal_id)
        self.assertIn("return width * height", (self.root / "shapes.py").read_text(encoding="utf-8"))

    def test_a_search_made_up_from_the_hidden_part_is_refused(self):
        self.reply = ("FILE: shapes.py\n<<<<<<< SEARCH\n    return value * 2\n=======\n"
                      "    return value\n>>>>>>> REPLACE\n")
        with self.assertRaisesRegex(ToolError, "찾지 못했습니다"):
            self.core.propose_bundle_local(self.session, ["shapes.py"], "고쳐라\n" + TRACEBACK)
        self.assertEqual((self.root / "shapes.py").read_text(encoding="utf-8"), self.text)


class DeveloperExcerptTests(unittest.TestCase):
    """개발 작업: 모델에게는 실패 부분만, 전체를 다시 쓰기엔 큰 파일 하나는 묶음(발췌) 수정안으로."""

    def setUp(self):
        from types import SimpleNamespace

        from jarvis.developer import DeveloperMode

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        (self.root / "shapes.py").write_text(big_module(), encoding="utf-8")
        (self.root / "tests" / "test_shapes.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\nfrom shapes import area\n"
            "class ShapeTests(unittest.TestCase):\n"
            + "".join(f"    def test_pass_{n}(self): self.assertTrue(True)\n" for n in range(40))
            + "    def test_area(self): self.assertEqual(area(2, 3), 6)\n", encoding="utf-8")
        self.store = Storage(self.root / "data" / "app.db")
        self.editor = SafeWorkspaceEditor(self.root, self.root / "backups", storage=self.store)
        self.instructions = []

        def propose_bundle(_session, paths, instruction, **_kwargs):
            self.instructions.append(instruction)
            return self.editor.create_bundle([{"path": "shapes.py", "edits": [
                ("    return width + height\n", "    return width * height\n")]}])

        def whole_file_edit(*_args):
            raise AssertionError("큰 파일을 통째로 다시 쓰게 하면 안 된다")

        self.core = SimpleNamespace(
            storage=self.store, workspace_editor=self.editor, current_project_root=lambda: self.root,
            propose_file_edit_local=whole_file_edit, propose_bundle_local=propose_bundle,
            fits_whole_file_edit=lambda _path, _instruction: False,
            record_edit_experience=lambda *args, **kwargs: None,
        )
        self.mode = DeveloperMode(self.core)

    def test_a_single_file_too_big_to_rewrite_gets_a_bundle_and_the_model_sees_only_the_failure(self):
        self.mode.start("shapes.py", "tests/test_shapes.py", "넓이를 고쳐라")
        reply = self.mode.next("session")
        self.assertIn("테스트 실패", reply)
        proposal_id = re.search(r"^편집 제안 ID: ([0-9a-f]{12})$", reply, re.M).group(1)
        instruction = self.instructions[-1]
        self.assertNotIn("... ok", instruction)
        self.assertNotIn("test_pass_3 (", instruction)
        self.assertIn("FAIL: test_area", instruction)
        self.assertIn("AssertionError: 5 != 6", instruction)
        self.assertIn("def test_area(self): self.assertEqual(area(2, 3), 6)", instruction)  # 실패한 테스트 코드(읽기 전용)
        self.assertNotIn("test_pass_39(self): self.assertTrue(True)\n    def test_pass_38", instruction)  # 큰 테스트 파일은 발췌
        self.editor.apply_bundle(proposal_id)
        self.mode.approved(proposal_id)  # /apply-edit 이 하는 일
        self.assertIn("테스트 통과", self.mode.next("session"))

    def test_the_failure_digest_keeps_headers_tracebacks_and_the_summary(self):
        from jarvis.developer import _failure_digest

        output = ("test_a (t.T.test_a) ... ok\ntest_b (t.T.test_b)\n문서 한 줄 ... ok\ntest_c (t.T.test_c) ... skipped 'x'\n"
                  "test_d (t.T.test_d) ... FAIL\n\n======\nFAIL: test_d (t.T.test_d)\n------\nTraceback (most recent call last):\n"
                  '  File "t.py", line 3, in test_d\nAssertionError: 1 != 2\n\n------\nRan 4 tests in 0.001s\n\nFAILED (failures=1)')
        digest = _failure_digest(output)
        self.assertNotIn("test_a", digest)
        self.assertNotIn("test_b", digest)
        self.assertNotIn("skipped", digest)
        for kept in ("test_d (t.T.test_d) ... FAIL", "FAIL: test_d", 'File "t.py", line 3', "AssertionError: 1 != 2", "FAILED (failures=1)"):
            self.assertIn(kept, digest)
        self.assertEqual(_failure_digest("테스트 실행 실패: OSError"), "테스트 실행 실패: OSError")


if __name__ == "__main__":
    unittest.main()
