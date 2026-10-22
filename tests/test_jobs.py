"""뒤 작업(ADR-101, 자비스 구조 3번 '뒤에서 일하고 보고' — 2026-10-22 "미뤄둔 것과 보강할 것 지금 진행해").

브라우저 연결과 상관없이 끝까지 돌고, 답은 그 대화에 남고, 화면은 jobs 로 끝난 것을 알린다.
모델은 한 번에 한 요청 — 기다리는 대화 요청이 기다리는 뒤 작업보다 먼저 들어간다."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.storage import OWNER
from jarvis.web import Ticket, make_server
from tests.test_web import make_state

FINISHED = {"done", "failed", "cancelled", "interrupted"}


def wait_for(state, job_id: str, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = state.jobs[job_id]
        if job["status"] in FINISHED and not any(t.request_id == job_id for t in state.queue):
            for thread in threading.enumerate():  # 기록 저장까지 끝나게(임시 DB 를 지우기 전에)
                if thread.name == f"alex-job-{job_id[:6]}":
                    thread.join(5)
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {state.jobs[job_id]}")


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.viewer = self.state.owner_viewer()
        self.sid = self.state.session_id

    def tearDown(self):
        self.state.shutting_down = True
        self.temp.cleanup()

    def test_job_runs_to_the_end_and_the_answer_stays_in_the_room(self):
        job = self.state.start_job(self.viewer, self.sid, "뒤에서 정리해 줘", None)
        self.assertEqual(job["status"], "queued")
        done = wait_for(self.state, job["id"])
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["started"] and done["finished"])
        self.assertTrue(done["result"])
        messages = self.state.core.storage.message_page(self.sid)["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "뒤에서 정리해 줘")
        self.assertIsNone(self.state.running)
        self.assertFalse(self.state.lock._is_owned())  # 잠금을 돌려준다
        listed = self.state.snapshot(self.viewer)["jobs"]
        self.assertEqual((listed[0]["id"], listed[0]["status"]), (job["id"], "done"))

    def test_progress_is_kept_while_running(self):
        def process(message, attachments=None, **kwargs):
            self.state.core.response_chunk_callback("[1/3 단계]\n")
            self.state.core.response_chunk_callback("[2/3 단계]\n")
            return {"response": "끝", "session_id": self.sid}
        self.state.process = process
        job = wait_for(self.state, self.state.start_job(self.viewer, self.sid, "/build 계산기", None)["id"])
        self.assertEqual(job["progress"], "[1/3 단계]\n[2/3 단계]\n")
        self.assertIsNone(self.state.core.response_chunk_callback)  # 콜백도 되돌린다

    def test_waiting_chat_goes_before_a_waiting_job(self):
        q = self.state.queue
        job, chat = Ticket(OWNER, session_id="a", background=True), Ticket(OWNER, session_id="b")
        q.extend([job, chat])
        self.assertTrue(self.state.may_run(chat))
        self.assertFalse(self.state.may_run(job))
        self.assertEqual(self.state.position(job), 1)
        q.remove(chat)
        self.assertTrue(self.state.may_run(job))
        second = Ticket(OWNER, session_id="c", background=True)
        q.append(second)
        self.assertFalse(self.state.may_run(second))  # 뒤 작업끼리는 들어온 순서
        later_chat = Ticket(OWNER, session_id="d")
        q.append(later_chat)
        self.assertTrue(self.state.may_run(later_chat))  # 늦게 온 대화도 기다리는 뒤 작업들보다 먼저
        q.clear()

    def test_a_job_waits_for_the_model_and_can_be_cancelled_while_waiting(self):
        with self.state.lock:  # 다른 요청이 모델을 쓰는 중인 것처럼 — 뒤 작업 스레드는 못 들어간다
            job = self.state.start_job(self.viewer, self.sid, "기다리는 일", None)
            time.sleep(0.2)
            self.assertEqual(self.state.jobs[job["id"]]["status"], "queued")
            next(t for t in self.state.queue if t.request_id == job["id"]).cancel.set()
            with self.state.queue_cv:
                self.state.queue_cv.notify_all()
            done = wait_for(self.state, job["id"])
        self.assertEqual(done["status"], "cancelled")
        self.assertEqual(self.state.core.storage.message_page(self.sid)["messages"], [])

    def test_failure_is_reported_in_the_room(self):
        def process(*_args, **_kwargs):
            raise RuntimeError("모델 서버가 응답하지 않습니다")
        self.state.process = process
        done = wait_for(self.state, self.state.start_job(self.viewer, self.sid, "/build 게임", None)["id"])
        self.assertEqual(done["status"], "failed")
        self.assertIn("모델 서버가 응답하지 않습니다", done["result"])
        last = self.state.core.storage.message_page(self.sid)["messages"][-1]
        self.assertIn("뒤 작업이 실패했습니다: /build 게임", last["content"])

    def test_after_a_restart_unfinished_jobs_show_as_interrupted(self):
        saved = [{"id": "a1", "owner": OWNER, "session_id": self.sid, "status": "running", "created": "2026-10-22T01:00:00"},
                 {"id": "b2", "owner": OWNER, "session_id": self.sid, "status": "done", "created": "2026-10-22T00:00:00"}]
        self.state.core.storage.set_state(f"jobs:{OWNER}", json.dumps(saved))
        listed = self.state.job_list(self.viewer)
        self.assertEqual([(j["id"], j["status"]) for j in listed], [("a1", "interrupted"), ("b2", "done")])

    def test_records_are_kept_per_person_and_trimmed(self):
        for n in range(22):
            self.state.jobs[f"j{n}"] = {"id": f"j{n}", "owner": OWNER, "status": "done", "created": f"2026-10-22T00:{n:02d}"}
        self.state.jobs["x"] = {"id": "x", "owner": "someone", "status": "done", "created": "2026-10-22T09:00"}
        self.state.jobs_loaded.add(OWNER)
        self.state._save_jobs(OWNER)
        stored = json.loads(self.state.core.storage.get_state(f"jobs:{OWNER}"))
        self.assertEqual(len(stored), 20)
        self.assertEqual(stored[0]["id"], "j21")
        self.assertNotIn("x", [j["id"] for j in self.state.job_list(self.viewer)])


def chat_request(state, viewer, session_id: str, text: str, out: dict) -> threading.Thread:
    """화면의 보통 전송과 같은 길 — 대기열에 서서 차례가 오면 잠금을 잡고 처리한다(web._stream 과 같은 순서)."""
    ticket = Ticket(viewer.id, session_id=session_id)
    with state.queue_cv:
        state.queue.append(ticket)
        state.queue_cv.notify_all()

    def run():
        try:
            if state.wait_turn(ticket):
                with state.ticket_running(ticket, "chat", lambda _chunk: None):
                    out["result"] = state.process(text, None, viewer=viewer, session_id=session_id)
                    out["permission_inside"] = state.core.permission_mode
        finally:
            with state.queue_cv:
                state.queue.remove(ticket)
                state.queue_cv.notify_all()
    thread = threading.Thread(target=run)
    thread.start()
    return thread


class YieldAndRetryTests(unittest.TestCase):
    """ADR-110: 뒤 작업은 단계 사이에서 기다리는 대화에 모델을 비켜 주고, 끊긴·실패한 일은 '다시 맡기기'."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.viewer = self.state.owner_viewer()
        self.sid = self.state.session_id
        self.other = self.state.core.storage.create_session(OWNER)

    def tearDown(self):
        self.state.shutting_down = True
        self.temp.cleanup()

    def test_chat_goes_in_between_steps_and_the_job_continues_with_its_own_state(self):
        core, steps, chats = self.state.core, [], {}
        original = self.state.process
        started = threading.Event()

        def job_process(message, attachments=None, **kwargs):
            if kwargs.get("session_id") != self.sid:
                return original(message, attachments, **kwargs)
            core._turn_hint = "뒤 작업 차례 안내"
            core.permission_mode = "read"
            steps.append("1")
            started.set()
            time.sleep(0.3)  # 그사이 대화가 들어와 기다린다
            core._yield_turn()
            steps.append(("2", core._turn_hint, core.permission_mode, self.state.running is not None))
            core.response_chunk_callback("[2단계]\n")
            return {"response": "끝", "session_id": self.sid}
        self.state.process = job_process
        job = self.state.start_job(self.viewer, self.sid, "/eda 긴 일", None)
        self.assertTrue(started.wait(5))
        thread = chat_request(self.state, self.viewer, self.other, "그동안 안녕", chats)
        thread.join(10)
        self.assertIn("GENERAL", chats["result"]["response"])  # 대화가 뒤 작업이 끝나기 전에 답했다
        self.assertNotEqual(chats["permission_inside"], "read")  # 대화는 제 권한으로
        done = wait_for(self.state, job["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(steps[1], ("2", "뒤 작업 차례 안내", "read", True))  # 뒤 작업은 제 상태로 이어서
        self.assertIn("잠시 비켜 줍니다", done["progress"])
        self.assertIn("[이어서 합니다]\n[2단계]", done["progress"])
        self.assertIsNone(core.turn_yield)
        self.assertFalse(self.state.lock._is_owned())
        rooms = [m["content"] for m in core.storage.message_page(self.other)["messages"]]
        self.assertEqual(rooms[0], "그동안 안녕")

    def test_a_long_document_summary_lets_chat_in_between_pieces(self):
        from tests.test_doc_digest import RecordingSummarizer, long_text

        class SlowSummarizer(RecordingSummarizer):
            def generate(self, messages, **kwargs):
                time.sleep(0.15)
                return super().generate(messages, **kwargs)
        core = self.state.core
        core.local_summarizer = summarizer = SlowSummarizer()
        core.knowledge.import_bytes(long_text().encode(), "긴책.txt", source_path="대화 첨부", scope=f"session:{self.sid}")
        job = self.state.start_job(self.viewer, self.sid, "이 문서 전체 요약해줘", None)
        deadline = time.monotonic() + 5
        while not summarizer.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        chats = {}
        chat_request(self.state, self.viewer, self.other, "그동안 안녕", chats).join(10)
        pieces_when_chat_answered = len(summarizer.calls)
        done = wait_for(self.state, job["id"], timeout=20)
        self.assertIn("GENERAL", chats["result"]["response"])
        self.assertLess(pieces_when_chat_answered, max(summarizer.calls))  # 요약이 다 끝나기 전에 대화가 답했다
        self.assertEqual(summarizer.calls, list(range(1, max(summarizer.calls) + 1)))  # 조각은 빠짐없이 한 번씩
        self.assertEqual(done["status"], "done")
        self.assertIn("조각으로 나눠 먼저 요약한 것을", core.storage.message_page(self.sid)["messages"][-1]["content"])

    def test_no_waiting_chat_means_no_pause(self):
        core, seen = self.state.core, []

        def job_process(message, attachments=None, **kwargs):
            core._yield_turn()
            seen.append(self.state.jobs[next(iter(self.state.jobs))]["status"])
            return {"response": "끝", "session_id": self.sid}
        self.state.process = job_process
        done = wait_for(self.state, self.state.start_job(self.viewer, self.sid, "일", None)["id"])
        self.assertEqual((done["status"], seen), ("done", ["running"]))
        self.assertNotIn("비켜", done["progress"])

    def test_retry_uses_the_saved_request_and_permission(self):
        saved = [{"id": "a1", "owner": OWNER, "session_id": self.sid, "status": "running", "created": "2026-10-22T01:00:00",
                  "request": "긴 문서 요약", "message": "이 문서 전체 요약해줘" + "!" * 300, "permission": "read"}]
        self.state.core.storage.set_state(f"jobs:{OWNER}", json.dumps(saved))
        self.assertEqual(self.state.job_message(self.viewer, "a1"), (self.sid, "이 문서 전체 요약해줘" + "!" * 300, "read"))
        self.state.jobs["d1"] = {"id": "d1", "owner": OWNER, "session_id": self.sid, "status": "done", "message": "x"}
        with self.assertRaisesRegex(ValueError, "끝나지 못한"):
            self.state.job_message(self.viewer, "d1")
        self.state.jobs["o1"] = {"id": "o1", "owner": OWNER, "session_id": self.sid, "status": "failed",
                                 "request": "가" * 200}
        with self.assertRaisesRegex(ValueError, "원문이 남아 있지 않아"):
            self.state.job_message(self.viewer, "o1")
        self.state.jobs["s1"] = {"id": "s1", "owner": "someone", "session_id": self.sid, "status": "failed", "message": "x"}
        with self.assertRaisesRegex(ValueError, "찾지 못했습니다"):
            self.state.job_message(self.viewer, "s1")

    def test_saved_records_always_fit_one_state_cell(self):
        for n in range(20):
            self.state.jobs[f"j{n}"] = {"id": f"j{n}", "owner": OWNER, "status": "failed" if n % 2 else "done",
                                        "created": f"2026-10-22T00:{n:02d}", "progress": "진" * 1500, "result": "답" * 600,
                                        "request": "요" * 200, "message": "원" * 4000}
        self.state.jobs_loaded.add(OWNER)
        self.state._save_jobs(OWNER)  # 예전에는 1만 자를 넘어 저장이 실패했다
        stored = json.loads(self.state.core.storage.get_state(f"jobs:{OWNER}"))
        self.assertGreater(len(stored), 0)
        self.assertEqual(stored[0]["id"], "j19")
        self.assertTrue(all(job["progress"] == "" for job in stored))
        self.assertTrue(all(job["message"] == "" for job in stored if job["status"] == "done"))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Jarvis-Token": "test-token"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_job_endpoint_returns_at_once_and_one_request_per_room(self):
        sid = self.state.session_id
        hold = self.state.lock.acquire()  # 모델이 바쁜 것처럼 — 뒤 작업은 기다린다
        try:
            status, data = self.post("/api/job", {"message": "뒤에서 해 줘", "session_id": sid})
            self.assertEqual(status, 200)
            self.assertEqual(data["job"]["status"], "queued")
            status, data2 = self.post("/api/job", {"message": "또", "session_id": sid})
            self.assertEqual(status, 409)
            self.assertIn("앞선 요청", data2["error"])
            snapshot = self.state.snapshot(self.state.owner_viewer())
            self.assertTrue(snapshot["queue"]["requests"][0]["background"])
        finally:
            if hold:
                self.state.lock.release()
        self.assertEqual(wait_for(self.state, data["job"]["id"])["status"], "done")

    def test_retry_endpoint_starts_the_same_request_again(self):
        sid = self.state.session_id
        self.state.jobs["a1"] = {"id": "a1", "owner": OWNER, "session_id": sid, "status": "interrupted",
                                 "created": "2026-10-22T01:00:00", "request": "다시 할 일", "message": "다시 할 일"}
        self.state.jobs_loaded.add(OWNER)
        status, data = self.post("/api/job", {"retry": "a1", "session_id": sid})
        self.assertEqual(status, 200)
        self.assertEqual(data["job"]["request"], "다시 할 일")
        self.assertEqual(wait_for(self.state, data["job"]["id"])["status"], "done")
        status, data = self.post("/api/job", {"retry": "nope", "session_id": sid})
        self.assertEqual(status, 400)

    def test_job_rejects_attachments(self):
        status, data = self.post("/api/job", {"message": "그림 봐 줘", "session_id": self.state.session_id,
                                              "documents": ["abc"]})
        self.assertIn(status, (400, 409))


if __name__ == "__main__":
    unittest.main()
