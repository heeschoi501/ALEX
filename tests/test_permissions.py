"""대화별 권한 3단계(ADR-095, 2026-10-18 사용자 "권한은 클라우드 LLM 처럼 선택해서 쓰게").

읽기 전용은 파일을 바꾸거나 코드를 실행하거나 그림을 만들지 않고, 확인 후 실행은 예전과 같고, 자동 실행은 수정안을
바로 적용(백업·되돌리기)하되 ALEX 자기 프로그램 파일은 사람 확인으로 남긴다. 새 대화는 언제나 확인 후 실행."""
import tempfile
import unittest
from pathlib import Path

from jarvis import accounts
from jarvis.app import auto_apply_proposals
from jarvis.permissions import READ_ONLY_BLOCKED, clean_permission, is_alex_program
from jarvis.router import Destination, RouteDecision
from jarvis.web import Viewer
from jarvis.workspace import SafeWorkspaceEditor
from tests.test_web import make_state


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)
        self.core = self.state.core
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "notes.py").write_text("value = 0\n", encoding="utf-8")
        self.core.workspace_editor = SafeWorkspaceEditor(self.project, self.root / "backups", storage=self.core.storage)
        self.core.propose_file_edit_local = lambda _sid, path, _instruction: self.core.workspace_editor.create_proposal(
            path, "value = 1\n")
        self.owner = self.state.owner_viewer()
        self.sid = self.state.session_id

    def tearDown(self):
        self.temp.cleanup()

    def file(self):
        return (self.project / "notes.py").read_text(encoding="utf-8")

    def member(self):
        user = self.core.storage.create_user("민수", "minsu", accounts.hash_password("member-pass-1"))
        return Viewer(user, remote=False)

    def test_default_is_ask_and_behaves_as_before(self):
        self.assertEqual(self.state.snapshot()["permission"], "ask")
        reply = self.state.process("/edit-file notes.py :: 1로", permission="ask")["response"]
        self.assertIn("적용: /apply-edit", reply)
        self.assertEqual(self.file(), "value = 0\n")  # 사람이 적용할 때까지 그대로

    def test_read_only_refuses_changes_and_running_code(self):
        proposal = self.state.process("/edit-file notes.py :: 1로", permission="read")["response"]
        proposal_id = proposal.split("편집 제안 ID: ", 1)[1].split("\n", 1)[0]  # 수정안(보여 주기)은 된다
        for command in (f"/apply-edit {proposal_id} --yes", "/build 계산기", "/dev-auto", "/image 고양이", "/open-vscode --yes"):
            with self.subTest(command=command):
                self.assertEqual(self.state.process(command, permission="read")["response"], READ_ONLY_BLOCKED)
        self.assertEqual(self.file(), "value = 0\n")
        self.assertEqual(self.state.snapshot()["permission"], "read")  # 보낼 때 고른 권한이 대화에 남는다

    def test_read_only_blocks_pictures_and_build_in_plain_chat(self):
        self.core.permission_mode = "read"
        decision = RouteDecision(Destination.TOOL_IMAGE_GEN, "draw", "")
        self.assertEqual(self.core._handle_decision(self.sid, "고양이 그려줘", decision)[0], READ_ONLY_BLOCKED)

    def test_auto_applies_with_backup_and_undo(self):
        reply = self.state.process("/edit-file notes.py :: 1로", permission="auto")["response"]
        self.assertEqual(self.file(), "value = 1\n")
        self.assertIn("[자동 실행] 적용", reply)
        self.assertIn("되돌리기: /undo-file notes.py --yes", reply)
        self.assertNotIn("/apply-edit", reply)  # 이미 적용했으니 적용 안내는 없다
        self.state.process("/undo-file notes.py --yes", permission="auto")
        self.assertEqual(self.file(), "value = 0\n")

    def test_auto_never_applies_alex_own_program(self):
        alex = self.root / "alex"
        (alex / "jarvis").mkdir(parents=True)
        (alex / "jarvis" / "web.py").write_text("RULE = 1\n", encoding="utf-8")
        self.core.workspace_editor = SafeWorkspaceEditor(alex, self.root / "backups2", storage=self.core.storage)
        self.assertTrue(is_alex_program(alex / "jarvis" / "web.py", alex))
        proposal = self.core.workspace_editor.create_proposal("jarvis/web.py", "RULE = 0\n")
        response = f"편집 제안 ID: {proposal.proposal_id}\n적용: /apply-edit {proposal.proposal_id} --yes\n"
        import jarvis.app as app_module
        original = app_module.is_alex_program
        app_module.is_alex_program = lambda path: original(path, alex)
        try:
            reply = auto_apply_proposals(self.core, response)
        finally:
            app_module.is_alex_program = original
        self.assertEqual((alex / "jarvis" / "web.py").read_text(encoding="utf-8"), "RULE = 1\n")
        self.assertIn("자동으로 적용하지 않았습니다", reply)
        self.assertIn(f"/apply-edit {proposal.proposal_id} --yes", reply)  # 사람이 적용할 길은 남긴다

    def test_alex_program_files(self):
        root = Path("C:/ALEX/jarvis")
        self.assertTrue(is_alex_program(root / "jarvis" / "permissions.py", root))
        self.assertTrue(is_alex_program(root / "jarvis.local.json", root))
        self.assertFalse(is_alex_program(root / "tests" / "test_x.py", root))
        self.assertFalse(is_alex_program(Path("C:/work/app/main.py"), root))

    def test_members_cannot_choose_auto(self):
        member = self.member()
        sid = self.core.storage.create_session(member.id)
        with self.assertRaises(ValueError):
            self.state.set_permission(member, sid, "auto")
        self.assertEqual(self.state.set_permission(member, sid, "read"), "read")
        with self.assertRaises(ValueError):  # 남의 대화는 못 바꾼다
            self.state.set_permission(member, self.sid, "read")

    def test_each_conversation_keeps_its_own_and_new_ones_start_at_ask(self):
        self.state.set_permission(self.owner, self.sid, "auto")
        other = self.state.process("/new")["session_id"]
        self.assertEqual(self.state.session_permission(self.owner, other), "ask")
        self.assertEqual(self.state.session_permission(self.owner, self.sid), "auto")
        self.core.storage.set_state(f"permission:{other}", "auto")  # 빈 대화를 다시 쓰는 경우
        self.state.reset_permission(other)
        self.assertEqual(self.state.session_permission(self.owner, other), "ask")

    def test_request_mode_is_reset_after_processing(self):
        self.state.process("안녕", permission="read")
        self.assertEqual(self.core.permission_mode, "ask")

    def test_bad_values_are_refused(self):
        for value in ("full", "", None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                clean_permission(value)


if __name__ == "__main__":
    unittest.main()
