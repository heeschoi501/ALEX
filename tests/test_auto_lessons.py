"""틀린 것·몰랐던 것을 자동으로 배운다 (ADR-057, 사용자 결정 2026-09-25 "ALEX 가 찾은 것까지 자동").

- 사용자가 대화에서 바로잡으면("틀렸어…", "아니야, …", "…잖아", "…텐데?", /bad 이유) 그 말이 곧 승인이다.
- 출처 있는 웹 자료로 답하면 그 답을 날짜·출처와 함께 배운다. 금방 바뀌는 것·못 찾은 답은 배우지 않고,
  90일이 지나면 쓰지 않는다.
- 배우면 답 끝에서 알리고, 되돌리는 명령을 같이 보인다. 가중치 학습 후보(training_pool)와는 별개다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.app import handle_command  # noqa: E402
from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.lessons import (  # noqa: E402
    FOUND_SOURCE_PREFIX, LESSON_MARKER, looks_like_correction, looks_like_undo_learning, select_lessons, usable)
from jarvis.providers import MockLLMProvider  # noqa: E402
from jarvis.storage import Storage  # noqa: E402
from jarvis.webread import SearchResult  # noqa: E402


class FakeWeb:
    def search(self, query):
        return [SearchResult("Qwen3.8 모델 카드", "https://example.com/qwen3.8", "2026년 8월 공개")]

    def fetch(self, url):
        from jarvis.webread import WebError
        raise WebError("본문은 읽지 않는다")


class Model(MockLLMProvider):
    """정해 둔 답을 하고, 도구가 오면 한 번 검색한다. 받은 메시지는 남긴다."""

    def __init__(self, answer="Qwen3.8 은 2026년 8월에 공개됐습니다 [1]", search=None):
        super().__init__("general", "G")
        self.answer = answer
        self.search = search
        self.seen = []

    def generate(self, messages, **kwargs):
        self.seen = list(messages)
        if self.search and "tool_runner" in kwargs:
            kwargs["tool_runner"]("web_search", {"query": self.search})
        return self.answer


class CorrectionModel(Model):
    """표시 없는 반박을 모델이 알아챘을 때의 도구 호출을 재현한다."""

    def __init__(self):
        super().__init__(answer="앞서 제가 틀렸습니다. 바로잡아 주셔서 감사합니다.")
        self.tools_seen = []
        self.tool_result = ""

    def generate(self, messages, **kwargs):
        self.tools_seen.append([t["function"]["name"] for t in kwargs.get("tools", [])])
        if messages[-1].content.startswith("2026년 2월 기준") and "tool_runner" in kwargs:
            self.tool_result = kwargs["tool_runner"]("remember_correction", {"right": "모델이 지어낸 요약"})
        return self.answer


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Storage(Path(self._tmp.name) / "t.db")

    def core(self, model, **overrides):
        options = dict(
            storage=self.store,
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=model, local_coder=model,
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True, web_reader=FakeWeb(),
        )
        options.update(overrides)
        return JarvisCore(**options)

    def learned(self):
        """교훈으로 쓰이는 것: 승인된 것 + 자동으로 찾은 사실(candidate)."""
        return [e for e in self.store.list_experiences() if usable(e)]


class DetectionTests(unittest.TestCase):
    def test_what_counts_as_a_correction(self):
        for text in ("틀렸어, qwen3.8은 8월에 나왔어", "아니야, 검색은 앱이 자동으로 해", "브라우저 검색 되잖아..",
                     "지금 데스크탑 모델은 다를텐데? 노트북 기준이야??", "그건 아니고 16GB 야"):
            self.assertTrue(looks_like_correction(text), text)

    def test_what_does_not(self):
        for text in ("아니 괜찮아", "아니 그냥 궁금해서 물어본거야", "내가 틀렸나?", "어떻게 하는데?",
                     "코드 수정됐을텐데 다시 확인해봐", "/bad 틀림"):
            self.assertFalse(looks_like_correction(text), text)

    def test_natural_undo_phrases(self):
        for text in ("방금 배운 거 취소", "그거 배우지 마", "잘못 배웠어", "저장한 내용 지워"):
            self.assertTrue(looks_like_undo_learning(text), text)
        for text in ("어떻게 배우는 거야?", "그 내용을 설명해줘", "이건 취소해줘"):
            self.assertFalse(looks_like_undo_learning(text), text)


class CorrectionTests(Base):
    def test_unmarked_pushback_is_recorded_from_the_users_words(self):
        model = CorrectionModel()
        core = self.core(model, web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "Qwen 모델의 학습 기준은 언제야?")
        self.assertNotIn("remember_correction", model.tools_seen[0])
        reply, _ = core.handle(session, "2026년 2월 기준 모델이야. 다시 확인해줘")
        [lesson] = self.learned()
        self.assertEqual(lesson.reviewed_by, "user(chat)")
        self.assertEqual(lesson.correction, "사용자가 바로잡았다: 2026년 2월 기준 모델이야. 다시 확인해줘")
        self.assertNotIn("모델이 지어낸 요약", lesson.correction)
        self.assertIn("remember_correction", model.tools_seen[1])
        self.assertIn("앞선 답이 틀렸으면", model.tool_result)
        self.assertIn("앞서 제가 틀렸습니다", reply)

    def test_a_correction_in_chat_is_learned_and_announced(self):
        model = Model(answer="자연어로는 검색이 자동으로 되지 않습니다. /search 를 쓰셔야 합니다.")
        core = self.core(model, web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "자연어로도 검색이 될까?")
        reply, _ = core.handle(session, "틀렸어, 가격이나 최신 정보는 앱이 알아서 검색해")
        [lesson] = self.learned()
        self.assertEqual(lesson.reviewed_by, "user(chat)")
        self.assertIn("자연어로도 검색이 될까?", lesson.situation)
        self.assertIn("/search 를 쓰셔야", lesson.judgment)
        self.assertIn("앱이 알아서 검색해", lesson.correction)
        self.assertIn("바로잡아 주신 내용을 배웠습니다", reply)
        self.assertIn("'방금 배운 거 취소'", reply)

    def test_the_next_similar_question_uses_it(self):
        core = self.core(Model(answer="자연어로는 검색이 자동으로 되지 않습니다."), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "자연어로도 검색이 될까?")
        core.handle(session, "틀렸어, 앱이 알아서 검색해")
        model = Model(answer="네, 됩니다.")
        later = self.core(model, web_reader=None)
        reply, _ = later.handle(later.get_or_create_session(resume=False), "자연어로 물어봐도 검색이 될까?")
        self.assertTrue(any(LESSON_MARKER in m.content for m in model.seen if m.role == "system"))
        self.assertIn("참고한 경험", reply)

    def test_correction_survives_storage_reopen_and_new_session_then_undo(self):
        core = self.core(Model(answer="자연어 검색이 안 됩니다."), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "자연어로도 검색이 될까?")
        core.handle(session, "틀렸어, 앱이 알아서 검색해")
        [saved] = self.learned()
        self.store = Storage(Path(self._tmp.name) / "t.db")
        model = Model(answer="검색할 수 있습니다.")
        later = self.core(model, web_reader=None)
        fresh_session = later.get_or_create_session(resume=False)
        self.assertNotEqual(session, fresh_session)
        reply, _ = later.handle(fresh_session, "자연어로 물어봐도 검색이 될까?")
        self.assertIn(saved.id[:8], reply)
        self.assertTrue(any(LESSON_MARKER in m.content for m in model.seen))
        later.undo_auto_learning(saved.id[:8])
        self.store = Storage(Path(self._tmp.name) / "t.db")
        final = self.core(model, web_reader=None)
        reply, _ = final.handle(final.get_or_create_session(resume=False), "자연어로 물어봐도 검색이 될까?")
        self.assertNotIn("참고한 경험", reply)
        self.assertFalse(any(LESSON_MARKER in m.content for m in model.seen))

    def test_no_previous_answer_or_no_marker_means_nothing_is_learned(self):
        core = self.core(Model(), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "틀렸어, 처음 하는 말이야")  # 바로잡을 직전 답이 없다
        core.handle(session, "고마워, 이제 다른 얘기 하자")
        self.assertEqual(self.learned(), [])

    def test_the_same_correction_is_learned_once_and_the_switch_turns_it_off(self):
        core = self.core(Model(answer="틀린 답"), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "질문 하나")
        core.handle(session, "틀렸어, 이게 맞아")
        self.assertEqual(len(self.learned()), 1)
        off = self.core(Model(answer="틀린 답"), web_reader=None, auto_lessons=False)
        other = off.get_or_create_session(resume=False)
        off.handle(other, "질문 둘")
        off.handle(other, "틀렸어, 이게 맞아")
        self.assertEqual(len(self.learned()), 1)

    def test_natural_undo_only_reverses_the_last_learning_in_this_session(self):
        core = self.core(Model(answer="틀린 답"), web_reader=None)
        first = core.get_or_create_session(resume=False)
        second = core.get_or_create_session(resume=False)
        core.handle(first, "첫 번째 질문")
        core.handle(first, "틀렸어, 첫 번째 교정이야")
        first_lesson = self.learned()[0]
        core.handle(second, "두 번째 질문")
        core.handle(second, "틀렸어, 두 번째 교정이야")
        second_lesson = next(e for e in self.learned() if e.id != first_lesson.id)
        reply, _ = core.handle(first, "방금 배운 거 취소")
        self.assertIn(first_lesson.id[:8], reply)
        stages = {e.id: e.stage for e in self.store.list_experiences()}
        self.assertEqual(stages[first_lesson.id], "rejected")
        self.assertEqual(stages[second_lesson.id], "approved")
        self.assertEqual(len(self.store.list_experiences()), 2)
        self.assertIn("취소할 학습이 없습니다", core.handle(first, "방금 배운 거 취소")[0])

    def test_natural_undo_preserves_an_experience_manually_approved_later(self):
        core = self.core(Model(answer="틀린 답"), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "질문")
        core.handle(session, "틀렸어, 정답은 이것")
        lesson = self.learned()[0]
        self.store.review_experience(lesson.id, "approved", reviewed_by="user(app)")
        self.assertIn("취소할 수 있는 자동 학습이 없습니다", core.handle(session, "방금 배운 거 취소")[0])
        self.assertEqual(self.store.list_experiences()[0].stage, "approved")

    def test_button_command_only_cancels_auto_learned_items(self):
        core = self.core(Model(answer="틀린 답"), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "질문")
        core.handle(session, "틀렸어, 정답은 이것")
        lesson = self.learned()[0]
        self.assertIn("취소했습니다", handle_command(core, session, f"/undo-learning {lesson.id[:8]}")[0])
        self.assertEqual(self.store.list_experiences()[0].stage, "rejected")
        self.store.review_experience(lesson.id, "approved", reviewed_by="user(app)")
        self.assertIn("사람이 승인한 기록은 그대로", handle_command(core, session,
                                                      f"/undo-learning {lesson.id[:8]}")[0])
        self.assertEqual(self.store.list_experiences()[0].stage, "approved")

    def test_bad_with_a_reason_is_learned_without_a_reason_it_waits(self):
        core = self.core(Model(answer="답"), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "질문 하나")
        self.assertIn("바로 배웠습니다", handle_command(core, session, "/bad 공개일은 8월이다")[0])
        core.handle(session, "질문 둘")
        self.assertIn("/review 로 검토", handle_command(core, session, "/bad")[0])
        stages = sorted((e.stage, e.reviewed_by) for e in self.store.list_experiences())
        self.assertEqual(stages, [("approved", "user(/bad)"), ("raw", "")])


class FoundFactTests(Base):
    def test_natural_undo_reverses_a_found_fact_from_the_last_answer(self):
        core = self.core(Model(search="Qwen3.8 release date"))
        session = core.get_or_create_session(resume=False)
        core.handle(session, "qwen3.8 모델은 언제 공개됐어?")
        [lesson] = self.learned()
        self.assertEqual(lesson.stage, "candidate")
        reply, _ = core.handle(session, "방금 배운 거 취소")
        self.assertIn(lesson.id[:8], reply)
        self.assertEqual(self.learned(), [])
        self.assertEqual(self.store.list_experiences()[0].stage, "rejected")

    def test_a_sourced_self_search_answer_is_learned_with_date_and_source(self):
        reply, _ = self.core(Model(search="Qwen3.8 release date")).handle(
            self.core(Model()).get_or_create_session(resume=False), "qwen3.8 모델은 언제 공개됐어?")
        [lesson] = self.learned()
        self.assertEqual(lesson.reviewed_by, "auto(found)")
        self.assertTrue(lesson.source.startswith(FOUND_SOURCE_PREFIX))
        self.assertIn("https://example.com/qwen3.8", lesson.outcome)
        self.assertRegex(lesson.judgment, r"^\d{4}-\d{2}-\d{2} 웹에서 찾아 답했다")
        self.assertIn("찾은 내용을 배웠습니다", reply)

    def test_an_explicit_search_route_answer_is_learned_too(self):
        core = self.core(Model(answer="Qwen3.8 은 2026년 8월 공개입니다 [1]"))
        core.handle(core.get_or_create_session(resume=False), "qwen3.8 공개일 검색해줘")
        self.assertEqual([e.reviewed_by for e in self.learned()], ["auto(found)"])

    def test_things_that_change_quickly_and_failed_answers_are_not(self):
        core = self.core(Model(search="RTX 5090 price"))
        core.handle(core.get_or_create_session(resume=False), "RTX 5090 가격 얼마야?")
        core = self.core(Model(answer="자료에 없습니다. 확인하지 못했습니다.", search="qwen3.8 cutoff"))
        core.handle(core.get_or_create_session(resume=False), "qwen3.8 학습 기준 시점은?")
        self.assertEqual(self.learned(), [])

    def test_the_same_question_is_learned_once(self):
        for _ in range(2):
            core = self.core(Model(search="Qwen3.8 release date"))
            core.handle(core.get_or_create_session(resume=False), "qwen3.8 모델은 언제 공개됐어?")
        self.assertEqual(len(self.learned()), 1)

    def test_a_found_fact_is_shown_as_found_and_expires(self):
        core = self.core(Model(search="Qwen3.8 release date"))
        core.handle(core.get_or_create_session(resume=False), "qwen3.8 모델은 언제 공개됐어?")
        model = Model(answer="8월입니다")
        later = self.core(model, web_reader=None)
        later.handle(later.get_or_create_session(resume=False), "qwen3.8 모델 공개가 언제였지?")
        lessons = [m.content for m in model.seen if m.role == "system" and LESSON_MARKER in m.content]
        self.assertTrue(lessons and "[찾은 것 " in lessons[0])
        pool = self.learned()
        soon = datetime.now(timezone.utc) + timedelta(days=10)
        late = datetime.now(timezone.utc) + timedelta(days=100)
        self.assertEqual(len(select_lessons("qwen3.8 모델 공개가 언제였지?", pool, 3, now=soon)), 1)
        self.assertEqual(select_lessons("qwen3.8 모델 공개가 언제였지?", pool, 3, now=late), [])

    def test_a_found_fact_is_a_lesson_but_not_training_data(self):
        """찾은 사실은 대화에서 참고할 뿐이다. 가중치 학습 후보(approved)가 되려면 사람이 /review 로 승인한다."""
        core = self.core(Model(search="Qwen3.8 release date"))
        session = core.get_or_create_session(resume=False)
        core.handle(session, "qwen3.8 모델은 언제 공개됐어?")
        [found] = self.learned()
        self.assertEqual(found.stage, "candidate")
        self.assertEqual(self.store.training_pool(), [])
        handle_command(core, session, f"/review {found.id[:8]} 거절")
        self.assertEqual(self.learned(), [])  # 거절하면 더는 쓰이지 않는다

    def test_a_user_correction_can_become_training_data(self):
        """사용자가 바로잡은 것은 사람이 판단한 것이라 approved — train 분할이면 학습 후보도 된다."""
        core = self.core(Model(answer="틀린 답"), web_reader=None)
        session = core.get_or_create_session(resume=False)
        core.handle(session, "질문 하나")
        core.handle(session, "틀렸어, 이게 맞아")
        [lesson] = self.learned()
        self.assertEqual(lesson.stage, "approved")
        self.assertEqual(len(self.store.training_pool()), 1 if lesson.split == "train" else 0)


if __name__ == "__main__":
    unittest.main()
