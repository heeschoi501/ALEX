"""오픈코드 일꾼(ADR-083) — 일은 오픈코드, 판정·적용은 ALEX. 진짜 오픈코드·모델 없이 가짜 오픈코드 프로세스로 검사한다.

2026-10-05: 공개 자료는 Qwen3.8-27B 로컬 코딩에 검증된 에이전트(Claude Code·OpenCode)를 붙이라고 하고, 같은 날 이 PC 에서
같은 과제를 OpenCode 46초, ALEX 수정안 방식 765초에 처리했다. ALEX 는 범위·tests/ 보호·전체 검사·승인을 그대로 맡는다.
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from jarvis.developer import DeveloperRun  # noqa: E402
from jarvis.opencode_worker import PERMISSION, OpenCodeWorker, WorkerRun  # noqa: E402
from jarvis.providers import GenerationCancelled, ProviderError  # noqa: E402
from jarvis.tools import ToolError  # noqa: E402
from test_developer import TwoFileProject  # noqa: E402

FAKE = r'''
import json, os, sys, time
from pathlib import Path
mode = os.environ.get("FAKE_OPENCODE_MODE", "fix")
prompt = sys.argv[-1]
Path("seen_prompt.txt").write_text(prompt, encoding="utf-8")  # 범위 밖 새 파일 — ALEX 가 버려야 한다
print(json.dumps({"type": "tool_use", "part": {"tool": "read"}}), flush=True)
if mode == "sleep":
    time.sleep(60)
if mode in ("fix", "touch_tests", "failed_fix"):
    Path("jarvis/a.py").write_text("a = 1\n", encoding="utf-8")
    Path("jarvis/b.py").write_text("b = 2\n", encoding="utf-8")
    print(json.dumps({"type": "tool_use", "part": {"tool": "edit"}}), flush=True)
if mode == "touch_tests":
    Path("tests/test_ab.py").write_text("print('바꿈')\n", encoding="utf-8")
if mode == "wrong_then_right":
    count = Path(os.environ["FAKE_OPENCODE_COUNT"])  # 실행마다 새 임시 폴더라 바깥에 센다
    n = int(count.read_text()) if count.exists() else 0
    count.write_text(str(n + 1))
    Path("jarvis/a.py").write_text("a = 1\n", encoding="utf-8")
    Path("jarvis/b.py").write_text(("b = 3\n" if n == 0 else "b = 2\n"), encoding="utf-8")
if mode in ("build", "build_bad"):
    Path("calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    want = 5 if mode == "build" else 6
    Path("test_calc.py").write_text("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
                                    f"    def test_add(self):\n        self.assertEqual(add(2, 3), {want})\n\n\n"
                                    "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
print(json.dumps({"type": "tool_use", "part": {"tool": "bash"}}), flush=True)
if mode == "failed_fix":
    sys.exit(2)
'''


class FakeOpenCode:
    def setUp(self):
        super().setUp()
        self.fake_dir = TemporaryDirectory()
        self.addCleanup(self.fake_dir.cleanup)
        script = Path(self.fake_dir.name) / "fake_opencode.py"
        script.write_text(FAKE, encoding="utf-8")
        self.worker = OpenCodeWorker(Path(self.fake_dir.name), "fake-model", timeout=30,
                                     command=[sys.executable, str(script)])

    def fake_mode(self, name):
        return patch.dict(os.environ, {"FAKE_OPENCODE_MODE": name})


class WorkerTests(FakeOpenCode, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.project = Path(self.fake_dir.name) / "project"
        (self.project / "jarvis").mkdir(parents=True)
        (self.project / "tests").mkdir()
        (self.project / "jarvis" / "a.py").write_text("a = 0\n", encoding="utf-8")
        (self.project / "jarvis" / "b.py").write_text("b = 0\n", encoding="utf-8")
        (self.project / "tests" / "test_ab.py").write_text("x = 1\n", encoding="utf-8")

    def test_changes_come_back_and_the_given_folder_is_untouched(self):
        with self.fake_mode("touch_tests"):
            result = self.worker.run(self.project, "고쳐라")
        self.assertEqual(result.changed, {"jarvis/a.py": "a = 1\n", "jarvis/b.py": "b = 2\n", "tests/test_ab.py": "print('바꿈')\n"})
        self.assertIn("seen_prompt.txt", result.created)
        self.assertEqual(result.tools, {"read": 1, "edit": 1, "bash": 1})
        self.assertEqual((self.project / "jarvis" / "a.py").read_text(encoding="utf-8"), "a = 0\n")  # 사본에서만 일했다
        self.assertIn("python -m unittest", result.created["seen_prompt.txt"])  # ALEX 규칙이 프롬프트에 붙었다

    def test_a_run_that_takes_too_long_is_stopped(self):
        self.worker.timeout = 1
        with self.fake_mode("sleep"):
            result = self.worker.run(self.project, "고쳐라")
        self.assertTrue(result.timed_out)
        self.assertLess(result.seconds, 20)

    def test_cancel_stops_the_run(self):
        event = threading.Event()
        threading.Timer(0.5, event.set).start()
        with self.fake_mode("sleep"), self.assertRaises(GenerationCancelled):
            self.worker.run(self.project, "고쳐라", event)

    def test_gpu_release_failure_blocks_copy_and_process_start(self):
        self.worker.before_run = Mock(side_effect=ProviderError("GPU not released"))
        with patch("jarvis.opencode_worker._copy_project") as copy, \
                patch("jarvis.opencode_worker.subprocess.Popen") as process, \
                self.assertRaisesRegex(ProviderError, "GPU not released"):
            self.worker.run(self.project, "고쳐라")
        self.worker.before_run.assert_called_once_with()
        copy.assert_not_called()
        process.assert_not_called()

    def test_cancel_before_run_does_not_request_gpu_or_start_process(self):
        event = threading.Event()
        event.set()
        self.worker.before_run = Mock()
        with patch("jarvis.opencode_worker.subprocess.Popen") as process, self.assertRaises(GenerationCancelled):
            self.worker.run(self.project, "고쳐라", event)
        self.worker.before_run.assert_not_called()
        process.assert_not_called()

    def test_cancel_during_release_does_not_copy_or_start_process(self):
        event = threading.Event()
        self.worker.before_run = Mock(side_effect=event.set)
        with patch("jarvis.opencode_worker._copy_project") as copy, \
                patch("jarvis.opencode_worker.subprocess.Popen") as process, self.assertRaises(GenerationCancelled):
            self.worker.run(self.project, "고쳐라", event)
        copy.assert_not_called()
        process.assert_not_called()

    def test_permissions_allow_only_tests_and_no_internet(self):
        self.assertEqual(PERMISSION["bash"]["*"], "deny")
        self.assertEqual(PERMISSION["bash"]["python -m unittest *"], "allow")
        self.assertEqual((PERMISSION["webfetch"], PERMISSION["external_directory"]), ("deny", "deny"))
        config = self.worker.config()
        self.assertEqual(config["share"], "disabled")
        self.assertFalse(config["autoupdate"])
        self.assertIn("127.0.0.1", config["provider"]["ollama"]["options"]["baseURL"])

    def test_a_missing_executable_or_model_is_reported(self):
        worker = OpenCodeWorker(Path(self.fake_dir.name) / "없음", "fake-model")
        self.assertIn("실행 파일이 없습니다", worker.unavailable_reason())


class AutoLoopWithOpenCodeTests(FakeOpenCode, TwoFileProject, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.core.coding_worker = "opencode"
        self.core.opencode_worker = self.worker
        self.proposed = []
        self.core.propose_bundle_local = lambda *args, **kwargs: self.proposed.append(1)

    def test_opencode_fixes_the_copy_and_alex_offers_the_bundle(self):
        with self.fake_mode("fix"):
            reply = self.mode_run()
        self.assertIn("일꾼: 오픈코드", reply)
        self.assertIn("2회차 테스트: 통과", reply)
        self.assertEqual(self.proposed, [])  # 모델 수정안 방식은 쓰지 않았다
        run = DeveloperRun.load(self.store)
        self.assertEqual(run.phase, "awaiting_approval")
        self.assertEqual((self.read("a.py"), self.read("b.py")), ("a = 0\n", "b = 0\n"))  # 실제 파일은 승인 전 그대로

    def test_test_edits_and_stray_files_are_dropped_and_reported(self):
        with self.fake_mode("touch_tests"):
            reply = self.mode_run()
        self.assertIn("범위 밖 변경", reply)
        self.assertIn("tests/test_ab.py", reply)
        self.assertIn("2회차 테스트: 통과", reply)  # 테스트를 바꾼 덕이 아니라 진짜 수정으로 통과
        final = self.editor.get_bundle(DeveloperRun.load(self.store).proposal_id)
        self.assertEqual(sorted(c.relative_path.replace(os.sep, "/") for c in final.changes), ["jarvis/a.py", "jarvis/b.py"])

    def test_a_wrong_first_try_gets_the_failure_and_tries_again(self):
        with self.fake_mode("wrong_then_right"), \
                patch.dict(os.environ, {"FAKE_OPENCODE_COUNT": str(Path(self.fake_dir.name) / "count.txt")}):
            reply = self.mode_run()
        self.assertIn("2회차 테스트: 실패", reply)
        self.assertIn("3회차 테스트: 통과", reply)

    def test_without_opencode_it_falls_back_to_the_old_way(self):
        self.worker.command = None
        self.worker.home = Path(self.fake_dir.name) / "없음"
        from jarvis.workspace import parse_change_blocks
        self.core.propose_bundle_local = lambda _s, files, instruction, editor=None, record=True: (
            (editor or self.editor).create_bundle(parse_change_blocks(self.FIX)))
        reply = self.mode_run()
        self.assertIn("오픈코드를 쓰지 못해 ALEX 수정안 방식", reply)
        self.assertIn("2회차 테스트: 통과", reply)

    def test_failed_worker_does_not_offer_partial_changes_for_approval(self):
        with self.fake_mode("failed_fix"):
            reply = self.mode_run()
        self.assertIn("종료 코드 2", reply)
        self.assertEqual(DeveloperRun.load(self.store).phase, "needs_test")
        self.assertEqual((self.read("a.py"), self.read("b.py")), ("a = 0\n", "b = 0\n"))
        self.assertEqual(self.proposed, [])

    def test_gpu_guard_failure_restores_auto_run_without_process(self):
        self.worker.before_run = Mock(side_effect=ToolError("GPU not released"))
        self.mode.start("jarvis/a.py, jarvis/b.py", "tests/test_ab.py", "둘 다 고쳐")
        with patch("jarvis.opencode_worker._copy_project") as copy:
            reply = self.mode.auto("session")
        self.assertIn("GPU not released", reply)
        self.assertEqual(DeveloperRun.load(self.store).phase, "needs_test")
        self.assertEqual((self.read("a.py"), self.read("b.py")), ("a = 0\n", "b = 0\n"))
        copy.assert_not_called()

    def mode_run(self):
        self.mode.start("jarvis/a.py, jarvis/b.py", "tests/test_ab.py", "둘 다 고쳐")
        return self.mode.auto("session")


class BuildWithOpenCodeTests(FakeOpenCode, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import MockLLMProvider
        from jarvis.storage import Storage

        class Coder(MockLLMProvider):
            def _collect_stream(self, *args, **kwargs):
                raise AssertionError("오픈코드가 있으면 ALEX 자체 루프를 쓰지 않는다")

        self.data = Path(self.fake_dir.name) / "data"
        self.core = JarvisCore(
            storage=Storage(self.data / "t.db"),
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=MockLLMProvider("general", "G"), local_coder=Coder("coder", "C"),
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True, build_loop=True, coding_worker="opencode", opencode_worker=self.worker)
        self.session = self.core.get_or_create_session(resume=False)

    def test_core_binds_media_release_guard_to_worker(self):
        from jarvis.imagegen import ImageGenError

        self.core.image_generator = Mock()
        self.core.image_generator.prepare_for_chat.side_effect = ImageGenError("GPU not released")
        self.assertEqual(self.worker.before_run, self.core._release_media_for_worker)
        with patch("jarvis.opencode_worker.subprocess.Popen") as process, \
                self.assertRaisesRegex(ToolError, "GPU not released"):
            self.worker.run(Path(self.fake_dir.name) / "unused", "만들어라")
        self.core.image_generator.prepare_for_chat.assert_called_once_with()
        process.assert_not_called()

    def test_opencode_builds_and_alex_reruns_the_tests_itself(self):
        self.core.image_generator = Mock()
        with self.fake_mode("build"):
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("검사 통과", answer)
        self.assertIn("일꾼: 오픈코드", answer)
        workspace = Path(self.core._build_state(self.session)["workspace"])
        self.assertTrue((workspace / "calc.py").is_file() and (workspace / "test_calc.py").is_file())
        self.assertFalse(self.core._answer_failed)
        self.core.image_generator.prepare_for_chat.assert_called_once_with()

    def test_a_failing_test_is_reported_even_if_opencode_ends(self):
        with self.fake_mode("build_bad"):
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("검사를 통과하지 못했습니다", answer)
        self.assertIn("ALEX 재확인에서 검사 실패", answer)
        self.assertTrue(self.core._answer_failed)

    def build_result(self, **kwargs):
        return WorkerRun(created={"calc.py": "def add(a, b):\n    return a + b\n",
                                  "test_calc.py": "from calc import add\nassert add(2, 3) == 5\n"}, **kwargs)

    def test_timeout_and_failed_worker_do_not_verify_partial_files(self):
        for options in ({"timed_out": True}, {"exit_code": 1}):
            with self.subTest(options=options), patch.object(self.worker, "run", return_value=self.build_result(**options)):
                answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
            self.assertIn("검사를 통과하지 못했습니다", answer)
            self.assertTrue(self.core._answer_failed)

    def test_source_modified_during_test_is_not_verified(self):
        result = self.build_result()
        result.created["test_calc.py"] += ('with open("calc.py", "w", encoding="utf-8") as handle:\n'
                                          '    handle.write("def add(a, b):\\n    return 0\\n")\n')
        with patch.object(self.worker, "run", return_value=result):
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("검사를 통과하지 못했습니다", answer)
        self.assertIn("검사 중 Python 소스가 바뀌어", answer)
        self.assertTrue(self.core._answer_failed)

    def test_dropped_worker_change_does_not_claim_complete_verification(self):
        result = self.build_result()
        result.created["rejected.exe"] = "invalid"
        with patch.object(self.worker, "run", return_value=result):
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("검사를 통과하지 못했습니다", answer)
        self.assertIn("rejected.exe", answer)

    def test_worker_file_size_limit_does_not_silently_verify_old_files(self):
        result = self.build_result()
        result.created["oversized.py"] = "#" * 200_001
        with patch.object(self.worker, "run", return_value=result):
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("검사를 통과하지 못했습니다", answer)
        self.assertIn("oversized.py", answer)

    def test_build_release_error_has_saved_workspace_and_no_process(self):
        from jarvis.imagegen import ImageGenError

        self.core.image_generator = Mock()
        self.core.image_generator.prepare_for_chat.side_effect = ImageGenError("GPU not released")
        with patch("jarvis.opencode_worker.subprocess.Popen") as process:
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("만들기 작업을 진행하지 못했습니다: GPU not released", answer)
        self.assertIn(self.core._build_state(self.session)["workspace"], answer)
        self.assertTrue(self.core._answer_failed)
        process.assert_not_called()

    def test_worker_cancel_preserves_build_workspace_in_answer(self):
        self.core.command_cancel_event = threading.Event()
        self.core.command_cancel_event.set()
        with patch("jarvis.opencode_worker.subprocess.Popen") as process:
            answer, _ = self.core.handle_build(self.session, "덧셈 프로그램 만들어줘")
        self.assertIn("중단했습니다", answer)
        self.assertIn(self.core._build_state(self.session)["workspace"], answer)
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
