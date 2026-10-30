"""고친 직후 오류 알려 주기(jarvis/harness/diagnostics.py, ADR-112) — ruff·yosys 결과 읽기, JSON, 20개 상한(2026-10-26 보강).

ruff·yosys 가 없는 PC 에서도 돌게 가짜 도구(출력만 흉내 내는 스크립트)를 쓴다. 이 PC 에 실제 ruff 가 있으면 실제로도 본다."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from jarvis.harness import diagnostics

FAKE_RUFF = '''import json, sys
print(json.dumps([{"code": "F821", "message": "Undefined name `nope`", "location": {"row": 3, "column": 7}},
                  {"code": "F821", "message": "no location"}]))
'''
FAKE_YOSYS = '''print("bad.v:2: ERROR: syntax error, unexpected TOK_ENDMODULE")
print("ERROR: Parser error in line bad.v:5: unexpected token")
print("ERROR: something without a line")
print("warning: not an error")
'''


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        saved = dict(diagnostics.TOOLS)
        self.addCleanup(lambda: (diagnostics.TOOLS.clear(), diagnostics.TOOLS.update(saved)))

    def tool(self, name: str, body: str) -> Path:
        script = self.root / f"fake_{name}"
        script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        script.chmod(0o755)
        diagnostics.set_tool(name, script)
        return script

    def put(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_json_errors_point_at_the_line(self):
        out = diagnostics.diagnose(self.put("a.json", '{\n  "a": 1\n  "b": 2\n}\n'))   # 쉼표 빠짐 — 3.11·3.13 같은 위치
        self.assertIn("LSP errors detected in this file, please fix:", out)
        self.assertIn("ERROR [3:3]", out)
        self.assertEqual(diagnostics.diagnose(self.put("b.json", '{"a": 1}')), "")

    def test_only_twenty_errors_are_listed(self):
        out = diagnostics._report(Path("x.py"), [(n, 1, "bad") for n in range(1, 26)])
        self.assertEqual(out.count("ERROR ["), 20)
        self.assertIn("... and 5 more", out)
        self.assertEqual(diagnostics._report(Path("x.py"), []), "")

    def test_missing_tools_skip_only_that_check(self):
        diagnostics.set_tool("ruff", self.root / "no-ruff")
        diagnostics.set_tool("yosys", self.root / "no-yosys")
        self.assertEqual(diagnostics.diagnose(self.put("ok.py", "x = undefined\n")), "")
        self.assertEqual(diagnostics.diagnose(self.put("ok.v", "module m; endmodule\n")), "")
        self.assertIn("ERROR [1:", diagnostics.diagnose(self.put("bad.py", "def f(:\n")))   # 문법은 도구 없이도

    @unittest.skipIf(os.name == "nt", "가짜 도구는 실행 권한이 있는 스크립트로 흉내 낸다")
    def test_ruff_json_is_read_and_rows_without_a_location_are_dropped(self):
        self.tool("ruff", FAKE_RUFF)
        self.assertEqual(diagnostics.python_errors(self.put("a.py", "x = 1\n"), "x = 1\n"),
                         [(3, 7, "Undefined name `nope` (F821)")])
        self.tool("ruff", "print('ruff crashed')")
        self.assertEqual(diagnostics.python_errors(self.put("b.py", "x = 1\n"), "x = 1\n"), [])
        self.tool("ruff", "print('[not json')")
        self.assertEqual(diagnostics.python_errors(self.put("c.py", "x = 1\n"), "x = 1\n"), [])

    @unittest.skipIf(os.name == "nt", "가짜 도구는 실행 권한이 있는 스크립트로 흉내 낸다")
    def test_both_yosys_error_shapes_are_read(self):
        self.tool("yosys", FAKE_YOSYS)
        self.assertEqual(diagnostics.verilog_errors(self.put("bad.v", "module m;\n")),
                         [(2, 1, "syntax error, unexpected TOK_ENDMODULE"), (5, 1, "unexpected token"),
                          (1, 1, "something without a line")])

    @unittest.skipUnless(shutil.which("ruff") or diagnostics.TOOLS["ruff"].is_file(), "실제 ruff 없음")
    def test_real_ruff_reports_an_undefined_name_but_not_an_unused_import(self):
        if not diagnostics.TOOLS["ruff"].is_file():
            diagnostics.set_tool("ruff", Path(shutil.which("ruff")))
        out = diagnostics.diagnose(self.put("u.py", "import os\n\ny = missing_name + 1\n"))
        self.assertIn("ERROR [3:5] Undefined name `missing_name` (F821)", out)
        self.assertNotIn("F401", out)   # 안 쓴 import 는 경고라 알리지 않는다
        self.assertEqual(out.count("ERROR ["), 1)


if __name__ == "__main__":
    unittest.main()
