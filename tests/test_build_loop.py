"""만들기 루프 — 쓰고, 실행하고, 고친다. 모델 없이 대본대로 움직이는 가짜 모델로 루프의 규칙을 검사한다.

2026-10-05 테트리스: 한 번에 써서 넘긴 코드는 회전이 틀렸고, 고쳐 달라는 말에 실행 없이 추측으로 답했다.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import build_loop  # noqa: E402
from jarvis.build_loop import (BuildWorkspace, answer_text, is_build_followup, is_build_request,  # noqa: E402
                               run_build, unsafe_reason)
from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import GenerationCancelled, MockLLMProvider, ProviderError, ThinkingTooLong  # noqa: E402
from jarvis.router import Destination  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED_OLD, FIXED_NEW = "return a - b", "return a + b"
TEST = "from logic import add\nassert add(2, 3) == 5, add(2, 3)\nprint('OK')\n"


def step(content="", *calls):
    return content, list(calls)


class ScriptedModel:
    """대본대로 도구를 부르는 가짜 코딩 모델(OllamaProvider 의 _collect_stream·last_tool_calls 와 같은 모양)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []
        self.last_tool_calls = []
        self.last_metrics = {}
        self.think = False
        self.cancel_event = None
        self.model, self.num_ctx = "fake-coder", 16384
        self.one_shot = 0

    def health_check(self):
        return True

    def _collect_stream(self, messages, callback, tools=None):
        self.seen.append([(m.role, m.content, m.tool_calls) for m in messages])
        content, calls = self.steps.pop(0) if self.steps else ("", [])
        self.last_tool_calls = [{"function": {"name": name, "arguments": args}} for name, args in calls]
        return content

    def generate(self, messages, **kwargs):
        self.one_shot += 1
        return "한 번에 쓴 답"


def good_script():
    return [
        step("", ("write_file", {"path": "logic.py", "content": BUGGY}),
             ("write_file", {"path": "test_logic.py", "content": TEST})),
        step("", ("run_python", {"path": "test_logic.py"})),
        step("", ("edit_file", {"path": "logic.py", "old": FIXED_OLD, "new": FIXED_NEW})),
        step("", ("run_python", {"path": "test_logic.py"})),
        step("", ("finish", {"summary": "덧셈을 만들고 검사로 확인했습니다.", "main_file": "logic.py"})),
    ]


class SafetyTests(unittest.TestCase):
    def test_dangerous_code_is_named(self):
        for source, expected in (("import subprocess\nsubprocess.run(['x'])", "subprocess"),
                                 ("import os\nos.remove('a.txt')", "os.remove"),
                                 ("import shutil", "shutil"),
                                 ("from pathlib import Path\nPath('a').unlink()", "unlink"),
                                 ("open('C:/Windows/x.txt', 'w')", "작업 폴더 밖"),
                                 ("open('../x.txt')", "작업 폴더 밖"),
                                 ("__import__('os')", "__import__")):
            with self.subTest(source=source):
                self.assertIn(expected, unsafe_reason(source) or "")

    def test_ordinary_program_code_runs(self):
        for source in ("import pygame, random\ns = 'a-b'.replace('-', '+')\nprint(eval('1+2'))",
                       "with open('scores.txt', 'w') as f:\n    f.write('1')",
                       "import os\nprint(os.path.join('a', 'b'))", "def broken(:"):
            with self.subTest(source=source):
                self.assertIsNone(unsafe_reason(source))

    def test_paths_stay_inside_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            for bad in ("C:/x.py", "/etc/x.py", "../x.py", "a/../../x.py", "tool.exe", ""):
                with self.subTest(path=bad), self.assertRaises(ValueError):
                    workspace.resolve(bad)
            self.assertIn("저장", workspace.write("game/logic.py", "x = 1\n"))
            self.assertEqual(workspace.files(), ["game/logic.py"])

    def test_a_path_repeating_the_workspace_name_stays_flat(self):
        # 2026-10-05 07:59: '20261005-…/test_run.py', 'data/builds/20261005-…/x.py' 로 하위 폴더가 겹겹이 생겼다
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp) / "20261005-075924-8a0cd304")
            for given in ("20261005-075924-8a0cd304/test_run.py", "builds/20261005-075924-8a0cd304/test_run.py",
                          "data/builds/20261005-075924-8a0cd304/test_run.py"):
                self.assertEqual(workspace.resolve(given), workspace.root / "test_run.py")

    def test_edit_needs_exactly_one_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("a.py", "x = 1\nx = 1\n")
            self.assertIn("2곳", workspace.edit("a.py", "x = 1", "x = 2"))
            self.assertIn("고침", workspace.edit("a.py", "x = 1\nx = 1", "x = 2"))
            self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 2\n")


