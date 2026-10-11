"""Loopback Web Alpha tests. No external network or browser needed."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.storage import Storage
from jarvis.web import LOOPBACK_HOST, WebAppState, make_server


def make_state(root: Path) -> WebAppState:
    core = JarvisCore(
        storage=Storage(root / "web.db"),
        hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
        local_general=MockLLMProvider("general", "GENERAL"),
        local_coder=MockLLMProvider("coder", "CODER"),
        cloud_coder=CloudStubProvider("cloud-coder", "CLOUD"),
        cloud_reasoning=CloudStubProvider("cloud-reasoning", "CLOUD"),
        require_cloud_approval=True,
        approval_callback=lambda _request: False,
    )
    return WebAppState(core, core.get_or_create_session(False), token="test-token")


class WebStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_message_uses_core(self):
        result = self.state.process("안녕")
        self.assertIn("GENERAL", result["response"])
        self.assertEqual(result["route"], "local_general")

    def test_new_command_updates_session(self):
        old = self.state.session_id
        result = self.state.process("/new")
        self.assertNotEqual(result["session_id"], old)
        self.assertEqual(self.state.session_id, result["session_id"])

    def test_exit_does_not_stop_web_server(self):
        result = self.state.process("/exit")
        self.assertIn("Ctrl+C", result["response"])

    def test_oversized_message_rejected(self):
        with self.assertRaises(ValueError):
            self.state.process("x" * 32001)

    def test_web_forces_cloud_approval_even_if_core_was_configured_off(self):
        class TrackingCloud(CloudStubProvider):
            def __init__(self):
                super().__init__("cloud", "CLOUD")
                self.calls = 0

            def generate(self, messages, **kwargs):
                self.calls += 1
                return super().generate(messages, **kwargs)

        cloud = TrackingCloud()
        core = JarvisCore(
            storage=Storage(self.root / "unsafe-global.db"),
            hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("general", "GENERAL"),
            local_coder=MockLLMProvider("coder", "CODER"),
            cloud_coder=cloud,
            cloud_reasoning=cloud,
            require_cloud_approval=False,
            approval_callback=None,
        )
        state = WebAppState(core, core.get_or_create_session(False), token="test-token")
        result = state.process("/ask-cloud 프로젝트 전체 구조를 심층 분석해줘")
        self.assertEqual(cloud.calls, 0)
        self.assertIn("local 대체", result["response"])
        self.assertTrue(core.require_cloud_approval)


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_server_binds_loopback(self):
        self.assertEqual(self.server.server_address[0], LOOPBACK_HOST)

    def test_root_has_security_headers_and_tokenized_page(self):
        with urllib.request.urlopen(self.base + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("Project ALEX", body)
            self.assertIn("test-token", body)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src", response.headers["Content-Security-Policy"])

    def test_root_rejects_non_local_host_header_before_exposing_token(self):
        request = urllib.request.Request(self.base + "/", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(ctx.exception.code, 403)
        body = ctx.exception.read().decode("utf-8")
        self.assertNotIn("test-token", body)

    # ── 휴대폰·노트북에서 앱처럼(2026-10-11): Tailscale Serve 주소 + 허용 로그인만 ──────────────────────
    def _get(self, path, headers):
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read().decode("utf-8", "replace"), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), exc.headers

    def test_remote_app_host_needs_the_configured_tailscale_login(self):
        from jarvis.config import Settings

        host = "my-pc.tail1234.ts.net"
        self.assertEqual(self._get("/", {"Host": host, "Tailscale-User-Login": "owner@example.com"})[0], 403)  # 꺼져 있음
        self.state.settings = Settings(remote_web_host=host, remote_web_users="owner@example.com, Second@Example.com")
        status, body, _ = self._get("/", {"Host": host, "Tailscale-User-Login": "owner@example.com"})
        self.assertEqual(status, 200)
        self.assertIn("test-token", body)
        self.assertEqual(self._get("/", {"Host": host, "Tailscale-User-Login": "second@example.com"})[0], 200)
        for headers in ({"Host": host}, {"Host": host, "Tailscale-User-Login": "stranger@example.com"},
                        {"Host": "other.tail1234.ts.net", "Tailscale-User-Login": "owner@example.com"},
                        {"Host": "evil.example", "Tailscale-User-Login": "owner@example.com"}):
            status, body, _ = self._get("/", headers)
            self.assertEqual(status, 403, headers)
            self.assertNotIn("test-token", body)

    def test_app_install_files_are_served_with_csp(self):
        status, body, headers = self._get("/manifest.webmanifest", {})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/manifest+json")
        manifest = json.loads(body)
        self.assertEqual((manifest["name"], manifest["display"], manifest["start_url"]), ("ALEX", "standalone", "/"))
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
        status, body, headers = self._get("/sw.js", {})
        self.assertEqual(status, 200)
        self.assertIn("navigate", body)
        self.assertNotIn("caches.open", body)  # 토큰이 든 페이지·API 를 저장하지 않는다
        with urllib.request.urlopen(self.base + "/icon-192.png", timeout=2) as response:
            self.assertTrue(response.read().startswith(b"\x89PNG"))
            csp = response.headers["Content-Security-Policy"]
        self.assertIn("manifest-src 'self'", csp)
        self.assertIn("worker-src 'self'", csp)
        page = self._get("/", {})[1]
        self.assertIn('rel="manifest"', page)

    def test_remote_app_settings_are_validated(self):
        from jarvis.config import ConfigError, Settings, validate

        with self.assertRaises(ConfigError):
            validate(Settings(remote_web_host="evil.example", remote_web_users="a@b.c"))
        with self.assertRaises(ConfigError):
            validate(Settings(remote_web_host="my-pc.tail1234.ts.net", remote_web_users=" , "))
        validate(Settings(remote_web_host="my-pc.tail1234.ts.net", remote_web_users="a@b.c"))

    def test_api_rejects_missing_token(self):
        request = urllib.request.Request(self.base + "/api/state")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(ctx.exception.code, 403)

    def test_chat_accepts_json_with_token(self):
        request = urllib.request.Request(
            self.base + "/api/chat",
            data=json.dumps({"message": "안녕"}).encode(),
            headers={"Content-Type": "application/json", "X-Jarvis-Token": "test-token"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            result = json.loads(response.read())
        self.assertEqual(result["route"], "local_general")
        self.assertIn("GENERAL", result["response"])

    def test_chat_rejects_form_content_type(self):
        request = urllib.request.Request(
            self.base + "/api/chat",
            data=b"message=hi",
            headers={"Content-Type": "application/x-www-form-urlencoded", "X-Jarvis-Token": "test-token"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(ctx.exception.code, 415)


if __name__ == "__main__":
    unittest.main()
