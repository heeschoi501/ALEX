"""Regression coverage for real local usage boundaries and browser workflows."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from jarvis.app import build_cloud_providers
from jarvis.config import ConfigError, Settings, load_settings, validate
from jarvis.providers import GenerationCancelled, Message, OllamaProvider, ProviderError
from jarvis.router import Destination
from jarvis.storage import Storage
from jarvis.tools import ToolError
from jarvis.web import make_server
from jarvis.workspace import SafeWorkspaceEditor
from test_web import make_state


class LocalOnlyTests(unittest.TestCase):
    def test_default_never_builds_paid_adapters_even_with_cloud_config(self):
        settings = Settings(cloud_provider="openai", cloud_coder_model="x", cloud_reasoning_model="y")
        with patch.dict(os.environ, {}, clear=True):
            coder, reasoning, notes = build_cloud_providers(settings)
        self.assertFalse(coder.health_check())
        self.assertFalse(reasoning.health_check())
        self.assertIn("LOCAL ONLY", str(notes))

    def test_even_forced_cloud_is_routed_locally_without_approval(self):
        with tempfile.TemporaryDirectory() as td:
            state = make_state(Path(td))
            state.core.local_only = True
            cloud = state.core.providers[Destination.CLOUD_REASONING]
            with patch.object(cloud, "generate", side_effect=AssertionError("paid request")), patch.object(state.core, "approval_callback", side_effect=AssertionError("cloud approval")):
                result, decision = state.core.handle_forced(state.session_id, "hello", Destination.CLOUD_REASONING)
            self.assertIn("GENERAL", result)
            self.assertEqual(decision.destination, Destination.LOCAL_GENERAL)

    def test_ollama_cloud_tag_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(general_model="large:cloud"))

    def test_local_config_has_env_precedence_and_strict_types(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"general_model": "mine:8b", "local_max_output_tokens": 2048}))
            with patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(path), "JARVIS_GENERAL_MODEL": "env:8b"}, clear=True):
                settings = load_settings()
                self.assertEqual(settings.general_model, "env:8b")
                self.assertEqual(settings.local_max_output_tokens, 2048)
                path.write_text('{"local_only": false}')
                with self.assertRaises(ConfigError):
                    load_settings()
                path.write_text('{"local_max_output_tokens": true}')
                with self.assertRaises(ConfigError):
                    load_settings()


class StreamProtocolTests(unittest.TestCase):
    def generate(self, records, callback=None):
        stream = io.BytesIO(b"\n".join(json.dumps(item, ensure_ascii=False).encode() for item in records))
        provider = OllamaProvider("test:local")
        with patch("urllib.request.urlopen", return_value=stream):
            value = provider.generate([Message("user", "hello")], on_chunk=callback or (lambda _: None))
        return value, provider

    def test_unicode_chunks_and_usage(self):
        seen = []
        value, provider = self.generate([
            {"message": {"content": "안녕"}, "done": False},
            {"message": {"content": "하세요"}, "done": True, "eval_count": 10, "eval_duration": 500000000},
        ], seen.append)
        self.assertEqual(value, "안녕하세요")
        self.assertEqual(seen, ["안녕", "하세요"])
        self.assertEqual(provider.last_metrics["tokens_per_second"], 20)

    def test_incomplete_stream_is_not_success(self):
        with self.assertRaisesRegex(ProviderError, "끊겼"):
            self.generate([{"message": {"content": "partial"}, "done": False}])

    def test_invalid_shapes_are_provider_errors(self):
        for record in ([], {"message": []}, {"message": {"content": ["bad"]}}):
            with self.subTest(record=record), self.assertRaises(ProviderError):
                self.generate([record])

    def test_cancel_preserves_partial_and_does_not_continue(self):
        provider = OllamaProvider("test:local")
        provider.cancel_event = threading.Event()
        raw = b'{"message":{"content":"first"}}\n{"message":{"content":"second"},"done":true}\n'
        with patch("urllib.request.urlopen", return_value=io.BytesIO(raw)):
            with self.assertRaises(GenerationCancelled) as caught:
                provider.generate([Message("user", "hi")], on_chunk=lambda _: provider.cancel_event.set())
        self.assertEqual(caught.exception.partial, "first")


class BrowserWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None, token="test-token"):
        req = urllib.request.Request(self.url + path, headers={"X-Jarvis-Token": token, "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        return urllib.request.urlopen(req, timeout=3)

    def test_stream_and_refresh_restore_same_answer(self):
        with self.request("/api/chat/stream", {"message": "안녕", "session_id": self.state.session_id}) as r:
            events = [json.loads(line) for line in r]
        self.assertEqual([e["type"] for e in events], ["start", "running", "done"])  # running: 차례가 와서 답을 만들기 시작
        self.assertIn("GENERAL", events[-1]["response"])
        with self.request("/api/state") as r:
            saved = json.load(r)
        self.assertEqual(len(saved["messages"]), 2)
        self.assertEqual(saved["messages"][-1]["content"], events[-1]["response"])
        self.assertIsNone(self.state.active_id)
        self.assertEqual(saved["sessions"][0]["title"], "안녕")

    def test_static_assets_and_csp_agree(self):
        bodies = {}
        for path in ("/", "/style.css", "/app.js"):
            with self.request(path) as r:
                self.assertEqual(r.status, 200)
                self.assertIn("style-src 'self'", r.headers["Content-Security-Policy"])
                bodies[path] = r.read().decode("utf-8")
                self.assertGreater(len(bodies[path]), 100)
        # style-src 'self' 는 HTML 의 style="" 을 막는다 — 카탈로그 창의 배치가 조용히 빠져 있었다(2026-09-26).
        self.assertNotIn('style="', bodies["/"])
        # 대화를 열면 맨 아래에서 시작해야 한다. 부드러운 스크롤이면 긴 대화를 위에서부터 내려오며 보여 준다(2026-09-26 사용자).
        self.assertNotIn("scroll-behavior:smooth", bodies["/style.css"].replace(" ", ""))

    def test_mutations_reject_missing_token(self):
        for path in ("/api/settings", "/api/workspace", "/api/shutdown", "/api/cancel", "/api/chat/stream",
                     "/api/session/delete"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(path, {"message": "hi"}, token="")
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()

    def test_busy_request_rejected_without_queuing(self):
        with self.state.lock:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/chat", {"message": "duplicate"})
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        self.assertEqual(self.state.core.storage.message_count(self.state.session_id), 0)

    # 대화 목록의 [×] (2026-09-26 사용자: "대화목록 제거 기능도 필요할거 같은데").
    def test_deleting_another_conversation_keeps_the_open_one(self):
        storage = self.state.core.storage
        current, other = self.state.session_id, storage.create_session()
        storage.add_message(other, "user", "지울 대화")
        storage.add_message(current, "user", "남길 대화")
        with self.request("/api/session/delete", {"session_id": current, "target": other, "confirmed": True}) as r:
            self.assertEqual(json.load(r), {"deleted": other, "session_id": current})
        self.assertFalse(storage.session_exists(other))
        self.assertEqual(storage.message_count(current), 1)
        with self.request("/api/state") as r:
            self.assertNotIn(other, [s["session_id"] for s in json.load(r)["sessions"]])

    def test_deleting_the_open_conversation_opens_a_new_one(self):
        core = self.state.core
        old = self.state.session_id
        core.storage.add_message(old, "user", "지울 대화")
        core.last_exchange = {"question": "지울 대화", "answer": "답", "session_id": old}
        with self.request("/api/session/delete", {"session_id": old, "target": old, "confirmed": True}) as r:
            new = json.load(r)["session_id"]
        self.assertNotEqual(new, old)
        self.assertFalse(core.storage.session_exists(old))
        self.assertIsNone(core.last_exchange)  # 지운 대화의 질문·답이 교정 학습에 쓰이지 않는다
        with self.request("/api/state") as r:
            self.assertEqual(json.load(r)["session_id"], new)

    def test_conversation_delete_needs_confirmation_an_existing_target_and_no_answer_running(self):
        target = self.state.session_id
        for body in ({"target": target}, {"target": target, "confirmed": "yes"}, {"target": "nope", "confirmed": True}):
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/session/delete", body)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        with self.state.lock:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/session/delete", {"target": target, "confirmed": True})
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        self.assertTrue(self.state.core.storage.session_exists(target))

    def test_shutdown_waits_for_the_answer_and_then_refuses_new_work(self):
        # 답변 중에는 종료도 409 — logs/probe/restart_alex.py 가 이 거절을 믿고 사용자의 답을 끊지 않는다.
        with self.state.lock:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/shutdown", {})
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        self.assertFalse(self.state.shutting_down)
        # 종료를 받은 뒤 서버가 멈추기 전의 틈에 온 요청은 새 대화를 시작하지 않는다.
        with patch.object(self.server, "shutdown"):  # 실제로 멈추면 그 틈을 재현할 수 없다
            with self.request("/api/shutdown", {}) as r:
                self.assertTrue(json.load(r)["stopped"])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/chat", {"message": "늦게 온 요청"})
            self.assertEqual(caught.exception.code, 409)
            caught.exception.close()
            with self.request("/api/state") as r:
                self.assertTrue(json.load(r)["shutting_down"])
        self.assertEqual(self.state.core.storage.message_count(self.state.session_id), 0)

    def test_stale_browser_session_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/chat/stream", {"message": "hello", "session_id": "stale"})
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()

    def test_cancel_scoped_to_current_request(self):
        self.state.active_id = "request-one"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/cancel", {"request_id": "request-two"})
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        self.assertFalse(self.state.cancel_event.is_set())
        with self.request("/api/cancel", {"request_id": "request-one"}) as r:
            self.assertTrue(json.load(r)["cancelled"])
        self.assertTrue(self.state.cancel_event.is_set())

    def test_workspace_requires_explicit_confirmation(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/workspace", {"path": str(self.root)})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        with self.request("/api/workspace", {"path": str(self.root), "confirmed": True}) as r:
            self.assertEqual(Path(json.load(r)["project_root"]), self.root.resolve())   # 화면에는 풀어 쓴 경로
        self.assertEqual(self.state.core.storage.get_state("web.workspace_root"), str(self.root.resolve()))

    def test_settings_atomic_save_and_reload(self):
        self.state.settings = Settings(file_search_root=self.root)
        path = self.root / "settings.json"
        with patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(path)}):
            with self.request("/api/settings", {"settings": {"local_max_output_tokens": 2048}}) as r:
                self.assertTrue(json.load(r)["saved"])
            self.assertEqual(load_settings().local_max_output_tokens, 2048)
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/settings", {"settings": {"local_only": False}})
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()

    def test_message_pagination_has_no_overlap(self):
        storage = self.state.core.storage
        for i in range(105):
            storage.add_message(self.state.session_id, "user", str(i))
        page = storage.message_page(self.state.session_id)
        self.assertTrue(page["has_more"])
        before = page["messages"][0]["id"]
        with self.request(f"/api/history?before={before}") as r:
            previous = json.load(r)
        self.assertFalse(previous["has_more"])
        self.assertEqual(len(previous["messages"]), 5)
        self.assertLess(previous["messages"][-1]["id"], before)


class EditSafetyRegressionTests(unittest.TestCase):
    def test_change_during_generation_is_rejected_before_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "demo.py"
            target.write_text("x = 1\n")
            editor = SafeWorkspaceEditor(root, root / "backups")
            snapshot = editor.read("demo.py")
            target.write_text("x = 3\n")
            with self.assertRaises(ToolError):
                editor.create_proposal("demo.py", "x = 2\n", expected_original=snapshot)
            self.assertEqual(target.read_text(), "x = 3\n")

    def test_backups_isolated_between_projects_with_same_filename(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            editors = []
            for name in ("first", "second"):
                folder = root / name
                folder.mkdir()
                (folder / "same.py").write_text(f"value = '{name}'\n")
                editors.append(SafeWorkspaceEditor(folder, root / "backups"))
            p = editors[0].create_proposal("same.py", "value = 'changed'\n")
            editors[0].apply(p.proposal_id)
            with self.assertRaises(ToolError):
                editors[1].restore_latest("same.py")

    def test_windows_newlines_and_byte_exact_undo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = b'def add(a, b):\r\n    return a - b\r\n'
            target = root / "demo.py"
            target.write_bytes(original)
            editor = SafeWorkspaceEditor(root, root / "backups")
            p = editor.create_proposal("demo.py", 'def add(a, b):\n    return a + b\n')
            editor.apply(p.proposal_id)
            self.assertEqual(target.read_bytes(), original.replace(b'a - b', b'a + b'))
            editor.restore_latest("demo.py")
            self.assertEqual(target.read_bytes(), original)

    def test_literal_backup_filename_with_glob_characters(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "file[1].py"
            target.write_text("x = 1\n")
            editor = SafeWorkspaceEditor(root, root / "backups")
            proposal = editor.create_proposal(target.name, "x = 2\n")
            editor.apply(proposal.proposal_id)
            editor.restore_latest(target.name)
            self.assertEqual(target.read_text(), "x = 1\n")