class RunTests(unittest.TestCase):
    def test_runtime_guard_blocks_computed_outside_write_and_alias_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "keep.txt"
            outside.write_text("original")
            workspace = BuildWorkspace(Path(tmp) / "work")
            snippets = [
                "p = '../keep.txt'\nopen(p, 'w').write('changed')",
                "from pathlib import Path\np = '../keep.txt'\nPath(p).write_text('changed')",
                "import os as ops\nops.remove('../keep.txt')",
                "p = '../keep.txt'\nprint(open(p).read())",
            ]
            for code in snippets:
                with self.subTest(code=code):
                    workspace.write("test_guard.py", code)
                    status, output = workspace.run("test_guard.py", None)
                    self.assertNotEqual(status, 0)
                    self.assertIn("ALEX 실행 보호", output)
                    self.assertEqual(outside.read_text(), "original")

    def test_child_does_not_inherit_host_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ALEX_TEST_SECRET": "do-not-inherit"}):
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("test_env.py", "import os\nassert 'ALEX_TEST_SECRET' not in os.environ\n")
            status, output = workspace.run("test_env.py", None)
            self.assertEqual(status, 0, output)
            self.assertEqual(workspace.last_run_checks, 1)

    def test_real_assertions_count_but_unexecuted_assertions_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            for code, count in [("if False:\n    assert True\nprint('OK')", 0),
                                ("assert 1 + 1 == 2\n", 1),
                                ("def test_never_called():\n    assert False\n", 0)]:
                workspace.write("test_checks.py", code)
                status, output = workspace.run("test_checks.py", None)
                self.assertEqual(status, 0, output)
                self.assertEqual(workspace.last_run_checks, count)

    def test_unittest_assertions_and_all_skipped_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            source = "import unittest\nclass Check(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2+3, 5)\nunittest.main()\n"
            workspace.write("test_checks.py", source)
            status, output = workspace.run("test_checks.py", None)
            self.assertEqual(status, 0, output)
            self.assertGreater(workspace.last_run_checks, 0)
            workspace.write("test_checks.py", source.replace("    def test_value", "    @unittest.skip('skip')\n    def test_value"))
            self.assertEqual(workspace.run("test_checks.py", None)[0], 0)
            self.assertEqual(workspace.last_run_checks, 0)

    def test_it_really_runs_and_reports_the_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("logic.py", BUGGY)
            workspace.write("test_logic.py", TEST)
            status, output = workspace.run("test_logic.py", None)
            self.assertEqual(status, 1)
            self.assertIn("AssertionError", output)
            workspace.edit("logic.py", FIXED_OLD, FIXED_NEW)
            self.assertEqual(workspace.run("test_logic.py", None), (0, workspace.run("test_logic.py", None)[1]))

    def test_a_failed_run_shows_the_programs_real_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("logic.py", "class Game:\n    def hard_drop(self):\n        pass\n\ndef add(a, b):\n    return a + b\n")
            workspace.write("test_logic.py", "from logic import Game\nGame().soft_drop()\n")
            observation, _, run = build_loop._apply(workspace, "run_python", {"path": "test_logic.py"}, None, lambda _t: None)
            self.assertEqual(run, ("test_logic.py", 1))
            self.assertIn("soft_drop", observation)  # 실제 오류
            self.assertIn("class Game: hard_drop()", observation)  # 실제로 있는 것
            self.assertIn("def add(a, b)", observation)
            self.assertNotIn("test_logic.py:", observation.split("실제로 있는")[1])  # 검사 파일은 목록에 넣지 않는다

    def test_blocked_code_is_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("evil.py", "import os\nos.remove('logic.py')\n")
            workspace.write("logic.py", "x = 1\n")
            status, output = workspace.run("evil.py", None)
            self.assertEqual(status, 2)
            self.assertIn("실행하지 않았다", output)
            self.assertTrue((Path(tmp) / "logic.py").exists())

    def test_endless_programs_are_stopped(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_loop, "RUN_SECONDS", 1):
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("loop.py", "while True:\n    pass\n")
            status, output = workspace.run("loop.py", None)
            self.assertEqual(status, 124)
            self.assertIn("끝나지 않아", output)


