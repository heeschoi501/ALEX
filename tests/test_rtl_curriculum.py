"""Trusted HDL task contracts: the oracle must not share the DUT's common bugs."""
import tempfile
import json
from pathlib import Path
import unittest

from scripts.rtl_curriculum import TASKS, evaluate, evaluate_timing, expected, prepare, timing_samples, vectors


class RtlCurriculumTests(unittest.TestCase):
    def test_full_fifo_uses_pre_edge_acceptance_even_during_a_read(self):
        rows = [("reset", (1, 0, 0, 0)), ("capacity", (0, 1, 0, 10)),
                ("capacity", (0, 1, 0, 11)), ("capacity", (0, 1, 0, 12)),
                ("simultaneous", (0, 1, 1, 99)), ("capacity", (0, 0, 1, 0)),
                ("capacity", (0, 0, 1, 0)), ("empty", (0, 0, 1, 0))]
        self.assertEqual(expected("fifo3", rows),
                         [(0, 0, 1), (0, 0, 0), (0, 0, 0), (0, 1, 0),
                          (10, 0, 0), (11, 0, 0), (12, 0, 1), (12, 0, 1)])

    def test_empty_fifo_does_not_read_the_byte_written_on_the_same_edge(self):
        rows = [("reset", (1, 0, 0, 0)), ("simultaneous", (0, 1, 1, 42)),
                ("simultaneous", (0, 1, 1, 57)), ("empty", (0, 0, 1, 0))]
        self.assertEqual(expected("fifo3", rows), [(0, 0, 1), (0, 0, 0), (42, 0, 0), (57, 0, 1)])

    def test_register_reset_has_priority_and_zero_is_immutable(self):
        rows = [("reset", (1, 1, 3, 0xCAFE, 3, 0)), ("zero", (0, 1, 0, 99, 0, 0)),
                ("write", (0, 1, 2, 55, 2, 0)), ("hold", (0, 0, 2, 99, 2, 0))]
        self.assertEqual(expected("regfile", rows), [(0, 0), (0, 0), (55, 0), (55, 0)])

    def test_incomplete_reordered_duplicate_and_unknown_traces_do_not_pass(self):
        rows = [("reset", (1, 0, 0, 0, 0, 0))]
        for output in ("", "ROW 1 0 0", "ROW 0 0 0\nROW 0 0 0", "ROW 0 junk 0"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                evaluate("regfile", rows, output)
        self.assertFalse(evaluate("regfile", rows, "ROW 0 xxxx 0000")["reset"]["ok"])

    def test_directed_cases_cover_all_groups_and_new_seeds_change_the_random_inputs(self):
        for task in TASKS:
            first = vectors(task, 1)
            second = vectors(task, 2)
            self.assertEqual(set(name for name, _ in first), set(TASKS[task]["groups"]))
            self.assertNotEqual([v for g, v in first if g == "random"], [v for g, v in second if g == "random"])

    def test_existing_curriculum_folder_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            projects = prepare(root)
            original = (projects[0] / "fifo3.v").read_bytes()
            with self.assertRaises(ValueError):
                prepare(root)
            self.assertEqual((projects[0] / "fifo3.v").read_bytes(), original)

    def test_future_tasks_require_between_edge_contracts_in_the_fixed_test_list(self):
        with tempfile.TemporaryDirectory() as folder:
            projects = prepare(Path(folder))
            for task, project in zip(TASKS, projects):
                config = json.loads((project / 'rtl.json').read_text())
                self.assertIn('--include-timing', config['test'])
                self.assertIn('timing.synchronous_reset', config['expected_tests'])
                if task == 'regfile':
                    self.assertIn('timing.asynchronous_read', config['expected_tests'])
                self.assertEqual(len(config['expected_tests']), len(set(config['expected_tests'])))

    def test_clock_between_edge_contracts_are_scored_separately(self):
        for task in TASKS:
            samples = timing_samples(task)
            def output(modify=None):
                lines = []
                for i, (group, _, want) in enumerate(samples):
                    if modify == group:
                        want = (want[0] ^ 1, *want[1:])
                    lines.append(f"ROW {i} " + " ".join(f"{value:x}" for value in want))
                return "\n".join(lines)
            self.assertTrue(all(row["ok"] for row in evaluate_timing(task, samples, output()).values()))
            for contract in ("synchronous_reset", "write_waits_for_edge"):
                self.assertFalse(evaluate_timing(task, samples, output(contract))[contract]["ok"])
            if task == "regfile":
                self.assertFalse(evaluate_timing(task, samples, output("asynchronous_read"))["asynchronous_read"]["ok"])
                self.assertEqual(sum(g == "asynchronous_read" for g, _, _ in samples), 64)


if __name__ == "__main__":
    unittest.main()
