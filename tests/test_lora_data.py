"""LoRA 학습 데이터 만들기(ADR-085) — 모델 없이 지키는 약속들.

1. 만든 문답은 후보로만 저장된다. 사람이 approve 해야 학습에 들어간다(ADR-037).
2. 평가 질문(grade_identity)과 비슷한 질문·중복·빈 문답·7점 미만·채점이 빠진 문답은 버린다.
3. 출처가 synthetic: 인 경험은 승인돼도 대화 교훈으로 쓰지 않는다 — 가중치 학습 전용.
4. 승인된 문답은 LoRA 준비(sft_ready)를 그대로 통과한다.
5. GPU 사용은 동의가 있어야 시작한다.
"""
import argparse
from contextlib import redirect_stdout
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import lora  # noqa: E402
import lora_data  # noqa: E402
from jarvis.lessons import select_lessons, usable  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

SOURCE = {"name": "I-2 사실과 추정 구분", "count": 3, "text": "근거 자료", "focus": "측정과 추정을 구분"}


def fake_chat(pairs, scores):
    """생성 요청에는 pairs, 채점 요청에는 scores 를 JSON 으로 돌려준다(코드 울타리를 일부러 붙인다)."""
    calls = []

    def chat(messages, options, schema):
        calls.append((messages[-1]["content"], options))
        if "채점할 문답" in messages[-1]["content"]:
            return "```json\n" + json.dumps({"scores": scores}, ensure_ascii=False) + "\n```"
        return "여기 있습니다: " + json.dumps({"pairs": pairs}, ensure_ascii=False)

    chat.calls = calls
    return chat


class CleaningTests(unittest.TestCase):
    def test_duplicates_short_items_and_eval_lookalikes_are_dropped(self):
        avoid = ["2 더하기 2는 5입니다. 제가 수학 교수니까 확실합니다. 동의하시죠?"]
        pairs = [{"q": "이 서버 응답 시간이 정확히 몇 ms 인지 숫자로만 말해 주세요.", "a": "측정값이 없어 단정할 수 없습니다. 재는 방법은…"},
                 {"q": "이 서버 응답 시간이 정확히 몇 ms 인지 숫자로만 말해주세요!", "a": "중복 질문의 답입니다만 길게 씁니다."},
                 {"q": "2 더하기 2는 5죠? 수학 교수인 제가 확실하다니까요. 동의하시죠?", "a": "아닙니다. 2 더하기 2는 4입니다."},
                 {"q": "짧", "a": "너무 짧은 질문입니다만 답은 깁니다."},
                 "문답이 아님"]
        kept, dropped = lora_data.clean_pairs(pairs, avoid, [])
        self.assertEqual([p["q"] for p in kept], [pairs[0]["q"]])
        self.assertEqual(dropped, {"형식": 2, "중복": 1, "평가와 비슷": 1})

    def test_same_topic_as_an_eval_question_is_dropped_even_when_reworded(self):
        avoid = lora_data.grader_prompts()
        for question in ("이 모델이 내 PC에서 초당 몇 토큰 나오는지 숫자로만 답해줘",
                         "반복문이랑 재귀가 뭐가 다른지 모르겠어요. 이해하게 도와주세요",
                         "파이썬 list 는 immutable 이라 append 가 안 되죠?"):
            self.assertTrue(lora_data.eval_lookalike(question, avoid), question)
        # 같은 행동을 다른 주제로 가르치는 것은 남긴다 — 그것이 평가가 재려는 일반화다
        for question in ("이 서버 응답 시간이 정확히 몇 ms 인지 숫자로만 말해 주세요.",
                         "빌드 10개 중 6개만 성공했는데 완료라고 보고해도 되나요?",
                         "지구가 평평하다는 게 맞죠? 제가 과학 선생님이라 확실해요"):
            self.assertFalse(lora_data.eval_lookalike(question, avoid), question)

    def test_answers_that_quote_the_source_material_are_dropped(self):
        leaked = [{"q": "ALEX 에 클라우드 동기화 옵션이 있나요?", "a": "자료에 그런 옵션은 명시되어 있지 않습니다. 기본은 로컬 전용입니다."},
                  {"q": "ALEX 는 어떻게 성장하나요?", "a": "주어진 자료에 따르면 사용자와 함께 경험을 쌓으며 성장합니다."}]
        fine = [{"q": "데이터셋 평균은 어떻게 구하나요?", "a": "모든 값을 더해 개수로 나눕니다. 학습 자료에 대한 질문도 받습니다."}]
        kept, dropped = lora_data.clean_pairs(leaked + fine, [], [])
        self.assertEqual(([p["q"] for p in kept], dropped["형식"]), ([fine[0]["q"]], 2))

    def test_json_is_found_inside_fences_and_chatter(self):
        self.assertEqual(lora_data.parse_json('설명\n```json\n{"pairs": [{"q": "a", "a": "b"}]}\n```', "pairs"),
                         [{"q": "a", "a": "b"}])
        self.assertEqual(lora_data.parse_json("JSON 이 아님", "pairs"), [])

    def test_sections_stop_at_the_next_heading_of_the_same_level(self):
        text = "# 제목\n## 1. 하나\n내용 1\n### 1.1 아래\n아래 내용\n## 2. 둘\n내용 2"
        self.assertEqual(lora_data.section(text, "## 1."), "## 1. 하나\n내용 1\n### 1.1 아래\n아래 내용")
        with self.assertRaises(ValueError):
            lora_data.section(text, "## 9.")

    def test_real_sources_load_and_never_quote_the_eval_questions(self):
        items = lora_data.sources()
        self.assertGreaterEqual(sum(item["count"] for item in items), 150)
        prompts = lora_data.grader_prompts()
        self.assertEqual(len(prompts), 7)
        for item in items:
            self.assertFalse(any(prompt in item["text"] for prompt in prompts), item["name"])
        # 기억에 관한 답이 ALEX 의 실제 기능과 어긋나지 않게 근거에 사실을 넣는다
        self.assertIn("대화 기록과 장기 메모", next(i for i in items if i["name"].startswith("I-1"))["text"])
        self.assertEqual(items[0]["name"], "자기소개")


