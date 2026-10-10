"""학습 시도를 경험으로 남기는 경로의 회귀 테스트 (ADR-037).

이 경로가 값어치 있는 이유는 **판정이 모델 바깥에서 오기 때문**이다. 파이썬이 실제로
실행해서 PASS/FAIL 을 정한다. 모델이 자기 답을 채점하면 자기참조 루프이고 학습 신호가
아니다(STUDY_GUIDE 문제 25).
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.storage import Storage  # noqa: E402
from jarvis.study import _default_db, record_attempt, recording_enabled  # noqa: E402


class RecordAttemptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.db"

    def tearDown(self):
        self._tmp.cleanup()

    def attempt(self, code: str, passed: bool, message: str = "") -> str | None:
        return record_attempt(number=2, title="Memory PASS 판정", statement="문제 설명",
                              code=code, passed=passed, message=message, db_path=self.db)

    def test_a_wrong_attempt_is_kept_not_discarded(self):
        """틀린 시도가 교재다. 정답만 모으면 가르칠 것이 없다."""
        self.assertIsNotNone(self.attempt("return True", False, "기대와 다르다"))
        [item] = Storage(self.db).list_experiences(kind="codegen")
        self.assertEqual(item.verdict, "bad")
        self.assertEqual(item.correction, "기대와 다르다")

    def test_wrong_then_fixed_makes_two_records(self):
        self.attempt("return True", False, "틀림")
        self.attempt("return all(...)", True)
        verdicts = sorted(e.verdict for e in Storage(self.db).list_experiences(kind="codegen"))
        self.assertEqual(verdicts, ["bad", "good"])

    def test_rerunning_unchanged_code_is_not_a_new_attempt(self):
        self.assertIsNotNone(self.attempt("return True", False, "틀림"))
        self.assertIsNone(self.attempt("return True", False, "또 틀림"),
                          "고치지 않고 다시 돌린 것은 새 시도가 아니다")

    def test_empty_code_is_not_recorded(self):
        self.assertIsNone(self.attempt("   ", False, "비어 있음"))

    def test_the_learner_code_is_the_judgment_not_the_statement(self):
        self.attempt("return 42", True)
        [item] = Storage(self.db).list_experiences(kind="codegen")
        self.assertIn("return 42", item.judgment)
        self.assertIn("문제 설명", item.situation)

    def test_recording_can_be_turned_off(self):
        with mock.patch.dict("os.environ", {"JARVIS_STUDY_RECORD": "0"}):
            self.assertFalse(recording_enabled())
            self.assertIsNone(self.attempt("return 1", True))

    def test_storage_failure_does_not_break_studying(self):
        """기록이 실패해도 공부를 막지 않는다."""
        with mock.patch("jarvis.study.Storage", side_effect=__import__("jarvis.storage",
                        fromlist=["StorageError"]).StorageError("boom")):
            self.assertIsNone(self.attempt("return 1", True))


class DefaultDatabaseTests(unittest.TestCase):
    def test_db_is_found_relative_to_the_package_not_the_cwd(self):
        """학습 팩은 바탕화면에서 돌아간다. cwd 기준으로 풀면 거기에 빈 DB 가 새로 생기고
        기록이 사라진다 — 2026-09-21 에 실제로 그렇게 됐다."""
        self.assertTrue(_default_db().is_absolute())
        self.assertTrue(str(_default_db()).startswith(str(ROOT)), _default_db())


if __name__ == "__main__":
    unittest.main()
