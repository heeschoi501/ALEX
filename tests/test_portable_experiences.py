"""경험을 기기 간에 옮길 때 train/eval 분할이 보존되는지 (ADR-037, ADR-041).

**여기서 틀리면 평가셋이 오염된다.** `record_experience` 로 다시 기록하면 새 id 가 생기고
split 은 id 해시에서 나오므로(`_split_for`), 같은 내용이 노트북에서는 train
데스크톱에서는 eval 이 될 수 있다. 그러면 한쪽이 학습한 것을 다른 쪽이 평가에 쓴다.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.portable import (BUNDLE_FORMAT, export_portable_state,  # noqa: E402
                            import_portable_state, PortableStateError)
from jarvis.storage import Storage, StorageError  # noqa: E402


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.laptop = Storage(self.dir / "laptop.db")
        self.desktop = Storage(self.dir / "desktop.db")
        # split 은 id 해시에서 나오고 id 는 타임스탬프를 포함하므로 실행마다 달라진다.
        # 8건을 고정으로 넣으면 EVAL_RATIO=0.2 에서 0.8**8 = 16.8% 확률로 전부 train
        # 이 되어 아래 두 테스트가 무의미해진다. 양쪽이 나올 때까지 넣는다.
        self.count = 0
        while True:
            self.laptop.record_experience("codegen", situation=f"과제 {self.count}",
                                          judgment=f"code {self.count}", verdict="bad",
                                          source=f"study:{self.count:02d}")
            self.count += 1
            splits = {e.split for e in self.laptop.list_experiences()}
            if self.count >= 8 and splits == {"train", "eval"}:
                break
            if self.count >= 200:
                raise AssertionError(f"200건을 넣어도 한쪽 split 만 나왔다: {splits}")

    def tearDown(self):
        self._tmp.cleanup()

    def _move(self) -> dict:
        return import_portable_state(self.desktop, export_portable_state(
            self.laptop, self.dir / "b.json"))

    def test_ids_are_preserved(self):
        self._move()
        self.assertEqual({e.id for e in self.laptop.list_experiences()},
                         {e.id for e in self.desktop.list_experiences()})

    def test_train_eval_split_is_preserved(self):
        """이 테스트가 이 파일의 이유다."""
        self._move()
        before = {e.id: e.split for e in self.laptop.list_experiences()}
        after = {e.id: e.split for e in self.desktop.list_experiences()}
        self.assertEqual(before, after)

    def test_the_fixture_actually_contains_both_splits(self):
        """한쪽만 있으면 위 테스트가 아무것도 지키지 못한다."""
        splits = {e.split for e in self.laptop.list_experiences()}
        self.assertEqual(splits, {"train", "eval"}, splits)

    def test_importing_twice_adds_nothing(self):
        self._move()
        stats = self._move()
        self.assertEqual(stats["experiences_added"], 0)
        self.assertEqual(stats["experiences_skipped"], self.count)

    def test_training_pool_stays_consistent_across_devices(self):
        for item in self.laptop.list_experiences():
            self.laptop.review_experience(item.id, "approved")
        self._move()
        for item in self.desktop.list_experiences():
            self.desktop.review_experience(item.id, "approved")
        self.assertEqual({e.id for e in self.laptop.training_pool()},
                         {e.id for e in self.desktop.training_pool()})


class TamperTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.src = Storage(self.dir / "s.db")
        self.dst = Storage(self.dir / "d.db")
        for i in range(3):
            self.src.record_experience("codegen", situation=f"s{i}", judgment=f"j{i}")
        self.bundle = export_portable_state(self.src, self.dir / "b.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _rewrite(self, mutate) -> dict:
        payload = json.loads(self.bundle.read_text(encoding="utf-8"))
        mutate(payload)
        self.bundle.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return import_portable_state(self.dst, self.bundle)

    def test_edited_body_is_rejected_because_the_hash_no_longer_matches(self):
        stats = self._rewrite(lambda p: p["experiences"][0].update(judgment="몰래 바꾼 답"))
        self.assertEqual(stats["experiences_rejected"], 1)
        self.assertEqual(stats["experiences_added"], 2, "나머지는 계속 받아야 한다")

    def test_one_bad_record_does_not_abort_the_whole_move(self):
        stats = self._rewrite(lambda p: p["experiences"].insert(0, {"id": "x"}))
        self.assertEqual(stats["experiences_rejected"], 1)
        self.assertEqual(stats["experiences_added"], 3)

    def test_unknown_kind_is_rejected(self):
        stats = self._rewrite(lambda p: p["experiences"][0].update(kind="아무거나"))
        self.assertEqual(stats["experiences_rejected"], 1)

    def test_bad_split_value_is_rejected(self):
        stats = self._rewrite(lambda p: p["experiences"][0].update(split="둘다"))
        self.assertEqual(stats["experiences_rejected"], 1)

    def test_v1_bundles_without_experiences_still_load(self):
        """이미 만들어둔 번들을 못 읽게 하면 기기 이동이라는 목적이 깨진다."""
        payload = json.loads(self.bundle.read_text(encoding="utf-8"))
        old = {"format": BUNDLE_FORMAT, "version": 1, "exported_at": payload["exported_at"],
               "memories": {}, "tasks": []}
        path = self.dir / "v1.json"
        path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        stats = import_portable_state(self.dst, path)
        self.assertEqual(stats["experiences_added"], 0)


class ImportExperienceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = Storage(Path(self._tmp.name) / "t.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_required_field_raises(self):
        with self.assertRaises(StorageError):
            self.store.import_experience({"id": "abc"})


if __name__ == "__main__":
    unittest.main()
