"""A supervised coding run must preserve changes until review and survive restarts."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import time
import unittest
from unittest import mock

from jarvis.app import handle_command
from jarvis.developer import DeveloperMode, DeveloperRun, _default_runner, read_outcomes
from jarvis.storage import Storage
from jarvis.tools import ToolError
from jarvis.workspace import SafeWorkspaceEditor


class DeveloperModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'jarvis').mkdir()
        (self.root / 'tests').mkdir()
        (self.root / 'jarvis' / 'source.py').write_text('value = 0\n', encoding='utf-8')
        (self.root / 'tests' / 'test_source.py').write_text(
            'import sys, unittest\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis"))\n'
            'from source import value\n'
            'class SourceTests(unittest.TestCase):\n'
            '    def test_value(self): self.assertEqual(value, 1)\n', encoding='utf-8')
        self.store = Storage(self.root / 'data' / 'app.db')
        self.editor = SafeWorkspaceEditor(self.root, self.root / 'backups', storage=self.store)
        self.proposals = []

        def propose(_session, path, _instruction):
            result = self.editor.create_proposal(path, 'value = 1\n')
            self.proposals.append(result)
            return result

        self.core = SimpleNamespace(
            storage=self.store, workspace_editor=self.editor,
            current_project_root=lambda: self.root,
            propose_file_edit_local=propose,
            record_edit_experience=lambda *args, **kwargs: None,
        )
        self.mode = DeveloperMode(self.core)

    def test_failure_proposal_user_approval_and_retest(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '값을 1로 고쳐줘')
        first = self.mode.next('session')
        self.assertIn('테스트 실패', first)
        self.assertIn('-value = 0', first)
        self.assertEqual((self.root / 'jarvis' / 'source.py').read_text(), 'value = 0\n')
        proposal_id = self.proposals[-1].proposal_id
        waiting = DeveloperRun.load(self.store)
        self.assertEqual(waiting.phase, 'awaiting_approval')
        self.assertIn(f'편집 제안 ID: {proposal_id}', first)
        self.assertRegex(first, rf'(?m)^편집 제안 ID: {re.escape(proposal_id)}$')
        self.assertIn('대기 중인 diff 다시 보기: /dev-next', self.mode.status())
        reopened_diff = self.mode.next('session')
        self.assertIn(f'편집 제안 ID: {proposal_id}', reopened_diff)
        self.assertIn('-value = 0', reopened_diff)
        self.assertIn(f'/apply-edit {proposal_id} --yes', reopened_diff)
        self.assertEqual((self.root / 'jarvis' / 'source.py').read_text(), 'value = 0\n')
        answer, _, _ = handle_command(self.core, 'session', f'/apply-edit {proposal_id} --yes')
        self.assertIn('편집 적용 완료', answer)
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertIn('테스트 통과', self.mode.next('session'))
        self.assertEqual(DeveloperRun.load(self.store).phase, 'passed')
        self.editor.restore_latest('jarvis/source.py')
        self.assertEqual((self.root / 'jarvis' / 'source.py').read_text(), 'value = 0\n')

    def test_state_and_unapplied_proposal_survive_reopen(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '목표')
        self.mode.next('session')
        reopened = Storage(self.store.db_path)
        new_editor = SafeWorkspaceEditor(self.root, self.root / 'backups', storage=reopened)
        new_core = SimpleNamespace(storage=reopened, workspace_editor=new_editor,
                                   current_project_root=lambda: self.root)
        status = DeveloperMode(new_core).status()
        self.assertIn('awaiting_approval', status)
        proposal_id = DeveloperRun.load(reopened).proposal_id
        restored = new_editor.get(proposal_id)
        self.assertIn('-value = 0', restored.diff)
        self.assertEqual((self.root / 'jarvis/source.py').read_text(), 'value = 0\n')
        new_editor.apply(proposal_id)
        self.assertEqual((self.root / 'jarvis/source.py').read_text(), 'value = 1\n')
        self.assertIsNone(self.editor.get(proposal_id))

    def test_archived_runs_can_be_listed_and_resumed_without_execution(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '첫 작업')
        first = DeveloperRun.load(self.store)
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '두 번째 작업')
        second = DeveloperRun.load(self.store)
        self.assertNotEqual(first.run_id, second.run_id)
        listed, _, _ = handle_command(self.core, 'session', '/dev-list')
        self.assertIn(first.run_id, listed)
        self.assertIn(second.run_id, listed)
        resumed, _, _ = handle_command(self.core, 'session', '/dev-resume ' + first.run_id)
        self.assertIn('첫 작업', resumed)
        self.assertEqual(DeveloperRun.load(self.store).run_id, first.run_id)
        self.assertEqual(len(self.proposals), 0)
        self.assertEqual((self.root / 'jarvis/source.py').read_text(), 'value = 0\n')
        other = self.root / 'other'
        other.mkdir()
        self.core.current_project_root = lambda: other
        with self.assertRaises(ToolError):
            self.mode.resume(first.run_id)
        self.assertNotIn(first.run_id, self.mode.history())

    def test_resume_cannot_abandon_pending_approval(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '첫 작업')
        first = DeveloperRun.load(self.store)
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '다음 작업')
        self.mode.next('session')
        with self.assertRaises(ToolError):
            self.mode.resume(first.run_id)
        self.assertEqual(DeveloperRun.load(self.store).phase, 'awaiting_approval')

    def test_interrupted_execution_is_recovered_without_automatic_test_or_edit(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '중단 복구')
        for phase in ('testing', 'proposing', 'verifying'):
            run = DeveloperRun.load(self.store)
            run.phase = phase
            run.save(self.store)
            self.assertIn('중단된 실행', DeveloperMode(self.core).status())
            self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual(len(self.proposals), 0)

    def test_legacy_snapshot_gets_stable_id_and_archive(self):
        import json
        from dataclasses import asdict
        from jarvis.developer import STATE_KEY
        run = DeveloperRun('이전 작업', str(self.root.resolve()), 'jarvis/source.py', 'tests/test_source.py')
        data = asdict(run)
        del data['run_id']
        del data['created_at']
        self.store.set_state(STATE_KEY, json.dumps(data))
        first = DeveloperRun.load(self.store)
        self.assertEqual(DeveloperRun.load(self.store).run_id, first.run_id)
        self.assertIn(first.run_id, self.mode.history())

    def test_pending_proposal_must_be_resolved_before_new_run(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '첫 작업')
        self.mode.next('session')
        with self.assertRaises(ToolError):
            self.mode.start('jarvis/source.py', 'tests/test_source.py', '다음 작업')
        self.assertEqual(DeveloperRun.load(self.store).goal, '첫 작업')

    def test_passing_baseline_can_request_feature_proposal_then_retest(self):
        (self.root / 'tests' / 'test_source.py').write_text(
            'import unittest\nclass Smoke(unittest.TestCase):\n'
            '    def test_smoke(self): self.assertTrue(True)\n', encoding='utf-8')
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '값 변경')
        with self.assertRaises(ToolError):
            self.mode.propose('session')
        self.assertIn('테스트 통과', self.mode.next('session'))
        answer = self.mode.propose('session')
        self.assertIn('-value = 0', answer)
        proposal_id = self.proposals[-1].proposal_id
        self.assertIn(f'편집 제안 ID: {proposal_id}', answer)
        self.assertRegex(answer, rf'(?m)^편집 제안 ID: {re.escape(proposal_id)}$')
        handle_command(self.core, 'session', f'/apply-edit {proposal_id} --yes')
        self.assertIn('테스트 통과', self.mode.next('session'))
        self.assertEqual(DeveloperRun.load(self.store).phase, 'passed')

    def test_full_suite_reports_unrelated_failure_then_pass_without_editing(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '값 변경')
        with self.assertRaises(ToolError):
            self.mode.verify()
        self.mode.next('session')
        proposal_id = self.proposals[-1].proposal_id
        handle_command(self.core, 'session', f'/apply-edit {proposal_id} --yes')
        self.assertIn('테스트 통과', self.mode.next('session'))
        unrelated = self.root / 'tests' / 'test_other.py'
        unrelated.write_text('import unittest\nclass Other(unittest.TestCase):\n'
                             '    def test_other(self): self.assertTrue(False)\n', encoding='utf-8')
        before = (self.root / 'jarvis' / 'source.py').read_text(encoding='utf-8')
        failed, _, _ = handle_command(self.core, 'session', '/dev-verify')
        self.assertIn('전체 테스트 실패', failed)
        self.assertIn('test_other', failed)
        self.assertEqual((self.root / 'jarvis' / 'source.py').read_text(encoding='utf-8'), before)
        self.assertIn('전체 테스트(마지막 실행): FAIL', self.mode.status())
        unrelated.write_text('import unittest\nclass Other(unittest.TestCase):\n'
                             '    def test_other(self): self.assertTrue(True)\n', encoding='utf-8')
        passed, _, _ = handle_command(self.core, 'session', '/dev-verify')
        self.assertIn('전체 테스트 통과', passed)
        self.assertIn('전체 테스트(마지막 실행): PASS', self.mode.status())
        self.assertEqual(len(self.proposals), 1)
        (self.root / 'jarvis' / 'source.py').write_text('value = 2\n', encoding='utf-8')
        self.assertIn('전체 테스트(마지막 실행): not_run', self.mode.status())

    @unittest.skipUnless(shutil.which('git'), 'Git executable is unavailable')
    def test_git_changes_reads_status_and_diff_summary(self):
        git = shutil.which('git')

        def call(*args):
            subprocess.run([git, *args], cwd=self.root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        call('init', '-q')
        call('add', 'jarvis/source.py')
        call('-c', 'user.name=ALEX Test', '-c', 'user.email=alex@example.invalid',
             'commit', '-q', '-m', 'baseline')
        (self.root / 'jarvis' / 'source.py').write_text('value = 2\n', encoding='utf-8')
        result = self.mode.changes()
        self.assertIn('Git 변경 상태', result)
        self.assertIn('source.py', result)
        self.assertIn('1 file changed', result)
        self.assertEqual((self.root / 'jarvis' / 'source.py').read_text(encoding='utf-8'), 'value = 2\n')

    def test_git_changes_explains_non_repository(self):
        if shutil.which('git'):
            self.assertIn('Git 저장소가 아니거나', self.mode.changes())

    def test_test_run_does_not_claim_success_if_test_changes_source(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '값 변경')

        def runner(_root, _test):
            (self.root / 'jarvis' / 'source.py').write_text('value = 1\n', encoding='utf-8')
            return 0, 'OK'

        result = DeveloperMode(self.core, runner=runner).next('session')
        self.assertIn('수정 대상 파일이 변경', result)
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual(len(self.proposals), 0)

    def test_full_suite_does_not_claim_success_if_test_changes_source(self):
        (self.root / 'tests' / 'test_source.py').write_text(
            'import unittest\nclass Smoke(unittest.TestCase):\n'
            '    def test_smoke(self): self.assertTrue(True)\n', encoding='utf-8')
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '값 변경')
        self.assertIn('테스트 통과', self.mode.next('session'))

        def suite_runner(_root):
            (self.root / 'jarvis' / 'source.py').write_text('value = 2\n', encoding='utf-8')
            return 0, 'OK'

        result = DeveloperMode(self.core, suite_runner=suite_runner).verify()
        self.assertIn('수정 대상 파일이 변경', result)
        self.assertEqual(DeveloperRun.load(self.store).suite_result, 'INCOMPLETE')

    def test_passed_status_expires_if_source_changes(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '목표')
        self.mode.next('session')
        proposal_id = self.proposals[-1].proposal_id
        handle_command(self.core, 'session', f'/apply-edit {proposal_id} --yes')
        self.mode.next('session')
        self.assertEqual(DeveloperRun.load(self.store).phase, 'passed')
        (self.root / 'jarvis' / 'source.py').write_text('value = 2\n', encoding='utf-8')
        self.assertIn('통과 상태를 취소함', self.mode.status())
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')

    def test_test_target_and_source_are_confined(self):
        for test in ('../outside.py', 'tests/../bad.py', 'tests/test_source.py/other', 'tests/test_source.txt'):
            with self.subTest(test=test), self.assertRaises(ToolError):
                self.mode.start('jarvis/source.py', test, '목표')
        with self.assertRaises(ToolError):
            self.mode.start('../outside.py', 'tests/test_source.py', '목표')
        self.assertIsNone(DeveloperRun.load(self.store))

    def test_command_is_not_a_shell_and_cannot_run_an_arbitrary_name(self):
        status, output = _default_runner(self.root, 'tests/test_source.py')
        self.assertNotEqual(status, 0)
        self.assertIn('FAIL', output)
        with self.assertRaises(ToolError):
            self.mode.start('jarvis/source.py', 'tests/test_source.py;echo_secret', '목표')

    def test_empty_or_all_skipped_test_does_not_mark_the_run_as_passed(self):
        test = self.root / 'tests/test_source.py'
        for body in ('# no tests\n',
                     'import unittest\n@unittest.skip("pending")\n'
                     'class Example(unittest.TestCase):\n    def test_x(self): pass\n'):
            with self.subTest(body=body):
                test.write_text(body, encoding='utf-8')
                status, _ = _default_runner(self.root, 'tests/test_source.py')
                self.assertEqual(status, 125)
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '목표')
        self.assertIn('테스트 실행 문제', self.mode.next('session'))
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual(self.proposals, [])

    def test_full_suite_cannot_pass_when_all_tests_disappear(self):
        test = self.root / 'tests/test_source.py'
        test.write_text('import unittest\nclass Example(unittest.TestCase):\n'
                        '    def test_ok(self): pass\n', encoding='utf-8')
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '목표')
        self.assertIn('테스트 통과', self.mode.next('session'))
        test.write_text('# no tests\n', encoding='utf-8')
        self.mode.verify()
        self.assertEqual(DeveloperRun.load(self.store).suite_result, 'INCOMPLETE')

    def test_test_process_stops_when_user_cancels(self):
        (self.root / 'tests' / 'test_sleep.py').write_text(
            'import time, unittest\n'
            'class SleepTest(unittest.TestCase):\n'
            '    def test_wait(self): time.sleep(30)\n', encoding='utf-8')
        cancel = threading.Event()
        timer = threading.Timer(0.5, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            status, output = _default_runner(self.root, 'tests/test_sleep.py', cancel)
        finally:
            timer.join()
        self.assertEqual(status, 130)
        self.assertIn('중단', output)
        self.assertLess(time.monotonic() - started, 4)

    @unittest.skipUnless(os.name == 'nt', 'Windows child-process cleanup')
    def test_cancel_stops_child_process_too(self):
        import ctypes

        pid_file = self.root / 'child.pid'
        (self.root / 'tests' / 'test_child.py').write_text(
            'import subprocess, sys, time, unittest\nfrom pathlib import Path\n'
            'class ChildTest(unittest.TestCase):\n'
            '    def test_child(self):\n'
            '        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])\n'
            '        Path("child.pid").write_text(str(child.pid))\n'
            '        time.sleep(30)\n', encoding='utf-8')
        cancel = threading.Event()

        def stop_when_child_starts():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not pid_file.exists():
                time.sleep(.02)
            cancel.set()

        trigger = threading.Thread(target=stop_when_child_starts)
        trigger.start()
        try:
            status, _ = _default_runner(self.root, 'tests/test_child.py', cancel)
        finally:
            trigger.join()
        self.assertTrue(pid_file.exists())
        self.assertEqual(status, 130)
        child_pid = int(pid_file.read_text())
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, child_pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                kernel.CloseHandle(handle)

    def test_attempt_limit_stops_repeated_edits(self):
        self.mode.start('jarvis/source.py', 'tests/test_source.py', '목표')
        for attempt in range(3):
            self.mode.next('session')
            self.mode.discarded(self.proposals[-1].proposal_id)
            self.editor.discard(self.proposals[-1].proposal_id)
        result = self.mode.next('session')
        self.assertIn('시도 한도', result)
        self.assertEqual(len(self.proposals), 3)


class DevCheckTests(unittest.TestCase):
    """/dev-check (4단계 3번, 2026-09-30): 사용자가 alex-checks.json 에 적은 이름 붙은 명령만, 셸 없이, 테스트와 같은 한도로."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Storage(self.root / 'data' / 'app.db')
        self.editor = SafeWorkspaceEditor(self.root, self.root / 'backups', storage=self.store)
        self.core = SimpleNamespace(storage=self.store, workspace_editor=self.editor, current_project_root=lambda: self.root)
        self.mode = DeveloperMode(self.core)

    def write_checks(self, checks):
        (self.root / 'alex-checks.json').write_text(json.dumps({'checks': checks}), encoding='utf-8')

    def test_without_the_file_it_explains_how_to_add_one(self):
        self.assertIn('alex-checks.json', self.mode.check(''))

    def test_named_commands_are_listed_and_run_without_a_shell(self):
        self.write_checks({'ok': ['python', '-c', "print('checked ok')"], 'bad': ['python', '-c', 'import sys; sys.exit(3)']})
        self.assertIn('ok: python -c', self.mode.check(''))
        passed = self.mode.check('ok')
        self.assertIn('검사 ok: 통과 (종료 코드 0)', passed)
        self.assertIn('checked ok', passed)
        self.assertIn('검사 bad: 실패 (종료 코드 3)', self.mode.check('bad'))
        with self.assertRaisesRegex(ToolError, '없는 검사 이름'):
            self.mode.check('rm')

    def test_a_shell_string_or_odd_name_is_refused(self):
        self.write_checks({'x': 'python -c 1'})
        with self.assertRaisesRegex(ToolError, '셸 문자열 금지'):
            self.mode.check('x')
        self.write_checks({'a b': ['python']})
        with self.assertRaisesRegex(ToolError, '검사 이름'):
            self.mode.check('')

    def test_a_model_bundle_cannot_create_or_change_the_command_list(self):
        with self.assertRaisesRegex(ToolError, '사용자만'):
            self.editor.create_bundle([{'path': 'alex-checks.json', 'create': '{"checks": {}}'}])
        self.write_checks({'ok': ['python', '-c', '1']})
        with self.assertRaisesRegex(ToolError, '사용자만'):
            self.editor.create_bundle([{'path': 'alex-checks.json', 'edits': [('"ok"', '"evil"')]}])

    def test_a_check_is_attached_only_to_a_run_of_the_same_project(self):
        """Codex 리뷰: 프로젝트를 바꾼 뒤 다른 프로젝트의 작업에 검사 결과가 붙으면 안 된다."""
        from jarvis.developer import read_outcomes
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'test_s.py').write_text('import unittest\n', encoding='utf-8')
        (self.root / 's.py').write_text('x = 1\n', encoding='utf-8')
        self.mode.start('s.py', 'tests/test_s.py', '확인')
        self.write_checks({'ok': ['python', '-c', '1']})
        self.mode.check('ok')
        self.assertEqual(read_outcomes(self.store)[0][1][-1]['type'], 'check')
        other = self.root / 'other'
        other.mkdir()
        (other / 'alex-checks.json').write_text(json.dumps({'checks': {'ok': ['python', '-c', '1']}}), encoding='utf-8')
        self.core.current_project_root = lambda: other
        self.core.workspace_editor = SafeWorkspaceEditor(other, self.root / 'backups2', storage=self.store)
        before = len(read_outcomes(self.store)[0][1])
        self.assertIn('통과', self.mode.check('ok'))
        self.assertEqual(len(read_outcomes(self.store)[0][1]), before)  # 다른 프로젝트의 작업에는 붙이지 않았다

    def test_the_chat_command_reaches_it(self):
        self.write_checks({'ok': ['python', '-c', "print('via chat')"]})
        self.assertIn('via chat', handle_command(self.core, 'session', '/dev-check ok')[0])


