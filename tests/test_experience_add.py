"""작업 중에 받은 정정을 그 자리에서 경험으로 남기는 경로 (2026-09-21).

**왜 필요했나.** 경험 저장소를 채우는 문은 `/good` `/bad` 와 `/apply-edit`
`/discard-edit` 뿐이었다. 둘 다 사용자가 ALEX 와 직접 주고받은 것이다.
`import-corrections` 는 문서에 이미 적힌 정정을 뒤늦게 수확하는데, 목록이
`experiences.py` 안에 하드코딩돼 있어 새 정정이 생길 때마다 스크립트를 고쳐야 했다.

그래서 **claude 나 코덱스가 하는 작업은 하나도 들어가지 않았다**(GPT_NEXT.md 2절).
측정에 의해 판단이 교정되는 기록이 ALEX 가 못 배우는 바로 그 행동의 교재인데,
그것을 담을 문이 없었다. `add` 가 그 문이다.

지켜야 할 세 가지를 테스트한다: split 을 받지 않을 것, source 로 중복을 막을 것,
기본이 dry-run 일 것. 본문 5필드는 `content_hash` 로 고정돼 나중에 못 고친다.
"""
import argparse
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from experiences import cmd_add  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

GOOD = {
    "kind": "identity",
    "source": "claude:test-one",
    "situation": "상황",
    "judgment": "측정 없이 단언했다",
    "rationale": "근거",
    "choice": "선택",
    "outcome": "측정이 반박했다",
    "correction": "재보고 말한다",
}


class AddTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = Storage(self.dir / "t.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, payload, write=False):
        path = self.dir / "rec.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return cmd_add(self.store, argparse.Namespace(file=str(path), write=write))

    def _count(self):
        return len(self.store.list_experiences(limit=1000))

    def test_dry_run_stores_nothing(self):
        self._run([GOOD])
        self.assertEqual(self._count(), 0)

    def test_write_stores(self):
        self._run([GOOD], write=True)
        self.assertEqual(self._count(), 1)

    def test_a_single_object_is_accepted_not_only_a_list(self):
        self._run(GOOD, write=True)
        self.assertEqual(self._count(), 1)

    def test_same_source_is_not_stored_twice(self):
        """같은 정정이 두 번 학습되면 안 된다."""
        self._run([GOOD], write=True)
        self._run([GOOD], write=True)
        self.assertEqual(self._count(), 1)

    def test_duplicate_source_inside_one_file_is_caught(self):
        self._run([GOOD, dict(GOOD)], write=True)
        self.assertEqual(self._count(), 1)

    def test_missing_required_field_is_rejected(self):
        broken = dict(GOOD)
        del broken["judgment"]
        rc = self._run([broken], write=True)
        self.assertEqual(self._count(), 0)
        self.assertEqual(rc, 1)

    def test_unknown_kind_is_rejected(self):
        broken = dict(GOOD, kind="lithography")
        self._run([broken], write=True)
        self.assertEqual(self._count(), 0)

    def test_split_cannot_be_set_by_hand(self):
        """ADR-037 결정 5. 사람이 고르면 흥미로운 사례가 학습으로 몰린다."""
        broken = dict(GOOD, split="train")
        self._run([broken], write=True)
        self.assertEqual(self._count(), 0)

    def test_stored_record_stays_raw(self):
        """검토를 거치지 않고 학습에 들어가면 안 된다."""
        self._run([GOOD], write=True)
        item = self.store.list_experiences(limit=1)[0]
        self.assertEqual(item.stage, "raw")
        self.assertEqual(self.store.training_pool(), [])

    def test_body_fields_survive_intact(self):
        self._run([GOOD], write=True)
        item = self.store.list_experiences(limit=1)[0]
        self.assertEqual(item.situation, GOOD["situation"])
        self.assertEqual(item.judgment, GOOD["judgment"])
        self.assertEqual(item.outcome, GOOD["outcome"])
        self.assertEqual(item.correction, GOOD["correction"])

    def test_one_bad_record_does_not_block_the_good_ones(self):
        broken = dict(GOOD, source="claude:test-two", kind="lithography")
        self._run([GOOD, broken], write=True)
        self.assertEqual(self._count(), 1)

    def test_missing_file_is_reported_not_raised(self):
        rc = cmd_add(self.store, argparse.Namespace(
            file=str(self.dir / "nope.json"), write=True))
        self.assertEqual(rc, 2)

    def test_malformed_json_is_reported_not_raised(self):
        path = self.dir / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        rc = cmd_add(self.store, argparse.Namespace(file=str(path), write=True))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
