"""OpenCode 무료 모델 목록(2026-10-23) — OpenCode 와 같은 거름·하루 한 번 받기·인터넷 없을 때 마지막 목록."""
import json
import tempfile
import unittest
from pathlib import Path

from jarvis.freemodels import free_models, is_free_model, load_free_models

CATALOG = {"opencode": {"models": {
    "big-pickle": {"name": "Big Pickle", "cost": {"input": 0, "output": 0}, "tool_call": True, "reasoning": True,
                   "limit": {"context": 200000, "output": 32000}},
    "alpha-free": {"name": "Alpha", "cost": {"input": 0}, "tool_call": True, "status": "alpha"},
    "old-free": {"name": "Old", "cost": {"input": 0}, "tool_call": True, "status": "deprecated"},
    "no-tools-free": {"name": "No tools", "cost": {"input": 0}, "tool_call": False},
    "paid": {"name": "Paid", "cost": {"input": 1.5, "output": 6}, "tool_call": True}}},
    "openai": {"models": {"free-looking": {"name": "x", "cost": {"input": 0}, "tool_call": True}}}}


class FreeModelTests(unittest.TestCase):
    def test_same_filter_as_opencode_without_a_key(self):
        models = free_models(CATALOG)
        self.assertEqual([m["id"] for m in models], ["opencode/big-pickle"])  # 다른 공급자·유료·지원 종료·실험·도구 없음 제외
        self.assertEqual((models[0]["context"], models[0]["output"], models[0]["reasoning"]), (200000, 32000, True))
        self.assertTrue(is_free_model("opencode/big-pickle"))
        self.assertFalse(is_free_model("ollama/qwen"))

    def test_cached_for_a_day_and_last_list_when_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "free.json"
            calls = []
            download = lambda: calls.append(1) or CATALOG  # noqa: E731
            self.assertEqual(len(load_free_models(cache, download, now=lambda: 1000.0)), 1)
            self.assertEqual(len(load_free_models(cache, download, now=lambda: 1000.0 + 3600)), 1)
            self.assertEqual(len(calls), 1)  # 하루 안에는 다시 받지 않는다

            def offline():
                raise OSError("no network")
            self.assertEqual([m["id"] for m in load_free_models(cache, offline, now=lambda: 1000.0 + 90000)],
                             ["opencode/big-pickle"])  # 하루 지났지만 못 받으면 마지막 목록
            self.assertEqual(load_free_models(Path(temp) / "none.json", offline), [])
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["models"][0]["name"], "Big Pickle")


if __name__ == "__main__":
    unittest.main()
