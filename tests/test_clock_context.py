"""모델에게 '지금' 을 알려주는 경로의 회귀 테스트.

2026-09-21 에 사용자가 날짜를 묻자 로컬 모델이 **"2023년 10월 10일입니다" 라고 단정했다.**
학습 데이터의 날짜를 사실처럼 말한 것이다. 앱이 도는 기계는 시계를 아는데 모델에게
알려준 적이 없었다 — DEFAULT_SYSTEM_PROMPT 에도, SystemInfoTool 에도, router 에도 없었다.
IDENTITY.md I-2(사실과 추정 구분)가 실패하는 가장 흔한 형태다.
"""
import locale
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.core import KOREAN_WEEKDAYS, clock_line  # noqa: E402
from jarvis.hardware import detect_hardware  # noqa: E402
from jarvis.tools import SystemInfoTool  # noqa: E402


class ClockLineTests(unittest.TestCase):
    def test_korean_weekday_is_computed_in_code(self):
        """영어 (Monday) 를 주고 옮기게 했더니 8B 가 '수요일' 이라고 답했다.
        코드가 할 수 있는 변환을 모델에게 시키면 그 단계에서 틀린다."""
        line = clock_line(datetime(2026, 9, 21, 10, 45))   # 실제로 월요일
        self.assertIn("월요일", line)
        self.assertNotIn("Monday", line)

    def test_tomorrow_is_given_so_the_model_need_not_add_a_day(self):
        """'내일은 무슨 요일?' 에 8B 가 '금요일' 이라고 답했다(실제 화요일)."""
        line = clock_line(datetime(2026, 9, 21, 10, 45))
        self.assertIn("화요일", line)

    def test_every_weekday_maps_correctly(self):
        base = datetime(2026, 9, 21)  # 월요일
        for offset in range(7):
            day = base + timedelta(days=offset)
            self.assertIn(f"{KOREAN_WEEKDAYS[day.weekday()]}요일", clock_line(day))

    def test_year_month_day_are_all_present(self):
        line = clock_line(datetime(2026, 9, 21, 10, 45))
        for piece in ("2026", "09", "21", "10시", "45분"):
            self.assertIn(piece, line)

    def test_it_tells_the_model_not_to_compute_dates_itself(self):
        self.assertIn("스스로 계산하지 않는다", clock_line(datetime(2026, 9, 21)))

    def test_works_when_the_locale_code_page_cannot_encode_korean(self):
        """2026-09-25 데스크톱: 시스템 로캘이 en-US(cp1252)라 모든 대화가 이 줄에서 멈췄다.

        Windows 의 strftime 은 서식 문자열을 C 로캘 코드페이지로 인코딩한다. 서식에 '년' 이
        있으면 cp1252 에서 UnicodeEncodeError 다. 노트북 로캘에서는 통과했으므로 기계 로캘에
        기대지 않고 로캘을 직접 바꿔서 잰다. 출력은 고치기 전과 글자 하나 다르지 않아야 한다.
        """
        previous = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "English_United States.1252" if os.name == "nt" else "C")
        except locale.Error:
            self.skipTest("cp1252 로캘을 쓸 수 없는 환경")
        try:
            line = clock_line(datetime(2026, 9, 21, 10, 45))
        finally:
            locale.setlocale(locale.LC_CTYPE, previous)
        self.assertTrue(line.startswith(
            "오늘은 2026년 09월 21일 월요일이고 지금은 10시 45분이다. "
            "내일은 2026년 09월 22일 화요일이다. "), line)


class SystemInfoClockTests(unittest.TestCase):
    def test_system_info_reports_the_time_first(self):
        """이 도구는 '이 기계에 대해 아는 것' 을 보고하는 자리인데 시계가 빠져 있었다."""
        first = SystemInfoTool(detect_hardware()).run().splitlines()[0]
        self.assertTrue(first.startswith("now: "), first)


if __name__ == "__main__":
    unittest.main()