class TwoFileProject:
    """jarvis/a.py·b.py 와 둘 다 고쳐야 통과하는 tests/test_ab.py (여러 파일 작업 테스트의 공통 준비)."""

    FIX = ("FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 1\n>>>>>>> REPLACE\n"
           "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 0\n=======\nb = 2\n>>>>>>> REPLACE\n")

    def setUp(self):
        from jarvis.workspace import parse_change_blocks
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'jarvis').mkdir()
        (self.root / 'tests').mkdir()
        (self.root / 'jarvis' / 'a.py').write_text('a = 0\n', encoding='utf-8')
        (self.root / 'jarvis' / 'b.py').write_text('b = 0\n', encoding='utf-8')
        (self.root / 'tests' / 'test_ab.py').write_text(
            'import sys, unittest\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis"))\n'
            'from a import a\nfrom b import b\n'
            'class ABTests(unittest.TestCase):\n'
            '    def test_ab(self): self.assertEqual((a, b), (1, 2))\n', encoding='utf-8')
        self.store = Storage(self.root / 'data' / 'app.db')
        self.editor = SafeWorkspaceEditor(self.root, self.root / 'backups', storage=self.store)
        self.blocks = self.FIX
        self.seen_files = []

        self.seen_instructions = []

        def propose_bundle(_session, files, instruction):
            self.seen_files.append(list(files))
            self.seen_instructions.append(instruction)
            return self.editor.create_bundle(parse_change_blocks(self.blocks))

        self.core = SimpleNamespace(
            storage=self.store, workspace_editor=self.editor, current_project_root=lambda: self.root,
            propose_bundle_local=propose_bundle, propose_file_edit_local=None,
            record_edit_experience=lambda *args, **kwargs: None,
        )
        self.mode = DeveloperMode(self.core)

    def read(self, name):
        return (self.root / 'jarvis' / name).read_text(encoding='utf-8')


