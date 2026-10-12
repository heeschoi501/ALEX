"""A failed chat connection must not leave the local server permanently busy."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from jarvis.providers import GenerationCancelled, OllamaProvider
from jarvis.web import Ticket, _handler_factory
from tests.test_web import make_state


class BrokenWriter:
    def write(self, _data):
        raise OSError("connection closed")

    def flush(self):
        raise OSError("connection closed")


class StreamLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = make_state(Path(self.temp.name))
        self.handler = object.__new__(_handler_factory(self.state))
        self.handler._security_headers = lambda _type: None
        self.handler.send_header = lambda *_args: None
        self.handler.end_headers = lambda: None

    def test_header_failure_clears_busy_state(self):
        self.handler.send_response = lambda _status: (_ for _ in ()).throw(OSError("closed"))
        self.handler.wfile = BrokenWriter()
        self.handler._stream("hello")
        self.assertIsNone(self.state.active_id)
        self.assertIsNone(self.state.active_kind)
        self.assertIsNone(self.state.core.response_chunk_callback)

    def test_separate_summary_provider_receives_cancel_signal(self):
        summarizer = OllamaProvider("unused-summary")
        self.state.core.local_summarizer = summarizer
        observed = []

        def process(_message, attachments=None, **_kwargs):
            observed.append((self.state.active_kind, summarizer.cancel_event is self.state.cancel_event,
                             self.state.core.command_cancel_event is self.state.cancel_event))
            return {"response": "done", "session_id": self.state.session_id}

        self.state.process = process
        self.handler.send_response = lambda _status: None
        self.handler.wfile = BytesIO()
        self.handler._stream("hello")
        self.assertEqual(observed, [("chat", True, True)])
        self.assertIsNone(summarizer.cancel_event)
        self.assertIsNone(self.state.core.command_cancel_event)
        self.assertIsNone(self.state.active_id)
        self.assertIsNone(self.state.active_kind)

    def active_request(self):
        """An existing request owns the shared core and model cancellation state."""
        active = Ticket("active-user")
        provider = OllamaProvider("unused-active-model")
        callback = lambda _chunk: None
        self.state.core.local_summarizer = provider
        self.state.core.response_chunk_callback = callback
        self.state.core.command_cancel_event = active.cancel
        provider.cancel_event = active.cancel
        self.state.cancel_event = active.cancel
        self.state.running = active
        self.state.active_id, self.state.active_kind = active.request_id, "chat"
        self.state.queue.append(active)
        return active, provider, callback

    def assert_active_request_unchanged(self, active, provider, callback):
        self.assertIs(self.state.core.response_chunk_callback, callback)
        self.assertIs(self.state.core.command_cancel_event, active.cancel)
        self.assertIs(provider.cancel_event, active.cancel)
        self.assertIs(self.state.cancel_event, active.cancel)
        self.assertIs(self.state.running, active)
        self.assertEqual((self.state.active_id, self.state.active_kind), (active.request_id, "chat"))
        self.assertEqual(self.state.queue, [active])

    def test_cancelled_waiter_preserves_the_active_model_cancel_signal(self):
        active, provider, callback = self.active_request()
        ticket = Ticket("waiting-user")
        ticket.cancel.set()
        self.state.queue.append(ticket)
        self.handler.send_response = lambda _status: None
        self.handler.wfile = BytesIO()
        with self.state.lock:
            self.handler._stream("cancelled before its turn", ticket=ticket)
            self.assert_active_request_unchanged(active, provider, callback)

    def test_waiter_header_failure_preserves_the_active_request(self):
        active, provider, callback = self.active_request()
        ticket = Ticket("waiting-user")
        self.state.queue.append(ticket)
        self.handler.send_response = lambda _status: (_ for _ in ()).throw(OSError("closed"))
        self.handler.wfile = BrokenWriter()
        with self.state.lock:
            self.handler._stream("cannot send headers", ticket=ticket)
            self.assert_active_request_unchanged(active, provider, callback)

    def test_disconnected_waiter_preserves_the_active_request(self):
        active, provider, callback = self.active_request()
        ticket = Ticket("waiting-user")
        self.state.queue.append(ticket)
        self.handler.send_response = lambda _status: None
        self.handler.wfile = BrokenWriter()
        with self.state.lock:
            self.handler._stream("cannot send start event", ticket=ticket)
            self.assertTrue(ticket.cancel.is_set())
            self.assert_active_request_unchanged(active, provider, callback)

    def test_waiter_uses_and_restores_state_from_when_it_acquires_the_model(self):
        active, old_provider, _callback = self.active_request()
        ticket = Ticket("waiting-user")
        self.state.queue.append(ticket)
        # An engine/settings operation may change the model while this request waits.
        new_provider = OllamaProvider("unused-replacement-model")
        idle_cancel = threading.Event()
        idle_callback = lambda _chunk: None
        entered_wait = threading.Event()
        observed = []
        original_wait = self.handler._wait_turn

        def wait(*args):
            entered_wait.set()
            return original_wait(*args)

        def process(_message, attachments=None, **_kwargs):
            observed.append((new_provider.cancel_event is ticket.cancel,
                             self.state.core.command_cancel_event is ticket.cancel))
            return {"response": "done", "session_id": self.state.session_id}

        self.handler._wait_turn = wait
        self.state.process = process
        self.state.auto_title = lambda *_args: ""
        self.handler.send_response = lambda _status: None
        self.handler.wfile = BytesIO()
        worker = threading.Thread(target=self.handler._stream, args=("after the active request",),
                                  kwargs={"ticket": ticket}, daemon=True)
        with self.state.lock:
            worker.start()
            ready = entered_wait.wait(3)
            # Simulate the active request's cleanup, then an engine change under the same lock.
            self.state.core.response_chunk_callback = idle_callback
            self.state.core.command_cancel_event = idle_cancel
            self.state.core.local_summarizer = new_provider
            new_provider.cancel_event = idle_cancel
            self.state.running = None
            self.state.active_id = self.state.active_kind = None
            with self.state.queue_cv:
                self.state.queue.remove(active)
                self.state.queue_cv.notify_all()
        worker.join(5)
        self.assertTrue(ready, "request never reached the queue")
        self.assertFalse(worker.is_alive(), "queued request did not finish")
        self.assertEqual(observed, [(True, True)])
        self.assertIs(self.state.core.response_chunk_callback, idle_callback)
        self.assertIs(self.state.core.command_cancel_event, idle_cancel)
        self.assertIs(new_provider.cancel_event, idle_cancel)
        self.assertIs(old_provider.cancel_event, active.cancel)  # no longer part of this request
        self.assertEqual(self.state.queue, [])
        self.assertIsNone(self.state.running)
        self.assertIsNone(self.state.active_id)

    def test_acquired_request_restores_existing_cancel_state_after_errors(self):
        for error in (RuntimeError("process failed"), GenerationCancelled("cancelled")):
            with self.subTest(error=type(error).__name__):
                provider = OllamaProvider("unused-summary")
                prior_cancel = threading.Event()
                prior_callback = lambda _chunk: None
                provider.cancel_event = prior_cancel
                self.state.core.local_summarizer = provider
                self.state.core.response_chunk_callback = prior_callback
                self.state.core.command_cancel_event = prior_cancel
                self.state.lock = threading.Lock()

                def process(*_args, **_kwargs):
                    self.assertIs(provider.cancel_event, self.state.cancel_event)
                    raise error

                self.state.process = process
                self.handler.send_response = lambda _status: None
                self.handler.wfile = BytesIO()
                self.handler._stream("failing request")
                self.assertIs(provider.cancel_event, prior_cancel)
                self.assertIs(self.state.core.command_cancel_event, prior_cancel)
                self.assertIs(self.state.core.response_chunk_callback, prior_callback)
                self.assertFalse(self.state.lock.locked())
                self.assertIsNone(self.state.running)
                self.assertIsNone(self.state.active_id)
                self.assertEqual(self.state.queue, [])


if __name__ == "__main__":
    unittest.main()
