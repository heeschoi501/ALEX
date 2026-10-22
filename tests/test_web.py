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


CATALOG = {"opencode": {"models": {
    "big-pickle": {"name": "Big Pickle", "cost": {"input": 0, "output": 0}, "tool_call": True, "limit": {"context": 200000}},
    "old-free": {"name": "Old", "cost": {"input": 0}, "tool_call": True, "status": "deprecated"},
    "paid": {"name": "Paid", "cost": {"input": 1.5}, "tool_call": True}}}}


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        patcher = mock.patch("jarvis.freemodels._download", return_value=CATALOG)  # 시험은 models.dev 에 나가지 않는다
        patcher.start()
        self.addCleanup(patcher.stop)
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


    def test_code_session_api(self):
        """코딩 세션 API(ADR-112 — OpenCode 서버와 같은 일): 만들기 → 요청 → 사건 흐름 → 바뀐 내용·할 일·대화 → 되돌리기 → 지우기."""
        class Fake:
            think, model = False, "fake"

            def __init__(self):
                self.steps = [("", [{"function": {"name": "write", "arguments": {"filePath": "n.py", "content": "n = 1\n"}}}]),
                              ("", [{"function": {"name": "todowrite", "arguments": {"todos": [{"content": "n.py", "status": "completed"}]}}}]),
                              ("만들었습니다", [])]

            def chat_step(self, messages, tools=None, on_chunk=None):
                content, calls = self.steps.pop(0)
                if on_chunk and content:
                    on_chunk(content)
                return content, calls

        root = Path(self.temp.name).resolve()
        (root / "proj").mkdir()
        self.state.core._approved_file_root = root
        fake = Fake()
        self.state.code.provider_getter = lambda: fake
        headers = {"X-Jarvis-Token": "test-token", "Content-Type": "application/json"}

        def call(method, path, body=None):
            data = json.dumps(body).encode("utf-8") if body is not None else (b"" if method == "POST" else None)
            request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

        self.assertEqual(call("POST", "/api/code/session", {"directory": "../outside"})[0], 400)
        status, created = call("POST", "/api/code/session", {"directory": "proj", "tier": "auto"})
        self.assertEqual(status, 200)
        sid = created["session"]["id"]
        request = urllib.request.Request(self.base + "/api/code/session", headers={"Content-Type": "application/json"},
                                         data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(ctx.exception.code, 403)  # 토큰 없으면 거절
        status, _ = call("POST", f"/api/code/session/{sid}/prompt_async", {"parts": [{"type": "text", "text": "n.py 만들어"}]})
        self.assertEqual(status, 200)
        events = []
        stream = urllib.request.Request(self.base + f"/api/code/event?session={sid}&since=0", headers=headers)
        with urllib.request.urlopen(stream, timeout=10) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
            for raw in response:
                line = raw.decode("utf-8").strip()
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                    if events[-1]["type"] == "session.idle":
                        break
        types = [e["type"] for e in events]
        self.assertEqual((types[0], types[1], types[-1]), ("session.created", "message.user", "session.idle"))
        self.assertIn("message.delta", types)
        self.assertEqual(events[-1]["result"]["text"], "만들었습니다")
        self.assertIn("+n = 1", call("GET", f"/api/code/session/{sid}/diff")[1]["diff"])
        self.assertEqual(call("GET", f"/api/code/session/{sid}/todo")[1]["todos"][0]["content"], "n.py")
        self.assertEqual(call("GET", f"/api/code/session/{sid}/message")[1]["messages"][0]["content"], "n.py 만들어")
        self.assertEqual([s["id"] for s in call("GET", "/api/code/session")[1]["sessions"]], [sid])
        self.assertEqual(call("POST", f"/api/code/session/{sid}/revert", {"step": 0})[1]["files"], ["n.py"])
        self.assertFalse((root / "proj" / "n.py").exists())
        self.assertEqual(call("POST", f"/api/code/session/{sid}/permissions/per_x", {"response": "once"})[0], 404)
        self.assertEqual(call("POST", f"/api/code/session/{sid}/delete")[0], 200)
        self.assertEqual(call("GET", f"/api/code/session/{sid}")[0], 404)
        status, page, _ = self._get("/code", {})
        self.assertEqual(status, 200)
        self.assertIn("test-token", page)
        self.assertIn('src="/code.js"', page)
        self.assertEqual(self._get("/code.js", {})[0], 200)
        self.assertEqual(call("GET", "/api/code/path")[1]["root"], str(root))
        self.assertEqual([m["id"] for m in call("GET", "/api/code/path")[1]["models"]], ["opencode/big-pickle"])
        self.assertEqual(call("POST", "/api/code/session", {"directory": "proj", "model": "openai/gpt"})[0], 400)

    def test_coding_settings_and_idle_auto_learn(self):
        """설정의 '코딩 에이전트 · API' 칸과 쉬는 중 자동 학습(2026-10-23)."""
        from unittest import mock
        from jarvis.config import Settings
        from jarvis.developer import record_outcome
        headers = {"X-Jarvis-Token": "test-token", "Content-Type": "application/json"}

        def call(method, path, body=None):
            data = json.dumps(body).encode("utf-8") if body is not None else None
            request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

        status, info = call("GET", "/api/coding")
        self.assertEqual((status, info["insights"], info["auto_learn"], info["pending"]), (200, False, False, 0))
        self.assertEqual(call("POST", "/api/coding", {"coding_insights_enabled": True})[0], 400)  # 설정 없는 실행 모드
        config = Path(self.temp.name) / "local.json"
        self.state.settings = Settings(data_dir=Path(self.temp.name))  # 실제 data 폴더에 쓰지 않게
        with mock.patch("jarvis.web.local_config_path", return_value=config):
            self.assertEqual(call("POST", "/api/coding", {"coding_insights_enabled": "yes"})[0], 400)
            status, info = call("POST", "/api/coding", {"coding_insights_enabled": True, "coding_auto_learn_enabled": True})
        self.assertEqual((status, info["insights"], info["auto_learn"]), (200, True, True))
        self.assertEqual([m["id"] for m in info["free_models"]], ["opencode/big-pickle"])
        with mock.patch("jarvis.web.local_config_path", return_value=config):
            self.assertEqual(call("POST", "/api/coding", {"coding_free_model": "not-a-model"})[0], 400)
            status, info = call("POST", "/api/coding", {"coding_free_model": "opencode/big-pickle"})
        self.assertEqual((status, info["free_model"]), (200, "opencode/big-pickle"))
        worker = self.state.core.opencode_worker
        self.assertTrue(worker.free and worker.model == "opencode/big-pickle")  # 자동 수정·만들기 일꾼이 바로 바뀐다
        with mock.patch("jarvis.web.local_config_path", return_value=config):
            call("POST", "/api/coding", {"coding_free_model": ""})
        self.assertFalse(getattr(self.state.core.opencode_worker, "free", False))
        self.assertTrue(json.loads(config.read_text(encoding="utf-8"))["coding_auto_learn_enabled"])
        self.assertTrue((Path(self.temp.name) / "free-models.json").exists())  # 목록 저장도 시험 폴더에
        self.assertTrue(self.state.core.coding_insights and self.state.core.coding_auto_learn)  # 바로 적용
        self.assertEqual(self.state.maybe_auto_learn(), "")  # 배울 기록이 아직 없다
        for n in range(3):
            record_outcome(self.state.core.storage, {"type": "code", "run_id": f"ses_{n}@1", "goal": "g", "result": "verified",
                                                     "rounds": [{"round": 1, "exit": 0, "diff": "+x"}]})
        self.assertEqual(call("GET", "/api/coding")[1]["pending"], 3)
        self.state.core.coding_auto_learn = False
        self.assertEqual(self.state.maybe_auto_learn(), "")  # 꺼져 있으면 안 함
        self.state.core.coding_auto_learn = True
        self.assertIn("교훈 연산", self.state.maybe_auto_learn())  # 가짜 모델 답엔 연산이 없다 — 그래도 차례를 얻어 /dev-learn 을 돌렸다
        self.assertEqual(self.state.queue, [])

    def test_restart_spawns_the_launcher_and_waits_for_busy_coding_sessions(self):
        """'다시 켜기'(2026-10-23) — 코딩 세션이 일하는 중이면 거절, 아니면 실행기를 띄우고 이 웹은 꺼진다(실행기는 가짜)."""
        from types import SimpleNamespace
        from unittest import mock
        headers = {"X-Jarvis-Token": "test-token", "Content-Type": "application/json"}

        def post(path):
            request = urllib.request.Request(self.base + path, data=b"{}", headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

        self.state.code.sessions["ses_busy"] = SimpleNamespace(status="running")
        with mock.patch.object(self.state, "spawn_restarter") as spawn:
            status, body = post("/api/restart")
            self.assertEqual(status, 409)
            self.assertIn("코딩 세션", body["error"])
            spawn.assert_not_called()
            del self.state.code.sessions["ses_busy"]
            status, body = post("/api/restart")
        self.assertEqual((status, body["stopped"], body["restarting"]), (200, True, True))
        spawn.assert_called_once()
        self.assertTrue(self.state.shutting_down)

if __name__ == "__main__":
    unittest.main()
