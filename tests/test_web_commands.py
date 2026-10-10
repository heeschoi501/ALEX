"""`/search` 와 `/web` 명령 (ADR-051). 인터넷에 나가지 않는다.

지키는 것:
- 웹 자료는 **LOCAL 모델에게만** 간다. CLOUD 로 절대 넘어가지 않는다.
- 자료는 지시가 아니라 데이터라는 머리말이 붙는다(프롬프트 인젝션 방어).
- 페이지 원문은 세션에 저장하지 않는다. 표지(주소/검색어 + 질문)와 답만 남는다.
- 출처는 모델이 아니라 **앱이** 붙인다.
- 2026-09-25 에 ALEX 가 "인터넷에 접근할 수 없다" 고 답한 원인이던 환경 문장이 바뀌었다.
"""
import tempfile
import unittest
from pathlib import Path

from jarvis.app import handle_command
from jarvis.config import LOCAL_FIELDS, Settings
from jarvis.core import WEB_DISABLED_MESSAGE, WEB_MATERIAL_HEADER, JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider, ProviderError
from jarvis.router import Destination
from jarvis.storage import Storage
from jarvis.tools import SafeFileSearchTool
from jarvis.webread import Page, SearchResult, WebError


class RecordingProvider(MockLLMProvider):
    def __init__(self, name="gpt-oss:20b", fail=False):
        super().__init__(name, "LOCAL")
        self.calls = []
        self.fail = fail

    def generate(self, messages, **kwargs):
        self.calls.append(list(messages))
        if self.fail:
            raise ProviderError("down")
        return "답: 자료 [1] 에 따르면 V13.0 이다."


class RecordingCloud(CloudStubProvider):
    def __init__(self):
        super().__init__("cloud", "CLOUD")
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        return "cloud answer"


PAGE_TEXT = "Icarus Verilog Release Notes. Release V13.0 is the latest. " * 3


class FakeWebReader:
    def __init__(self, *, page=None, results=None, fetch_error=None, search_error=None,
                 failing_urls=()):
        self.page = page or Page("https://steveicarus.github.io/iverilog/", "Icarus Verilog", PAGE_TEXT)
        self.results = results if results is not None else [
            SearchResult("Releases · iverilog", "https://github.com/steveicarus/iverilog/releases", "Release notes"),
            SearchResult("Icarus docs", "https://steveicarus.github.io/iverilog/", "Welcome"),
        ]
        self.fetch_error = fetch_error
        self.search_error = search_error
        self.failing_urls = set(failing_urls)
        self.fetched = []

    def fetch(self, url):
        self.fetched.append(url)
        if self.fetch_error or url in self.failing_urls:
            raise self.fetch_error or WebError("down")
        return Page(url, self.page.title, self.page.text, self.page.truncated)

    def search(self, query):
        if self.search_error:
            raise self.search_error
        return list(self.results)


class WebCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, reader=None, *, provider=None, budget=6000, web=True):
        self.local = provider or RecordingProvider()
        self.cloud = RecordingCloud()
        self.reader = reader if reader is not None else FakeWebReader()
        core = JarvisCore(
            storage=Storage(self.path / "t.db"),
            hardware=HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP),
            local_general=self.local, local_coder=self.local,
            cloud_coder=self.cloud, cloud_reasoning=self.cloud,
            file_search_tool=SafeFileSearchTool(self.path),
            context_char_budget=budget, local_only=True,
            web_reader=self.reader if web else None,
        )
        return core, core.get_or_create_session(False)

    def run_cmd(self, core, sid, text):
        output, keep, _ = handle_command(core, sid, text)
        self.assertTrue(keep)
        return output

    def stored(self, core, sid):
        return [m.content for m in core.storage.get_messages(sid, 50)]

    # ── /search ──

    def test_search_answers_and_the_app_appends_sources(self):
        core, sid = self.make()
        out = self.run_cmd(core, sid, "/search iverilog 최신 버전")
        self.assertIn("V13.0", out)
        self.assertIn("출처", out)
        self.assertIn("[1] Releases · iverilog — https://github.com/steveicarus/iverilog/releases", out)
        self.assertIn("[2] Icarus docs — https://steveicarus.github.io/iverilog/", out)

    def test_search_reads_the_top_page_for_depth(self):
        core, sid = self.make()
        self.run_cmd(core, sid, "/search iverilog")
        self.assertEqual(self.reader.fetched, ["https://github.com/steveicarus/iverilog/releases"])
        material = self.local.calls[0][1].content
        self.assertIn("Release V13.0 is the latest", material)
        self.assertIn("[1] 본문", material)

    def test_search_falls_back_to_the_second_page(self):
        reader = FakeWebReader(failing_urls={"https://github.com/steveicarus/iverilog/releases"})
        core, sid = self.make(reader)
        self.run_cmd(core, sid, "/search iverilog")
        self.assertEqual(reader.fetched[-1], "https://steveicarus.github.io/iverilog/")
        self.assertIn("[2] 본문", self.local.calls[0][1].content)

    def test_search_still_answers_from_snippets_when_no_page_reads(self):
        core, sid = self.make(FakeWebReader(fetch_error=WebError("down")))
        out = self.run_cmd(core, sid, "/search iverilog")
        self.assertIn("출처", out)
        self.assertNotIn("본문", self.local.calls[0][1].content)

    def test_search_failure_is_reported_and_nothing_is_stored(self):
        core, sid = self.make(FakeWebReader(search_error=WebError("검색엔진이 막았습니다")))
        out = self.run_cmd(core, sid, "/search iverilog")
        self.assertIn("검색하지 못했습니다", out)
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.stored(core, sid), [])

    def test_empty_search_shows_usage(self):
        core, sid = self.make()
        self.assertIn("사용법", self.run_cmd(core, sid, "/search"))
        self.assertIn("사용법", self.run_cmd(core, sid, "/search    "))

    # ── /web ──

    def test_web_reads_the_given_page_with_the_question(self):
        core, sid = self.make()
        out = self.run_cmd(core, sid, "/web https://steveicarus.github.io/iverilog/ :: 최신 버전은?")
        self.assertEqual(self.reader.fetched, ["https://steveicarus.github.io/iverilog/"])
        self.assertEqual(self.local.calls[0][-1].content, "최신 버전은?")
        self.assertIn("[1] Icarus Verilog — https://steveicarus.github.io/iverilog/", out)

    def test_web_argument_forms(self):
        cases = {
            "/web https://a.example/x :: 질문": ("https://a.example/x", "질문"),
            "/web https://a.example/x::질문": ("https://a.example/x", "질문"),
            "/web https://a.example/x 질문": ("https://a.example/x", "질문"),
            "/web https://a.example/x": ("https://a.example/x", "이 페이지의 핵심을 한국어로 정리해줘."),
            "/web http://[2606:4700::1111]/": ("http://[2606:4700::1111]/", "이 페이지의 핵심을 한국어로 정리해줘."),
        }
        for command, (url, question) in cases.items():
            core, sid = self.make()
            self.run_cmd(core, sid, command)
            self.assertEqual(self.reader.fetched, [url], command)
            self.assertEqual(self.local.calls[0][-1].content, question, command)

    def test_web_without_argument_shows_usage(self):
        core, sid = self.make()
        self.assertIn("사용법", self.run_cmd(core, sid, "/web"))

    def test_web_fetch_failure_is_reported_and_nothing_is_stored(self):
        core, sid = self.make(FakeWebReader(fetch_error=WebError("사설/로컬 주소로는 나가지 않습니다")))
        out = self.run_cmd(core, sid, "/web http://127.0.0.1:11434/ :: 읽어")
        self.assertIn("페이지를 읽지 못했습니다", out)
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.stored(core, sid), [])

    # ── 공통 경계 ──

    def test_material_is_marked_as_data_not_instructions(self):
        core, sid = self.make()
        self.run_cmd(core, sid, "/search iverilog")
        roles = [m.role for m in self.local.calls[0]]
        self.assertEqual(roles, ["system", "system", "user"])
        self.assertTrue(self.local.calls[0][1].content.startswith(WEB_MATERIAL_HEADER))

    def test_page_text_is_not_persisted_in_the_session(self):
        core, sid = self.make()
        self.run_cmd(core, sid, "/web https://steveicarus.github.io/iverilog/ :: 최신 버전은?")
        stored = "\n".join(self.stored(core, sid))
        self.assertIn("[web read: https://steveicarus.github.io/iverilog/] 최신 버전은?", stored)
        self.assertNotIn("Release V13.0 is the latest", stored)

    def test_web_material_never_goes_to_the_cloud_even_when_local_fails(self):
        core, sid = self.make(provider=RecordingProvider(fail=True))
        out = self.run_cmd(core, sid, "/search iverilog")
        self.assertEqual(self.cloud.calls, 0)
        self.assertIn("Cloud 로는 보내지 않았습니다", out)
        self.assertIn("출처", out)

    def test_a_huge_page_is_cut_to_the_context_budget(self):
        huge = Page("https://big.example/", "Big", "datasheet line. " * 5000)
        core, sid = self.make(FakeWebReader(page=huge), budget=6000)
        self.run_cmd(core, sid, "/web https://big.example/ :: 요약")
        total = sum(len(m.content) for m in self.local.calls[0])
        self.assertLess(total, 6000 + 800)

    def test_disabled_web_says_so(self):
        core, sid = self.make(web=False)
        self.assertEqual(self.run_cmd(core, sid, "/search iverilog"), WEB_DISABLED_MESSAGE)
        self.assertEqual(self.run_cmd(core, sid, "/web https://a.example/"), WEB_DISABLED_MESSAGE)

    # ── ALEX 가 자기 능력을 아는가 ──

    def test_environment_line_no_longer_says_there_is_no_internet(self):
        core, _ = self.make()
        line = core._environment_line(Destination.LOCAL_GENERAL)
        self.assertNotIn("외부 API 로 나가지 않는다", line)
        self.assertIn("/search", line)
        self.assertIn("결제", line)

    def test_environment_line_when_web_is_off(self):
        core, _ = self.make(web=False)
        self.assertIn("웹 읽기 기능은 꺼져 있다", core._environment_line(Destination.LOCAL_GENERAL))

    def test_environment_line_says_cloud_exists_but_is_switched_off(self):
        """2026-09-25 에 ALEX 는 '클라우드 연결 기능이 없다, 방화벽을 풀어야 한다' 고 지어냈다."""
        line = self.make()[0]._environment_line(Destination.LOCAL_GENERAL)
        for fact in ("OpenAI", "Anthropic", "Gemini", "꺼져 있다", "화면에 켜는 메뉴는 없다",
                     "자기 API", "설정 파일에는 넣지 않는다",
                     "웹 채팅 화면은 켜져 있어도 클라우드 전송을 거부한다"):
            self.assertIn(fact, line)

    def test_environment_line_separates_local_only_from_internet(self):
        """재측정에서 ALEX 는 '인터넷이 금지' 라 하고 /search 가 클라우드로 간다고 했다."""
        line = self.make()[0]._environment_line(Destination.LOCAL_GENERAL)
        self.assertIn("인터넷 금지가 아니다", line)
        self.assertIn("클라우드 LLM 을 부르는 것이 아니다", line)

    def test_environment_line_tells_the_model_not_to_invent_app_internals(self):
        line = self.make()[0]._environment_line(Destination.LOCAL_GENERAL)
        self.assertIn("지어내지 말고", line)
        self.assertIn("/status", line)
        self.assertIn("확인했다고 말하지 않는다", line)
        self.assertIn("명령은 /help 에 있는 것만 있다", line)

    def test_environment_line_when_cloud_is_enabled(self):
        core, _ = self.make()
        core.local_only = False
        line = core._environment_line(Destination.LOCAL_GENERAL)
        self.assertIn("클라우드 모델 연결 설정: cloud", line)
        self.assertIn("승인", line)

    def test_status_shows_whether_web_is_on(self):
        core, sid = self.make()
        self.assertIn("web: on", self.run_cmd(core, sid, "/status"))
        core, sid = self.make(web=False)
        self.assertIn("web: off", self.run_cmd(core, sid, "/status"))

    def test_help_lists_both_commands(self):
        core, sid = self.make()
        help_text = self.run_cmd(core, sid, "/help")
        self.assertIn("/search", help_text)
        self.assertIn("/web", help_text)


