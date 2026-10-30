"""Office chat uses real packages but fake model output: no GPU, network or personal files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.documents import NAME_RE, _docx, _xlsx, _pptx, parse_markdown
from jarvis.officeedit import OfficeDocument
from test_web import make_state


class OfficeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.web = make_state(self.base)
        self.core, self.sid = self.web.core, self.web.session_id
        self.root = self.base / 'project'
        self.root.mkdir()
        self.core.current_project_root = lambda: self.root
        self.model_calls = []
        self.output = [{'id': 'p2', 'text': '고친 문단'}]
        def generate(messages, **kwargs):
            self.model_calls.append(messages)
            return json.dumps(self.output, ensure_ascii=False)
        self.core.providers[next(d for d in self.core.providers if d.value == 'local_general')].generate = generate

    def package(self, ext='docx'):
        return {'docx': _docx, 'xlsx': _xlsx, 'pptx': _pptx}[ext]('보고서', parse_markdown('# 제목\n\n원래 문단\n\n둘째 문단'))

    def pc(self, name='보고서.docx'):
        path = self.root / name
        path.write_bytes(self.package())
        return path

    def attach(self, name='보고서.docx', sid=None):
        return self.core.knowledge.import_bytes(self.package(Path(name).suffix[1:]), name, scope='session:' + (sid or self.sid))

    def say(self, text, mode='ask'):
        self.core.permission_mode = mode
        response, decision = self.core.handle(self.sid, text)
        self.assertEqual(decision.reason, 'office edit request')
        return response

    def contents(self, path):
        return [i.text for i in OfficeDocument(path.read_bytes(), path.suffix).items]

    def test_pc_ask_approve_backup_undo_and_read_rejection(self):
        path = self.pc()
        original = path.read_bytes()
        self.assertIn('읽기 전용', self.say('보고서.docx 고쳐줘', 'read'))
        self.assertEqual(self.model_calls, [])
        proposal = self.say('보고서.docx 고쳐줘')
        self.assertIn('원본 미변경', proposal)
        self.assertEqual(path.read_bytes(), original)
        self.assertIn('적용했습니다', self.say('응'))
        self.assertIn('고친 문단', self.contents(path))
        backups = list((self.base / 'backups/office').glob('*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertIn('되돌렸습니다', self.say('되돌려줘'))
        self.assertEqual(path.read_bytes(), original)

    def test_pc_auto_reject_stale_source_and_project_change(self):
        path = self.pc()
        self.assertIn('적용했습니다', self.say('보고서.docx 고쳐줘', 'auto'))
        current = path.read_bytes()
        self.output = [{'id': 'p2', 'text': '승인 전 문단'}]
        self.say('보고서.docx 수정해줘')
        self.assertIn('취소했습니다', self.say('아니'))
        self.assertEqual(path.read_bytes(), current)
        self.say('보고서.docx 수정해줘')
        path.write_bytes(current + b'changed')
        self.assertIn('원본이 바뀌었습니다', self.say('응'))
        path.write_bytes(current)
        self.say('보고서.docx 수정해줘')
        other = self.base / 'other'
        other.mkdir()
        self.core.current_project_root = lambda: other
        self.assertIn('프로젝트 폴더가 바뀌었습니다', self.say('응'))

    def test_owned_upload_copy_followup_uses_last_result_and_keeps_original(self):
        record = self.attach()
        original = (self.core.knowledge.sources / record['snapshot']).read_bytes()
        self.core.member_mode = True
        response = self.say('보고서.docx 고쳐줘')
        state = self.core.office_edits.state(self.sid)
        first = self.base / state['current']
        self.assertRegex(first.name, NAME_RE)
        self.assertIn('문서 다운로드', response)
        self.assertIn('고친 문단', self.contents(first))
        self.output = [{'id': 'p3', 'text': '후속 수정'}]
        self.say('2장도 다듬어줘')
        second = self.base / self.core.office_edits.state(self.sid)['current']
        self.assertNotEqual(first, second)
        self.assertIn('고친 문단', self.model_calls[-1][-1].content)
        self.assertIn('고친 문단', self.contents(second))
        self.assertIn('후속 수정', self.contents(second))
        self.assertEqual((self.core.knowledge.sources / record['snapshot']).read_bytes(), original)

    def test_xlsx_and_pptx_upload_copy_paths(self):
        for ext in ('xlsx', 'pptx'):
            with self.subTest(ext=ext):
                name = '자료.' + ext
                record = self.attach(name)
                doc = OfficeDocument(self.package(ext), '.' + ext)
                self.output = [{'id': next(i.id for i in doc.items if i.text), 'text': '편집된 자료'}]
                self.say(name + ' 수정해줘')
                target = self.base / self.core.office_edits.state(self.sid)['current']
                self.assertIn('편집된 자료', self.contents(target))
                self.assertTrue((self.core.knowledge.sources / record['snapshot']).is_file())

    def test_other_session_attachment_member_pc_and_path_escape_are_denied(self):
        path = self.pc()
        original = path.read_bytes()
        other = self.core.storage.create_session('other')
        self.attach('비밀.docx', other)
        self.core.member_mode = True
        self.assertIn('관리자만', self.say('보고서.docx 고쳐줘'))
        self.assertIn('관리자만', self.say('비밀.docx 고쳐줘'))
        self.assertEqual(self.model_calls, [])
        self.core.member_mode = False
        outside = self.base / 'outside.docx'
        outside.write_bytes(self.package())
        self.assertIn('승인된 프로젝트', self.say('../outside.docx 고쳐줘', 'auto'))
        self.assertEqual(path.read_bytes(), original)

    def test_persisted_proposal_still_checks_scope_and_read_permission(self):
        record = self.attach()
        self.say('보고서.docx 고쳐줘')
        from jarvis.office import OfficeEdits
        self.core.office_edits = OfficeEdits(self.core)
        self.core.member_mode = True
        self.core.knowledge.forget(record['id'], scope='session:' + self.sid)
        self.assertIn('문서를 편집하지 못했습니다', self.say('2장도 다듬어줘'))

    def test_office_wins_over_old_code_replies_and_unrelated_turn_ends_approval(self):
        self.pc()
        viewer = self.web.owner_viewer()
        self.web.code_links[self.sid] = {'code': 'older-code', 'fresh': True}
        self.assertIsNone(self.web.code_chat_plan(viewer, self.sid, '보고서.docx 고쳐줘'))
        self.assertFalse(self.web.code_links[self.sid]['fresh'])
        self.say('보고서.docx 고쳐줘')
        self.assertIsNone(self.web.code_chat_plan(viewer, self.sid, '응'))
        self.assertTrue(self.core.office_edits.state(self.sid)['active'])
        self.web.code_chat_plan(viewer, self.sid, '안녕')
        self.assertFalse(self.core.office_edits.matches(self.sid, '응'))

    def test_explicit_pc_path_wins_over_same_named_attachment_and_repeated_yes_does_not_approve_old_code(self):
        from types import SimpleNamespace
        import threading
        path = self.pc()
        self.attach()
        self.assertIn('원본 미변경', self.say('프로젝트 보고서.docx 고쳐줘'))
        self.assertEqual(self.core.office_edits.state(self.sid)['kind'], 'pc')
        self.say('응')
        self.assertIn('고친 문단', self.contents(path))
        viewer = self.web.owner_viewer()
        self.web.code_links[self.sid] = {'code': 'old', 'fresh': True}
        self.web.code.sessions['old'] = SimpleNamespace(permissions={'p': SimpleNamespace(answered=threading.Event())}, status='running')
        self.assertIsNone(self.web.code_chat_plan(viewer, self.sid, '응'))
        self.assertIn('수정안이 없습니다', self.say('응'))


if __name__ == '__main__':
    unittest.main()
