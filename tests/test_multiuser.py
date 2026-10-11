"""여러 사람이 쓰기·프로젝트(2026-10-11). 사람마다 대화·기억·할 일·프로젝트가 따로이고, 모델은 차례로 나눠 쓴다.

브라우저 없이 실제 HTTP 로 확인한다. 이 PC(127.0.0.1)에서 쿠키 없이 오면 주인(관리자), '사용자 전환'(alex_manual)
뒤에는 로그인해야 한다. 밖(Tailscale Serve 로 넘어온 요청)은 로그인하거나 연결된 Tailscale 계정이어야 한다.
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from jarvis import accounts
from jarvis.config import load_settings
from jarvis.storage import OWNER
from jarvis.web import make_server
from test_web import make_state

REMOTE = "my-pc.tail1234.ts.net"


class MultiUserHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.storage = self.state.core.storage
        self.member = self.storage.create_user("민수", "minsu", accounts.hash_password("member-pass-1"))
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def call(self, path, body=None, cookie="", headers=None, token="test-token"):
        """(상태 코드, JSON, Set-Cookie 목록). 오류 응답도 그대로 돌려준다."""
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-Jarvis-Token": token, "Content-Type": "application/json",
                                                  **({"Cookie": cookie} if cookie else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}"), response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}"), error.headers.get_all("Set-Cookie") or []

    def login_member(self) -> str:
        status, body, cookies = self.call("/api/login", {"login": "minsu", "password": "member-pass-1"}, cookie="alex_manual=1")
        self.assertEqual(status, 200, body)
        session = next(c for c in cookies if c.startswith("alex_session="))
        self.assertIn("HttpOnly", session)
        self.assertIn("SameSite=Strict", session)
        return "alex_manual=1; " + session.split(";", 1)[0]

    def test_this_pc_is_the_owner_until_switching_user(self):
        status, me, _ = self.call("/api/me")
        self.assertEqual((status, me["user"]["id"], me["user"]["role"]), (200, OWNER, "admin"))
        status, body, cookies = self.call("/api/logout", {})
        self.assertTrue(any(c.startswith("alex_manual=1") for c in cookies))
        self.assertIsNone(self.call("/api/me", cookie="alex_manual=1")[1]["user"])
        self.assertEqual(self.call("/api/state", cookie="alex_manual=1")[0], 401)

    def test_each_person_sees_only_their_own_conversations_memories_and_tasks(self):
        owner_sid = self.state.session_id
        self.storage.add_message(owner_sid, "user", "주인의 비밀 대화")
        self.storage.remember("호칭", "주인", owner=OWNER)
        cookie = self.login_member()
        status, snap, _ = self.call("/api/state", cookie=cookie)
        self.assertEqual(snap["user"]["login"], "minsu")
        self.assertFalse(snap["is_admin"])
        self.assertNotIn(owner_sid, [s["session_id"] for s in snap["sessions"]])
        self.assertEqual(snap["memories"], {})
        self.assertNotIn("settings", snap)  # 모델·엔진 설정은 관리자 화면에만
        # 주인의 대화를 이름으로 맞혀도 열 수 없다
        self.assertEqual(self.call(f"/api/state?session_id={owner_sid}", cookie=cookie)[1]["session_id"], snap["session_id"])
        self.assertEqual(self.call(f"/api/history?session_id={owner_sid}&before=999", cookie=cookie)[0], 400)
        self.assertEqual(self.call("/api/chat", {"message": "끼어들기", "session_id": owner_sid}, cookie=cookie)[0], 409)
        self.assertEqual(self.call("/api/session/delete", {"target": owner_sid, "confirmed": True}, cookie=cookie)[0], 400)
        self.assertTrue(self.storage.session_exists(owner_sid))
        # 기억·할 일은 바로 저장되고(차례를 기다리지 않는다) 자기 것만 보인다
        self.assertEqual(self.call("/api/memory", {"action": "add", "key": "호칭", "value": "민수"}, cookie=cookie)[1]["memories"], {"호칭": "민수"})
        self.assertEqual(self.storage.recall("호칭"), "주인")
        tasks = self.call("/api/task", {"action": "add", "text": "단어 외우기"}, cookie=cookie)[1]["tasks"]
        self.assertEqual([t["text"] for t in tasks], ["단어 외우기"])
        self.assertEqual(self.storage.list_tasks(), [])
        self.assertEqual(self.call("/api/task", {"action": "done", "id": tasks[0]["task_id"]})[0], 200)  # 주인이 남의 할 일 번호로
        self.assertEqual(self.storage.list_tasks(owner=self.member.user_id)[0].status, "open")

    def test_members_cannot_use_admin_endpoints_or_pc_commands(self):
        cookie = self.login_member()
        for path, body in (("/api/settings", {"settings": {}}), ("/api/shutdown", {}), ("/api/users", {"action": "create"}),
                           ("/api/workspace", {"path": "C:/", "confirmed": True})):
            with self.subTest(path=path):
                status, reply, _ = self.call(path, body, cookie=cookie)
                self.assertEqual(status, 403)
                self.assertTrue(reply["admin_only"])
        self.assertFalse(self.state.shutting_down)
        for command in ("/tree 2", "/learn-file C:/secret.txt", "/build 계산기", "/wrong 틀렸어", "/profile"):
            with self.subTest(command=command):
                self.assertIn("관리자만", self.call("/api/chat", {"message": command}, cookie=cookie)[1]["response"])
        self.assertIn("기억했습니다", self.call("/api/chat", {"message": "/remember 언어=영어"}, cookie=cookie)[1]["response"])
        self.assertEqual(self.storage.list_memories(owner=self.member.user_id), {"언어": "영어"})

    def test_member_chat_runs_without_learning_or_pc_tools_and_restores_the_owner_context(self):
        core = self.state.core
        core.auto_lessons, core.code_check = True, True
        seen = []
        original = core.handle

        def spy(sid, text):
            seen.append((core.member_mode, core.auto_lessons, core.code_check))
            return original(sid, text)

        core.handle = spy
        cookie = self.login_member()
        self.assertEqual(self.call("/api/chat", {"message": "안녕"}, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/chat", {"message": "안녕"})[0], 200)
        self.assertEqual(seen, [(True, False, False), (False, True, True)])
        self.assertEqual((core.member_mode, core.auto_lessons, core.code_check), (False, True, True))

    def test_wrong_passwords_lock_the_login_for_a_minute(self):
        for _ in range(5):
            self.assertEqual(self.call("/api/login", {"login": "minsu", "password": "nope-nope"})[0], 401)
        status, body, _ = self.call("/api/login", {"login": "minsu", "password": "member-pass-1"})
        self.assertEqual(status, 401)
        self.assertIn("너무 많습니다", body["error"])

    def test_admin_manages_users_and_disabling_ends_their_login(self):
        cookie = self.login_member()
        status, body, _ = self.call("/api/users", {"action": "create", "name": "지원", "login": "jiwon", "password": "short"})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("/api/users", {"action": "disable", "id": OWNER})[0], 400)  # 주인은 멈출 수 없다
        users = self.call("/api/users", {"action": "disable", "id": self.member.user_id})[1]["users"]
        self.assertTrue(next(u for u in users if u["login"] == "minsu")["disabled"])
        self.assertEqual(self.call("/api/state", cookie=cookie)[0], 401)

    def test_remote_requests_need_a_login_or_a_linked_tailscale_account(self):
        self.state.settings = replace(load_settings(), remote_web_host=REMOTE, remote_web_users="owner@example.com")
        remote = {"Host": REMOTE, "Tailscale-User-Login": "owner@example.com", "X-Forwarded-For": "100.64.0.2"}
        self.assertEqual(self.call("/api/state", headers=remote)[0], 401)  # 밖에서는 주인 자동 로그인 없음
        self.storage.update_user(OWNER, tailscale_login="owner@example.com")
        status, snap, _ = self.call("/api/state", headers=remote)
        self.assertEqual((status, snap["user"]["id"]), (200, OWNER))
        # 연결된 계정이면 허용 목록에 없어도 들어온다(관리자가 사용자 관리에서 연결)
        self.storage.update_user(self.member.user_id, tailscale_login="friend@example.com")
        friend = {**remote, "Tailscale-User-Login": "friend@example.com"}
        self.assertEqual(self.call("/api/state", headers=friend)[1]["user"]["login"], "minsu")
        status, _, cookies = self.call("/api/login", {"login": "minsu", "password": "member-pass-1"}, headers=friend)
        self.assertTrue(any("Secure" in c for c in cookies))

    def test_forwarded_requests_cannot_pretend_to_be_this_pc(self):
        # Serve 로 들어온 요청이 Host 를 127.0.0.1 로 꾸며도 주인으로 들어오지 못한다
        for header in ({"X-Forwarded-For": "100.64.0.9"}, {"Tailscale-User-Login": "x@example.com"}):
            with self.subTest(header=header):
                self.assertEqual(self.call("/api/me", headers=header)[0], 403)

    def test_pictures_belong_to_whoever_made_them(self):
        self.storage.record_artifacts(["20261011-120000-1.png"], OWNER)
        cookie = self.login_member()
        self.assertEqual(self.call("/api/image?name=20261011-120000-1.png", cookie=cookie)[0], 404)
        self.assertEqual(self.call("/api/document?name=alex-document-20261011-120000-0123456789abcdef.pdf", cookie=cookie)[0], 404)

    def test_second_person_waits_in_line_and_sees_the_position(self):
        cookie = self.login_member()
        member_sid = self.call("/api/state", cookie=cookie)[1]["session_id"]
        events = []

        def ask():
            request = urllib.request.Request(self.url + "/api/chat/stream", data=json.dumps({"message": "기다려요", "session_id": member_sid}).encode(),
                                             headers={"X-Jarvis-Token": "test-token", "Content-Type": "application/json", "Cookie": cookie})
            with urllib.request.urlopen(request, timeout=10) as response:
                for line in response:
                    events.append(json.loads(line))

        with self.state.lock:  # 다른 사람의 답이 만들어지는 중
            worker = threading.Thread(target=ask)
            worker.start()
            for _ in range(50):
                if any(e["type"] == "queue" for e in events):
                    break
                threading.Event().wait(0.05)
            self.assertEqual([e for e in events if e["type"] == "queue"][0]["position"], 1)
            # 줄 서 있는 동안 같은 사람의 두 번째 질문은 받지 않는다
            self.assertEqual(self.call("/api/chat/stream", {"message": "또", "session_id": member_sid}, cookie=cookie)[0], 409)
        worker.join(10)
        self.assertEqual([e["type"] for e in events][0], "start")
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("GENERAL", events[-1]["response"])
        self.assertEqual(self.state.queue, [])

    def test_waiting_request_can_be_cancelled_by_its_owner_only(self):
        cookie = self.login_member()
        events = []

        def ask():
            request = urllib.request.Request(self.url + "/api/chat/stream", data=json.dumps({"message": "취소할 질문"}).encode(),
                                             headers={"X-Jarvis-Token": "test-token", "Content-Type": "application/json", "Cookie": cookie})
            with urllib.request.urlopen(request, timeout=10) as response:
                for line in response:
                    events.append(json.loads(line))

        with self.state.lock:
            worker = threading.Thread(target=ask)
            worker.start()
            for _ in range(50):
                if events:
                    break
                threading.Event().wait(0.05)
            request_id = events[0]["request_id"]
            self.storage.create_user("지원", "jiwon", accounts.hash_password("other-pass-1"))
            other = self.call("/api/login", {"login": "jiwon", "password": "other-pass-1"}, cookie="alex_manual=1")[2]
            other_cookie = "alex_manual=1; " + next(c for c in other if c.startswith("alex_session=")).split(";", 1)[0]
            self.assertEqual(self.call("/api/cancel", {"request_id": request_id}, cookie=other_cookie)[0], 409)
            self.assertEqual(self.call("/api/cancel", {"request_id": request_id}, cookie=cookie)[0], 200)
            worker.join(10)
        self.assertTrue(events[-1].get("cancelled"))
        self.assertEqual(self.storage.message_count(self.call("/api/state", cookie=cookie)[1]["session_id"]), 0)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.core = self.state.core
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def post(self, path, body):
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                         headers={"X-Jarvis-Token": "test-token", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def upload(self, project_id, name, data):
        request = urllib.request.Request(self.url + "/api/project/upload", data=data, headers={
            "X-Jarvis-Token": "test-token", "Content-Type": "text/markdown", "X-Project-Id": project_id, "X-File-Name": name})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_project_instructions_and_files_apply_only_inside_the_project(self):
        view = self.post("/api/project", {"action": "create", "name": "RTL 리뷰", "instructions": "항상 표로 정리해."})
        pid = view["project"]["id"]
        view = self.upload(pid, "fifo-notes.md", "# FIFO\nFIFO 의 almost_full 은 깊이-2 에서 켠다.\n".encode("utf-8"))
        self.assertEqual([f["title"] for f in view["files"]], ["fifo-notes.md"])
        outside = self.state.session_id
        inside = self.post("/api/session/new", {"project_id": pid})["session_id"]
        self.assertNotEqual(inside, outside)
        for sid in (inside, outside):
            self.core.storage.add_message(sid, "user", "FIFO almost_full 은 언제 켜?")
        messages = "\n".join(m.content for m in self.core._build_messages(inside) if m.role == "system")
        self.assertIn("[프로젝트: RTL 리뷰]", messages)
        self.assertIn("항상 표로 정리해.", messages)
        self.assertIn("깊이-2", messages)
        outside_messages = "\n".join(m.content for m in self.core._build_messages(outside) if m.role == "system")
        self.assertNotIn("항상 표로 정리해.", outside_messages)
        self.assertNotIn("깊이-2", outside_messages)  # 프로젝트 자료는 모두의 학습 자료에 섞이지 않는다
        self.assertEqual(self.core.knowledge.documents(), [])
        # 왼쪽 목록에는 프로젝트 밖 대화만, 프로젝트 대화는 프로젝트 화면에
        with urllib.request.urlopen(urllib.request.Request(self.url + "/api/state?session_id=" + inside,
                                                           headers={"X-Jarvis-Token": "test-token"}), timeout=5) as r:
            snap = json.load(r)
        self.assertEqual(snap["project"], {"id": pid, "name": "RTL 리뷰"})
        self.assertNotIn(inside, [s["session_id"] for s in snap["sessions"]])
        self.assertEqual(snap["projects"][0]["session_count"], 1)

    def test_deleting_a_project_removes_its_chats_and_private_files(self):
        pid = self.post("/api/project", {"action": "create", "name": "임시"})["project"]["id"]
        self.upload(pid, "a.md", b"# only here\nprivate words\n")
        sid = self.post("/api/session/new", {"project_id": pid})["session_id"]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/project", {"action": "delete", "id": pid})
        caught.exception.close()
        reply = self.post("/api/project", {"action": "delete", "id": pid, "confirmed": True})
        self.assertFalse(self.core.storage.session_exists(sid))
        self.assertNotEqual(reply["session_id"], sid)
        self.assertEqual(self.core.knowledge.search("private words", scopes=("", f"project:{pid}")), [])

    def test_new_chat_reuses_an_empty_one(self):
        first = self.post("/api/session/new", {})["session_id"]
        self.assertEqual(self.post("/api/session/new", {})["session_id"], first)
        self.core.storage.add_message(first, "user", "hi")
        self.assertNotEqual(self.post("/api/session/new", {})["session_id"], first)

    def test_someone_elses_project_is_invisible(self):
        other = self.core.storage.create_user("민수", "minsu", accounts.hash_password("member-pass-1"))
        pid = self.core.storage.create_project(other.user_id, "민수 프로젝트")
        for body in ({"action": "update", "id": pid, "name": "가로채기"}, {"action": "delete", "id": pid, "confirmed": True}):
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as caught:
                self.post("/api/project", body)
            caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/session/new", {"project_id": pid})
        caught.exception.close()
        self.assertEqual(self.core.storage.get_project(pid)["name"], "민수 프로젝트")


if __name__ == "__main__":
    unittest.main()
