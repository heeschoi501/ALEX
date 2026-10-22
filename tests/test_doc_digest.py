"""긴 문서 전체 요약(ADR-106, 2026-10-22) — 올린 문서가 질문에 다 들어가지 않을 때 '요약해줘' 는 조각마다 먼저 요약하고
그 요약들로 답한다. 일부·주제를 집은 질문과 짧은 문서는 예전 길(관련 조각·전문)."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis.core import Destination
from jarvis.digest import split_pages, summarize, summary_request
from jarvis.providers import BaseLLMProvider, ProviderError
from tests.test_web import make_state


class RecordingSummarizer(BaseLLMProvider):
    """조각 번호와 그 조각의 첫·끝 문장을 돌려준다 — 모든 조각을 처음부터 끝까지 읽었는지 확인용."""
    name = "summary"

    def __init__(self, fail_at: int = 0):
        self.calls: list[int] = []
        self.fail_at = fail_at
        self.num_predict, self.num_ctx, self.predicts = 3000, 196608, []

    def generate(self, messages, **kwargs):
        prompt = messages[-1].content
        match = re.search(r"전체 (\d+)개 중 (\d+)번째", prompt)
        index = int(match.group(2)) if match else 0
        if index and index == self.fail_at:
            self.fail_at = 0
            raise ProviderError("연결 끊김")
        self.calls.append(index)
        self.predicts.append(self.num_predict)
        sentences = re.findall(r"문장\d{5}", prompt)
        return f"- 조각요약{index}: {sentences[0] if sentences else ''}~{sentences[-1] if sentences else ''}"

    def health_check(self):
        return True


def long_text(count: int = 3000) -> str:
    return "".join(f"문장{n:05d} 내용입니다. " for n in range(count))


class SummaryRequestTests(unittest.TestCase):
    def test_whole_document_summary_wording(self):
        for text in ("요약해줘", "요약해줘\n\n[첨부 문서: 보고서.pdf · 31쪽 · 120,000자]", "이 문서 전체 요약해줘", "이 PDF 무슨 내용이야?", "올린 파일 핵심만 정리해줘", "3줄로 요약해줘",
                     "보고서 요약 부탁해", "정리해줘"):
            with self.subTest(text=text):
                self.assertTrue(summary_request(text))

    def test_parts_topics_conversation_and_other_questions_are_not(self):
        for text in ("3장 요약해줘", "12쪽 내용 요약해줘", "메모리 관련 내용 요약해줘", "지금까지 대화 요약해줘", "방금 답 요약해줘",
                     "파이썬 리스트 정렬하는 법 알려줘", "이 코드 정리해줘", "그림 정리해줘", "요약이라는 단어를 영어로 하면 뭐고 예문도 몇 개 들어 줄 수 있어?", ""):
            with self.subTest(text=text):
                self.assertFalse(summary_request(text))


class SplitTests(unittest.TestCase):
    def test_pages_are_kept_whole_and_labelled(self):
        pages = [(n, f"쪽{n} " + "가" * 900) for n in range(1, 11)]
        pieces = split_pages(pages, 4000)
        self.assertEqual(pieces[0][0], "1~4쪽")
        self.assertTrue(all(len(text) <= 4000 for _, text in pieces))
        joined = "\n".join(text for _, text in pieces)
        self.assertEqual([int(n) for n in re.findall(r"\[(\d+)쪽\]", joined)], list(range(1, 11)))

    def test_one_long_page_is_cut_and_nothing_is_lost(self):
        text = long_text(2000)
        pieces = split_pages([(1, text)], 5000)
        self.assertGreater(len(pieces), 1)
        self.assertEqual(pieces[0][0], f"1/{len(pieces)}번째 부분")
        self.assertTrue(all(len(body) <= 5000 for _, body in pieces))
        self.assertEqual("".join(body for _, body in pieces), text.strip())

    def test_too_long_digest_is_merged_to_fit(self):
        pieces = [(f"{n}쪽", "본문") for n in range(1, 21)]
        notes: list = [None] * 20
        calls = []

        def generate(messages):
            calls.append(messages[0].content[:12])
            return "가" * 600
        digest = summarize("책", pieces, notes, generate, room=3000, limit=400)
        self.assertLessEqual(len(digest), 3000)
        self.assertEqual(sum(1 for c in calls if c.startswith("긴 문서")), 20)
        self.assertTrue(any(c.startswith("같은 문서") for c in calls))
        self.assertTrue(digest.startswith("[1쪽"))


class ChatDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.core = self.state.core
        self.sid = self.state.session_id
        self.summarizer = RecordingSummarizer()
        self.core.local_summarizer = self.summarizer

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, data: bytes, name: str) -> str:
        return self.core.knowledge.import_bytes(data, name, source_path="대화 첨부", scope=f"draft:{self.sid}")["id"]

    def test_long_document_is_read_to_the_end_piece_by_piece(self):
        doc = self.upload(long_text().encode(), "긴책.txt")
        result = self.state.process("이 문서 전체 요약해줘", documents=[doc])
        reply = result["response"]
        count = max(self.summarizer.calls)
        self.assertGreater(count, 3)
        self.assertEqual(self.summarizer.calls, list(range(1, count + 1)))
        self.assertIn("조각별로 먼저 요약한 것", reply)  # 가짜 대화 모델은 받은 마지막 메시지를 그대로 돌려준다
        self.assertIn("문장00000", reply)
        self.assertIn("문장02999", reply)  # 마지막 문장까지
        self.assertIn(f"{count}조각으로 나눠 먼저 요약한 것을", reply)
        self.assertNotIn("— 일부만(", reply)
        self.assertEqual(set(self.summarizer.predicts), {8192})  # 요약하는 동안만 출력 상한을 올리고(잘림 방지)
        self.assertEqual(self.summarizer.num_predict, 3000)      # 끝나면 되돌린다
        self.assertEqual(result["route"], "local_general")
        # 저장되는 질문은 사용자 말 그대로
        self.assertEqual(self.core.storage.message_page(self.sid)["messages"][-2]["content"].split("\n")[0], "이 문서 전체 요약해줘")

    def test_second_summary_reuses_pieces_and_resumes_after_failure(self):
        doc = self.upload(long_text().encode(), "긴책.txt")
        self.summarizer.fail_at = 3
        reply = self.state.process("요약해줘", documents=[doc])["response"]
        self.assertIn("2조각까지 요약했습니다", reply)
        self.assertEqual(self.summarizer.calls, [1, 2])
        reply = self.state.process("요약해줘")["response"]
        self.assertEqual(self.summarizer.calls[:3], [1, 2, 3])  # 3번째부터 이어서
        self.assertIn("조각별로 먼저 요약한 것", reply)
        calls = len(self.summarizer.calls)
        self.state.process("3줄로 요약해줘")
        self.assertEqual(len(self.summarizer.calls), calls)  # 이미 요약한 조각은 다시 부르지 않는다

    def test_short_documents_and_specific_questions_keep_the_old_path(self):
        short = self.upload("회의 결론: 출시는 12월 3일.".encode(), "회의록.txt")
        reply = self.state.process("요약해줘", documents=[short])["response"]
        self.assertIn("전문", reply)
        filler = "".join(f"일반 문장 {n}. " for n in range(6000))
        doc = self.upload((filler + " 전압 강하 원인은 배선 저항이다. " + filler).encode(), "긴문서.txt")
        reply = self.state.process("전압 강하 원인이 뭐야?", documents=[doc])["response"]
        self.assertIn("일부만", reply)
        reply = self.state.process("3장 요약해줘")["response"]
        self.assertIn("일부만", reply)
        self.assertEqual(self.summarizer.calls, [])

    def test_named_document_is_chosen_over_the_newest(self):
        book = self.upload(long_text().encode(), "설계교재.txt")
        self.state.process("받았어?", documents=[book])
        newer = self.upload(long_text(2500).replace("문장", "항목").encode(), "다른자료.txt")
        self.state.process("이것도", documents=[newer])
        reply = self.state.process("설계교재 요약해줘")["response"]
        self.assertIn("설계교재.txt", reply.split("조각별로 먼저 요약한 것")[0].rsplit("===", 1)[-1])
        self.assertIn("문장02999", reply)

    def test_summary_after_a_picture_is_not_a_picture_change(self):
        doc = self.upload(long_text().encode(), "긴책.txt")
        self.state.process("받았어?", documents=[doc])
        self.core.image_generator = mock.MagicMock()
        self.core.last_exchange = {"session_id": self.sid, "destination": Destination.TOOL_IMAGE_GEN.value,
                                   "question": "그려줘", "answer": "그림"}
        result = self.state.process("정리해줘")
        self.assertIn("조각별로 먼저 요약한 것", result["response"])
        self.assertFalse([c for c in self.core.image_generator.method_calls if c[0].startswith("generate")])

    def test_summarizer_down_says_so(self):
        doc = self.upload(long_text().encode(), "긴책.txt")
        self.summarizer.health_check = lambda: False
        reply = self.state.process("요약해줘", documents=[doc])["response"]
        self.assertIn("요약 모델에 연결하지 못해", reply)


class RecordingGeneral(BaseLLMProvider):
    name = "general"

    def __init__(self):
        self.seen = []

    def generate(self, messages, **kwargs):
        self.seen.append(list(messages))
        return "# 요약 문서\n- 정리한 답"

    def health_check(self):
        return True


class LibraryAndDocumentTests(unittest.TestCase):
    """저장 자료(/ask-knowledge)의 전체 요약과 '요약해서 워드로'(같은 날 사용자 "5번까지 해" 의 3·4번)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.core = self.state.core
        self.sid = self.state.session_id
        self.summarizer = RecordingSummarizer()
        self.core.local_summarizer = self.summarizer
        self.general = RecordingGeneral()
        self.core.providers[Destination.LOCAL_GENERAL] = self.general
        self.core.providers[Destination.LOCAL_CODER] = self.general

    def tearDown(self):
        self.temp.cleanup()

    def library(self, text: str, name: str) -> str:
        return self.core.knowledge.import_bytes(text.encode(), name)["id"][:12]

    def prompt(self) -> str:
        return "\n".join(m.content for m in self.general.seen[-1])

    def test_long_library_material_is_summarized_whole(self):
        did = self.library(long_text(), "교재.txt")
        reply = self.state.process(f"/ask-knowledge {did} :: 이 자료의 핵심을 정리해줘")["response"]
        count = max(self.summarizer.calls)
        self.assertEqual(self.summarizer.calls, list(range(1, count + 1)))
        self.assertIn("문장00000", self.prompt())
        self.assertIn("문장02999", self.prompt())
        self.assertIn("[문서 전체 요약]", self.prompt())
        self.assertIn(f"{count}조각으로 나눠 먼저 요약한 것을 바탕으로", reply)

    def test_short_library_material_is_read_in_full_and_questions_keep_passages(self):
        did = self.library("FSM 리셋 시험기 메모. 클록은 100 MHz, 리셋은 동기식이다.", "메모.txt")
        reply = self.state.process(f"/ask-knowledge {did} :: 요약해줘")["response"]
        self.assertIn("리셋은 동기식이다", self.prompt())
        self.assertIn("전문", self.prompt())
        self.assertRegex(reply, r"전문 [\d,]+자 전부를 읽고 답했습니다")
        reply = self.state.process(f"/ask-knowledge {did} :: 클록 주파수는?")["response"]
        self.assertNotIn("전부를 읽고", reply)
        self.assertEqual(self.summarizer.calls, [])

    def test_summarize_into_a_word_file_uses_the_whole_document(self):
        doc = self.core.knowledge.import_bytes(long_text().encode(), "긴책.txt", source_path="대화 첨부",
                                               scope=f"draft:{self.sid}")["id"]
        reply = self.state.process("이 문서 요약해서 워드로 만들어줘", documents=[doc])["response"]
        self.assertTrue(self.summarizer.calls)
        self.assertIn("조각별로 먼저 요약한 것", self.prompt())
        self.assertIn("[이번 답은 그대로 워드(DOCX) 문서 파일이 된다]", self.prompt())
        self.assertIn("](alex-document:alex-document-", reply)


if __name__ == "__main__":
    unittest.main()
