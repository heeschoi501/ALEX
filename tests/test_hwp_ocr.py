"""한글(HWP·HWPX)과 스캔 PDF 글자 인식(ADR-109, 2026-10-22).

- HWP 5.x: 문서 환경의 olefile 일꾼이 본문 스트림만 읽는다(배포용 문서는 잠긴 본문을 푼다). HWPX: Core 가 zip + XML 로.
- 스캔 PDF: 글자가 거의 없는 쪽만 OCR 일꾼(RapidOCR 한국어)이 다시 읽고 '[스캔 쪽 — 글자 인식(OCR)…]' 표시를 단다.
시험 파일은 tests/fixtures(hwp/README.md 에 출처·라이선스). 실제 일꾼 환경이 없으면 그 시험은 건너뛴다."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from jarvis.knowledge import OCR_MARK, KnowledgeError, KnowledgeStore, import_notice, ocr_pages, office_pages
from jarvis.knowledge import _venv_python

FIXTURES = Path(__file__).with_name("fixtures")
HAS_DOCUMENTS = _venv_python("JARVIS_PDF_PYTHON", "documents").is_file()
HAS_OCR = _venv_python("JARVIS_OCR_PYTHON", "ocr").is_file()


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(Path(self.temp.name))

    def text(self, record) -> str:
        return "\n".join(text for _, text in self.store.document_pages(record["id"]))


class HwpxTests(unittest.TestCase):
    def test_paragraphs_and_order(self):
        pages = office_pages((FIXTURES / "hwp" / "test.hwpx").read_bytes(), ".hwpx")
        text = "\n".join(p["text"] for p in pages)
        self.assertTrue(text.startswith("개요\n개요 내용입니다"))
        self.assertIn("문서의 URL 링크 정보는 바로가기 위치입니다.", text)

    def test_page_breaks_tabs_and_broken_files(self):
        import io, zipfile
        hp = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
        section = (f'<hs:sec xmlns:hs="x" {hp}><hp:p><hp:run><hp:t>첫 쪽<hp:tab/>칸</hp:t></hp:run></hp:p>'
                   f'<hp:p pageBreak="1"><hp:run><hp:t>둘째 쪽</hp:t></hp:run></hp:p></hs:sec>')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Contents/section0.xml", section)
        self.assertEqual(office_pages(buffer.getvalue(), ".hwpx"), [{"page": 1, "text": "첫 쪽\t칸"}, {"page": 2, "text": "둘째 쪽"}])
        with self.assertRaisesRegex(KnowledgeError, "본문을 찾지 못했습니다"):
            empty = io.BytesIO()
            with zipfile.ZipFile(empty, "w") as archive:
                archive.writestr("mimetype", "application/hwp+zip")
            office_pages(empty.getvalue(), ".hwpx")
        bomb = section.replace("<hs:sec", '<!DOCTYPE x [<!ENTITY a "aaaa">]><hs:sec')
        with self.assertRaisesRegex(KnowledgeError, "올바르지 않습니다"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("Contents/section0.xml", bomb)
            office_pages(buffer.getvalue(), ".hwpx")


@unittest.skipUnless(HAS_DOCUMENTS, "문서 환경(tools/documents/venv) 없음")
class HwpTests(StoreTest):
    def test_plain_hwp(self):
        record = self.store.import_bytes((FIXTURES / "hwp" / "hello_world.hwp").read_bytes(), "hello.hwp")
        self.assertEqual(self.text(record), "Hello World!")

    def test_tables_come_out_in_order(self):
        text = self.text(self.store.import_bytes((FIXTURES / "hwp" / "work_report.hwp").read_bytes(), "주간보고.hwp"))
        self.assertTrue(text.startswith("주간업무보고서\n부서명: 부서명"))
        self.assertLess(text.index("금주 진행업무"), text.index("차주 진행업무"))

    def test_distribution_document_is_unlocked(self):
        record = self.store.import_bytes((FIXTURES / "hwp" / "spec.hwp").read_bytes(), "한글문서파일형식.hwp")
        text = self.text(record)
        self.assertIn("Hwp Document File Formats 5.0", text)
        self.assertGreater(record["pages"], 10)
        self.assertGreater(record["chars"], 50_000)

    def test_not_hwp5_is_explained(self):
        with self.assertRaisesRegex(KnowledgeError, "옛 HWP"):
            self.store.import_bytes(b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05", "old.hwp")
        broken = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 600
        with self.assertRaises(KnowledgeError):
            self.store.import_bytes(broken, "broken.hwp")


class OcrFallbackTests(StoreTest):
    def test_no_ocr_environment_keeps_the_old_behaviour(self):
        with patch.dict(os.environ, {"JARVIS_OCR_PYTHON": str(Path(self.temp.name) / "none.exe")}):
            self.assertEqual(ocr_pages(Path("x.pdf"), [1]), {})
            with patch("jarvis.knowledge.pdf_pages", return_value=[{"page": 1, "text": "본문이 충분히 긴 첫 쪽입니다. 스캔이 아닙니다."},
                                                                  {"page": 2, "text": ""}]):
                record = self.store.import_bytes(b"%PDF-1.4 synthetic", "mixed.pdf")
        self.assertEqual(record["ocr_pages"], 0)
        self.assertIn("본문 없는 페이지: [2]", import_notice(record))

    def test_ocr_text_goes_only_to_scanned_pages_and_is_marked(self):
        calls = []

        def fake_ocr(path, numbers):
            calls.append(numbers)
            return {2: "스캔으로 읽은 글자 — 클록 100 MHz"}
        with patch("jarvis.knowledge.pdf_pages", return_value=[{"page": 1, "text": "본문이 충분히 긴 첫 쪽입니다. 스캔이 아닙니다."},
                                                              {"page": 2, "text": " "}]), \
                patch("jarvis.knowledge.ocr_pages", side_effect=fake_ocr):
            record = self.store.import_bytes(b"%PDF-1.4 synthetic2", "mixed.pdf")
        self.assertEqual(calls, [[2]])
        pages = dict(self.store.document_pages(record["id"]))
        self.assertEqual(pages[2], f"{OCR_MARK}\n스캔으로 읽은 글자 — 클록 100 MHz")
        self.assertNotIn(OCR_MARK, pages[1])
        self.assertIn("스캔 쪽 1개는 글자 인식(OCR)으로", import_notice(record))


@unittest.skipUnless(HAS_DOCUMENTS and HAS_OCR, "문서·OCR 환경 없음")
class RealOcrTests(StoreTest):
    def test_scanned_korean_page_is_read(self):
        record = self.store.import_bytes((FIXTURES / "scan-ko.pdf").read_bytes(), "스캔.pdf")
        text = self.text(record)
        self.assertEqual(record["ocr_pages"], 1)
        self.assertTrue(text.startswith(OCR_MARK))
        for words in ("반도체 8대 공정", "감광액(PR)", "Formal Verification", "100 MHz"):
            self.assertIn(words.replace(" ", ""), text.replace(" ", ""))
        self.assertTrue(self.store.search("감광액 마스크"))


if __name__ == "__main__":
    unittest.main()
