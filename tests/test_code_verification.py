"""/code의 독립 검사·재수정은 권한을 지키며 복사본에서 실행하고 원본 테스트를 덮지 않는다."""
import time
from pathlib import Path
from jarvis.harness.sessions import CodeSessions
from test_harness import Fixture, Script, call


class CodeVerificationTests(Fixture):
    def prepare(self):
        self.put('calc.py', 'def add(a, b):\n    return a - b\n')
        self.put('tests/test_calc.py', 'import unittest\nfrom calc import add\n'
                 'class T(unittest.TestCase):\n    def test_negative(self): self.assertEqual(add(-2, 1), -1)\n')

    def wait(self, sessions, sid, replies=None):
        since, deadline = 0, time.monotonic() + 10
        while time.monotonic() < deadline:
            for event in sessions.events(sid, since, wait=0.05):
                since = event['n'] + 1
                if event['type'] == 'permission.asked':
                    response = replies(event['request']) if replies else 'once'
                    sessions.reply_permission(sid, event['request']['id'], response)
                if event['type'] == 'session.idle':
                    return sessions.get(sid).last
        self.fail('코딩 세션이 끝나지 않았습니다.')

    def partial(self):
        return [('', [call('read', filePath='calc.py')]),
                ('', [call('edit', filePath='calc.py', oldString='a - b', newString='abs(a) + abs(b)')]),
                ('끝', [])]

    def test_failed_contract_is_repaired_and_persisted(self):
        self.prepare()
        model = Script(self.partial() + [('', [call('edit', filePath='calc.py', oldString='abs(a) + abs(b)', newString='a + b')]), ('고쳤습니다', [])])
        store = Path(self.temp.name) / 'sessions'
        sessions = CodeSessions(lambda: model, store=store)
        session = sessions.create('owner', self.root, 'auto')
        original = (self.root / 'tests/test_calc.py').read_bytes()
        sessions.prompt(session.id, 'calc.py를 고쳐라. tests/test_calc.py')
        result = self.wait(sessions, session.id)
        self.assertEqual(result.stopped, 'done')
        self.assertEqual([r['status'] for r in result.verification], ['unverified', 'verified'])
        self.assertIn('a + b', (self.root / 'calc.py').read_text())
        self.assertEqual((self.root / 'tests/test_calc.py').read_bytes(), original)
        self.assertTrue(any('AssertionError' in m.content for seen in model.seen for m in seen))
        restored = CodeSessions(lambda: model, store=store).get(session.id)
        self.assertEqual(restored.last.verification[-1]['status'], 'verified')
        self.assertIn('calc.py', sessions.revert(session.id, 0))
        self.assertIn('a - b', (self.root / 'calc.py').read_text())

    def test_checked_sessions_leave_a_learning_record_and_denied_ones_do_not(self):
        # 2026-10-23 claude: 검사로 판정된 코딩 세션만 dev-outcomes 'code' 기록(/dev-learn 재료)
        self.prepare()
        recorded = []
        model = Script(self.partial() + [('', [call('edit', filePath='calc.py', oldString='abs(a) + abs(b)', newString='a + b')]), ('고쳤습니다', [])])
        sessions = CodeSessions(lambda: model, outcome=recorded.append)
        session = sessions.create('owner', self.root, 'auto')
        sessions.prompt(session.id, 'calc.py를 고쳐라. tests/test_calc.py')
        self.wait(sessions, session.id)
        self.assertEqual(len(recorded), 1)
        event = recorded[0]
        self.assertEqual((event['type'], event['result'], event['files']), ('code', 'verified', ['calc.py']))
        self.assertEqual([r['exit'] for r in event['rounds']], [1, 0])
        self.assertIn('AssertionError', event['rounds'][0]['failure'])
        self.assertIn('+    return a + b', event['rounds'][-1]['diff'])
        self.assertIn('calc.py를 고쳐라', event['goal'])
        denied = []
        self.put('calc.py', 'def add(a, b):\n    return a - b\n')
        sessions = CodeSessions(lambda: Script(self.partial()), outcome=denied.append)
        session = sessions.create('owner', self.root, 'ask')
        sessions.prompt(session.id, 'calc.py 수정. tests/test_calc.py')
        self.wait(sessions, session.id, lambda request: 'reject' if request['permission'] == 'bash' else 'once')
        self.assertEqual(denied, [])  # 검사를 못 돌린 세션은 배울 재료가 아니다

    def test_denied_independent_execution_is_not_repaired_or_certified(self):
        self.prepare()
        model = Script(self.partial())
        sessions = CodeSessions(lambda: model)
        session = sessions.create('owner', self.root, 'ask')
        requested = []
        def reply(request):
            requested.append(request['permission'])
            return 'reject' if request['permission'] == 'bash' else 'once'
        sessions.prompt(session.id, 'calc.py 수정. tests/test_calc.py')
        result = self.wait(sessions, session.id, reply)
        self.assertEqual(result.stopped, 'unverified')
        self.assertEqual(requested, ['edit', 'bash'])
        self.assertEqual(len(model.seen), 3)

    def test_approved_test_changes_are_not_silently_restored_or_used_as_proof(self):
        self.prepare()
        model = Script([('', [call('read', filePath='tests/test_calc.py')]),
                        ('', [call('write', filePath='tests/test_calc.py', content='# changed by request\n')]), ('변경', [])])
        sessions = CodeSessions(lambda: model)
        session = sessions.create('owner', self.root, 'ask')
        sessions.prompt(session.id, 'tests/test_calc.py를 수정해라')
        result = self.wait(sessions, session.id)
        self.assertEqual(result.stopped, 'unverified')
        self.assertEqual((self.root / 'tests/test_calc.py').read_text(), '# changed by request\n')
        self.assertIn('기존 검사 기준', result.error)
        sessions.revert(session.id, 0)
        self.assertIn('test_negative', (self.root / 'tests/test_calc.py').read_text())

    def test_checks_run_in_a_copy_and_do_not_remove_live_cache_or_include_logs(self):
        self.prepare()
        p = self.root / 'tests/test_calc.py'
        p.write_text('from pathlib import Path\nPath("side-effect.txt").write_text("in copy")\n'
                     + p.read_text(), encoding='utf-8')
        self.put('logs/private.py', 'raise RuntimeError("must not enter test copy")\n')
        self.put('tests/test_private.py', 'import unittest\nfrom pathlib import Path\n'
                 'class T(unittest.TestCase):\n    def test_no_private(self): self.assertFalse(Path("logs/private.py").exists())\n')
        cache = self.root / '__pycache__/keep.pyc'
        cache.parent.mkdir(); cache.write_bytes(b'owned cache')
        model = Script([('', [call('read', filePath='calc.py')]),
                        ('', [call('edit', filePath='calc.py', oldString='a - b', newString='a + b')]), ('完', [])])
        sessions = CodeSessions(lambda: model)
        session = sessions.create('owner', self.root, 'auto')
        sessions.prompt(session.id, 'calc.py 수정 tests/test_calc.py tests/test_private.py')
        result = self.wait(sessions, session.id)
        self.assertEqual(result.verification[-1]['status'], 'verified')
        self.assertFalse((self.root / 'side-effect.txt').exists())
        self.assertEqual(cache.read_bytes(), b'owned cache')

    def test_read_and_plan_do_not_execute_independent_commands(self):
        for tier, mode in [('read', 'build'), ('auto', 'plan')]:
            with self.subTest(tier=tier, mode=mode):
                self.prepare()
                model = Script([('설명', [])])
                sessions = CodeSessions(lambda: model)
                session = sessions.create('owner', self.root, tier)
                sessions.prompt(session.id, 'calc.py 설명 tests/test_calc.py', mode=mode)
                result = self.wait(sessions, session.id)
                self.assertEqual(result.verification, [])
                self.assertNotIn('bash', session.workspace.counts)

    def test_permission_wait_expires_with_request_deadline(self):
        self.prepare()
        model = Script([('', [call('read', filePath='calc.py')]),
                        ('', [call('edit', filePath='calc.py', oldString='a - b', newString='a + b')])])
        sessions = CodeSessions(lambda: model, timeout=0.15)
        session = sessions.create('owner', self.root, 'ask')
        sessions.prompt(session.id, 'calc.py 수정')
        deadline = time.monotonic() + 2
        while session.status == 'running' and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(session.last.stopped, 'timeout')
        self.assertFalse(session.permissions)
        self.assertIn('a - b', (self.root / 'calc.py').read_text())

    def test_repair_cannot_hide_failure_by_undoing_its_change(self):
        self.prepare()
        model = Script(self.partial() + [('', [call('edit', filePath='calc.py', oldString='abs(a) + abs(b)', newString='a - b')]), ('원복', [])])
        sessions = CodeSessions(lambda: model)
        session = sessions.create('owner', self.root, 'auto')
        sessions.prompt(session.id, 'calc.py 고쳐라 tests/test_calc.py')
        result = self.wait(sessions, session.id)
        self.assertEqual(result.stopped, 'unverified')
        self.assertEqual(len(result.verification), 2)
        self.assertEqual(result.verification[-1]['checks'][0]['status'], 'failed')

    def test_session_deadline_and_abort_interrupt_real_waiting_headers(self):
        from test_harness_control import server
        from jarvis.providers import StrataProvider
        for abort in (False, True):
            with self.subTest(abort=abort), server('headers', strata=True) as (host, entered):
                model = StrataProvider('test', host=host, timeout=5)
                sessions = CodeSessions(lambda: model, timeout=4 if abort else 0.15)
                session = sessions.create('owner', self.root, 'auto')
                started = time.monotonic()
                sessions.prompt(session.id, '설명')
                self.assertTrue(entered.wait(1))
                if abort:
                    sessions.abort(session.id)
                result = self.wait(sessions, session.id)
                self.assertEqual(result.stopped, 'cancelled' if abort else 'timeout')
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertIsNone(model.cancel_event)
