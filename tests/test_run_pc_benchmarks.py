"""PC 평가 일괄 실행(scripts/run_pc_benchmarks.py, 2026-10-26) — 모델·서버 없이 계획·건너뛰기·요약만 본다."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest

from scripts import run_pc_benchmarks as bench


def args(**changes):
    values = {"only": None, "model": "m", "lcb_limit": 20, "gpqa_limit": 0}
    values.update(changes)
    return argparse.Namespace(**values)


class PlanTests(unittest.TestCase):
    def test_server_evals_are_skipped_with_a_reason_when_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            steps = bench.plan(args(only=["lcb", "gpqa"]), Path(temp), ready=lambda: "Strata 서버 꺼짐")
        self.assertEqual([s["name"] for s in steps], ["lcb", "gpqa"])
        self.assertTrue(all(s["skip"] for s in steps))

    def test_devauto_uses_the_refreshed_tasks_and_given_model(self):
        with tempfile.TemporaryDirectory() as temp:
            step = bench.plan(args(only=["devauto"], model="qwen"), Path(temp), ready=lambda: "")[0]
        command = step["command"]
        self.assertEqual(command[command.index("--model") + 1], "qwen")
        self.assertEqual(Path(command[command.index("--tasks-file") + 1]), bench.TASKS)


class SummaryTests(unittest.TestCase):
    def test_results_are_counted_by_the_same_rules_as_the_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "devauto.json").write_text(json.dumps([
                {"task": "a", "bug_detected": True, "acceptance_passed": True, "suite_exit": 0, "new_failures": [], "seconds": 10},
                # 기존 실패 때문에 전체 검사가 1 이어도 새 회귀가 없으면 따로 센다(2026-10-22 0/5 와 4/5 를 나눈 기준)
                {"task": "b", "bug_detected": True, "acceptance_passed": True, "suite_exit": 1, "new_failures": [], "seconds": 5},
                {"task": "c", "bug_detected": True, "acceptance_passed": False, "suite_exit": 1, "new_failures": ["x"]},
                {"task": "d", "bug_detected": False, "note": "변이 자리 없음"},
            ]), encoding="utf-8")
            (out / "lcb.jsonl").write_text("\n".join(json.dumps(r) for r in [
                {"passed": True, "seconds": 3}, {"passed": False, "reason": "no code block", "seconds": 2}]), encoding="utf-8")
            (out / "gpqa.jsonl").write_text("\n".join(json.dumps(r) for r in [
                {"ok": True, "domain": "Physics"}, {"ok": False, "domain": "Physics"}, {"ok": False, "domain": "Biology",
                                                                                          "error": "timeout"}]), encoding="utf-8")
            summary = bench.summarize(out)
        self.assertEqual(summary["devauto"]["valid"], 3)
        self.assertEqual((summary["devauto"]["strict_success"], summary["devauto"]["passed_without_new_failures"]), (1, 2))
        self.assertEqual(summary["devauto"]["skipped"], {"d": "변이 자리 없음"})
        self.assertEqual((summary["lcb"]["passed"], summary["lcb"]["no_code"]), (1, 1))
        self.assertEqual(summary["gpqa"]["by_domain"], {"Biology": "0/1", "Physics": "1/2"})
        text = bench.report(summary, [{"name": "lcb", "exit": 0, "seconds": 5}, {"name": "gpqa", "skip": "데이터 없음"}])
        self.assertIn("LiveCodeBench hard: 1/2", text)
        self.assertIn("건너뜀 — 데이터 없음", text)

    def test_an_empty_folder_has_nothing_to_report(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(bench.summarize(Path(temp)), {})


if __name__ == "__main__":
    unittest.main()
