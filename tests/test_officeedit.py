"""워드·엑셀·파워포인트 고치기 엔진(2026-10-23, jarvis/officeedit.py) — 서식은 두고 글자만. ALEX 가 만든 문서로 열기·고치기·다시 열기."""
import io
import unittest
import zipfile

from jarvis.documents import _docx, _pptx, _xlsx, parse_markdown
from jarvis.officeedit import OfficeDocument, OfficeEditError, parse_edits, summary

MD = "# 보고서\n\n첫 문단입니다.\n\n둘째 문단입니다.\n\n| 이름 | 점수 |\n| --- | --- |\n| 가 | 1 |\n"


class OfficeEditTests(unittest.TestCase):
    def test_local_text_correction_keeps_other_runs_tabs_breaks_and_package_parts(self):
        from jarvis.officeedit import _set_paragraph_text
        xml = ('<w:p><w:r><w:t>First </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>bold</w:t></w:r>'
               '<w:r><w:tab/><w:t>old</w:t><w:br/><w:t>end</w:t></w:r></w:p>')
        fixed = _set_paragraph_text(xml, 'w', 'First bold\tnew\nend')
        self.assertIn('<w:rPr><w:b/></w:rPr><w:t>bold</w:t>', fixed)
        self.assertIn('<w:tab/>', fixed)
        self.assertIn('<w:br/>', fixed)
        self.assertIn('new', fixed)

    def test_invalid_cell_and_external_formula_are_rejected(self):
        for ref, value in [('A0', '1'), ('XFE1', '1'), ('A1048577', '1'), ('A1', '=WEBSERVICE("https://example.com")')]:
            document = OfficeDocument(_xlsx('표', parse_markdown(MD)), '.xlsx')
            sheet = next(iter(document.sheets))
            with self.subTest(ref=ref, value=value), self.assertRaises(OfficeEditError):
                document.apply([{'id': sheet + '!' + ref, 'text': value}])

    def test_long_paragraph_is_shown_whole_or_omitted_never_silently_clipped(self):
        body = 'x' * 2400 + ' IMPORTANT TAIL'
        document = OfficeDocument(_docx('제목', parse_markdown(body)), '.docx')
        outline, cut = document.outline(limit=6000)
        self.assertIn('IMPORTANT TAIL', outline)
        self.assertFalse(cut)
        outline, cut = document.outline(limit=1000)
        self.assertTrue(cut)
        self.assertNotIn('xxxx', outline)
    def test_docx_replace_insert_delete_keeps_a_valid_file(self):
        document = OfficeDocument(_docx("보고서", parse_markdown(MD)), ".docx")
        outline, cut = document.outline()
        self.assertFalse(cut)
        ids = {item.text: item.id for item in document.items}
        first, second = ids["첫 문단입니다."], ids["둘째 문단입니다."]
        data, changes = document.apply([{"id": first, "text": "고친 첫 문단 & <기호>"},
                                        {"id": first, "insert_after": "새로 넣은 문단"},
                                        {"id": second, "delete": True}, {"id": "p999", "text": "없는 곳"}])
        again = OfficeDocument(data, ".docx")
        texts = [item.text for item in again.items]
        self.assertIn("고친 첫 문단 & <기호>", texts)
        self.assertEqual(texts[texts.index("고친 첫 문단 & <기호>") + 1], "새로 넣은 문단")
        self.assertNotIn("둘째 문단입니다.", texts)
        self.assertEqual(len(changes), 3)  # 없는 id 는 건너뜀
        self.assertIn("→ 고친 첫 문단", summary(changes))
        self.assertTrue(any(item.note == "표" for item in again.items))
        zipfile.ZipFile(io.BytesIO(data)).testzip()

    def test_xlsx_cells_text_number_formula_and_new_cell(self):
        document = OfficeDocument(_xlsx("표", parse_markdown(MD)), ".xlsx")
        sheet = next(iter(document.sheets))
        data, changes = document.apply([{"id": f"{sheet}!B3", "text": "99"}, {"id": f"{sheet}!C3", "text": "=B3*2"},
                                        {"id": f"{sheet}!A9", "text": "새 칸"}, {"id": "없는시트!A1", "text": "x"}])
        again = OfficeDocument(data, ".xlsx")
        cells = {item.id: item.text for item in again.items}
        self.assertEqual(cells[f"{sheet}!B3"], "99")
        self.assertTrue(cells[f"{sheet}!C3"].startswith("=B3*2"))
        self.assertEqual(cells[f"{sheet}!A9"], "새 칸")
        self.assertEqual(len(changes), 3)
        self.assertIn("fullCalcOnLoad", zipfile.ZipFile(io.BytesIO(data)).read("xl/workbook.xml").decode())

    def test_pptx_slide_paragraph_and_bad_input(self):
        document = OfficeDocument(_pptx("발표", parse_markdown(MD)), ".pptx")
        target = next(item for item in document.items if item.text.strip())
        data, _ = document.apply([{"id": target.id, "text": "바뀐 슬라이드 글"}])
        self.assertIn("바뀐 슬라이드 글", [item.text for item in OfficeDocument(data, ".pptx").items])
        with self.assertRaises(OfficeEditError):
            OfficeDocument(b"not a zip", ".docx")
        with self.assertRaises(OfficeEditError):
            OfficeDocument(b"", ".hwp")
        self.assertEqual(parse_edits('설명 [{"id": "p1", "text": "a"}] 끝'), [{"id": "p1", "text": "a"}])
        with self.assertRaises(OfficeEditError):
            parse_edits("고칠 곳이 없습니다")


if __name__ == "__main__":
    unittest.main()
