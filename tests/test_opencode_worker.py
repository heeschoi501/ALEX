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
prompt = sys.argv[-1] or sys.stdin.read()
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

    def test_long_unicode_request_uses_verbatim_stdin_and_not_argv(self):
        request = ('한글 "quoted" \\path\n' * 6000) + 'END'
        with self.worker.request_command(request) as (command, stdin):
            self.assertEqual(stdin.read().decode('utf-8'), request)
            self.assertLess(len(__import__('subprocess').list2cmdline(command)), 2000)
            self.assertFalse(stdin.closed)
        self.assertTrue(stdin.closed)
        result = self.worker.run(self.project, request)
        self.assertEqual(result.created['seen_prompt.txt'], request + __import__('jarvis.opencode_worker', fromlist=['RULES']).RULES)

    def test_long_request_input_is_closed_on_error(self):
        stdin = None
        with self.assertRaises(RuntimeError):
            with self.worker.request_command('long' * 30000) as (command, stdin):
                raise RuntimeError('process failed')
        self.assertTrue(stdin.closed)

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

    def test_process_start_failure_is_marked_as_infrastructure(self):
        with patch('jarvis.opencode_worker.subprocess.Popen', side_effect=OSError('startup failed')):
            result = self.worker.run(self.project, '고쳐라')
        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.infrastructure_error, 'startup failed')
        self.assertEqual(result.changed, {})


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



