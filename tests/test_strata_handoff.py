"""Follow-up contracts, executed only in the staged copy with no real GPU calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis import launcher
from jarvis.config import Settings
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import ProviderError, StrataProvider


class StrataHandoffTests(unittest.TestCase):
    def test_strata_launch_never_requires_an_ollama_server_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(data_dir=root / "data", local_backend="strata")
            hardware = HardwareInfo("Windows", "CPU", 96, "RTX 5080", 16, PerformanceProfile.DESKTOP)
            web_checks = []

            def get_json(url, timeout=2):
                if url != launcher.WEB + "/health":
                    raise AssertionError("Strata launch contacted another backend: " + url)
                web_checks.append(url)
                if len(web_checks) == 1:
                    raise OSError("web is off")
                return {"app": "jarvis", "instance": launcher.instance_id(settings.data_dir)}

            child = Mock()
            child.poll.return_value = None
            with patch.object(launcher, "ROOT", root), patch.object(launcher.os, "chdir"), \
                    patch.dict(os.environ, {}, clear=False), \
                    patch.object(launcher, "load_settings", return_value=settings), \
                    patch.object(launcher, "detect_hardware", return_value=hardware), \
                    patch.object(launcher, "get_json", side_effect=get_json), \
                    patch.object(launcher, "effective_local_models", side_effect=AssertionError("Ollama model selection")), \
                    patch.object(launcher, "ensure_strata") as strata, \
                    patch.object(launcher, "install", side_effect=AssertionError("Ollama installation")), \
                    patch.object(launcher.subprocess, "Popen", return_value=child) as spawn:
                self.assertEqual(launcher.launch(), launcher.WEB)
            strata.assert_called_once()
            self.assertEqual(spawn.call_count, 1)  # Only the mocked web server.
            self.assertIn("jarvis.web", spawn.call_args.args[0])

    def test_missing_or_non_boolean_loaded_status_is_not_gpu_release(self):
        provider = StrataProvider("fake", host="http://127.0.0.1:9")
        for data in ({"service": "strata"}, {"service": "strata", "loaded": None},
                     {"service": "strata", "loaded": 0}, {"service": "strata", "loaded": "false"},
                     {"service": "strata", "loaded": False, "error": "unavailable"}):
            with self.subTest(data=data), patch.object(provider, "_health", return_value=data):
                with self.assertRaises(ProviderError):
                    provider.loaded_models()

    def test_only_an_explicit_boolean_false_means_unloaded(self):
        provider = StrataProvider("fake", host="http://127.0.0.1:9")
        for loaded, expected in ((False, []), (True, ["fake"])):
            with self.subTest(loaded=loaded), \
                    patch.object(provider, "_health", return_value={"service": "strata", "loaded": loaded}):
                self.assertEqual(provider.loaded_models(), expected)

    def _make_strata_install(self, folder):
        for relative in (".venv/Scripts/python.exe", ".venv/bin/python"):
            executable = folder / relative
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"")
        (folder / "strata-first.json").write_text("{}", encoding="utf-8")

    def test_explicit_config_selects_a_model_with_multiple_installed_configs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self._make_strata_install(folder)
            selected = folder / "strata-second.json"
            selected.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"JARVIS_STRATA_CONFIG": str(selected)}):
                command = launcher.strata_command(folder, 8080)
            self.assertEqual(command[command.index("--config") + 1], str(selected))

    def test_relative_explicit_config_is_relative_to_strata_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self._make_strata_install(folder)
            selected = folder / "strata-second.json"
            selected.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"JARVIS_STRATA_CONFIG": selected.name}):
                command = launcher.strata_command(folder, 8080)
            self.assertEqual(command[command.index("--config") + 1], str(selected))

    def test_missing_explicit_config_is_rejected_without_silent_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self._make_strata_install(folder)
            with patch.dict(os.environ, {"JARVIS_STRATA_CONFIG": str(folder / "missing.json")}):
                with self.assertRaisesRegex(RuntimeError, "설정 파일"):
                    launcher.strata_command(folder, 8080)


if __name__ == "__main__":
    unittest.main()
