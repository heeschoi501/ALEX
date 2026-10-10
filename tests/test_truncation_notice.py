"""출력이 예산 소진으로 끊겼을 때 사용자에게 알리는 경로.

`done_reason` 은 Provider 가 `last_metrics` 에 이미 담고 있었는데 아무도 읽지 않았다.
그래서 잘린 답이 **완성된 답처럼** 보였다 — 끝이 어색하면 모델이 이상한 줄 알지
예산이 모자랐다고는 생각하지 않는다. 2026-09-21 에 gpt-oss:20b 로 바꾸면서
`think: False` 여도 thinking 이 예산을 먹는 것이 확인돼 실제로 일어날 수 있는 상황이다.
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import MockLLMProvider  # noqa: E402
from jarvis.router import Destination  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

NOTICE = "출력 예산을 모두 사용해"


class TruncationContractTests(unittest.TestCase):
    def test_small_and_boundary_budgets_never_overflow(self):
        # 호출부에서 다시 자르는 우회 수정으로는 이 계약을 만족할 수 없다.
        text = "앞쪽 정보 가나다 " * 30 + "끝쪽 중요 정보"
        for limit in (-1, 0, 1, 19, 20, 40, 79, 80, 81, 100, len(text), len(text) + 1):
            with self.subTest(limit=limit):
                result = JarvisCore._truncate_text(text, limit)
                self.assertLessEqual(len(result), max(0, limit))
                if 0 < limit < 80:
                    self.assertEqual(result, text[:limit])
                if limit >= len(text):
                    self.assertEqual(result, text)

    def test_large_budget_keeps_both_ends(self):
        text = "START" + "가" * 500 + "END"
        result = JarvisCore._truncate_text(text, 100)
        self.assertTrue(result.startswith("START"))
        self.assertTrue(result.endswith("END"))
        self.assertLessEqual(len(result), 100)


class TruncationNoticeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.general = MockLLMProvider("general", "G")
        self.core = JarvisCore(
            storage=Storage(Path(self._tmp.name) / "t.db"),
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=31.2,
                                  gpu="GPU", vram_gb=8.0,
                                  profile=PerformanceProfile.LAPTOP),
            local_general=self.general, local_coder=MockLLMProvider("coder", "C"),
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_length_is_reported(self):
        self.general.last_metrics = {"done_reason": "length"}
        self.assertIn(NOTICE, self.core._truncation_notice(Destination.LOCAL_GENERAL))

    def test_a_normal_stop_says_nothing(self):
        self.general.last_metrics = {"done_reason": "stop"}
        self.assertEqual(self.core._truncation_notice(Destination.LOCAL_GENERAL), "")

    def test_missing_metrics_say_nothing(self):
        """지표가 없다고 경고를 띄우면 매 답변에 붙는다."""
        self.general.last_metrics = {}
        self.assertEqual(self.core._truncation_notice(Destination.LOCAL_GENERAL), "")

    def test_provider_without_metrics_attribute_is_safe(self):
        if hasattr(self.general, "last_metrics"):
            del self.general.last_metrics
        self.assertEqual(self.core._truncation_notice(Destination.LOCAL_GENERAL), "")

    def test_no_destination_says_nothing(self):
        self.assertEqual(self.core._truncation_notice(None), "")

    def test_it_reads_the_provider_for_that_route(self):
        """코더 경로가 잘렸는데 일반 모델 지표를 보면 안 된다."""
        self.general.last_metrics = {"done_reason": "length"}
        self.core.providers[Destination.LOCAL_CODER].last_metrics = {"done_reason": "stop"}
        self.assertEqual(self.core._truncation_notice(Destination.LOCAL_CODER), "")


if __name__ == "__main__":
    unittest.main()
