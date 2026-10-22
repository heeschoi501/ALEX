"""RTL 모드(/rtl, ADR-087) — 가짜 모델·가짜 시험기로 순환의 약속을 지킨다. 실제 시뮬레이터·모델은 쓰지 않는다.

1. 시험·린트 결과를 다음 회차 요청에 넣고, 모두 통과하면 멈춘다(회차마다 코드·결과를 남긴다).
2. 모델은 설계 파일 하나만 쓴다. 파일·명령 실행 시스템 태스크가 있으면 시뮬레이션하지 않고 돌려보낸다.
3. 프로젝트 밖 경로·이상한 모듈 이름·rtl_root 밖 폴더는 받지 않는다.
"""
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

from jarvis.app import handle_command
from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.providers import GenerationCancelled
from jarvis.rtl_loop import FORBIDDEN, RtlProject, code_problem, extract_code, run_rtl, run_tests
from jarvis.rtl_repair import apply_repair, code_tokens, repair_messages
from jarvis.storage import Storage

# 가짜 시험기: 설계 파일에 GOOD 이 있으면 2/2, 아니면 1/2 실패. 시험기는 사람이 둔 것이라 그대로 실행된다.
RUNNER = textwrap.dedent("""
    import json, sys
    code = open(sys.argv[1], encoding="utf-8").read()
    good = "GOOD" in code
    report = {"t_basic": {"ok": True, "problems": [], "about": "기본"},
              "t_shift": {"ok": good, "problems": [] if good else ["x4: 기대 f8000001 / 실제 08000001"],
                          "about": "SRAI", "source": "srai x4, x1, 4"}}
    json.dump(report, open(sys.argv[2], "w", encoding="utf-8"))
""")
GOOD = "```verilog\nmodule cpu(input clk); wire GOOD = 1'b1;\nendmodule\n```"
BAD = "설명\n```verilog\nmodule cpu(input clk); // bad\nwire bad = 1'b0;\nendmodule\n```"


def make_project(root: Path, file: str = "cpu.v", top: str = "cpu") -> Path:
    folder = root / "proj"
    (folder / "verify").mkdir(parents=True)
    (folder / "verify" / "runner.py").write_text(RUNNER, encoding="utf-8")
    (folder / "spec.md").write_text("cpu 모듈을 만든다.", encoding="utf-8")
    (folder / "rtl.json").write_text(json.dumps({"top": top, "file": file, "spec": "spec.md",
                                                 "test": ["{python}", "verify/runner.py", "{file}", "{report}"]}),
                                     encoding="utf-8")
    return folder


class Scripted:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, messages):
        self.requests.append(messages[-1].content)
        return self.answers.pop(0)


class RtlLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / "no-tools"  # 린트 도구 없음 → 린트 건너뜀(-1)

    def test_failures_go_back_to_the_model_until_every_test_passes(self):
        project = RtlProject.load(make_project(self.root))
        model = Scripted(BAD, GOOD)
        notes = []
        result = run_rtl(model, project, self.tools, progress=notes.append)
        self.assertTrue(result.verified)
        self.assertEqual([(r.passed, r.total) for r in result.rounds], [(1, 2), (2, 2)])
        second = model.requests[1]
        self.assertIn("[지금 코드]", second)
        self.assertIn("// bad", second)
        self.assertIn("x4: 기대 f8000001 / 실제 08000001", second)
        self.assertIn("srai x4, x1, 4", second)
        self.assertIn("GOOD", project.target.read_text(encoding="utf-8"))
        self.assertTrue((result.run_dir / "round-1.v").is_file())
        self.assertTrue((result.run_dir / "round-2.json").is_file())
        self.assertTrue(json.loads((project.root / "rtl-run.json").read_text(encoding="utf-8"))["verified"])
        self.assertEqual(len(notes), 2)

    def test_system_tasks_are_never_simulated(self):
        project = RtlProject.load(make_project(self.root))
        evil = '```verilog\nmodule cpu(input clk); initial $system("del C:\\\\x"); endmodule\n```'
        model = Scripted(evil, GOOD)
        result = run_rtl(model, project, self.tools)
        self.assertIn("$system", result.rounds[0].problem)
        self.assertFalse((result.run_dir / "round-1.v").exists())
        self.assertIn("시스템 태스크", model.requests[1])
        self.assertTrue(result.verified)
        for task in ("$fopen", "$fwrite", "$readmemh", "$dumpfile", "$value$plusargs"):
            self.assertTrue(FORBIDDEN.search(task), task)
        self.assertIsNone(FORBIDDEN.search("assign y = a >>> b; // $signed 는 괜찮다 $signed(a)"))

    def test_missing_code_block_and_round_limit(self):
        project = RtlProject.load(make_project(self.root))
        model = Scripted("코드 없이 말만", BAD)
        result = run_rtl(model, project, self.tools, max_rounds=2)
        self.assertFalse(result.verified)
        self.assertIn("코드 블록이 없다", result.rounds[0].problem)
        self.assertIn("2회 안에", result.stopped)

    def test_a_cut_off_answer_is_named_and_raw_answers_are_kept(self):
        project = RtlProject.load(make_project(self.root))
        model = Scripted("설명\n```Verilog\nmodule cpu(input clk);\n  // 여기서 끊김", GOOD)
        result = run_rtl(model, project, self.tools)
        self.assertIn("끊겼다", result.rounds[0].problem)
        self.assertIn("끊겼다", model.requests[1])
        self.assertIn("여기서 끊김", (result.run_dir / "round-1.answer.md").read_text(encoding="utf-8"))
        self.assertTrue(result.verified)

    def test_a_worse_fix_is_thrown_away_and_the_best_version_is_fixed_again(self):
        # 2/2 판정은 GOOD, 1/2 는 bad. 이번엔 가짜 시험기가 'WORSE' 면 0/2 로 본다.
        folder = make_project(self.root)
        runner = folder / "verify" / "runner.py"
        runner.write_text(runner.read_text(encoding="utf-8").replace(
            '"t_basic": {"ok": True,', '"t_basic": {"ok": "WORSE" not in code,'), encoding="utf-8")
        project = RtlProject.load(folder)
        worse = "```verilog\nmodule cpu(input clk); // WORSE\nendmodule\n```"
        model = Scripted(BAD, worse, GOOD)
        result = run_rtl(model, project, self.tools)
        self.assertEqual([r.passed for r in result.rounds], [1, 0, 2])
        third = model.requests[2]
        self.assertIn("되돌렸다", third)
        self.assertIn("// bad", third)       # 최고 판(1/2)의 코드에서 다시 고친다
        self.assertNotIn("// WORSE", third)
        self.assertTrue(result.verified)

    def test_continue_starts_from_the_current_file(self):
        project = RtlProject.load(make_project(self.root))
        project.target.write_text("module cpu(input clk); // 이어서\nendmodule\n", encoding="utf-8")
        model = Scripted(GOOD)
        run_rtl(model, project, self.tools, resume=True)
        self.assertIn("// 이어서", model.requests[0])

    def test_bad_project_definitions_are_refused(self):
        with self.assertRaises(ValueError):
            RtlProject.load(make_project(self.root / "a", file="../escape.v"))
        with self.assertRaises(ValueError):
            RtlProject.load(make_project(self.root / "b", file="cpu.py"))
        with self.assertRaises(ValueError):
            RtlProject.load(make_project(self.root / "c", top="cpu; rm"))

    def test_largest_code_block_wins(self):
        self.assertIn("big", extract_code("```verilog\nsmall\n```\n```verilog\nbig big big\n```"))
        self.assertEqual(extract_code("코드 없음"), "")

    def test_continue_checks_the_seed_before_asking_for_another_fix(self):
        project = RtlProject.load(make_project(self.root))
        project.target.write_text("module cpu; // bad\nendmodule\n", encoding="utf-8")
        model = Scripted(GOOD)
        result = run_rtl(model, project, self.tools, resume=True)
        self.assertIn("x4: 기대", model.requests[0])
        self.assertTrue((result.run_dir / "baseline.json").is_file())
        already = Scripted()
        result = run_rtl(already, project, self.tools, resume=True)
        self.assertTrue(result.verified)
        self.assertFalse(already.requests)

    def test_repeated_runs_preserve_the_previous_raw_answers(self):
        project = RtlProject.load(make_project(self.root))
        first = run_rtl(Scripted(BAD), project, self.tools, max_rounds=1)
        second = run_rtl(Scripted(GOOD), project, self.tools, max_rounds=1)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertIn("// bad", (first.run_dir / "round-1.answer.md").read_text(encoding="utf-8"))
        self.assertFalse(json.loads((first.run_dir / "rtl-run.json").read_text())["verified"])

    def test_malformed_or_partial_results_and_failed_runner_never_verify(self):
        project = RtlProject.load(make_project(self.root))
        report = project.root / "result.json"
        for data, rc in (({"a": {"ok": "false"}}, 0), ({"a": True}, 0), ({"a": {"ok": True}}, 2),
                         ({"a": {"ok": True}}, 1)):
            def fake_run(*args, **kwargs):
                report.write_text(json.dumps(data), encoding="utf-8")
                import subprocess
                return subprocess.CompletedProcess(args, rc, "", "")
            with self.subTest(data=data, rc=rc), patch("jarvis.rtl_loop.subprocess.run", side_effect=fake_run):
                self.assertEqual(run_tests(project, self.tools, report)[0], {})
        project.expected_tests = ("a", "b")
        with patch("jarvis.rtl_loop.subprocess.run", side_effect=fake_run):
            self.assertEqual(run_tests(project, self.tools, report)[0], {})

    def test_disallowed_directives_and_task_variants_are_rejected(self):
        for text in ('`include "other.v"', '`define RUN $system', '$fopen("x")', '$readmemh("x")',
                     '$sformat("x")', '$finish;', '$dumpflush;'):
            self.assertTrue(code_problem(text), text)
        self.assertFalse(code_problem('`timescale 1ns/1ps\n// $system is forbidden\nassign y = $signed(a);'))

    def test_round_bounds_are_checked_before_generation(self):
        project = RtlProject.load(make_project(self.root))
        for rounds in (0, -1, 21):
            with self.subTest(rounds=rounds), self.assertRaises(ValueError):
                run_rtl(Scripted(), project, self.tools, max_rounds=rounds)

    def test_cancellation_during_a_test_stops_the_process_and_keeps_the_run(self):
        folder = make_project(self.root)
        runner = folder / "verify" / "runner.py"
        marker = folder / "started"
        runner.write_text("from pathlib import Path\nimport time\nPath('started').write_text('yes')\ntime.sleep(30)\n", encoding="utf-8")
        project = RtlProject.load(folder)
        cancel = threading.Event()
        def stop_when_started():
            deadline = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            cancel.set()
        thread = threading.Thread(target=stop_when_started)
        thread.start()
        started = time.monotonic()
        try:
            with self.assertRaises(GenerationCancelled):
                run_rtl(Scripted(GOOD), project, self.tools, cancel_event=cancel)
        finally:
            thread.join(5)
        self.assertLess(time.monotonic() - started, 8)
        record = json.loads((project.root / "rtl-run.json").read_text(encoding="utf-8"))
        self.assertFalse(record["verified"])
        self.assertEqual(record["stopped"], "사용자 취소")
        self.assertTrue((Path(record["run_dir"]) / "round-1.answer.md").is_file())

    def test_generation_failure_keeps_completed_rounds(self):
        project = RtlProject.load(make_project(self.root))
        replies = iter([BAD])
        def model(messages):
            try:
                return next(replies)
            except StopIteration:
                raise RuntimeError("connection lost")
        with self.assertRaises(RuntimeError):
            run_rtl(model, project, self.tools)
        record = json.loads((project.root / "rtl-run.json").read_text(encoding="utf-8"))
        self.assertEqual(len(record["rounds"]), 1)
        self.assertFalse(record["verified"])
        self.assertIn("RuntimeError", record["stopped"])

    def test_exact_partial_repair_preserves_untouched_code_and_records_evidence(self):
        project = RtlProject.load(make_project(self.root))
        project.target.write_text(extract_code(BAD), encoding="utf-8")
        answer = json.dumps({"diagnosis": "bad signal is incorrect", "edits": [
            {"search": "wire bad = 1'b0;", "replace": "wire GOOD = 1'b1;"}]})
        result = run_rtl(Scripted(answer), project, self.tools, resume=True)
        self.assertTrue(result.verified)
        self.assertTrue(result.rounds[0].accepted)
        self.assertEqual(result.rounds[0].change_type, "patch")
        self.assertIn("// bad", project.target.read_text())
        self.assertTrue((result.run_dir / "round-1.request.json").is_file())
        self.assertIn("+wire GOOD", (result.run_dir / "round-1.diff").read_text())
        evidence = json.loads((result.run_dir / "verified-repair.json").read_text(encoding="utf-8"))
        self.assertFalse(evidence["holdout_verified"])
        self.assertEqual(evidence["passed_tests"], ["t_basic", "t_shift"])

    def test_bad_anchor_leaves_the_original_file_and_test_results_intact(self):
        project = RtlProject.load(make_project(self.root))
        original = extract_code(BAD)
        project.target.write_text(original, encoding="utf-8")
        answer = json.dumps({"diagnosis": "fix", "edits": [{"search": "absent", "replace": "GOOD"}]})
        result = run_rtl(Scripted(answer), project, self.tools, max_rounds=1, resume=True)
        self.assertFalse(result.verified)
        self.assertFalse(result.rounds[0].accepted)
        self.assertEqual(project.target.read_text(), original)
        self.assertFalse((result.run_dir / "round-1.json").exists())

    def test_comment_only_fix_is_rejected_without_running_the_tests_again(self):
        project = RtlProject.load(make_project(self.root))
        model = Scripted(BAD, BAD.replace("// bad", "// differently explained"), GOOD)
        result = run_rtl(model, project, self.tools)
        self.assertIn("실제", result.rounds[1].problem)
        self.assertFalse(result.rounds[1].accepted)
        self.assertFalse((result.run_dir / "round-2.json").exists())
        self.assertIn("같은 답", model.requests[2])
        self.assertTrue(result.verified)

    def test_same_score_cannot_hide_a_regression_in_a_previously_passing_test(self):
        folder = make_project(self.root)
        (folder / "verify" / "runner.py").write_text(textwrap.dedent("""
            import json, sys
            code = open(sys.argv[1], encoding='utf-8').read()
            swapped = 'swap' in code
            json.dump({'a': {'ok': not swapped}, 'b': {'ok': swapped}},
                      open(sys.argv[2], 'w', encoding='utf-8'))
        """), encoding="utf-8")
        project = RtlProject.load(folder)
        result = run_rtl(Scripted(BAD, "```verilog\nmodule cpu; wire swap = 1; endmodule\n```"),
                         project, self.tools, max_rounds=2)
        self.assertEqual([r.passed for r in result.rounds], [1, 1])
        self.assertEqual(result.rounds[1].regressions, ["a"])
        self.assertFalse(result.rounds[1].accepted)
        self.assertNotIn("swap", project.target.read_text())

    def test_resume_cancellation_is_saved_before_the_first_model_answer(self):
        project = RtlProject.load(make_project(self.root))
        project.target.write_text(extract_code(BAD))
        with patch("jarvis.rtl_loop.lint", side_effect=GenerationCancelled("stop")):
            with self.assertRaises(GenerationCancelled):
                run_rtl(Scripted(), project, self.tools, resume=True)
        self.assertEqual(json.loads((project.root / "rtl-run.json").read_text(encoding="utf-8"))["stopped"],
                         "사용자 취소")


