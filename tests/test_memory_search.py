"""기억 찾기 색인(2026-10-18 사용자: "기록이 쌓이면 느려지거나 하지 않나" → 속도보다 '최근 50개 안에서만 골라 오래된 기억이 빠지는'
문제를 고침). 기억 전체를 색인해 질문과 관련 있으면 오래된 기억도 쓴다."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.storage import Storage


def make_core(root: Path) -> JarvisCore:
    return JarvisCore(storage=Storage(root / "m.db"),
                      hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
                      local_general=MockLLMProvider("general", "GENERAL"), local_coder=MockLLMProvider("coder", "CODER"),
                      cloud_coder=CloudStubProvider("c", "C"), cloud_reasoning=CloudStubProvider("r", "R"),
                      require_cloud_approval=True, approval_callback=lambda _r: False)


class MemorySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = make_core(self.root)
        self.storage = self.core.storage

    def tearDown(self):
        self.temp.cleanup()

    def test_an_old_related_memory_is_used_even_after_many_newer_ones(self):
        self.storage.remember("자동차", "2019년식 아반떼, 다음 점검은 11월")
        for i in range(60):  # 최근 50개 밖으로 밀어낸다
            self.storage.remember(f"메모{i}", f"관계없는 기록 {i}")
        self.assertNotIn("자동차", self.storage.list_memories(limit=50))
        self.assertIn("자동차", self.core._select_memories("내 자동차 점검이 언제였지?"))

    def test_particles_and_endings_do_not_hide_a_memory(self):
        self.storage.remember("선호언어", "한국어")
        for i in range(55):
            self.storage.remember(f"기록{i}", f"다른 내용 {i}")
        self.assertIn("선호언어", self.core._select_memories("앞으로는 한국어로 답해줘"))

    def test_the_index_follows_updates_and_deletions(self):
        self.storage.remember("취미", "등산")
        self.assertIn("취미", self.storage.search_memories({"등산"}))
        self.storage.remember("취미", "수영")
        self.assertEqual(self.storage.search_memories({"등산"}), {})
        self.assertEqual(self.storage.search_memories({"수영"}), {"취미": "수영"})
        self.storage.forget("취미")
        self.assertEqual(self.storage.search_memories({"수영"}), {})

    def test_an_existing_database_gets_its_index_on_start(self):
        self.storage.remember("장비", "RTX 5080 16GB")
        with closing(sqlite3.connect(self.root / "m.db")) as conn, conn:
            conn.execute("DELETE FROM memory_search")  # 색인이 없던 예전 DB 처럼
        reopened = Storage(self.root / "m.db")
        self.assertEqual(reopened.search_memories({"rtx"}), {"장비": "RTX 5080 16GB"})

    def test_other_peoples_memories_never_come_back(self):
        self.storage.remember("비밀번호 힌트", "고양이 이름", owner="someone")
        self.assertEqual(self.storage.search_memories({"고양"}), {})
        self.assertNotIn("비밀번호 힌트", self.core._select_memories("고양이 이름이 뭐였지"))

    def test_no_match_keeps_the_old_behaviour_of_recent_memories(self):
        for i in range(3):
            self.storage.remember(f"k{i}", f"v{i}")
        self.assertEqual(len(self.core._select_memories("완전히 다른 질문입니다")), 3)


if __name__ == "__main__":
    unittest.main()
