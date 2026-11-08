"""ALEX 가 자기 기능을 스스로 안다·스스로 점검(2026-10-23) — 명령 목록·화면에서 뽑은 줄, 기능 질문일 때만 붙이기, 점검이 틀린 답을 교훈으로."""
import tempfile
import unittest
from pathlib import Path

from jarvis.selfknow import SelfKnowledge, help_lines, principle_lines, ui_lines

ROOT = Path(__file__).resolve().parents[1]

HELP = """사용 가능한 명령
  /read 상대/경로.py        승인된 루트에서 작은 텍스트 파일 읽기
  /edit-file 경로 :: 지시    Local 모델이 수정안을 만들고 diff만 표시 (자동 적용 없음)
  /search 검색어            웹 검색 + 상위 결과 한 쪽을 읽고 Local 모델이 답
  /image 설명               그림 그리기(ComfyUI)
                            그냥 '그려줘' 라고 말해도 된다
"""
PAGE = ('<button id="a" title="코딩 세션 — 폴더의 파일을 직접 읽고 쓰고 고치고 시험" aria-label="코딩 세션"><svg></svg></button>'
        '<button role="menuitem" title="같은 기능의 메뉴">코딩 세션</button>'
        '<button title="폰·AI 안경에서 한국어 자막으로">실시간 통역</button><button>제목 없음</button>')


def core_with(knowledge, temp):
    from jarvis.core import JarvisCore
    from jarvis.hardware import HardwareInfo, PerformanceProfile
    from jarvis.providers import CloudStubProvider, MockLLMProvider
    from jarvis.storage import Storage
    core = JarvisCore(storage=Storage(Path(temp) / "alex.db"),
                      hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
                      local_general=MockLLMProvider("general", "GENERAL"), local_coder=MockLLMProvider("coder", "CODER"),
                      cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"))
    core.self_knowledge = knowledge
    return core


class SelfKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.knowing = SelfKnowledge(HELP, PAGE)

    def test_lines_come_from_the_help_text_and_the_page(self):
        self.assertEqual(help_lines(HELP)[3], "명령 /image 설명 — 그림 그리기(ComfyUI) 그냥 '그려줘' 라고 말해도 된다")
        pages = ui_lines(PAGE)
        self.assertEqual(len(pages), 2)  # 같은 이름은 한 번, 설명 없는 버튼은 뺀다
        self.assertTrue(pages[0].startswith("화면 '코딩 세션' — 코딩 세션 — 폴더의 파일을"))

    def test_capability_questions_get_the_matching_lines_only(self):
        block = self.knowing.block("현재 파일 읽기 쓰기 같은것도 가능하지?")
        self.assertIn("/read", block)
        self.assertIn("코딩 세션", block)
        self.assertNotIn("/search", block)
        self.assertIn("/image", self.knowing.block("그림 그릴 수 있어?"))
        self.assertIn("/search", self.knowing.block("웹 검색 가능해?"))
        self.assertEqual(self.knowing.block("파이썬으로 리스트 정렬하는 법 알려줘"), "")  # 기능 질문이 아니면 붙이지 않는다
        self.assertIn("명령 /read", self.knowing.block("너 뭘 할 수 있어?"))           # 통째로 물으면 이름 목록

    def test_the_block_reaches_the_chat_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            core = core_with(self.knowing, temp)
            sid = core.get_or_create_session(False)
            core.storage.add_message(sid, "user", "파일 읽기도 가능해?")
            systems = [m.content for m in core._build_messages(sid) if m.role == "system"]
            self.assertTrue(any("ALEX 의 실제 기능" in text and "/read" in text for text in systems))


class PrincipleTests(unittest.TestCase):
    """2026-11-08 사용자 "ALEX도 학습할수있게 해줘" — Codex·Claude 가 읽는 AGENTS.md 를 ALEX 도 같은 내용으로 안다."""

    def test_the_repository_principles_are_read_from_agents_md(self):
        lines = principle_lines(ROOT / "AGENTS.md")
        self.assertGreaterEqual(len(lines), 5)
        self.assertTrue(any(line.startswith("개발 원칙 '기억은 지우지 않는다' — ") for line in lines))
        self.assertEqual(principle_lines(ROOT / "없는-파일.md"), [])

    def test_a_question_about_principles_gets_them_and_others_do_not(self):
        knowing = SelfKnowledge(HELP, PAGE, extra=tuple(principle_lines(ROOT / "AGENTS.md")))
        self.assertIn("개발 원칙 '흡수'", knowing.block("너의 개발 원칙이 뭐야?"))
        self.assertIn("기억은 지우지 않는다", knowing.block("배운 기억을 지우는 규칙 있어?"))
        self.assertEqual(knowing.block("파이썬으로 리스트 정렬하는 법 알려줘"), "")

    def test_the_coding_harness_reads_the_same_file_whole(self):
        from jarvis.harness.prompt import system_prompt
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text), 8000)  # the harness keeps the first 8,000 characters
        self.assertIn(text.strip().splitlines()[-1], system_prompt(ROOT))


class SelfCheckTests(unittest.TestCase):
    def test_wrong_answers_become_approved_lessons_once_and_the_bank_rotates(self):
        from jarvis.selfcheck import questions, run
        knowing = SelfKnowledge(HELP, PAGE)
        bank = questions(knowing.lines)
        self.assertEqual(bank[0]["expect"], ["/read"])
        self.assertEqual(bank[-1]["expect"], ["실시간 통역"])
        with tempfile.TemporaryDirectory() as temp:
            core = core_with(knowing, temp)
            replies = {"/read": "/read 경로 로 읽으면 됩니다", "/edit-file": "그건 못 합니다"}
            answer = lambda question: next((v for k, v in replies.items() if k == bank[[b["question"] for b in bank].index(question)]["expect"][0]), "모름")  # noqa: E731
            report = run(core, 2, folder=Path(temp) / "sc", answer=answer, cool=lambda: True)
            self.assertIn("통과 1, 틀려서 교훈으로 남김 1", report)
            lessons = core.storage.list_experiences(stage="approved", kind="answer")
            self.assertEqual(len(lessons), 1)
            self.assertTrue(lessons[0].source.startswith("selfcheck:"))
            self.assertIn("/edit-file", lessons[0].correction)
            report = run(core, 1, folder=Path(temp) / "sc", answer=answer, cool=lambda: True)  # 다음 질문부터
            self.assertIn("/search", report)
            run(core, len(bank), folder=Path(temp) / "sc", answer=lambda q: "모름", cool=lambda: True)
            sources = [e.source for e in core.storage.list_experiences(stage="approved", kind="answer")]
            self.assertEqual(len(sources), len(set(sources)))  # 같은 질문의 교훈은 한 번만
            self.assertIn("온도", run(core, 3, folder=Path(temp) / "sc", answer=answer, cool=lambda: False))

    def test_command_is_admin_only(self):
        from jarvis.app import handle_command
        with tempfile.TemporaryDirectory() as temp:
            core = core_with(SelfKnowledge(HELP, PAGE), temp)
            sid = core.get_or_create_session(False)
            core.member_mode = True
            self.assertIn("관리자만", handle_command(core, sid, "/self-check 3")[0])
            core.member_mode = False
            self.assertIn("사용법", handle_command(core, sid, "/self-check many")[0])


if __name__ == "__main__":
    unittest.main()
