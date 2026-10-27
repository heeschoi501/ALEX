"""프롬프트 캐시를 살리는 순서(ADR-119, 설정 stable_prompt_prefix, 2026-10-26 — Hermes Agent 의 고정 시스템 프롬프트 참고).

켜면 차례마다 바뀌는 것(시각·메모·교훈·자료)이 질문 바로 앞에 가고, 앞부분(지시·대화 기록)은 다음 차례에도 그대로다."""
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from jarvis import core as core_module
from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.lessons import LESSON_MARKER
from jarvis.providers import Message, MockLLMProvider
from jarvis.storage import Storage
from tests.test_lessons import QUESTION, record


class Recorder(MockLLMProvider):
    def __init__(self):
        super().__init__("general", "G")
        self.calls: list[list[Message]] = []

    def generate(self, messages, **kwargs):
        self.calls.append(list(messages))
        return f"답 {len(self.calls)}"


class StablePrefixTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Storage(Path(temp.name) / "t.db")
        self.store.import_experience(record("a" * 24))
        self.store.remember("자연어", "검색은 앱이 자동으로 한다")
        self.model = Recorder()
        minutes = iter(range(100))
        patcher = mock.patch.object(core_module, "clock_line", lambda: f"지금은 10시 {next(minutes):02d}분이다.")
        patcher.start()
        self.addCleanup(patcher.stop)

    def core(self, **overrides):
        options = dict(storage=self.store, local_general=self.model, local_coder=self.model,
                       hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                             profile=PerformanceProfile.DESKTOP),
                       cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"), local_only=True)
        options.update(overrides)
        return JarvisCore(**options)

    def turns(self, core, count=3):
        session = core.get_or_create_session(resume=False)
        for text in (QUESTION, "오늘 저녁 메뉴 추천해줘", "그럼 내일은?")[:count]:   # 교훈·메모가 붙는 질문과 안 붙는 질문
            core.handle(session, text)
        return self.model.calls[-count:]

    @staticmethod
    def shared(first: list[Message], second: list[Message]) -> int:
        count = 0
        for a, b in zip(first, second):
            if (a.role, a.content) != (b.role, b.content):
                break
            count += 1
        return count

    def test_off_keeps_the_old_order_and_the_prefix_breaks_at_the_first_message(self):
        first, second, third = self.turns(self.core())
        self.assertIn("지금은 10시", second[0].content)          # 시각이 맨 앞 system 안에 있다
        self.assertEqual(self.shared(second, third), 0)          # 그래서 다음 차례에 처음부터 다르다
        lesson_at = next(i for i, m in enumerate(first) if LESSON_MARKER in m.content)
        self.assertLess(lesson_at, next(i for i, m in enumerate(first) if m.role == "user"))

    def test_on_moves_turn_specific_parts_after_the_history(self):
        first, second, third = self.turns(self.core(stable_prompt_prefix=True))
        self.assertNotIn("지금은 10시", first[0].content)
        tail = [m.content for m in first[:-1] if m.role == "system"][1:]
        self.assertTrue(any("지금은 10시" in c for c in tail))
        self.assertTrue(any(LESSON_MARKER in c for c in tail))
        self.assertTrue(any("장기 메모" in c for c in tail))
        self.assertEqual(first[-1], Message("user", QUESTION))
        # 둘째 차례에서 차례별 부분 앞까지(지시 + 대화 기록)가 셋째 차례의 앞부분과 그대로 같다 — 모델 서버가 다시 읽지 않는 부분
        reused = next(i for i, m in enumerate(second) if i and m.role == "system")
        self.assertEqual(reused, 3)
        self.assertEqual(self.shared(second, third), reused)

    def test_on_and_over_budget_the_turn_parts_stay_right_before_the_question(self):
        core = self.core(stable_prompt_prefix=True)
        core.context_char_budget = 1_800
        session = core.get_or_create_session(resume=False)
        for n in range(6):
            core.handle(session, f"{QUESTION} {'긴 설명 ' * 30} {n}")
        last = self.model.calls[-1]
        self.assertEqual(last[-1].role, "user")
        self.assertTrue(any("지금은 10시" in m.content for m in last[-4:-1] if m.role == "system"))
        self.assertLessEqual(sum(len(m.content) for m in last), 1_800 + 200)


class MeasureScriptTests(unittest.TestCase):
    def test_the_offline_measurement_shows_the_reused_prefix_growing_with_the_setting(self):
        from scripts.measure_prompt_cache import offline
        off, on = offline(False), offline(True)
        self.assertLess(off["reused_share_after_first"], 0.2)
        self.assertGreater(on["reused_share_after_first"], 0.7)
        self.assertGreater(on["turns"][-1]["reused"], 0.85 * on["turns"][-1]["chars"])

    def test_live_measurement_streams_balanced_replays_and_excludes_warmups(self):
        from jarvis.config import Settings
        from scripts.measure_prompt_cache import live, QUESTIONS
        models = []

        class TimedModel(Recorder):
            def generate(inner, messages, **kwargs):
                self.assertTrue(callable(kwargs.get("on_chunk")))
                inner.last_metrics = {"first_token_seconds": 0.25, "total_seconds": 1.0, "prompt_tokens": 50}
                super().generate(messages, **kwargs)
                return f"generated model {models.index(inner)} answer {len(inner.calls)}"

        def build(settings, **_kwargs):
            self.assertTrue(settings.local_only)
            self.assertFalse(settings.summary_enabled)
            model = TimedModel()
            models.append(model)
            core = JarvisCore(storage=Storage(settings.data_dir / "t.db"), local_general=model, local_coder=model,
                              cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
                              stable_prompt_prefix=settings.stable_prompt_prefix, summary_enabled=False,
                              hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                                    profile=PerformanceProfile.DESKTOP))
            return core, []

        with TemporaryDirectory() as temp, mock.patch("jarvis.app.build_core", side_effect=build), \
                mock.patch("jarvis.config.load_settings", return_value=Settings()), redirect_stdout(StringIO()):
            out = Path(temp) / "result.json"
            result = live(out, pairs=2)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), result)
        self.assertEqual([r["stable_prompt_prefix"] for r in result["runs"]], [False, True, True, False])
        self.assertFalse(result["cache_reset"])
        for run, model in zip(result["runs"], models):
            self.assertEqual(len(model.calls), len(QUESTIONS) + 1)
            self.assertEqual(len(run["turns"]), len(QUESTIONS))
            self.assertEqual(run["turns"][0]["first_token_seconds"], 0.25)
            replay = [[(m.role, m.content) for m in call if m.role != "system"] for call in model.calls[1:]]
            reference = [[(m.role, m.content) for m in call if m.role != "system"] for call in models[0].calls[1:]]
            self.assertEqual(replay, reference)


if __name__ == "__main__":
    unittest.main()
