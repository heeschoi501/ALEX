"""Router edge case tests. 규칙 변경 시 여기부터 깨져야 한다."""
import unittest

from jarvis.router import Destination, explain, route_request


class RouterTests(unittest.TestCase):
    def assert_route(self, text, expected):
        decision = route_request(text)
        self.assertEqual(decision.destination, expected, f"{text!r} -> {explain(decision)}")

    # ---------------------------------------------------------- 기본 5문장
    def test_general_chat(self):
        self.assert_route("안녕. 오늘 뭐부터 할까?", Destination.LOCAL_GENERAL)

    def test_simple_code(self):
        self.assert_route("이 Python 리스트에서 중복 제거 코드 알려줘.", Destination.LOCAL_CODER)

    def test_complex_code(self):
        self.assert_route("3만 줄 프로젝트 전체 구조를 분석해서 리팩터링해줘.", Destination.CLOUD_CODER)

    def test_system_info(self):
        self.assert_route("현재 시스템 RAM과 GPU 알려줘.", Destination.TOOL_SYSTEM_INFO)

    def test_resume(self):
        self.assert_route("지난 세션 이어서 해.", Destination.SESSION_RESUME)

    # ---------------------------------------------------------- 일반 대화
    def test_general_variants(self):
        for text in [
            "오늘 점심 뭐 먹을까?",
            "내일 일정 정리해줘",
            "이 문서 요약해줘",
            "고맙다",
        ]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.LOCAL_GENERAL)

    # ---------------------------------------------------------- 로컬 코더
    def test_local_coder_variants(self):
        for text in [
            "이 함수에서 에러가 나는데 봐줘",
            "정규식으로 날짜만 뽑고 싶어",
            "pandas 로 groupby 하는 법",
            "SQL 쿼리 하나만 짜줘",
            "traceback 해석 좀",
            "파이썬 클래스 예시 보여줘",
        ]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.LOCAL_CODER)

    # ---------------------------------------------------------- 클라우드
    def test_cloud_coder_needs_both_signals(self):
        self.assert_route("코드베이스 전체 구조 리팩터링 해줘", Destination.CLOUD_CODER)
        # 복잡 키워드만 있고 코드 맥락이 없으면 cloud 로 올리지 않는다.
        self.assert_route("우리 팀 조직 아키텍처 어떻게 생각해?", Destination.LOCAL_GENERAL)

    def test_cloud_reasoning(self):
        for text in ["이 명제를 증명해줘", "이 데이터를 심층 분석 해줘"]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.CLOUD_REASONING)

    # ---------------------------------------------------------- Tool
    def test_system_info_variants(self):
        for text in ["내 컴퓨터 사양 알려줘", "vram 얼마나 돼?", "지금 프로필 알려줘"]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.TOOL_SYSTEM_INFO)

    def test_file_search(self):
        for text in ["router 파일 찾아줘", "config 파일 검색", "그 파일 어디 있지"]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.TOOL_FILE_SEARCH)

    # ---------------------------------------------------------- 경계 사례
    def test_resume_exclusion(self):
        """'계속해서 설명해줘' 는 세션 재개가 아니다."""
        self.assert_route("계속해서 설명해줘", Destination.LOCAL_GENERAL)
        self.assert_route("이어서 작성해줘", Destination.LOCAL_GENERAL)

    def test_resume_variants(self):
        for text in ["아까 하던 거 계속하자", "어제 하던 작업 이어서 해", "resume please"]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.SESSION_RESUME)

    def test_empty_and_whitespace(self):
        self.assert_route("", Destination.LOCAL_GENERAL)
        self.assert_route("     ", Destination.LOCAL_GENERAL)

    def test_priority_tool_over_code(self):
        """시스템 정보 키워드가 코드 키워드보다 우선한다."""
        self.assert_route("python 으로 시스템 정보 알려줘", Destination.TOOL_SYSTEM_INFO)

    def test_decision_is_explainable(self):
        decision = route_request("파이썬 함수 알려줘")
        self.assertTrue(decision.reason)
        self.assertTrue(decision.matched)
        self.assertIn("matched", explain(decision))