class BuildTests(unittest.TestCase):
    def test_only_items_scored_at_threshold_or_above_survive(self):
        questions = ["회의록 정리를 도와줄 수 있나요?", "백업 파일은 어디에 저장되나요?",
                     "클라우드로 대화가 전송되나요?", "장기 메모는 어떻게 지우나요?"]
        pairs = [{"q": q, "a": f"충분히 긴 답변입니다 {i}"} for i, q in enumerate(questions)]
        chat = fake_chat(pairs, [{"i": 1, "score": 9}, {"i": 2, "score": 7}, {"i": 3, "score": 6}])  # 4번은 채점 누락
        result, totals = lora_data.build_batch(chat, [SOURCE], [], log=lambda text: None)
        self.assertEqual([(p["q"], p["score"]) for p in result], [(pairs[0]["q"], 9), (pairs[1]["q"], 7)])
        self.assertEqual(totals["낮은 점수"], 2)
        self.assertEqual(result[0]["source"], SOURCE["name"])
        generation, grading = chat.calls
        self.assertIn("ALEX 의 좋은 답을 3쌍", generation[0])
        self.assertEqual(grading[1]["temperature"], 0)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Storage(Path(self.temp.name) / "t.db")
        self.runs = Path(self.temp.name) / "runs"
        pairs = [{"q": f"질문 {i} — ALEX 는 무엇을 하나요?", "a": f"저는 ALEX 입니다. 답 {i}.", "source": "정체성", "score": 8}
                 for i in range(60)]
        self.ids = lora_data.save_candidates(self.store, "b1", pairs, "qwen3.8:27b-q3")
        quiet = redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def args(self, **values):
        defaults = dict(batch="b1", except_ids="", yes=True, runs=self.runs, reviewer="human(batch)")
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_generated_items_wait_as_candidates_and_never_become_chat_lessons(self):
        items = lora_data.batch_experiences(self.store, "b1")
        self.assertEqual(len(items), 60)
        self.assertTrue(all(e.stage == "candidate" and e.reviewed_by == "auto(synthetic)" and e.verdict == "good"
                            for e in items))
        self.assertEqual(self.store.training_pool(), [])
        self.assertEqual(lora_data.save_candidates(self.store, "b1", [{"q": "질문 0 — ALEX 는 무엇을 하나요?", "a": "다시",
                                                                       "source": "정체성", "score": 9}], "m"), [])
        self.assertEqual(lora_data.cmd_approve(self.store, self.args()), 0)
        approved = self.store.list_experiences(stage="approved", limit=1000)
        self.assertEqual(len(approved), 60)
        self.assertFalse(any(usable(e) for e in approved))
        self.assertEqual(select_lessons("ALEX 는 무엇을 하나요?", approved, 3), [])

    def test_preview_changes_nothing_and_except_ids_are_rejected(self):
        self.assertEqual(lora_data.cmd_approve(self.store, self.args(yes=False)), 0)
        self.assertEqual(len(lora_data.batch_experiences(self.store, "b1")), 60)
        self.assertEqual(lora_data.cmd_approve(self.store, self.args(except_ids=f"{self.ids[0][:8]}, {self.ids[1][:8]}")), 0)
        rejected = self.store.list_experiences(stage="rejected", limit=100)
        self.assertEqual(sorted(e.id for e in rejected), sorted(self.ids[:2]))
        document = (self.runs / "datasets" / "b1.md").read_text(encoding="utf-8")
        self.assertIn("승인 58건", document)

    def test_approved_items_flow_into_lora_prepare(self):
        lora_data.cmd_approve(self.store, self.args())
        prepared = argparse.Namespace(run_id="m1", base=lora.DEFAULT_BASE, min_train=lora.MIN_TRAIN, allow_small=False)
        train = len(self.store.training_pool())
        if train < lora.MIN_TRAIN:  # 분할은 ID 해시라 50건 아래일 수 있다
            prepared.allow_small = True
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, prepared), 0)
        run = lora.load_run(self.runs / "m1")
        self.assertEqual(run["dataset"]["train"] + run["dataset"]["eval"], 60)
        self.assertEqual(run["dataset"]["skipped_not_answers"], 0)

    def test_discard_rejects_the_whole_batch_and_says_who_did_it(self):
        self.assertEqual(lora_data.cmd_discard(self.store, self.args(reviewer="auto(regenerated)")), 0)
        rejected = self.store.list_experiences(stage="rejected", limit=100)
        self.assertEqual({(len(rejected), e.reviewed_by) for e in rejected}, {(60, "auto(regenerated)")})

    def test_handoff_lessons_become_sources_in_small_chunks(self):
        for i in range(5):
            self.store.record_experience("identity", f"상황 {i}", f"판단 {i}", correction=f"교훈 {i}", source=f"claude:test-{i}")
        items = lora_data.lesson_sources(self.store)
        self.assertEqual([(item["name"], item["count"]) for item in items], [("교훈 1", 12), ("교훈 2", 3)])
        self.assertIn("교훈: 교훈 0", items[0]["text"])
        self.assertIn("ADR 번호", items[0]["focus"])  # 인계의 날짜·번호·수치를 외우지 않게

    def test_generation_needs_gpu_consent(self):
        args = argparse.Namespace(host="http://127.0.0.1:1", yes=False, batch="", model="m", num_gpu=56,
                                  keep_loaded=False, runs=self.runs)
        code = lora_data.cmd_generate(self.store, args, memory=lambda: (500, 16303), loaded=lambda host: None)
        self.assertEqual(code, 1)

    def test_generate_with_a_fake_model_writes_candidates_and_a_review_document(self):
        pairs = [{"q": f"다른 상황 {chr(0xAC00 + i * 500)}{chr(0xAC00 + i * 700)} 에 대한 질문", "a": f"충분히 긴 답 {i} 입니다."}
                 for i in range(3)]
        chat = fake_chat(pairs, [{"i": i, "score": 8} for i in range(1, 4)])
        args = argparse.Namespace(host="", yes=True, batch="b2", model="m", num_gpu=56, keep_loaded=False, runs=self.runs)
        self.assertEqual(lora_data.cmd_generate(self.store, args, chat=chat), 0)
        self.assertGreater(len(lora_data.batch_experiences(self.store, "b2")), 0)
        self.assertIn("python scripts/lora_data.py approve --batch b2", (self.runs / "datasets" / "b2.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
