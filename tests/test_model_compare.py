"""The comparison harness must not turn fake output or early exits into PASS."""
import unittest
import os
from unittest.mock import patch

from scripts.compare_python import export_prompts, score_reply


class ModelComparisonTests(unittest.TestCase):
    def test_prompts_exclude_check_code(self):
        bundle = export_prompts()
        self.assertEqual(len(bundle["tasks"]), 18)
        self.assertEqual(len(bundle["suite_sha256"]), 64)
        self.assertTrue(all(set(task) == {"name", "prompt"} for task in bundle["tasks"]))

    def test_correct_answer_passes_real_checks(self):
        reply = """def merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result
"""
        self.assertTrue(score_reply(reply, "merge_ranges")["passed"])

    def test_incorrect_and_forged_success_do_not_pass(self):
        for reply in ("def merge_ranges(ranges): return []", "print('@@OK@@')"):
            with self.subTest(reply=reply):
                self.assertFalse(score_reply(reply, "merge_ranges")["passed"])

    def test_system_exit_is_not_completion(self):
        self.assertFalse(score_reply("import sys; sys.exit(0)", "merge_ranges")["passed"])

    def test_immediate_process_exit_has_no_positive_evidence(self):
        self.assertFalse(score_reply("import os; os._exit(0)", "merge_ranges")["passed"])

    def test_network_access_is_blocked_before_connecting(self):
        verdict = score_reply("import socket; socket.create_connection(('127.0.0.1', 9))", "merge_ranges")
        self.assertFalse(verdict["passed"])
        self.assertIn("PermissionError", verdict["detail"])

    def test_candidate_does_not_inherit_the_host_api_credential(self):
        reply = """import os
assert 'OPENAI_API_KEY' not in os.environ
def merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result
"""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret-not-for-the-candidate"}):
            self.assertTrue(score_reply(reply, "merge_ranges")["passed"])