# 의존 블록(ADR-103): 시험기는 받은 의존 파일 경로를 report 에 남기고, 설계에 GOOD 이 있으면 통과.
DEP_RUNNER = textwrap.dedent("""
    import json, sys
    code, report, deps = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2], sys.argv[3:]
    json.dump({"t_top": {"ok": "GOOD" in code and len(deps) > 0, "problems": [], "about": " ".join(deps)}},
              open(report, "w", encoding="utf-8"))
""")
FIFO = "module fifo #(parameter W = 8) (input clk, input [W-1:0] d, output [W-1:0] q);\n  assign q = d;\nendmodule\n"


def make_dep(root: Path, name: str = "fifo", verified: bool = True, deps: list | None = None) -> Path:
    import hashlib
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "spec.md").write_text("fifo", encoding="utf-8")
    (folder / f"{name}.v").write_text(FIFO.replace("module fifo", f"module {name}"), encoding="utf-8")
    test = ["{python}", "verify/runner.py", "{file}", "{report}"] + (["{deps}"] if deps else [])
    (folder / "rtl.json").write_text(json.dumps({"top": name, "file": f"{name}.v", "test": test, "deps": deps or []}),
                                     encoding="utf-8")
    sha = hashlib.sha256((folder / f"{name}.v").read_text(encoding="utf-8").encode()).hexdigest()
    (folder / "rtl-run.json").write_text(json.dumps({"verified": verified, "final_sha256": sha}), encoding="utf-8")
    return folder


