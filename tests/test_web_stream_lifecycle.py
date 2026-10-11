"""A failed chat connection must not leave the local server permanently busy."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jarvis.providers import OllamaProvider
from jarvis.web import _handler_factory
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


if __name__ == "__main__":
    unittest.main()
