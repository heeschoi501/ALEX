"""GPU를 실행하지 않고 미디어→LLM 전환 차단과 반환 확인을 검사한다."""
from pathlib import Path
import socket
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from jarvis.imagegen import ComfyImageGenerator, ImageGenError
from jarvis.providers import Message, OllamaProvider, ProviderError, _loaded_names


class GPUHandoffTests(unittest.TestCase):
    def generator(self):
        generator = ComfyImageGenerator(Path("unused"))
        generator._request = Mock(return_value=b"{}")
        generator._listening = Mock(return_value=True)
        return generator

    def test_engine_off_skips_http_without_waiting(self):
        generator = self.generator()
        generator._listening.return_value = False
        generator.prepare_for_chat()
        generator._request.assert_not_called()

    def test_closed_port_is_seen_as_off_quickly(self):
        # Windows 는 꺼진 포트의 거절을 2초쯤 늦게 알린다. 그 사이 HTTP 2초 시간 초과가 나면 대화가 막혔다.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        generator = ComfyImageGenerator(Path("unused"), url=f"http://127.0.0.1:{port}")
        started = time.monotonic()
        self.assertFalse(generator._listening())
        self.assertLess(time.monotonic() - started, 1.0)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            generator = ComfyImageGenerator(Path("unused"), url=f"http://127.0.0.1:{server.getsockname()[1]}")
            self.assertTrue(generator._listening())

    def test_busy_media_does_not_get_unloaded(self):
        generator = self.generator()
        generator._json = Mock(return_value={"queue_running": ["active"], "queue_pending": []})
        with self.assertRaisesRegex(ImageGenError, "진행 중"):
            generator.prepare_for_chat()
        self.assertFalse(any(call.args[0] == "POST" for call in generator._request.call_args_list))

    def test_free_acknowledgement_waits_for_allocator_release(self):
        generator = self.generator()
        idle = {"queue_running": [], "queue_pending": []}
        generator._json = Mock(side_effect=[idle, {"devices": [{"type": "cuda", "torch_vram_total": 8 * 1024**3}]},
                                           idle, {"devices": [{"type": "cuda", "torch_vram_total": 0}]}])
        with patch("jarvis.imagegen.time.sleep"):
            generator.prepare_for_chat()
        generator._request.assert_any_call("POST", "/free", {"unload_models": True, "free_memory": True}, timeout=2)
        self.assertEqual(generator._json.call_count, 4)

    def test_memory_still_reserved_is_not_success(self):
        generator = self.generator()
        generator._json = Mock(side_effect=[{"queue_running": [], "queue_pending": []},
                                           {"devices": [{"type": "cuda", "torch_vram_total": 1024**3}]}])
        with self.assertRaisesRegex(ImageGenError, "반환하지 않아"):
            generator.prepare_for_chat(timeout=0)

    def test_bad_memory_or_queue_data_is_unverified(self):
        for stats in ({}, {"devices": []}, {"devices": [{}]},
                      {"devices": [{"type": "cuda", "torch_vram_total": -1}]}):
            with self.subTest(stats=stats):
                generator = self.generator()
                generator._json = Mock(side_effect=[{"queue_running": [], "queue_pending": []}, stats])
                with self.assertRaises(ImageGenError):
                    generator.prepare_for_chat()

    def test_connection_refused_is_off_but_timeout_is_not(self):
        generator = self.generator()
        for cause, off in ((ConnectionRefusedError(), True), (TimeoutError(), False)):
            error = ImageGenError("unreachable")
            error.__cause__ = urllib.error.URLError(cause)
            generator._request.side_effect = error
            if off:
                generator.prepare_for_chat()
            else:
                with self.assertRaises(ImageGenError):
                    generator.prepare_for_chat()

    def test_guard_blocks_both_stream_and_nonstream_before_http(self):
        provider = OllamaProvider(model="fake")
        provider.before_generate = Mock(side_effect=ProviderError("GPU not released"))
        with patch("jarvis.providers.urllib.request.urlopen") as http:
            with self.assertRaises(ProviderError):
                provider.generate([Message("user", "hello")])
            with self.assertRaises(ProviderError):
                list(provider.generate_stream([Message("user", "hello")]))
        http.assert_not_called()

    def test_bad_ollama_status_is_not_an_empty_gpu(self):
        for payload in ({}, [], {"models": None}, {"error": "down", "models": []}, {"models": [{}]}):
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                _loaded_names(payload)
        self.assertEqual(_loaded_names({"models": []}), [])
        self.assertEqual(_loaded_names({"models": [{"model": "a"}]}), ["a"])


if __name__ == "__main__":
    unittest.main()
