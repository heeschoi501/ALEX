"""대화 제목(2026-10-11 사용자: "첫 메시지를 바로 제목으로 만들어 버리는데 좀 다르게"). 첫 답 뒤에 모델이 주제로 짓는다."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.core import clean_title
from jarvis.providers import OllamaProvider
from jarvis.web import make_server, provisional_title
from test_web import make_state


class TitleModel(OllamaProvider):
    """요약용 모델 자리 — 받은 문맥을 기록하고 정해 둔 제목을 돌려준다."""
    def __init__(self, reply):
        super().__init__("title-model")
        self.reply, self.calls = reply, []

    def health_check(self):
        return True

    def generate(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


class TitleTextTests(unittest.TestCase):
    def test_model_reply_is_cleaned_to_a_short_title(self):
        self.assertEqual(clean_title('제목: "파이썬 리스트와 딕셔너리 비교".\n설명은 생략'), "파이썬 리스트와 딕셔너리 비교")
        self.assertEqual(clean_title("## **CDC 2단 동기화기 원리**"), "CDC 2단 동기화기 원리")
        self.assertEqual(clean_title("\n\n"), "")
        self.assertLessEqual(len(clean_title("가" * 100)), 40)

    def test_provisional_title_is_one_line_and_cut_at_a_word(self):
        self.assertEqual(provisional_title("  안녕\n하세요  "), "안녕 하세요")
        long = "근데 진짜 이제는 간단한 작업 같은 건 너랑 해도 될 것 같긴 해 그래서 물어보는 건데"
        cut = provisional_title(long)
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(len(cut), 41)
        self.assertTrue(long.startswith(cut[:-1]))


class TitleHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.model = TitleModel("간단한 인사 나누기")
        self.state.core.local_summarizer = self.model
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
        return urllib.request.urlopen(request, timeout=5)

    def stream(self, message):
        with self.post("/api/chat/stream", {"message": message, "session_id": self.state.session_id}) as response:
            return [json.loads(line) for line in response]

    def test_first_answer_gets_a_model_title_and_later_answers_do_not(self):
        sid = self.state.session_id
        events = self.stream("안녕 반가워, 오늘 뭐 하고 놀까")
        self.assertEqual([e["type"] for e in events], ["start", "done", "title"])
        self.assertEqual(events[-1], {"type": "title", "session_id": sid, "title": "간단한 인사 나누기"})
        self.assertEqual(self.state.core.storage.session_title(sid), "간단한 인사 나누기")
        self.assertIn("안녕 반가워", self.model.calls[0][-1].content)  # 첫 질문과 답을 보고 짓는다
        self.assertEqual([e["type"] for e in self.stream("두 번째 말")], ["start", "done"])
        self.assertEqual(len(self.model.calls), 1)

    def test_a_failed_title_keeps_the_provisional_one(self):
        self.model.reply = "   "
        self.assertEqual([e["type"] for e in self.stream("파이썬 리스트 설명해줘")], ["start", "done"])
        self.assertEqual(self.state.core.storage.session_title(self.state.session_id), "파이썬 리스트 설명해줘")

    def test_titles_can_be_renamed_by_hand_but_only_your_own(self):
        sid = self.state.session_id
        with self.post("/api/session/rename", {"session_id": sid, "title": "  내가 정한 제목 "}) as r:
            self.assertEqual(json.load(r)["title"], "내가 정한 제목")
        other = self.state.core.storage.create_session(owner="someone")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/session/rename", {"session_id": other, "title": "가로채기"})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
