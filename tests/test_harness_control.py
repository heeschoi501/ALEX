"""실제 로컬 HTTP의 헤더·본문 정지, 계속 오는 조각, 취소·재시도·압축의 기한 검사. GPU는 쓰지 않는다."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest

from jarvis.harness.agent import CodingAgent
from jarvis.harness.tools import Workspace
from jarvis.providers import GenerationCancelled, GenerationTimeout, Message, OllamaProvider, ProviderError, StrataProvider


@contextmanager
def server(mode, strata=False):
    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            entered.set()
            if mode == 'headers':
                release.wait(3)
                return
            self.send_response(200)
            self.end_headers()
            try:
                while not release.wait(0.03):
                    self.wfile.write(b'data: {}\n\n' if strata else b'{}\n')
                    self.wfile.flush()
            except (OSError, ValueError):
                pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', entered
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join(2)


class RequestControlTests(unittest.TestCase):
    def test_deadline_interrupts_headers_and_continuous_body_for_both_protocols(self):
        for kind in (OllamaProvider, StrataProvider):
            for mode in ('headers', 'body'):
                with self.subTest(kind=kind.__name__, mode=mode), server(mode, kind is StrataProvider) as (host, entered):
                    provider = kind('test', host=host, timeout=5)
                    old = provider.cancel_event
                    started = time.monotonic()
                    with self.assertRaises(GenerationTimeout):
                        provider.chat_step_controlled([Message('user', 'hello')], deadline=started + 0.2)
                    self.assertTrue(entered.is_set())
                    self.assertLess(time.monotonic() - started, 1.5)
                    self.assertIs(provider.cancel_event, old)
                    self.assertEqual(provider.timeout, 5)

    def test_user_cancel_interrupts_waiting_headers_without_becoming_a_timeout(self):
        with server('headers') as (host, entered):
            provider = OllamaProvider('test', host=host, timeout=5)
            cancel = threading.Event()
            timer = threading.Timer(0.15, cancel.set)
            timer.start()
            try:
                started = time.monotonic()
                with self.assertRaises(GenerationCancelled):
                    provider.chat_step_controlled([Message('user', 'hello')], cancel_event=cancel, deadline=started + 4)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertIsNone(provider.cancel_event)
            finally:
                timer.cancel()
                timer.join()

    def test_agent_expired_deadline_does_not_start_a_summary_call(self):
        class Model:
            calls = 0
            def chat_step(self, *_):
                self.calls += 1
                return '완료', []
        with tempfile.TemporaryDirectory() as tmp:
            model = Model()
            result = CodingAgent(model, Workspace(Path(tmp)), deadline=time.monotonic() - 1).run('t')
            self.assertEqual((result.stopped, model.calls), ('timeout', 0))

    def test_agent_uses_control_during_compaction_and_retry_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Model:
                calls = 0
                def chat_step_controlled(self, *_args, **kwargs):
                    self.calls += 1
                    self.deadline = kwargs['deadline']
                    raise ProviderError('temporary')
            model = Model()
            deadline = time.monotonic() + 0.15
            agent = CodingAgent(model, Workspace(Path(tmp)), deadline=deadline)
            agent.messages = [Message('system', 's'), Message('user', 'u')] + [Message('assistant', 'a') for _ in range(10)]
            with self.assertRaises(GenerationTimeout):
                agent._compact()
            self.assertEqual((model.calls, model.deadline), (1, deadline))