def make_top(root: Path, deps: list, with_placeholder: bool = True) -> Path:
    folder = root / "soc"
    (folder / "verify").mkdir(parents=True)
    (folder / "verify" / "runner.py").write_text(DEP_RUNNER, encoding="utf-8")
    (folder / "spec.md").write_text("soc 는 fifo 를 쓴다.", encoding="utf-8")
    test = ["{python}", "verify/runner.py", "{file}", "{report}"] + (["{deps}"] if with_placeholder else [])
    (folder / "rtl.json").write_text(json.dumps({"top": "soc", "file": "soc.v", "test": test, "deps": deps}), encoding="utf-8")
    return folder


class DepsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / "no-tools"

    def test_verified_block_is_given_read_only_with_its_ports(self):
        dep = make_dep(self.root)
        project = RtlProject.load(make_top(self.root, ["fifo"]))
        self.assertEqual(project.deps, ((dep / "fifo.v").resolve(),))
        self.assertIn("module fifo #(parameter W = 8) (input clk", project.prompt_spec)
        self.assertIn("다시 정의하지 말고 인스턴스로", project.prompt_spec)
        redefine = "```verilog\n" + FIFO + "module soc(input clk); wire GOOD; endmodule\n```"
        good = "```verilog\nmodule soc(input clk); wire GOOD; fifo u(.clk(clk), .d(8'd0), .q()); endmodule\n```"
        model = Scripted(redefine, good)
        result = run_rtl(model, project, self.tools)
        self.assertTrue(result.verified)
        self.assertIn("검증된 블록 fifo 를 이 파일에서 다시 정의했다", result.rounds[0].problem)
        report = json.loads((result.run_dir / "round-2.json").read_text(encoding="utf-8"))
        self.assertIn("fifo.v", report["t_top"]["about"])               # 시험기가 의존 파일을 받았다
        self.assertIn("module fifo", model.requests[0])                  # 처음 요청에도 포트가 보인다
        self.assertEqual(FIFO, (dep / "fifo.v").read_text(encoding="utf-8"))  # 의존 블록은 그대로
        record = json.loads((project.root / "rtl-run.json").read_text(encoding="utf-8"))
        self.assertEqual(len(record["final_sha256"]), 64)
        self.assertEqual(record["deps"], [str((dep / "fifo.v").resolve())])

    def test_the_new_block_can_be_used_by_the_next_project(self):
        make_dep(self.root)
        top = RtlProject.load(make_top(self.root, ["fifo"]))
        run_rtl(Scripted("```verilog\nmodule soc(input clk); wire GOOD; endmodule\n```"), top, self.tools)
        from jarvis.rtl_loop import verified_now
        self.assertTrue(verified_now(top))
        top.target.write_text(top.target.read_text(encoding="utf-8") + "// 손댐\n", encoding="utf-8")
        self.assertFalse(verified_now(top))                               # 검증 뒤 바뀌면 다시 확인해야 한다

    def test_transitive_blocks_come_first(self):
        make_dep(self.root, "mem")
        make_dep(self.root, "cache", deps=["mem"])
        project = RtlProject.load(make_top(self.root, ["cache"]))
        self.assertEqual([path.name for path in project.deps], ["mem.v", "cache.v"])
        self.assertEqual(set(project.dep_modules), {"mem", "cache"})

    def test_unverified_changed_missing_placeholder_cycle_and_outside_are_refused(self):
        make_dep(self.root, "fifo", verified=False)
        with self.assertRaisesRegex(ValueError, "검증되지 않았거나"):
            RtlProject.load(make_top(self.root, ["fifo"]))
        import shutil
        shutil.rmtree(self.root / "soc")
        shutil.rmtree(self.root / "fifo")
        dep = make_dep(self.root)
        (dep / "fifo.v").write_text(FIFO + "// 검증 뒤 손댐\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "검증 뒤 바뀌었습니다"):
            RtlProject.load(make_top(self.root, ["fifo"]))
        shutil.rmtree(self.root / "soc")
        with self.assertRaisesRegex(ValueError, "\\{deps\\}"):
            RtlProject.load(make_top(self.root, ["fifo"], with_placeholder=False))
        shutil.rmtree(self.root / "soc")
        with self.assertRaisesRegex(ValueError, "같은 RTL 폴더"):
            RtlProject.load(make_top(self.root, ["../elsewhere"]))
        shutil.rmtree(self.root / "soc")
        a = make_dep(self.root, "a", deps=["b"])
        make_dep(self.root, "b", deps=["a"])
        with self.assertRaisesRegex(ValueError, "순환"):
            RtlProject.load(a)


class RtlRepairTests(unittest.TestCase):
    def test_edits_are_atomic_and_use_positions_in_the_original_code(self):
        answer = json.dumps({"diagnosis": "independent edits", "edits": [
            {"search": "left", "replace": "right"}, {"search": "right", "replace": "new"}]})
        self.assertEqual(apply_repair(answer, "left + right"), "right + new\n")
        for edits in ([{"search": "a", "replace": "b"}],
                      [{"search": "abc", "replace": "b"}, {"search": "bc", "replace": "d"}]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                apply_repair(json.dumps({"diagnosis": "bad", "edits": edits}), "abc a")

    def test_formatting_comparison_preserves_real_operator_and_literal_changes(self):
        self.assertEqual(code_tokens("a /* explanation */ >>> 4;"), code_tokens("a>>>4; // note"))
        self.assertNotEqual(code_tokens("a >>> 4;"), code_tokens("a >> 4;"))
        self.assertNotEqual(code_tokens('"// a"'), code_tokens('"// b"'))
        self.assertNotEqual(code_tokens("32'hff"), code_tokens("32'hfe"))
        self.assertNotEqual(code_tokens("a b"), code_tokens("ab"))

    def test_relevant_review_rules_are_selected_without_copying_the_golden_design(self):
        messages = repair_messages("RV32I core", "y = s ? ($signed(a) >>> n) : (a >> n);\nif (rd != 0) rf[rd] <= wb_data;",
                                   "first divergence SW x7,4(x6)")
        self.assertIn("unsigned 문맥", messages[1].content)
        self.assertIn("STORE와 BRANCH", messages[1].content)
        self.assertIn("L1:", messages[1].content)
        simple = repair_messages("counter", "assign q = d;", "failed at cycle 1")
        self.assertNotIn("RV32I 상태", simple[1].content)

    def test_patch_validation_rejects_empty_diagnosis_wrong_schema_and_malformed_json(self):
        for answer in ('{bad', '{"diagnosis":"", "edits":[]}',
                       '{"diagnosis":"x", "edits":[{"search":"a", "replace":1}]}',
                       '{"diagnosis":"x", "edits":[{"search":"a", "replace":"b"}], "file":"../x"}'):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                apply_repair(answer, "a")

    def test_sequential_review_checks_follow_the_spec_and_preserve_opposite_fifo_policies(self):
        fifo = repair_messages("FIFO, full must reject writes even with read",
                               "if(pop) count <= count-1; if(push) count <= count+1;", "wrong flags")
        self.assertIn("뒤의 대입만", fifo[1].content)
        self.assertIn("반드시 규격", fifo[1].content)
        self.assertNotIn("STORE와 BRANCH", fifo[1].content)
        registers = repair_messages("Register file, reset priority", "if(rst) regs[wa]<=0; if(we) regs[wa]<=wd;",
                                     "reset failed")
        self.assertIn("reset 값을 덮어쓸", registers[1].content)


class RtlCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.rtl_root = root / "fabless"
        self.rtl_root.mkdir()
        self.core = JarvisCore(
            storage=Storage(root / "t.db"), hardware=HardwareInfo("OS", "CPU", 96, "GPU", 16, PerformanceProfile.DESKTOP),
            local_general=MockLLMProvider("g", "G"), local_coder=MockLLMProvider("c", "C"),
            cloud_coder=CloudStubProvider("cc", "C"), cloud_reasoning=CloudStubProvider("cr", "C"),
            rtl_root=self.rtl_root, rtl_tools_dir=root / "no-tools")
        self.sid = self.core.get_or_create_session(False)

    def test_usage_and_folders_outside_the_root(self):
        self.assertIn("사용법: /rtl", handle_command(self.core, self.sid, "/rtl")[0])
        self.assertIn("안에 있어야", handle_command(self.core, self.sid, "/rtl ../../etc")[0])
        self.assertIn("읽지 못했습니다", handle_command(self.core, self.sid, "/rtl none")[0])

    def test_command_runs_the_loop_with_the_coding_model(self):
        make_project(self.rtl_root)
        self.core.providers[next(k for k in self.core.providers if k.value == "local_coder")] = _FixedProvider(GOOD)
        answer = handle_command(self.core, self.sid, "/rtl proj 3")[0]
        self.assertIn("모든 시험 통과", answer)
        self.assertIn("1회차: 시험 2/2", answer)


class _FixedProvider(MockLLMProvider):
    def __init__(self, answer):
        super().__init__("fixed", "F")
        self.answer = answer

    def generate(self, messages, **kwargs):
        return self.answer


if __name__ == "__main__":
    unittest.main()
