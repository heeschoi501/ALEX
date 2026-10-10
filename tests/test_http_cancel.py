"""중단 버튼이 멈춘 응답을 실제로 끊는가 (코덱스 system-audit 작업, 2026-09-25 밤 — 테스트는 claude 가 더함).

Ollama 가 응답 본문을 보내다 멈추면 urllib 은 읽기에서 기다리기만 한다. 사용자가 [중단] 을 눌러도 그 읽기가
풀리지 않으면 화면은 계속 '생성 중' 이다. `interrupt_on_cancel` 은 그 응답의 소켓만 끊고, provider 는 끊긴
연결을 오류가 아니라 '중단됨'(GenerationCancelled)으로 바꾼다.
"""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.http_control import interrupt_on_cancel  # noqa: E402
from jarvis.providers import GenerationCancelled, Message, OllamaProvider, ProviderError  # noqa: E402


class InterruptTests(unittest.TestCase):
    def test_a_stalled_read_is_released_when_cancel_is_pressed(self):
        """urllib 처럼 makefile 로 감싼 소켓. Windows 에서 shutdown 만으로는 5초 그대로 기다렸다(2026-09-25 실측)."""
        ours, theirs = socket.socketpair()
        ours.settimeout(5)  # 끊지 못하면 무한 대기 대신 5초 뒤 실패로 끝나게
        body = ours.makefile("rb")
        self.addCleanup(theirs.close)
        response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=ours)))
        cancel = threading.Event()
        threading.Timer(0.2, cancel.set).start()
        started = time.monotonic()
        with interrupt_on_cancel(response, cancel):
            try:
                body.read(1)  # 상대가 아무것도 보내지 않으므로 끊지 않으면 계속 기다린다
            except OSError:
                pass
        self.assertLess(time.monotonic() - started, 3.0)
        for thing in (body, ours):
            try:
                thing.close()
            except OSError:
                pass

    def test_without_a_socket_or_cancel_event_it_does_nothing(self):
        with interrupt_on_cancel(SimpleNamespace(), threading.Event()) as response:
            self.assertIsInstance(response, SimpleNamespace)
        with interrupt_on_cancel("body", None) as response:
            self.assertEqual(response, "body")


class FakeStream:
    """읽는 도중 사용자가 중단을 누르고, 그 결과 연결이 끊기거나 조용히 닫히는 응답."""

    def __init__(self, cancel, error):
        self.cancel, self.error = cancel, error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        self.cancel.set()
        if self.error:
            raise ConnectionAbortedError("socket shut down")
        return iter([])


class ProviderCancelTests(unittest.TestCase):
    def stream(self, cancelled, error):
        provider = OllamaProvider("qwen3.8:27b")
        provider.cancel_event = threading.Event()
        fake = FakeStream(provider.cancel_event if cancelled else threading.Event(), error)
        with patch("urllib.request.urlopen", return_value=fake):
            return list(provider.generate_stream([Message("user", "hi")]))

    def test_a_cut_connection_after_cancel_is_a_cancellation(self):
        with self.assertRaises(GenerationCancelled):
            self.stream(cancelled=True, error=True)

    def test_a_quietly_closed_stream_after_cancel_is_a_cancellation(self):
        with self.assertRaises(GenerationCancelled):
            self.stream(cancelled=True, error=False)

    def test_without_cancel_a_cut_connection_is_still_an_error(self):
        with self.assertRaises(ProviderError) as ctx:
            self.stream(cancelled=False, error=True)
        self.assertNotIsInstance(ctx.exception, GenerationCancelled)


if __name__ == "__main__":
    unittest.main()
