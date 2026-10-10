"""승인된 경험을 다음 비슷한 요청에 다시 쓴다 — 가중치를 바꾸지 않는 성장 (ADR-055).

지키는 것:
- 승인된 **train** 분할의 답·정체성 경험만 프롬프트에 들어간다. eval 은 성장을 재는 자라서,
  raw 는 사람이 보지 않아서, route 는 라우터 몫이라서 절대 들어가지 않는다.
- 쓰였으면 답 끝에 그렇다고 적는다. 답을 못 했거나 클라우드로 갔으면 적지 않는다.
- 승인·거절은 사람이 `/review` 로 한다.

예시는 2026-09-25 실제 오답이다: 자동 검색(ADR-053)이 있는데 qwen3.5:122b 가 같은 대화의 앞선
답을 따라 "자연어로는 검색이 자동 실행되지 않는다, /search 가 필요하다" 고 했다.
"""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.app import handle_command  # noqa: E402
from jarvis.config import ConfigError, load_settings  # noqa: E402
from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.lessons import LESSON_MARKER, format_lessons, lesson_notice, lesson_terms, select_lessons  # noqa: E402
from jarvis.providers import Message, MockLLMProvider  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

AUTO_SEARCH = dict(
    situation="사용자가 '자연어로도 처리가 될거애' 라고 물었다. 앞선 답에서 ALEX 는 최신 정보는 /search 명령이 필요하다고 했다.",
    judgment="ALEX 는 자연어로 검색 요청을 해도 앱이 자동으로 웹 검색을 실행하지 않으며 /search 가 필요하다고 답했다.",
    correction="가격·뉴스·최신 버전·추천처럼 최신 정보가 필요한 자연어 질문은 앱이 알아서 웹 검색을 한다(ADR-053). "
               "/search 는 필수가 아니다. 앞선 답이 그렇게 말했다면 틀렸다고 정정한다.",
)
QUESTION = "자연어로 물어봐도 웹 검색이 자동으로 돼?"


def record(eid, *, stage="approved", split="train", kind="answer", verdict="bad",
           created_at="2026-09-25T09:30:00+00:00", **body):
    fields = {**AUTO_SEARCH, **body}
    return {
        "id": eid, "created_at": created_at, "kind": kind,
        "situation": fields["situation"], "judgment": fields["judgment"], "rationale": "", "choice": "",
        "outcome": "", "verdict": verdict, "correction": fields["correction"], "source": f"test:{eid}",
        "model": "qwen3.5:122b", "stage": stage, "split": split, "reviewed_at": None, "reviewed_by": "",
        "content_hash": Storage._experience_hash(fields["situation"], fields["judgment"], "", "", ""),
    }


class TermsTests(unittest.TestCase):
    def test_korean_particles_still_match(self):
        """'인터넷은' 과 '인터넷을' 이 단어로는 다르지만 두 글자 조각은 겹친다."""
        self.assertTrue({"인터", "터넷"} <= lesson_terms("인터넷은 금지야?") & lesson_terms("인터넷을 쓸 수 있다"))

    def test_ascii_words_and_numbers(self):
        self.assertTrue({"rtx", "5090", "search"} <= lesson_terms("RTX 5090 가격 /search"))

    def test_common_endings_do_not_count(self):
        self.assertNotIn("니다", lesson_terms("됩니다 합니다"))


class SelectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = Storage(Path(self._tmp.name) / "t.db")

    def tearDown(self):
        self._tmp.cleanup()

    def pool(self):
        return self.store.list_experiences(limit=1000)

    def test_approved_train_answer_is_selected(self):
        self.store.import_experience(record("a" * 24))
        self.assertEqual([e.id for e in select_lessons(QUESTION, self.pool(), 3)], ["a" * 24])

    def test_raw_rejected_and_route_are_never_selected(self):
        """같은 내용이어도 raw·rejected·route 는 들어가지 않는다 — 넘겨받은 목록이 무엇이든."""
        self.store.import_experience(record("r" * 24, stage="raw"))
        self.store.import_experience(record("j" * 24, stage="rejected"))
        self.store.import_experience(record("o" * 24, kind="route"))
        self.assertEqual(select_lessons(QUESTION, self.pool(), 3), [])

    def test_unrelated_question_gets_nothing(self):
        self.store.import_experience(record("a" * 24))
        self.assertEqual(select_lessons("오늘 저녁 메뉴 뭐 먹을까", self.pool(), 3), [])

    def test_limit_and_zero(self):
        for n in range(3):
            self.store.import_experience(record(f"{n}" * 24, created_at=f"2026-09-25T09:3{n}:00+00:00"))
        self.assertEqual(len(select_lessons(QUESTION, self.pool(), 2)), 2)
        self.assertEqual(select_lessons(QUESTION, self.pool(), 0), [])

    def test_the_users_own_rewording_still_finds_it(self):
        """사용자가 실제로 한 말(2026-09-25). 조사·어미가 달라도 명령어·알아서·검색 세 단어가 겹친다."""
        self.store.import_experience(record("a" * 24))
        got = select_lessons("그 명령어 없어도 너가 알아서 검색할 수있도록 설정해뒀는데?", self.pool(), 3)
        self.assertEqual([e.id for e in got], ["a" * 24])

    def test_a_word_counts_only_when_its_stem_is_there(self):
        """'갑자기' 의 '자기' 가 교훈의 '자기 앞선 답' 과 겹쳐도 어간 '갑자' 가 없으면 나온 것이 아니다."""
        self.store.import_experience(record("a" * 24, situation="ALEX 가 자기 앞선 답을 따랐다. 코드 수정 뒤였다."))
        self.assertEqual(select_lessons("갑자기 코드가 왜나와", self.pool(), 3), [])

    def test_one_stem_counts_once(self):
        """'설정한' 과 '설정한거야' 는 같은 단어다. 두 번 세면 흔한 말 하나로 기준을 넘는다."""
        self.store.import_experience(record("a" * 24, situation="설정 화면에서 바꿨다."))
        self.assertEqual(select_lessons("설정한 모델 설정한거야", self.pool(), 3), [])

    def test_approved_eval_is_used_too_since_adr_057(self):
        """ADR-055 는 eval 을 뺐다. 그러면 사용자가 바로잡은 것 다섯 중 하나가 말없이 적용되지 않는다.
        eval 은 가중치 학습에서만 빠지고, 모델을 잴 때는 교훈을 끈다."""
        self.store.import_experience(record("e" * 24, split="eval"))
        self.assertEqual([e.id for e in select_lessons(QUESTION, self.pool(), 3)], ["e" * 24])

    def test_the_training_pool_for_weights_still_leaves_eval_out(self):
        self.store.import_experience(record("a" * 24))
        self.store.import_experience(record("e" * 24, split="eval"))
        self.assertEqual([e.id for e in select_lessons(QUESTION, self.store.training_pool(), 3)], ["a" * 24])


class FormatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = Storage(Path(self._tmp.name) / "t.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bad_answer_shows_what_was_wrong_and_the_fix(self):
        self.store.import_experience(record("a" * 24))
        text = format_lessons(self.store.list_experiences())
        self.assertIn(LESSON_MARKER, text)
        self.assertIn("그때 판단:", text)
        self.assertIn("바로잡은 것: 가격·뉴스", text)
        self.assertIn("[경험 aaaaaaaa]", text)

    def test_good_answer_is_shown_as_good(self):
        self.store.import_experience(record("g" * 24, verdict="good", correction=""))
        self.assertIn("좋았던 판단:", format_lessons(self.store.list_experiences()))

    def test_long_fields_are_clipped(self):
        self.store.import_experience(record("a" * 24, situation="가" * 5000))
        self.assertLess(len(format_lessons(self.store.list_experiences())), 1500)

    def test_notice(self):
        self.assertEqual(lesson_notice([]), "")
        self.assertIn("aaaaaaaa", lesson_notice(["a" * 24]))


class CapturingProvider(MockLLMProvider):
    def __init__(self, healthy=True):
        super().__init__("general", "G", healthy)
        self.seen: list[Message] = []

    def generate(self, messages, **kwargs):
        self.seen = list(messages)
        return "답"


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = Storage(Path(self._tmp.name) / "t.db")
        self.general = CapturingProvider()

    def tearDown(self):
        self._tmp.cleanup()

    def core(self, **overrides):
        options = dict(
            storage=self.store,
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=self.general, local_coder=self.general,
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True,
        )
        options.update(overrides)
        return JarvisCore(**options)

    def ask(self, core, text=QUESTION):
        return core.handle(core.get_or_create_session(resume=False), text)[0]

    def lesson_messages(self):
        return [m for m in self.general.seen if m.role == "system" and LESSON_MARKER in m.content]

    def test_approved_lesson_reaches_the_model_and_the_answer_says_so(self):
        self.store.import_experience(record("a" * 24))
        answer = self.ask(self.core())
        self.assertEqual(len(self.lesson_messages()), 1)
        self.assertIn("aaaaaaaa", answer)
        # 교훈은 대화 기록 앞, system 자리에 있다. 마지막은 여전히 사용자 질문이다.
        self.assertEqual(self.general.seen[-1].role, "user")

    def test_unapproved_experience_does_not(self):
        self.store.import_experience(record("r" * 24, stage="raw"))
        answer = self.ask(self.core())
        self.assertEqual(self.lesson_messages(), [])
        self.assertNotIn("참고한 경험", answer)

    def test_limit_zero_turns_it_off(self):
        self.store.import_experience(record("a" * 24))
        self.ask(self.core(lesson_context_limit=0))
        self.assertEqual(self.lesson_messages(), [])

    def test_no_notice_when_no_model_answered(self):
        self.store.import_experience(record("a" * 24))
        self.general = CapturingProvider(healthy=False)
        answer = self.ask(self.core())
        self.assertIn("응답할 수 있는 모델이 없습니다", answer)
        self.assertNotIn("참고한 경험", answer)

    def test_lessons_are_not_sent_to_cloud(self):
        self.store.import_experience(record("a" * 24))
        core = self.core()
        lesson = Message("system", format_lessons(self.store.training_pool()))
        self.assertEqual(core._prepare_cloud_messages([lesson]), [])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = Storage(Path(self._tmp.name) / "t.db")
        self.core = JarvisCore(
            storage=self.store,
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=MockLLMProvider("g", "G"), local_coder=MockLLMProvider("c", "C"),
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True,
        )
        self.session = self.core.get_or_create_session(resume=False)

    def tearDown(self):
        self._tmp.cleanup()

    def run_command(self, text):
        return handle_command(self.core, self.session, text)[0]

    def test_review_lists_waiting_experiences(self):
        self.store.import_experience(record("r" * 24, stage="raw"))
        self.assertIn("rrrrrrrr", self.run_command("/review"))

    def test_review_shows_the_whole_record_before_deciding(self):
        self.store.import_experience(record("r" * 24, stage="raw"))
        shown = self.run_command("/review rrrrrr")
        self.assertIn("바로잡은 것: 가격·뉴스", shown)
        self.assertIn("/review rrrrrrrr 승인", shown)

    def test_user_approval_makes_it_a_lesson(self):
        self.store.import_experience(record("r" * 24, stage="raw"))
        self.assertIn("교훈으로 쓰이고", self.run_command("/review rrrrrrrr 승인"))
        [e] = self.store.list_experiences()
        self.assertEqual((e.stage, e.reviewed_by), ("approved", "user(app)"))
        self.assertIn("배워서 쓰는 것      1건", self.run_command("/growth"))

    def test_approving_eval_makes_it_a_lesson_but_not_training_data(self):
        self.store.import_experience(record("e" * 24, stage="raw", split="eval"))
        self.assertIn("가중치 학습에서는 빠집니다", self.run_command("/review eeeeee approve"))
        self.assertIn("배워서 쓰는 것      1건", self.run_command("/growth"))
        self.assertEqual(self.store.training_pool(), [])

    def test_reject(self):
        self.store.import_experience(record("r" * 24, stage="raw"))
        self.run_command("/review rrrrrr 거절")
        self.assertEqual(self.store.list_experiences()[0].stage, "rejected")

    def test_short_or_ambiguous_ids_are_refused(self):
        self.store.import_experience(record("abcdef" + "1" * 18, stage="raw"))
        self.store.import_experience(record("abcdef" + "2" * 18, stage="raw",
                                            situation="다른 상황", judgment="다른 판단"))
        self.assertIn("6자 이상", self.run_command("/review abc"))
        self.assertIn("더 길게", self.run_command("/review abcdef"))
        self.assertEqual({e.stage for e in self.store.list_experiences()}, {"raw"})

    def test_status_names_the_lesson_setting(self):
        self.assertIn("lessons: 최대 3개", self.run_command("/status"))

    def test_export_record_is_not_reported_as_completed_training(self):
        self.store.record_training_run("test-export", "test-model", [])
        report = self.run_command("/growth")
        self.assertIn("실행 기록 1건(그중 실제 LoRA 학습 0건)", report)
        self.assertIn("학습 완료 증거가 아닙니다", report)
        self.assertNotIn("학습 실행 1회", report)

    def test_only_a_finished_lora_training_counts_as_training(self):
        # scripts/lora.py 는 학습이 끝났을 때만 이 말로 시작하는 기록을 남긴다(ADR-084)
        self.store.record_training_run("test-export", "test-model", [])
        self.store.record_training_run("m1", "Qwen/Qwen3-4B-Instruct-2507", [],
                                       notes=self.store.LORA_TRAINED_NOTE + ": cpu 3단계")
        report = self.run_command("/growth")
        self.assertIn("실행 기록 2건(그중 실제 LoRA 학습 1건)", report)
        self.assertIn("ALEX 설정에 자동으로 들어가지 않습니다", report)


class SettingTests(unittest.TestCase):
    def test_env_can_turn_it_off_and_range_is_checked(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "JARVIS_CONFIG_FILE": str(Path(tmp) / "none.json"), "JARVIS_LESSON_CONTEXT_LIMIT": "0"}):
            self.assertEqual(load_settings().lesson_context_limit, 0)
            os.environ["JARVIS_LESSON_CONTEXT_LIMIT"] = "11"
            with self.assertRaises(ConfigError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
