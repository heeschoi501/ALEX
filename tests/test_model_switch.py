"""Engine switching contracts; no real models, GPUs, or downloads."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis.app import build_providers
from jarvis.config import Settings, load_settings
from jarvis.hardware import PerformanceProfile
from jarvis.opencode_worker import OpenCodeWorker
from jarvis.providers import OllamaProvider, StrataProvider, ProviderError
from jarvis.router import Destination
from jarvis import model_switch
from jarvis.web import WebAppState
from test_web import make_state


class EngineSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "settings.json"
        self.state = make_state(self.root)
        self.state.core.hardware.profile = PerformanceProfile.DESKTOP
        for context in (patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(self.config)}, clear=True),
                        patch.object(OllamaProvider, "health_check", return_value=True),
                        patch.object(StrataProvider, "health_check", return_value=True)):
            context.start()
            self.addCleanup(context.stop)
        self.configure("strata")

    def configure(self, backend):
        self.state.settings = Settings(use_ollama=True, local_backend=backend, num_ctx=16384,
                                       strata_model="large-current", strata_num_ctx=65536,
                                       desktop_general_model="small:7b", desktop_coder_model="coder:14b")
        general, coder, _ = build_providers(self.state.settings, self.state.core.hardware)
        self.state.core.providers[Destination.LOCAL_GENERAL] = general
        self.state.core.providers[Destination.LOCAL_CODER] = coder
        self.state.core.opencode_worker = OpenCodeWorker(self.root, "old-worker", command=["fake-worker"])

    def test_preview_lists_saved_ollama_models_without_switching_or_starting(self):
        old = self.state.core.providers[Destination.LOCAL_GENERAL]
        with patch.object(OllamaProvider, "_fetch_installed_models", return_value=set()), \
             patch.object(OllamaProvider, "_fetch_loaded_models", return_value=set()), \
             patch.object(model_switch, "stored_ollama_models", return_value={"small:7b", "coder:14b"}), \
             patch.object(model_switch, "start_backend") as start:
            data = self.state.model_status("ollama")
        self.assertEqual(data["backend"], "ollama")
        self.assertEqual(data["installed"], ["coder:14b", "small:7b"])
        self.assertTrue(data["offline_store"])
        self.assertTrue(data["preview"])
        self.assertIs(self.state.core.providers[Destination.LOCAL_GENERAL], old)
        self.assertEqual(self.state.settings.local_backend, "strata")
        self.assertFalse(self.config.exists())
        start.assert_not_called()

    def test_strata_to_ollama_releases_first_and_updates_every_route(self):
        events = []
        old = self.state.core.providers[Destination.LOCAL_GENERAL]
        with patch.object(model_switch, "check_backend_install", side_effect=lambda _: events.append("check")), \
             patch.object(model_switch, "release_current", side_effect=lambda providers: events.append("release") or [old.model]) as release, \
             patch.object(model_switch, "start_backend", side_effect=lambda *_: events.append("start")):
            result = self.state.save_settings({"local_backend": "ollama", "desktop_general_model": "small:7b", "desktop_coder_model": "coder:14b"})
        self.assertEqual(events, ["check", "release", "start"])
        self.assertIn(old, release.call_args.args[0])
        self.assertEqual(result["released_models"], ["large-current"])
        general = self.state.core.providers[Destination.LOCAL_GENERAL]
        coder = self.state.core.providers[Destination.LOCAL_CODER]
        self.assertIs(type(general), OllamaProvider)
        self.assertEqual((general.model, coder.model), ("small:7b", "coder:14b"))
        self.assertEqual(self.state.core.local_summarizer.model, "small:7b")
        worker = self.state.core.opencode_worker
        self.assertEqual((worker.backend, worker.model, worker.host, worker.context), ("ollama", "coder:14b", coder.host, 16384))
        self.assertEqual(worker.command, ["fake-worker"])
        self.assertEqual(worker.before_run, self.state.core._release_media_for_worker)
        self.assertEqual(load_settings().opencode_model, "coder:14b")
        self.assertEqual(load_settings().local_backend, "ollama")
        self.assertIn(old, self.state.inactive_providers)
        self.assertIsNone(self.state.active_id)

    def test_ollama_to_strata_keeps_saved_ollama_preferences(self):
        self.configure("ollama")
        with patch.object(model_switch, "check_backend_install"), patch.object(model_switch, "release_current", return_value=["small:7b"]), patch.object(model_switch, "start_backend"):
            self.state.save_settings({"local_backend": "strata"})
        general = self.state.core.providers[Destination.LOCAL_GENERAL]
        self.assertIsInstance(general, StrataProvider)
        self.assertIs(self.state.core.providers[Destination.LOCAL_CODER], general)
        self.assertEqual(self.state.core.opencode_worker.backend, "strata")
        self.assertEqual(self.state.core.opencode_worker.context, 65536)
        self.assertEqual(load_settings().desktop_general_model, "small:7b")
        self.assertEqual(load_settings().strata_model, "large-current")

    def test_unknown_gpu_return_prevents_target_start_and_config_write(self):
        original = self.state.core.providers[Destination.LOCAL_GENERAL]
        with patch.object(model_switch, "check_backend_install"), \
             patch.object(model_switch, "release_current", side_effect=ValueError("GPU unknown")), \
             patch.object(model_switch, "start_backend") as start, self.assertRaisesRegex(ValueError, "GPU"):
            self.state.save_settings({"local_backend": "ollama"})
        start.assert_not_called()
        self.assertIs(self.state.core.providers[Destination.LOCAL_GENERAL], original)
        self.assertEqual(self.state.settings.local_backend, "strata")
        self.assertFalse(self.config.exists())
        self.assertIsNone(self.state.active_id)

    def test_busy_switch_does_not_clear_someone_elses_request(self):
        self.state.active_id = "existing-request"
        self.state.active_kind = "model_switch"
        with patch.object(model_switch, "release_current") as release, self.assertRaisesRegex(ValueError, "작업 중"):
            self.state.save_settings({"local_backend": "ollama"})
        release.assert_not_called()
        self.assertEqual(self.state.active_id, "existing-request")
        self.assertEqual(self.state.active_kind, "model_switch")

    def test_fixed_engine_environment_is_rejected_before_handoff(self):
        with patch.dict(os.environ, {"JARVIS_LOCAL_BACKEND": "strata"}), \
             patch.object(model_switch, "release_current") as release, self.assertRaisesRegex(ValueError, "환경변수"):
            self.state.save_settings({"local_backend": "ollama"})
        release.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_start_failure_preserves_old_settings_and_resets_own_busy_state(self):
        original = self.state.core.providers[Destination.LOCAL_GENERAL]
        with patch.object(model_switch, "check_backend_install"), patch.object(model_switch, "release_current", return_value=[]), \
             patch.object(model_switch, "start_backend", side_effect=ValueError("not ready")), self.assertRaisesRegex(ValueError, "not ready"):
            self.state.save_settings({"local_backend": "ollama"})
        self.assertIs(self.state.core.providers[Destination.LOCAL_GENERAL], original)
        self.assertEqual(self.state.settings.local_backend, "strata")
        self.assertFalse(self.config.exists())
        self.assertIsNone(self.state.active_id)

    def test_config_commit_failure_releases_ready_target_and_keeps_old_provider(self):
        original = self.state.core.providers[Destination.LOCAL_GENERAL]
        with patch.object(model_switch, "check_backend_install"), patch.object(model_switch, "release_current", return_value=[]) as release, \
             patch.object(model_switch, "start_backend"), patch("jarvis.web.os.replace", side_effect=PermissionError("read only")), self.assertRaises(PermissionError):
            self.state.save_settings({"local_backend": "ollama"})
        self.assertEqual(release.call_count, 2)
        self.assertIs(type(release.call_args.args[0][0]), OllamaProvider)
        self.assertIs(self.state.core.providers[Destination.LOCAL_GENERAL], original)
        self.assertFalse(self.config.exists())
        self.assertTrue(self.state.handoff_blocked)

    def test_inactive_model_reloaded_elsewhere_blocks_chat_and_worker(self):
        self.state.inactive_providers = [OllamaProvider("other:7b")]
        self.state.core.local_engine_guard = self.state._check_inactive_engines
        with patch("jarvis.providers.loaded_local_models", return_value=["other:7b"]), self.assertRaisesRegex(ProviderError, "다른 엔진"):
            self.state.core._release_media_for_chat()
        self.assertTrue(self.state.handoff_blocked)

    def test_switching_engine_switches_input_limits_too(self):
        """2026-10-11 "선택에 따라서 자동으로 설정을 바뀌게끔" — Strata 64K 창 ↔ Ollama 16K 기준 값."""
        core = self.state.core
        with patch.object(model_switch, "check_backend_install"), patch.object(model_switch, "release_current", return_value=[]), \
             patch.object(model_switch, "start_backend"):
            self.state.save_settings({"local_backend": "strata", "strata_max_output_tokens": 16384})
            self.assertEqual((core.context_char_budget, core.history_limit), (40550, 24))
            self.assertGreaterEqual(core.summary_trigger_messages, 48)
            self.assertEqual(core.providers[Destination.LOCAL_GENERAL].num_predict, 16384)
            self.state.save_settings({"local_backend": "ollama", "desktop_general_model": "small:7b",
                                      "desktop_coder_model": "coder:14b"})
            self.assertEqual((core.context_char_budget, core.history_limit, core.summary_trigger_messages),
                             (24000, 12, 24))
        snapshot = self.state.snapshot()
        self.assertEqual(snapshot["context_limits"], {"chars": 24000, "history": 12})
        self.assertEqual(snapshot["engine_limits"]["strata"], {"chars": 40550, "history": 24})

    def test_strata_output_above_half_the_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "strata_max_output_tokens"):
            self.state.save_settings({"strata_max_output_tokens": 40000})

    def test_same_engine_output_save_does_not_unload_again(self):
        self.state.inactive_providers = [OllamaProvider("other:7b")]
        with patch.object(model_switch, "start_backend") as start, patch.object(model_switch, "release_current") as release:
            self.state.save_settings({"local_backend": "strata", "strata_max_output_tokens": 5120})
        start.assert_not_called()
        release.assert_not_called()
        self.assertEqual(self.state.core.providers[Destination.LOCAL_GENERAL].num_predict, 5120)

    def test_startup_tracks_an_existing_other_engine_for_gpu_guard(self):
        with patch.object(model_switch, "backend_available", return_value=True):
            state = WebAppState(self.state.core, self.state.session_id, settings=self.state.settings)
        self.assertEqual(len(state.inactive_providers), 1)
        self.assertIs(type(state.inactive_providers[0]), OllamaProvider)
        with patch("jarvis.providers.loaded_local_models", side_effect=ProviderError("malformed")), self.assertRaises(ProviderError):
            state.core._release_media_for_chat()


class ModelRuntimeTests(unittest.TestCase):
    def test_offline_store_ignores_invalid_manifests_and_other_hosts(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(model_switch.launcher, "ROOT", Path(temp)):
            root = Path(temp) / "runtime/models/manifests/registry.ollama.ai/library"
            for name, content in (("valid/7b", '{"layers":[{}]}'), ("empty/7b", '{"layers":[]}'), ("bad/7b", 'not JSON')):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.assertEqual(model_switch.stored_ollama_models(model_switch.launcher.OLLAMA), {"valid:7b"})
            self.assertEqual(model_switch.stored_ollama_models("http://127.0.0.1:11435"), set())

    def test_release_deduplicates_servers_and_checks_after_unload(self):
        a, b = OllamaProvider("a"), OllamaProvider("b")
        events = []
        with patch.object(model_switch, "unload_local_models", side_effect=lambda *_args, **_kw: events.append("unload") or ["a"]) as unload, \
             patch.object(model_switch, "loaded_local_models", side_effect=lambda *_args, **_kw: events.append("verify") or []):
            self.assertEqual(model_switch.release_current([a, b]), ["a"])
        self.assertEqual(events, ["unload", "verify"])
        unload.assert_called_once()

    def test_ambiguous_loaded_state_is_not_treated_as_returned(self):
        with patch.object(model_switch, "unload_local_models", return_value=[]), \
             patch.object(model_switch, "loaded_local_models", side_effect=ProviderError("unknown")), self.assertRaisesRegex(ValueError, "unknown"):
            model_switch.release_current([StrataProvider("large")])

    def test_loaded_model_timeout_is_not_success(self):
        with patch.object(model_switch, "unload_local_models", return_value=["large"]), \
             patch.object(model_switch, "loaded_local_models", return_value=["large"]), \
             patch.object(model_switch.time, "monotonic", side_effect=[0, 20]), self.assertRaisesRegex(ValueError, "GPU 반환"):
            model_switch.release_current([StrataProvider("large")], timeout=1)

    def test_failed_new_server_is_reaped_instead_of_loading_later(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / "runtime/ollama/ollama.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            process = Mock(pid=123)
            process.poll.return_value = None
            with patch.object(model_switch.launcher, "ROOT", root), patch.object(model_switch, "backend_available", return_value=False), \
                 patch.object(model_switch.subprocess, "Popen", return_value=process), \
                 patch.object(model_switch.time, "monotonic", side_effect=[0, 30]), self.assertRaisesRegex(ValueError, "시간"):
                model_switch.start_backend(Settings(local_backend="ollama"), PerformanceProfile.DESKTOP)
            process.terminate.assert_called_once()
            process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
