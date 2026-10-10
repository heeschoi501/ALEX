"""Document package, content preservation, command and download boundary tests."""
from pathlib import Path
import posixpath
import tempfile
import subprocess
import unittest
from unittest.mock import patch, Mock
from urllib.parse import quote
import urllib.error
import urllib.request
import threading
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from jarvis.app import handle_command
from jarvis.documents import (DocumentError, export_document, download_path,
                              parse_markdown, MAX_TEXT, FORMATS, NAME_RE, _html)
from tests.test_web import make_state
from jarvis.web import make_server


SAMPLE = """# 통계 시험

다음 물음에 답하세요. 기호: < & >, **굵은 글씨**, `코드`.

| 번호 | 문제 | 정답 | 해설 |
| --- | --- | --- | --- |
| 01 | =2+2 | 4 | 2와 2의 합 |
| 02 | 분산의 의미는? | 흩어짐 | 자료의 흩어진 정도 |

## 서술형
1. 평균과 중앙값을 설명하세요.
2. 표본을 설명하세요.

```python
if x < 3:
    print("한글 & 숫자")
```
"""


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def package(self, format, text=SAMPLE):
        path = export_document(self.root, format, text, "통계 시험지")
        self.assertRegex(path.name, NAME_RE)
        with ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            xml = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()}
            names = set(xml)
            # Every relationship resolves to a real part; no external relationships/macros.
            for name, tree in xml.items():
                if not name.endswith(".rels"):
                    continue
                parent = "" if name == "_rels/.rels" else name.split("/_rels/")[0]
                for rel in tree:
                    self.assertNotIn("TargetMode", rel.attrib)
                    target = posixpath.normpath(posixpath.join(parent, rel.attrib["Target"]))
                    self.assertIn(target, names)
            overrides = xml["[Content_Types].xml"]
            for item in overrides:
                if "PartName" in item.attrib:
                    self.assertIn(item.attrib["PartName"].lstrip("/"), names)
            return path, xml

    def test_docx_korean_tables_headings_and_code_preserved(self):
        _, xml = self.package("docx")
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tree = xml["word/document.xml"]
        self.assertEqual(len(tree.findall(".//w:tbl", ns)), 1)
        texts = [node.text for node in tree.findall(".//w:t", ns)]
        self.assertIn("흩어짐", texts)
        self.assertIn('    print("한글 & 숫자")', texts)
        self.assertIn("통계 시험지", texts)

    def test_excel_strings_keep_formulas_and_leading_zero_as_data(self):
        _, xml = self.package("xlsx")
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet = xml["xl/worksheets/sheet1.xml"]
        self.assertEqual(sheet.find('.//s:c[@r="A2"]/s:is/s:t', ns).text, "01")
        self.assertEqual(sheet.find('.//s:c[@r="B2"]/s:is/s:t', ns).text, "=2+2")
        self.assertFalse(sheet.findall(".//s:f", ns))
        self.assertIsNotNone(sheet.find("s:autoFilter", ns))
        self.assertIn("xl/worksheets/sheet2.xml", xml)

    def test_ppt_slide_relationships_and_all_paginated_text(self):
        text = "# 첫 절\n## 다음 절\n" + '\n'.join(f"문항 {i:03d}: 설명하세요" for i in range(75))
        _, xml = self.package("pptx", text)
        ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        slides = [tree for name, tree in xml.items() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        self.assertGreater(len(slides), 6)
        content = '\n'.join(node.text or "" for tree in slides for node in tree.findall(".//a:t", ns))
        self.assertIn("첫 절", content)
        self.assertIn("다음 절", content)
        for i in range(75):
            self.assertIn(f"문항 {i:03d}", content)

    def test_ppt_long_heading_and_code_not_dropped(self):
        heading = "긴 제목" * 35
        _, xml = self.package("pptx", f"# {heading}\n```\n원문 *중요* <태그>\n```")
        nodes = [n.text or "" for name, tree in xml.items() if name.startswith("ppt/slides/slide") and name.endswith(".xml") for n in tree.iter() if n.tag.endswith("}t")]
        self.assertIn("원문 *중요* <태그>", nodes)
        self.assertIn(heading, ''.join(nodes))

    def test_table_detection_and_escaped_pipes(self):
        blocks = parse_markdown("파이프 a | b는 문장\n\n| 열 | 설명 |\n| --- | --- |\n| 값 | a\\|b |")
        self.assertEqual(blocks[0].kind, "paragraph")
        self.assertEqual(blocks[1].rows[1][1], "a|b")

    def test_multiple_tables_are_separate_excel_sheets(self):
        text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n| C | D |\n| --- | --- |\n| 3 | 4 |"
        _, xml = self.package("xlsx", text)
        self.assertIn("xl/worksheets/sheet2.xml", xml)
        self.assertNotIn("xl/worksheets/sheet3.xml", xml)

    def test_excel_multiline_rows_have_visible_height(self):
        _, xml = self.package("xlsx", "| 문제 | 정답 |\n| --- | --- |\n| " + "긴 문장 " * 40 + " | 1 |")
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        row = xml["xl/worksheets/sheet1.xml"].find('.//s:row[@r="2"]', ns)
        self.assertGreater(float(row.attrib["ht"]), 22)

    def test_empty_invalid_control_over_limit_and_unknown_format(self):
        for format, text in (("xlsx", ""), ("docx", "x\x00y"), ("docx", "x" * (MAX_TEXT + 1)), ("exe", "test")):
            with self.subTest(format=format, length=len(text)), self.assertRaises(DocumentError):
                export_document(self.root, format, text)
        self.assertFalse((self.root / "exports").exists())

    def test_oversize_excel_cell_rejected_without_partial_file(self):
        with self.assertRaises(DocumentError):
            export_document(self.root, "xlsx", "가" * 32768)
        self.assertFalse((self.root / "exports").exists())

    def test_export_does_not_overwrite_existing_files(self):
        one = export_document(self.root, "docx", "첫 파일")
        before = one.read_bytes()
        two = export_document(self.root, "docx", "두 번째 파일")
        self.assertNotEqual(one, two)
        self.assertEqual(one.read_bytes(), before)

    def test_filename_collision_does_not_delete_existing_document(self):
        with patch("jarvis.documents.secrets.token_hex", return_value="a" * 16), patch("jarvis.documents.datetime") as clock:
            clock.now.return_value.strftime.return_value = "20261004-080000-"
            path = export_document(self.root, "docx", "원본")
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                export_document(self.root, "docx", "덮어쓰기 시도")
            self.assertEqual(path.read_bytes(), before)

    def test_download_names_restrict_paths_and_non_generated_files(self):
        one = export_document(self.root, "xlsx", SAMPLE)
        self.assertEqual(download_path(self.root, one.name), one)
        for name in ("../jarvis.db", "secret.docx", "x.xlsx", str(one), "..\\" + one.name):
            self.assertIsNone(download_path(self.root, name))

    def test_symlink_export_root_rejected(self):
        outside = self.root / "outside"; outside.mkdir()
        try:
            (self.root / "exports").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("OS does not grant symlink creation")
        with self.assertRaises(DocumentError):
            export_document(self.root, "docx", "내용")
        self.assertFalse(list(outside.iterdir()))

    def test_pdf_without_browser_reports_error(self):
        with patch("jarvis.documents.find_pdf_browser", return_value=None), self.assertRaisesRegex(DocumentError, "Edge"):
            export_document(self.root, "pdf", SAMPLE)
        self.assertFalse((self.root / "exports").exists())

    def test_pdf_html_is_escaped_and_contains_no_active_source(self):
        source = _html("<script>제목</script>", parse_markdown('<img src="https://evil.test/x">\n![그림](https://evil.test/img)'))
        self.assertNotIn('<img ', source)
        self.assertNotIn('<script>', source)
        self.assertIn("Content-Security-Policy", source)
        self.assertIn("&lt;img", source)

    def test_pdf_uses_isolated_profile_gpu_off_and_validates_output(self):
        class Process:
            returncode = 0
            def wait(inner, timeout):
                pass
        def spawn(command, **kwargs):
            self.assertIn("--disable-gpu", command)
            self.assertIn("--disable-software-rasterizer", command)
            self.assertTrue(any(c.startswith("--user-data-dir=") for c in command))
            output = next(c.split("=", 1)[1] for c in command if c.startswith("--print-to-pdf="))
            Path(output).write_bytes(b"%PDF-1.7\nmock\n%%EOF")
            return Process()
        with patch("jarvis.documents.find_pdf_browser", return_value=Path("fake.exe")), patch("jarvis.documents.subprocess.Popen", side_effect=spawn):
            path = export_document(self.root, "pdf", SAMPLE)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))

    def test_pdf_timeout_stops_only_owned_process_tree(self):
        process = Mock(pid=123456)
        process.wait.side_effect = [subprocess.TimeoutExpired("renderer", 60), 0]
        with patch("jarvis.documents.find_pdf_browser", return_value=Path("fake.exe")), patch("jarvis.documents.subprocess.Popen", return_value=process), patch("jarvis.documents.subprocess.run") as kill, self.assertRaisesRegex(DocumentError, "60초"):
            export_document(self.root, "pdf", SAMPLE)
        if __import__("os").name == "nt":
            self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "123456", "/T", "/F"])
        self.assertFalse((self.root / "exports").exists())


class DocumentCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)
        self.storage = self.state.core.storage
        self.sid = self.state.session_id

    def tearDown(self):
        self.temp.cleanup()

    def test_latest_answer_and_pasted_content_use_no_model(self):
        self.storage.add_message(self.sid, "assistant", SAMPLE)
        self.storage.add_message(self.sid, "user", "추가 질문")
        with patch("jarvis.providers.MockLLMProvider.generate", side_effect=AssertionError("model called")):
            output, keep, sid = handle_command(self.state.core, self.sid, "/export xlsx")
            direct, _, _ = handle_command(self.state.core, self.sid, "/document docx 시험 | 직접 쓴 문제")
        self.assertIn("문서 저장 완료", output)
        self.assertIn("alex-document:", output)
        self.assertIn("문서 저장 완료", direct)
        self.assertTrue(keep)
        self.assertEqual(sid, self.sid)

    def test_all_includes_older_pages_in_order_and_last_searches_older_pages(self):
        self.storage.add_message(self.sid, "assistant", "가장 오래된 답변")
        for i in range(205):
            self.storage.add_message(self.sid, "user", f"질문 {i:03d}")
        output = self.state.process("/export docx all 전체 대화")["response"]
        name = output.split("alex-document:")[1].split(")")[0]
        with ZipFile(download_path(self.root, name)) as archive:
            tree = ET.fromstring(archive.read("word/document.xml"))
            text = ''.join(tree.itertext())
            self.assertLess(text.index("질문 000"), text.index("질문 204"))
            self.assertIn("가장 오래된 답변", text)
        self.assertIn("문서 저장 완료", self.state.process("/export docx")["response"])

    def test_missing_answer_and_bad_command_return_error(self):
        for command in ("/export docx", "/export exe", "/export pdf typo", "/document docx 제목", "/document"):
            with self.subTest(command=command):
                self.assertIn("문서 저장 실패", self.state.process(command)["response"])
        self.assertFalse((self.root / "exports").exists())


class DocumentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, name, token=None):
        headers = {"X-Jarvis-Token": token} if token else {}
        return urllib.request.urlopen(urllib.request.Request(self.base + "/api/document?name=" + quote(name), headers=headers), timeout=3)

    def test_download_requires_token_and_returns_document_mime(self):
        path = export_document(self.root, "docx", SAMPLE)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get(path.name)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.get(path.name, "test-token") as response:
            self.assertEqual(response.read(), path.read_bytes())
            self.assertEqual(response.headers["Content-Type"], FORMATS["docx"])
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_traversal_and_unknown_files_are_not_served(self):
        for name in ("../web.db", "..\\web.db", "unknown.xlsx"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get(name, "test-token")
            self.assertEqual(error.exception.code, 404)
            error.exception.close()


if __name__ == "__main__":
    unittest.main()
