"""코딩 대화의 후속 질문과, 생각이 답 자리까지 먹은 경우 — 2026-10-05 테트리스 대화에서 나온 두 문제.

1. "윗쪽 화살표를 누르면 도형이 바껴버리는데?" 처럼 코딩 단어가 없는 후속 질문이 대화용 모델(Q3)로 갔다. 그 모델이 맞는 회전
   코드를 원인으로 잘못 짚었고, "전체 코드 줘" 를 받은 코딩 모델(Q4)이 그 조언을 따라 맞던 코드를 틀리게 바꿨다.
2. 대화 8,658토큰 + 생각 7,722토큰으로 16K 문맥이 차서 답 없이 "/new 로 새 대화를 열라" 는 안내만 두 번 나갔다.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import (  # noqa: E402
    ContextExhausted, Message, MockLLMProvider, OllamaProvider, ThinkingTooLong)
from jarvis.router import Destination  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

DESKTOP = HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                       profile=PerformanceProfile.DESKTOP)


class Recorder(MockLLMProvider):
    def __init__(self, name, answer="답입니다."):
        super().__init__(name, name)
        self.answer = answer
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        return self.answer


def make_core(tmp, general, coder):
    return JarvisCore(
        storage=Storage(Path(tmp) / "t.db"), hardware=DESKTOP,
        local_general=general, local_coder=coder,
        cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
        local_only=True,
    )


class CodingFollowUpRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.general = Recorder("general")
        self.coder = Recorder("coder", "```python\nprint('tetris')\n```")
        self.core = make_core(self._tmp.name, self.general, self.coder)
        self.session = self.core.get_or_create_session(resume=False)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_follow_up_without_coding_words_stays_with_the_coding_model(self):
        _, first = self.core.handle(self.session, "파이썬 코드로 테트리스 게임 만들어줘")
        self.assertEqual(first.destination, Destination.LOCAL_CODER)
        _, follow = self.core.handle(self.session, "윗쪽 화살표를 누르면 도형이 바껴버리는데?")
        self.assertEqual(follow.destination, Destination.LOCAL_CODER)
        self.assertEqual(follow.reason, "follow-up in a coding conversation")
        self.assertEqual((self.general.calls, self.coder.calls), (0, 2))

    def test_an_answer_with_a_code_block_makes_the_next_question_a_coding_one(self):
        self.general.answer = "이렇게 하면 됩니다.\n```python\nx = 1\n```"
        _, first = self.core.handle(self.session, "오늘 할 일 정리하는 방법 알려줘")
        self.assertEqual(first.destination, Destination.LOCAL_GENERAL)
        _, follow = self.core.handle(self.session, "전체 다 보여줘")
        self.assertEqual(follow.destination, Destination.LOCAL_CODER)

    def test_a_new_conversation_and_plain_chat_stay_general(self):
        _, plain = self.core.handle(self.session, "안녕, 오늘 기분 어때?")
        self.assertEqual(plain.destination, Destination.LOCAL_GENERAL)
        _, again = self.core.handle(self.session, "고마워")
        self.assertEqual(again.destination, Destination.LOCAL_GENERAL)
        self.core.handle(self.session, "파이썬 코드로 테트리스 게임 만들어줘")
        other = self.core.get_or_create_session(resume=False)
        _, fresh = self.core.handle(other, "윗쪽 화살표를 누르면 도형이 바껴버리는데?")
        self.assertEqual(fresh.destination, Destination.LOCAL_GENERAL)


class ThinkingProvider(MockLLMProvider):
    """생각을 켜면 답 자리까지 생각만 하고, 끄면 답하는 모델."""

    def __init__(self, name, exhaust_without_thinking=False):
        super().__init__(name, name)
        self.model, self.num_ctx, self.think = "qwen3.8:27b", 16384, True
        self.seen_think = []
        self.exhaust_without_thinking = exhaust_without_thinking

    def generate(self, messages, **kwargs):
        self.seen_think.append(self.think)
        if self.think or self.exhaust_without_thinking:
            raise ThinkingTooLong("생각이 약 4096토큰을 넘어 답 쓸 자리를 지키려고 끊었습니다.")
        return "회전은 행렬을 돌려서 계산합니다."


class AnswerWithoutThinkingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.general = ThinkingProvider("general")
        self.core = make_core(self._tmp.name, self.general, Recorder("coder"))
        self.session = self.core.get_or_create_session(resume=False)

    def tearDown(self):
        self._tmp.cleanup()

    def test_it_answers_without_thinking_once_and_says_so(self):
        answer, _ = self.core.handle(self.session, "회전은 어떻게 하는 게 좋아?")
        self.assertIn("회전은 행렬을 돌려서 계산합니다.", answer)
        self.assertIn("생각 없이 바로 답했습니다", answer)
        self.assertNotIn("/new", answer)
        self.assertEqual(self.general.seen_think, [True, False])
        self.assertTrue(self.general.think)  # 대화 설정은 되돌린다

    def test_if_even_that_fails_the_old_guidance_stays(self):
        self.general.exhaust_without_thinking = True
        coder = ThinkingProvider("coder", exhaust_without_thinking=True)  # 같은 모델·문맥 창이면 다시 보내지 않는다
        self.core = make_core(self._tmp.name, self.general, coder)
        self.session = self.core.get_or_create_session(resume=False)
        answer, _ = self.core.handle(self.session, "회전은 어떻게 하는 게 좋아?")
        self.assertEqual(coder.seen_think, [])
        self.assertIn("/new", answer)
        self.assertIn("생각 없이 다시", answer)
        self.assertNotIn("생각 없이 바로 답했습니다", answer)
        self.assertTrue(self.general.think)

    def test_a_model_without_thinking_is_not_asked_twice(self):
        self.general.think = False
        self.general.exhaust_without_thinking = True
        self.core.handle(self.session, "회전은 어떻게 하는 게 좋아?")
        self.assertEqual(self.general.seen_think, [False])


class FakeStream:
    def __init__(self, lines):
        self.lines = [json.dumps(line).encode("utf-8") + b"\n" for line in lines]
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def __iter__(self):
        return iter(self.lines)

    def close(self):
        self.closed = True


class ThinkingBudgetTests(unittest.TestCase):
    def provider(self):
        return OllamaProvider("qwen3.8:27b", host="http://127.0.0.1:9", num_ctx=16384, num_predict=8192, think=True)

    def test_the_budget_leaves_room_for_the_answer(self):
        provider = self.provider()
        short = provider._thinking_budget([Message("user", "안녕")])
        self.assertEqual(short, 8192 - 8192 // 3)  # 출력 한도의 1/3 은 답 자리
        long = provider._thinking_budget([Message("user", "가" * 24000)])  # 테트리스 대화 크기
        self.assertLess(long, short)
        self.assertGreaterEqual(16384 - 24000 * 2 // 5 - long, 8192 // 3)  # 문맥에도 답 자리가 남는다
        self.assertEqual(provider._thinking_budget([Message("user", "가" * 60000)]), 1024)  # 최소 1,024

    def test_long_thinking_is_cut_before_any_answer_and_the_stream_closed(self):
        provider = self.provider()
        lines = [{"message": {"thinking": "word "}, "done": False}] * 6000
        stream = FakeStream(lines)
        with patch("jarvis.providers.urllib.request.urlopen", return_value=stream):
            with self.assertRaises(ThinkingTooLong) as ctx:
                provider.generate([Message("user", "테트리스")], on_chunk=lambda _c: None)
        self.assertIsInstance(ctx.exception, ContextExhausted)
        self.assertTrue(stream.closed)

    def test_short_thinking_then_the_answer_passes(self):
        provider = self.provider()
        lines = ([{"message": {"thinking": "plan "}, "done": False}] * 100
                 + [{"message": {"content": "답"}, "done": False},
                    {"message": {}, "done": True, "done_reason": "stop", "eval_count": 101, "eval_duration": 10**9}])
        with patch("jarvis.providers.urllib.request.urlopen", return_value=FakeStream(lines)):
            self.assertEqual(provider.generate([Message("user", "테트리스")], on_chunk=lambda _c: None), "답")

    def test_without_thinking_there_is_no_budget(self):
        provider = self.provider()
        provider.think = False
        lines = ([{"message": {"thinking": "word "}, "done": False}] * 9000
                 + [{"message": {"content": "답"}, "done": True, "done_reason": "stop"}])
        with patch("jarvis.providers.urllib.request.urlopen", return_value=FakeStream(lines)):
            self.assertEqual(provider.generate([Message("user", "x")], on_chunk=lambda _c: None), "답")


if __name__ == "__main__":
    unittest.main()
