"""검증 쓰기 모드(/rtl-tests, ADR-103). 가짜 모델(정해진 답) + 이 PC 의 iverilog·yosys(있으면)."""
import json
from pathlib import Path
import tempfile
import unittest

from jarvis.rtl_loop import RtlProject
from jarvis.rtl_tests import TB_FILE, answer_text, describe, run_tb, tb_problem
from tests.test_eda import COUNTER, HAS_TOOLS, TOOLS, make_project
from tests.test_rtl_loop import Scripted

WEAK = """```verilog
module alex_tb;
  reg clk = 0, rst = 1, en = 0; wire [7:0] q; integer errors = 0;
  counter dut(.clk(clk), .rst(rst), .en(en), .q(q));
  always #5 clk = ~clk;
  initial begin
    @(negedge clk); rst = 0; en = 1;
    repeat (3) @(negedge clk);
    if (q !== 8'd3) begin errors = errors + 1; $display("ALEX_TB_FAIL: q=%0d", q); end
    if (errors == 0) $display("ALEX_TB_PASS");
    $finish;
  end
endmodule
```"""
STRONG = """```verilog
module alex_tb;
  reg clk = 0, rst = 1, en = 0; wire [7:0] q; integer errors = 0; integer i; reg [7:0] model;
  counter dut(.clk(clk), .rst(rst), .en(en), .q(q));
  always #5 clk = ~clk;
  task check; begin if (q !== model) begin errors = errors + 1; $display("ALEX_TB_FAIL: 기대=%0d 실제=%0d", model, q); end end endtask
  initial begin
    model = 0; @(negedge clk); check;
    rst = 0;
    for (i = 0; i < 600; i = i + 1) begin
      en = (i % 7 != 3) && (i % 11 != 5);
      @(negedge clk);
      if (rst) model = 0; else if (en) model = model + 1;
      check;
      rst = (i == 300);
    end
    if (errors == 0) $display("ALEX_TB_PASS");
    $finish;
  end
endmodule
```"""


class UnitTests(unittest.TestCase):
    def test_tb_rules(self):
        self.assertIn("$fopen", tb_problem("module alex_tb; initial $fopen(\"x\"); counter d(); endmodule", "counter"))
        self.assertIn("`include", tb_problem("`include \"a.v\"\nmodule alex_tb; counter d(); endmodule", "counter"))
        self.assertIn("alex_tb", tb_problem("module tb; counter d(); endmodule", "counter"))
        self.assertIn("인스턴스", tb_problem("module alex_tb; endmodule", "counter"))
        self.assertEqual(tb_problem("module alex_tb; counter #(.W(8)) dut (.clk(c)); endmodule", "counter"), "")

    def test_describe(self):
        self.assertEqual(describe("mutate -mode inv -module counter -cell $procmux$3 -port S -portbit 0 -wire \\\\en -wirebit 0 "
                                  "-src counter.v:1.45-1.47 -src counter.v:4.14-4.16"), "counter.v 1행: inv(en)")
        self.assertEqual(describe("mutate -mode const0 -module c -cell x -src C:\\\\T\\\\counter.v:4.23-4.31"), "counter.v 4행: const0")


@unittest.skipUnless(HAS_TOOLS, "oss-cad-suite 가 없는 기기")
class RealToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_weak_testbench_is_strengthened_from_the_missed_faults(self):
        project = RtlProject.load(make_project(self.root, sby=None))
        model = Scripted(WEAK, STRONG)
        result = run_tb(model, project, TOOLS, mutants=12, target=0.75)
        self.assertTrue(result.done, answer_text(result, project))
        first, second = result.rounds
        self.assertTrue(first.design_pass)
        self.assertLess(first.killed, first.total)
        self.assertIn("놓친 고장 위치", model.requests[1])
        self.assertIn("counter.v", model.requests[1])
        self.assertGreater(second.killed, first.killed)
        self.assertNotIn("always @(posedge clk)", model.requests[0])     # 설계 코드는 보여 주지 않는다
        self.assertIn("module counter", model.requests[0])               # 포트는 보여 준다
        self.assertEqual(project.target.read_text(encoding="utf-8"), COUNTER)  # 설계 파일은 그대로
        self.assertIn("ALEX_TB_PASS", (project.root / TB_FILE).read_text(encoding="utf-8"))
        self.assertTrue(json.loads((project.root / "rtl-tb-run.json").read_text(encoding="utf-8"))["done"])

    def test_a_failing_design_stops_without_bending_the_testbench(self):
        project = RtlProject.load(make_project(self.root, sby=None))
        project.target.write_text(COUNTER.replace("else if (en) q", "else q"), encoding="utf-8")  # en 무시 버그
        model = Scripted(STRONG, WEAK)
        result = run_tb(model, project, TOOLS, mutants=8)
        self.assertTrue(result.found_bug)
        self.assertEqual(len(model.requests), 1)
        self.assertIn("설계 버그 후보", answer_text(result, project))
        self.assertIn("ALEX_TB_FAIL", answer_text(result, project))

    def test_compile_errors_go_back_to_the_model(self):
        project = RtlProject.load(make_project(self.root, sby=None))
        broken = "```verilog\nmodule alex_tb; counter dut(.clk(1'b0)) endmodule\n```"
        model = Scripted(broken, STRONG)
        result = run_tb(model, project, TOOLS, mutants=8, target=0.5)
        self.assertEqual(result.rounds[0].problem, "컴파일 실패")
        self.assertIn("iverilog 컴파일 오류", model.requests[1])
        self.assertTrue(result.done)


if __name__ == "__main__":
    unittest.main()
