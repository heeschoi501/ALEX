"""런처가 띄우는 Ollama 의 환경 — 플래시 어텐션은 FA 에서 죽은 모델이 데스크톱 기본일 때만 끈다.

2026-09-25 데스크톱(RTX 5080)에서 qwen3.5:122b 가 Ollama 의 플래시 어텐션 CUDA 워밍업에서 죽어
데스크톱 전체에서 FA 를 껐다(ADR-054). 그 모델이 결선에서 떨어진 뒤(2026-09-26) FA 는 Ollama 기본값으로
돌리고 qwen3.5 계열이 기본 모델일 때만 끈다(ADR-064). 프로필·모델 판별이 Ollama 시작 뒤로 돌아가면
qwen3.5 를 고른 데스크톱에서도 기본값으로 떠서 같은 크래시가 난다 — 그 순서를 연결 테스트로 고정한다.
"""
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jarvis import launcher
from jarvis.config import load_settings
from jarvis.hardware import HardwareInfo, PerformanceProfile


def without_fa_override():
    """사용자 환경에 OLLAMA_FLASH_ATTENTION 이 있으면 그대로 물려받으므로 테스트에서는 뺀다."""
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    os.environ.pop("OLLAMA_FLASH_ATTENTION", None)
    return patcher


class OllamaEnvironmentTests(unittest.TestCase):
    def test_desktop_does_not_add_or_override_gpu_reservation(self):
        for original in (None, "0", str(2 * 1024**3)):
            with self.subTest(original=original), patch.dict(os.environ, {}, clear=False):
                if original is None:
                    os.environ.pop("OLLAMA_GPU_OVERHEAD", None)
                else:
                    os.environ["OLLAMA_GPU_OVERHEAD"] = original
                env = launcher.ollama_environment(PerformanceProfile.DESKTOP)
                self.assertEqual(env.get("OLLAMA_GPU_OVERHEAD"), original)
                self.assertEqual(os.environ.get("OLLAMA_GPU_OVERHEAD"), original)

    def setUp(self):
        self.addCleanup(without_fa_override().stop)

    def test_desktop_keeps_the_default_unless_a_model_crashed_with_fa(self):
        env = launcher.ollama_environment(PerformanceProfile.DESKTOP, ("qwen3.8:27b", "qwen3.8:27b"))
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", env)
        env = launcher.ollama_environment(PerformanceProfile.DESKTOP, ("gpt-oss:20b", "qwen3.5:122b"))
        self.assertEqual(env["OLLAMA_FLASH_ATTENTION"], "0")

    def test_desktop_kv_cache_is_8bit_unless_the_user_chose(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OLLAMA_KV_CACHE_TYPE", None)
            self.assertEqual(launcher.ollama_environment(PerformanceProfile.DESKTOP)["OLLAMA_KV_CACHE_TYPE"], "q8_0")
            for profile in (PerformanceProfile.LAPTOP, PerformanceProfile.UNKNOWN):
                self.assertNotIn("OLLAMA_KV_CACHE_TYPE", launcher.ollama_environment(profile))
            os.environ["OLLAMA_KV_CACHE_TYPE"] = "f16"
            self.assertEqual(launcher.ollama_environment(PerformanceProfile.DESKTOP)["OLLAMA_KV_CACHE_TYPE"], "f16")

    def test_laptop_and_unknown_keep_the_ollama_default(self):
        """노트북 측정(PERFORMANCE 12~25)은 전부 Ollama 기본값에서 했다. 재지 않은 조건으로 바꾸지 않는다."""
        for profile in (PerformanceProfile.LAPTOP, PerformanceProfile.UNKNOWN):
            with self.subTest(profile=profile):
                self.assertNotIn("OLLAMA_FLASH_ATTENTION", launcher.ollama_environment(profile, ("qwen3.5:122b",)))

    def test_every_profile_stays_local_only_and_single_model(self):
        for profile in PerformanceProfile:
            with self.subTest(profile=profile):
                env = launcher.ollama_environment(profile)
                self.assertEqual(env["OLLAMA_NO_CLOUD"], "1")
                self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11434")
                self.assertEqual(env["OLLAMA_MAX_LOADED_MODELS"], "1")
                self.assertEqual(env["OLLAMA_NUM_PARALLEL"], "1")


class LaunchWiringTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(without_fa_override().stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "runtime" / "ollama").mkdir(parents=True)
        (self.root / "runtime" / "ollama" / "ollama.exe").write_bytes(b"")

    def ollama_env_at_start(self, detected: PerformanceProfile, force_profile: str = "",
                            desktop_model: str = "qwen3.5:122b") -> dict:
        """ALEX 도 Ollama 도 안 떠 있을 때 launch() 가 Ollama 를 어떤 환경으로 띄우는지."""
        captured = {}

        class Started(Exception):
            pass

        def fake_popen(args, **kwargs):
            captured.update(kwargs["env"])
            raise Started

        hardware = HardwareInfo("Windows-10", "cpu", 95.6, "NVIDIA GeForce RTX 5080", 15.9, detected)
        settings = replace(load_settings(), force_profile=force_profile, auto_profile_models=True,
                           general_model="gpt-oss:20b", coder_model="gpt-oss:20b",
                           desktop_general_model=desktop_model, desktop_coder_model=desktop_model)
        with patch.object(launcher, "ROOT", self.root), \
             patch.object(launcher.os, "chdir"), \
             patch.object(launcher, "load_settings", return_value=settings), \
             patch.object(launcher, "get_json", side_effect=OSError("not running")), \
             patch.object(launcher, "detect_hardware", return_value=hardware), \
             patch.object(launcher.subprocess, "Popen", side_effect=fake_popen):
            with self.assertRaises(Started):
                launcher.launch()
        return captured

    def test_desktop_with_a_crashing_model_starts_ollama_with_flash_attention_off(self):
        self.assertEqual(self.ollama_env_at_start(PerformanceProfile.DESKTOP).get("OLLAMA_FLASH_ATTENTION"), "0")

    def test_desktop_with_other_models_starts_ollama_with_the_default(self):
        env = self.ollama_env_at_start(PerformanceProfile.DESKTOP, desktop_model="qwen3.8:27b")
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", env)

    def test_laptop_starts_ollama_with_the_default(self):
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", self.ollama_env_at_start(PerformanceProfile.LAPTOP))

    def test_force_profile_is_applied_before_ollama_starts(self):
        env = self.ollama_env_at_start(PerformanceProfile.DESKTOP, force_profile="LAPTOP")
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", env)


if __name__ == "__main__":
    unittest.main()
