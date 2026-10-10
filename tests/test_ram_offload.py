"""RAM 오프로딩 보고 회귀 테스트.

2026-09-13 에 모델을 9b 로 바꾸면서 num_ctx 8192 가 그대로 남아 층의 12% 가 RAM 으로
넘어갔고, **아무 경고도 없이 벽시계가 6.6배 느려졌다.** `ollama ps` 를 직접 치지 않으면
알 수 없었다. 여기서 그 상황을 고정한다.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.app import GIB, offload_report  # noqa: E402
from jarvis.config import ConfigError, Settings, validate  # noqa: E402
from jarvis.providers import OllamaProvider  # noqa: E402


def placement(total_gb: float, vram_gb: float) -> dict:
    return {"model": "m", "total_bytes": int(total_gb * GIB),
            "vram_bytes": int(vram_gb * GIB),
            "ram_bytes": int(max(0.0, total_gb - vram_gb) * GIB)}


class OffloadReportTests(unittest.TestCase):
    def test_fully_on_gpu_says_nothing(self):
        """지금 이 노트북 상태다 — 8B/ctx8192 가 6.19GB 로 100% GPU."""
        self.assertIsNone(offload_report(placement(6.19, 6.19), 16.0))

    def test_unknown_placement_says_nothing(self):
        self.assertIsNone(offload_report(None, 16.0))

    def test_offload_with_no_budget_is_a_warning(self):
        sev, msg = offload_report(placement(7.0, 6.2), 0.0)
        self.assertEqual(sev, "warn")
        self.assertIn("느려집니다", msg)

    def test_offload_within_budget_is_informational(self):
        sev, msg = offload_report(placement(7.0, 6.2), 16.0)
        self.assertEqual(sev, "info")
        self.assertIn("허용", msg)

    def test_offload_over_budget_is_a_warning(self):
        sev, msg = offload_report(placement(20.0, 6.9), 8.0)
        self.assertEqual(sev, "warn")
        self.assertIn("넘었습니다", msg)

    def test_heavier_offload_costs_more(self):
        def pct(total, vram):
            _, msg = offload_report(placement(total, vram), 16.0)
            return int(msg.split("약 ")[1].split("%")[0])
        self.assertLess(pct(5.85, 5.13), pct(6.74, 5.14))   # 12% < 24%
        self.assertLess(pct(6.74, 5.14), pct(20.0, 6.9))    # 24% < 65%

    def test_measured_points_are_reproduced(self):
        """2026-09-19 실측: 12% 오프로딩 -> 43.1에서 38.4 tok/s (약 11% 저하)."""
        from jarvis.app import _remaining_throughput
        self.assertAlmostEqual(_remaining_throughput(0.12), 0.89, places=2)
        self.assertAlmostEqual(_remaining_throughput(0.24), 0.55, places=2)
        self.assertAlmostEqual(_remaining_throughput(0.0), 1.0, places=2)

    def test_throughput_never_increases_with_offload(self):
        from jarvis.app import _remaining_throughput
        prev = 1.01
        for r in (0.0, 0.05, 0.12, 0.2, 0.24, 0.35, 0.5, 0.8, 1.0):
            cur = _remaining_throughput(r)
            self.assertLessEqual(cur, prev, f"ratio {r} 에서 속도가 올라갔다")
            prev = cur

    def test_every_message_names_the_measured_cost(self):
        for total, vram, budget in ((7.0, 6.2, 0.0), (7.0, 6.2, 16.0), (20.0, 6.9, 8.0)):
            _, msg = offload_report(placement(total, vram), budget)
            self.assertTrue("느려집니다" in msg, msg)


class PlacementReaderTests(unittest.TestCase):
    def _provider(self) -> OllamaProvider:
        return OllamaProvider(model="qwen3:8b-q4_K_M", host="http://127.0.0.1:11434")

    def _respond(self, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        handle = mock.MagicMock()
        handle.read.return_value = body
        handle.__enter__ = lambda s: s
        handle.__exit__ = lambda *a: False
        return handle

    def test_reads_the_split_from_api_ps(self):
        payload = {"models": [{"name": "qwen3:8b-q4_K_M", "size": 7_000_000_000,
                               "size_vram": 6_200_000_000}]}
        with mock.patch("urllib.request.urlopen", return_value=self._respond(payload)):
            info = self._provider().placement()
        self.assertEqual(info["ram_bytes"], 800_000_000)

    def test_a_different_loaded_model_is_not_reported_as_ours(self):
        payload = {"models": [{"name": "qwen3.5:9b-q4_K_M", "size": 7_000_000_000,
                               "size_vram": 6_200_000_000}]}
        with mock.patch("urllib.request.urlopen", return_value=self._respond(payload)):
            self.assertIsNone(self._provider().placement())

    def test_nothing_loaded_returns_none(self):
        with mock.patch("urllib.request.urlopen", return_value=self._respond({"models": []})):
            self.assertIsNone(self._provider().placement())

    def test_unreachable_server_returns_none_instead_of_raising(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(self._provider().placement())


class BudgetValidationTests(unittest.TestCase):
    def test_default_is_zero(self):
        self.assertEqual(Settings().ram_offload_budget_gb, 0.0)

    def test_the_configured_budget_is_accepted(self):
        validate(Settings(ram_offload_budget_gb=16.0))

    def test_negative_budget_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(ram_offload_budget_gb=-1.0))

    def test_absurd_budget_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(ram_offload_budget_gb=1024.0))


if __name__ == "__main__":
    unittest.main()


class NumGpuLayerTests(unittest.TestCase):
    """GPU 층 수 강제 지정.

    2026-09-19 실측(qwen3:14b / ctx 8192 / 8GB VRAM): Ollama 자동 배치가 VRAM 을 1.7GB
    남겨두고 10.3 tok/s 였는데, num_gpu=32 로 밀어넣으니 15.2 tok/s 로 48% 빨라졌다.
    다만 36·40 은 물리 VRAM 8.0G 를 넘겨 다시 느려졌다(14.4, 10.7). **최댓값이 최적이 아니다.**
    """

    def test_zero_sends_no_num_gpu_so_ollama_decides(self):
        opts = OllamaProvider(model="m", num_gpu=0)._options()
        self.assertNotIn("num_gpu", opts)

    def test_default_is_automatic(self):
        self.assertNotIn("num_gpu", OllamaProvider(model="m")._options())

    def test_explicit_layers_are_sent(self):
        self.assertEqual(OllamaProvider(model="m", num_gpu=32)._options()["num_gpu"], 32)

    def test_negative_is_clamped_to_automatic(self):
        self.assertNotIn("num_gpu", OllamaProvider(model="m", num_gpu=-5)._options())

    def test_context_and_output_still_travel_with_it(self):
        opts = OllamaProvider(model="m", num_ctx=8192, num_predict=6144, num_gpu=32)._options()
        self.assertEqual((opts["num_ctx"], opts["num_predict"]), (8192, 6144))


class NumGpuValidationTests(unittest.TestCase):
    def test_zero_means_automatic(self):
        validate(Settings(num_gpu_layers=0))

    def test_measured_optimum_is_accepted(self):
        validate(Settings(num_gpu_layers=32))

    def test_negative_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(num_gpu_layers=-1))

    def test_absurd_layer_count_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(num_gpu_layers=100_000))


class NumGpuPerModelTests(unittest.TestCase):
    """2026-09-30: 27b 의 최적 54층을 gpt-oss:120b(37층·MoE)에도 주면 안 된다 — 한 모델에만 적용할 수 있게."""

    def test_without_a_model_name_every_model_gets_the_layers(self):
        from jarvis.app import num_gpu_for
        settings = Settings(num_gpu_layers=54)
        self.assertEqual((num_gpu_for(settings, 'qwen3.8:27b'), num_gpu_for(settings, 'gpt-oss:120b')), (54, 54))

    def test_with_a_model_name_only_that_model_gets_them(self):
        from jarvis.app import num_gpu_for
        settings = Settings(num_gpu_layers=54, num_gpu_layers_model='qwen3.8:27b')
        self.assertEqual((num_gpu_for(settings, 'qwen3.8:27b'), num_gpu_for(settings, 'gpt-oss:120b')), (54, 0))

    def test_the_settings_screen_keeps_the_field(self):
        from jarvis.config import LOCAL_FIELDS
        self.assertIs(LOCAL_FIELDS['num_gpu_layers_model'], str)  # 화면 저장은 LOCAL_FIELDS 만 다시 쓴다 — 빠지면 조용히 사라진다