class PastedTextTests(unittest.TestCase):
    """붙여 넣은 긴 글 속의 낱말로 모델 없는 지름길(시스템 정보·파일 검색·세션 이어가기)을 타지 않는다.

    2026-09-26 사용자가 새 모델을 시험하려고 실습 노트북 문제를 붙여 넣었다. 코드 속 예시 문자열
    '현재 GPU 알려줘' 하나로 시스템 정보 표만 두 번 나왔고 모델은 한 번도 불리지 않았다("답변이 제대로 안나오는데").
    """

    NOTEBOOK = (
        "현재 새모델 설치했고 검증중이야.\n\n## 1. 조건문과 반환값\n\n"
        "`memory_pass(count)`는 기억이 3개 이상이면 `PASS`, 아니면 `FAIL`을 반환해야 합니다.\n"
        "from jarvis.router import route_request\n\n"
        "examples = ['안녕', 'Python 함수 알려줘', '현재 GPU 알려줘']\n"
        "for sentence in examples:\n"
        "    decision = route_request(sentence)\n"
        "    print(sentence, '→', decision.destination.value, '|', decision.reason)\n\n"
        "def memory_pass(count: int) -> str:\n"
        "    raise NotImplementedError('직접 구현해 보세요')\n"
    )

    def test_the_users_pasted_notebook_goes_to_the_model(self):
        decision = route_request(self.NOTEBOOK)
        self.assertEqual(decision.destination, Destination.LOCAL_CODER, explain(decision))

    def test_other_shortcut_words_inside_a_paste_go_to_the_model(self):
        filler = "아래는 어제 회의록이다. 일정과 담당자를 정리해 주면 좋겠다. " * 3
        for words, shortcut in (("config 파일 찾아", Destination.TOOL_FILE_SEARCH),
                                ("지난 세션 이어서 해", Destination.SESSION_RESUME),
                                ("내 컴퓨터 사양 알려줘", Destination.TOOL_SYSTEM_INFO)):
            with self.subTest(words=words):
                self.assertEqual(route_request(words).destination, shortcut)  # 짧으면 여전히 지름길
                decision = route_request(filler + f"'{words}' 라는 말도 있었다.")
                self.assertNotEqual(decision.destination, shortcut, explain(decision))

    def test_a_long_paste_with_fresh_words_is_not_sent_to_the_search_engine(self):
        """알아서 검색도 짧은 질문만 한다(ADR-062 결정 2). 긴 글이 통째로 검색어가 되어 밖으로 나가지 않는다."""
        paste = "아래는 회의록이다. 요즘 반도체 동향과 가격 이야기가 나왔고 최신 장비 도입 일정을 정했다. " * 3
        self.assertGreater(len(paste), 120)
        decision = route_request(paste)
        self.assertNotEqual(decision.destination, Destination.TOOL_WEB_SEARCH, explain(decision))
        self.assertEqual(route_request("요즘 반도체 동향 어때?").destination, Destination.TOOL_WEB_SEARCH)  # 짧으면 그대로
        # 부탁 말이 있으면 길어도 검색한다 — 사용자가 밖으로 보내라고 한 것이다.
        self.assertEqual(route_request(paste + "이 내용 검색해줘").destination, Destination.TOOL_WEB_SEARCH)


class AutoSearchTests(unittest.TestCase):
    """부탁 말 없이도 최신 정보가 있어야 맞는 질문은 검색한다(ADR-053).

    2026-09-25 데스크톱: "26년 현재기준 rtx5090 가격은?" 에 ALEX 는 2024년 지식이라며 /search 를 쓰라고
    했고, "상하이 여행 호텔 추천해줘" 에는 2024년 기준 표를 지어 냈다. 사용자는 "그 명령어 없어도 너가
    알아서 검색할 수 있도록 설정해뒀는데?" 라고 했다. 그 설정은 아직 없었다.
    """

    def assert_route(self, text, expected):
        decision = route_request(text)
        self.assertEqual(decision.destination, expected, f"{text!r} -> {explain(decision)}")
        return decision

    def test_the_users_own_questions_search(self):
        for text in ["26년 현재기준 rtx5090 가격은?", "상하이 여행 호텔 추천해줘"]:
            with self.subTest(text=text):
                decision = self.assert_route(text, Destination.TOOL_WEB_SEARCH)
                self.assertIn("(auto)", decision.reason)

    def test_current_information_searches(self):
        for text in ["요즘 반도체 동향 어때?", "오늘 서울 날씨 어때?", "환율 얼마야?", "최신 pandas 버전은?",
                     "Python 3.14 정식 버전은 언제 나왔어?", "2025년 노벨 물리학상은 누가 받았어?",
                     "노트북 추천해줘"]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.TOOL_WEB_SEARCH)

    def test_questions_about_alex_itself_do_not_search(self):
        """사용자가 모델을 바꾸자마자 실제로 보낸 문장들이다. '최신' 이 있어도 ALEX 자신 이야기다."""
        for text in ["모델 변경됐는데, 지금 최신 학습은 언제가 마지막이야?",
                     "너의 가장 최신 학습은 언제가 마지막이야?",
                     "그 명령어 없어도 너가 알아서 검색할 수있도록 설정해뒀는데?"]:
            with self.subTest(text=text):
                self.assert_route(text, Destination.LOCAL_GENERAL)

    def test_same_words_in_local_work_do_not_search(self):
        cases = [("가격 계산하는 함수 짜줘", Destination.LOCAL_CODER),
                 ("최신 코드 리뷰해줘", Destination.LOCAL_CODER),
                 ("변수 이름 추천해줘", Destination.LOCAL_GENERAL),
                 ("이 가격이면 괜찮아?", Destination.LOCAL_GENERAL),
                 ("26년 계획 세워줘", Destination.LOCAL_GENERAL),
                 ("좋은 소식이 있어", Destination.LOCAL_GENERAL),
                 ("최근 에러 로그 찾아줘", Destination.LOCAL_CODER),
                 ("최신 정보 찾아줄 수 있어?", Destination.LOCAL_GENERAL),
                 ("지난 세션 이어서 해", Destination.SESSION_RESUME)]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assert_route(text, expected)

    def test_search_engine_gets_the_question_not_the_request_manner(self):
        from jarvis.router import web_search_query
        self.assertEqual(web_search_query("26년 현재기준 rtx5090 가격은?"), "26년 현재기준 rtx5090 가격은")


if __name__ == "__main__":
    unittest.main()
