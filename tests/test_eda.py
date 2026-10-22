"""EDA 실행기(/eda, ADR-103). 이 PC 에 oss-cad-suite(C:\\oss-cad-suite)가 있으면 실제 yosys·sby·nextpnr 로 작은 설계를 돌린다."""
import json
from pathlib import Path
import tempfile
import unittest

from jarvis.app import handle_command
from jarvis.eda import formal, fpga, parse_stat, synth
from jarvis.rtl_loop import RtlProject
from tests.test_memory_search import make_core

TOOLS = Path(r"C:\oss-cad-suite")
HAS_TOOLS = (TOOLS / "bin" / "yosys.exe").is_file()
COUNTER = """module counter (input clk, input rst, input en, output reg [7:0] q);
  always @(posedge clk) begin
    if (rst) q <= 8'd0;
    else if (en) q <= q + 8'd1;
  end
`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;
  always @(posedge clk) if (past_valid && $past(rst)) assert (q == 0);
  always @(posedge clk) if (past_valid && !$past(rst) && !$past(en)) assert (q == $past(q));
`endif
endmodule
"""
SBY = "[options]\nmode prove\n\n[engines]\nsmtbmc\n\n[script]\nread -formal counter.v\nprep -top counter\n\n[files]\ncounter.v\n"
STAT = """4. Printing statistics.

=== counter ===

        +----------Local Count, excluding submodules.
        | 
       12 wires
       22 cells
        5   $_AND_
        8   $_SDFFE_PP0P_
        6   $_XOR_

End of script.
"""


def make_project(root: Path, code: str = COUNTER, top: str = "counter", sby: str | None = SBY) -> Path:
    folder = root / "counter"
    folder.mkdir()
    (folder / "spec.md").write_text("8비트 카운터", encoding="utf-8")
    (folder / f"{top}.v").write_text(code, encoding="utf-8")
    (folder / "rtl.json").write_text(json.dumps({"top": top, "file": f"{top}.v", "test": ["{python}", "x.py"]}),
                                     encoding="utf-8")
    if sby:
        (folder / "counter.sby").write_text(sby, encoding="utf-8")
    return folder


class ParseTests(unittest.TestCase):
    def test_stat(self):
        self.assertEqual(parse_stat(STAT), (22, [("$_AND_", 5), ("$_SDFFE_PP0P_", 8), ("$_XOR_", 6)]))
        self.assertEqual(parse_stat("nothing"), (-1, []))


class CommandTests(unittest.TestCase):
    def test_usage_and_root(self):
        with tempfile.TemporaryDirectory() as temp:
            core = make_core(Path(temp))
            core.rtl_root, core.rtl_tools_dir = Path(temp), TOOLS
            sid = core.get_or_create_session(False)
            self.assertIn("사용법", handle_command(core, sid, "/eda")[0])
            self.assertIn("사용법", handle_command(core, sid, "/eda counter timing")[0])
            self.assertIn("안에 있어야", handle_command(core, sid, "/eda ../.. synth")[0])
            core.permission_mode = "read"
            from jarvis.permissions import READ_ONLY_BLOCKED_COMMANDS
            self.assertIn("/eda", READ_ONLY_BLOCKED_COMMANDS)


@unittest.skipUnless(HAS_TOOLS, "oss-cad-suite 가 없는 기기")
class RealToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_synth_counts_cells_and_flags_latches(self):
        result = synth(RtlProject.load(make_project(self.root)), TOOLS)
        self.assertTrue(result.ok)
        self.assertTrue(result.lines[0].startswith("셀 22개"), result.lines)
        latch = "module latchy(input en, input d, output reg q); always @* if (en) q = d; endmodule\n"
        other = Path(tempfile.mkdtemp(dir=self.root))
        result = synth(RtlProject.load(make_project(other, latch, "latchy", None)), TOOLS)
        self.assertFalse(result.ok)
        self.assertIn("래치", result.lines[-1])

    def test_formal_proves_and_finds_a_counterexample(self):
        project = RtlProject.load(make_project(self.root))
        self.assertEqual(formal(project, TOOLS).lines[0].split(" — ")[0], "counter.sby: PASS")
        broken = COUNTER.replace("else if (en) q", "else q")  # en 이 0 이어도 센다 — 두 번째 성질이 깨진다
        project.target.write_text(broken, encoding="utf-8")
        result = formal(project, TOOLS)
        self.assertFalse(result.ok)
        self.assertTrue(result.lines[0].startswith("counter.sby: FAIL"), result.lines)

    def test_missing_sby_explains_what_to_add(self):
        result = formal(RtlProject.load(make_project(self.root, sby=None)), TOOLS)
        self.assertFalse(result.ok)
        self.assertIn(".sby", result.lines[0])

    def test_fpga_reports_fmax_and_usage(self):
        result = fpga(RtlProject.load(make_project(self.root)), TOOLS)
        self.assertTrue(result.ok, result.lines)
        self.assertRegex(result.lines[0], r"clk 최고 \d+MHz\(목표 100\)")
        self.assertIn("FF 8/", result.lines[1])
        self.assertTrue((result.folder / "pnr.json").is_file())


if __name__ == "__main__":
    unittest.main()