class MultiFileDeveloperTests(TwoFileProject, unittest.TestCase):
    """여러 파일 개발 작업은 묶음 수정안을 쓴다(ADR-065, 2026-09-26 claude). 테스트는 합격 기준이라 고칠 수 없다."""

    def test_two_files_are_fixed_by_one_approved_bundle(self):
        started = self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.assertIn('수정 대상: jarvis', started)
        first = self.mode.next('session')
        self.assertIn('묶음 수정안 — 파일 2개', first)
        self.assertEqual(len(self.seen_files[-1]), 2)  # 모델은 두 파일을 다 받았다
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))
        proposal_id = re.search(r'(?m)^편집 제안 ID: ([0-9a-f]{12})$', first).group(1)
        self.assertIn('묶음 수정안', self.mode.next('session'))  # 대기 중인 묶음을 다시 보여 준다
        answer, _, _ = handle_command(self.core, 'session', f'/apply-edit {proposal_id} --yes')
        self.assertIn('묶음 적용 완료', answer)
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertIn('테스트 통과', self.mode.next('session'))
        (self.root / 'jarvis' / 'b.py').write_text('b = 3\n', encoding='utf-8')  # 둘째 파일만 바뀌어도 통과 상태는 취소된다
        self.assertIn('needs_test', self.mode.status())

    def test_a_bundle_that_touches_the_test_is_discarded(self):
        self.blocks = self.FIX + ("FILE: tests/test_ab.py\n<<<<<<< SEARCH\n(a, b), (1, 2)\n=======\n(a, b), (0, 0)\n"
                                  ">>>>>>> REPLACE\n")
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        with self.assertRaisesRegex(ToolError, '작업 범위 밖'):
            self.mode.next('session')
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertIn('(1, 2)', (self.root / 'tests' / 'test_ab.py').read_text(encoding='utf-8'))
        self.assertEqual(self.read('a.py'), 'a = 0\n')

    def test_start_refuses_tests_duplicates_and_too_many_files(self):
        with self.assertRaisesRegex(ToolError, '합격 기준'):
            self.mode.start('jarvis/a.py, tests/test_ab.py', 'tests/test_ab.py', '목표')
        with self.assertRaisesRegex(ToolError, '두 번'):
            self.mode.start('jarvis/a.py, jarvis/a.py', 'tests/test_ab.py', '목표')
        with self.assertRaisesRegex(ToolError, '1~8'):
            self.mode.start(', '.join(['jarvis/a.py'] * 9), 'tests/test_ab.py', '목표')

    def test_every_acceptance_test_must_pass(self):
        (self.root / 'tests' / 'test_a_only.py').write_text(
            'import sys, unittest\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis"))\n'
            'from a import a\nclass ATests(unittest.TestCase):\n'
            '    def test_a(self): self.assertEqual(a, 1)\n', encoding='utf-8')
        (self.root / 'tests' / 'test_b_zero.py').write_text(
            'import sys, unittest\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis"))\n'
            'from b import b\nclass BTests(unittest.TestCase):\n'
            '    def test_b(self): self.assertEqual(b, 0)\n', encoding='utf-8')
        started = self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py, tests/test_a_only.py', '둘 다 고쳐')
        self.assertIn('테스트: tests/test_ab.py, tests/test_a_only.py', started)
        first = self.mode.next('session')
        self.assertIn('=== tests/test_ab.py', self.seen_instructions[-1])  # 모델은 두 테스트의 결과를 모두 받는다
        self.assertIn('=== tests/test_a_only.py', self.seen_instructions[-1])
        proposal_id = re.search(r'(?m)^편집 제안 ID: ([0-9a-f]{12})$', first).group(1)
        handle_command(self.core, 'session', f'/apply-edit {proposal_id} --yes')
        self.assertIn('테스트 통과: tests/test_ab.py, tests/test_a_only.py', self.mode.next('session'))
        # 하나만 실패해도 실패다: b 를 2 로 고친 지금 test_b_zero 는 실패하고 test_a_only 는 통과한다
        self.mode.start('jarvis/b.py', 'tests/test_b_zero.py, tests/test_a_only.py', '확인')
        code, output = self.mode._run_tests(self.root, DeveloperRun.load(self.store))
        self.assertNotEqual(code, 0)
        self.assertIn('=== tests/test_b_zero.py: 종료 코드 1', output)
        self.assertIn('=== tests/test_a_only.py: 종료 코드 0', output)

    def test_start_refuses_repeated_or_too_many_tests(self):
        with self.assertRaisesRegex(ToolError, '두 번'):
            self.mode.start('jarvis/a.py', 'tests/test_ab.py, tests/test_ab.py', '목표')
        with self.assertRaisesRegex(ToolError, '1~5'):
            self.mode.start('jarvis/a.py', ', '.join(['tests/test_ab.py'] * 6), '목표')

    def test_runs_saved_before_multi_file_support_still_load(self):
        import json
        old = {'goal': '옛 작업', 'workspace': str(self.root), 'source': 'jarvis/a.py', 'test': 'tests/test_ab.py',
               'phase': 'needs_test', 'run_id': 'abc123abc123'}
        self.store.set_state('developer.active', json.dumps(old))
        run = DeveloperRun.load(self.store)
        self.assertEqual(run.files(), ['jarvis/a.py'])


