"""편집 승인/거부가 경험으로 남는지 (ADR-037).

/apply-edit 과 /discard-edit 은 모델이 만든 코드에 사람이 내린 실제 판단이다.
학습에 가장 값어치 있는 신호이므로 흘리지 않고 잡아야 한다.
"""
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage
from jarvis.workspace import SafeWorkspaceEditor


class _StubProvider:
    model = "test-model"


class _MiniCore:
    """core 의 편집 경험 경로만 떼어 낸 대역.

    JarvisCore 전체를 세우려면 provider/router/config 가 전부 필요해서,
    여기서는 record_edit_experience 가 의존하는 것만 최소로 흉내 낸다.
    """

    def __init__(self, storage):
        self.storage = storage
        self.providers = {"local_coder": _StubProvider()}
        self._edit_instructions = {}
        import logging
        self.logger = logging.getLogger("test")

    remember_edit_instruction = None  # 아래에서 실제 구현을 붙인다
    record_edit_experience = None


class EditExperienceTests(unittest.TestCase):
    def setUp(self):
        from jarvis.core import JarvisCore
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        (self.project / "demo.py").write_text("x = 1\n", encoding="utf-8")
        self.storage = Storage(root / "t.db")
        self.editor = SafeWorkspaceEditor(self.project, root / "backups", max_bytes=10_000)
        self.core = _MiniCore(self.storage)
        # 실제 구현을 그대로 빌려 쓴다 (복제하지 않는다)
        self.core.remember_edit_instruction = JarvisCore.remember_edit_instruction.__get__(self.core)
        self.core.record_edit_experience = JarvisCore.record_edit_experience.__get__(self.core)

    def tearDown(self):
        self.temp.cleanup()

    def make_proposal(self, instruction="x 를 2 로 바꿔줘"):
        proposal = self.editor.create_proposal("demo.py", "x = 2\n")
        self.core.remember_edit_instruction(proposal.proposal_id, instruction)
        return proposal

    def test_approved_edit_becomes_good_experience(self):
        proposal = self.make_proposal()
        self.core.record_edit_experience(proposal, "good", note="사용자가 적용함")
        rows = self.storage.list_experiences(kind="edit", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].verdict, "good")
        self.assertIn("demo.py", rows[0].situation)
        self.assertIn("x 를 2 로 바꿔줘", rows[0].situation)
        self.assertIn("+x = 2", rows[0].judgment)

    def test_discarded_edit_becomes_bad_experience_with_correction(self):
        proposal = self.make_proposal()
        self.core.record_edit_experience(proposal, "bad", note="사용자가 폐기함")
        row = self.storage.list_experiences(kind="edit", limit=10)[0]
        self.assertEqual(row.verdict, "bad")
        self.assertEqual(row.correction, "사용자가 폐기함")

    def test_experience_starts_raw_and_is_not_trainable(self):
        self.core.record_edit_experience(self.make_proposal(), "good")
        self.assertEqual(self.storage.list_experiences(kind="edit", limit=5)[0].stage, "raw")
        self.assertEqual(self.storage.training_pool(), [])

    def test_instruction_is_consumed_once(self):
        """같은 제안으로 두 번 기록해도 지시문이 중복 소비되지 않는다."""
        proposal = self.make_proposal("첫 지시")
        self.core.record_edit_experience(proposal, "good")
        self.core.record_edit_experience(proposal, "bad")
        rows = self.storage.list_experiences(kind="edit", limit=10)
        self.assertEqual(len(rows), 2)
        situations = [r.situation for r in rows]
        self.assertTrue(any("첫 지시" in x for x in situations))
        self.assertTrue(any("기록되지 않음" in x for x in situations))

    def test_none_proposal_is_ignored(self):
        self.assertIsNone(self.core.record_edit_experience(None, "good"))
        self.assertEqual(self.storage.list_experiences(kind="edit", limit=5), [])

    def test_instruction_cache_is_bounded(self):
        for i in range(80):
            self.core.remember_edit_instruction(f"p{i}", f"지시 {i}")
        self.assertLessEqual(len(self.core._edit_instructions), 50)


if __name__ == "__main__":
    unittest.main()