class ChatRoutedWebTests(unittest.TestCase):
    """명령어 없이 채팅에서 "검색해줘" 라고 하면 바로 되는가 (2026-09-25 사용자 요청)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, web=True, reader=None):
        self.local = RecordingProvider()
        self.reader = reader or FakeWebReader()
        core = JarvisCore(
            storage=Storage(self.path / "t.db"),
            hardware=HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP),
            local_general=self.local, local_coder=self.local,
            cloud_coder=RecordingCloud(), cloud_reasoning=RecordingCloud(),
            file_search_tool=SafeFileSearchTool(self.path), local_only=True,
            web_reader=self.reader if web else None,
        )
        return core, core.get_or_create_session(False)

    def test_asking_in_plain_words_searches(self):
        core, sid = self.make()
        answer, decision = core.handle(sid, "iverilog 최신 버전 검색해줘")
        self.assertEqual(decision.destination, Destination.TOOL_WEB_SEARCH)
        self.assertIn("출처", answer)
        self.assertEqual(self.reader.fetched[0], "https://github.com/steveicarus/iverilog/releases")

    def test_two_searches_and_a_url_are_all_read_in_one_turn(self):
        """The user's real request used to read only the URL and miss both searches."""
        class DistinctResults(FakeWebReader):
            def __init__(self):
                super().__init__()
                self.queries = []

            def search(self, query):
                self.queries.append(query)
                if query == 'iverilog 최신 버전':
                    return [SearchResult('Icarus releases', 'https://github.com/steveicarus/iverilog/releases', 'V13.0')]
                if query == 'RTX 5080 VRAM 용량':
                    return [SearchResult('NVIDIA RTX 5080', 'https://www.nvidia.com/rtx-5080', '16 GB GDDR7')]
                raise AssertionError(f'wrong search query: {query}')

        reader = DistinctResults()
        core, sid = self.make(reader=reader)
        prompt = ('iverilog 최신 버전 검색해줘 RTX 5080 VRAM 용량 좀 찾아봐 '
                  'https://steveicarus.github.io/iverilog/ 여기서 최신 버전 알려줘')
        answer, decision = core.handle(sid, prompt)
        self.assertEqual(decision.destination, Destination.TOOL_WEB_READ)
        self.assertEqual(reader.queries, ['iverilog 최신 버전', 'RTX 5080 VRAM 용량'])
        self.assertEqual(reader.fetched, ['https://steveicarus.github.io/iverilog/'])
        self.assertEqual(len(self.local.calls), 1)
        material = self.local.calls[0][1].content
        self.assertIn('Release V13.0 is the latest', material)
        self.assertIn('16 GB GDDR7', material)
        self.assertIn('RTX 5080 VRAM 용량', material)
        self.assertIn('[3] NVIDIA RTX 5080', answer)

    def test_two_searches_without_url_both_reach_web_reader(self):
        class RecordingSearch(FakeWebReader):
            def __init__(self):
                super().__init__()
                self.queries = []

            def search(self, query):
                self.queries.append(query)
                return super().search(query)

        reader = RecordingSearch()
        core, sid = self.make(reader=reader)
        core.handle(sid, 'iverilog 최신 버전 검색해줘 RTX 5080 VRAM 용량 좀 찾아봐')
        self.assertEqual(reader.queries, ['iverilog 최신 버전', 'RTX 5080 VRAM 용량'])

    def test_partial_multi_search_failure_is_explicit(self):
        class PartialFailure(FakeWebReader):
            def search(self, query):
                if 'RTX' in query:
                    raise WebError('검색엔진 응답 없음')
                return super().search(query)

        core, sid = self.make(reader=PartialFailure())
        answer, _ = core.handle(sid, 'iverilog 최신 버전 검색해줘 RTX 5080 VRAM 용량 좀 찾아봐')
        self.assertIn('출처', answer)
        self.assertIn('읽지 못한 요청: 검색 RTX 5080 VRAM 용량', answer)

    def test_local_file_request_next_to_url_is_not_sent_to_search_engine(self):
        class NoSearch(FakeWebReader):
            def search(self, query):
                raise AssertionError(f'local file leaked into web search: {query}')

        reader = NoSearch()
        core, sid = self.make(reader=reader)
        from jarvis.router import explicit_web_queries
        self.assertEqual(explicit_web_queries('프로젝트에서 router.py 파일 찾아줘 https://example.com'), [])
        _, decision = core.handle(sid, '코드에서 버그 찾아줘 https://example.com 설명해줘')
        self.assertEqual(decision.destination, Destination.TOOL_WEB_READ)
        self.assertEqual(reader.fetched, ['https://example.com'])

    def test_the_model_answers_what_the_user_actually_said(self):
        """검색엔진에는 부탁 말투를 걷어낸 말을, 모델에게는 사용자가 한 말 그대로를 준다."""
        core, sid = self.make()
        core.handle(sid, "RTX 5080 가격 좀 찾아봐")
        self.assertEqual(self.local.calls[0][-1].content, "RTX 5080 가격 좀 찾아봐")

    def test_a_pasted_url_is_read_with_its_case_intact(self):
        core, sid = self.make()
        _, decision = core.handle(sid, "이 페이지 요약해줘 https://GitHub.com/SteveIcarus/iverilog/Releases")
        self.assertEqual(decision.destination, Destination.TOOL_WEB_READ)
        self.assertEqual(self.reader.fetched, ["https://GitHub.com/SteveIcarus/iverilog/Releases"])
        self.assertEqual(self.local.calls[0][-1].content, "이 페이지 요약해줘")

    def test_the_exchange_is_recorded_once_as_a_normal_turn(self):
        core, sid = self.make()
        core.handle(sid, "iverilog 최신 버전 검색해줘")
        stored = [m.content for m in core.storage.get_messages(sid, 50)]
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0], "iverilog 최신 버전 검색해줘")
        self.assertNotIn("Release V13.0 is the latest", "\n".join(stored))

    def test_search_with_nothing_to_search_asks_what(self):
        core, sid = self.make()
        answer, _ = core.handle(sid, "검색해봐")
        self.assertIn("무엇을 검색할까요", answer)
        self.assertEqual(self.local.calls, [])

    def test_web_failure_becomes_a_readable_answer(self):
        core, sid = self.make(reader=FakeWebReader(search_error=WebError("검색엔진이 막았습니다")))
        answer, _ = core.handle(sid, "iverilog 최신 버전 검색해줘")
        self.assertIn("검색하지 못했습니다", answer)

    def test_with_web_off_the_request_goes_to_the_model(self):
        core, sid = self.make(web=False)
        _, decision = core.handle(sid, "iverilog 최신 버전 검색해줘")
        self.assertEqual(decision.destination, Destination.LOCAL_GENERAL)
        self.assertEqual(decision.matched, "web-disabled")

    def test_capability_question_is_not_a_search(self):
        core, sid = self.make()
        _, decision = core.handle(sid, "인터넷에서 찾아줄 수 있어?")
        self.assertEqual(decision.destination, Destination.LOCAL_GENERAL)
        self.assertEqual(self.reader.fetched, [])

    def test_a_price_question_searches_without_being_asked_to(self):
        """ADR-053. 사용자가 데스크톱에서 실제로 보낸 문장 — 부탁 말이 없는데도 출처가 붙어야 한다."""
        core, sid = self.make()
        answer, decision = core.handle(sid, "26년 현재기준 rtx5090 가격은?")
        self.assertEqual(decision.destination, Destination.TOOL_WEB_SEARCH)
        self.assertIn("출처", answer)
        self.assertTrue(self.reader.fetched)

    def test_a_question_about_alex_is_answered_by_the_model_without_the_web(self):
        core, sid = self.make()
        _, decision = core.handle(sid, "모델 변경됐는데, 지금 최신 학습은 언제가 마지막이야?")
        self.assertEqual(decision.destination, Destination.LOCAL_GENERAL)
        self.assertEqual(self.reader.fetched, [])

    def test_environment_line_says_the_app_searches_on_its_own(self):
        """모델이 '인터넷에 직접 접속할 수 없다' 고 답해 사용자가 기능이 없는 줄 알았다(2026-09-25)."""
        core, _ = self.make()
        line = core._environment_line(Destination.LOCAL_GENERAL)
        self.assertIn("알아서 검색", line)
        self.assertIn("인터넷에 접근할 수 없다고 말하지 않는다", line)


class WebSettingTests(unittest.TestCase):
    def test_on_by_default(self):
        self.assertTrue(Settings().web_enabled)

    def test_can_be_set_from_the_local_config_file(self):
        self.assertIs(LOCAL_FIELDS["web_enabled"], bool)


if __name__ == "__main__":
    unittest.main()
