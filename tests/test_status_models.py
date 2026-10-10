"""`/status` 가 실제로 대답하는 모델을 보여주는지 (2026-09-21).

**이 테스트가 있는 이유.** `build_providers` 는 laptop 프로필에서
`single_model_on_laptop` 이 켜져 있으면 `coder = general` 로 덮어쓴다. 설정 파일의
`coder_model` 은 그대로 남아 있지만 **아무 곳에도 쓰이지 않는다.** 그런데 그 사실이
어느 화면에도 나오지 않았다. `/status` 는 모델을 아예 안 보여줬고, `/gpu` 는
`id(provider) in seen` 으로 같은 객체를 건너뛰어 coder 줄 자체를 안 찍었다.

그래서 2026-09-21 에 `jarvis.local.json` 의 `coder_model` 이 ADR-047 이 정한
`gpt-oss:20b` 인데도 실제로는 `general_model` 이 모든 route 를 처리하고 있었고,
설정 화면만 봐서는 알 방법이 없었다. 조용히 무시되는 설정은 없어야 한다.
"""
import tempfile
import unittest
from pathlib import Path

from jarvis.app import handle_command
from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.storage import Storage
from jarvis.tools import SafeFileSearchTool


class StatusModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _core(self, general, coder):
        core = JarvisCore(
            storage=Storage(self.path / "t.db"),
            hardware=HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP),
            local_general=general,
            local_coder=coder,
            cloud_coder=CloudStubProvider("cc", "CLOUD CODER"),
            cloud_reasoning=CloudStubProvider("cr", "CLOUD REASONING"),
            file_search_tool=SafeFileSearchTool(self.path),
        )
        return core, core.get_or_create_session(False)

    def _status(self, general, coder):
        core, sid = self._core(general, coder)
        output, _, _ = handle_command(core, sid, "/status")
        return output

    def test_both_models_are_named(self):
        out = self._status(MockLLMProvider("qwen3.5:9b-q4_K_M", "G"),
                           MockLLMProvider("gpt-oss:20b", "C"))
        self.assertIn("general_model: qwen3.5:9b-q4_K_M", out)
        self.assertIn("coder_model: gpt-oss:20b", out)

    def test_separate_providers_carry_no_shared_marker(self):
        out = self._status(MockLLMProvider("qwen3.5:9b-q4_K_M", "G"),
                           MockLLMProvider("gpt-oss:20b", "C"))
        self.assertNotIn("general 공유", out)

    def test_shared_provider_says_the_setting_is_not_used(self):
        """single-model 모드의 실제 모양이다 — 같은 객체가 양쪽에 들어간다."""
        shared = MockLLMProvider("qwen3.5:9b-q4_K_M", "G")
        out = self._status(shared, shared)
        self.assertIn("general 공유", out)
        self.assertIn("쓰이지 않습니다", out)

    def test_the_shared_marker_would_have_exposed_the_2026_09_21_state(self):
        """설정은 gpt-oss:20b 인데 실제로는 9b 가 코딩까지 처리하던 상태."""
        shared = MockLLMProvider("qwen3.5:9b-q4_K_M", "G")
        out = self._status(shared, shared)
        self.assertNotIn("gpt-oss:20b", out)
        self.assertIn("coder_model: qwen3.5:9b-q4_K_M", out)


if __name__ == "__main__":
    unittest.main()