class AutoLoopTests(TwoFileProject, unittest.TestCase):
    """/dev-auto — 격리 복사본에서 테스트→수정→재시험을 최대 3회. 실제 파일은 최종 묶음을 사용자가 승인할 때만 바뀐다."""

    WRONG = ("FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 1\n>>>>>>> REPLACE\n"
             "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 0\n=======\nb = 3\n>>>>>>> REPLACE\n")
    SECOND = "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 3\n=======\nb = 2\n>>>>>>> REPLACE\n"

    def setUp(self):
        super().setUp()
        from jarvis.workspace import parse_change_blocks
        self.script = [self.WRONG, self.SECOND]
        self.instructions = []
        self.editors = []

        def propose_bundle(_session, files, instruction, editor=None, record=True):
            self.instructions.append(instruction)
            self.editors.append(editor)
            text = self.script[min(len(self.instructions), len(self.script)) - 1]
            return (editor or self.editor).create_bundle(parse_change_blocks(text))

        self.core.propose_bundle_local = propose_bundle

    def test_the_loop_fixes_a_copy_and_offers_one_final_bundle(self):
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = self.mode.auto('session')
        self.assertIn('1회차 테스트: 실패', reply)
        self.assertIn('3회차 테스트: 통과', reply)
        self.assertIsNot(self.editors[0], self.editor)  # 모델의 수정안은 복사본 편집기에 만들어졌다
        # 실패한 테스트 코드를 읽기 전용 참고로 받는다(2026-09-29) — 트레이스백이 테스트 파일에서 끝나도 기대값을 안다
        self.assertIn('실패한 테스트 코드(읽기 전용', self.instructions[0])
        self.assertIn('self.assertEqual((a, b), (1, 2))', self.instructions[0])
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))  # 실제 파일은 그대로
        run = DeveloperRun.load(self.store)
        self.assertEqual(run.phase, 'awaiting_approval')
        final = self.editor.get_bundle(run.proposal_id)
        self.assertEqual(sorted(c.relative_path.replace(os.sep, '/') for c in final.changes), ['jarvis/a.py', 'jarvis/b.py'])
        self.assertIn(f'편집 제안 ID: {run.proposal_id}', reply)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 1\n', 'b = 2\n'))
        self.assertIn('테스트 통과', self.mode.next('session'))

    def test_no_proposal_when_the_loop_never_passes(self):
        self.script = [self.WRONG, "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 1\n=======\na = 5\n>>>>>>> REPLACE\n",
                       "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 5\n=======\na = 6\n>>>>>>> REPLACE\n"]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = self.mode.auto('session')
        self.assertIn('통과시키지 못했습니다', reply)
        self.assertEqual(len(self.instructions), 3)  # 최대 3번 고쳤다
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))

    # 독립 풀이(ADR-077): 한 풀이가 3회차 안에 통과하지 못하면 복사본을 처음 상태로 되돌려 새로 푼다.
    A15 = "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 1\n=======\na = 5\n>>>>>>> REPLACE\n"
    A56 = "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 5\n=======\na = 6\n>>>>>>> REPLACE\n"

    def test_a_second_independent_attempt_starts_from_the_original(self):
        self.script = [self.WRONG, self.A15, self.A56, self.FIX]  # FIX 의 찾을 부분은 원본(a = 0, b = 0)에만 맞는다
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = self.mode.auto('session', attempts=2)
        self.assertIn('2번째 독립 풀이', reply)
        # 앞 풀이의 실패·피드백 없이 같은 출발점 — 실행 시간 줄('Ran 1 test in 0.002s')은 CPU 부하마다 달라 빼고 비교한다
        timing = re.compile(r"Ran \d+ tests? in [\d.]+s")
        self.assertEqual(timing.sub("", self.instructions[3]), timing.sub("", self.instructions[0]))
        run = DeveloperRun.load(self.store)
        self.assertEqual(run.phase, 'awaiting_approval')
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 1\n', 'b = 2\n'))
        auto = next(e for e in read_outcomes(self.store, 1)[0][1] if e.get('type') == 'auto')
        self.assertEqual((auto['attempts'], auto['result']), (2, 'copy_passed'))

    def test_a_file_made_by_a_failed_attempt_is_not_carried_over(self):
        helper = ("NEW FILE: jarvis/helper.py\n<<<<<<< CONTENT\nx = 1\n>>>>>>> END\n"
                  "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 5\n>>>>>>> REPLACE\n")
        self.script = [helper, self.A56, "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 6\n=======\na = 7\n>>>>>>> REPLACE\n", self.FIX]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.mode.auto('session', attempts=2)
        final = self.editor.get_bundle(DeveloperRun.load(self.store).proposal_id)
        self.assertEqual(sorted(c.relative_path.replace(os.sep, '/') for c in final.changes), ['jarvis/a.py', 'jarvis/b.py'])

    def test_one_attempt_by_default_and_the_setting_raises_it(self):
        self.script = [self.WRONG, self.A15, self.A56] * 2
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.core.coding_auto_attempts = 2
        reply = self.mode.auto('session')
        self.assertIn('독립 풀이 2번 모두', reply)
        self.assertEqual(len(self.instructions), 6)  # 풀이마다 최대 3번
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))

    # 회귀 검사(ADR-078): 합격 테스트가 통과해도 복사본 전체 테스트에서 새로 깨진 것이 있으면 그 실패를 주고 다시 고치게 한다.
    EXTRA = "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 0\n=======\nb = 2\nextra = 1\n>>>>>>> REPLACE\n"
    NO_EXTRA = "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 2\nextra = 1\n=======\nb = 2\n>>>>>>> REPLACE\n"

    def guard_test(self, body):
        (self.root / 'tests' / 'test_guard.py').write_text(
            'import sys, unittest\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis"))\n'
            'import b\nclass GuardTests(unittest.TestCase):\n'
            f'    def test_guard(self): {body}\n', encoding='utf-8')

    def a_fix_that_also_adds_extra(self):
        return "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 1\n>>>>>>> REPLACE\n" + self.EXTRA

    def test_a_fix_that_breaks_another_test_is_sent_back(self):
        self.guard_test('self.assertFalse(hasattr(b, "extra"))')  # 수정 전에는 통과한다
        self.script = [self.a_fix_that_also_adds_extra(), self.NO_EXTRA]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.core.coding_regression_check = True
        reply = self.mode.auto('session')
        self.assertIn('전체 검사에서 새 실패 1개', reply)
        self.assertIn('test_guard', reply)
        self.assertIn('다른 테스트를 깨뜨렸다', self.instructions[1])
        self.assertIn('통과 · 전체 테스트 통과', reply)
        run = DeveloperRun.load(self.store)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 1\n', 'b = 2\n'))

    def test_without_the_check_the_breaking_fix_passes_as_before(self):
        self.guard_test('self.assertFalse(hasattr(b, "extra"))')
        self.script = [self.a_fix_that_also_adds_extra()]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = self.mode.auto('session')  # 기본은 꺼짐 — 예전과 같다
        self.assertNotIn('전체 검사', reply)
        self.assertEqual(len(self.instructions), 1)
        self.assertEqual(DeveloperRun.load(self.store).phase, 'awaiting_approval')
        auto = next(e for e in read_outcomes(self.store, 1)[0][1] if e.get('type') == 'auto')
        self.assertEqual((auto['verification_status'], auto['verification_scope']), ('verified', 'selected_tests'))

    def test_a_test_that_already_failed_is_not_blamed_on_the_fix(self):
        self.guard_test('self.fail("원래부터 실패")')
        self.script = [self.FIX]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.core.coding_regression_check = True
        reply = self.mode.auto('session')
        # 수정 전에는 합격 테스트(test_ab)와 원래 실패(test_guard) 둘이 실패했다
        # 고친 합격 테스트는 빠지고 원래 실패(test_guard) 하나만 남는다(Codex Luna 제안 A — 예전 문구는 수정 전 개수를 셌다)
        self.assertIn('2회차 테스트: 통과 · 새로 실패한 테스트 ID 없음(수정 전부터 실패하던 1개는 그대로)', reply)
        self.assertNotIn('전체 테스트 통과', reply)  # 원래 실패가 있으니 '전부 통과'라고 말하지 않는다
        self.assertEqual(len(self.instructions), 1)

    def test_trimming_that_breaks_another_test_is_undone(self):
        # offset 은 합격 테스트(test_ab)에는 필요 없지만 b + offset == 2 를 보는 test_guard 를 지킨다 — 덜어내기가 빼면 깨진다
        (self.root / 'jarvis' / 'b.py').write_text('b = 0\n# 설명\n# 설명\noffset = 2\n', encoding='utf-8')
        self.guard_test('self.assertEqual(b.b + b.offset, 2)')
        both = ("FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 1\n>>>>>>> REPLACE\n"
                "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 0\n=======\nb = 2\n>>>>>>> REPLACE\n"
                "FILE: jarvis/b.py\n<<<<<<< SEARCH\noffset = 2\n=======\noffset = 0\n>>>>>>> REPLACE\n")
        self.script = [both]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.core.coding_regression_check = True
        reply = self.mode.auto('session')
        self.assertIn('검증된 이전 후보로 되돌림', reply)
        run = DeveloperRun.load(self.store)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.assertIn('offset = 0', self.read('b.py'))

    def test_trimming_that_unfixes_a_test_broken_since_before_is_undone(self):
        # 2026-10-05 역사 gpumodel_config(Q3): 버그 주입 때부터 실패하던 config 테스트를 모델이 함께 고쳤는데, 합격 테스트에는
        # 필요 없다며 덜어냈다. 그 테스트는 '수정 전부터 실패' 라서 새 실패로 안 잡혔다 — 덜어내기 전과 비교해야 한다.
        (self.root / 'jarvis' / 'b.py').write_text('b = 0\n# 설명\n# 설명\nextra_ok = False\n', encoding='utf-8')
        self.guard_test('self.assertTrue(b.extra_ok)')  # 수정 전부터 실패
        fix_all = ("FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 1\n>>>>>>> REPLACE\n"
                   "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 0\n=======\nb = 2\n>>>>>>> REPLACE\n"
                   "FILE: jarvis/b.py\n<<<<<<< SEARCH\nextra_ok = False\n=======\nextra_ok = True\n>>>>>>> REPLACE\n")
        self.script = [fix_all]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.core.coding_regression_check = True
        reply = self.mode.auto('session')
        self.assertIn('덜어내지 않은 검증된 후보로 되돌림', reply)
        self.assertIn('test_guard', reply)
        run = DeveloperRun.load(self.store)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.assertIn('extra_ok = True', self.read('b.py'))

    def test_an_out_of_scope_round_is_refused_and_the_model_is_told(self):
        touch_test = self.FIX + "FILE: tests/test_ab.py\n<<<<<<< SEARCH\n(1, 2)\n=======\n(0, 0)\n>>>>>>> REPLACE\n"
        self.script = [touch_test, self.FIX]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = self.mode.auto('session')
        self.assertIn('수정안 거부', reply)
        self.assertIn('직전 수정안은 적용되지 않았다', self.instructions[1])
        self.assertIn('통과', reply)
        self.assertIn('(1, 2)', (self.root / 'tests' / 'test_ab.py').read_text(encoding='utf-8'))

    def test_a_real_file_changed_during_the_loop_is_not_overwritten(self):
        original = self.core.propose_bundle_local

        def edit_real_file_meanwhile(*args, **kwargs):
            (self.root / 'jarvis' / 'a.py').write_text('a = 9\n', encoding='utf-8')
            return original(*args, **kwargs)

        self.core.propose_bundle_local = edit_real_file_meanwhile
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        with self.assertRaisesRegex(ToolError, '바뀌었습니다'):
            self.mode.auto('session')
        self.assertEqual(self.read('a.py'), 'a = 9\n')
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')

    def test_missing_selected_test_in_copy_stops_without_calling_model(self):
        from jarvis import developer
        (self.root / 'tests/__init__.py').write_text('', encoding='utf-8')
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        with mock.patch.object(developer, 'AUTO_FILE_LIMIT', 80):
            with self.assertRaisesRegex(ToolError, '테스트 파일'):
                self.mode.auto('session')
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual(self.instructions, [])
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))

    def test_stale_source_in_copy_stops_without_calling_model(self):
        from jarvis import developer
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        original_copy = developer._isolated_copy

        def changed_copy(root, target, skipped=None):
            size = original_copy(root, target, skipped)
            (target / 'jarvis/a.py').write_text('a = 9\n', encoding='utf-8')
            return size

        with mock.patch.object(developer, '_isolated_copy', changed_copy):
            with self.assertRaisesRegex(ToolError, '바뀌었습니다'):
                self.mode.auto('session')
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual(self.instructions, [])
        self.assertEqual(self.read('a.py'), 'a = 0\n')

    def test_changed_acceptance_test_in_copy_stops_before_model_call(self):
        from jarvis import developer
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        original_copy = developer._isolated_copy

        def changed_copy(root, target, skipped=None):
            size = original_copy(root, target, skipped)
            (target / 'tests/test_ab.py').write_text('pass\n', encoding='utf-8')
            return size

        with mock.patch.object(developer, '_isolated_copy', changed_copy):
            with self.assertRaisesRegex(ToolError, '합격 기준 테스트가 바뀌었습니다'):
                self.mode.auto('session')
        self.assertEqual(self.instructions, [])
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')

    def test_test_that_rewrites_itself_cannot_make_auto_pass(self):
        def mutating_runner(root, test):
            (root / test).write_text('pass\n', encoding='utf-8')
            return 0, 'Ran 1 test in 0.001s\nOK'

        mode = DeveloperMode(self.core, runner=mutating_runner)
        mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        with self.assertRaisesRegex(ToolError, '테스트가 실행 중 변경되었습니다'):
            mode.auto('session')
        self.assertEqual(self.instructions, [])
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')

    def test_changed_real_acceptance_test_prevents_final_proposal(self):
        original = self.core.propose_bundle_local

        def change_real_test_during_model_call(*args, **kwargs):
            (self.root / 'tests/test_ab.py').write_text('pass\n', encoding='utf-8')
            return original(*args, **kwargs)

        self.core.propose_bundle_local = change_real_test_during_model_call
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        with self.assertRaisesRegex(ToolError, '실제 프로젝트의 합격 기준 테스트가 바뀌었습니다'):
            self.mode.auto('session')
        self.assertIsNone(self.editor.get_bundle(DeveloperRun.load(self.store).proposal_id))
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')

    def test_every_round_and_the_user_decision_are_recorded_as_one_outcome(self):
        """Codex 조사 1순위(2026-09-30): 회차별 시험·수정안·적용·전체 검사를 증거로 남기고 상태를 종료 코드로만 정한다."""
        from jarvis.developer import OUTCOMES_FILE, outcome_status, read_outcomes
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.mode.auto('session')
        run = DeveloperRun.load(self.store)
        (run_id, events), = read_outcomes(self.store)
        self.assertEqual(run_id, run.run_id)
        auto = events[-1]
        self.assertEqual((auto['type'], auto['result'], auto['proposal_id']), ('auto', 'copy_passed', run.proposal_id))
        self.assertEqual([r['exit'] for r in auto['rounds']], [1, 1, 0])
        self.assertIn('applied', auto['rounds'][0])
        self.assertIn('AssertionError', auto['rounds'][0]['failure'])
        self.assertEqual(outcome_status(events), '미검증')
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.mode.next('session')
        self.assertEqual(outcome_status(read_outcomes(self.store)[0][1]), '선택 검사 통과')
        self.mode.verify()
        self.assertEqual(outcome_status(read_outcomes(self.store)[0][1]), '사용자 채택')
        summary = handle_command(self.core, 'session', '/dev-outcomes')[0]
        self.assertIn(run.run_id, summary)
        self.assertIn('사용자 채택', summary)
        self.assertTrue((self.root / 'data' / OUTCOMES_FILE).is_file())

    def test_a_newly_applied_fix_does_not_inherit_the_old_pass(self):
        """Codex 리뷰 R01: 전체 검사 통과 뒤 새 수정안을 적용하면, 다시 재기 전까지 '사용자 채택' 이 아니다."""
        from jarvis.developer import outcome_status, read_outcomes
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.mode.auto('session')
        run = DeveloperRun.load(self.store)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.mode.next('session')
        self.mode.verify()
        self.assertEqual(outcome_status(read_outcomes(self.store)[0][1]), '사용자 채택')
        self.core.propose_bundle_local = lambda *_a, **_k: self.editor.create_bundle(
            [{"path": "jarvis/b.py", "edits": [("b = 2\n", "b = 2  # 다시 손봄\n")]}])
        reply = self.mode.propose('session')
        new_id = re.search(r"^편집 제안 ID: ([0-9a-f]{12})$", reply, re.M).group(1)
        handle_command(self.core, 'session', f'/apply-edit {new_id} --yes')
        self.assertEqual(outcome_status(read_outcomes(self.store)[0][1]), '미검증')  # 옛 통과를 이어받지 않는다
        self.mode.next('session')
        self.assertEqual(outcome_status(read_outcomes(self.store)[0][1]), '선택 검사 통과')  # 전체 검사는 새 버전으로 다시

    def test_manual_revision_pass_is_not_reported_as_an_approved_fix(self):
        """An applied proposal cannot claim a later, manually edited revision."""
        from jarvis.developer import outcome_status
        events = [
            {'type': 'applied', 'digest': 'approved'},
            {'type': 'tests', 'digest': 'approved', 'exit': 0},
            {'type': 'suite', 'digest': 'approved', 'result': 'PASS'},
            {'type': 'tests', 'digest': 'manual', 'exit': 0},
            {'type': 'suite', 'digest': 'manual', 'result': 'PASS'},
        ]
        self.assertEqual(outcome_status(events, 'manual'), '전체 검사 통과')
        self.assertEqual(outcome_status(events, 'approved'), '사용자 채택')
        # Applying a new revision invalidates all earlier verification, even
        # if its contents happen to match an older revision.
        events.append({'type': 'applied', 'digest': 'approved'})
        self.assertEqual(outcome_status(events, 'approved'), '미검증')

    def test_a_pass_while_the_source_changed_is_not_counted(self):
        """Codex 리뷰 R02: 테스트 중 대상 파일이 바뀌면 종료 코드 0 이어도 통과로 세지 않는다."""
        from jarvis.developer import outcome_status, read_outcomes

        def edits_while_testing(root, test):
            (root / 'jarvis' / 'a.py').write_text('a = 5\n', encoding='utf-8')
            return 0, 'Ran 1 test\nOK'

        mode = DeveloperMode(self.core, runner=edits_while_testing)
        mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.assertIn('변경되었습니다', mode.next('session'))
        events = read_outcomes(self.store)[0][1]
        self.assertIs(events[-1]['valid'], False)
        self.assertEqual(outcome_status(events), '미검증')

    def test_old_records_without_a_fingerprint_are_not_this_versions_pass(self):
        """Codex 2차 R2-02: 지문 없는 옛 통과는 지금 파일의 지문을 알 때 지금 버전의 통과가 아니다. 파일을 못 읽으면 미검증."""
        from jarvis.developer import outcome_status
        legacy = [{"type": "applied"}, {"type": "tests", "exit": 0}, {"type": "suite", "result": "PASS"}]
        self.assertEqual(outcome_status(legacy), "사용자 채택")  # 지문 없는 옛 기록만 있을 때의 역사 보기
        self.assertEqual(outcome_status(legacy, "B"), "미검증")
        current = [{"type": "tests", "exit": 0, "digest": "A"}]
        self.assertEqual(outcome_status(current, current_unreadable=True), "미검증")

    def test_outcomes_say_unverified_when_the_active_files_cannot_be_read(self):
        from jarvis.developer import read_outcomes
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        self.mode.auto('session')
        run = DeveloperRun.load(self.store)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.mode.next('session')
        self.assertIn('선택 검사 통과', self.mode.outcomes())
        (self.root / 'jarvis' / 'a.py').unlink()
        self.assertIn('미검증', self.mode.outcomes())
        self.assertTrue(read_outcomes(self.store))

    def test_a_bounded_run_honours_a_shorter_limit(self):
        """Codex 2차 E2-02: 전체 제한의 남은 시간을 개별 검사에 넘길 수 있다."""
        from jarvis.developer import _run_bounded
        started = time.monotonic()
        code, output = _run_bounded([sys.executable, '-c', 'import time; time.sleep(30)'], self.root, None, '검사', limit_seconds=1)
        self.assertEqual(code, 124)
        self.assertIn('시간 제한(1초)', output)
        self.assertLess(time.monotonic() - started, 20)

    def test_a_record_that_cannot_be_written_does_not_stop_the_work(self):
        from jarvis.developer import record_outcome
        blocker = self.root / 'not-a-dir'
        blocker.write_text('x', encoding='utf-8')
        record_outcome(SimpleNamespace(db_path=blocker / 'app.db'), {'type': 'tests', 'run_id': 'x'})  # 예외 없이 지나간다

    def test_changes_the_tests_do_not_need_are_trimmed_but_notes_stay(self):
        """2026-09-27 실제 ALEX 코드 평가: 실패한 회차의 군더더기가 최종 수정안에 남았다. 되돌려도 통과하는 덩어리는 뺀다."""
        (self.root / 'jarvis' / 'a.py').write_text('a = 0\n\n\ndef unchanged():\n    return 1\n', encoding='utf-8')
        self.script = [
            "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 0\n=======\na = 1\n>>>>>>> REPLACE\n"
            "<<<<<<< SEARCH\ndef unchanged():\n    return 1\n=======\n# 설명 주석\ndef unchanged():\n    return int('1')\n>>>>>>> REPLACE\n"
            "FILE: jarvis/b.py\n<<<<<<< SEARCH\nb = 0\n=======\nb = 3\n>>>>>>> REPLACE\n",
            self.SECOND]
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = self.mode.auto('session')
        self.assertIn('필요 없는 변경 1곳을 덜어냄', reply)
        run = DeveloperRun.load(self.store)
        handle_command(self.core, 'session', f'/apply-edit {run.proposal_id} --yes')
        self.assertEqual(self.read('a.py'), "a = 1\n\n\n# 설명 주석\ndef unchanged():\n    return 1\n")  # 주석은 남고 군더더기는 빠졌다
        self.assertEqual(self.read('b.py'), 'b = 2\n')
        self.assertIn('테스트 통과', self.mode.next('session'))

    def test_the_copy_skips_windows_device_names_and_unreadable_files(self):
        """2026-09-26: ALEX 폴더의 셸 리다이렉트 흔적 `NUL` 파일 때문에 복사가 멈췄다."""
        from unittest import mock
        from jarvis import developer
        target = self.root.parent / (self.root.name + '-copy2')
        self.addCleanup(shutil.rmtree, target, True)
        real_copy, skipped = shutil.copy2, []

        def copy2(src, dst, *a, **k):
            if Path(src).name == 'b.py':
                raise OSError('locked')
            return real_copy(src, dst, *a, **k)

        with mock.patch.object(developer.shutil, 'copy2', side_effect=copy2):
            developer._isolated_copy(self.root, target, skipped)
        self.assertTrue((target / 'jarvis' / 'a.py').is_file())
        self.assertEqual([Path(p).name for p in skipped], ['b.py'])
        self.assertIn('NUL', developer._WINDOWS_DEVICE_NAMES)

    def test_the_copy_leaves_out_heavy_and_hidden_folders(self):
        from jarvis.developer import _isolated_copy
        for folder in ('runtime', 'logs', '.git', 'node_modules'):
            (self.root / folder).mkdir(exist_ok=True)
            (self.root / folder / 'big.bin').write_bytes(b'x' * 10)
        target = self.root.parent / (self.root.name + '-copy')
        self.addCleanup(shutil.rmtree, target, True)
        _isolated_copy(self.root, target)
        self.assertTrue((target / 'jarvis' / 'a.py').is_file())
        self.assertTrue((target / 'tests' / 'test_ab.py').is_file())
        for folder in ('runtime', 'logs', '.git', 'node_modules', 'data'):
            self.assertFalse((target / folder).exists(), folder)


