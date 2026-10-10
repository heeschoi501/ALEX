"""문서에 명시된 정정을 경험으로 수확하는 경로 (GPT_NEXT.md 2026-09-21 명세).

**이 데이터가 쓸 만한 이유는 verdict 가 모델 바깥에서 오기 때문이다.** 아홉 건 모두
측정값이 반박했거나 사람이 지적한 것이고, 문서에 '정정' 이라고 명시돼 있다.
"정정처럼 보이는 문장" 을 자동으로 골라내면 그것이 곧 모델의 자기 채점이 된다 —
그래서 목록은 손으로 고르고, 스크립트는 **그 근거가 문서에 아직 있는지만** 확인한다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from experiences import CORRECTIONS  # noqa: E402
from jarvis.storage import Storage  # noqa: E402


class CorrectionTableTests(unittest.TestCase):
    def test_every_anchor_still_exists_in_its_document(self):
        """닻이 사라졌다면 기록이 원본과 어긋난 것이다."""
        for item in CORRECTIONS:
            text = (ROOT / item["file"]).read_text(encoding="utf-8")
            self.assertIn(item["anchor"], text,
                          f"{item['source']}: {item['file']} 에서 닻을 찾지 못했다")

    def test_sources_are_unique(self):
        sources = [c["source"] for c in CORRECTIONS]
        self.assertEqual(len(sources), len(set(sources)))

    def test_every_entry_has_a_correction(self):
        """정정이 없으면 학습 신호가 없다. 틀린 판단만 있고 답이 없는 셈이다."""
        for item in CORRECTIONS:
            self.assertTrue(item["correction"].strip(), item["source"])

    def test_judgment_and_correction_differ(self):
        for item in CORRECTIONS:
            self.assertNotEqual(item["judgment"].strip(), item["correction"].strip())

    def test_all_five_body_fields_are_present(self):
        for item in CORRECTIONS:
            for field in ("situation", "judgment", "rationale", "outcome"):
                self.assertTrue(str(item.get(field, "")).strip(),
                                f"{item['source']} 에 {field} 가 비어 있다")

    def test_kind_is_accepted_by_the_store(self):
        self.assertIn("identity", Storage.EXPERIENCE_KINDS)


if __name__ == "__main__":
    unittest.main()
