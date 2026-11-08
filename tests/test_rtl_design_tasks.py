"""RTL 설계 과제(2026-11-08 사용자: "CPU 간단한거랑 GPU가장 간단한거,그리고 그래픽메모리 간단한거") — 정답 모델·과제 폴더·시험기.

정답은 Python 상태 모델이 정하고, 기준 설계(tests/fixtures/rtl_design)는 시험기가 맞는지만 확인한다. 시뮬레이터(iverilog)가 있는
기기에서는 기준 설계가 모든 시험을 통과하고 일부러 망친 설계는 해당 시험에서 떨어지는지까지 본다.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from jarvis.rtl_loop import RtlProject
from scripts.rtl_design_tasks import TASKS, asm, check, evaluate, expected, prepare, tool_env, vectors

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rtl_design"
SIMULATOR = shutil.which("iverilog", path=tool_env()["PATH"]) and shutil.which("vvp", path=tool_env()["PATH"])


class ModelTests(unittest.TestCase):
    def test_the_cpu_model_runs_a_counting_loop_and_halts(self):
        program = [asm("LDI", 0, 0, 5), asm("LDI", 1, 0, 0), asm("ADD", 1, 0), asm("ADDI", 0, 0, 0xFF), asm("JNZ", imm=2),
                   asm("HALT")]
        rows = [("branch", (1,), program)] + [("branch", (0,), None)] * 20
        pc, regs, zero, halted = expected("cpu8", rows)[-1]
        self.assertEqual((pc, regs & 0xFF, regs >> 8 & 0xFF, zero, halted), (5, 0, 15, 1, 1))   # 5+4+3+2+1, Z from the last ADDI

    def test_the_cpu_model_keeps_z_through_non_alu_instructions_and_clears_memory_on_reset(self):
        rows = [("zero_flag", (1,), [asm("LDI", 0, 0, 1), asm("SUB", 0, 0), asm("LDI", 1, 0, 7), asm("ST", 1, 1, 0x13),
                                     asm("LD", 2, 0, 3)]),
                ("zero_flag", (0,), None), ("zero_flag", (0,), None), ("zero_flag", (0,), None), ("zero_flag", (0,), None),
                ("zero_flag", (0,), None), ("reset_mid", (1,), [asm("LD", 3, 0, 3)]), ("reset_mid", (0,), None)]
        states = expected("cpu8", rows)
        self.assertEqual([s[2] for s in states[2:6]], [1, 1, 1, 1])             # LDI/ST/LD keep Z
        self.assertEqual(states[5][1] >> 16 & 0xFF, 7)                         # ST [0x13] wrote address 3
        self.assertEqual(states[-1][1], 0)                                     # the reset cleared memory too

    def test_the_gpu_model_clips_at_the_screen_edge_and_ignores_empty_and_busy_commands(self):
        rows = [("reset", (1, 0, 0, 0, 0, 0, 0), None), ("clipping", (0, 1, 14, 3, 5, 2, 0x31), None)] + \
               [("clipping", (0, 1, 0, 0, 1, 1, 0x99), None)] * 5
        written = [(we, addr, data) for busy, we, addr, data in expected("gpu_fill", rows) if we]
        self.assertEqual(written, [(1, 3 * 16 + 14, 0x31), (1, 3 * 16 + 15, 0x31), (1, 4 * 16 + 14, 0x31), (1, 4 * 16 + 15, 0x31),
                                   (1, 0, 0x99)])   # start held: the next command only after the edge that ends busy
        empty = expected("gpu_fill", [("reset", (1, 0, 0, 0, 0, 0, 0), None), ("zero_size", (0, 1, 4, 4, 0, 5, 1), None)])
        self.assertEqual(empty[-1][:2], (0, 0))

    def test_the_memory_model_shows_a_same_edge_write_and_wraps_the_scan(self):
        rows = [("reset", (1, 1, 0, 0x5E), None), ("write_first", (0, 1, 1, 0xC1), None), ("write_first", (0, 1, 9, 0x77), None)] + \
               [("scan_order", (0, 0, 0, 0), None)] * 254
        states = expected("vram16", rows)
        self.assertEqual(states[0], (0, 0, 0, 1))          # the write during reset is ignored
        self.assertEqual(states[1], (1, 0, 0xC1, 0))       # write-first at (1, 0)
        self.assertEqual(states[-1], (0, 0, 0, 1))         # (15, 15) wraps to (0, 0) and starts a new frame
        self.assertEqual(states[9], (9, 0, 0x77, 0))

    def test_registered_outputs_must_not_change_before_the_edge(self):
        rows = [("reset", (1, 0, 0, 0), None), ("scan_order", (0, 0, 0, 0), None)]
        good = "PRE 0 x x xx x\nROW 0 0 0 00 1\nPRE 1 0 0 00 1\nROW 1 1 0 00 0\n"
        early = "PRE 0 x x xx x\nROW 0 0 0 00 1\nPRE 1 1 0 00 0\nROW 1 1 0 00 0\n"
        self.assertTrue(all(row["ok"] for row in evaluate("vram16", rows, good).values()))
        report = evaluate("vram16", rows, early)
        self.assertFalse(report["scan_order"]["ok"])
        self.assertIn("before the edge", report["scan_order"]["problems"][0])
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            evaluate("vram16", rows, good.replace("ROW 1", "ROW 2"))


class FolderTests(unittest.TestCase):
    def test_prepare_makes_from_scratch_tasks_that_the_rtl_loop_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            folders = prepare(Path(tmp))
            self.assertEqual([f.name for f in folders], ["cpu8-design", "gpu_fill-design", "vram16-design"])
            for (name, task), folder in zip(TASKS.items(), folders):
                project = RtlProject.load(folder)
                self.assertEqual((project.top, project.expected_tests), (task["top"], tuple(task["groups"])))
                self.assertFalse(project.target.exists())                            # no starting code: design from the spec
                self.assertIn(f"module {task['top']}(", project.spec)
                self.assertEqual((folder / "verify/run_tests.py").read_text(encoding="utf-8"),
                                 (Path(__file__).resolve().parents[1] / "scripts/rtl_design_tasks.py").read_text(encoding="utf-8"))
                self.assertEqual(len({group for group, _, _ in vectors(name, 20261108)}), len(task["groups"]))
            with self.assertRaisesRegex(ValueError, "overwrite"):
                prepare(Path(tmp))


@unittest.skipUnless(SIMULATOR, "iverilog·vvp 가 없다(PC: oss-cad-suite)")
class SimulatorTests(unittest.TestCase):
    BROKEN = {   # a realistic bug in each design and the test that must catch it
        "cpu8": ("for (i = 0; i < 16; i = i + 1) mem[i] <= 8'd0;", "", "reset_mid"),
        "gpu_fill": ("wire [4:0] xlim = (xsum > 6'd16) ? 5'd16 : xsum[4:0];", "wire [4:0] xlim = xsum[4:0];", "clipping"),
        "vram16": ("pixel <= (we && waddr == naddr) ? wdata : (written[naddr] ? mem[naddr] : 8'd0);",
                   "pixel <= written[naddr] ? mem[naddr] : 8'd0;", "write_first"),
    }

    def test_the_reference_designs_pass_and_typical_bugs_are_caught(self):
        for task, (old, new, group) in self.BROKEN.items():
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                passed = check(task, FIXTURES / f"{task}.v", work / "good.json", 20261108, work / "good")
                self.assertTrue(all(row["ok"] for row in passed.values()), json.dumps(passed)[:500])
                source = (FIXTURES / f"{task}.v").read_text(encoding="utf-8")
                self.assertEqual(source.count(old), 1)
                (work / "broken.v").write_text(source.replace(old, new), encoding="utf-8")
                failed = check(task, work / "broken.v", work / "bad.json", 20261108, work / "bad")
                self.assertFalse(failed[group]["ok"])


if __name__ == "__main__":
    unittest.main()
