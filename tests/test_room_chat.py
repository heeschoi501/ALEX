"""Room navigation must not retarget requests or cancel another room's stream.

Temporary DB, loopback HTTP and mock providers only; no GPU/model calls.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from jarvis.storage import OWNER
from jarvis.web import Ticket, make_server
from test_web import make_state


class RoomChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.storage = self.state.core.storage
        self.server = make_server(self.state, 0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None):
        return urllib.request.Request(self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Jarvis-Token": "test-token", "Content-Type": "application/json"})

    def call(self, path, body=None):
        try:
            with urllib.request.urlopen(self.request(path, body), timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def stream(self, sid, text):
        events, errors, queued = [], [], threading.Event()

        def receive():
            try:
                with urllib.request.urlopen(self.request("/api/chat/stream",
                        {"message": text, "session_id": sid}), timeout=8) as response:
                    for line in response:
                        event = json.loads(line)
                        events.append(event)
                        if event["type"] == "queue":
                            queued.set()
            except Exception as error:
                errors.append(str(error))
                queued.set()

        thread = threading.Thread(target=receive)
        thread.start()
        self.addCleanup(thread.join, 9)
        return thread, events, errors, queued

    def test_two_rooms_can_queue_and_navigation_does_not_retarget_either_answer(self):
        first = self.state.session_id
        second, viewing = self.storage.create_session(), self.storage.create_session()
        with self.state.lock:
            a = self.stream(first, "첫 번째 방의 질문")
            self.assertTrue(a[3].wait(3))
            b = self.stream(second, "두 번째 방의 질문")
            self.assertTrue(b[3].wait(3))
            self.assertFalse(a[2] or b[2])
            snapshot = self.call("/api/state?session_id=" + viewing)[1]
            self.assertEqual({r["session_id"] for r in snapshot["queue"]["requests"]}, {first, second})
            self.assertIsNone(snapshot["request_id"])
            self.assertEqual(self.call("/api/chat/stream", {"message": "중복", "session_id": first})[0], 409)
        for thread, events, errors, _queued in (a, b):
            thread.join(8)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertIn("done", [e["type"] for e in events])
            done = next(e for e in events if e["type"] == "done")
            latest = self.storage.message_page(done["session_id"], limit=1)["messages"][-1]["id"]
            self.assertEqual(done["last_message_id"], latest)
        self.assertEqual(self.state.session_id, viewing)
        self.assertEqual([m.content for m in self.storage.get_messages(first) if m.role == "user"], ["첫 번째 방의 질문"])
        self.assertEqual([m.content for m in self.storage.get_messages(second) if m.role == "user"], ["두 번째 방의 질문"])
        self.assertEqual(self.state.queue, [])

    def test_cancelled_waiter_preserves_the_running_rooms_callbacks(self):
        callback = lambda _chunk: None
        signal = threading.Event()
        self.state.core.response_chunk_callback = callback
        self.state.core.command_cancel_event = signal
        with self.state.lock:
            thread, events, errors, queued = self.stream(self.state.session_id, "대기 요청")
            self.assertTrue(queued.wait(3))
            request_id = next(e["request_id"] for e in events if e["type"] == "start")
            self.assertEqual(self.call("/api/cancel", {"request_id": request_id})[0], 200)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertIs(self.state.core.response_chunk_callback, callback)
            self.assertIs(self.state.core.command_cancel_event, signal)
        self.assertFalse(errors)
        self.assertTrue(events[-1]["cancelled"])

    def test_new_chat_does_not_reuse_a_blank_room_that_is_already_queued(self):
        sid = self.state.session_id
        self.state.queue.append(Ticket(OWNER, session_id=sid))
        try:
            status, result = self.call("/api/session/new", {})
            self.assertEqual(status, 200)
            self.assertNotEqual(result["session_id"], sid)
        finally:
            self.state.queue.clear()

    def test_pending_room_cannot_be_deleted_but_other_rooms_can(self):
        queued, other = self.state.session_id, self.storage.create_session()
        self.state.queue.append(Ticket(OWNER, session_id=queued))
        try:
            self.assertEqual(self.call("/api/session/delete", {"target": queued, "confirmed": True})[0], 409)
            self.assertEqual(self.call("/api/session/delete", {"target": other, "confirmed": True})[0], 200)
            self.assertTrue(self.storage.session_exists(queued))
        finally:
            self.state.queue.clear()

    def test_feedback_after_returning_to_a_room_uses_that_rooms_last_answer(self):
        first, second = self.state.session_id, self.storage.create_session()
        self.state.process("첫 방에서 한 질문", session_id=first)
        self.state.process("다른 방에서 한 질문", session_id=second)
        self.state.process("/bad 답변을 다시 확인해 주세요", session_id=first)
        feedback = self.storage.list_experiences(kind="answer")
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].source, "session:" + first)
        self.assertEqual(feedback[0].situation, "첫 방에서 한 질문")


if __name__ == "__main__":
    unittest.main()
