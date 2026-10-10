"""Provider contract and failure handling tests.

네트워크를 실제로 쓰지 않는다. `_post_json` 만 바꿔치기(monkeypatch)한다.
"""
import unittest

from jarvis.providers import (
    CloudStubProvider,
    FailingProvider,
    Message,
    MockLLMProvider,
    OllamaProvider,
    ProviderError,
)


class MockProviderTests(unittest.TestCase):
    def test_mock_generate(self):
        provider = MockLLMProvider("m", "PREFIX")
        self.assertIn("PREFIX", provider.generate([Message("user", "hi")]))
        self.assertTrue(provider.health_check())

    def test_unhealthy_mock(self):
        self.assertFalse(MockLLMProvider("m", "P", healthy=False).health_check())

    def test_cloud_stub_does_not_call_network(self):
        text = CloudStubProvider("c", "CLOUD").generate([Message("user", "hi")])
        self.assertIn("실제 전송하지 않습니다", text)

    def test_failing_provider(self):
        provider = FailingProvider()
        self.assertFalse(provider.health_check())
        with self.assertRaises(ProviderError):
            provider.generate([Message("user", "hi")])


class OllamaProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = OllamaProvider(model="test-model:q4", host="http://127.0.0.1:9/")

    def test_host_trailing_slash_removed(self):
        self.assertEqual(self.provider.host, "http://127.0.0.1:9")

    def test_name_defaults_to_model(self):
        self.assertEqual(self.provider.name, "ollama:test-model:q4")

    def test_generate_parses_content(self):
        captured = {}

        def fake_post(path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return {"message": {"role": "assistant", "content": "  안녕하세요  "}}

        self.provider._post_json = fake_post
        result = self.provider.generate([Message("user", "hi")])
        self.assertEqual(result, "안녕하세요")
        self.assertEqual(captured["path"], "/api/chat")
        self.assertEqual(captured["payload"]["model"], "test-model:q4")
        self.assertFalse(captured["payload"]["stream"])
        self.assertIn("num_ctx", captured["payload"]["options"])

    def test_generate_raises_on_missing_content(self):
        self.provider._post_json = lambda path, payload: {"message": {}}
        with self.assertRaises(ProviderError):
            self.provider.generate([Message("user", "hi")])

    def test_generate_raises_on_empty_messages(self):
        with self.assertRaises(ProviderError):
            self.provider.generate([])

    def test_health_check_false_when_server_down(self):
        """존재하지 않는 포트이므로 예외가 아니라 False 를 돌려줘야 한다."""
        self.assertFalse(self.provider.health_check())


if __name__ == "__main__":
    unittest.main()


class OllamaModelMatchTests(unittest.TestCase):
    def test_exact_tag_does_not_accept_different_size(self):
        provider = OllamaProvider("qwen2.5:7b", health_ttl=0)
        self.assertFalse(provider._requested_model_is_installed({"qwen2.5:14b"}))
        self.assertTrue(provider._requested_model_is_installed({"qwen2.5:7b"}))

    def test_untagged_name_accepts_latest_only(self):
        provider = OllamaProvider("qwen2.5", health_ttl=0)
        self.assertTrue(provider._requested_model_is_installed({"qwen2.5:latest"}))
        self.assertFalse(provider._requested_model_is_installed({"qwen2.5:14b"}))

    def test_health_check_cache_avoids_repeated_fetch(self):
        provider = OllamaProvider("qwen2.5:7b", health_ttl=60)
        calls = []

        def fake_fetch():
            calls.append(1)
            return {"qwen2.5:7b"}

        provider._fetch_installed_models = fake_fetch
        self.assertTrue(provider.health_check())
        self.assertTrue(provider.health_check())
        self.assertEqual(len(calls), 1)


class ProviderStreamingTests(unittest.TestCase):
    def test_base_stream_falls_back_to_single_full_chunk(self):
        provider = MockLLMProvider("m", "MOCK")
        chunks = list(provider.generate_stream([Message("user", "hi")]))
        self.assertEqual(len(chunks), 1)
        self.assertIn("MOCK", chunks[0])

    def test_ollama_generate_calls_chunk_callback(self):
        provider = OllamaProvider("test:q4")
        provider.generate_stream = lambda messages, **kwargs: iter(["안", "녕", "하세요"])
        seen = []
        result = provider.generate([Message("user", "hi")], on_chunk=seen.append)
        self.assertEqual(result, "안녕하세요")
        self.assertEqual(seen, ["안", "녕", "하세요"])

    def test_chunk_callback_failure_does_not_lose_response(self):
        provider = OllamaProvider("test:q4")
        provider.generate_stream = lambda messages, **kwargs: iter(["a", "b"])
        def broken(_chunk):
            raise RuntimeError("screen closed")
        self.assertEqual(provider.generate([Message("user", "hi")], on_chunk=broken), "ab")


class CloudAdapterTests(unittest.TestCase):
    def test_openai_responses_payload_and_parse(self):
        from jarvis.providers import OpenAIResponsesProvider
        p = OpenAIResponsesProvider("secret", "gpt-test", max_output_tokens=123)
        seen = {}
        def fake(url, payload, headers):
            seen.update(url=url, payload=payload, headers=headers)
            return {"output": [{"content": [{"type": "output_text", "text": "answer"}]}]}
        p._request_json = fake
        result = p.generate([Message("system", "rules"), Message("user", "hello")])
        self.assertEqual(result, "answer")
        self.assertFalse(seen["payload"]["store"])
        self.assertEqual(seen["payload"]["instructions"], "rules")
        self.assertNotIn("secret", str(seen["payload"]))
        self.assertTrue(seen["headers"]["Authorization"].startswith("Bearer "))

    def test_anthropic_messages_payload_and_parse(self):
        from jarvis.providers import AnthropicMessagesProvider
        p = AnthropicMessagesProvider("secret", "claude-test")
        seen = {}
        def fake(url, payload, headers):
            seen.update(url=url, payload=payload, headers=headers)
            return {"content": [{"type": "text", "text": "claude answer"}]}
        p._request_json = fake
        result = p.generate([
            Message("system", "rules"), Message("user", "a"), Message("user", "b")
        ])
        self.assertEqual(result, "claude answer")
        self.assertEqual(seen["payload"]["system"], "rules")
        self.assertEqual(len(seen["payload"]["messages"]), 1)
        self.assertIn("a", seen["payload"]["messages"][0]["content"])
        self.assertEqual(seen["headers"]["x-api-key"], "secret")

    def test_gemini_payload_and_parse(self):
        from jarvis.providers import GeminiGenerateContentProvider
        p = GeminiGenerateContentProvider("secret", "gemini-test")
        seen = {}
        def fake(url, payload, headers):
            seen.update(url=url, payload=payload, headers=headers)
            return {"candidates": [{"content": {"parts": [{"text": "gemini answer"}]}}]}
        p._request_json = fake
        result = p.generate([Message("system", "rules"), Message("user", "hello")])
        self.assertEqual(result, "gemini answer")
        self.assertIn("systemInstruction", seen["payload"])
        self.assertEqual(seen["headers"]["x-goog-api-key"], "secret")
        self.assertIn("gemini-test:generateContent", seen["url"])

    def test_cloud_health_check_is_configuration_only(self):
        from jarvis.providers import OpenAIResponsesProvider
        self.assertTrue(OpenAIResponsesProvider("key", "model").health_check())
        self.assertFalse(OpenAIResponsesProvider("", "model").health_check())

class OllamaThinkingTests(unittest.TestCase):
    def test_thinking_is_explicitly_disabled_by_default(self):
        provider = OllamaProvider("qwen3:8b-q4_K_M")
        payload = provider._chat_payload([Message("user", "hi")], stream=False)
        self.assertIs(payload["think"], False)

    def test_thinking_can_be_enabled(self):
        provider = OllamaProvider("qwen3:8b-q4_K_M", think=True)
        payload = provider._chat_payload([Message("user", "hard problem")], stream=False)
        self.assertIs(payload["think"], True)

class OllamaRuntimePayloadTests(unittest.TestCase):
    def test_num_predict_and_keep_alive_are_bounded_in_payload(self):
        provider = OllamaProvider("qwen3:8b-q4_K_M", num_predict=777, keep_alive="3m")
        payload = provider._chat_payload([Message("user", "hi")], stream=False)
        self.assertEqual(payload["options"]["num_predict"], 777)
        self.assertEqual(payload["keep_alive"], "3m")

class CloudHTTPErrorPrivacyTests(unittest.TestCase):
    def test_cloud_http_error_body_is_not_reflected(self):
        import io
        import urllib.error
        from unittest.mock import patch
        from jarvis.providers import OpenAIResponsesProvider, ProviderError

        secret_body = b'{"error":"request contained sk-THIS-SHOULD-NOT-BE-LOGGED"}'
        http_error = urllib.error.HTTPError(
            "https://api.openai.com/v1/responses", 400, "bad request", {}, io.BytesIO(secret_body)
        )
        provider = OpenAIResponsesProvider("api-key", "gpt-test")
        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(ProviderError) as ctx:
                provider.generate([Message("user", "hello")])
        text = str(ctx.exception)
        self.assertIn("HTTP 400", text)
        self.assertNotIn("THIS-SHOULD-NOT-BE-LOGGED", text)
        self.assertNotIn("request contained", text)
