"""grade_codegen 의 코드 추출·실패 분류 회귀 테스트.

2026-09-19 측정에서 채점기가 실패 사유를 거짓으로 적고 있었다. 예산이 소진돼 응답이
코드 블록 중간에서 잘리면 닫는 ``` 가 없는데, 그때 본문 전체를 그대로 실행해
맨 앞 ```python 줄 때문에 1행에서 SyntaxError 가 났다. 로그에는 '모델이 문법을 틀렸다' 로
남았지만 실제 원인은 잘림이었다. 아래 케이스는 그때 실제로 저장돼 있던 응답 모양이다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from grade_codegen import CASES, extract_code, grade, repetition_ratio  # noqa: E402


class ExtractCodeTests(unittest.TestCase):
    def test_closed_fence_returns_block_without_backticks(self):
        text = "설명입니다.\n```python\ndef calc(expr):\n    return 1\n```\n끝."
        code, how = extract_code(text)
        self.assertEqual(how, "fenced")
        self.assertNotIn("```", code)
        self.assertIn("def calc", code)

    def test_prefers_the_block_that_defines_calc(self):
        text = "```python\nprint(calc('1+2'))\n```\n```python\ndef calc(expr):\n    return 3\n```"
        code, _ = extract_code(text)
        self.assertIn("def calc", code)

    def test_truncated_response_does_not_keep_the_opening_fence(self):
        """이게 핵심 회귀다. 예전 구현은 여기서 본문 전체를 돌려줘 SyntaxError 를 만들었다."""
        text = "```python\nimport math\n\ndef calc(expr):\n    return math.sqrt(9)\n"
        code, how = extract_code(text)
        self.assertEqual(how, "unclosed-fence")
        self.assertFalse(code.lstrip().startswith("```"), "여는 펜스가 코드에 남으면 안 된다")
        compile(code, "<candidate>", "exec")  # 잘렸어도 그때까지의 코드는 파싱돼야 한다

    def test_bare_code_without_any_fence(self):
        code, how = extract_code("def calc(expr):\n    return 1\n")
        self.assertEqual(how, "bare")

    def test_empty_response_is_labelled_no_code(self):
        code, how = extract_code("")
        self.assertEqual(how, "no-code")
        self.assertEqual(code, "")


class GradeFailureReasonTests(unittest.TestCase):
    def test_empty_output_is_not_reported_as_nameerror(self):
        """think ON 실패 3건이 content 가 비어 있었는데 NameError 7개로 기록됐다."""
        results = grade("", exec_timeout=5)
        self.assertEqual(len(results), len(CASES))
        self.assertTrue(all(not r["ok"] for r in results))
        self.assertTrue(all(r["got"] == "no code emitted" for r in results))

    def test_prose_without_calc_is_distinguished(self):
        results = grade("죄송하지만 도와드릴 수 없습니다.", exec_timeout=5)
        self.assertTrue(all(r["got"] == "no def calc() in output" for r in results))

    def test_indented_block_still_runs(self):
        results = grade("    def calc(expr):\n        return 3.0\n", exec_timeout=10)
        by_expr = {r["expr"]: r for r in results}
        self.assertTrue(by_expr["sqrt(9)"]["ok"], by_expr["sqrt(9)"]["got"])


class RepetitionRatioTests(unittest.TestCase):
    def test_degenerate_loop_is_detected(self):
        line = "    expr = re.sub(r'(\\d+)(deg)', r'\\1', expr)\n"
        code = "def calc(expr):\n" + line * 200
        self.assertGreater(repetition_ratio(code), 0.9)

    def test_normal_code_is_not_flagged(self):
        code = "import math\n\ndef calc(expr):\n    ns = {'sqrt': math.sqrt}\n    return eval(expr, ns)\n"
        self.assertLess(repetition_ratio(code), 0.5)

    def test_empty_code_is_zero(self):
        self.assertEqual(repetition_ratio(""), 0.0)


if __name__ == "__main__":
    unittest.main()


class BriefThinkingPromptTests(unittest.TestCase):
    """--brief-thinking 이 실제로 system 메시지에만, 한 번만 붙는지 고정한다."""

    def test_the_app_prompt_carries_adr_046(self):
        """ADR-046 채택 후에는 앱의 코딩 지시 자체가 간결 지시를 포함한다."""
        from grade_codegen import brief_already_applied
        self.assertTrue(brief_already_applied())

    def test_verification_comes_after_writing_code(self):
        """ADR-046 의 핵심은 순서다. 검산이 코드 출력보다 앞이면 되돌아간 것이다."""
        from jarvis.core import CODING_SYSTEM_ADDENDUM as C
        self.assertGreater(C.index("검산은 코드를 다 쓴 뒤"), C.index("실행 가능한 완결된 코드"))
        self.assertNotIn("코드를 내놓기 전에 사용자가 준 예시", C)

    def test_brief_flag_does_not_duplicate_the_instruction(self):
        from grade_codegen import build_messages
        content = build_messages(coding_prompt=True, brief=True)[0]["content"]
        self.assertEqual(content.count("검산은 코드를 다 쓴 뒤"), 1)

    def test_coding_prompt_is_one_system_message(self):
        from grade_codegen import CODING_SYSTEM_ADDENDUM, build_messages
        msgs = build_messages(coding_prompt=True, brief=True)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn(CODING_SYSTEM_ADDENDUM, msgs[0]["content"])
        self.assertEqual(sum(1 for m in msgs if m["role"] == "system"), 1)

    def test_brief_without_coding_prompt_still_reaches_the_model(self):
        from grade_codegen import BRIEF_THINKING_ADDENDUM, build_messages
        msgs = build_messages(coding_prompt=False, brief=True)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], BRIEF_THINKING_ADDENDUM)

    def test_user_task_is_untouched_in_every_variant(self):
        from grade_codegen import TASK, build_messages
        for coding in (True, False):
            for brief in (True, False):
                msgs = build_messages(coding_prompt=coding, brief=brief)
                user = [m for m in msgs if m["role"] == "user"]
                self.assertEqual([m["content"] for m in user], [TASK])