if __name__ == '__main__':
    unittest.main()


class RegressionVerdictTests(TwoFileProject, unittest.TestCase):
    """회귀 검사 판정(ADR-078 보충, Codex 가이드 2026-10-04 우선순위 1): 미검증·취소를 통과로 올리지 않는다, 불완전한 실패 목록으로 빈 집합 통과 금지.

    합격 테스트는 진짜로 돌리고(작은 복사본), 전체 테스트만 정해 둔 결과를 돌려주는 가짜로 바꾼다."""

    OK = "test_ab (test_ab.ABTests.test_ab) ... ok\n\n" + "-" * 70 + "\nRan 2 tests in 0.010s\n\nOK\n"
    GUARD = ("=" * 70 + "\nFAIL: test_guard (test_guard.GuardTests.test_guard)\n" + "-" * 70
             + "\nTraceback (most recent call last):\nAssertionError: 1 != 2\n\n" + "-" * 70 + "\nRan 2 tests in 0.010s\n\nFAILED (failures=1)\n")
    CUT = "...\nAssertionError: 3\n\n" + "-" * 70 + "\nRan 9 tests in 0.200s\n\nFAILED (failures=3)\n"  # 머리글 셋이 출력 앞쪽에서 잘렸다
    KEEP_A = "FILE: jarvis/a.py\n<<<<<<< SEARCH\na = 1\n=======\na = 1  # 확인\n>>>>>>> REPLACE\n"

    def setUp(self):
        super().setUp()
        from jarvis.workspace import parse_change_blocks
        self.instructions, self.suite_calls = [], 0
        self.script = [self.FIX]

        def propose_bundle(_session, files, instruction, editor=None, record=True):
            self.instructions.append(instruction)
            text = self.script[min(len(self.instructions), len(self.script)) - 1]
            return (editor or self.editor).create_bundle(parse_change_blocks(text))

        self.core.propose_bundle_local = propose_bundle
        self.core.coding_regression_check = True
        self.core.command_cancel_event = threading.Event()

    def suite(self, *results, cancel_at=None):
        queue = list(results)

        def fake(_root):
            self.suite_calls += 1
            if cancel_at == self.suite_calls:
                self.core.command_cancel_event.set()
            return queue.pop(0) if queue else (0, self.OK)
        self.mode = DeveloperMode(self.core, suite_runner=fake)
        self.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')

    def auto_record(self):
        return next(e for e in read_outcomes(self.store, 1)[0][1] if e.get('type') == 'auto')

    def test_an_unfinished_suite_is_not_a_pass_and_no_more_model_calls(self):
        for code in (124, 125, 126):
            with self.subTest(code=code):
                self.setUp()
                self.suite((code, "시간 초과"))
                reply = self.mode.auto('session')
                self.assertIn('통과로 인정하지 않았습니다', reply)
                self.assertEqual(len(self.instructions), 1)  # 다시 고치거나 새로 풀지 않는다
                self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
                record = self.auto_record()
                self.assertEqual((record['result'], record['verification_status'], record['verification_scope']),
                                 ('not_passed', 'unverified', 'full_suite'))

    def test_an_unfinished_baseline_is_not_a_pass(self):
        self.suite((1, self.GUARD), (124, ""))
        reply = self.mode.auto('session')
        self.assertIn('수정 전 전체 검사가 끝나지 못했다', reply)
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')

    def test_a_cancel_during_the_suite_stops_without_a_proposal(self):
        for results, cancel_at in (([(130, "")], None), ([(0, self.OK)], 1), ([(1, self.GUARD), (0, self.OK)], 2)):
            with self.subTest(results=results[0][0], cancel_at=cancel_at):
                self.setUp()
                self.suite(*results, cancel_at=cancel_at)
                with self.assertRaisesRegex(ToolError, '중단'):
                    self.mode.auto('session')
                run = DeveloperRun.load(self.store)
                self.assertEqual((run.phase, run.proposal_id), ('needs_test', ''))
                self.assertEqual(self.auto_record()['verification_status'], 'cancelled')
                self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))

    def test_cut_failure_names_against_a_failing_baseline_are_unverified(self):
        self.suite((1, self.CUT), (1, self.GUARD))
        reply = self.mode.auto('session')
        self.assertIn('실패 목록이 완전하지 않다', reply)
        self.assertIn('통과로 인정하지 않았습니다', reply)

    def test_an_unreadable_failure_after_a_clean_baseline_is_sent_back(self):
        self.script = [self.FIX, self.KEEP_A]
        self.suite((1, "깨진 출력"), (0, self.OK), (0, self.OK))
        reply = self.mode.auto('session')
        self.assertIn('전체 검사에서 새 실패 1개 — (실패한 테스트 이름을 읽지 못함', reply)
        self.assertIn('다른 테스트를 깨뜨렸다', self.instructions[1])
        self.assertEqual(DeveloperRun.load(self.store).phase, 'awaiting_approval')

    def test_the_same_complete_failures_before_and_after_pass_without_claiming_all_green(self):
        self.suite((1, self.GUARD), (1, self.GUARD))
        reply = self.mode.auto('session')
        self.assertIn('새로 실패한 테스트 ID 없음(수정 전부터 실패하던 1개는 그대로)', reply)
        self.assertNotIn('전체 테스트 통과', reply)
        self.assertEqual(self.auto_record()['verification_status'], 'verified')

    def test_a_cancel_right_after_the_final_proposal_discards_it(self):
        self.suite()  # 전체 검사 통과
        create = self.editor.create_bundle

        def create_then_cancel(*args, **kwargs):
            bundle = create(*args, **kwargs)
            self.core.command_cancel_event.set()
            self.made = bundle.proposal_id
            return bundle

        self.editor.create_bundle = create_then_cancel
        with self.assertRaisesRegex(ToolError, '중단'):
            self.mode.auto('session')
        self.assertEqual(DeveloperRun.load(self.store).phase, 'needs_test')
        self.assertEqual(self.auto_record()['verification_status'], 'cancelled')
        with self.assertRaises(ToolError):  # 만든 수정안은 버려졌다 — 적용할 수 없다
            self.editor.apply_bundle(self.made)
        self.assertEqual((self.read('a.py'), self.read('b.py')), ('a = 0\n', 'b = 0\n'))


