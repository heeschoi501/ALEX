"""모델에게 '자기 자신과 이 기계' 를 알려주는 경로의 회귀 테스트.

2026-09-21 에 확인된 오답:
  "너는 무슨 모델이야?"  -> "GPT-4 기반 모델입니다"   (사실이 아님)
  "프로젝트 폴더는?"     -> "저"

앱은 둘 다 알고 있었다. 넘겨주지 않았을 뿐이다. 날짜가 2023년으로 나오던 것과 같은
구조다 — 모델은 우리가 준 글만 본다.
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
from jarvis.tools import SafeFileSearchTool  # noqa: E402


class EnvironmentLineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        hardware = HardwareInfo(os_name="Windows", cpu="AMD64", ram_gb=31.2,
                                gpu="NVIDIA GeForce RTX 4070 Laptop GPU", vram_gb=8.0,
                                profile=PerformanceProfile.LAPTOP)
        self.core = JarvisCore(
            storage=Storage(self.root / "t.db"), hardware=hardware,
            local_general=MockLLMProvider("general-model", "G"),
            local_coder=MockLLMProvider("coder-model", "C"),
            cloud_coder=MockLLMProvider("cloud", "X"),
            cloud_reasoning=MockLLMProvider("cloud2", "Y"),
            file_search_tool=SafeFileSearchTool(self.root),
            local_only=True,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_it_names_the_model_actually_running(self):
        """'GPT-4 기반' 이라고 답하던 자리다."""
        self.assertIn("general-model", self.core._environment_line())

    def test_the_coder_route_names_the_coder_model(self):
        line = self.core._environment_line(Destination.LOCAL_CODER)
        self.assertIn("coder-model", line)
        self.assertNotIn("general-model", line)

    def test_hardware_facts_are_present(self):
        line = self.core._environment_line()
        for piece in ("LAPTOP", "RTX 4070", "8.0GB", "31.2GB"):
            self.assertIn(piece, line, line)

    def test_project_root_is_present(self):
        self.assertIn(str(self.root.resolve()), self.core._environment_line())   # 풀어 쓴 경로(Windows 짧은 이름·링크)

    def test_local_only_is_stated(self):
        self.assertIn("로컬 전용", self.core._environment_line())

    def test_learning_capabilities_are_present_for_conversation(self):
        base = self.core._system_base()
        for fact in ("새 세션과 앱 재시작 후에도 남는다", "모델 가중치 업데이트와 구분",
                     "학습 후보 내보내기까지", "scripts/lora.py", "설정에 자동으로 들어가지 않는다",
                     "자동 경험 기록은 켜져", "최대 3개", "/growth"):
            self.assertIn(fact, base)

    def test_recording_and_retrieval_switches_are_independent(self):
        self.core.auto_lessons = False
        line = self.core._system_base()
        self.assertIn("자동 경험 기록은 꺼져", line)
        self.assertIn("교훈 재사용은 켜져", line)
        self.core.auto_lessons = True
        self.core.lesson_context_limit = 0
        line = self.core._system_base()
        self.assertIn("자동 경험 기록은 켜져", line)
        self.assertIn("교훈 재사용은 꺼져", line)

    def test_facts_survive_a_tight_context_budget(self):
        """예산으로 자른 **뒤에** 붙어야 한다. 먼저 붙이면 사실이 먼저 잘려나가고
        모델은 다시 학습 데이터에서 자기 정체를 꺼낸다."""
        base = self.core._system_base(limit=10)
        self.assertIn("general-model", base)
        self.assertIn("오늘은", base)

    def test_missing_hardware_fields_do_not_break_the_line(self):
        self.core.hardware = HardwareInfo(os_name="Linux", cpu="x86", ram_gb=None,
                                          gpu=None, vram_gb=None,
                                          profile=PerformanceProfile.UNKNOWN)
        line = self.core._environment_line()
        self.assertIn("UNKNOWN", line)
        self.assertNotIn("None", line)


if __name__ == "__main__":
    unittest.main()
