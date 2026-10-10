"""시작 배너 회귀 테스트.

배너는 세로로 A-L-E-X 를 읽고 가로로 그 약자를 편다. 줄을 고치다 보면 세로 축이
조용히 깨지는데, 화면을 직접 보지 않으면 모른다. 여기서 고정한다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.app import BANNER  # noqa: E402


def acrostic_lines() -> list[str]:
    return [line for line in BANNER.splitlines() if line.strip()
            and line.strip()[0] in "ALEX" and len(line.strip()) > 1
            and line.strip()[1] == " "]


class BannerTests(unittest.TestCase):
    def test_vertical_letters_spell_alex(self):
        letters = [line.strip()[0] for line in acrostic_lines()]
        self.assertEqual(letters, ["A", "L", "E", "X"])

    def test_each_line_expands_its_own_letter(self):
        """가로로 편 단어가 그 줄의 글자로 시작해야 한다. X 는 eXperience/eXistence 다."""
        for line in acrostic_lines():
            letter, expansion = line.strip()[0], line.strip()[1:].strip()
            if letter == "X":
                self.assertIn("X", expansion, line)
            else:
                self.assertTrue(expansion.startswith(letter), line)

    def test_both_meanings_are_present(self):
        """PROJECT_ALEX.md 1절의 두 의미. A/L/E 를 공유하고 X 만 갈린다."""
        self.assertIn("eXperience", BANNER)
        self.assertIn("eXistence", BANNER)

    def test_banner_names_the_project(self):
        self.assertIn("Project ALEX", BANNER)

    def test_ascii_only_so_cp949_console_does_not_break_it(self):
        """.cmd 한글이 cp949 로 깨진 전례가 있다. 배너는 콘솔에 직접 찍히므로 ASCII 로 둔다."""
        BANNER.encode("ascii")


if __name__ == "__main__":
    unittest.main()
