"""대화에서 말로 코딩 세션(2026-10-23) — '파일 고쳐줘' 로 시작, 허락은 '응', 결과·독립 검사, '되돌려줘', 파일 없는 코드 질문은 평소 대화."""
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from jarvis.harness.intent import is_coding_request, mentioned_files, permission_reply
from jarvis.web import make_server
from test_harness import Script, call
from test_web import CATALOG, make_state


class IntentTests(unittest.TestCase):
    def test_project_file_plus_edit_word_or_saying_coding_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "rtl").mkdir()
            (root / "rtl" / "alex_counter.sv").write_text("module m; endmodule\n", encoding="utf-8")
            (root / "logs").mkdir()
            (root / "logs" / "secret.py").write_text("x = 1\n", encoding="utf-8")
            self.assertEqual(mentioned_files("alex_counter.sv에 리셋 추가해줘", root), ["rtl/alex_counter.sv"])
            self.assertTrue(is_coding_request("alex_counter.sv에 리셋 추가해줘", root))
            self.assertTrue(is_coding_request("코딩 세션에서 테스트 정리해줘", root))
            self.assertFalse(is_coding_request("alex_counter.sv 설명해줘", root))         # 고치기 말이 없으면 대화
            self.assertFalse(is_coding_request("파이썬 정렬 코드 짜줘", root))             # 프로젝트 파일이 없으면 대화
            self.assertFalse(is_coding_request("secret.py 고쳐줘", root))                 # 빼는 폴더(logs)는 안 찾는다
            self.assertFalse(is_coding_request("/read alex_counter.sv", root))
        self.assertEqual([permission_reply(t) for t in ("응", "네 진행해", "항상 허락", "아니 하지마", "오늘 날씨 어때?")],
                         ["once", "once", "always", "reject", ""])
        # 짧은 '노·no' 는 낱말일 때만 거절(2026-10-26: '노트에 …'·'no-op …' 이 거절로 읽혔다)
        self.assertEqual([permission_reply(t) for t in ("노", "노노", "no", "No thanks", "nope", "아니요")], ["reject"] * 6)
        self.assertEqual([permission_reply(t) for t in ("노트에 적어둔 거 말고", "no-op 함수는?", "nothing")], ["", "", ""])


class CodeChatTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("jarvis.freemodels._download", return_value=CATALOG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.state = make_state(base)
        self.root = (base / "proj").resolve()
        (self.root / "tests").mkdir(parents=True)
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.root / "tests" / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8")
        self.state.core._approved_file_root = self.root
        self.state.core.current_project_root = lambda: self.root
        self.model = Script([("", [call("read", filePath="calc.py")]),
                             ("", [call("edit", filePath="calc.py", oldString="a - b", newString="a + b")]),
                             ("add 를 덧셈으로 고쳤습니다", [])])
        self.state.code.provider_getter = lambda: self.model
        self.server = make_server(self.state, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def say(self, text):
        body = json.dumps({"message": text, "session_id": self.state.session_id}).encode("utf-8")
        request = urllib.request.Request(self.base + "/api/chat/stream", data=body, method="POST",
                                         headers={"X-Jarvis-Token": "test-token", "Content-Type": "application/json"})
        chunks = []
        with urllib.request.urlopen(request, timeout=30) as response:
            for raw in response:
                event = json.loads(raw)
                if event["type"] == "chunk":
                    chunks.append(event["text"])
                if event["type"] == "done":
                    return event["response"], "".join(chunks)
        self.fail("done 이 오지 않았습니다")

    def test_say_fix_then_yes_then_revert(self):
        reply, progress = self.say("calc.py 의 add 가 틀렸어 고쳐줘")
        self.assertIn("코딩 세션 시작", progress)
        self.assertIn("calc.py", reply)
        self.assertIn("+    return a + b", reply)          # 허락을 물으며 diff 를 보여 준다
        self.assertIn("'응'", reply)
        for _ in range(3):                                   # 고치기 허락, 독립 검사 명령 허락
            reply, _ = self.say("응")
            if "바뀐 파일" in reply:
                break
        self.assertIn("add 를 덧셈으로 고쳤습니다", reply)
        self.assertIn("바뀐 파일: calc.py", reply)
        self.assertIn("독립 검사: 통과", reply)
        self.assertIn("a + b", (self.root / "calc.py").read_text())
        reply, _ = self.say("되돌려줘")
        self.assertIn("calc.py", reply)
        self.assertIn("a - b", (self.root / "calc.py").read_text())
        history = [m.content for m in self.state.core.storage.get_messages(self.state.session_id, 50)]
        self.assertIn("calc.py 의 add 가 틀렸어 고쳐줘", history)   # 말과 답이 대화에 남는다
        self.assertTrue(any("바뀐 파일: calc.py" in m for m in history))

    def test_code_questions_without_a_project_file_stay_in_chat(self):
        self.assertIsNone(self.state.code_chat_plan(self.state.owner_viewer(), self.state.session_id, "파이썬 정렬 코드 짜줘"))
        self.assertEqual(self.state.code.sessions, {})


if __name__ == "__main__":
    unittest.main()
