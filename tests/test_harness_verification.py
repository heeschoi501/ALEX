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
        # 2026-10-23 claude: 이름 하나짜리 상수는 그 이름(과 읽는 함수)만 — 도움말 글 하나에 모듈 전체(31개 파일)를 돌리던 것 줄임
        self.assertEqual(changed_names('VALUE = 1\n', 'VALUE = 2\n'), {'VALUE'})
        self.assertIsNone(changed_names('A, B = 1, 2\n', 'A, B = 1, 3\n'))  # 여러 이름 한꺼번에 넣기는 모듈 전체

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

    def test_constant_change_selects_its_readers_not_the_whole_module(self):
        before = 'HELP = "a"\nOTHER = 1\ndef show():\n    return HELP\ndef run():\n    return OTHER\n'
        after = 'HELP = "a b"\nOTHER = 1\ndef show():\n    return HELP\ndef run():\n    return OTHER\n'
        self.assertEqual(changed_names(before, after), {'HELP', 'show'})
        self.assertIsNone(changed_names('import os\n', 'import sys\n'))  # 그 밖의 전역 문장은 모듈 전체

    def test_tests_the_agent_already_passed_after_the_last_edit_are_not_rerun(self):
        self.prepare()
        model = Script([('', [call('read', filePath='calc.py')]),
                        ('', [call('edit', filePath='calc.py', oldString='a - b', newString='a + b')]),
                        ('', [call('bash', command='python -m unittest discover -s tests -p test_target.py')]),
                        ('고쳤고 시험이 통과했습니다', [])])
        worker = HarnessWorker(lambda: model, timeout=30)
        result = worker.run(self.root, 'calc.py만 수정. python -m unittest discover -s tests -p test_target.py')
        self.assertEqual(result.exit_code, 0)
        checks = {c['path']: c for c in worker.last_verification[0]['checks']}
        self.assertIn('실행 기록', checks['tests/test_target.py']['reason'])      # 에이전트 실행으로 인정
        self.assertTrue(checks['tests/test_contract.py']['command'].startswith('python -m unittest discover'))  # 나머지는 직접

    def test_runs_before_the_last_edit_or_partial_runs_do_not_count(self):
        self.prepare()
        workspace = Workspace(self.root)
        index = TestIndex(self.root, 'tests/test_target.py')
        ok = 'Ran 1 test in 0.001s\n\nOK'
        workspace.test_runs = [
            {'words': ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_target.py'], 'exit': 0, 'edits': 0, 'tail': ok},
            {'words': ['python', '-m', 'unittest', 'tests.test_contract.T.test_negative'], 'exit': 0, 'edits': 1, 'tail': ok},
            {'words': ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'], 'exit': 0, 'edits': 1,
             'tail': 'Ran 2 tests in 0.1s\n\nOK (skipped=2)'},
        ]
        workspace.edits = 1
        self.assertEqual(index.already_passed(workspace), {})
        workspace.test_runs.append({'words': ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'],
                                    'exit': 0, 'edits': 1, 'tail': 'Ran 2 tests in 0.1s\n\nOK'})
        self.assertEqual(index.already_passed(workspace), {})  # stdout-only records are not execution evidence
        self.put('calc.py', 'def add(a, b):\n    return a + b\n')
        workspace.run('bash', {'command': 'python -m unittest discover -s tests -p test_*.py -v'})
        self.assertEqual(sorted(index.already_passed(workspace)), ['tests/test_contract.py', 'tests/test_target.py'])

    def test_undiscovered_and_all_skipped_files_are_not_cached(self):
        self.put('tests/test_ok.py', 'import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        self.put('tests/sub/test_hidden.py', 'import unittest\nclass T(unittest.TestCase):\n def test_hidden(self): self.fail()\n')
        self.put('tests/test_skip.py', 'import unittest\n@unittest.skip("not available")\n'
                 'class T(unittest.TestCase):\n def test_skip(self): self.fail()\n')
        index, workspace = TestIndex(self.root, ''), Workspace(self.root)
        workspace.run('bash', {'command': 'python -m unittest discover -s tests'})
        self.assertEqual(set(index.already_passed(workspace)), {'tests/test_ok.py'})
        self.assertTrue(workspace.test_runs[-1]['evidence']['successful'])

    def test_child_changes_revert_and_external_same_timestamp_changes_invalidate_pass(self):
        self.prepare()
        self.put('calc.py', 'def add(a, b):\n    return a + b\n')
        index, workspace = TestIndex(self.root, ''), Workspace(self.root)
        child = workspace.child([])
        child.run('bash', {'command': 'python -m unittest discover -s tests'})
        self.assertEqual(len(index.already_passed(workspace)), 2)
        child.run('read', {'filePath': 'calc.py'})
        child.run('edit', {'filePath': 'calc.py', 'oldString': 'a + b', 'newString': 'a - b'})
        self.assertEqual(workspace.edits, child.edits)
        self.assertEqual(index.already_passed(workspace), {})
        workspace.revert()
        self.assertEqual(index.already_passed(child), {})
        workspace.run('bash', {'command': 'python -m unittest discover -s tests'})
        self.assertEqual(len(index.already_passed(child)), 2)
        source = self.root / 'calc.py'
        stat = source.stat()
        source.write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(index.already_passed(workspace), {})

    def test_partial_selection_and_mutating_test_do_not_certify_inputs(self):
        self.put('value.txt', 'before')
        self.put('tests/test_mutate.py', 'import unittest, pathlib\nclass T(unittest.TestCase):\n'
                 ' def test_mutate(self): pathlib.Path("value.txt").write_text("after")\n')
        index, workspace = TestIndex(self.root, ''), Workspace(self.root)
        workspace.run('bash', {'command': 'python -m unittest discover -s tests'})
        self.assertEqual(index.already_passed(workspace), {})
        workspace.run('bash', {'command': 'python -m unittest discover -s tests -k mutate'})
        self.assertEqual(index.already_passed(workspace), {})

    def test_duplicate_basenames_do_not_borrow_each_others_pass(self):
        self.put('tests/one/test_same.py', 'import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        self.put('tests/two/test_same.py', 'import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail()\n')
        index, workspace = TestIndex(self.root, ''), Workspace(self.root)
        workspace.run('bash', {'command': 'python -m unittest discover -s tests/one -p test_same.py'})
        self.assertEqual(set(index.already_passed(workspace)), {'tests/one/test_same.py'})

    def test_independent_check_does_not_borrow_nested_file_success(self):
        self.put('tests/test_empty.py', '# No tests here\n')
        self.put('tests/pkg/__init__.py', '')
        self.put('tests/pkg/test_empty.py', 'import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        index, workspace = TestIndex(self.root, ''), Workspace(self.root)
        checks = index.verify(workspace, ['tests/test_empty.py'], time.monotonic() + 10)
        self.assertEqual(checks[0]['status'], 'unverified')

    def test_parallel_verification_keeps_order_and_bash_runs_in_utf8_mode(self):
        self.prepare()
        self.put('tests/test_utf8.py', 'import unittest, pathlib, tempfile\nclass T(unittest.TestCase):\n'
                 '    def test_default_encoding(self):\n'
                 '        p = pathlib.Path(tempfile.mkdtemp()) / "k.txt"\n'
                 '        p.write_bytes("한글".encode("utf-8"))\n'
                 '        self.assertEqual(p.read_text(), "한글")\n')
        self.put('calc.py', 'def add(a, b):\n    return a + b\n')
        workspace = Workspace(self.root)
        index = TestIndex(self.root, '')
        paths = ['tests/test_contract.py', 'tests/test_target.py', 'tests/test_utf8.py']
        checks = index.verify(workspace, paths, time.monotonic() + 60, workers=3)
        self.assertEqual([c['path'] for c in checks], paths)
        self.assertEqual([c['status'] for c in checks], ['verified'] * 3)  # 기본 인코딩이 UTF-8(ALEX 시험 실행과 같게)
