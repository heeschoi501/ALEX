"""OpenCode 무료 모델 코딩 세션(2026-10-23) — OpenCode(가짜)가 복사본에서 고치고, ALEX 가 권한대로 들이고, 독립 검사·되돌리기."""
import json
import sys
import time
from pathlib import Path

from jarvis.harness.sessions import CodeSessions
from test_harness import Fixture

FAKE = r"""
import json, sys
from pathlib import Path
args = sys.argv[1:]
session = args[args.index("--session") + 1] if "--session" in args else ""
Path("calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
print(json.dumps({"type": "tool_use", "sessionID": "oc_1", "part": {"tool": "edit", "state": {"input": {"filePath": "calc.py"}}}}), flush=True)
print(json.dumps({"type": "text", "sessionID": "oc_1", "part": {"text": "고쳤습니다(이어 말하기: " + (session or "새") + ")"}}), flush=True)
"""


class FreeSessionTests(Fixture):
    def prepare(self):
        self.put('calc.py', 'def add(a, b):\n    return a - b\n')
        self.put('tests/test_calc.py', 'import unittest\nfrom calc import add\n'
                 'class T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n')
        script = Path(self.temp.name) / 'fake_opencode.py'
        script.write_text(FAKE, encoding='utf-8')
        return [sys.executable, str(script)]

    def wait(self, sessions, sid, reply=None, since=0):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for event in sessions.events(sid, since, wait=0.05):
                since = event['n'] + 1
                if event['type'] == 'permission.asked':
                    sessions.reply_permission(sid, event['request']['id'], reply(event['request']) if reply else 'once')
                if event['type'] == 'session.idle':
                    return sessions.get(sid).last
        self.fail('세션이 끝나지 않았습니다.')

    def test_free_model_edits_come_in_verified_continue_and_revert(self):
        fake = self.prepare()
        store = Path(self.temp.name) / 'sessions'
        recorded = []
        sessions = CodeSessions(lambda: None, store=store, opencode_command=fake, outcome=recorded.append)
        with self.assertRaises(ValueError):
            sessions.create('owner', self.root, 'auto', model='openai/gpt')
        session = sessions.create('owner', self.root, 'auto', model='opencode/big-pickle')
        sessions.prompt(session.id, 'calc.py 고쳐. tests/test_calc.py')
        result = self.wait(sessions, session.id)
        self.assertEqual(result.stopped, 'done')
        self.assertIn('return a + b', (self.root / 'calc.py').read_text())        # 복사본의 수정을 들였다
        self.assertEqual(result.verification[-1]['status'], 'verified')           # ALEX 독립 검사
        self.assertEqual(sessions.get(session.id).oc_session, 'oc_1')
        self.assertEqual(recorded[0]['model'], 'opencode/big-pickle')
        mark = sessions.get(session.id).public()['next_event']
        sessions.prompt(session.id, '계속')
        again = self.wait(sessions, session.id, since=mark)
        self.assertIn('이어 말하기: oc_1', again.text)                            # OpenCode 세션으로 이어 감
        self.assertEqual([m['role'] for m in sessions.messages(session.id)], ['user', 'assistant', 'user', 'assistant'])
        reloaded = CodeSessions(lambda: None, store=store, opencode_command=fake).get(session.id)
        self.assertEqual((reloaded.model, reloaded.oc_session), ('opencode/big-pickle', 'oc_1'))
        self.assertIn('calc.py', sessions.revert(session.id, 0))
        self.assertIn('a - b', (self.root / 'calc.py').read_text())

    def test_ask_tier_shows_the_opencode_diff_and_rejection_keeps_the_file(self):
        fake = self.prepare()
        sessions = CodeSessions(lambda: None, opencode_command=fake)
        session = sessions.create('owner', self.root, 'ask', model='opencode/big-pickle')
        asked = []
        sessions.prompt(session.id, 'calc.py 고쳐')
        result = self.wait(sessions, session.id, lambda request: asked.append(request) or 'reject')
        self.assertEqual(asked[0]['detail']['tool'], 'opencode')
        self.assertIn('+    return a + b', asked[0]['detail']['diff'])
        self.assertIn('a - b', (self.root / 'calc.py').read_text())
        self.assertIn('denied by the user', result.text)

    def test_private_and_heavy_folders_are_not_copied_out(self):
        from jarvis.harness.sessions import _copy_for_free
        self.prepare()
        for name in ('data/alex.db', 'logs/run.log', 'runtime/models/x.bin', '.env', 'jarvis.local.json', '.git/HEAD',
                     # 2026-10-26: 키·인증 파일도 opencode.ai 로 보내지 않는다(cloudagent 와 같은 목록)
                     'id_rsa', 'deploy/server.pem', 'credentials.json', 'config/secrets.toml', 'cert.pfx', '.netrc'):
            self.put(name, 'secret')
        target = Path(self.temp.name) / 'copy'
        _copy_for_free(self.root, target)
        copied = sorted(p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file())
        self.assertEqual(copied, ['calc.py', 'tests/test_calc.py'])

    def test_files_with_secret_contents_are_withheld_and_their_paths_not_overwritten(self):
        # 2026-10-26: 이름이 평범해도 내용에 비밀값 모양이 보이면 보내지 않고, OpenCode 가 그 자리에 새 파일을 써도 원본을 덮지 않는다.
        fake = self.prepare()
        self.put('settings.py', 'API_KEY = "sk-abcdefghijklmnop1234"\n')
        self.put('notes.md', '-----BEGIN RSA PRIVATE KEY-----\n' + 'MIIEow' * 20 + '\n-----END RSA PRIVATE KEY-----\n')
        # 평범한 코드의 'password: str'·'api_key=' 는 비밀값이 아니다(이것까지 빼면 ALEX 의 core·web 이 빠졌다)
        self.put('accounts.py', 'def login(name: str, password: str):\n    api_key = load()\n')
        target = Path(self.temp.name) / 'copy'
        from jarvis.harness.sessions import _copy_for_free
        self.assertEqual(_copy_for_free(self.root, target), ['notes.md', 'settings.py'])
        self.assertFalse((target / 'settings.py').exists() or (target / 'notes.md').exists())
        self.assertTrue((target / 'accounts.py').exists())
        script = Path(fake[1])
        script.write_text(FAKE + "Path('settings.py').write_text('API_KEY = None\\n', encoding='utf-8')\n"
                          "Path('.env').write_text('X=1\\n', encoding='utf-8')\n", encoding='utf-8')
        sessions = CodeSessions(lambda: None, opencode_command=fake)
        session = sessions.create('owner', self.root, 'auto', model='opencode/big-pickle')
        sessions.prompt(session.id, 'calc.py 고쳐')
        result = self.wait(sessions, session.id)
        self.assertIn('return a + b', (self.root / 'calc.py').read_text())        # 평범한 변경은 들인다
        self.assertIn('sk-abcdefghijklmnop1234', (self.root / 'settings.py').read_text())
        self.assertFalse((self.root / '.env').exists())
        self.assertIn('settings.py: 보내지 않은 비밀 파일 자리', result.text)

    def test_read_tier_tells_opencode_not_to_edit_and_brings_nothing_in(self):
        fake = self.prepare()
        sessions = CodeSessions(lambda: None, opencode_command=fake)
        session = sessions.create('owner', self.root, 'read', model='opencode/big-pickle')
        sessions.prompt(session.id, '설명해줘')
        self.wait(sessions, session.id)
        config = json.loads((sessions._free_dir(session) / 'opencode.json').read_text(encoding='utf-8'))
        self.assertEqual((config['model'], config['permission']['edit'], config['permission']['bash']),
                         ('opencode/big-pickle', 'deny', 'deny'))
        self.assertIn('a - b', (self.root / 'calc.py').read_text())

    def test_linked_directory_replacements_are_not_imported(self):
        fake = self.prepare()
        outside = Path(self.temp.name) / 'private'
        outside.mkdir()
        (outside / 'note.txt').write_text('private', encoding='utf-8')
        try:
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'symlinks unavailable: {exc}')
        script = Path(fake[1])
        script.write_text(FAKE + "Path('linked').mkdir(exist_ok=True)\n"
                          "Path('linked/new.txt').write_text('replacement')\n", encoding='utf-8')
        sessions = CodeSessions(lambda: None, opencode_command=fake)
        session = sessions.create('owner', self.root, 'auto', model='opencode/big-pickle')
        sessions.prompt(session.id, 'calc.py 고쳐')
        self.wait(sessions, session.id)
        self.assertFalse((outside / 'new.txt').exists())
        self.assertEqual((outside / 'note.txt').read_text(), 'private')
