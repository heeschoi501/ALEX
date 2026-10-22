"""런처가 이미 떠 있는 **옛 코드** 서버를 조용히 재사용하지 않는지 (2026-09-25).

런처는 같은 프로젝트의 ALEX 가 떠 있으면 그 서버를 그대로 쓴다(두 개 띄우지 않으려고).
그런데 그 뒤에 코드가 바뀌었는지는 보지 않았다. 2026-09-25 에 웹 읽기를 넣고 런처를 다시
켰더니 07:56 부터 떠 있던 서버가 계속 응답해서 `/status` 에 새 기능이 없었다 — 사용자는
업데이트한 줄 알고 옛 버전을 쓰게 된다. ADR-049(조용히 덮어쓰이는 것 금지)와 같은 종류다.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import launcher
from jarvis.config import load_settings


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pkg = Path(self._tmp.name)
        (self.pkg / "core.py").write_text("x = 1\n", encoding="utf-8")
        (self.pkg / "static").mkdir()
        (self.pkg / "static" / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_same_content_same_fingerprint(self):
        self.assertEqual(launcher.code_fingerprint(self.pkg), launcher.code_fingerprint(self.pkg))

    def test_python_change_changes_fingerprint(self):
        before = launcher.code_fingerprint(self.pkg)
        (self.pkg / "core.py").write_text("x = 2\n", encoding="utf-8")
        self.assertNotEqual(before, launcher.code_fingerprint(self.pkg))

    def test_static_ui_change_changes_fingerprint(self):
        before = launcher.code_fingerprint(self.pkg)
        (self.pkg / "static" / "index.html").write_text("<p>new</p>", encoding="utf-8")
        self.assertNotEqual(before, launcher.code_fingerprint(self.pkg))

    def test_touching_without_changing_content_does_not_matter(self):
        """압축 해제·복사는 수정 시각만 바꾼다. 그걸로 옛 코드라고 하면 안 된다."""
        before = launcher.code_fingerprint(self.pkg)
        os.utime(self.pkg / "core.py", (1, 1))
        self.assertEqual(before, launcher.code_fingerprint(self.pkg))

    def test_bytecode_cache_is_ignored(self):
        before = launcher.code_fingerprint(self.pkg)
        (self.pkg / "__pycache__").mkdir()
        (self.pkg / "__pycache__" / "core.cpython-311.py").write_text("junk", encoding="utf-8")
        self.assertEqual(before, launcher.code_fingerprint(self.pkg))

    def test_the_running_web_module_reports_the_code_it_loaded(self):
        from jarvis import web
        self.assertEqual(web.LOADED_CODE, launcher.code_fingerprint())


class ExistingServerTests(unittest.TestCase):
    def launch_against(self, health):
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(launcher.os, "chdir"), \
             patch.object(launcher, "get_json", return_value=health):
            return launcher.launch()

    def health(self, **overrides):
        with patch.dict(os.environ, {"JARVIS_USE_OLLAMA": "1", "JARVIS_LOCAL_ONLY": "1"}):
            instance = launcher.instance_id(load_settings().data_dir)
        data = {"app": "jarvis", "instance": instance, "local_only": True,
                "code": launcher.code_fingerprint()}
        data.update(overrides)
        return data

    def test_current_server_is_reused(self):
        self.assertEqual(self.launch_against(self.health()), launcher.WEB)

    def test_server_running_older_code_is_not_silently_reused(self):
        # 2026-10-23: 옛 버전이면 웹만 끄고 새로 켠다(일하는 중이면 끄지 않고 알림) — 실제 8765 에는 절대 닿지 않게 가짜로
        busy = RuntimeError("ALEX 가 지금 일하는 중이라 새 버전으로 다시 켜지 않았습니다.")
        with patch.object(launcher, "stop_outdated_web", side_effect=busy) as stop:
            with self.assertRaises(RuntimeError) as ctx:
                self.launch_against(self.health(code="0000000000000000"))
        stop.assert_called_once()
        self.assertIn("일하는 중", str(ctx.exception))
        with patch.object(launcher, "stop_outdated_web") as stop:
            self.assertEqual(self.launch_against(self.health()), launcher.WEB)  # 같은 코드면 그대로 쓰고 끄지 않는다
        stop.assert_not_called()


    def test_server_from_before_this_check_counts_as_old(self):
        health = self.health()
        del health["code"]
        with patch.object(launcher, "stop_outdated_web", side_effect=RuntimeError("busy")) as stop, \
                self.assertRaises(RuntimeError):
            self.launch_against(health)
        stop.assert_called_once()  # 지문 없는 옛 서버도 옛 코드로 보고 끄려 한다(가짜 — 실제 8765 에 닿지 않음)


class StopOutdatedWebTests(unittest.TestCase):
    """옛 웹 끄기 — 가짜 웹(임의 포트)으로만. 화면 토큰을 읽어 '앱 종료' 를 부르고, 꺼질 때까지 기다린다."""

    def serve(self, shutdown_status):
        import http.server
        import threading
        calls = []

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path == "/":
                    body = b'<meta name="jarvis-token" content="tok-1">'
                    self.send_response(200)
                else:
                    body = b'{"app": "jarvis"}'
                    self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                calls.append((self.path, self.headers.get("X-Jarvis-Token")))
                self.send_response(shutdown_status)
                self.end_headers()
                self.wfile.write(b"{}")
                if shutdown_status == 200:
                    threading.Thread(target=server.shutdown, daemon=True).start()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_address[1]}", calls

    def test_idle_server_is_stopped_with_its_page_token(self):
        url, calls = self.serve(200)
        with patch.object(launcher, "WEB", url):
            launcher.stop_outdated_web(wait_seconds=5)
        self.assertEqual(calls, [("/api/shutdown", "tok-1")])

    def test_busy_server_is_left_running(self):
        url, calls = self.serve(409)
        with patch.object(launcher, "WEB", url), self.assertRaises(RuntimeError) as ctx:
            launcher.stop_outdated_web(wait_seconds=1)
        self.assertIn("일하는 중", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