class LoopTests(unittest.TestCase):
    def test_no_tests_or_print_only_is_not_verification(self):
        for path in ("main.py", "test_empty.py"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                script = [step("", ("write_file", {"path": path, "content": "print('OK')"}),
                               ("run_python", {"path": path}),
                               ("finish", {"main_file": path, "summary": "done"}))]
                result = run_build(ScriptedModel(script), "x", BuildWorkspace(Path(tmp)), max_rounds=1)
                self.assertFalse(result.verified)

    def test_every_test_file_must_pass_on_the_same_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("main.py", "x=1")
            workspace.write("test_bad.py", "assert False")
            workspace.write("test_good.py", "assert True")
            script = [step("", ("run_python", {"path": "test_bad.py"}), ("run_python", {"path": "test_good.py"}),
                           ("finish", {"main_file": "main.py", "summary": "done"}))]
            self.assertFalse(run_build(ScriptedModel(script), "x", workspace, max_rounds=1).verified)
            workspace.write("test_bad.py", "assert True")
            self.assertTrue(run_build(ScriptedModel(script), "x", workspace, max_rounds=1).verified)

    def test_successful_run_that_rewrites_source_is_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = BuildWorkspace(Path(tmp))
            workspace.write("logic.py", "x=1")
            workspace.write("test_mutate.py", "assert True\nopen('logic.py', 'w').write('x=2')")
            script = [step("", ("run_python", {"path": "test_mutate.py"}),
                           ("finish", {"main_file": "logic.py", "summary": "done"}))]
            result = run_build(ScriptedModel(script), "x", workspace, max_rounds=1)
            self.assertEqual(result.runs, [("test_mutate.py", 0)])
            self.assertFalse(result.verified)

    def test_invalid_run_cannot_keep_previous_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = good_script()[:4] + [step("", ("run_python", {"path": "../test_outside.py"}),
                                              ("finish", {"main_file": "logic.py", "summary": "done"}))]
            result = run_build(ScriptedModel(script), "x", BuildWorkspace(Path(tmp)), max_rounds=5)
            self.assertFalse(result.verified)

    def test_cancelled_before_start_makes_no_model_call_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = threading.Event(); event.set()
            model = ScriptedModel(good_script())
            workspace = BuildWorkspace(Path(tmp))
            with self.assertRaises(GenerationCancelled):
                run_build(model, "x", workspace, cancel_event=event)
            self.assertEqual(model.seen, [])
            self.assertEqual(workspace.files(), [])

    def test_cancellation_between_tools_stops_remaining_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = threading.Event()
            workspace = BuildWorkspace(Path(tmp))
            original = workspace.write
            def write(*args):
                result = original(*args); event.set(); return result
            workspace.write = write
            with self.assertRaises(GenerationCancelled):
                run_build(ScriptedModel(good_script()), "x", workspace, cancel_event=event)
            self.assertEqual(workspace.files(), ["logic.py"])

    def test_provider_failure_keeps_partial_files_and_reports_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = ScriptedModel(good_script())
            original = model._collect_stream
            def collect(*args, **kwargs):
                if model.seen:
                    raise ProviderError("connection lost")
                return original(*args, **kwargs)
            model._collect_stream = collect
            result = run_build(model, "x", BuildWorkspace(Path(tmp)))
            self.assertFalse(result.verified)
            self.assertIn("connection lost", result.stopped)
            self.assertIn("logic.py", result.files)

    def test_malformed_and_json_encoded_tool_calls_do_not_break_compaction(self):
        calls = [{"function": "wrong"}, {"function": {"name": "write_file", "arguments": json.dumps({"content": "x" * 900})}}]
        compact = build_loop._compact_calls(calls)
        self.assertEqual(compact[0]["function"]["name"], "")
        self.assertLess(len(compact[1]["function"]["arguments"]["content"]), 100)

    def test_write_run_fix_run_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = ScriptedModel(good_script())
            said = []
            result = run_build(model, "덧셈 함수 만들어줘", BuildWorkspace(Path(tmp)), progress=said.append)
            self.assertTrue(result.verified)
            self.assertEqual([code for _, code in result.runs], [1, 0])
            self.assertEqual(result.main_file, "logic.py")
            self.assertIn("return a + b", (Path(tmp) / "logic.py").read_text(encoding="utf-8"))
            self.assertTrue(any("실행 결과: 종료 코드 1" in line for line in said))
            text = answer_text(result)
            self.assertIn("검사 통과", text)
            self.assertIn("return a + b", text)

    def test_finishing_before_a_passing_test_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = [step("", ("write_file", {"path": "logic.py", "content": BUGGY}),
                           ("write_file", {"path": "test_logic.py", "content": TEST})),
                      step("", ("finish", {"summary": "다 됐습니다", "main_file": "logic.py"}))] + good_script()[1:]
            model = ScriptedModel(script)
            result = run_build(model, "덧셈 함수 만들어줘", BuildWorkspace(Path(tmp)))
            self.assertTrue(result.verified)
            refused = [content for role, content, _ in model.seen[2] if role == "tool"]
            self.assertIn("아직 끝낼 수 없다", refused[-1])

    def test_editing_after_a_pass_needs_another_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = good_script()[:4] + [
                step("", ("edit_file", {"path": "logic.py", "old": "return a + b", "new": "return a + b  # 더하기"})),
                step("끝났습니다."),  # 고친 뒤 검사 없이 끝내려 함 → 다시 실행하라는 말
                step("", ("run_python", {"path": "test_logic.py"})),
                step("덧셈 함수와 검사를 만들었고 통과했습니다."),
            ]
            result = run_build(ScriptedModel(script), "덧셈 함수 만들어줘", BuildWorkspace(Path(tmp)))
            self.assertTrue(result.verified)
            self.assertEqual([code for _, code in result.runs], [1, 0, 0])

    def test_round_limit_reports_an_unverified_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = [step("", ("write_file", {"path": "logic.py", "content": BUGGY}),
                           ("write_file", {"path": "test_logic.py", "content": TEST}))] + [
                step("", ("run_python", {"path": "test_logic.py"}))] * 5
            result = run_build(ScriptedModel(script), "x", BuildWorkspace(Path(tmp)), max_rounds=4)
            self.assertFalse(result.verified)
            self.assertIn("4번 상한", result.stopped)
            self.assertIn("검사를 통과하지 못했습니다", answer_text(result))

    def test_big_file_contents_do_not_pile_up_in_the_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = "x = 1\n" * 2000
            script = [step("", ("write_file", {"path": "logic.py", "content": big}))] + good_script()
            model = ScriptedModel(script)
            run_build(model, "x", BuildWorkspace(Path(tmp)))
            later = model.seen[-1]
            self.assertFalse(any(big in str(calls) for _, _, calls in later))
            self.assertTrue(any("파일에 반영됨" in str(calls) for _, _, calls in later))

    def test_thinking_too_long_is_retried_without_thinking(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = ScriptedModel(good_script())
            model.think = True
            original = model._collect_stream
            calls = []

            def collect(messages, callback, tools=None):
                calls.append(model.think)
                if model.think and len(calls) == 1:
                    raise ThinkingTooLong("생각이 길다")
                return original(messages, callback, tools)

            model._collect_stream = collect
            result = run_build(model, "x", BuildWorkspace(Path(tmp)))
            self.assertTrue(result.verified)
            self.assertEqual(calls[:2], [True, False])
            self.assertTrue(model.think)


class RequestWordTests(unittest.TestCase):
    def test_build_requests(self):
        for text in ("파이썬 코드로 테트리스 게임 만들어줘", "다른대화에서 실패했던 테트리스 다시 만들어줘봐 파이썬 코드로",
                     "계산기 프로그램 짜줘"):
            self.assertTrue(is_build_request(text), text)
        for text in ("파이썬 정규식 만드는 방법 알려줘", "이 코드 설명해줘", "리스트와 튜플 차이가 뭐야"):
            self.assertFalse(is_build_request(text), text)
        self.assertTrue(is_build_followup("윗쪽 화살표를 누르면 도형이 바껴버리는데? 고쳐줘"))
        self.assertTrue(is_build_followup("점수 표시 추가해줘"))
        self.assertFalse(is_build_followup("이 코드 설명해줘"))


class CoreBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.coder = ScriptedModel(good_script())
        self.core = JarvisCore(
            storage=Storage(Path(self._tmp.name) / "t.db"),
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=MockLLMProvider("general", "G"), local_coder=self.coder,
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True, build_loop=True,
        )
        self.session = self.core.get_or_create_session(resume=False)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_build_request_goes_through_the_loop_and_a_fix_continues_in_the_same_folder(self):
        answer, decision = self.core.handle_build(self.session, "파이썬 코드로 덧셈 프로그램 만들어줘")
        self.assertEqual(decision.destination, Destination.LOCAL_CODER)
        self.assertIn("만들기 결과: 검사 통과", answer)
        self.assertEqual(self.coder.one_shot, 0)
        state = self.core._build_state(self.session)
        self.assertTrue((Path(state["workspace"]) / "logic.py").is_file())
        self.coder.steps = [step("", ("run_python", {"path": "test_logic.py"})),
                            step("", ("finish", {"summary": "다시 확인했습니다.", "main_file": "logic.py"}))]
        answer, decision = self.core.handle(self.session, "음수도 되는지 확인하고 고쳐줘")
        self.assertEqual(decision.reason, "continue explicit build")
        self.assertIn("검사 통과", answer)
        self.assertIn("처음 부탁: 파이썬 코드로 덧셈 프로그램 만들어줘", self.coder.seen[-1][1][1])
        self.assertIn("logic.py", self.coder.seen[-1][1][1])  # 이미 있는 파일을 알려 준다

    def test_questions_still_get_a_one_shot_answer(self):
        self.core.handle(self.session, "파이썬 리스트 컴프리헨션 설명해줘")
        self.assertEqual(self.coder.one_shot, 1)

    def test_switched_off_it_answers_in_one_go(self):
        self.core.build_loop = False
        answer, _ = self.core.handle_build(self.session, "파이썬 코드로 덧셈 프로그램 만들어줘")
        self.assertIn("꺼져 있습니다", answer)
        self.assertEqual(self.coder.one_shot, 0)

    def test_a_plain_chat_build_request_answers_with_code_and_makes_no_folder(self):
        # 2026-10-05 사용자: "코드를 짜서 나한테 주는 게 아니라 그냥 직접 만들어버리면 어쩌자는 거야"
        self.core.handle(self.session, "파이썬 코드로 덧셈 프로그램 만들어줘")
        self.assertEqual(self.coder.one_shot, 1)
        self.assertIsNone(self.core._build_state(self.session))
        self.assertFalse((Path(self._tmp.name) / "builds").exists())

    def test_a_fix_after_plain_chat_does_not_start_a_build(self):
        self.core.handle(self.session, "파이썬 코드로 덧셈 프로그램 만들어줘")
        _, decision = self.core.handle(self.session, "아직 안 되는데 고쳐줘")
        self.assertEqual(decision.reason, "follow-up in a coding conversation")
        self.assertEqual(self.coder.one_shot, 2)
        self.assertFalse((Path(self._tmp.name) / "builds").exists())

    def test_cancelled_build_keeps_a_resumable_workspace(self):
        event = threading.Event()
        self.core.command_cancel_event = event
        original = self.coder._collect_stream
        def collect(*args, **kwargs):
            if self.coder.seen:
                event.set()
                raise GenerationCancelled("stop")
            return original(*args, **kwargs)
        self.coder._collect_stream = collect
        answer, _ = self.core.handle_build(self.session, "파이썬 프로그램 만들어줘")
        state = self.core._build_state(self.session)
        self.assertIsNotNone(state)
        self.assertTrue((Path(state["workspace"]) / "logic.py").is_file())
        self.assertIn("계속 고쳐줘", answer)
        self.assertEqual(self.coder.one_shot, 0)

    def test_invalid_saved_build_workspace_is_not_resumed(self):
        for path in ("", str(Path(self._tmp.name)), str(Path(self._tmp.name) / "builds")):
            self.core.storage.set_state(f"build:{self.session}", json.dumps({"workspace": path}))
            self.assertIsNone(self.core._build_state(self.session), path)

    def test_new_builds_started_in_same_second_use_different_folders(self):
        with patch("jarvis.core.time.strftime", return_value="20261005-100000"):
            self.core.handle_build(self.session, "파이썬 덧셈 프로그램 만들어줘")
            first = self.core._build_state(self.session)["workspace"]
            self.coder.steps = good_script()
            self.core.handle_build(self.session, "파이썬 덧셈 프로그램 만들어줘")
            self.assertNotEqual(first, self.core._build_state(self.session)["workspace"])


if __name__ == "__main__":
    unittest.main()
