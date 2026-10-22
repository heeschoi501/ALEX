"""관련 실패를 실제 시험 출력으로 다시 고치고, 미해결·단계 초과를 통과로 내놓지 않는지 확인."""
import os
import time
from jarvis.harness.tools import Workspace
from jarvis.harness.verification import TestIndex, changed_names
from jarvis.harness.worker import HarnessWorker
from test_harness import Fixture, Script, call


class RelatedVerificationTests(Fixture):
    def prepare(self):
        self.put('calc.py', 'def add(a, b):\n    return a - b\n')
        self.put('tests/test_target.py', 'import unittest\nfrom calc import add\n'
                 'class T(unittest.TestCase):\n    def test_positive(self): self.assertEqual(add(2, 3), 5)\n')
        self.put('tests/test_contract.py', 'import unittest\nfrom calc import add\n'
                 'class T(unittest.TestCase):\n    def test_negative(self): self.assertEqual(add(-2, 1), -1)\n')

    def partial(self):
        return [('', [call('read', filePath='calc.py')]),
                ('', [call('edit', filePath='calc.py', oldString='a - b', newString='abs(a) + abs(b)')]),
                ('명시된 검사는 통과합니다', [])]

    def test_related_failure_reaches_model_and_is_repaired_without_touching_original(self):
        self.prepare()
        model = Script(self.partial() + [('', [call('edit', filePath='calc.py', oldString='abs(a) + abs(b)', newString='a + b')]),
                                         ('관련 검사도 고쳤습니다', [])])
        worker = HarnessWorker(lambda: model, timeout=30)
        result = worker.run(self.root, 'calc.py만 수정. python -m unittest discover -s tests -p test_target.py')
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.changed, {'calc.py': 'def add(a, b):\n    return a + b\n'})
        self.assertIn('a - b', (self.root / 'calc.py').read_text())
        self.assertEqual(len(worker.last_verification), 2)
        first = {c['path']: c for c in worker.last_verification[0]['checks']}
        self.assertEqual(first['tests/test_contract.py']['status'], 'failed')
        self.assertEqual(first['tests/test_target.py']['status'], 'verified')
        self.assertTrue(all(c['status'] == 'verified' for c in worker.last_verification[1]['checks']))
        self.assertTrue(any('AssertionError' in m.content and 'test_negative' in m.content
                            for seen in model.seen for m in seen if m.role == 'user'))

    def test_unfixed_related_failure_is_rejected_after_one_retry(self):
        self.prepare()
        model = Script(self.partial() + [('이미 끝났습니다', [])])
        worker = HarnessWorker(lambda: model, timeout=30)
        result = worker.run(self.root, 'calc.py를 고쳐라. tests/test_target.py')
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(len(worker.last_verification), 2)
        self.assertIn('test_negative', result.log_tail)
        self.assertIn('a - b', (self.root / 'calc.py').read_text())

    def test_model_cannot_weaken_existing_acceptance_test(self):
        self.prepare()
        model = Script([('', [call('read', filePath='tests/test_contract.py')]),
                        ('', [call('write', filePath='tests/test_contract.py', content='')]),
                        ('', [call('read', filePath='calc.py')]),
                        ('', [call('edit', filePath='calc.py', oldString='a - b', newString='a + b')]), ('끝', [])])
        worker = HarnessWorker(lambda: model, timeout=30)
        result = worker.run(self.root, 'calc.py만 수정. tests/test_target.py')
        self.assertEqual(result.exit_code, 0)
        self.assertNotIn('tests/test_contract.py', result.changed)
        self.assertTrue(any('not allowed' in m.content.lower() for seen in model.seen for m in seen if m.role == 'tool'))

    def test_step_limit_is_not_a_successful_completion(self):
        self.put('calc.py', 'x = 1\n')
        worker = HarnessWorker(lambda: Script([('아직 끝내지 못했습니다', [])]), max_steps=1)
        self.assertEqual(worker.run(self.root, '읽어줘').exit_code, 1)

    def test_index_selects_changed_method_uses_and_preserves_unrelated_test_failures(self):
        self.put('pkg/engine.py', 'class Engine:\n    def close(self): pass\n    def draw(self): pass\n')
        self.put('tests/test_close.py', 'from pkg.engine import Engine\ndef test_it(): Engine().close()\n')
        self.put('tests/test_draw.py', 'from pkg.engine import Engine\ndef test_it(): Engine().draw()\n')
        self.put('tests/test_other.py', 'import elsewhere\nraise RuntimeError("unrelated dependency")\n')
        index = TestIndex(self.root, 'pkg/engine.py')
        self.assertEqual(index.select({'pkg/engine.py': {'close'}}), ['tests/test_close.py'])
        self.assertIn('tests/test_close.py', index.hint())
        self.assertNotIn('tests/test_other.py', index.hint())
        self.assertEqual(changed_names('class E:\n def close(self): pass\n', 'class E:\n def close(self): return True\n'), {'close'})
        self.assertIsNone(changed_names('VALUE = 1\n', 'VALUE = 2\n'))

    def test_bounded_reads_can_continue_at_reported_offset(self):
        self.put('large.py', '\n'.join(f'value_{i} = {i}' for i in range(1, 502)))
        workspace = Workspace(self.root, read_max_lines=400)
        first = workspace.run('read', {'filePath': 'large.py'})
        second = workspace.run('read', {'filePath': 'large.py', 'offset': 401})
        self.assertIn('Use offset=401 to continue', first)
        self.assertNotIn('501: value_501', first)
        self.assertIn('501: value_501 = 501', second)

    def test_all_skipped_tests_do_not_certify_a_change(self):
        self.put('tests/test_skip.py', 'import unittest\n@unittest.skip("no runner")\n'
                 'class T(unittest.TestCase):\n    def test_it(self): pass\n')
        index = TestIndex(self.root, 'tests/test_skip.py')
        checks = index.verify(Workspace(self.root), ['tests/test_skip.py'], time.monotonic() + 10)
        self.assertEqual(checks[0]['status'], 'unverified')

    def test_same_size_same_timestamp_edit_is_not_checked_with_stale_bytecode(self):
        self.prepare()
        workspace = Workspace(self.root)
        workspace.run('bash', {'command': 'python -m unittest discover -s tests -p test_target.py'})
        source = self.root / 'calc.py'
        stat = source.stat()
        source.write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        index = TestIndex(self.root, 'tests/test_target.py')
        checks = index.verify(workspace, ['tests/test_target.py'], time.monotonic() + 10)
        self.assertEqual(checks[0]['status'], 'verified')
