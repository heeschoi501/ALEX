"""대화에 문서 올리기(ADR-096, 2026-10-18 사용자 "파일 올리는 게 안 되더라, 그림만 되고 — 수정 필요").

클립으로 올린 PDF·워드·엑셀·파워포인트·텍스트·코드를 그 대화에서만 참고한다. 보내기 전에는 draft, 보내면 그 대화의 자료.
짧은 문서는 전문, 긴 문서는 질문과 관련된 부분만(그렇다고 밝힌다). 대화를 지우면 그 대화에만 올린 문서도 지운다."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from jarvis import accounts
from jarvis.knowledge import KnowledgeError, KnowledgeStore, office_pages
from jarvis.web import Viewer
from tests.test_web import make_state

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def office(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def docx(*paragraphs: str, table: list[list[str]] | None = None, page_break_after: int | None = None) -> bytes:
    body = []
    for n, text in enumerate(paragraphs):
        body.append(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>")
        if page_break_after == n:
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
                       for row in table)
        body.append(f"<w:tbl>{rows}</w:tbl>")
    return office({"word/document.xml": f"<w:document {W}><w:body>{''.join(body)}</w:body></w:document>"})


def xlsx() -> bytes:
    s = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    return office({
        "xl/workbook.xml": f'<workbook {s} {r}><sheets><sheet name="매출" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f"<sst {s}><si><t>지역</t></si><si><t>서울</t></si></sst>",
        "xl/worksheets/sheet1.xml": f'<worksheet {s}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                                    f'<c r="B1" t="inlineStr"><is><t>금액</t></is></c></row><row r="2"><c r="A2" t="s"><v>1</v></c>'
                                    f'<c r="B2"><v>1200</v></c></row></sheetData></worksheet>',
    })


def pptx() -> bytes:
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    slide = lambda text: f"<p:sld xmlns:p=\"x\" {a}><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>"
    return office({"ppt/slides/slide2.xml": slide("두 번째 장"), "ppt/slides/slide10.xml": slide("열 번째 장"),
                   "ppt/slides/slide1.xml": slide("표지")})


class OfficeReadingTests(unittest.TestCase):
    def test_word_paragraphs_tables_and_pages(self):
        pages = office_pages(docx("첫 문단", "둘째 문단", table=[["이름", "값"], ["전압", "1.2V"]], page_break_after=0), ".docx")
        self.assertEqual(pages[0], {"page": 1, "text": "첫 문단"})
        self.assertIn("둘째 문단", pages[1]["text"])
        self.assertIn("전압 | 1.2V", pages[1]["text"])

    def test_excel_sheets_rows_and_shared_strings(self):
        text = office_pages(xlsx(), ".xlsx")[0]["text"]
        self.assertIn("[시트: 매출]", text)
        self.assertIn("[행 1] 지역 | 금액", text)
        self.assertIn("[행 2] 서울 | 1200", text)

    def test_powerpoint_slides_in_number_order(self):
        pages = office_pages(pptx(), ".pptx")
        self.assertEqual([p["page"] for p in pages], [1, 2, 10])
        self.assertEqual(pages[0]["text"], "표지")

    def test_broken_or_hostile_files_are_refused(self):
        with self.assertRaises(KnowledgeError):
            office_pages(b"not a zip", ".docx")
        entity = f'<!DOCTYPE x [<!ENTITY a "aaaa">]><w:document {W}><w:body/></w:document>'
        with self.assertRaises(KnowledgeError):
            office_pages(office({"word/document.xml": entity}), ".docx")


class TextReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = KnowledgeStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def text_of(self, record):
        return "\n".join(text for _, text in self.store.document_pages(record["id"]))

    def test_korean_windows_encoding_html_and_notebooks(self):
        self.assertIn("가나다", self.text_of(self.store.import_bytes("가나다,1\n".encode("cp949"), "표.csv")))
        page = self.text_of(self.store.import_bytes(b"<html><script>x()</script><p>Hello &amp; bye</p></html>", "a.html"))
        self.assertIn("Hello & bye", page)
        self.assertNotIn("x()", page)
        notebook = b'{"cells": [{"cell_type": "code", "source": ["print(1)"], "outputs": []}]}'
        self.assertIn("print(1)", self.text_of(self.store.import_bytes(notebook, "n.ipynb")))

    def test_long_text_comes_back_whole_from_overlapping_chunks(self):
        text = "".join(f"문장{n:05d}. " for n in range(3000))
        self.assertEqual(self.text_of(self.store.import_bytes(text.encode(), "long.txt")), text.strip())

    def test_old_office_and_hwp_get_a_clear_message(self):
        for name in ("old.doc", "old.xls"):
            with self.subTest(name=name), self.assertRaisesRegex(KnowledgeError, "PDF 로 저장"):
                self.store.import_bytes(b"x", name)
        with self.assertRaisesRegex(KnowledgeError, "옛 HWP"):  # 한글 5.x 는 읽는다(ADR-109) — 옛 형식만 안내
            self.store.import_bytes(b"x", "보고서.hwp")


class ChatDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = make_state(Path(self.temp.name))
        self.core = self.state.core
        self.sid = self.state.session_id
        self.knowledge = self.core.knowledge

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, data: bytes, name: str, sid: str | None = None) -> str:
        return self.knowledge.import_bytes(data, name, source_path="대화 첨부", scope=f"draft:{sid or self.sid}")["id"]

    def test_sent_document_is_read_with_the_question_and_kept_for_the_conversation(self):
        doc = self.upload(docx("회의 결론: 출시는 12월 3일로 미룬다."), "회의록.docx")
        reply = self.state.process("결론이 뭐야?", documents=[doc])["response"]
        self.assertIn("출시는 12월 3일로 미룬다", reply)  # 가짜 모델은 받은 마지막 메시지를 그대로 돌려준다
        self.assertIn("전문", reply)
        saved = self.core.storage.message_page(self.sid)["messages"][-2]["content"]
        self.assertIn("[첨부 문서: 회의록.docx · 1쪽", saved)
        self.assertNotIn("12월 3일", saved)  # 대화 기록에는 표시만, 본문은 넣지 않는다
        self.assertEqual([d["id"] for d in self.knowledge.documents(f"session:{self.sid}")], [doc])
        self.assertEqual(self.knowledge.documents(f"draft:{self.sid}"), [])
        self.assertIn("12월 3일", self.state.process("다시 말해 줘")["response"])  # 다음 질문에서도 참고

    def test_documents_stay_in_their_own_conversation(self):
        self.state.process("요약해 줘", documents=[self.upload(b"secret plan alpha", "a.txt")])
        other = self.state.process("/new")["session_id"]
        self.assertNotIn("secret plan alpha", self.state.process("요약해 줘", session_id=other)["response"])

    def test_long_documents_send_only_related_parts_and_say_so(self):
        filler = "".join(f"일반 문장 {n}. " for n in range(6000))
        doc = self.upload((filler + " 전압 강하 원인은 배선 저항이다. " + filler).encode(), "긴문서.txt")
        reply = self.state.process("전압 강하 원인이 뭐야?", documents=[doc])["response"]
        self.assertIn("일부만", reply)
        self.assertIn("배선 저항", reply)
        self.assertLess(len(reply), 30_000)

    def test_discarding_before_sending_deletes_it(self):
        doc = self.upload(b"draft text", "d.txt")
        self.state.discard_document(self.state.owner_viewer(), self.sid, doc)
        self.assertEqual(self.knowledge.documents(f"draft:{self.sid}"), [])
        with self.assertRaises(KnowledgeError):
            self.state.process("요약", documents=[doc])

    def test_someone_else_cannot_use_my_document(self):
        doc = self.upload(b"owner only", "o.txt")
        member = Viewer(self.core.storage.create_user("민수", "minsu", accounts.hash_password("member-pass-1")), remote=False)
        their = self.core.storage.create_session(member.id)
        with self.assertRaises(KnowledgeError):
            self.state.process("요약", viewer=member, session_id=their, documents=[doc])
        with self.assertRaises(ValueError):
            self.state.discard_document(member, self.sid, doc)

    def test_deleting_the_conversation_deletes_its_documents(self):
        self.state.process("요약", documents=[self.upload(b"to be removed", "r.txt")])
        self.upload(b"unsent draft", "u.txt")
        self.state.delete_session(self.sid, True)
        self.assertEqual(self.knowledge.documents(f"session:{self.sid}"), [])
        self.assertEqual(self.knowledge.documents(f"draft:{self.sid}"), [])

    def test_shared_knowledge_file_is_not_deleted_with_the_conversation(self):
        self.knowledge.import_bytes(b"shared manual", "manual.txt")  # 모두가 쓰는 학습 자료
        self.state.process("요약", documents=[self.upload(b"shared manual", "manual.txt")])
        self.state.delete_session(self.sid, True)
        self.assertEqual([d["title"] for d in self.knowledge.documents("")], ["manual.txt"])

    def test_conversation_title_leaves_out_the_attachment_lines(self):
        self.state.process("결론이 뭐야?", documents=[self.upload(b"x", "x.txt")])
        self.assertEqual(self.core.storage.session_title(self.sid), "결론이 뭐야?")

    def test_commands_cannot_carry_documents(self):
        with self.assertRaises(ValueError):
            self.state.process("/help", documents=[self.upload(b"x", "x.txt")])


if __name__ == "__main__":
    unittest.main()