class SuiteReportTests(unittest.TestCase):
    def report(self, code, output):
        from jarvis.developer import _suite_report
        return _suite_report(code, output)

    def test_subtest_arguments_and_descriptions_are_part_of_identity(self):
        before = "FAIL: test_x (demo.Case.test_x) [edge] (value='(old)')\nRan 1 test in 0.001s\nFAILED (failures=1)\n"
        after = before.replace("'(old)'", "'(new)'")
        first, second = self.report(1, before), self.report(1, after)
        self.assertEqual((first[0], second[0]), ('failed', 'failed'))
        self.assertNotEqual(first[1], second[1])
        self.assertIn("[edge] (value='(old)')", next(iter(first[1])))

    def test_unexpected_success_has_a_distinct_failed_identity(self):
        name = 'test_x (demo.Case.test_x)'
        normal = self.report(1, f'FAIL: {name}\nRan 1 test in 0.001s\nFAILED (failures=1)\n')
        unexpected = self.report(1, f'UNEXPECTED SUCCESS: {name}\nRan 1 test in 0.001s\nFAILED (unexpected successes=1)\n')
        self.assertEqual(unexpected[0], 'failed')
        self.assertNotEqual(normal[1], unexpected[1])

    def test_incomplete_unknown_or_inconsistent_summaries_are_unreadable(self):
        for code, output in (
            (1, 'Ran 1 test in 0.001s\nFAILED (failures=1)\n'),
            (1, 'Ran 1 test in 0.001s\nFAILED (future failures=1)\n'),
            (1, 'Ran 1 test in 0.001s\nFAILED (failures=0)\n'),
            (1, 'Ran 1 test in 0.001s\nFAILED (failures=1, failures=1)\n'),
            (1, 'Ran 1 test in 0.001s\nFAILED (failures=oops)\n'),
            (1, 'FAIL: test_x (demo.Case.test_x)\nRan 1 test in 0.001s\nFAILED (errors=1)\n'),
            (1, 'Ran 1 test in 0.001s\nFAILED (unexpected successes=1)\n'),
            (0, 'Ran 1 test in 0.001s\nFAILED (failures=1)\n'),
            (1, 'Ran 1 test in 0.001s\nOK\n'),
            (0, 'Ran 1 test in 0.001s\nOK\ntrailing broken output\n'),
            (0, 'Ran 0 tests in 0.001s\nOK\n'),
            (0, 'Ran 2 tests in 0.001s\nOK (skipped=2)\n'),
        ):
            with self.subTest(code=code, output=output):
                self.assertEqual(self.report(code, output)[0], 'unreadable')

    def test_expected_failure_and_some_skips_are_not_new_failures(self):
        report = self.report(0, 'Ran 3 tests in 0.001s\n\nOK (skipped=1, expected failures=1)\n')
        self.assertEqual(report[:2], ('ok', frozenset()))

    def test_a_prior_ok_does_not_override_the_last_failed_summary(self):
        output = 'Ran 1 test in 0.001s\nOK\nRan 1 test in 0.001s\nFAILED (failures=1)\n'
        self.assertEqual(self.report(0, output)[0], 'unreadable')


