"""코드 답 뒤쪽 실행 확인 — 코드는 답으로 주고, ALEX 가 보이지 않는 임시 폴더에서 몇 초 돌려 본다(2026-10-05).

사용자: "코드를 짜서 나한테 주는 게 아니라 그냥 직접 만들어버리면 어쩌자는 거야", "그냥 코드를 짜서 달라고 한 건데".
같은 날 실측: 한 번에 쓴 테트리스는 Q3 9번 중 6번이 시작하자마자 실제 버그로 죽었다 — 시작 직후 실행만 해도 잡힌다.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.code_check import check_code, extract_python, footer  # noqa: E402
from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import MockLLMProvider  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

CRASHING = '''class Game:
    def __init__(self):
        self.reset()

    def reset(self):
        self.piece = self.new_piece()
        self.next_kind = "T"

    def new_piece(self):
        return self.next_kind  # 만들기 전에 씀 — 2026-10-05 실측 테트리스와 같은 버그


if __name__ == "__main__":
    Game()
    print("started")
'''
FIXED = CRASHING.replace('''        self.piece = self.new_piece()
        self.next_kind = "T"''', '''        self.next_kind = "T"
        self.piece = self.new_piece()''')


def answer(code, text="테트리스입니다."):
    return f"{text}\n\n```bash\npip install pygame\n```\n\n```python\n{code}```\n\n실행: python tetris.py"


class ExtractTests(unittest.TestCase):
    def test_the_python_block_is_picked_not_the_bash_one(self):
        self.assertEqual(extract_python(answer(FIXED)), FIXED)
        self.assertEqual(extract_python("설명만 있습니다."), None)
        self.assertEqual(extract_python("```\nx = 1\nprint(x)\n```"), "x = 1\nprint(x)\n")
        self.assertIsNone(extract_python("```\n$ pip install pygame\n```"))


class CheckTests(unittest.TestCase):
    def test_a_startup_crash_is_found_by_running(self):
        result = check_code(CRASHING)
        self.assertEqual(result.status, "failed")
        self.assertIn("next_kind", result.detail)
        self.assertEqual(check_code(FIXED).status, "ok_finished")

    def test_a_long_running_program_without_errors_is_ok(self):
        result = check_code("import time\nwhile True:\n    time.sleep(0.05)\n", seconds=1)
        self.assertEqual(result.status, "ok_running")

    def test_syntax_errors_fail_without_running(self):
        result = check_code("def broken(:\n    pass\n")
        self.assertEqual(result.status, "failed")
        self.assertIn("문법 오류", result.detail)

    def test_what_cannot_be_run_is_said_not_guessed(self):
        for code, words in (("import pygame_not_installed_here\npygame_not_installed_here.init()\n", "설치돼 있지 않아"),
                            ("import tkinter\nroot = tkinter.Tk()\nroot.mainloop()\n", "창을 띄우는"),
                            ("name = input('이름? ')\nprint(name)\n", "입력을 받는"),
                            ("import subprocess\nsubprocess.run(['dir'])\n", "실행 보호")):
            with self.subTest(code=code):
                result = check_code(code)
                self.assertEqual(result.status, "unverifiable")
                self.assertIn(words, result.detail)

    def test_a_partial_snippet_is_not_reported_as_checked(self):
        # 2026-10-05 08:22: 회전표 조각만 준 답에 '실행 확인: 오류 없이 끝남' 이 붙었다
        snippet = "SHAPES[1] = [\n    [[0,0,0,0],[1,1,1,1],[0,0,0,0],[0,0,0,0]],\n]\n"
        for code in (snippet, "def rotate(m):\n    return [list(r) for r in zip(*m[::-1])]\n",
                     "class Piece:\n    def rotate(self):\n        self.rot = (self.rot + 1) % 4\n"):
            with self.subTest(code=code):
                result = check_code(code)
                self.assertEqual(result.status, "partial")
                self.assertIn("실행 확인 안 함", footer(result, []))
        self.assertEqual(check_code("for n in range(2, 10):\n    print(n)\n").status, "ok_finished")
        self.assertEqual(check_code('"""소수"""\nimport math\nprint(math.sqrt(4))\n').status, "ok_finished")

    @unittest.skipUnless(sys.platform == "win32", "Windows 레지스트리")
    def test_registry_reads_pass_but_registry_writes_are_blocked(self):
        # pygame SysFont·mimetypes 는 레지스트리를 읽는다(2026-10-05: 이것을 막아 pygame 게임 5/6 이 확인 불가였다)
        self.assertEqual(check_code("import mimetypes\nmimetypes.init()\nprint(len(mimetypes.types_map) > 0)\n").status,
                         "ok_finished")
        import winreg
        name = "Software\\ALEXGuardTest20261005"
        result = check_code(f'exec("import winreg; winreg.CreateKey(winreg.HKEY_CURRENT_USER, {name!r})")\n')
        try:
            self.assertEqual(result.status, "unverifiable")
            self.assertIn("실행 보호", result.detail)
            with self.assertRaises(OSError):
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, name)
        finally:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)
            except OSError:
                pass

    def test_a_pygame_program_with_system_fonts_runs(self):
        try:
            import pygame  # noqa: F401
        except ImportError:
            self.skipTest("pygame 없음")
        code = ("import pygame\npygame.init()\nscreen = pygame.display.set_mode((100, 100))\n"
                "font = pygame.font.SysFont('consolas', 16)\nscreen.blit(font.render('1', True, (255, 255, 255)), (0, 0))\n"
                "pygame.display.flip()\npygame.quit()\n")
        self.assertEqual(check_code(code).status, "ok_finished", check_code(code).output[-800:])

    def test_nothing_is_left_behind(self):
        before = set(Path(tempfile.gettempdir()).glob("alex-code-check-*"))
        check_code(FIXED)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("alex-code-check-*")), before)

    def test_footer_says_what_happened(self):
        ok = check_code(FIXED)
        self.assertIn("실행 확인:", footer(ok, []))
        self.assertIn("ALEX가 고쳤습니다", footer(ok, ["AttributeError"]))
        self.assertIn("실행 확인 못 함", footer(check_code("import pygame_not_installed_here\npygame_not_installed_here.init()\n"), []))


class Coder(MockLLMProvider):
    def __init__(self, replies):
        super().__init__("coder", "C")
        self.replies = list(replies)
        self.prompts = []

    def generate(self, messages, **kwargs):
        self.prompts.append([m.content for m in messages])
        return self.replies.pop(0) if self.replies else "더 드릴 답이 없습니다."


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def core(self, replies, **flags):
        coder = Coder(replies)
        core = JarvisCore(
            storage=Storage(Path(self._tmp.name) / "t.db"),
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=MockLLMProvider("general", "G"), local_coder=coder,
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True, **flags)
        return core, coder, core.get_or_create_session(resume=False)

    def test_a_crashing_answer_is_fixed_before_the_user_sees_it(self):
        core, coder, session = self.core([answer(CRASHING), answer(FIXED, "고친 코드입니다.")], code_check=True)
        reply, _ = core.handle(session, "파이썬 코드로 테트리스 게임 만들어줘")
        self.assertIn('self.next_kind = "T"\n        self.piece = self.new_piece()', reply)  # 고친 코드가 답이다
        self.assertIn("ALEX가 고쳤습니다", reply)
        self.assertIn("실행 확인:", reply)
        self.assertIn("next_kind", coder.prompts[1][-1])  # 실제 오류를 주고 고치게 했다
        self.assertFalse((Path(self._tmp.name) / "builds").exists())  # 사용자 쪽 파일 없음

    def test_a_follow_up_fix_is_checked_with_the_conversation_context(self):
        core, coder, session = self.core([answer(FIXED), answer(FIXED, "다시 드립니다.")], code_check=True)
        core.handle(session, "파이썬 코드로 테트리스 게임 만들어줘")
        reply, decision = core.handle(session, "아직 제대로 작성안되는데 코드 다시 만들어줘")
        self.assertIn("실행 확인:", reply)
        self.assertTrue(any("테트리스" in content for content in coder.prompts[1]))  # 앞 대화가 함께 간다

    def test_when_the_fix_still_fails_it_says_so(self):
        core, _, session = self.core([answer(CRASHING), answer(CRASHING), answer(CRASHING)], code_check=True)
        reply, _ = core.handle(session, "파이썬 코드로 테트리스 게임 만들어줘")
        self.assertIn("실행 확인 안 됨", reply)

    def test_explanations_are_not_run(self):
        core, coder, session = self.core([answer(CRASHING)], code_check=True)
        reply, _ = core.handle(session, "파이썬 클래스 설명해줘")
        self.assertNotIn("실행 확인", reply)
        self.assertEqual(len(coder.prompts), 1)

    def test_switched_off_the_answer_is_left_alone(self):
        core, coder, session = self.core([answer(CRASHING)])
        reply, _ = core.handle(session, "파이썬 코드로 테트리스 게임 만들어줘")
        self.assertNotIn("실행 확인", reply)
        self.assertEqual(len(coder.prompts), 1)


if __name__ == "__main__":
    unittest.main()
