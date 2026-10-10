"""여러 파일 변경 묶음 — Coding Mode 4단계 2번 (2026-09-26 claude).

부분 수정(찾을 부분 → 바꿀 내용)·새 파일을 한 수정안으로 묶고, 모두 적용하거나 하나도 적용하지 않으며,
적용한 묶음을 통째로 되돌린다. 모델 출력은 텍스트로만 해석한다.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis.storage import Storage
from jarvis.tools import ToolError
from jarvis.workspace import SafeWorkspaceEditor, parse_change_blocks

MODEL_OUTPUT = """변경 설명은 무시된다.
FILE: calc.py
<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE

FILE: README.md
<<<<<<< SEARCH
v1
=======
v2
>>>>>>> REPLACE

NEW FILE: tests/test_calc.py
```python
<<<<<<< CONTENT
import unittest
from calc import add


class AddTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
>>>>>>> END
```
"""


class ParseTests(unittest.TestCase):
    def test_edits_and_new_files_are_read_and_prose_is_ignored(self):
        specs = parse_change_blocks(MODEL_OUTPUT)
        self.assertEqual([s["path"] for s in specs], ["calc.py", "README.md", "tests/test_calc.py"])
        self.assertEqual(specs[0]["edits"], [("def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b")])
        self.assertTrue(specs[2]["create"].startswith("import unittest\n"))

    def test_small_marker_slips_seen_from_gpt_oss_120b_are_accepted(self):
        """2026-09-26 실측: gpt-oss:120b 가 꺾쇠를 여섯 개 쓰고 파일 이름 다음에 --- 를 넣었다. 내용은 맞았다."""
        raw = ("FILE: /calc.py\n---\n<<<<<< SEARCH\ndef add(a, b):\n    return a - b\n======\n"
               "def add(a, b):\n    return a + b\n>>>>>> REPLACE\n\nNEW FILE: ./tests/test_mul.py\n---\n"
               "<<<<<< CONTENT\nimport unittest\n>>>>>> END\n")
        specs = parse_change_blocks(raw)
        self.assertEqual([s["path"] for s in specs], ["calc.py", "tests/test_mul.py"])
        self.assertEqual(specs[0]["edits"][0][1], "def add(a, b):\n    return a + b")
        self.assertEqual(specs[1]["create"], "import unittest\n")

    def test_broken_or_missing_blocks_are_refused(self):
        for text in ("그냥 설명만 있다", "FILE: a.py\n<<<<<<< SEARCH\nx\n=======\ny\n",
                     "<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE", "NEW FILE: a.py\nprint(1)\n"):
            with self.subTest(text=text[:20]), self.assertRaises(ToolError):
                parse_change_blocks(text)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "project"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.root / "README.md").write_text("# calc\nv1\n", encoding="utf-8")
        self.backups = base / "backups"
        self.db = base / "edits.db"
        self.editor = SafeWorkspaceEditor(self.root, self.backups, storage=Storage(self.db))

    def tearDown(self):
        self.temp.cleanup()

    def read(self, name):
        return (self.root / name).read_text(encoding="utf-8")

    def test_bundle_applies_every_file_and_can_be_undone_whole(self):
        bundle = self.editor.create_bundle(parse_change_blocks(MODEL_OUTPUT))
        self.assertIn("+++ b/tests/test_calc.py", bundle.diff)
        self.assertIn("--- /dev/null", bundle.diff)
        self.assertIn("-    return a - b", bundle.diff)
        self.assertEqual(self.read("calc.py"), "def add(a, b):\n    return a - b\n")  # 만들기만 해서는 안 바뀐다
        targets, backup = self.editor.apply_bundle(bundle.proposal_id)
        self.assertEqual(len(targets), 3)
        self.assertIn("return a + b", self.read("calc.py"))
        self.assertEqual(self.read("README.md"), "# calc\nv2\n")
        self.assertTrue((self.root / "tests" / "test_calc.py").is_file())
        self.assertEqual((backup / "calc.py").read_text(encoding="utf-8"), "def add(a, b):\n    return a - b\n")
        self.assertIsNone(self.editor.get_bundle(bundle.proposal_id))  # 적용하면 수정안은 사라진다
        restored = self.editor.undo_bundle(bundle.proposal_id)
        self.assertEqual(len(restored), 3)
        self.assertEqual(self.read("calc.py"), "def add(a, b):\n    return a - b\n")
        self.assertEqual(self.read("README.md"), "# calc\nv1\n")
        self.assertFalse((self.root / "tests" / "test_calc.py").exists())
        with self.assertRaisesRegex(ToolError, "찾지 못했습니다"):
            self.editor.undo_bundle(bundle.proposal_id)  # 두 번 되돌리지 않는다

    def test_a_failure_in_the_middle_puts_every_file_back(self):
        bundle = self.editor.create_bundle(parse_change_blocks(MODEL_OUTPUT))
        real_replace, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2:  # 두 번째 파일을 바꾸다 실패
                raise OSError("disk full")
            return real_replace(src, dst)

        with mock.patch("jarvis.workspace.os.replace", side_effect=flaky):
            with self.assertRaisesRegex(ToolError, "모두 되돌렸습니다"):
                self.editor.apply_bundle(bundle.proposal_id)
        self.assertEqual(self.read("calc.py"), "def add(a, b):\n    return a - b\n")
        self.assertEqual(self.read("README.md"), "# calc\nv1\n")
        self.assertFalse((self.root / "tests" / "test_calc.py").exists())
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".")], [])  # 임시 파일 없음
        self.assertIsNotNone(self.editor.get_bundle(bundle.proposal_id))  # 다시 시도할 수 있다

    def test_a_file_changed_after_the_proposal_blocks_the_whole_bundle(self):
        bundle = self.editor.create_bundle(parse_change_blocks(MODEL_OUTPUT))
        (self.root / "README.md").write_text("# calc\nv1\n사용자가 고침\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "README.md.*바뀌었습니다"):
            self.editor.apply_bundle(bundle.proposal_id)
        self.assertIn("return a - b", self.read("calc.py"))
        self.assertFalse((self.root / "tests" / "test_calc.py").exists())

    def test_undo_refuses_when_a_file_changed_after_apply(self):
        bundle = self.editor.create_bundle(parse_change_blocks(MODEL_OUTPUT))
        self.editor.apply_bundle(bundle.proposal_id)
        (self.root / "calc.py").write_text("def add(a, b):\n    return a + b  # 사용자가 이어서 고침\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "calc.py.*바뀌어"):
            self.editor.undo_bundle(bundle.proposal_id)
        self.assertEqual(self.read("README.md"), "# calc\nv2\n")  # 아무것도 되돌리지 않았다

    def test_bundles_survive_a_restart_and_single_file_lookups_ignore_them(self):
        bundle = self.editor.create_bundle(parse_change_blocks(MODEL_OUTPUT))
        reopened = SafeWorkspaceEditor(self.root, self.backups, storage=Storage(self.db))
        self.assertEqual(reopened.get_bundle(bundle.proposal_id).diff, bundle.diff)
        self.assertIsNone(reopened.get(bundle.proposal_id))
        self.assertTrue(reopened.discard(bundle.proposal_id))
        self.assertIsNone(reopened.get_bundle(bundle.proposal_id))

    def test_proposals_that_cannot_be_applied_exactly_are_refused_up_front(self):
        cases = [
            ("FILE: calc.py\n<<<<<<< SEARCH\nreturn a * b\n=======\nx\n>>>>>>> REPLACE", "찾지 못했습니다"),
            ("FILE: README.md\n<<<<<<< SEARCH\nc\n=======\nd\n>>>>>>> REPLACE", "2곳"),  # 'calc' 에 c 가 두 번 — 한 곳이어야 한다
            ("FILE: calc.py\n<<<<<<< SEARCH\n    return a - b\n=======\n    return (a +\n>>>>>>> REPLACE", "문법 오류"),
            ("NEW FILE: calc.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END", "이미 있는 파일"),
            ("NEW FILE: ../escape.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END", "상대 경로"),
            ("NEW FILE: a/b/c/d/mod.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END", "3단계까지만"),
            ("NEW FILE: README.md/mod.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END", "폴더 자리에 파일"),
            ("NEW FILE: pkg/.hidden/mod.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END", "숨김"),
            ("NEW FILE: tool.exe\n<<<<<<< CONTENT\nx\n>>>>>>> END", "확장자"),
            ("NEW FILE: .env.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END", "숨김"),
        ]
        for text, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ToolError, message):
                self.editor.create_bundle(parse_change_blocks(text))
        self.assertIn("return a - b", self.read("calc.py"))

    def test_an_empty_search_appends_to_the_end_of_the_file(self):
        """gpt-oss:120b 실측: 새 함수를 빈 SEARCH 블록으로 덧붙였다."""
        bundle = self.editor.create_bundle(parse_change_blocks(
            "FILE: calc.py\n<<<<<<< SEARCH\n=======\ndef mul(a, b):\n    return a * b\n>>>>>>> REPLACE"))
        self.editor.apply_bundle(bundle.proposal_id)
        self.assertEqual(self.read("calc.py"),
                         "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n")
        with self.assertRaisesRegex(ToolError, "모두 비어"):
            self.editor.create_bundle(parse_change_blocks("FILE: README.md\n<<<<<<< SEARCH\n=======\n>>>>>>> REPLACE"))

    def test_new_folders_are_made_and_removed_again_by_undo_and_rollback(self):
        text = ("NEW FILE: pkg/sub/mod.py\n<<<<<<< CONTENT\nVALUE = 1\n>>>>>>> END\n"
                "FILE: README.md\n<<<<<<< SEARCH\nv1\n=======\nv2\n>>>>>>> REPLACE\n")
        bundle = self.editor.create_bundle(parse_change_blocks(text))
        self.assertFalse((self.root / "pkg").exists())  # 만들기만 해서는 폴더도 안 생긴다
        self.editor.apply_bundle(bundle.proposal_id)
        self.assertEqual((self.root / "pkg" / "sub" / "mod.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        restored = self.editor.undo_bundle(bundle.proposal_id)
        self.assertFalse((self.root / "pkg").exists())
        self.assertIn("삭제(새로 만들었던 폴더): pkg", "\n".join(restored).replace("\\", "/"))
        # 중간 실패 때도 새 폴더를 지운다
        again = self.editor.create_bundle(parse_change_blocks(text))
        real_replace, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_replace(src, dst)

        with mock.patch("jarvis.workspace.os.replace", side_effect=flaky):
            with self.assertRaisesRegex(ToolError, "모두 되돌렸습니다"):
                self.editor.apply_bundle(again.proposal_id)
        self.assertFalse((self.root / "pkg").exists())
        self.assertEqual(self.read("README.md"), "# calc\nv1\n")

    def test_an_empty_new_file_is_fine_but_emptying_an_existing_file_is_not(self):
        """2026-09-26 실측: 두 모델 모두 새 패키지의 빈 __init__.py 를 만들다 거부당했다."""
        bundle = self.editor.create_bundle(parse_change_blocks(
            "NEW FILE: pkg/__init__.py\n<<<<<<< CONTENT\n>>>>>>> END\n"
            "NEW FILE: pkg/ops.py\n<<<<<<< CONTENT\ndef mul(a, b):\n    return a * b\n>>>>>>> END\n"))
        self.editor.apply_bundle(bundle.proposal_id)
        self.assertEqual((self.root / "pkg" / "__init__.py").read_text(encoding="utf-8").strip(), "")
        with self.assertRaisesRegex(ToolError, "빈 파일"):
            self.editor.create_bundle(parse_change_blocks(
                "FILE: README.md\n<<<<<<< SEARCH\n# calc\nv1\n=======\n\n>>>>>>> REPLACE"))

    def test_undo_keeps_a_new_folder_the_user_has_put_files_in(self):
        bundle = self.editor.create_bundle(parse_change_blocks("NEW FILE: pkg/mod.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END\n"))
        self.editor.apply_bundle(bundle.proposal_id)
        (self.root / "pkg" / "notes.txt").write_text("사용자 메모", encoding="utf-8")
        restored = self.editor.undo_bundle(bundle.proposal_id)
        self.assertFalse((self.root / "pkg" / "mod.py").exists())
        self.assertTrue((self.root / "pkg" / "notes.txt").is_file())
        self.assertIn("남김", "\n".join(restored))

    def test_windows_line_endings_are_kept(self):
        (self.root / "win.py").write_bytes(b"a = 1\r\nb = 2\r\n")
        bundle = self.editor.create_bundle(parse_change_blocks(
            "FILE: win.py\n<<<<<<< SEARCH\na = 1\nb = 2\n=======\na = 10\nb = 20\n>>>>>>> REPLACE"))
        self.editor.apply_bundle(bundle.proposal_id)
        self.assertEqual((self.root / "win.py").read_bytes(), b"a = 10\r\nb = 20\r\n")

    def test_a_file_changed_while_the_model_was_writing_is_refused(self):
        with self.assertRaisesRegex(ToolError, "바뀌었습니다"):
            self.editor.create_bundle(parse_change_blocks(MODEL_OUTPUT), expected_originals={"calc.py": "옛 내용\n"})


class BundleCommandTests(unittest.TestCase):
    """채팅 명령으로: /edit-files → /apply-edit → /undo-bundle, 그리고 /discard-edit. 모델은 가짜(블록만 돌려준다)."""

    def setUp(self):
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider, MockLLMProvider

        class BlockModel(MockLLMProvider):
            def generate(self, messages, **kwargs):
                self.seen = messages
                return MODEL_OUTPUT

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "project"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.root / "README.md").write_text("# calc\nv1\n", encoding="utf-8")
        storage = Storage(base / "alex.db")
        self.model = BlockModel("coder", "CODER")
        self.core = JarvisCore(
            storage=storage,
            hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("general", "GENERAL"), local_coder=self.model,
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            workspace_editor=SafeWorkspaceEditor(self.root, base / "backups", storage=storage),
        )
        self.session = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp.cleanup()

    def run_command(self, text):
        from jarvis.app import handle_command
        return handle_command(self.core, self.session, text)[0]

    def test_propose_apply_and_undo_from_chat(self):
        reply = self.run_command("/edit-files calc.py, README.md :: add 를 고치고 테스트를 더해")
        proposal_id = re.search(r"^편집 제안 ID: ([0-9a-f]{12})$", reply, re.M).group(1)  # 화면의 적용 버튼이 이 줄을 찾는다
        self.assertIn("새 파일: tests", reply)
        self.assertIn("return a - b", "".join(m.content for m in self.model.seen))  # 모델은 파일 내용을 받았다
        self.assertIn("return a - b", (self.root / "calc.py").read_text(encoding="utf-8"))  # 아직 안 바뀐다
        applied = self.run_command(f"/apply-edit {proposal_id} --yes")
        self.assertIn("묶음 적용 완료 — 파일 3개", applied)
        self.assertIn("return a + b", (self.root / "calc.py").read_text(encoding="utf-8"))
        undone = self.run_command(f"/undo-bundle {proposal_id} --yes")
        self.assertIn("삭제(새로 만들었던 파일)", undone)
        self.assertFalse((self.root / "tests" / "test_calc.py").exists())
        self.assertIn("return a - b", (self.root / "calc.py").read_text(encoding="utf-8"))

    def test_discard_and_confirmation_are_required(self):
        reply = self.run_command("/edit-files calc.py, README.md :: 고쳐")
        proposal_id = re.search(r"^편집 제안 ID: ([0-9a-f]{12})$", reply, re.M).group(1)
        self.assertIn("확인이 필요합니다", self.run_command(f"/apply-edit {proposal_id}"))
        self.assertIn("확인이 필요합니다", self.run_command(f"/undo-bundle {proposal_id}"))
        self.assertEqual(self.run_command(f"/discard-edit {proposal_id}"), "편집 제안을 폐기했습니다.")
        self.assertIn("찾을 수 없습니다", self.run_command(f"/apply-edit {proposal_id} --yes"))
        self.assertIn("return a - b", (self.root / "calc.py").read_text(encoding="utf-8"))


class LocalEditRobustnessTests(BundleCommandTests):
    """2026-09-30 실측(/dev-auto 평가 developer 과제): 한 회차는 시간 초과, 한 회차는 변경 블록 없는 설명만 — 둘 다 막는다."""

    def setUp(self):
        super().setUp()
        self.replies = []
        self.calls = []

        def generate(messages, **kwargs):
            self.calls.append((list(messages), kwargs))
            self.model.seen = messages  # 물려받은 BundleCommandTests 시험도 그대로 돈다
            return self.replies.pop(0) if self.replies else MODEL_OUTPUT

        self.model.generate = generate

    def test_edit_proposals_are_streamed_so_a_long_answer_is_not_cut_by_the_timeout(self):
        self.run_command("/edit-files calc.py, README.md :: add 를 고쳐")
        self.assertTrue(callable(self.calls[0][1].get("on_chunk")))  # 스트리밍 경로 — 토큰이 오는 한 시간 제한에 걸리지 않는다

    def test_a_reply_without_blocks_is_asked_again_once(self):
        self.replies = ["원인은 add 의 부호입니다. 고치면 됩니다."]  # 설명만, 블록 없음
        reply = self.run_command("/edit-files calc.py, README.md :: add 를 고쳐")
        self.assertIn("편집 제안 ID:", reply)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("직전 답에 변경 블록이 없거나", self.calls[1][0][-1].content)

    def test_thinking_that_uses_up_the_budget_is_retried_once_without_thinking(self):
        from jarvis.providers import ContextExhausted
        self.model.think = True
        thinking = []

        def generate(messages, **kwargs):
            thinking.append(self.model.think)
            self.model.seen = messages
            if self.model.think:
                raise ContextExhausted("답을 쓰기 전에 출력 한도 6144토큰을 생각(thinking)에 다 썼습니다.")
            return MODEL_OUTPUT

        self.model.generate = generate
        self.assertIn("편집 제안 ID:", self.run_command("/edit-files calc.py, README.md :: add 를 고쳐"))
        self.assertEqual(thinking, [True, False])  # 생각 켜고 한 번, 끄고 한 번
        self.assertTrue(self.model.think)  # 대화용 설정은 되돌린다

    def test_the_same_model_behind_coder_and_general_is_not_asked_twice(self):
        from jarvis.providers import ProviderError
        general = self.core.providers[next(d for d in self.core.providers if d.value == "local_general")]
        for provider in (self.model, general):
            provider.model, provider.host = "qwen3.8:27b", "http://127.0.0.1:11434"
        asked = []

        def fail(messages, **kwargs):
            asked.append(1)
            raise ProviderError("시간 초과")

        self.model.generate = fail
        general.generate = fail
        reply = self.run_command("/edit-files calc.py, README.md :: add 를 고쳐")
        self.assertIn("편집안을 만들지 못했습니다", reply)
        self.assertEqual(len(asked), 1)

    def test_a_second_reply_without_blocks_is_refused_and_nothing_changes(self):
        self.replies = ["설명만.", "또 설명만."]
        reply = self.run_command("/edit-files calc.py, README.md :: add 를 고쳐")
        self.assertIn("변경 블록", reply)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("return a - b", (self.root / "calc.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class LooseSearchTests(unittest.TestCase):
    """찾을 부분이 공백·들여쓰기·빈 줄만 다르면 한 곳과 맞을 때 받는다(ADR-079) — 27b 거부 30회 중 9회가 '찾을 부분 없음'."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.editor = SafeWorkspaceEditor(self.root, Path(self.temp.name) / "backups")

    def bundle(self, name, text, blocks):
        (self.root / name).write_text(text, encoding="utf-8")
        bundle = self.editor.create_bundle(parse_change_blocks(blocks))
        self.editor.apply_bundle(bundle.proposal_id)
        return (self.root / name).read_text(encoding="utf-8")

    def test_trailing_spaces_and_a_shifted_indent_are_forgiven(self):
        text = "class C:\n    def f(self):\n        x = 1   \n        return x\n"
        result = self.bundle("c.py", text, "FILE: c.py\n<<<<<<< SEARCH\ndef f(self):\n    x = 1\n    return x\n=======\n"
                                           "def f(self):\n    x = 2\n    return x\n>>>>>>> REPLACE\n")
        self.assertEqual(result, "class C:\n    def f(self):\n        x = 2\n        return x\n")  # 바꿀 내용도 4칸 옮겨졌다

    def test_a_missing_blank_line_is_forgiven(self):
        text = "def area(width, height):\n    if width < 0:\n        return 0\n\n    return width + height\n"
        result = self.bundle("shapes.py", text, "FILE: shapes.py\n<<<<<<< SEARCH\n    if width < 0:\n        return 0\n"
                                                "    return width + height\n=======\n    if width < 0:\n        return 0\n"
                                                "    return width * height\n>>>>>>> REPLACE\n")
        self.assertIn("    return width * height\n", result)
        self.assertNotIn("width + height", result)

    def test_two_loose_places_are_refused_with_a_hint(self):
        (self.root / "two.py").write_text("def f():\n    x = 1\n    return x\n\n\ndef g():\n    x = 1\n    return x\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "공백·빈 줄을 무시하면 2곳"):
            self.editor.create_bundle(parse_change_blocks(
                "FILE: two.py\n<<<<<<< SEARCH\nx = 1\nreturn x\n=======\nx = 2\nreturn x\n>>>>>>> REPLACE\n"))

    def test_an_uneven_indent_is_not_guessed(self):
        (self.root / "ab.py").write_text("def f():\n    a = 1\n    b = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "찾지 못했습니다"):
            self.editor.create_bundle(parse_change_blocks(
                "FILE: ab.py\n<<<<<<< SEARCH\na = 1\n        b = 2\n=======\na = 1\nb = 3\n>>>>>>> REPLACE\n"))
