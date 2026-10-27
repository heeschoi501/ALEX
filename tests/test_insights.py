"""코딩 절차 교훈(ADR-070, ExpeL 방식) — 목록 연산·예산·켜짐일 때만 붙이기·/dev-learn·/dev-insights."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from jarvis.insights import Insight, InsightStore, apply_operations, prompt_block, trajectory_text
from jarvis.storage import Storage
from jarvis.workspace import SafeWorkspaceEditor

MODEL_OUTPUT = ("FILE: calc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a - b\n=======\n"
                "def add(a, b):\n    return a + b\n>>>>>>> REPLACE\n")


class OperationTests(unittest.TestCase):
    def test_add_edit_votes_and_removal_at_zero(self):
        items, applied = apply_operations([], "설명은 무시된다.\nADD: 트레이스백이 테스트에서 끝나면 테스트 코드를 먼저 읽는다\n"
                                              "ADD: 새 함수보다 지워진 줄을 되살리는 최소 수정을 먼저 시도한다", "run1")
        self.assertEqual([(i.id, i.count) for i in items], [(1, 2), (2, 2)])
        self.assertEqual(applied, ["ADD 1", "ADD 2"])
        items, applied = apply_operations(items, "UPVOTE 1\nEDIT 2: 지워진 줄을 되살리는 최소 수정을 먼저\nDOWNVOTE 2\nDOWNVOTE 2\n"
                                                 "DOWNVOTE 2\nUPVOTE 9", "run2")
        self.assertEqual([(i.id, i.count) for i in items], [(1, 3)])  # 2는 3→0 으로 지워졌다, 없는 9는 무시
        self.assertIn("run2", items[0].sources)

    def test_environment_failures_and_negative_tool_claims_are_not_learned(self):
        # 2026-10-26(Hermes Agent 의 'Do NOT capture'): 환경 탓 실패·'도구가 안 된다' 는 교훈이 되면 고쳐진 뒤에도 스스로를 막는다.
        from jarvis.insights import apply_operations_detailed
        items, applied, skipped = apply_operations_detailed([], "\n".join([
            "ADD: pytest 가 설치되지 않았으니 unittest 만 쓴다",
            "ADD: ModuleNotFoundError: No module named pygame 이면 그 과제를 건너뛴다",
            "ADD: 하네스의 bash 도구는 작동하지 않으니 시험을 돌리지 않는다",
            "ADD: The grep tool does not work on Windows, read whole files instead",
            "ADD: 트레이스백의 마지막 프레임부터 읽고 그 함수의 호출자를 grep 으로 찾는다",
        ]), "run1")
        self.assertEqual([i.text for i in items], ["트레이스백의 마지막 프레임부터 읽고 그 함수의 호출자를 grep 으로 찾는다"])
        self.assertEqual((applied, skipped), (["ADD 1"], ["environment", "environment", "negative_claim", "negative_claim"]))
        items, applied, skipped = apply_operations_detailed(items, "EDIT 1: 인터넷이 막혀 있으니 웹 문서를 찾지 않는다", "run2")
        self.assertEqual((items[0].text, applied, skipped),
                         ("트레이스백의 마지막 프레임부터 읽고 그 함수의 호출자를 grep 으로 찾는다", [], ["negative_claim"]))

    def test_the_list_is_capped_and_duplicates_are_skipped(self):
        text = "\n".join(f"ADD: 교훈 {n}" for n in range(20)) + "\nADD: 교훈 0"
        items, _ = apply_operations([], text, "run")
        self.assertEqual(len(items), 12)

    def test_prompt_block_keeps_the_strongest_within_budget(self):
        items = [Insight(1, "약한 것 " * 5, 2), Insight(2, "강한 것", 5)]
        block = prompt_block(items, budget=80)
        self.assertIn("강한 것", block)
        self.assertLess(len(block), 100)
        self.assertEqual(prompt_block([]), "")

    def test_an_oversized_insight_does_not_hide_a_shorter_one(self):
        block = prompt_block([Insight(1, "X" * 220, 5), Insight(2, "short", 3)], budget=100)
        self.assertIn("short", block)
        self.assertNotIn("X" * 20, block)
        self.assertLessEqual(len(block), 100)

    def test_trajectory_text_carries_failures_rejections_and_the_fix(self):
        text = trajectory_text({"run_id": "r1", "goal": "고쳐", "result": "copy_passed", "rounds": [
            {"round": 1, "exit": 1, "failure": "AssertionError: 1 != 2", "rejected": "찾을 부분을 찾지 못했습니다"},
            {"round": 2, "exit": 1, "failure": "x", "diff": "-a = 0\n+a = 1"}, {"round": 3, "exit": 0}]})
        for piece in ("copy_passed", "AssertionError", "찾을 부분을", "+a = 1", "3회차 종료 코드 0"):
            self.assertIn(piece, text)

    def test_a_long_trajectory_keeps_the_final_fix_and_shortens_early_rounds(self):
        """2026-09-30 Codex 리뷰: 앞에서부터 자르면 통과한 마지막 수정이 빠졌다."""
        rounds = [{"round": n, "exit": 1, "failure": "E" * 800, "diff": "-x\n+y" * 200} for n in (1, 2, 3)]
        rounds.append({"round": 4, "exit": 1, "failure": "F", "diff": "-final_bug\n+final_fix"})
        rounds.append({"round": 5, "exit": 0})
        text = trajectory_text({"run_id": "r", "goal": "g", "result": "copy_passed", "rounds": rounds}, limit=1200)
        self.assertLessEqual(len(text), 1200)
        self.assertIn("+final_fix", text)
        self.assertIn("5회차 종료 코드 0", text)
        self.assertIn("1회차 종료 코드 1 (수정 적용)", text)  # 앞 회차는 한 줄로


class EvidenceAndValidationTests(unittest.TestCase):
    def test_after_evidence_says_when_the_full_suite_failed(self):
        """Codex 리뷰 R05: 선택 테스트만 통과하고 전체 검사에서 새 실패를 낸 것을 성공처럼 주지 않는다."""
        from jarvis.insights import after_evidence
        auto = {"type": "auto", "run_id": "r", "result": "copy_passed", "proposal_id": "p1", "rounds": []}
        events = [auto, {"type": "applied", "proposal_id": "p1"}, {"type": "tests", "exit": 0},
                  {"type": "suite", "result": "FAIL", "new_failures": ["test_x (m.T.test_x)"]}]
        evidence = after_evidence(events, auto)
        self.assertIn("적용함", evidence)
        self.assertIn("선택 테스트 통과", evidence)
        self.assertIn("전체 검사 FAIL(새 실패 1: test_x", evidence)
        self.assertIn("사후 확인", trajectory_text(auto, after=evidence))
        self.assertIn("제안하지 않음", after_evidence([], {"result": "not_passed"}))

    def test_damaged_or_odd_files_are_read_safely_and_backed_up_before_saving(self):
        """Codex 리뷰 R06: null·문자열 점수·중복 번호를 건너뛰고, 손상된 파일은 덮어쓰기 전에 남긴다."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coding-insights.json"
            path.write_text(json.dumps({"insights": [{"id": 1, "text": "좋은 교훈", "count": 3},
                                                     {"id": 1, "text": "중복 번호", "count": 2},
                                                     {"id": 2, "text": "문자열 점수", "count": "9"}, None,
                                                     {"id": 3, "text": "  ", "count": 2}], "learned": None}), encoding="utf-8")
            store = InsightStore(path)
            self.assertEqual([(i.id, i.text) for i in store.load()], [(1, "좋은 교훈")])
            self.assertEqual(store.skipped, 4)
            self.assertEqual(store.learned(), [])
            path.write_text("{깨진 json", encoding="utf-8")
            self.assertEqual(store.load(), [])
            store.save([Insight(1, "새 교훈", 2)], ["k"])
            self.assertEqual(len(list(Path(tmp).glob("coding-insights.json.damaged-*"))), 1)
            self.assertEqual(store.learned(), ["k"])

    def test_evidence_stays_with_its_own_proposal(self):
        """Codex 2차 R2-01: 같은 작업의 다른 수정안(p2)의 검사 결과가 p1 의 사후 확인에 붙지 않는다(두 순서 모두)."""
        from jarvis.insights import after_evidence

        def run(first_result, second_result):
            a1 = {"type": "auto", "result": "copy_passed", "proposal_id": "p1"}
            a2 = {"type": "auto", "result": "copy_passed", "proposal_id": "p2"}
            return a1, a2, [a1, {"type": "applied", "proposal_id": "p1", "digest": "A"},
                            {"type": "suite", "result": first_result, "digest": "A"},
                            a2, {"type": "applied", "proposal_id": "p2", "digest": "B"},
                            {"type": "suite", "result": second_result, "digest": "B"}]

        a1, a2, events = run("FAIL", "PASS")
        self.assertIn("전체 검사 FAIL", after_evidence(events, a1))
        self.assertIn("전체 검사 PASS", after_evidence(events, a2))
        a1, a2, events = run("PASS", "FAIL")
        self.assertIn("전체 검사 PASS", after_evidence(events, a1))
        self.assertIn("전체 검사 FAIL", after_evidence(events, a2))
        discarded = {"type": "auto", "result": "copy_passed", "proposal_id": "p1"}
        events = [discarded, {"type": "discarded", "proposal_id": "p1"},
                  {"type": "auto", "result": "copy_passed", "proposal_id": "p2"},
                  {"type": "applied", "proposal_id": "p2", "digest": "B"}, {"type": "suite", "result": "PASS", "digest": "B"}]
        self.assertIn("폐기함", after_evidence(events, discarded))
        self.assertNotIn("PASS", after_evidence(events, discarded))
        lone = {"type": "auto", "result": "copy_passed", "proposal_id": "p1"}
        invalid = [lone, {"type": "applied", "proposal_id": "p1", "digest": "A"},
                   {"type": "suite", "result": "PASS", "digest": "A", "valid": False}]
        self.assertIn("전체 검사 확인 안 됨", after_evidence(invalid, lone))

    def test_repeated_auto_records_cannot_borrow_later_evidence(self):
        from jarvis.insights import after_evidence
        for proposal in ("p", ""):
            with self.subTest(proposal=proposal):
                first = {"type": "auto", "result": "copy_passed", "proposal_id": proposal}
                second = dict(first)  # 내용은 같아도 다른 작업 기록이다.
                events = [first, {"type": "applied", "proposal_id": proposal, "digest": "A"},
                          {"type": "suite", "result": "PASS", "digest": "A"},
                          second, {"type": "applied", "proposal_id": proposal, "digest": "B"},
                          {"type": "suite", "result": "FAIL", "digest": "B"}]
                self.assertIn("전체 검사 PASS", after_evidence(events, first))
                self.assertIn("전체 검사 FAIL", after_evidence(events, second))

    def test_a_valid_json_with_bad_content_is_kept_before_saving(self):
        """Codex 2차 R2-03: 문법은 맞지만 틀린 항목이 든 파일도 저장 전에 원본 바이트를 남긴다."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coding-insights.json"
            original = json.dumps({"insights": [{"id": 1, "text": "좋은 교훈", "count": 3}, {"id": "x", "text": "틀림"}],
                                   "learned": ["k"]}).encode()
            path.write_bytes(original)
            store = InsightStore(path)
            self.assertEqual(store.problem(), "형식이 틀린 교훈 1개")
            store.save(store.load(), store.learned())
            backups = list(Path(tmp).glob("coding-insights.json.damaged-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertEqual(store.problem(), "")
            path.write_text(json.dumps({"insights": None}), encoding="utf-8")
            self.assertEqual(store.problem(), "insights 가 목록이 아님")

    def test_invalid_operations_are_not_consumed_but_an_intended_no_change_is(self):
        """Codex 2차 R2-04: 내용 없는 ADD·없는 번호·ADDITION 같은 글은 해석 실패, 이미 있는 교훈 ADD 는 의도된 무변경."""
        from jarvis.insights import apply_operations_detailed, learning_consumed
        base = [Insight(1, "테스트 코드를 먼저 읽는다", 3)]
        for text in ("ADD:", "UPVOTE 999", "ADDITION of a new helper", "EDIT 1:"):
            _items, applied, skipped = apply_operations_detailed(base, text, "s")
            self.assertFalse(learning_consumed(applied, skipped), text)
        _items, applied, skipped = apply_operations_detailed(base, "ADD: 테스트 코드를 먼저 읽는다", "s")
        self.assertEqual((applied, skipped), ([], ["duplicate"]))
        self.assertTrue(learning_consumed(applied, skipped))

    def test_already_learned_records_are_skipped_and_duplicates_counted_once(self):
        """Codex 2차 R2-05: 추출 스크립트를 다시 돌려도 처리한 기록은 다시 투표하지 않는다."""
        from jarvis.insights import learning_key, unlearned
        autos = [{"run_id": "a", "at": "1"}, {"run_id": "b", "at": "2"}, {"run_id": "b", "at": "2"}]
        self.assertEqual(unlearned(autos, ["a@1"]), [({"run_id": "b", "at": "2"}, False)])  # 옛 표시(run@at)도 처리한 것으로
        # Codex 3차 R3-02: 배운 뒤 사후 확인이 바뀌면 한 번 더(다시 본 것으로 표시), 같은 사후 확인이면 다시 안 나온다
        evidence = {"a": "사후 확인: 적용함 · 전체 검사 FAIL"}
        again = unlearned(autos[:1], ["a@1"], lambda e: evidence.get(e["run_id"], ""))
        self.assertEqual(again, [({"run_id": "a", "at": "1"}, True)])
        key = learning_key(autos[0], evidence["a"])
        self.assertEqual(unlearned(autos[:1], ["a@1", key], lambda e: evidence.get(e["run_id"], "")), [])

    def test_output_without_operations_is_not_marked_as_learned(self):
        from jarvis.insights import has_operations
        self.assertFalse(has_operations("교훈을 정리하면 다음과 같습니다."))
        self.assertTrue(has_operations("설명\nUPVOTE 1"))


class CoreAndCommandTests(unittest.TestCase):
    def setUp(self):
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider, MockLLMProvider

        test = self

        class Model(MockLLMProvider):
            def generate(self, messages, **kwargs):
                test.seen = "".join(m.content for m in messages)
                return test.reply

        self.reply = MODEL_OUTPUT
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "project"
        self.root.mkdir()
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        self.storage = Storage(base / "data" / "alex.db")
        self.core = JarvisCore(
            storage=self.storage, hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("general", "GENERAL"), local_coder=Model("coder", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            workspace_editor=SafeWorkspaceEditor(self.root, base / "backups", storage=self.storage))
        self.session = self.core.get_or_create_session(False)
        self.store = InsightStore.beside(self.storage)
        self.store.save([Insight(1, "테스트 코드를 먼저 읽는다", 3)])

    def tearDown(self):
        self.temp.cleanup()

    def say(self, text):
        from jarvis.app import handle_command
        return handle_command(self.core, self.session, text)[0]

    def test_insights_reach_the_edit_prompt_only_when_enabled(self):
        self.core.propose_bundle_local(self.session, ["calc.py"], "add 를 고쳐")
        self.assertNotIn("테스트 코드를 먼저 읽는다", self.seen)  # 기본 꺼짐
        self.core.coding_insights = True
        self.core.propose_bundle_local(self.session, ["calc.py"], "add 를 고쳐")
        self.assertIn("절차 교훈", self.seen)
        self.assertIn("테스트 코드를 먼저 읽는다", self.seen)

    def test_the_same_insights_reach_the_coding_harness_only_when_enabled(self):
        # 2026-10-23 claude: ALEX 하네스(ADR-112) 지시문에도 같은 절차 교훈 — 설정이 꺼져 있으면 비움
        self.assertEqual(self.core.coding_insight_text(), "")
        self.core.coding_insights = True
        self.assertIn("테스트 코드를 먼저 읽는다", self.core.coding_insight_text())
        from jarvis.harness.prompt import system_prompt
        self.assertIn("테스트 코드를 먼저 읽는다", system_prompt(self.root, lessons=self.core.coding_insight_text()))

    def test_code_session_records_are_learned_and_counted(self):
        # 2026-10-23 claude: 코딩 세션 기록(type code)도 /dev-learn 재료 — 사후 확인은 '독립 관련 검사'
        from jarvis.developer import DeveloperMode, record_outcome
        record_outcome(self.storage, {"type": "code", "run_id": "ses_a@1", "goal": "add 고쳐", "result": "verified",
                                      "rounds": [{"round": 1, "exit": 1, "failure": "AssertionError"},
                                                 {"round": 2, "exit": 0, "diff": "-a - b\n+a + b"}]})
        record_outcome(self.storage, {"type": "code", "run_id": "ses_b@2", "goal": "x", "result": "stopped", "rounds": []})
        self.assertEqual(DeveloperMode(self.core).learning_pending(), 1)  # 검사 판정이 없는 기록은 재료가 아니다
        self.reply = "ADD: 실패한 관련 검사 출력을 먼저 읽고 같은 원인을 고친다"
        learned = self.say("/dev-learn")
        self.assertIn("교훈 연산 1개", learned)
        self.assertIn("독립 관련 검사 통과", self.seen)
        self.assertIn("+a + b", self.seen)
        self.assertEqual(DeveloperMode(self.core).learning_pending(), 0)

    def test_a_reviewed_insight_becomes_a_harness_skill(self):
        from jarvis.harness.tools import find_skills
        self.core.current_project_root = lambda: self.root
        reply = self.say("/dev-insights skill 1 read-tests-first")
        self.assertIn("스킬로 올렸습니다", reply)
        skills = find_skills(self.root / ".alex" / "skills")
        self.assertEqual(skills["read-tests-first"].description, "테스트 코드를 먼저 읽는다")
        self.assertIn("이미 있는 스킬", self.say("/dev-insights skill 1 read-tests-first"))
        self.assertIn("그 번호의 교훈이 없습니다", self.say("/dev-insights skill 9"))
        self.assertIn("영문 소문자", self.say("/dev-insights skill 1 Bad_Name"))

    def test_learn_reads_new_auto_records_once_and_the_list_can_be_shown_and_trimmed(self):
        outcomes = Path(self.storage.db_path).parent / "dev-outcomes.jsonl"
        outcomes.write_text(json.dumps({"type": "auto", "run_id": "r1", "at": "2026-09-30T00:00:00+00:00", "goal": "고쳐",
                                        "result": "copy_passed", "rounds": [{"round": 1, "exit": 1, "failure": "E"},
                                                                            {"round": 2, "exit": 0}]},
                                       ensure_ascii=False) + "\n", encoding="utf-8")
        self.reply = "UPVOTE 1\nADD: 지워진 줄을 되살리는 최소 수정을 먼저 시도한다"
        learned = self.say("/dev-learn")
        self.assertIn("교훈 연산 2개", learned)
        self.assertIn("(점수 4) 테스트 코드를 먼저 읽는다", learned)
        self.assertIn("최소 수정", learned)
        self.assertIn("새로 배울", self.say("/dev-learn"))  # 같은 기록은 다시 쓰지 않는다
        outcomes.write_text(outcomes.read_text(encoding="utf-8") + json.dumps(
            {"type": "auto", "run_id": "r2", "at": "2026-09-30T01:00:00+00:00", "goal": "또", "result": "not_passed",
             "rounds": [{"round": 1, "exit": 1}]}, ensure_ascii=False) + "\n", encoding="utf-8")
        self.reply = "교훈으로 정리하면 이렇습니다."  # 연산 줄이 없다
        self.assertIn("찾지 못했습니다", self.say("/dev-learn"))
        self.reply = "DOWNVOTE 1"
        self.assertIn("교훈 연산 1개", self.say("/dev-learn"))  # 해석 실패였던 기록을 다시 썼다
        # 배운 뒤 r1 을 적용하고 전체 검사가 실패했다 → 사후 확인이 바뀌었으니 한 번 더, '이미 반영' 표시와 함께(Codex 3차 R3-02)
        outcomes.write_text(outcomes.read_text(encoding="utf-8") + "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in (
            {"type": "applied", "run_id": "r1", "proposal_id": "", "digest": "D1"},
            {"type": "suite", "run_id": "r1", "result": "FAIL", "digest": "D1", "new_failures": ["test_x (m.T.test_x)"]})),
            encoding="utf-8")
        self.reply = "DOWNVOTE 1"
        self.assertIn("교훈 연산 1개", self.say("/dev-learn"))
        self.assertIn("이미 한 번 반영한 기록", self.seen)
        self.assertIn("전체 검사 FAIL", self.seen)
        self.assertIn("새로 배울", self.say("/dev-learn"))  # 같은 사후 확인으로는 다시 나오지 않는다
        listing = self.say("/dev-insights")
        self.assertIn("꺼짐", listing)
        second = re.search(r"^(\d+)\. \(점수 2\) 지워진", listing, re.M).group(1)
        self.assertEqual(self.say(f"/dev-insights remove {second}"), "지웠습니다.")
        self.assertNotIn("최소 수정", self.say("/dev-insights"))
        self.assertIn("사용법", self.say("/dev-insights delete x"))


if __name__ == "__main__":
    unittest.main()