class RegressionBoundaryTests(unittest.TestCase):
    """R1~R3: 실제 작은 unittest suite와 결정적인 취소 경계로 최종 제안/기록을 검사한다."""

    def fixture(self):
        case = RegressionVerdictTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def assert_cancelled(self, case):
        run = DeveloperRun.load(case.store)
        self.assertEqual((run.phase, run.proposal_id), ('needs_test', ''))
        record = case.auto_record()
        self.assertEqual((record['result'], record['verification_status']), ('stopped', 'cancelled'))
        self.assertTrue(record['verification_reason'])

    def run_guard(self, body):
        case = self.fixture()
        (case.root / 'tests/test_guard.py').write_text(
            'import sys, unittest\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis"))\n'
            'from a import a\nclass Guard(unittest.TestCase):\n' + body, encoding='utf-8')
        case.mode = DeveloperMode(case.core)  # 합격 검사와 전체 검사 모두 실제 CPU unittest
        case.mode.start('jarvis/a.py, jarvis/b.py', 'tests/test_ab.py', '둘 다 고쳐')
        reply = case.mode.auto('session', max_rounds=1)
        return case, reply

    def test_new_failing_subtest_does_not_alias_the_old_failure(self):
        case, reply = self.run_guard(
            '    def test_cases(self):\n'
            '        for case in (0, 1):\n'
            '            with self.subTest(case=case): self.assertNotEqual(a, case)\n')
        self.assertEqual(DeveloperRun.load(case.store).phase, 'needs_test')
        self.assertEqual(case.auto_record()['result'], 'not_passed')
        self.assertIn('(case=1)', reply)
        self.assertEqual((case.read('a.py'), case.read('b.py')), ('a = 0\n', 'b = 0\n'))

    def test_the_same_old_subtest_is_still_allowed(self):
        case, reply = self.run_guard(
            '    def test_cases(self):\n'
            '        with self.subTest(case="old"): self.fail("already failing")\n')
        self.assertEqual(DeveloperRun.load(case.store).phase, 'awaiting_approval')
        self.assertIn('새로 실패한 테스트 ID 없음', reply)

    def test_new_unexpected_success_is_not_dropped(self):
        case, reply = self.run_guard(
            '    def test_old(self): self.fail("old failure")\n'
            '    @unittest.expectedFailure\n'
            '    def test_new(self): self.assertEqual(a, 1)\n')
        self.assertEqual(DeveloperRun.load(case.store).phase, 'needs_test')
        self.assertEqual(case.auto_record()['result'], 'not_passed')
        self.assertIn('UNEXPECTED SUCCESS:', reply)

    def test_cancel_before_no_change_completion_is_stopped(self):
        import jarvis.developer as developer
        case = self.fixture()
        (case.root / 'jarvis/a.py').write_text('a = 1\n', encoding='utf-8')
        (case.root / 'jarvis/b.py').write_text('b = 2\n', encoding='utf-8')
        case.suite()
        original = developer._test_fingerprints
        real_calls = 0

        def fingerprints(root, tests):
            nonlocal real_calls
            result = original(root, tests)
            if root == case.root:
                real_calls += 1
                if real_calls == 2:  # 덜어내기 뒤 최종 무결성 확인 시 취소
                    case.core.command_cancel_event.set()
            return result

        with mock.patch.object(developer, '_test_fingerprints', fingerprints):
            with self.assertRaisesRegex(ToolError, '중단'):
                case.mode.auto('session')
        self.assert_cancelled(case)
        self.assertEqual(len(case.instructions), 0)

    def test_cancel_during_trim_overrides_final_verification_but_keeps_round_evidence(self):
        case = self.fixture()
        case.suite()

        def trim(*args):
            case.core.command_cancel_event.set()
            return []

        case.mode._trim = trim
        with self.assertRaisesRegex(ToolError, '중단'):
            case.mode.auto('session')
        self.assert_cancelled(case)
        self.assertEqual(case.auto_record()['rounds'][-1]['verification']['status'], 'verified')

    def test_cancel_during_trim_recheck_is_recorded(self):
        case = self.fixture()
        case.suite((0, case.OK), (130, 'cancelled'))

        def trim(run, root, *args):
            (root / 'jarvis/a.py').write_text('a = 1 # trimmed\n', encoding='utf-8')
            return ['changed']

        case.mode._trim = trim
        with self.assertRaisesRegex(ToolError, '중단'):
            case.mode.auto('session')
        self.assert_cancelled(case)

    def test_cancelled_selected_test_is_stopped_even_without_an_event(self):
        case = self.fixture()
        case.suite()
        case.core.coding_regression_check = False
        case.mode._run_frozen_tests = lambda *args: (130, 'cancelled')
        with self.assertRaisesRegex(ToolError, '중단'):
            case.mode.auto('session')
        self.assert_cancelled(case)
        self.assertEqual(case.auto_record()['verification_scope'], 'selected_tests')
        self.assertEqual(len(case.instructions), 0)

    def test_unverified_trim_restores_the_verified_candidate(self):
        case = self.fixture()
        case.suite((0, case.OK), (124, 'timeout'))

        def trim(run, root, *args):
            (root / 'jarvis/a.py').write_text('a = 1 # trimmed\n', encoding='utf-8')
            return ['changed without the old magic phrase']

        case.mode._trim = trim
        reply = case.mode.auto('session')
        run = DeveloperRun.load(case.store)
        self.assertEqual(run.phase, 'awaiting_approval')
        case.editor.apply_bundle(run.proposal_id)
        self.assertEqual((case.read('a.py'), case.read('b.py')), ('a = 1\n', 'b = 2\n'))
        self.assertIn('검증된 이전 후보', reply)


