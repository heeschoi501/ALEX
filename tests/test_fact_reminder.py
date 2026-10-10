"""사실 재확인(ADR-071): 앞선 답이 있는 대화에서만, 켜져 있을 때만, 마지막 질문 바로 앞에 핵심 사실을 한 번 더."""
import tempfile
import unittest
from pathlib import Path

from jarvis.router import Destination
from jarvis.storage import Storage


class FactReminderTests(unittest.TestCase):
    def setUp(self):
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider, MockLLMProvider

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        storage = Storage(Path(self.temp.name) / "alex.db")
        self.core = JarvisCore(
            storage=storage, hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("general", "GENERAL"), local_coder=MockLLMProvider("coder", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            local_only=True)
        self.session = self.core.get_or_create_session(False)

    def messages(self):
        return self.core._build_messages(self.session, Destination.LOCAL_GENERAL)

    def test_off_by_default(self):
        self.core.storage.add_message(self.session, "user", "인터넷 돼?")
        self.core.storage.add_message(self.session, "assistant", "인터넷이 막혀 있습니다.")
        self.core.storage.add_message(self.session, "user", "코드 수정됐을텐데 다시 확인해봐")
        self.assertFalse(any("다시 확인할 사실" in m.content for m in self.messages()))

    def test_it_sits_right_before_the_latest_question_when_there_are_earlier_answers(self):
        self.core.fact_reminder = True
        self.core.storage.add_message(self.session, "user", "인터넷 돼?")
        self.core.storage.add_message(self.session, "assistant", "인터넷이 막혀 있습니다.")
        self.core.storage.add_message(self.session, "user", "코드 수정됐을텐데 다시 확인해봐")
        messages = self.messages()
        self.assertEqual(messages[-1].content, "코드 수정됐을텐데 다시 확인해봐")
        reminder = messages[-2]
        self.assertEqual(reminder.role, "system")
        self.assertIn("다시 확인할 사실", reminder.content)
        self.assertIn("틀렸다고 먼저 말하고", reminder.content)
        self.assertIn("클라우드 LLM 연결은", reminder.content)
        self.assertIn("general", reminder.content)  # 모델 이름(가짜 모델은 name)

    def test_it_stays_right_before_the_question_when_a_long_conversation_is_trimmed(self):
        """2026-09-30 Codex 리뷰: 예산을 넘으면 _fit_context 가 system 을 모두 앞으로 모아 재확인이 기록 앞으로 갔다."""
        self.core.fact_reminder = True
        self.core.context_char_budget = 3000
        for n in range(6):
            self.core.storage.add_message(self.session, "user", f"질문 {n} " + "가" * 400)
            self.core.storage.add_message(self.session, "assistant", f"인터넷이 막혀 있습니다 {n} " + "나" * 400)
        self.core.storage.add_message(self.session, "user", "코드 수정됐을텐데 다시 확인해봐")
        messages = self.messages()
        self.assertLessEqual(sum(len(m.content) for m in messages), 3000 + 50)  # 예산 안으로 줄였다
        self.assertEqual(messages[-1].content, "코드 수정됐을텐데 다시 확인해봐")
        self.assertIn("다시 확인할 사실", messages[-2].content)
        self.assertEqual(sum("다시 확인할 사실" in m.content for m in messages), 1)

    def test_a_fresh_question_gets_no_extra_reminder(self):
        self.core.fact_reminder = True
        self.core.storage.add_message(self.session, "user", "인터넷 돼?")
        self.assertFalse(any("다시 확인할 사실" in m.content for m in self.messages()))


if __name__ == "__main__":
    unittest.main()
