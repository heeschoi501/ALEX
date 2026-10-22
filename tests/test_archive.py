"""월별 대화 기록(ADR-100, 사용자 결정 2026-10-18 "11월 1일이 되면 26년 11월.md … 1월 1일이 되면 26년 대화.md 를 보고서처럼").

한 달에 파일 하나, 글만. 그림·영상·문서는 한 줄 표시. 이미 적은 곳은 기억해 두고 새 메시지만 덧붙인다.
다른 사용자의 대화는 따로. 새해가 되면 지난해 월별 파일로 연간 보고서."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from jarvis.app import handle_command
from jarvis.archive import ConversationArchive, month_name, text_only, year_name
from jarvis.permissions import READ_ONLY_BLOCKED
from jarvis.storage import OWNER
from tests.test_memory_search import make_core
from tests.test_web import make_state


EARLY = datetime(2000, 1, 1).astimezone()


def stamp(storage, when: str) -> None:
    """방금 넣은 메시지의 시각을 바꾼다(달 나누기 시험용). 달 한가운데 시각이라 시간대와 상관없이 같은 달."""
    with storage._connection() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = (SELECT MAX(id) FROM messages)", (when,))


class TextTests(unittest.TestCase):
    def test_media_becomes_one_line(self):
        content = ("여기 있습니다\n![강아지 그림](alex-image:20261019-1.png)\n![달리는 개](alex-video:a.mp4)\n"
                   "[보고서.pdf](alex-document:abc123) 그리고 ![](alex-attachment:x.jpg)")
        self.assertEqual(text_only(content),
                         "여기 있습니다\n[그림: 강아지 그림 — 원본 보관 안 함]\n[영상: 달리는 개 — 원본 보관 안 함]\n"
                         "[문서: 보고서.pdf — 원본 보관 안 함] 그리고 [첨부 그림: 설명 없음 — 원본 보관 안 함]")
        self.assertEqual(text_only("보통 [링크](https://example.com) 는 그대로"), "보통 [링크](https://example.com) 는 그대로")

    def test_file_names(self):
        self.assertEqual(month_name(datetime(2026, 11, 3)), "26년 11월.md")
        self.assertEqual(month_name(datetime(2027, 1, 1)), "27년 1월.md")
        self.assertEqual(year_name(2026), "26년 대화.md")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = make_core(self.root)
        self.storage = self.core.storage
        self.archive = ConversationArchive(self.storage, self.root / "archive")

    def tearDown(self):
        self.temp.cleanup()

    def say(self, sid: str, role: str, text: str, when: str) -> None:
        self.storage.add_message(sid, role, text)
        stamp(self.storage, when)

    def test_each_month_gets_its_own_file_with_session_headings(self):
        sid = self.storage.create_session()
        self.storage.rename_session(sid, "강아지 영상")
        self.say(sid, "user", "강아지 바꿔 줘", "2026-11-15T03:00:00+00:00")
        self.say(sid, "assistant", "바꿨습니다 ![결과](alex-video:x.mp4)", "2026-11-15T03:05:00+00:00")
        self.say(sid, "user", "12월에 다시", "2026-12-15T03:00:00+00:00")
        self.assertEqual(self.archive.sync(), 3)
        november = (self.root / "archive" / "26년 11월.md").read_text(encoding="utf-8")
        december = (self.root / "archive" / "26년 12월.md").read_text(encoding="utf-8")
        self.assertTrue(november.startswith("# 26년 11월 대화 기록"))
        self.assertEqual(november.count("## 2026-11-15"), 1)  # 같은 대화방은 머리글 한 번
        self.assertIn("· 강아지 영상", november)
        self.assertIn("**사용자** ·", november)
        self.assertIn("**ALEX** ·", november)
        self.assertIn("[영상: 결과 — 원본 보관 안 함]", november)
        self.assertNotIn("alex-video:", november)
        self.assertIn("12월에 다시", december)
        self.assertNotIn("12월에 다시", november)

    def test_only_new_messages_are_appended(self):
        sid = self.storage.create_session()
        self.say(sid, "user", "첫째", "2026-11-10T03:00:00+00:00")
        self.archive.sync()
        self.assertEqual(self.archive.sync(), 0)
        self.say(sid, "assistant", "둘째", "2026-11-10T03:01:00+00:00")
        self.assertEqual(self.archive.sync(), 1)
        text = (self.root / "archive" / "26년 11월.md").read_text(encoding="utf-8")
        self.assertEqual((text.count("첫째"), text.count("둘째"), text.count("## ")), (1, 1, 1))
        self.storage.delete_session(sid)  # 기록한 뒤에 지워도 월별 기록에는 남는다
        self.assertIn("첫째", (self.root / "archive" / "26년 11월.md").read_text(encoding="utf-8"))

    def test_switching_rooms_adds_a_heading_and_projects_are_named(self):
        project = self.storage.create_project(OWNER, "1인 팹리스")
        a, b = self.storage.create_session(), self.storage.create_session(project_id=project)
        self.say(a, "user", "가", "2026-11-10T03:00:00+00:00")
        self.say(b, "user", "나", "2026-11-10T03:01:00+00:00")
        self.say(a, "user", "다", "2026-11-10T03:02:00+00:00")
        self.archive.sync()
        text = (self.root / "archive" / "26년 11월.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("\n## "), 3)
        self.assertEqual(text.count("· 프로젝트 1인 팹리스"), 1)

    def test_members_are_kept_apart(self):
        member = self.storage.create_user("친구", "friend", "x").user_id
        mine, theirs = self.storage.create_session(), self.storage.create_session(owner=member)
        self.say(mine, "user", "내 이야기", "2026-11-10T03:00:00+00:00")
        self.say(theirs, "user", "친구 이야기", "2026-11-10T03:00:00+00:00")
        self.archive.sync()
        mine_text = (self.root / "archive" / "26년 11월.md").read_text(encoding="utf-8")
        theirs_text = (self.root / "archive" / f"user-{member}" / "26년 11월.md").read_text(encoding="utf-8")
        self.assertIn("내 이야기", mine_text)
        self.assertNotIn("친구 이야기", mine_text)
        self.assertIn("친구 이야기", theirs_text)

    def test_nothing_before_november_2026(self):
        sid = self.storage.create_session()
        self.say(sid, "user", "10월 이야기", "2026-10-15T03:00:00+00:00")
        self.assertEqual(self.archive.sync(), 0)
        self.assertFalse((self.root / "archive").exists())
        self.say(sid, "user", "11월 이야기", "2026-11-15T03:00:00+00:00")
        self.assertEqual(self.archive.sync(), 1)
        self.assertEqual([p.name for p in (self.root / "archive").iterdir()], ["26년 11월.md"])

    def test_big_backlog_is_written_in_batches(self):
        sid = self.storage.create_session()
        for n in range(7):
            self.say(sid, "user", f"메시지{n}", "2026-11-10T03:00:00+00:00")
        self.assertEqual(self.archive.sync(limit=3), 7)
        self.assertEqual((self.root / "archive" / "26년 11월.md").read_text(encoding="utf-8").count("메시지"), 7)


class YearReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = ConversationArchive(make_core(self.root).storage, self.root / "archive")
        self.archive.root.mkdir()
        for month in (11, 2, 12):
            (self.archive.root / f"26년 {month}월.md").write_text(f"# 26년 {month}월\n{month}월 이야기", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_report_summarizes_each_month_then_the_year(self):
        calls = []

        def generate(messages):
            calls.append(messages[-1].content)
            return "올해 정리" if len(calls) == 4 else f"요약{len(calls)}"
        self.assertEqual([path.name for path in self.archive.months(2026)], ["26년 2월.md", "26년 11월.md", "26년 12월.md"])
        self.assertEqual(self.archive.needs_year_report(datetime(2027, 1, 1)), 2026)
        self.assertIsNone(self.archive.needs_year_report(datetime(2026, 12, 31)))  # 아직 그해
        path = self.archive.year_report(2026, generate)
        self.assertEqual(path.name, "26년 대화.md")
        self.assertEqual(len(calls), 4)                       # 달마다 하나 + 마지막 보고서
        self.assertIn("2월 이야기", calls[0])
        self.assertIn("### 26년 12월\n요약3", calls[3])
        text = path.read_text(encoding="utf-8")
        self.assertIn("올해 정리", text)
        self.assertIn("## 부록: 월별 요약", text)
        self.assertIsNone(self.archive.needs_year_report(datetime(2027, 1, 2)))  # 한 번만
        self.assertTrue((self.archive.root / "26년 11월.md").is_file())  # 월별 원본은 그대로


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = make_core(Path(self.temp.name))
        self.core.archive.start = EARLY  # 오늘 날짜로 시험
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_status(self):
        self.core.storage.add_message(self.sid, "user", "안녕")
        reply, keep, _ = handle_command(self.core, self.sid, "/archive")
        self.assertTrue(keep)
        self.assertIn("월별 대화 기록:", reply)
        self.assertIn(month_name(datetime.now()), reply)

    def test_year_command_needs_a_year_with_records(self):
        self.assertIn("사용법", handle_command(self.core, self.sid, "/archive-year")[0])
        self.assertIn("기록이 없습니다", handle_command(self.core, self.sid, "/archive-year 2001")[0])


class WebTests(unittest.TestCase):
    def test_every_answer_is_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            state = make_state(Path(temp))
            state.core.archive.start = EARLY
            state.process("월별 기록 시험")
            text = (state.core.archive.folder(OWNER) / month_name(datetime.now())).read_text(encoding="utf-8")
            self.assertIn("월별 기록 시험", text)
            self.assertIn("**ALEX**", text)

    def test_read_only_blocks_the_year_report(self):
        with tempfile.TemporaryDirectory() as temp:
            state = make_state(Path(temp))
            reply = state.process("/archive-year 2026", permission="read")["response"]
            self.assertEqual(reply, READ_ONLY_BLOCKED)
            self.assertNotEqual(state.process("/archive", permission="read")["response"], READ_ONLY_BLOCKED)


if __name__ == "__main__":
    unittest.main()
