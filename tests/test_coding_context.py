"""코딩 묶음 수정안만 긴 문맥으로 (2026-10-03 claude, ADR-076).

27b 는 core.py(11만 자)를 16K 문맥에 못 넣어 발췌만 보고, 발췌 밖 API 를 지어내 실패했다(PERFORMANCE 31 ⑦).
coding_num_ctx 를 켜면 묶음 수정안을 만드는 동안만 모델 문맥·GPU 층 수·글자 예산을 바꿔 파일을 통째로 보인다.
대화와 '파일 전체 다시 쓰기'는 그대로다. 기본은 꺼짐 — 실측 전에는 켜지 않는다.
"""
import tempfile
import unittest
from pathlib import Path

from jarvis.config import LOCAL_FIELDS, ConfigError, Settings, validate
from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.storage import Storage
from jarvis.tools import ToolError
from jarvis.workspace import SafeWorkspaceEditor
from tests.test_excerpt import TRACEBACK, big_module

FIX = "FILE: shapes.py\n<<<<<<< SEARCH\n    return width + height\n=======\n    return width * height\n>>>>>>> REPLACE\n"


class RecordingModel(MockLLMProvider):
    """Ollama 공급자처럼 num_ctx·num_gpu 를 가진 가짜 — 부를 때의 값과 모델이 본 글을 남긴다."""

    def __init__(self, name, reply, healthy=True):
        super().__init__(name, name.upper(), healthy)
        self.model, self.num_ctx, self.num_gpu, self.num_predict, self.think = f"{name}-model", 16384, 54, 6144, True
        self.reply, self.calls, self.predicts = reply, [], []

    def generate(self, messages, **kwargs):
        self.calls.append((self.num_ctx, self.num_gpu, "".join(m.content for m in messages)))
        self.predicts.append(self.num_predict)
        return self.reply


class CodingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.text = big_module(120)
        (self.root / "shapes.py").write_text(self.text, encoding="utf-8")

    def make_core(self, coder_healthy=True, **coding):
        self.coder = RecordingModel("coder", FIX, healthy=coder_healthy)
        self.general = RecordingModel("general", FIX)
        storage = Storage(self.base / "alex.db")
        core = JarvisCore(
            storage=storage,
            hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 16, PerformanceProfile.DESKTOP),
            local_general=self.general, local_coder=self.coder,
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            workspace_editor=SafeWorkspaceEditor(self.root, self.base / "backups", storage=storage),
            context_char_budget=24_000, **coding)
        self.assertGreater(len(self.text), core.context_char_budget)
        return core, core.get_or_create_session(False)

    def test_off_by_default_the_big_file_is_excerpted_with_the_chat_runtime(self):
        core, session = self.make_core()
        core.propose_bundle_local(session, ["shapes.py"], "테스트 실패를 고쳐라\n" + TRACEBACK)
        num_ctx, num_gpu, seen = self.coder.calls[-1]
        self.assertEqual((num_ctx, num_gpu), (16384, 54))
        self.assertIn("생략 ⋯", seen)
        self.assertNotIn("value * 5 + 100", seen)

    def test_long_context_shows_the_whole_file_then_puts_the_chat_runtime_back(self):
        core, session = self.make_core(coding_num_ctx=65536, coding_num_gpu=0, coding_context_char_budget=200_000)
        bundle = core.propose_bundle_local(session, ["shapes.py"], "테스트 실패를 고쳐라\n" + TRACEBACK)
        num_ctx, num_gpu, seen = self.coder.calls[-1]
        self.assertEqual((num_ctx, num_gpu), (65536, 0))  # 0 = Ollama 자동 배치(대화용 54층은 긴 문맥에서 넘친다)
        self.assertIn("value * 5 + 100", seen)  # 다른 함수 본문까지 통째로
        self.assertNotIn("생략 ⋯", seen)
        for model in (self.coder, self.general):
            self.assertEqual((model.num_ctx, model.num_gpu), (16384, 54))
        self.assertEqual(core.context_char_budget, 24_000)
        core.workspace_editor.apply_bundle(bundle.proposal_id)
        self.assertIn("return width * height", (self.root / "shapes.py").read_text(encoding="utf-8"))

    def test_the_chat_runtime_comes_back_after_a_refused_proposal(self):
        core, session = self.make_core(coding_num_ctx=65536, coding_context_char_budget=200_000)
        self.coder.reply = "FILE: shapes.py\n<<<<<<< SEARCH\n    return nothing_like_this\n=======\n    pass\n>>>>>>> REPLACE\n"
        with self.assertRaises(ToolError):
            core.propose_bundle_local(session, ["shapes.py"], "고쳐라\n" + TRACEBACK)
        self.assertEqual((self.coder.num_ctx, self.coder.num_gpu, core.context_char_budget), (16384, 54, 24_000))
        self.assertFalse(core._in_coding_runtime)

    def test_the_general_model_fallback_also_gets_the_long_context(self):
        core, session = self.make_core(coder_healthy=False, coding_num_ctx=65536, coding_context_char_budget=200_000)
        core.propose_bundle_local(session, ["shapes.py"], "고쳐라\n" + TRACEBACK)
        self.assertEqual(self.coder.calls, [])
        self.assertEqual(self.general.calls[-1][:2], (65536, 0))
        self.assertEqual((self.general.num_ctx, self.general.num_gpu), (16384, 54))

    def test_long_context_gives_the_leftover_room_to_the_answer(self):
        """출력 6,144 는 16K 기준 — 48K 에서 생각이 6,144 를 다 써 회차를 날렸다(평가 177회차 중 10회). 남은 자리를 준다(최대 16K)."""
        core, session = self.make_core(coding_num_ctx=49152, coding_context_char_budget=200_000)
        core.propose_bundle_local(session, ["shapes.py"], "고쳐라\n" + TRACEBACK)
        prompt_chars = len(self.coder.calls[-1][2])
        expected = min(16384, 49152 - prompt_chars // 3 - 1024)
        self.assertEqual(self.coder.predicts[-1], max(6144, expected))
        self.assertGreater(self.coder.predicts[-1], 6144)
        self.assertEqual(self.coder.num_predict, 6144)  # 끝나면 대화 값으로

    def test_the_answer_budget_is_untouched_without_long_context(self):
        core, session = self.make_core()
        core.propose_bundle_local(session, ["shapes.py"], "고쳐라\n" + TRACEBACK)
        self.assertEqual(self.coder.predicts[-1], 6144)

    def test_a_refused_proposal_keeps_the_model_text(self):
        """왜 형식이 틀렸는지 보려면 원문이 있어야 한다 — 지금까지 결과 기록에는 첫 줄만 남았다."""
        core, session = self.make_core()
        self.coder.reply = "설명만 하고 블록은 없는 답입니다."
        with self.assertRaises(ToolError) as caught:
            core.propose_bundle_local(session, ["shapes.py"], "고쳐라\n" + TRACEBACK)
        self.assertIn("블록은 없는 답", caught.exception.model_output)

    def test_long_context_keeps_a_long_instruction_whole(self):
        """실패 출력 + 실패한 테스트 코드가 48K 에서도 3,000자로 잘렸다(2026-10-04) — 긴 문맥에서는 글자 예산의 1/10 까지."""
        marker = "테스트가 기대하는 값: " + "x" * 6000 + " 끝표시"
        core, session = self.make_core()
        core.propose_bundle_local(session, ["shapes.py"], marker)
        self.assertIn("중간 생략", self.coder.calls[-1][2])  # 대화 예산: 3,000자로 잘림
        core, session = self.make_core(coding_num_ctx=49152, coding_context_char_budget=200_000)
        core.propose_bundle_local(session, ["shapes.py"], marker)
        self.assertIn(marker, self.coder.calls[-1][2])

    def test_the_failed_test_code_grows_with_long_context(self):
        from types import SimpleNamespace
        from jarvis.developer import TEST_CONTEXT_CHARS, DeveloperMode

        self.assertEqual(DeveloperMode(SimpleNamespace(coding_num_ctx=0))._test_context_limit(), TEST_CONTEXT_CHARS)
        long_mode = DeveloperMode(SimpleNamespace(coding_num_ctx=49152, coding_context_char_budget=135_000))
        self.assertEqual(long_mode._test_context_limit(), 9000)

    def test_a_whole_file_rewrite_keeps_the_chat_budget(self):
        """예산을 키운 채 판정하면 core.py 도 '전체 다시 쓰기'로 가 11만 자를 출력해야 한다 — 출력 한도에서 잘린다."""
        core, _ = self.make_core(coding_num_ctx=65536, coding_context_char_budget=200_000)
        self.assertFalse(core.fits_whole_file_edit("shapes.py", "고쳐라"))


class CodingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make(self, **kwargs):
        return Settings(file_search_root=self.root, **kwargs)

    def test_ranges_and_the_screen_keeps_the_values(self):
        validate(self.make(coding_num_ctx=65536, coding_num_gpu_layers=40, coding_context_char_budget=150_000))
        for bad in ({"coding_num_ctx": 512}, {"coding_num_ctx": 200_000}, {"coding_num_gpu_layers": -1},
                    {"coding_context_char_budget": 10}, {"coding_auto_attempts": 0}, {"coding_auto_attempts": 6}):
            with self.assertRaises(ConfigError, msg=str(bad)):
                validate(self.make(**bad))
        for key in ("coding_num_ctx", "coding_num_gpu_layers", "coding_context_char_budget", "coding_auto_attempts"):
            self.assertIs(LOCAL_FIELDS[key], int)  # 화면 저장은 LOCAL_FIELDS 만 다시 쓴다 — 빠지면 조용히 사라진다
        self.assertIs(LOCAL_FIELDS["coding_regression_check"], bool)  # ADR-078

    def test_the_whole_suite_gets_more_than_two_minutes(self):
        """ALEX 전체 검사는 131초(2026-10-03) — 테스트 하나용 120초 상한으로 돌리면 회귀 검사·/dev-verify 가 늘 시간 초과다."""
        from unittest import mock
        from jarvis import developer

        with mock.patch.object(developer, "_run_unittest", return_value=(0, "")) as run:
            developer._default_suite_runner(self.root)
        self.assertGreaterEqual(run.call_args.args[3], 600)

    def test_the_char_budget(self):
        from jarvis.app import coding_char_budget

        self.assertEqual(coding_char_budget(self.make(), 16384, 24_000), 0)  # 꺼짐 — 대화 예산을 쓴다
        self.assertEqual(coding_char_budget(self.make(coding_num_ctx=65536), 16384, 24_000), 96_000)  # 문맥 비율만큼
        self.assertEqual(coding_char_budget(self.make(coding_num_ctx=65536, coding_context_char_budget=150_000), 16384, 24_000),
                         150_000)


if __name__ == "__main__":
    unittest.main()
