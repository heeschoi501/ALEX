"""/unload — Ollama 에 올라간 모델을 지금 내려 그래픽카드를 비운다(2026-09-30, 게임 등). 가짜 Ollama 서버로 잰다."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from pathlib import Path

from jarvis.providers import OllamaProvider, ProviderError, unload_ollama_models
from jarvis.storage import Storage


class FakeOllama:
    def __init__(self, loaded):
        self.loaded = list(loaded)
        self.requests = []
        self.sticky = False  # True 면 keep_alive 0 을 받아도 바로 내려가지 않는다(답을 만드는 중)
        self.appear_after_first_ps = []  # 첫 /api/ps 다음에 나타나는 모델(다른 요청이 올림)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append(("GET", self.path, None))
                self._send({"models": [{"name": n, "model": n} for n in fake.loaded]})
                fake.loaded.extend(fake.appear_after_first_ps)
                fake.appear_after_first_ps = []

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(("POST", self.path, payload))
                if payload.get("keep_alive") == 0 and payload.get("model") in fake.loaded and not fake.sticky:
                    fake.loaded.remove(payload["model"])
                self._send({"done": True, "done_reason": "unload"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class UnloadTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama(["qwen3.8:27b", "gpt-oss:120b"])
        self.addCleanup(self.fake.close)

    def test_every_loaded_model_is_sent_keep_alive_zero(self):
        self.assertEqual(unload_ollama_models(self.fake.host), ["qwen3.8:27b", "gpt-oss:120b"])
        posts = [p for method, path, p in self.fake.requests if method == "POST"]
        self.assertEqual(posts, [{"model": "qwen3.8:27b", "keep_alive": 0}, {"model": "gpt-oss:120b", "keep_alive": 0}])
        self.assertEqual(self.fake.loaded, [])
        self.assertEqual(unload_ollama_models(self.fake.host), [])  # 비었으면 아무것도 보내지 않는다

    def test_a_server_that_is_not_there_gives_a_plain_error(self):
        with self.assertRaisesRegex(ProviderError, "연결할 수 없습니다"):
            unload_ollama_models("http://127.0.0.1:9", timeout=2)

    def test_the_chat_command_unloads_through_the_apps_ollama(self):
        from jarvis.app import handle_command
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ollama = OllamaProvider(model="qwen3.8:27b", host=self.fake.host)
        core = JarvisCore(storage=Storage(Path(temp.name) / "alex.db"),
                          hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.DESKTOP),
                          local_general=ollama, local_coder=ollama,
                          cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"))
        session = core.get_or_create_session(False)
        reply = handle_command(core, session, "/unload")[0]
        self.assertIn("모델을 내렸습니다: qwen3.8:27b, gpt-oss:120b", reply)
        self.assertIn("Ollama 가 쓰던", reply)
        self.assertIn("Ollama 에 올라간 모델이 없습니다", handle_command(core, session, "/unload")[0])

    def test_a_model_still_loaded_after_the_request_is_reported_not_hidden(self):
        from jarvis.app import handle_command
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider

        self.fake.loaded = ["busy-model"]
        self.fake.sticky = True
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ollama = OllamaProvider(model="busy-model", host=self.fake.host)
        core = JarvisCore(storage=Storage(Path(temp.name) / "alex.db"),
                          hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.DESKTOP),
                          local_general=ollama, local_coder=ollama,
                          cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"))
        reply = handle_command(core, core.get_or_create_session(False), "/unload")[0]
        self.assertIn("지금 올라가 있는 모델: busy-model", reply)

    def test_a_model_that_appears_after_the_first_look_is_reported(self):
        """Codex 2차 R2-06: 처음엔 없었지만 그사이 다른 요청이 올린 모델을 '없다' 고 답하지 않는다."""
        from jarvis.app import handle_command
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider

        self.fake.loaded = []
        self.fake.appear_after_first_ps = ["late-model"]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ollama = OllamaProvider(model="late-model", host=self.fake.host)
        core = JarvisCore(storage=Storage(Path(temp.name) / "alex.db"),
                          hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.DESKTOP),
                          local_general=ollama, local_coder=ollama,
                          cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"))
        reply = handle_command(core, core.get_or_create_session(False), "/unload")[0]
        self.assertIn("처음 확인할 때는 올라간 모델이 없었습니다", reply)
        self.assertIn("지금 올라가 있는 모델: late-model", reply)

    def test_the_screen_has_the_button(self):
        html = (Path(__file__).resolve().parents[1] / "jarvis" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-command="/unload"', html)


if __name__ == "__main__":
    unittest.main()
