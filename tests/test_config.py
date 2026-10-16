"""Config validation tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.config import ConfigError, Settings, load_settings, validate


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make(self, **kwargs):
        return Settings(file_search_root=self.root, **kwargs)

    def test_defaults_are_valid(self):
        self.assertIsInstance(validate(self.make()), Settings)

    def test_gpu_layer_model_environment_is_read_and_trimmed(self):
        with patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(self.root / "absent.json"),
                                  "JARVIS_NUM_GPU_LAYERS_MODEL": "  qwen3.8:27b  ",
                                  "JARVIS_NUM_GPU_LAYERS": "48"}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.num_gpu_layers_model, "qwen3.8:27b")
        self.assertEqual(settings.num_gpu_layers, 48)

    def test_gpu_layer_model_local_setting_and_environment_precedence(self):
        local = self.root / "gpu.json"
        local.write_text(json.dumps({"num_gpu_layers_model": "local-model", "num_gpu_layers": 48}), encoding="utf-8")
        for value in (None, "other-model", ""):
            with self.subTest(environment=value):
                env = {"JARVIS_CONFIG_FILE": str(local)}
                if value is not None:
                    env["JARVIS_NUM_GPU_LAYERS_MODEL"] = value
                with patch.dict(os.environ, env, clear=True):
                    settings = load_settings()
                self.assertEqual(settings.num_gpu_layers_model, "local-model" if value is None else value)
                self.assertEqual(settings.num_gpu_layers, 48)

    def test_history_limit_must_be_positive(self):
        with self.assertRaises(ConfigError):
            validate(self.make(history_limit=0))

    def test_history_limit_upper_bound(self):
        with self.assertRaises(ConfigError):
            validate(self.make(history_limit=5000))

    def test_host_must_have_scheme(self):
        with self.assertRaises(ConfigError):
            validate(self.make(ollama_host="127.0.0.1:11434"))

    def test_model_name_required(self):
        with self.assertRaises(ConfigError):
            validate(self.make(general_model="  "))

    def test_file_search_root_must_exist(self):
        settings = Settings(file_search_root=self.root / "nope")
        with self.assertRaises(ConfigError):
            validate(settings)

    def test_file_search_root_must_be_dir(self):
        target = self.root / "a.txt"
        target.write_text("x", encoding="utf-8")
        with self.assertRaises(ConfigError):
            validate(Settings(file_search_root=target))

    def test_database_path(self):
        settings = self.make(data_dir=Path("data"))
        self.assertEqual(settings.database_path, Path("data/jarvis.db"))

    def test_benchmark_environment_disables_web_tool_and_auto_lessons_over_local_file(self):
        local = self.root / "settings.json"
        local.write_text(json.dumps({"web_tool_enabled": True, "auto_lessons_enabled": True,
                                     "lesson_context_limit": 3}), encoding="utf-8")
        with patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(local), "JARVIS_WEB_TOOL": "0",
                                  "JARVIS_AUTO_LESSONS": "0", "JARVIS_LESSON_CONTEXT_LIMIT": "0"}, clear=True):
            settings = load_settings()
        self.assertFalse(settings.web_tool_enabled)
        self.assertFalse(settings.auto_lessons_enabled)
        self.assertEqual(settings.lesson_context_limit, 0)

    def test_expanded_environment_names_also_override_local_file(self):
        local = self.root / "settings.json"
        local.write_text(json.dumps({"web_tool_enabled": True, "auto_lessons_enabled": True}), encoding="utf-8")
        with patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(local), "JARVIS_WEB_TOOL_ENABLED": "0",
                                  "JARVIS_AUTO_LESSONS_ENABLED": "0"}, clear=True):
            settings = load_settings()
        self.assertFalse(settings.web_tool_enabled)
        self.assertFalse(settings.auto_lessons_enabled)


if __name__ == "__main__":
    unittest.main()


class ConfigV012Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_summary_trigger_must_exceed_history_window(self):
        with self.assertRaises(ConfigError):
            validate(Settings(file_search_root=self.root, history_limit=12, summary_trigger_messages=13))

    def test_summary_max_chars_range(self):
        with self.assertRaises(ConfigError):
            validate(Settings(file_search_root=self.root, summary_max_chars=100))

    def test_file_read_limit_range(self):
        with self.assertRaises(ConfigError):
            validate(Settings(file_search_root=self.root, file_read_max_bytes=0))


class RemoteOllamaSafetyTests(unittest.TestCase):
    def test_remote_ollama_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                validate(Settings(file_search_root=Path(td), ollama_host="http://192.168.0.20:11434"))

    def test_remote_ollama_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(
                file_search_root=Path(td),
                ollama_host="http://192.168.0.20:11434",
                allow_remote_ollama=True,
            )
            self.assertIs(validate(settings), settings)


class ForcedProfileConfigTests(unittest.TestCase):
    def test_valid_forced_profile(self):
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(file_search_root=Path(td), force_profile="LAPTOP")
            self.assertIs(validate(settings), settings)

    def test_invalid_forced_profile(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                validate(Settings(file_search_root=Path(td), force_profile="SUPER"))


class ContextBudgetConfigTests(unittest.TestCase):
    def test_small_manual_budget_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                validate(Settings(file_search_root=Path(td), context_char_budget=500))


class CloudPrivacyDefaultsTests(unittest.TestCase):
    def test_persistent_context_is_not_sent_to_cloud_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            settings = validate(Settings(file_search_root=Path(td)))
            self.assertTrue(settings.require_cloud_approval)
            self.assertFalse(settings.cloud_include_memory)
            self.assertFalse(settings.cloud_include_summary)

class CurrentLocalModelDefaultsTests(unittest.TestCase):
    def test_laptop_friendly_current_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            settings = validate(Settings(file_search_root=Path(td)))
            self.assertEqual(settings.general_model, "qwen3:8b-q4_K_M")
            self.assertEqual(settings.coder_model, "qwen3:8b-q4_K_M")
            self.assertFalse(settings.ollama_think)

class OllamaRuntimeConfigTests(unittest.TestCase):
    def test_local_output_limit_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                validate(Settings(file_search_root=Path(td), local_max_output_tokens=10))
            with self.assertRaises(ConfigError):
                validate(Settings(file_search_root=Path(td), local_max_output_tokens=9000))

    def test_keep_alive_format(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(validate(Settings(file_search_root=Path(td), ollama_keep_alive="10m")).ollama_keep_alive, "10m")
            with self.assertRaises(ConfigError):
                validate(Settings(file_search_root=Path(td), ollama_keep_alive="forever"))


class NumCtxOverrideTests(unittest.TestCase):
    """num_ctx 직접 지정은 ADR-010 의 'config 가 결정한다' 를 실제로 가능하게 한다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make(self, **kwargs):
        return Settings(file_search_root=self.root, **kwargs)

    def test_zero_means_profile_auto(self):
        self.assertEqual(validate(self.make()).num_ctx, 0)

    def test_explicit_value_is_accepted(self):
        self.assertEqual(validate(self.make(num_ctx=8192)).num_ctx, 8192)

    def test_too_small_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(self.make(num_ctx=512))

    def test_too_large_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(self.make(num_ctx=200_000))
