"""지난 대화 내용 찾기(2026-10-26, Hermes Agent 의 지난 대화 검색 참고) — 저장소·명령·웹. 내 대화만, 지운 대화는 안 나온다."""
from pathlib import Path
import tempfile
import unittest

from jarvis.app import handle_command
from jarvis.storage import OWNER, Storage
import urllib.parse

import tests.test_multiuser as multiuser


class StorageSearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.storage = Storage(Path(temp.name) / "t.db")
        self.minutes = self.storage.create_session()
        self.storage.add_message(self.minutes, "user", "지난주 회의록을 정리해줘. 예산은 300만원")
        self.storage.add_message(self.minutes, "assistant", "회의록 요약: 예산 300만원, 마감 11월")
        self.fifo = self.storage.create_session()
        self.storage.add_message(self.fifo, "user", "FIFO almost_full 조건 알려줘")

    def test_a_different_particle_still_finds_the_message_with_its_session(self):
        found = self.storage.search_messages("지난번 회의록은 어디 있어?")
        self.assertEqual({f["session_id"] for f in found}, {self.minutes})
        self.assertIn("회의록", found[0]["snippet"])
        self.assertEqual(self.storage.search_messages("almost_full")[0]["session_id"], self.fifo)

    def test_only_the_owners_chats_and_never_deleted_ones(self):
        friend = self.storage.create_user("친구", "friend", "x").user_id
        theirs = self.storage.create_session(owner=friend)
        self.storage.add_message(theirs, "user", "친구의 회의록 이야기")
        self.assertEqual({f["session_id"] for f in self.storage.search_messages("회의록", owner=friend)}, {theirs})
        self.assertNotIn(theirs, {f["session_id"] for f in self.storage.search_messages("회의록", owner=OWNER)})
        self.storage.delete_session(self.minutes)
        self.assertEqual(self.storage.search_messages("회의록", owner=OWNER), [])

    def test_like_wildcards_in_the_query_are_literal_and_results_are_capped_per_chat(self):
        self.assertEqual(self.storage.search_messages("100%_"), [])
        for n in range(6):
            self.storage.add_message(self.fifo, "assistant", f"almost_full 설명 {n}")
        self.assertEqual(len(self.storage.search_messages("almost_full", per_session=3)), 3)
        self.assertEqual(self.storage.search_messages("   "), [])


class CommandTests(unittest.TestCase):
    def test_the_command_lists_where_it_was_said(self):
        from tests.test_lessons import CapturingProvider
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import MockLLMProvider
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(Path(temp) / "t.db")
            model = CapturingProvider()
            core = JarvisCore(storage=storage, local_general=model, local_coder=model, local_only=True,
                              hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=32, gpu="", vram_gb=0,
                                                    profile=PerformanceProfile.DESKTOP),
                              cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"))
            old = storage.create_session()
            storage.rename_session(old, "예산 회의")
            storage.add_message(old, "user", "회의록을 정리해줘")
            current = core.get_or_create_session(resume=False)
            answer = handle_command(core, current, "/chat-search 회의록")[0]
            self.assertIn(old, answer)
            self.assertIn("예산 회의", answer)
            self.assertIn("사용법", handle_command(core, current, "/chat-search")[0])
            self.assertIn("찾지 못했습니다", handle_command(core, current, "/chat-search 우주선")[0])


class WebSearchTests(multiuser.MultiUserHTTPTests):
    def test_the_sidebar_search_reaches_message_text_but_only_your_own(self):
        owner_sid = self.state.session_id
        self.storage.add_message(owner_sid, "user", "주인의 비밀 회의록")
        query = "/api/chat-search?q=" + urllib.parse.quote("회의록")
        status, body, _ = self.call(query)
        self.assertEqual((status, [r["session_id"] for r in body["results"]]), (200, [owner_sid]))
        cookie = self.login_member()
        self.assertEqual(self.call(query, cookie=cookie)[1], {"results": []})
        self.assertEqual(self.call("/api/chat-search?q=", cookie=cookie)[1], {"results": []})


for _name in [n for n in vars(multiuser.MultiUserHTTPTests) if n.startswith("test_")]:   # 상속한 다중 사용자 시험은 여기서 다시 돌리지 않는다
    setattr(WebSearchTests, _name, None)

if __name__ == "__main__":
    unittest.main()