class SuiteDigestTests(unittest.TestCase):
    """2026-10-04 claude: 실제 ALEX 전체 검사(973개)는 테스트의 print 가 요약 뒤에 쏟아지고, 끝 3500자 밖으로 실패 머리글이 잘렸다.

    끝부분만 보던 판독은 정상 통과도 '읽을 수 없음', 실패가 있으면 이름이 모자랐다 — 회귀 검사를 켜면 늘 미검증으로 끝났을 것이다."""

    def log(self, heads, summary, noise=6000):
        progress = "".join(f"test_{i} (m.C.test_{i}) ... ok\n" for i in range(400))
        blocks = "".join(f"{'=' * 70}\n{head}\n{'-' * 70}\nTraceback (most recent call last):\n" + "  x\n" * 400
                         + f"AssertionError: {index}\n\n" for index, head in enumerate(heads))
        return (progress + "\n" + blocks + f"{'-' * 70}\nRan 973 tests in 167.261s\n\n{summary}\n"
                + "[넣을 것] 테스트가 print 한 잡음\n" * (noise // 20))

    def test_a_clean_run_with_print_noise_after_the_summary_is_ok(self):
        from jarvis.developer import _suite_digest, _suite_report
        text = self.log([], "OK (skipped=5)")
        self.assertEqual(_suite_report(0, text[-3500:])[0], 'unreadable')  # 끝부분만 보던 예전 출력
        self.assertEqual(_suite_report(0, _suite_digest(text)), ('ok', frozenset(), ''))

    def test_every_failure_name_survives_long_tracebacks(self):
        from jarvis.developer import _suite_digest, _suite_report
        text = self.log(['ERROR: test_err (m.C.test_err)', 'FAIL: test_sub (m.C.test_sub) (case=1)'],
                        "FAILED (failures=1, errors=1, skipped=5, unexpected successes=1)")
        text = text.replace(f"{'-' * 70}\nRan", f"{'=' * 70}\nUNEXPECTED SUCCESS: test_us (m.C.test_us)\n{'-' * 70}\nRan")
        digest = _suite_digest(text)
        self.assertLess(len(digest), 4500)
        self.assertEqual(_suite_report(1, digest), ('failed', frozenset(
            {'test_err (m.C.test_err)', 'test_sub (m.C.test_sub) (case=1)', 'UNEXPECTED SUCCESS: test_us (m.C.test_us)'}), ''))
        self.assertIn('AssertionError: 1', digest)  # 트레이스백은 끝(단언 메시지)이 남는다

    def test_header_like_lines_inside_a_traceback_are_not_counted(self):
        from jarvis.developer import _suite_digest, _suite_report
        text = self.log(['FAIL: test_a (m.C.test_a)'], "FAILED (failures=1)").replace(
            "AssertionError: 0\n", "AssertionError: 중첩 출력\nERROR: x (y.Z.x)\nFAIL: test_guard (g.G.test_guard)\n")
        self.assertEqual(_suite_report(1, _suite_digest(text))[:2], ('failed', frozenset({'test_a (m.C.test_a)'})))
        nested = text.replace("ERROR: x (y.Z.x)\n", f"{'=' * 70}\nERROR: x (y.Z.x)\n")  # 구분선까지 갖춘 중첩 보고 — 가려낼 수 없다
        self.assertEqual(_suite_report(1, _suite_digest(nested))[0], 'unreadable')  # 이름을 지어내지 않고 미검증

    def test_without_a_summary_right_after_ran_the_tail_is_kept_and_unreadable(self):
        from jarvis.developer import _suite_digest, _suite_report
        text = self.log(['FAIL: test_a (m.C.test_a)'], "FAILED (failures=1)")
        broken = text.replace("s\n\nFAILED (failures=1)\n", "s\n잡음이 끼었다\nFAILED (failures=1)\n")
        self.assertEqual(_suite_digest(broken), broken[-3500:])
        self.assertEqual(_suite_report(1, _suite_digest(broken))[0], 'unreadable')

    def test_a_repeated_failure_id_counts_each_time(self):
        from jarvis.developer import _suite_report
        once = "FAIL: t (m.C.t) (<subtest>)\nRan 1 test in 0.1s\n\nFAILED (failures=1)\n"
        twice = "FAIL: t (m.C.t) (<subtest>)\nFAIL: t (m.C.t) (<subtest>)\nRan 1 test in 0.1s\n\nFAILED (failures=2)\n"
        self.assertEqual(_suite_report(1, twice)[1] - _suite_report(1, once)[1], frozenset({'t (m.C.t) (<subtest>) #2'}))

    def test_the_real_suite_runner_reads_a_noisy_suite(self):
        """실제 unittest 하위 프로세스(CPU): 버퍼에 남은 print 가 요약 뒤에 붙고, 긴 트레이스백이 앞 머리글을 끝 3500자 밖으로 민다."""
        from jarvis.developer import _default_suite_runner, _suite_report
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tests').mkdir()
            test = root / 'tests' / 'test_noisy.py'
            test.write_text('import unittest\n'
                            'class Noisy(unittest.TestCase):\n'
                            '    def test_print(self): print("noise " * 600)\n'
                            '    def test_sub(self):\n'
                            '        for case in (0, 1):\n'
                            '            with self.subTest(case=case): self.assertEqual(case, 0, "x" * 3000)\n'
                            '    def test_err(self): raise ValueError("y" * 3000)\n'
                            '    @unittest.expectedFailure\n'
                            '    def test_us(self): pass\n', encoding='utf-8')
            code, output = _default_suite_runner(root)
            self.assertEqual(_suite_report(code, output), ('failed', frozenset({
                'test_err (test_noisy.Noisy.test_err)', 'test_sub (test_noisy.Noisy.test_sub) (case=1)',
                'UNEXPECTED SUCCESS: test_us (test_noisy.Noisy.test_us)'}), ''))
            test.write_text('import unittest\nclass Noisy(unittest.TestCase):\n'
                            '    def test_print(self): print("noise " * 600)\n', encoding='utf-8')
            self.assertEqual(_suite_report(*_default_suite_runner(root))[0], 'ok')


class ResidualEvidenceTests(unittest.TestCase):
    """Codex Luna 제안 A: 원래 실패가 남은 통과와 전체 통과를 기록에서 구분한다(정책은 그대로 — 새 실패만 막는다)."""

    def fixture(self):
        case = RegressionVerdictTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_old_failures_remain_allowed_but_names_and_scope_are_recorded(self):
        case = self.fixture()
        case.suite((1, case.GUARD), (1, case.GUARD))
        reply = case.mode.auto('session')
        event = case.auto_record()
        self.assertEqual(event['verification_status'], 'verified')
        self.assertEqual(event['verification_completion'], 'residual_failures')
        self.assertEqual(event['verification_residual_failures'], ['test_guard (test_guard.GuardTests.test_guard)'])
        self.assertIn('목표 전체 완료 여부는 별도 확인', reply)

    def test_clean_suite_is_marked_suite_passed(self):
        case = self.fixture()
        case.suite()
        case.mode.auto('session')
        event = case.auto_record()
        self.assertEqual(event['verification_completion'], 'suite_passed')
        self.assertEqual(event['verification_residual_failures'], [])

    def test_a_cancel_while_the_model_writes_is_recorded_as_cancelled(self):
        from jarvis.providers import GenerationCancelled
        case = self.fixture()
        case.suite()

        def cancelled(*_args, **_kwargs):
            case.core.command_cancel_event.set()
            raise GenerationCancelled('')

        case.core.propose_bundle_local = cancelled
        with self.assertRaisesRegex(ToolError, '중단'):
            case.mode.auto('session')
        event = case.auto_record()
        self.assertEqual((event['result'], event['verification_status']), ('stopped', 'cancelled'))
        self.assertEqual(DeveloperRun.load(case.store).phase, 'needs_test')
