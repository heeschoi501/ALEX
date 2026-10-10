"""경험 저장소 회귀 테스트 (ADR-037).

여기서 지키는 불변식은 넷이다.
  1. 본문은 불변이다 (content_hash 로 검증).
  2. 학습/평가 분할은 ID 해시로 결정적이다 (사람이 고르지 않는다).
  3. 검토를 거치지 않은 경험은 학습 풀에 들어갈 수 없다.
  4. 평가용은 어떤 경로로도 학습 풀에 섞이지 않는다.
"""
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage, StorageError


class ExperienceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Storage(Path(self.temp.name) / "t.db")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, n=1, kind="answer"):
        return [
            self.store.record_experience(kind, f"상황 {i}", f"판단 {i}", rationale="근거",
                                         source=f"test:{i}")
            for i in range(n)
        ]

    # ── 기본 ──────────────────────────────────────────────────────────────
    def test_record_requires_situation_and_judgment(self):
        with self.assertRaises(StorageError):
            self.store.record_experience("answer", "   ", "판단")
        with self.assertRaises(StorageError):
            self.store.record_experience("answer", "상황", "")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(StorageError):
            self.store.record_experience("nonsense", "상황", "판단")

    def test_unknown_stage_is_rejected(self):
        (eid,) = self.add(1)
        with self.assertRaises(StorageError):
            self.store.review_experience(eid, "shipped")

    # ── 불변식 1: 본문 불변 ────────────────────────────────────────────────
    def test_review_does_not_change_body(self):
        (eid,) = self.add(1)
        before = self.store.list_experiences(limit=1)[0]
        self.store.review_experience(eid, "approved", correction="고침")
        after = [e for e in self.store.list_experiences(limit=10) if e.id == eid][0]
        self.assertEqual(before.situation, after.situation)
        self.assertEqual(before.judgment, after.judgment)
        self.assertEqual(before.content_hash, after.content_hash)
        self.assertEqual(after.stage, "approved")
        self.assertEqual(after.correction, "고침")

    def test_integrity_check_catches_tampering(self):
        (eid,) = self.add(1)
        self.assertEqual(self.store.verify_experience_integrity(), [])
        # DB 를 직접 건드려 본문을 위조한다.
        # sqlite3 커넥션의 with 는 commit 만 하고 close 하지 않는다 —
        # Windows 에서는 파일이 잠겨 tearDown 이 실패하므로 closing() 으로 감싼다.
        with closing(sqlite3.connect(self.store.db_path)) as conn:
            conn.execute("UPDATE experiences SET judgment = '위조' WHERE id = ?", (eid,))
            conn.commit()
        self.assertEqual(self.store.verify_experience_integrity(), [eid])

    # ── 불변식 2: 결정적 분할 ──────────────────────────────────────────────
    def test_split_is_deterministic(self):
        for eid in self.add(20):
            self.assertEqual(Storage._split_for(eid), Storage._split_for(eid))

    def test_split_uses_both_buckets(self):
        self.add(60)
        counts = self.store.experience_counts()
        self.assertTrue(any(k.endswith("/train") for k in counts), counts)
        self.assertTrue(any(k.endswith("/eval") for k in counts), counts)

    def test_split_is_not_assigned_by_caller(self):
        """record_experience 에 split 을 넘길 방법이 없어야 한다."""
        import inspect
        params = inspect.signature(self.store.record_experience).parameters
        self.assertNotIn("split", params)

    # ── 불변식 3: 검토 없이는 학습 불가 ─────────────────────────────────────
    def test_raw_experiences_are_not_trainable(self):
        self.add(30)
        self.assertEqual(self.store.training_pool(), [])

    def test_rejected_experiences_are_not_trainable(self):
        ids = self.add(30)
        for eid in ids:
            self.store.review_experience(eid, "rejected")
        self.assertEqual(self.store.training_pool(), [])

    # ── 불변식 4: 평가셋 오염 차단 ─────────────────────────────────────────
    def test_training_pool_never_contains_eval(self):
        ids = self.add(60)
        for eid in ids:                      # 전부 승인해도
            self.store.review_experience(eid, "approved")
        pool = self.store.training_pool()
        self.assertTrue(pool, "승인했는데 학습 풀이 비어 있으면 표본이 부족한 것입니다")
        self.assertTrue(all(e.split == "train" for e in pool))
        self.assertTrue(all(e.stage == "approved" for e in pool))

    def test_train_and_eval_sets_are_disjoint(self):
        ids = self.add(60)
        for eid in ids:
            self.store.review_experience(eid, "approved")
        train = {e.id for e in self.store.training_pool()}
        evals = {e.id for e in self.store.list_experiences(stage="approved", split="eval",
                                                          limit=1000)}
        self.assertEqual(train & evals, set())
        self.assertEqual(len(train) + len(evals), len(ids))

    # ── 출처 추적 ─────────────────────────────────────────────────────────
    def test_training_run_records_lineage(self):
        ids = self.add(40)
        for eid in ids:
            self.store.review_experience(eid, "approved")
        pool = [e.id for e in self.store.training_pool()]
        self.store.record_training_run("ALEX-M1", "qwen3:8b-q4_K_M", pool, notes="첫 학습")
        self.assertEqual(sorted(self.store.experiences_in_run("ALEX-M1")), sorted(pool))

    def test_training_run_requires_id(self):
        with self.assertRaises(StorageError):
            self.store.record_training_run("  ", "base", [])

    # ── 기억과 분리 ───────────────────────────────────────────────────────
    def test_experiences_are_separate_from_memories(self):
        self.store.remember("선호언어", "한국어")
        self.add(3)
        self.assertEqual(list(self.store.list_memories()), ["선호언어"])
        self.assertEqual(len(self.store.list_experiences(limit=10)), 3)

    def test_filters(self):
        self.add(5, kind="answer")
        self.add(5, kind="route")
        self.assertEqual(len(self.store.list_experiences(kind="route", limit=50)), 5)
        self.assertEqual(len(self.store.list_experiences(kind="answer", limit=50)), 5)


if __name__ == "__main__":
    unittest.main()
