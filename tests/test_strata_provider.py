"""Strata 로컬 모델 서버(ADR-086) — 가짜 Strata 서버로 통신 형식과 안전장치를 잰다. 실제 모델·그래픽카드는 쓰지 않는다.

지키는 것:
1. OpenAI 호환 SSE 를 읽어 답 조각만 화면에 내고, 생각(reasoning_content)은 내지 않되 생각 상한은 Ollama 와 같게 센다.
2. 도구 호출은 조각으로 와도 합쳐서 OllamaProvider 의 도구 루프에 넘기고, 결과는 그 호출 id 로 돌려준다.
3. 문맥 초과·그래픽카드 사용 중·끊긴 연결·빈 답은 각각 알아볼 수 있는 오류가 된다.
4. /unload 는 JSON 으로 보낸다(Strata 가 다른 형식은 거절한다). 답을 만드는 중(409)이면 그렇다고 말한다.
5. 설정은 이 PC 주소만 받고, Strata 모드면 대화·코딩·요약·오픈코드 일꾼이 모두 Strata 를 쓴다. 기본은 그대로 Ollama.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from jarvis.app import build_providers, build_summary_provider
from jarvis.config import ConfigError, Settings, validate
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.opencode_worker import OpenCodeWorker
from jarvis.providers import (ContextExhausted, Message, OllamaProvider, ProviderError, StrataProvider, ThinkingTooLong,
                              loaded_local_models, unload_local_models)


def sse(*chunks: dict) -> list[bytes]:
    return [b": keep-alive\n\n"] + [f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode() for c in chunks] + [b"data: [DONE]\n\n"]


def delta(finish=None, usage=None, **fields) -> dict:
    chunk = {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": fields, "finish_reason": finish}]}
    if usage:
        chunk["usage"] = usage
    return chunk


class FakeStrata:
    def __init__(self):
        self.requests: list[tuple[str, str, dict | None, str]] = []
        self.replies: list = []          # 차례로 쓸 /v1/chat/completions 응답: list[bytes] 또는 (상태, dict)
        self.loaded = True
        self.unload_status = 200
        self.service = "strata"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append(("GET", self.path, None, ""))
                self._json(200, {"status": "ok", "service": fake.service, "loaded": fake.loaded, "max_context": 65536})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(raw) if raw else None
                fake.requests.append(("POST", self.path, payload, self.headers.get("Content-Type", "")))
                if self.path == "/unload":
                    if fake.unload_status == 200:
                        fake.loaded = False
                    self._json(fake.unload_status, {"status": "unloaded" if fake.unload_status == 200 else "busy"})
                    return
                reply = fake.replies.pop(0)
                if isinstance(reply, tuple):
                    self._json(*reply)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for line in reply:
                        self.wfile.write(line)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass  # 받는 쪽이 일부러 끊었다(생각 상한 등)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def chats(self) -> list[dict]:
        return [payload for method, path, payload, _ in self.requests if path == "/v1/chat/completions"]


class StrataProviderTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeStrata()
        self.addCleanup(self.fake.close)
        self.provider = StrataProvider("qwen3.8-flash-next", host=self.fake.host, timeout=10, num_ctx=65536,
                                       think=True, num_predict=4096)

    def test_streams_answer_without_thinking_and_records_metrics(self):
        self.fake.replies.append(sse(delta(role="assistant", content=""), delta(reasoning_content="생각 중"),
                                     delta(content="안녕"), delta(content="하세요"),
                                     delta("stop", {"prompt_tokens": 12, "completion_tokens": 7})))
        chunks = []
        answer = self.provider.generate([Message("user", "인사해줘")], on_chunk=chunks.append)
        self.assertEqual(answer, "안녕하세요")
        self.assertEqual(chunks, ["안녕", "하세요"])  # 생각은 화면에 내지 않는다
        payload = self.fake.chats()[0]
        self.assertEqual((payload["model"], payload["stream"], payload["max_tokens"], payload["reasoning_effort"]),
                         ("qwen3.8-flash-next", True, 4096, "medium"))
        self.assertEqual(self.provider.last_metrics["output_tokens"], 7)
        self.assertEqual(self.provider.last_metrics["prompt_tokens"], 12)
        self.assertEqual(self.provider.last_metrics["done_reason"], "stop")

    def test_thinking_off_asks_for_no_reasoning(self):
        self.provider.think = False
        self.fake.replies.append(sse(delta(content="네"), delta("stop", {"prompt_tokens": 3, "completion_tokens": 1})))
        self.assertEqual(self.provider.generate([Message("user", "q")]), "네")
        self.assertEqual(self.fake.chats()[0]["reasoning_effort"], "none")

    def test_runaway_thinking_is_cut_like_ollama(self):
        self.provider.num_predict = 2048  # 생각 상한 1,024토큰
        self.fake.replies.append(sse(*[delta(reasoning_content="x" * 400) for _ in range(20)], delta("stop")))
        with self.assertRaises(ThinkingTooLong):
            self.provider.generate([Message("user", "q")])

    def test_tool_calls_in_pieces_are_merged_and_results_go_back_with_their_id(self):
        self.fake.replies.append(sse(
            delta(tool_calls=[{"index": 0, "id": "call_a", "type": "function", "function": {"name": "web_search", "arguments": ""}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"query": '}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '"날씨"}'}}]),
            delta("tool_calls", {"prompt_tokens": 20, "completion_tokens": 9})))
        self.fake.replies.append(sse(delta(content="맑습니다."), delta("stop", {"prompt_tokens": 40, "completion_tokens": 3})))
        seen = []

        def runner(name, arguments):
            seen.append((name, arguments))
            return "서울 맑음"

        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
        answer = self.provider.generate([Message("user", "날씨?")], tools=tools, tool_runner=runner)
        self.assertEqual(answer, "맑습니다.")
        self.assertEqual(seen, [("web_search", {"query": "날씨"})])
        second = self.fake.chats()[1]["messages"]
        self.assertEqual(second[1]["tool_calls"][0]["id"], "call_a")
        self.assertEqual(json.loads(second[1]["tool_calls"][0]["function"]["arguments"]), {"query": "날씨"})
        self.assertEqual((second[2]["role"], second[2]["tool_call_id"], second[2]["content"]), ("tool", "call_a", "서울 맑음"))
        self.assertEqual(self.fake.chats()[0]["tools"], tools)

    def test_length_with_no_answer_is_context_exhaustion(self):
        self.fake.replies.append(sse(delta(reasoning_content="생각"), delta("length", {"prompt_tokens": 100, "completion_tokens": 4096})))
        with self.assertRaises(ContextExhausted):
            self.provider.generate([Message("user", "q")])

    def test_server_errors_are_named(self):
        self.fake.replies.append((400, {"error": {"message": "the prompt exceeds the context (70000 > 65536)"}}))
        with self.assertRaises(ContextExhausted):
            self.provider.generate([Message("user", "q")])
        self.fake.replies.append((503, {"error": {"message": "the GPU is in use by another program"}}))
        with self.assertRaisesRegex(ProviderError, "그래픽카드"):
            self.provider.generate([Message("user", "q")])
        self.fake.replies.append([b"data: " + json.dumps(delta(content="반")).encode() + b"\n\n"])  # 끝 표시 없이 끊김
        with self.assertRaisesRegex(ProviderError, "끊겼"):
            self.provider.generate([Message("user", "q")])

    def test_no_server_is_a_clear_error(self):
        provider = StrataProvider("m", host="http://127.0.0.1:9", timeout=2)
        with self.assertRaisesRegex(ProviderError, "Strata 서버에 연결할 수 없습니다"):
            provider.generate([Message("user", "q")])
        self.assertFalse(provider.health_check())

    def test_health_only_trusts_a_strata_server(self):
        self.assertTrue(self.provider.health_check())
        self.assertEqual(self.provider._fetch_loaded_models(), {"qwen3.8-flash-next"})
        other = FakeStrata()
        self.addCleanup(other.close)
        other.service = "llama.cpp"
        self.assertFalse(StrataProvider("m", host=other.host).health_check())

    def test_unload_sends_json_and_reports_a_busy_server(self):
        self.assertEqual(unload_local_models(self.provider), ["qwen3.8-flash-next"])
        method, path, payload, content_type = self.fake.requests[-1]
        self.assertEqual((method, path, payload, content_type), ("POST", "/unload", {}, "application/json"))
        self.assertEqual(loaded_local_models(self.provider), [])
        self.fake.loaded, self.fake.unload_status = True, 409
        with self.assertRaisesRegex(ProviderError, "답을 만드는 중"):
            unload_local_models(self.provider)

    def test_cancel_stops_the_stream(self):
        self.provider.cancel_event = threading.Event()
        self.provider.cancel_event.set()
        with self.assertRaises(ProviderError):
            self.provider.generate([Message("user", "q")])
        self.assertEqual(self.fake.chats(), [])


class StrataSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(file_search_root=Path(self.temp.name), use_ollama=True, ollama_host="http://127.0.0.1:9")

    def test_default_backend_is_still_ollama(self):
        self.assertEqual(self.settings.local_backend, "ollama")
        hardware = HardwareInfo("OS", "CPU", 96, "RTX 5080", 16, PerformanceProfile.DESKTOP)
        general, coder, _ = build_providers(self.settings, hardware)
        self.assertNotIsInstance(general, StrataProvider)

    def test_only_this_pc_and_known_values_are_accepted(self):
        for field, value in (("strata_host", "http://192.168.0.5:8080"), ("strata_host", "https://example.com"),
                             ("local_backend", "vllm"), ("strata_reasoning_effort", "max"), ("strata_num_ctx", 1000)):
            with self.subTest(field=field):
                settings = Settings(file_search_root=Path(self.temp.name), **{field: value})
                with self.assertRaises(ConfigError):
                    validate(settings)
        validate(Settings(file_search_root=Path(self.temp.name), local_backend="strata", strata_host="http://localhost:8080"))

    def test_strata_mode_uses_one_model_for_chat_code_and_summary(self):
        self.settings.local_backend = "strata"
        self.settings.strata_host = "http://127.0.0.1:9"
        self.settings.ollama_think = True
        hardware = HardwareInfo("OS", "CPU", 96, "RTX 5080", 16, PerformanceProfile.DESKTOP)
        general, coder, notes = build_providers(self.settings, hardware)
        self.assertIsInstance(general, StrataProvider)
        self.assertIs(general, coder)
        self.assertEqual((general.model, general.num_ctx, general.reasoning_effort), ("qwen3.8-flash-next", 65536, "medium"))
        self.assertTrue(any("Strata 서버" in note and "응답하지 않습니다" in note for note in notes))
        summary = build_summary_provider(general, self.settings)
        self.assertIsInstance(summary, StrataProvider)
        self.assertFalse(summary.think)
        self.assertIsInstance(general, OllamaProvider)  # 화면·중단·GPU 반환 처리가 같은 로컬 경로를 탄다


class StrataWorkerTests(unittest.TestCase):
    def test_opencode_talks_to_strata_and_checks_its_health(self):
        fake = FakeStrata()
        self.addCleanup(fake.close)
        home = Path(tempfile.mkdtemp())
        (home / "opencode.exe").write_bytes(b"")
        (home / "opencode").write_bytes(b"")
        worker = OpenCodeWorker(home, "qwen3.8-flash-next", context=65536, host=fake.host, backend="strata")
        self.assertEqual(worker.unavailable_reason(), "")
        provider = worker.config()["provider"]["ollama"]
        self.assertEqual(provider["options"]["baseURL"], f"{fake.host}/v1")
        self.assertEqual(provider["models"]["qwen3.8-flash-next"]["limit"]["context"], 65536)
        fake.service = "other"
        self.assertIn("Strata 서버", worker.unavailable_reason())


class StrataLauncherTests(unittest.TestCase):
    def test_start_command_uses_the_installed_config_and_refuses_a_missing_install(self):
        from jarvis.launcher import strata_command

        folder = Path(tempfile.mkdtemp())
        with self.assertRaisesRegex(RuntimeError, "설치되어 있지 않습니다"):
            strata_command(folder, 8080)
        python = folder / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        (folder / ".venv" / "bin").mkdir()
        (folder / ".venv" / "bin" / "python").write_bytes(b"")
        (folder / "strata-iq3_s.json").write_text("{}", encoding="utf-8")
        command = strata_command(folder, 8080)
        self.assertEqual(command[1:], [str(folder / "serve" / "server.py"), "--engine", "strata",
                                       "--config", str(folder / "strata-iq3_s.json"), "--port", "8080"])
        (folder / "strata-q2_0.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "여러 개"):
            strata_command(folder, 8080)

    def test_a_running_strata_is_reused(self):
        from jarvis.launcher import ensure_strata

        fake = FakeStrata()
        self.addCleanup(fake.close)
        reports = []
        ensure_strata(fake.host, reports.append, Path(tempfile.mkdtemp()), 0)
        self.assertEqual(reports, [])  # 이미 떠 있으면 새로 띄우지 않는다


if __name__ == "__main__":
    unittest.main()
