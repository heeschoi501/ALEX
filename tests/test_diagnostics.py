"""수정 전 읽기 전용 진단 근거(ADR-080, Codex 가이드 우선순위 2) — 모델 없이 합성 소스로 잰다."""
import os
import tempfile
import threading
import unittest
from pathlib import Path

from jarvis.diagnostics import HEADER, MAX_SECTIONS, collect_diagnostic_context
from jarvis.providers import GenerationCancelled

FILLER = "".join(f"\ndef helper_{n}(value):\n    deadline = value + {n}\n    return deadline\n" for n in range(60))
SERVICE = '''"""원격 연결."""


class RemoteError(RuntimeError):
    pass
''' + FILLER + '''

def check_health(data):
    deadline = 5
    if not isinstance(data, dict) or data.get("app") == "jarvis":
        raise RemoteError("그 포트에서 응답한 것은 ALEX 가 아닙니다.")
    if not data.get("local_only"):
        raise RemoteError("로컬 전용 모드가 아닙니다.")
    return data
'''
TESTS = '''import unittest
from service import check_health, RemoteError


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def health(self, **overrides):
        data = {"app": "jarvis", "local_only": True}
        data.update(overrides)
        return data
''' + "".join(f"\n    def test_filler_{n}(self):\n        self.assertTrue(True)\n" for n in range(40)) + '''
    def test_connect(self):
        self.assertEqual(check_health(self.health())["app"], "jarvis")
'''


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "service.py").write_text(SERVICE, encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_service.py").write_text(TESTS, encoding="utf-8")

    def collect(self, output, budget=6000, **kwargs):
        return collect_diagnostic_context(self.root, ["service.py"], ["tests/test_service.py"], output, char_budget=budget, **kwargs)

    def test_the_error_text_brings_its_condition_not_the_neighbours(self):
        output = ('ERROR: test_connect (test_service.ConnectTests.test_connect)\nTraceback (most recent call last):\n'
                  'service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n')
        context = self.collect(output)
        self.assertIn('data.get("app") == "jarvis"', context.text)  # 문구를 감싼 조건
        self.assertIn("raise RemoteError(\"그 포트에서", context.text)
        self.assertNotIn("helper_59", context.text)  # 상관없는 함수들은 오지 않는다
        first = context.anchors[0]
        self.assertEqual(first.path, "service.py")
        self.assertIn("실패 문구", first.reason)

    def test_a_silent_failure_still_shows_the_test_setup_far_away(self):
        """실패 출력에 문구가 없어도(assertEqual 1 != 0) 테스트가 넣는 값 — 100줄 떨어진 준비 함수 — 을 보인다."""
        output = ('FAIL: test_connect (test_service.ConnectTests.test_connect)\nTraceback (most recent call last):\n'
                  '  File "C:\\\\Temp\\\\x\\\\project\\\\tests\\\\test_service.py", line 99, in test_connect\nAssertionError: 1 != 0\n')
        context = self.collect(output)
        self.assertIn('data = {"app": "jarvis", "local_only": True}', context.text)
        self.assertIn("준비 코드 health", context.text)
        self.assertIn("준비 코드 setUp", context.text)
        self.assertNotIn("test_filler_3", context.text)

    def test_traceback_paths_are_not_mistaken_for_error_text(self):
        """2026-10-04 실제 remote 출력: 임시 복사본 경로("C:\\Users\\…\\test_remote.py")를 오류 문구로 알고 찾다가 파일 상한을 다 썼다."""
        for n in range(20):
            (self.root / f"other_{n}.py").write_text("X = 1\n", encoding="utf-8")
        output = ('FAIL: test_connect (test_service.ConnectTests.test_connect)\nTraceback (most recent call last):\n'
                  '  File "C:\\\\Users\\\\alexuser\\\\Temp\\\\alex-dev-auto-x\\\\project\\\\tests\\\\test_service.py", line 99, in test_connect\n'
                  "AssertionError: 1 != 0\n")
        context = self.collect(output)
        self.assertLessEqual(context.files_scanned, 2)  # 수정 대상과 테스트만 — 경로를 찾으러 다른 파일을 뒤지지 않는다
        self.assertIn("준비 코드 health", context.text)

    def test_a_missing_helper_is_not_made_up(self):
        (self.root / "tests" / "test_service.py").write_text(
            TESTS.replace("self.health()", "self.missing_helper()"), encoding="utf-8")
        context = self.collect("FAIL: test_connect (test_service.ConnectTests.test_connect)\nAssertionError: 1 != 0\n")
        self.assertNotIn("missing_helper", context.text.replace("self.missing_helper()", ""))

    def test_the_candidate_bytes_are_read_and_hashed(self):
        import hashlib

        context = self.collect("service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n")
        digest = hashlib.sha256((self.root / "service.py").read_bytes()).hexdigest()
        self.assertEqual(context.anchors[0].sha256, digest)
        (self.root / "service.py").write_text(SERVICE.replace('== "jarvis"', '!= "jarvis"'), encoding="utf-8")
        again = self.collect("service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n")
        self.assertIn('!= "jarvis"', again.text)  # 다음 회차에는 바뀐 후보로 다시 모은다
        self.assertNotEqual(again.anchors[0].sha256, digest)

    def test_outside_paths_hidden_and_sensitive_files_are_not_followed(self):
        outside = Path(self.temp.name).parent / "outside_secret.py"
        output = (f'  File "{outside}", line 1, in x\n  File "C:\\\\x\\\\..\\\\..\\\\etc\\\\passwd", line 1, in y\n'
                  "ValueError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n")
        (self.root / ".hidden").mkdir()
        (self.root / ".hidden" / "mod.py").write_text('X = "그 포트에서 응답한 것은 ALEX 가 아닙니다."\n', encoding="utf-8")
        (self.root / "api_token_store.py").write_text('X = "그 포트에서 응답한 것은 ALEX 가 아닙니다."\n', encoding="utf-8")
        context = collect_diagnostic_context(self.root, ["nothing.py"], [], output, char_budget=6000)  # 수정 대상에 없으니 전체 검색까지
        paths = {anchor.path for anchor in context.anchors}
        self.assertIn("service.py", paths)
        self.assertFalse({".hidden/mod.py", "api_token_store.py"} & paths)
        self.assertFalse(any("passwd" in path or "outside" in path for path in paths))

    @unittest.skipIf(os.name == "nt" and not hasattr(os, "symlink"), "symlink 없음")
    def test_a_symlink_is_not_followed(self):
        target = Path(self.temp.name) / "service.py"
        try:
            os.symlink(target, self.root / "linked.py")
        except (OSError, NotImplementedError):
            self.skipTest("이 환경에서는 심볼릭 링크를 만들 수 없다")
        context = collect_diagnostic_context(self.root, ["linked.py"], [], "ValueError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n",
                                             char_budget=6000)
        self.assertNotIn("linked.py", {anchor.path for anchor in context.anchors})

    def test_limits_empty_output_broken_files_and_cancel(self):
        self.assertEqual(self.collect("").stop_reason, "no_output")
        self.assertEqual(self.collect("AssertionError: 1 != 0\n", budget=50).stop_reason, "budget")
        small = self.collect("service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n"
                             "FAIL: test_connect (test_service.ConnectTests.test_connect)\n", budget=len(HEADER) + 300)
        self.assertLessEqual(len(small.text), len(HEADER) + 300)
        self.assertTrue(small.truncated)
        (self.root / "service.py").write_text(SERVICE + "\ndef broken(:\n", encoding="utf-8")  # 문법이 깨져도 문자열 검색은 한다
        self.assertIn("그 포트에서", self.collect("service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n").text)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(GenerationCancelled):
            self.collect("service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n", cancel_event=cancel)

    def test_the_same_input_gives_the_same_result_and_sections_are_capped(self):
        output = ("FAIL: test_connect (test_service.ConnectTests.test_connect)\n"
                  "service.RemoteError: 그 포트에서 응답한 것은 ALEX 가 아닙니다.\n")
        self.assertEqual(self.collect(output).text, self.collect(output).text)
        self.assertLessEqual(len(self.collect(output, budget=100_000).anchors), MAX_SECTIONS)


class CoreDiagnosticsTests(unittest.TestCase):
    """묶음 수정안에 붙을 때: 기본 꺼짐이면 예전과 같고, 켜면 남는 자리에만 — 고칠 파일을 밀어내지 않는다, 모델 호출 수는 그대로."""

    def setUp(self):
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider, MockLLMProvider
        from jarvis.storage import Storage
        from jarvis.workspace import SafeWorkspaceEditor

        test = self
        self.seen = []

        class Model(MockLLMProvider):
            def generate(self, messages, **kwargs):
                test.seen.append([m.content for m in messages])
                return ('FILE: service.py\n<<<<<<< SEARCH\n    if not isinstance(data, dict) or data.get("app") == "jarvis":\n'
                        '=======\n    if not isinstance(data, dict) or data.get("app") != "jarvis":\n>>>>>>> REPLACE\n')

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "service.py").write_text(SERVICE, encoding="utf-8")
        (self.root / "tests" / "test_service.py").write_text(TESTS, encoding="utf-8")
        storage = Storage(base / "alex.db")
        self.core = JarvisCore(
            storage=storage, hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 16, PerformanceProfile.DESKTOP),
            local_general=MockLLMProvider("general", "GENERAL"), local_coder=Model("coder", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            workspace_editor=SafeWorkspaceEditor(self.root, base / "backups", storage=storage), context_char_budget=24_000)
        self.session = self.core.get_or_create_session(False)
        self.output = "FAIL: test_connect (test_service.ConnectTests.test_connect)\nAssertionError: 1 != 0\n"

    def propose(self):
        return self.core.propose_bundle_local(self.session, ["service.py"], "고쳐라", failure_output=self.output,
                                              test_paths=["tests/test_service.py"])

    def test_off_by_default_nothing_changes(self):
        self.propose()
        self.assertFalse(any("읽기 전용 진단 근거" in part for part in self.seen[-1]))
        self.assertIsNone(self.core.last_diagnostics)

    def test_on_it_adds_evidence_without_pushing_out_the_file_and_without_more_calls(self):
        self.propose()
        without = self.seen[-1]
        self.core.coding_diagnostic_context = True
        bundle = self.propose()
        with_diag = self.seen[-1]
        self.assertEqual(len(self.seen), 2)  # 모델 호출은 수정안마다 한 번 그대로
        self.assertEqual(without[2], with_diag[2])  # 고칠 파일 메시지는 그대로
        evidence = next(part for part in with_diag if part.startswith("읽기 전용 진단 근거"))
        self.assertIn('data = {"app": "jarvis", "local_only": True}', evidence)
        self.assertEqual(self.core.last_diagnostics["anchors"][0]["path"], "tests/test_service.py")
        self.assertIn('!= "jarvis"', bundle.diff)

    def test_no_room_means_no_evidence_and_a_recorded_reason(self):
        self.core.coding_diagnostic_context = True
        self.core.context_char_budget = 5_000  # 파일을 넣고 나면 자리가 없다
        self.propose()
        self.assertFalse(any(part.startswith("읽기 전용 진단 근거") for part in self.seen[-1]))
        self.assertIn("skipped", self.core.last_diagnostics)


if __name__ == "__main__":
    unittest.main()
