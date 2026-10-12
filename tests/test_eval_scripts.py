"""공개 평가 스크립트(2026-10-12): GPQA Diamond·LiveCodeBench. 모델·실제 데이터 없이 채점 규칙만 확인한다."""
import base64
import json
import pickle
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts import eval_gpqa, eval_lcb


def problem(tests, private=None, starter="", func=""):
    raw = base64.b64encode(zlib.compress(pickle.dumps(json.dumps(private or [])))).decode()
    return {"public_test_cases": json.dumps(tests), "private_test_cases": raw, "starter_code": starter,
            "metadata": json.dumps({"func_name": func} if func else {}), "question_content": "q"}


class LiveCodeBenchJudgeTests(unittest.TestCase):
    def test_output_comparison_is_exact_like_the_official_grader(self):
        """2026-10-12 Codex 지적: 상대 오차 비교가 1000000↔1000001·1↔nan 을 통과시켰다."""
        for expected, actual, ok in (("1000000", "1000001", False), ("1", "nan", False), ("1", "inf", False),
                                     ("1", "1.0", True), ("0.5", "0.50", True), ("3 4", " 3 4 ", True),
                                     ("0.333333", "0.3333333", False), ("Yes", "yes", False), ("1\n2", "1\n2\n", True)):
            with self.subTest(expected=expected, actual=actual):
                self.assertEqual(eval_lcb.same(expected, actual), ok)

    def test_private_tests_are_read_without_running_pickled_code(self):
        tests = eval_lcb.tests_of(problem([{"input": "1\n", "output": "2\n", "testtype": "stdin"}],
                                          [{"input": "5\n", "output": "6\n", "testtype": "stdin"}]))
        self.assertEqual([t["output"] for t in tests], ["2\n", "6\n"])

        class Evil:
            def __reduce__(self):
                import os
                return (os.getcwd, ())

        evil = problem([])
        evil["private_test_cases"] = base64.b64encode(zlib.compress(pickle.dumps(Evil()))).decode()
        with self.assertRaises(pickle.UnpicklingError):
            eval_lcb.tests_of(evil)

    def test_stdin_and_function_answers_are_judged_on_every_test(self):
        stdin = problem([{"input": "2 3\n", "output": "5\n", "testtype": "stdin"}],
                        [{"input": "10 -4\n", "output": "6\n", "testtype": "stdin"}])
        self.assertTrue(eval_lcb.judge(stdin, "a, b = map(int, input().split())\nprint(a + b)\n")[0])
        self.assertFalse(eval_lcb.judge(stdin, "print(5)\n")[0])  # 공개 시험만 맞히면 통과가 아니다
        self.assertFalse(eval_lcb.judge(stdin, "import socket\nsocket.create_connection(('127.0.0.1', 9))\n")[0])
        func = problem([{"input": "[1, 2, 3]\n2", "output": "6", "testtype": "functional"}],
                       starter="class Solution:\n    def scale(self, nums: List[int], k: int) -> int:\n", func="scale")
        good = "class Solution:\n    def scale(self, nums: List[int], k: int) -> int:\n        return sum(nums) * k // 2\n"
        self.assertTrue(eval_lcb.judge(func, good)[0])
        self.assertFalse(eval_lcb.judge(func, good.replace("// 2", "+ 1"))[0])


class GPQATests(unittest.TestCase):
    def test_choices_are_shuffled_reproducibly_and_the_answer_letter_is_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "gpqa.csv"
            header = "Question,Correct Answer,Incorrect Answer 1,Incorrect Answer 2,Incorrect Answer 3,High-level domain\n"
            data.write_text(header + "".join(f"Q{i},right{i},w1,w2,w3,Physics\n" for i in range(20)), encoding="utf-8")
            first, second = list(eval_gpqa.items(data)), list(eval_gpqa.items(data))
        self.assertEqual(first, second)
        for item in first:
            line = next(l for l in item["prompt"].splitlines() if l.startswith(item["correct"] + ") "))
            self.assertIn("right", line)
        self.assertGreater(len({item["correct"] for item in first}), 1)  # 정답이 한 자리로 몰리지 않는다

    def test_only_the_final_answer_line_is_read(self):
        found = eval_gpqa.ANSWER.findall("A seems likely... but\nAnswer: **C**")
        self.assertEqual(found[-1], "C")
        self.assertEqual(eval_gpqa.ANSWER.findall("I think it is B"), [])


if __name__ == "__main__":
    unittest.main()