class FreeModelWorkerTests(unittest.TestCase):
    """OpenCode 무료 모델 일꾼(2026-10-23) — 공급자 설정 없이 모델 그대로, 개인 파일은 보내지 않는다(가짜 오픈코드)."""

    def test_free_model_command_config_and_private_files(self):
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            (project / "jarvis").mkdir(parents=True)
            (project / "jarvis" / "a.py").write_text("a = 0\n", encoding="utf-8")
            (project / "jarvis.local.json").write_text('{"secret": 1}', encoding="utf-8")
            (project / ".env").write_text("TOKEN=x", encoding="utf-8")
            (project / "id_rsa").write_text("KEY", encoding="utf-8")
            (project / "credentials.json").write_text("{}", encoding="utf-8")
            script = root / "fake.py"
            script.write_text("import json, sys\nfrom pathlib import Path\n"
                              "Path('seen.json').write_text(json.dumps({'args': sys.argv[1:], "
                              "'files': sorted(str(p) for p in Path('.').rglob('*') if p.is_file())}), encoding='utf-8')\n",
                              encoding="utf-8")
            worker = OpenCodeWorker(root, "opencode/big-pickle", command=[sys.executable, str(script)])
            self.assertTrue(worker.free)
            self.assertEqual(worker.unavailable_reason(), "")
            config = worker.config()
            self.assertEqual((config["model"], "provider" in config), ("opencode/big-pickle", False))
            env = worker.environment(root / "c.json", root / "d")
            self.assertNotIn("OPENCODE_DISABLE_MODELS_FETCH", env)  # 무료 모델은 OpenCode 가 목록을 알아야 한다
            run = worker.run(project, "고쳐")
            seen = json.loads(run.created["seen.json"])
            self.assertEqual(seen["args"][:3], ["run", "--model", "opencode/big-pickle"])
            self.assertNotIn("jarvis.local.json", " ".join(seen["files"]))
            self.assertNotIn(".env", " ".join(seen["files"]))
            self.assertNotIn("id_rsa", " ".join(seen["files"]))   # 2026-10-26: 키·인증 파일도 빼고 보낸다
            self.assertNotIn("credentials.json", " ".join(seen["files"]))
            self.assertEqual(run.deleted, [])  # 빼고 보낸 개인 파일이 '삭제'로 돌아오지 않는다
            self.assertTrue((project / "jarvis.local.json").exists())

    def test_free_model_withholds_secret_contents_and_ignores_writes_to_those_paths(self):
        # 2026-10-26: 내용에 비밀값 모양이 보이는 파일도 보내지 않고, 그 자리에 쓴 파일은 수정안에 넣지 않는다.
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            (project / "jarvis").mkdir(parents=True)
            (project / "jarvis" / "a.py").write_text("a = 0\n", encoding="utf-8")
            (project / "jarvis" / "keys.py").write_text('TOKEN = "ghp_abcdefghijklmnopqrstuvwx"\n', encoding="utf-8")
            script = root / "fake.py"
            script.write_text("from pathlib import Path\n"
                              "seen = Path('jarvis/keys.py').exists()\n"
                              "Path('jarvis/keys.py').write_text('TOKEN = None\\n', encoding='utf-8')\n"
                              "Path('jarvis/a.py').write_text('a = 1\\n', encoding='utf-8')\n"
                              "Path('seen.txt').write_text(str(seen), encoding='utf-8')\n", encoding="utf-8")
            run = OpenCodeWorker(root, "opencode/big-pickle", command=[sys.executable, str(script)]).run(project, "고쳐")
            self.assertEqual(run.created.get("seen.txt"), "False")
            self.assertEqual(run.withheld, ["jarvis/keys.py"])
            self.assertEqual(run.changed, {"jarvis/a.py": "a = 1\n"})
            self.assertNotIn("jarvis/keys.py", run.created)

    def test_free_worker_excludes_links_and_private_folders_and_rejects_their_replacements(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (root / "outside").mkdir()
            (root / "outside" / "note.txt").write_text("private", encoding="utf-8")
            for rel in ("data/alex.db", "logs/run.log", "credentials.d/key.txt"):
                path = project / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private", encoding="utf-8")
            try:
                (project / "linked.txt").symlink_to(root / "outside" / "note.txt")
                (project / "linked-dir").symlink_to(root / "outside", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            script = root / "fake.py"
            script.write_text("import json\nfrom pathlib import Path\n"
                              "Path('seen.json').write_text(json.dumps(sorted(p.as_posix() for p in Path('.').rglob('*'))))\n"
                              "Path('linked.txt').write_text('replacement')\n"
                              "Path('linked-dir').mkdir(exist_ok=True)\n"
                              "Path('linked-dir/new.txt').write_text('replacement')\n"
                              "Path('.env').write_text('replacement')\n"
                              "Path('normal.py').write_text('ok = True\\n')\n", encoding="utf-8")
            run = OpenCodeWorker(root, "opencode/big-pickle", command=[sys.executable, str(script)]).run(project, "고쳐")
            seen = json.loads(run.created["seen.json"])
            self.assertFalse(set(seen) & {"linked.txt", "linked-dir", "data", "logs", "credentials.d"})
            self.assertEqual(set(run.created), {"normal.py", "seen.json"})
            self.assertEqual((root / "outside" / "note.txt").read_text(), "private")

    def test_a_link_created_by_the_worker_is_not_read_back(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            outside = root / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            try:
                (project / "probe").symlink_to(outside)
                (project / "probe").unlink()
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            script = root / "fake.py"
            script.write_text("from pathlib import Path\n"
                              f"Path('leak.txt').symlink_to({str(outside)!r})\n", encoding="utf-8")
            run = OpenCodeWorker(root, "opencode/big-pickle", command=[sys.executable, str(script)]).run(project, "고쳐")
            self.assertNotIn("leak.txt", run.created)

    def test_free_worker_environment_keeps_network_setup_but_drops_ambient_secrets(self):
        worker = OpenCodeWorker(Path("tools"), "opencode/big-pickle")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret", "AWS_SECRET_ACCESS_KEY": "test-secret",
                                     "PYTHONPATH": "/private/imports", "OPENCODE_CONFIG_CONTENT": "private",
                                     "HTTPS_PROXY": "http://proxy:8080", "NODE_EXTRA_CA_CERTS": "/trust/ca.pem"}):
            env = worker.environment(Path("config.json"), Path("data"))
            local = OpenCodeWorker(Path("tools"), "local").environment(Path("config.json"), Path("data"))
        for name in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH", "OPENCODE_CONFIG_CONTENT"):
            self.assertFalse(name in env, name)  # never dump inherited environment values on failure
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy:8080")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], "/trust/ca.pem")
        self.assertEqual(local["OPENAI_API_KEY"], "test-secret")


if __name__ == "__main__":
    unittest.main()
