"""본문을 쓰기 전에 한도에 닿은 빈 답 — 원인대로 말하고, 같은 조건으로 다시 보내지 않는다.

2026-09-25 데스크톱: 긴 대화(프롬프트 7830 토큰)를 문맥 창 8192 로 보냈더니 qwen3.8:27b 가 남은
361 토큰을 생각에 다 쓰고 빈 답을 냈다(Ollama 로그 `truncated = 1`). 앱은 같은 모델의 coder 로
35초를 더 들여 같은 빈 답을 받았고, "모델이 없다, Ollama 서버와 모델 tag 를 확인하라" 는 안내에
"답변이 중간에 끊겼다" 꼬리표까지 붙였다. 셋 다 원인과 다른 말이었다.
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import (  # noqa: E402
    ContextExhausted, Message, MockLLMProvider, OllamaProvider, ProviderError)
from jarvis.storage import Storage  # noqa: E402


def empty_stream(provider, done_reason, prompt_tokens):
    def stream(messages, **kwargs):
        provider.last_metrics = {"done_reason": done_reason, "prompt_tokens": prompt_tokens}
        return iter([])
    return stream


class ProviderTests(unittest.TestCase):
    def provider(self, done_reason="length", prompt_tokens=7830):
        provider = OllamaProvider("qwen3.8:27b", num_ctx=8192, num_predict=6144)
        provider.generate_stream = empty_stream(provider, done_reason, prompt_tokens)
        return provider

    def test_full_context_window_says_so_with_numbers(self):
        with self.assertRaises(ContextExhausted) as ctx:
            self.provider().generate([Message("user", "hi")], on_chunk=lambda _c: None)
        self.assertIn("7830", str(ctx.exception))
        self.assertIn("8192", str(ctx.exception))

    def test_short_prompt_means_the_output_budget_went_to_thinking(self):
        with self.assertRaises(ContextExhausted) as ctx:
            self.provider(prompt_tokens=1000).generate([Message("user", "hi")], on_chunk=lambda _c: None)
        self.assertIn("출력 한도 6144", str(ctx.exception))

    def test_an_empty_answer_for_another_reason_stays_a_plain_error(self):
        with self.assertRaises(ProviderError) as ctx:
            self.provider(done_reason="stop").generate([Message("user", "hi")], on_chunk=lambda _c: None)
        self.assertNotIsInstance(ctx.exception, ContextExhausted)


class ExhaustedProvider(MockLLMProvider):
    def __init__(self, name):
        super().__init__(name, name)
        self.model = "qwen3.8:27b"
        self.num_ctx = 8192
        self.calls = 0
        self.last_metrics = {"done_reason": "length"}

    def generate(self, messages, **kwargs):
        self.calls += 1
        raise ContextExhausted("답을 쓰기 전에 문맥 창이 찼습니다 — 대화 7830토큰")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.general = ExhaustedProvider("general")
        self.coder = ExhaustedProvider("coder")
        self.core = JarvisCore(
            storage=Storage(Path(self._tmp.name) / "t.db"),
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=self.general, local_coder=self.coder,
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True,
        )
        self.answer, _ = self.core.handle(self.core.get_or_create_session(resume=False), "너 지금 qwen 3.8이면 26년 2월 기준 모델이야...")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_same_model_and_window_is_not_tried_again(self):
        self.assertEqual((self.general.calls, self.coder.calls), (1, 0))
        self.assertIn("다시 보내지 않음", self.answer)

    def test_it_names_the_real_cause_and_what_to_do(self):
        self.assertIn("문맥 창", self.answer)
        self.assertIn("/new", self.answer)
        self.assertNotIn("모델 tag", self.answer)

    def test_no_cut_off_notice_on_an_answer_that_never_started(self):
        self.assertNotIn("출력 예산을 모두 사용해", self.answer)


if __name__ == "__main__":
    unittest.main()
