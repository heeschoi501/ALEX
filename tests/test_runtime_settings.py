"""Live limits and settings writes must agree, including desktop overrides."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jarvis.app import build_providers
from jarvis.config import Settings, load_settings
from jarvis.hardware import PerformanceProfile
from jarvis.providers import OllamaProvider
from jarvis.router import Destination
from test_web import make_state


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = make_state(self.root)
        self.config = self.root / "settings.json"
        env = patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(self.config)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        health = patch.object(OllamaProvider, "health_check", return_value=True)
        health.start()
        self.addCleanup(health.stop)

    def configure(self, profile, **kwargs):
        self.state.core.hardware.profile = profile
        self.state.settings = Settings(use_ollama=True, **kwargs)
        general, coder, _ = build_providers(self.state.settings, self.state.core.hardware)
        self.state.core.providers[Destination.LOCAL_GENERAL] = general
        self.state.core.providers[Destination.LOCAL_CODER] = coder

    def test_status_reports_clamped_live_limits(self):
        self.configure(PerformanceProfile.LAPTOP, num_ctx=4096,
                       local_max_output_tokens=4000, request_timeout=321, ollama_think=True)
        p = self.state.core.providers[Destination.LOCAL_GENERAL]
        self.assertLess(p.num_predict, 4000)
        out = self.state.process("/status")["response"]
        self.assertIn(f"num_predict={p.num_predict}", out)
        self.assertIn("num_ctx=4096", out)
        self.assertIn("think=True", out)
        self.assertIn("timeout=321", out)
        self.assertNotIn("num_predict=4000", out)

    def test_snapshot_uses_provider_not_stale_settings(self):
        self.configure(PerformanceProfile.LAPTOP, general_model="actual:20b",
                       coder_model="unused:7b", num_ctx=8192, local_max_output_tokens=6144)
        self.state.settings.general_model = "not-applied:8b"
        snapshot = self.state.snapshot()
        effective = snapshot["effective_settings"]
        self.assertTrue(effective["shared"])
        self.assertEqual(effective["coder"]["model"], "actual:20b")
        self.assertEqual(effective["general"]["num_predict"], 6144)
        self.assertEqual(snapshot["settings"]["coder_model"], "unused:7b")

    def test_desktop_save_updates_provider_and_survives_reload(self):
        # Runtime limits use DESKTOP even when automatic model selection is off.
        for auto in (True, False):
            with self.subTest(auto_profile_models=auto):
                self.configure(PerformanceProfile.DESKTOP, auto_profile_models=auto,
                               num_ctx=8192, local_max_output_tokens=2048,
                               desktop_max_output_tokens=4096)
                self.state.save_settings({"desktop_max_output_tokens": 5120})
                for route in (Destination.LOCAL_GENERAL, Destination.LOCAL_CODER):
                    self.assertEqual(self.state.core.providers[route].num_predict, 5120)
                restored = load_settings()
                self.assertEqual(restored.desktop_max_output_tokens, 5120)
                self.assertEqual(restored.local_max_output_tokens, 2048)
                self.assertEqual(json.loads(self.config.read_text())["desktop_max_output_tokens"], 5120)

    def test_laptop_save_preserves_desktop_limit(self):
        self.configure(PerformanceProfile.LAPTOP, num_ctx=8192,
                       local_max_output_tokens=2048, desktop_max_output_tokens=5120)
        self.state.save_settings({"local_max_output_tokens": 4096})
        effective = self.state.snapshot()["effective_settings"]
        self.assertEqual(effective["general"]["num_predict"], 4096)
        self.assertEqual(load_settings().desktop_max_output_tokens, 5120)

    def test_model_change_updates_summary_provider_too(self):
        self.configure(PerformanceProfile.DESKTOP, desktop_general_model="old:20b",
                       desktop_coder_model="old:20b", ollama_think=True,
                       desktop_max_output_tokens=6144)
        self.state.save_settings({"desktop_general_model": "new:20b"})
        summary = self.state.core.local_summarizer
        self.assertEqual(self.state.core.providers[Destination.LOCAL_GENERAL].model, "new:20b")
        self.assertEqual(summary.model, "new:20b")
        self.assertFalse(summary.think)
        self.assertEqual(summary.num_predict, 3000)
        # 모델 교체로 새 Provider가 생겨도 이미지 엔진 GPU 반환 확인을 잃지 않는다.
        for provider in (*self.state.core.providers.values(), summary):
            if isinstance(provider, OllamaProvider):
                self.assertEqual(provider.before_generate, self.state.core._release_media_for_chat)

    def test_desktop_environment_override_rejects_ineffective_write(self):
        self.configure(PerformanceProfile.DESKTOP, num_ctx=8192)
        with patch.dict(os.environ, {"JARVIS_DESKTOP_MAX_OUTPUT_TOKENS": "4096"}):
            with self.assertRaisesRegex(ValueError, "환경변수"):
                self.state.save_settings({"desktop_max_output_tokens": 5120})
        self.assertFalse(self.config.exists())

    def test_invalid_desktop_budget_keeps_current_provider_and_file(self):
        self.configure(PerformanceProfile.DESKTOP, num_ctx=8192, desktop_max_output_tokens=4096)
        previous = self.state.core.providers[Destination.LOCAL_GENERAL]
        for value in (True, 8192):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.state.save_settings({"desktop_max_output_tokens": value})
        self.assertIs(self.state.core.providers[Destination.LOCAL_GENERAL], previous)
        self.assertFalse(self.config.exists())

    def test_separate_providers_report_separate_runtime_values(self):
        self.configure(PerformanceProfile.LAPTOP, single_model_on_laptop=False)
        coder = self.state.core.providers[Destination.LOCAL_CODER]
        coder.think = True
        coder.timeout = 456
        out = self.state.process("/status")["response"]
        self.assertIn("coder_runtime: think=True", out)
        self.assertIn("timeout=456", out)
        self.assertFalse(self.state.snapshot()["effective_settings"]["shared"])


if __name__ == "__main__":
    unittest.main()
