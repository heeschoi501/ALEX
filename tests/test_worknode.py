"""작업 노드(ADR-125): 메인 ALEX 의 코드 세션이 다른 기기에서 돈다. ssh 대신 이 PC 에서 일꾼(python -m jarvis.harness.worknode)을
따로 띄워 끝까지 확인한다 — 파일·명령·되돌리기는 일꾼 쪽, 모델은 메인 쪽, 허락 요청은 메인 화면(사건)으로.
"""
import subprocess
import sys
import time
from pathlib import Path

from jarvis.harness.sessions import CodeSessions
from jarvis.harness.worknode import NodeRoot
from jarvis.providers import ContextExhausted
from test_harness import Fixture, Script, call

ROOT = Path(__file__).resolve().parent.parent
NODE = (sys.executable, "-X", "utf8", "-m", "jarvis.harness.worknode")


def node_popen(command, **kwargs):
    kwargs.pop("cwd", None)
    return subprocess.Popen(command, cwd=ROOT, **kwargs)


class WorkNodeTests(Fixture):
    def sessions(self, model) -> CodeSessions:
        return CodeSessions(lambda: model, node_popen=node_popen)

    def open(self, sessions, tier="ask", path=None):
        session = sessions.create("owner", NodeRoot("laptop", str(path or self.root), NODE), tier)
        self.addCleanup(lambda: session.id in sessions.sessions and sessions.delete(session.id))
        return session

    def wait(self, sessions, sid, reply="once") -> list[dict]:
        seen, since, deadline = [], 0, time.monotonic() + 60
        while time.monotonic() < deadline:
            for event in sessions.events(sid, since, wait=0.2):
                since = event["n"] + 1
                seen.append(event)
                if event["type"] == "permission.asked":
                    sessions.reply_permission(sid, event["request"]["id"], reply)
                if event["type"] == "session.idle":
                    return seen
        self.fail("작업 노드의 코딩 세션이 끝나지 않았습니다.")

    def test_the_session_works_there_and_the_model_answers_here(self):
        self.put("calc.py", "def add(a, b):\n    return a - b\n")
        model = Script([("", [call("read", filePath="calc.py")]),
                        ("", [call("edit", filePath="calc.py", oldString="a - b", newString="a + b")]),
                        ("고쳤습니다", [])])
        sessions = self.sessions(model)
        session = self.open(sessions)
        self.assertEqual(session.public()["directory"], f"laptop:{self.root}")
        sessions.prompt(session.id, "calc.py 의 add 를 고쳐라")
        events = self.wait(sessions, session.id)
        self.assertIn("a + b", (self.root / "calc.py").read_text(encoding="utf-8"))
        self.assertEqual(len(model.seen), 3)                                  # 모델 세 차례 모두 메인 모델이 답했다
        self.assertTrue(any(e["type"] == "permission.asked" for e in events))   # 'ask' 단계의 고치기 허락이 메인으로 왔다
        self.assertEqual(sessions.get(session.id).public()["status"], "idle")
        self.assertIn("calc.py", sessions.diff(session.id))
        self.assertTrue(any(m["role"] == "assistant" for m in sessions.messages(session.id)))
        self.assertEqual(sessions.revert(session.id, 0), ["calc.py"])        # 되돌리기도 그 기기 파일에서
        self.assertIn("a - b", (self.root / "calc.py").read_text(encoding="utf-8"))

    def test_a_rejected_permission_leaves_the_file_alone(self):
        self.put("calc.py", "def add(a, b):\n    return a - b\n")
        model = Script([("", [call("edit", filePath="calc.py", oldString="a - b", newString="a + b")]), ("못 고쳤습니다", [])])
        sessions = self.sessions(model)
        session = self.open(sessions)
        sessions.prompt(session.id, "calc.py 를 고쳐라")
        self.wait(sessions, session.id, reply="reject")
        self.assertIn("a - b", (self.root / "calc.py").read_text(encoding="utf-8"))

    def test_a_model_error_keeps_its_kind_so_the_agent_retries_without_thinking(self):
        self.put("a.txt", "x\n")
        model = Script([ContextExhausted("생각이 길어 끊었습니다"), ("끝", [])])
        sessions = self.sessions(model)
        session = self.open(sessions, tier="auto")
        sessions.prompt(session.id, "a.txt 를 보고 끝내라")
        self.wait(sessions, session.id)
        self.assertEqual(len(model.seen), 2)
        self.assertEqual(sessions.get(session.id).public()["last"]["text"], "끝")

    def test_a_missing_folder_there_is_reported(self):
        with self.assertRaisesRegex(ValueError, "열지 못했습니다"):
            self.sessions(Script([])).create("owner", NodeRoot("laptop", str(self.root / "없음"), NODE), "ask")

    def test_the_folder_box_opens_a_registered_node_and_keeps_local_paths_local(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from jarvis.config import Settings
        from jarvis.web import WebAppState
        state = SimpleNamespace(settings=replace(Settings(), work_nodes=r"laptop=alexuser@laptop-1:C:\ALEX\ALEX-main"))
        state.work_nodes = lambda: WebAppState.work_nodes(state)
        root = WebAppState.node_root(state, r"laptop:C:\Unity\MyGame")
        self.assertEqual((root.node, root.path), ("laptop", r"C:\Unity\MyGame"))
        self.assertEqual(root.command[-2:], ("alexuser@laptop-1", r'cd /d "C:\ALEX\ALEX-main" && py -3.11 -X utf8 -m jarvis.harness.worknode'))
        self.assertIsNone(WebAppState.node_root(state, r"C:\ALEX\jarvis"))        # 이 PC 경로
        self.assertIsNone(WebAppState.node_root(state, "sub/folder"))
        for raw in (r"mac:/Users/me/game", r"laptop:relative\path", 'laptop:C:\\a" & calc'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                WebAppState.node_root(state, raw)

    def test_a_dropped_link_is_reported_instead_of_hanging(self):
        sessions = self.sessions(Script([]))
        session = self.open(sessions)
        session.link.process.kill()
        session.link.process.wait(timeout=10)
        with self.assertRaisesRegex(ValueError, "연결이 끊겼습니다"):
            sessions.prompt(session.id, "아무 일")
        sessions.delete(session.id)
