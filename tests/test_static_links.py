"""채팅 링크 렌더링의 보안 속성이 빠지지 않게 고정한다 (2026-09-25).

동작 검사는 실제 크롬에서 한다(`logs/claude-links-ui-smoke.py`, 19개). 여기서는 크롬 없이도
돌아가는 정적 확인만 한다 — 누군가 링크를 고치다 noopener 나 scheme 검사를 지우면 여기서 잡힌다.
웹 답의 출처가 링크가 되면서 ALEX 탭에서 바깥 페이지로 나가는 길이 생겼다. 그 길의 조건이다.
"""
import unittest
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "jarvis" / "static" / "app.js").read_text(encoding="utf-8")


class StaticLinkSafetyTests(unittest.TestCase):
    def test_links_open_in_a_new_tab_without_opener_or_referrer(self):
        self.assertIn("a.target = '_blank'", APP)
        self.assertIn("a.rel = 'noopener noreferrer'", APP)

    def test_only_http_and_https_become_links(self):
        self.assertIn("u.protocol === 'http:' || u.protocol === 'https:'", APP)

    def test_rendering_never_parses_html(self):
        self.assertNotIn("innerHTML", APP)
        self.assertNotIn("insertAdjacentHTML", APP)
        self.assertNotIn("outerHTML", APP)

    def test_source_line_format_matches_what_core_writes(self):
        """core._compose_web_answer 가 '[n] 제목 — 주소' 로 쓴다. 둘이 어긋나면 제목 링크가 사라진다."""
        from jarvis import core
        import inspect
        self.assertIn('f"[{n}] {title} — {url}"', inspect.getsource(core.JarvisCore._compose_web_answer))
        self.assertIn(r"(.+) — (https?:\/\/\S+)$", APP)


if __name__ == "__main__":
    unittest.main()
