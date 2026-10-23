"""딥서치의 실제 수집 범위·취소·근거·기존 대화 경로를 외부 통신 없이 검증한다."""
import gzip
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jarvis.app import handle_command
from jarvis.core import JarvisCore, WEB_DISABLED_MESSAGE
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.http_control import RequestControl
from jarvis.providers import GenerationCancelled, MockLLMProvider, ProviderError
from jarvis.research import (AUTO_REASON, Limits, Researcher, Source, _excerpt, automatic_request,
                             canonical, request, web_disabled)
from jarvis.router import Destination
from jarvis.storage import Storage
from jarvis.tools import SafeFileSearchTool
from jarvis.webread import Page, SearchResult, WebError
from test_webread import FakeResponse, reader_returning


class Reader:
    def __init__(self, results=None, pages=None):
        self.results = results or {}
        self.pages = pages or {}
        self.searched, self.fetched = [], []

    def search(self, query):
        self.searched.append(query)
        rows = self.results.get(query, [])
        if isinstance(rows, Exception):
            raise rows
        return rows

    def fetch(self, url):
        self.fetched.append(url)
        page = self.pages.get(url, Page(url, url, "확보한 본문 " + url))
        if isinstance(page, Exception):
            raise page
        return page


class Model:
    def __init__(self, plan=None, review=None, report="확인한 결론입니다. [1]"):
        self.plan = plan if plan is not None else {"queries": ["topic"]}
        self.review = review if review is not None else {"queries": [], "gaps": []}
        self.report = report
        self.calls = []

    def __call__(self, messages, phase):
        self.calls.append((messages, phase))
        value = {"검색 계획": self.plan, "추가 확인": self.review, "보고서": self.report}[phase]
        if isinstance(value, Exception):
            raise value
        return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value


def rows(*urls):
    return [SearchResult("자료 " + url, url, "요약 " + url) for url in urls]


def researcher(reader, model=None, **kwargs):
    return Researcher(reader, model or Model(), safe_query=lambda q: "PRIVATE" not in q, **kwargs)


class RequestTests(unittest.TestCase):
    def test_complex_public_questions_select_deep_without_magic_words(self):
        for text in ("RTX 5080과 5090의 성능, 가격, VRAM을 비교해줘",
                     "현재 Windows에서 쓸 로컬 코딩용 LLM을 성능, 메모리, 라이선스로 비교해줘",
                     "최신 오픈소스 모델들의 장단점과 비용을 종합해서 추천해줘",
                     "이 논문의 상반된 주장을 여러 출처의 근거로 조사해줘",
                     "Ollama와 다른 도구를 공식 문서의 근거로 비교해줘"):
            self.assertEqual(automatic_request(text), text, text)

    def test_simple_local_brief_and_offline_questions_do_not_auto_research(self):
        for text in ("Python 최신 버전은?", "오늘 날씨는?", "리스트와 튜플의 차이가 뭐야?",
                     "내 프로젝트 코드의 성능과 메모리 사용을 비교해줘", "calc.py 가격 성능 비교 코드를 수정해줘",
                     "[첨부 문서: 자료.pdf] 논문 근거를 조사해서 요약해줘", "ALEX의 검색 성능과 비용을 비교해줘",
                     "내 GPU와 CPU 성능 가격을 비교해줘", "최신 GPU의 성능 가격을 간단히 비교해줘",
                     "최신 GPU 성능 가격 비교, 검색 없이 아는 대로 답해줘", "/research topic"):
            self.assertIsNone(automatic_request(text), text)

    def test_search_optout_and_only_deep_optout_are_distinct(self):
        for text in ("최신 버전은 검색하지 마", "인터넷 없이 답해", "검색은 하지 말고 설명해줘", "don't browse the web"):
            self.assertTrue(web_disabled(text), text)
        text = "딥서치는 하지 말고 최신 GPU 가격만 검색해줘"
        self.assertFalse(web_disabled(text))
        self.assertIsNone(request(text))
        self.assertIsNone(automatic_request(text))

    def test_explicit_research_and_feature_questions(self):
        for text in ("딥서치로 Python 3.13 변경점 비교해줘", "Python 3.13 심층 조사해줘", "/research Python 3.13", "/deepsearch Python 3.13"):
            self.assertIn("Python 3.13", request(text))
        for text in ("딥서치 기능을 보완해줘", "딥서치 가능한가?", "딥서치를 할 수 있어?", "/search 딥서치", "Python 설명해줘"):
            self.assertIsNone(request(text), text)
        self.assertEqual(request("딥서치 해줘"), "")
        self.assertEqual(request("이거 딥서치 해줘"), "")

    def test_canonical_keeps_meaningful_path_and_query(self):
        self.assertEqual(canonical("HTTPS://Example.COM:443/Doc?x=1&utm_source=ad#part"), "https://example.com/Doc?x=1")
        for url in ("file:///C:/secret", "https://user:secret@example.com/", "http://example.com:99999/", "http://[bad/"):
            self.assertEqual(canonical(url), "")


class ResearchTests(unittest.TestCase):
    def test_multiple_queries_and_reserved_followup_read_new_evidence(self):
        reader = Reader({"q1": rows("https://a.example/1", "https://a.example/2"),
                         "q2": rows("https://b.example/1", "https://b.example/2"),
                         "q3": rows("https://c.example/1", "https://c.example/2"),
                         "q4": rows("https://official.example/missing")})
        model = Model({"queries": ["q1", "q2", "q3"]}, {"queries": ["q4"], "gaps": ["버전은 추가 확인"]})
        result = researcher(reader, model).run("topic")
        self.assertEqual(reader.searched, ["q1", "q2", "q3", "q4"])
        self.assertEqual(reader.fetched[:3], ["https://a.example/1", "https://b.example/1", "https://c.example/1"])
        self.assertIn("https://official.example/missing", reader.fetched)
        report = model.calls[-1][0][-1].content
        self.assertIn("official.example", report)
        self.assertTrue(result.generated)
        self.assertIn("버전은 추가 확인", result.render())

    def test_invalid_plan_falls_back_to_original_and_no_sources_no_report(self):
        reader = Reader()
        model = Model("not json")
        result = researcher(reader, model).run("original topic")
        self.assertEqual(reader.searched, ["original topic"])
        self.assertEqual([phase for _, phase in model.calls], ["검색 계획"])
        self.assertFalse(result.generated)
        self.assertIn("결론을 내리지 않았습니다", result.render())

    def test_blocked_search_and_unread_bodies_are_not_claimed_read(self):
        reader = Reader({"q1": WebError("검색 서비스의 임시 제한"), "q2": rows("https://a.example/")},
                        {"https://a.example/": WebError("HTTP 403")})
        result = researcher(reader, Model({"queries": ["q1", "q2"]})).run("topic")
        rendered = result.render()
        self.assertIn("임시 제한", rendered)
        self.assertIn("검색 요약만", rendered)
        self.assertIn("HTTP 403", rendered)
        self.assertIn("서로 다른 본문 0개", rendered)
        self.assertNotIn("인터넷이 차단", rendered)

    def test_url_alias_redirect_and_identical_body_do_not_inflate_sources(self):
        same = "동일한 본문입니다. " * 40
        reader = Reader({"topic": rows("https://a.example/start?utm_source=x", "https://a.example/final"),
                         "extra": rows("https://b.example/copy")},
                        {"https://a.example/start": Page("https://a.example/final", "원본", same),
                         "https://b.example/copy": Page("https://b.example/copy", "복제", same)})
        result = researcher(reader, Model(review={"queries": ["extra"]})).run("topic")
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(sum(bool(s.text) and not s.duplicate for s in result.sources), 1)
        self.assertIn("독립 근거로 세지 않음", result.render())

    def test_original_private_query_stops_before_model_and_network(self):
        reader, model = Reader(), Model()
        with self.assertRaises(WebError):
            researcher(reader, model).run("PRIVATE project")
        self.assertEqual(model.calls, [])
        self.assertEqual(reader.searched, [])

    def test_generated_private_query_and_duplicate_queries_are_not_sent(self):
        reader = Reader({"public": rows("https://a.example/")})
        result = researcher(reader, Model({"queries": ["PRIVATE data", "public", "PUBLIC"]})).run("topic")
        self.assertEqual(reader.searched, ["public"])
        self.assertIn("전송하지 않았습니다", result.render())

    def test_cancel_during_fetch_stops_before_more_network_or_model_calls(self):
        cancel = threading.Event()
        reader, model = Reader({"topic": rows("https://a.example/", "https://b.example/")}), Model()
        original = reader.fetch

        def fetch(url):
            cancel.set()
            return original(url)

        reader.fetch = fetch
        with self.assertRaises(GenerationCancelled) as raised:
            researcher(reader, model, cancel_event=cancel).run("topic")
        self.assertEqual(len(reader.fetched), 1)
        self.assertEqual(len(model.calls), 1)
        self.assertIn("출처", raised.exception.partial)

    def test_deadline_during_model_stops_followups_and_returns_partial(self):
        now = [time.monotonic()]
        reader = Reader({"topic": rows("https://a.example/")})
        model = Model()

        def generate(messages, phase):
            value = model(messages, phase)
            if phase == "추가 확인":
                now[0] += 20
            return value

        result = researcher(reader, generate, limits=Limits(seconds=10), clock=lambda: now[0]).run("topic")
        self.assertTrue(result.limited)
        self.assertFalse(result.generated)
        self.assertEqual([phase for _, phase in model.calls], ["검색 계획", "추가 확인"])
        self.assertIn("본문 확보", result.render())
        self.assertIn("시간 한도", result.render())

    def test_native_model_cancel_keeps_collected_sources(self):
        reader = Reader({"topic": rows("https://a.example/")})
        model = Model(report=GenerationCancelled("UNFINISHED_MODEL_OUTPUT"))
        with self.assertRaises(GenerationCancelled) as raised:
            researcher(reader, model).run("topic")
        self.assertIn("https://a.example/", raised.exception.partial)
        self.assertIn("실제로 확보한 자료", raised.exception.partial)
        self.assertNotIn("UNFINISHED_MODEL_OUTPUT", raised.exception.partial)

    def test_long_topic_with_small_context_is_rejected_before_generation(self):
        reader, model = Reader(), Model()
        with self.assertRaisesRegex(WebError, "문맥 예산"):
            researcher(reader, model, context_chars=1200).run("긴 조사 주제 " * 100)
        self.assertEqual(model.calls, [])
        self.assertEqual(reader.searched, [])

    def test_model_failure_still_keeps_actual_sources(self):
        reader = Reader({"topic": rows("https://a.example/")})
        result = researcher(reader, Model(ProviderError("local down"), ProviderError("local down"), ProviderError("local down"))).run("topic")
        self.assertFalse(result.generated)
        self.assertIn("실제로 확보한 자료", result.render())
        self.assertIn("https://a.example/", result.render())

    def test_limits_bound_searches_pages_and_failed_fetch_attempts(self):
        reader = Reader({q: rows(*(f"https://{q}.example/{i}" for i in range(6))) for q in ("q1", "q2", "q3", "q4")})
        model = Model({"queries": ["q1", "q2", "q3"]}, {"queries": ["q4"]})
        result = researcher(reader, model, limits=Limits(searches=2, pages=2, sources=4, fetch_attempts=2)).run("topic")
        self.assertLessEqual(len(reader.searched), 2)
        self.assertLessEqual(len(reader.fetched), 2)
        self.assertLessEqual(len(result.sources), 4)

    def test_unregistered_citations_and_addresses_are_labeled(self):
        reader = Reader({"topic": rows("https://a.example/doc")})
        result = researcher(reader, Model(report="근거 [1], 가짜 [99], https://fabricated.example/doc")).run("topic")
        self.assertIn("[1]", result.answer)
        self.assertIn("[근거 확인 필요]", result.answer)
        self.assertNotIn("fabricated.example", result.render())
        self.assertIn("문장별 근거 일치까지 자동 보장하지는 않습니다", result.render())

    def test_context_budget_all_sources_and_relevant_middle_excerpt(self):
        worker = researcher(Reader(), context_chars=6000)
        worker.source_limit = 12
        for i in range(12):
            item = worker._add("자료 " + str(i), f"https://s{i}.example/")
            item.text = "내용 " * 500
        evidence = worker._evidence("topic", 1500)
        self.assertLessEqual(len(evidence), 1500)
        for i in range(1, 13):
            self.assertIn(f"[{i}]", evidence)
        text = "a" * 1800 + "UNIQUE_MATCH " * 20 + "z" * 4000
        self.assertIn("UNIQUE_MATCH", _excerpt(text, ["unique_match"], 900))

    def test_excerpt_covers_rare_second_question_and_keeps_bound(self):
        text = ('common framework detail ' * 350) + ('skipped outcome meaning ' * 25) + ('other material ' * 300)
        excerpt = _excerpt(text, ['common', 'framework', 'skipped', 'outcome'], 2400)
        self.assertIn('skipped outcome', excerpt)
        self.assertIn('common framework', excerpt)
        self.assertLessEqual(len(excerpt), 2400)

    def test_identifiers_keep_versions_and_separate_korean_particles(self):
        worker = researcher(Reader())
        worker.source_limit = 12
        source = worker._add('official source', 'https://docs.example/unittest')
        source.text = ('ordinary python module ' * 700 + 'version 3.11 requires __init__.py in subdirectories ' * 10
                       + 'other material ' * 900 + 'skipped tests do not run ' * 10)
        evidence = worker._evidence('Python 3.11 unittest discover가 __init__.py 없는 폴더와 skipped를 비교해줘', 2400)
        self.assertIn('3.11 requires __init__.py', evidence)
        self.assertIn('skipped tests do not run', evidence)

    def test_small_search_budget_reserves_a_followup_for_missing_evidence(self):
        reader = Reader({'q1': rows('https://a.example/'), 'q2': [], 'followup': rows('https://b.example/')})
        model = Model({'queries': ['q1', 'q2']}, {'queries': ['followup'], 'gaps': []})
        result = researcher(reader, model, limits=Limits(searches=2)).run('topic')
        self.assertEqual(reader.searched, ['q1', 'followup'])
        self.assertIn('추가 확인', [phase for _, phase in model.calls])


class CoreResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.local = MockLLMProvider("local", "LOCAL")
        self.local.generate = mock.Mock(side_effect=self.model)
        self.cloud = MockLLMProvider("cloud", "CLOUD")
        self.cloud.generate = mock.Mock(side_effect=AssertionError("cloud must never run"))
        self.reader = Reader({"topic": rows("https://a.example/")})
        self.core = JarvisCore(storage=Storage(self.root / "t.db"),
                               hardware=HardwareInfo("OS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
                               local_general=self.local, local_coder=self.local, cloud_coder=self.cloud,
                               cloud_reasoning=self.cloud, file_search_tool=SafeFileSearchTool(self.root),
                               context_char_budget=6000, local_only=False, web_reader=self.reader)
        self.sid = self.core.get_or_create_session(False)

    def model(self, messages, **kwargs):
        if "queries" in messages[0].content:
            return '{"queries":["topic"]}'
        return "근거가 있습니다. [1]"

    def test_command_and_natural_route_store_only_single_exchange_each(self):
        self.local.generate.side_effect = self.model
        self.core.storage.add_message(self.sid, "user", "PRIVATE_HISTORY_SENTINEL")
        for text in ("/research topic", "topic 딥서치 해줘"):
            before = len(self.core.storage.get_messages(self.sid, 50))
            if text.startswith("/"):
                out, keep, _ = handle_command(self.core, self.sid, text)
                self.assertTrue(keep)
            else:
                out, _ = self.core.handle(self.sid, text)
            self.assertIn("조사 범위", out)
            self.assertEqual(len(self.core.storage.get_messages(self.sid, 50)), before + 2)
        for call in self.local.generate.call_args_list:
            self.assertNotIn("PRIVATE_HISTORY_SENTINEL", " ".join(m.content for m in call.args[0]))
        self.cloud.generate.assert_not_called()

    def test_complex_question_automatically_uses_research_and_progress(self):
        seen = []
        self.core.response_chunk_callback = seen.append
        out, decision = self.core.handle(self.sid, "RTX 5080과 5090의 성능, 가격, VRAM을 비교해줘")
        self.assertEqual(decision.reason, AUTO_REASON)
        self.assertIn("질문에 맞춰 자동 선택", out)
        self.assertIn("여러 출처", " ".join(seen))
        self.assertEqual(len(self.core.storage.get_messages(self.sid, 50)), 2)
        self.cloud.generate.assert_not_called()

    def test_simple_fresh_question_keeps_quick_search(self):
        out, decision = self.core.handle(self.sid, "Python 최신 버전은?")
        self.assertEqual(decision.destination, Destination.TOOL_WEB_SEARCH)
        self.assertNotIn("질문에 맞춰 자동 선택", out)

    def test_model_can_choose_deep_for_question_outside_rules(self):
        question = "두 기술 중 어느 쪽이 장기 운영에 유리할까요?"
        self.assertIsNone(automatic_request(question))

        def choose(messages, **kwargs):
            if "tool_runner" in kwargs:
                schema = kwargs["tools"][0]["function"]["parameters"]["properties"]
                self.assertIn("deep", schema["depth"]["enum"])
                return kwargs["tool_runner"]("web_search", {"query": "public comparison", "depth": "deep"})
            return self.model(messages)

        self.local.generate.side_effect = choose
        out, _ = self.core.handle(self.sid, question)
        self.assertIn("질문에 맞춰 자동 선택", out)
        self.assertIn("조사 범위", out)
        self.assertEqual(len(self.core.storage.get_messages(self.sid, 50)), 2)
        self.cloud.generate.assert_not_called()

    def test_native_tool_loop_escalates_once_without_extra_summary(self):
        from test_web_tool import ScriptedOllama
        call = {"function": {"name": "web_search", "arguments": {"query": "public", "depth": "deep"}}}
        model = ScriptedOllama(([call], ""), ([], '{"queries":["topic"]}'),
                               ([], '{"queries":[]}'), ([], "실제 근거 보고서 [1]"))
        model.health_check = lambda: True
        self.core.providers[Destination.LOCAL_GENERAL] = model
        self.core.providers[Destination.LOCAL_CODER] = model
        out, _ = self.core.handle(self.sid, "두 기술 중 어느 쪽이 장기 운영에 유리할까요?")
        self.assertIn("실제 근거 보고서", out)
        self.assertEqual(len(model.seen), 4)
        self.assertEqual(model.script, [])
        self.cloud.generate.assert_not_called()

    def test_search_optout_prevents_direct_route_and_model_tools(self):
        out, decision = self.core.handle(self.sid, "최신 GPU 가격과 성능을 비교하되 검색 없이 아는 대로 답해줘")
        self.assertNotIn(decision.destination, (Destination.TOOL_WEB_SEARCH, Destination.TOOL_WEB_READ))
        self.assertEqual(self.reader.searched, [])
        self.assertNotIn("tools", self.local.generate.call_args.kwargs)
        self.assertNotIn("자동 선택", out)

    def test_model_cannot_escalate_private_or_local_request(self):
        self.core._current_user_text = r"C:\secret\plan.txt 를 보고 성능 가격을 비교해줘"
        result = self.core._run_tool("web_search", {"query": "public", "depth": "deep"})
        self.assertIn("전환하지 않는다", result)
        self.assertEqual(self.reader.searched, [])

    def test_local_unavailable_never_falls_back_to_cloud(self):
        self.local.generate.side_effect = ProviderError("local unavailable")
        out = self.core.research_local(self.sid, "topic")
        self.assertIn("종합 보고서는 만들지 못했습니다", out)
        self.cloud.generate.assert_not_called()

    def test_usage_disabled_and_original_private_paths(self):
        self.assertIn("조사할 주제", self.core.research_local(self.sid, ""))
        self.core.web_reader = None
        self.assertEqual(self.core.research_local(self.sid, "topic"), WEB_DISABLED_MESSAGE)
        self.core.web_reader = self.reader
        self.assertIn("개인 정보나 로컬 경로", self.core.research_local(self.sid, r"C:\secret\plan.py 조사"))
        self.assertEqual(self.reader.searched, [])
        self.local.generate.assert_not_called()

    def test_deep_request_with_existing_filename_does_not_start_coding(self):
        from test_web import CATALOG, make_state
        with mock.patch("jarvis.freemodels._download", return_value=CATALOG):
            state = make_state(self.root)
        (self.root / "calc.py").write_text("x = 1", encoding="utf-8")
        state.core._approved_file_root = self.root
        state.core.current_project_root = lambda: self.root
        self.assertIsNone(state.code_chat_plan(state.owner_viewer(), state.session_id, "calc.py 수정 방식을 딥서치로 비교해줘"))
        self.assertEqual(state.code.sessions, {})


class ReaderControlTests(unittest.TestCase):
    def test_cancel_before_fetch_does_not_open_socket(self):
        cancel = threading.Event()
        cancel.set()
        reader = reader_returning(FakeResponse(b"<p>body</p>"))
        with mock.patch.object(reader, "_open", side_effect=AssertionError("must not connect")):
            with self.assertRaises(WebError):
                reader.fetch("https://example.com/", control=RequestControl(cancel))

    def test_gzip_expansion_over_cap_is_explicitly_truncated(self):
        body = b"<html><body><p>" + b"expanded text " * 300 + b"</p></body></html>"
        page = reader_returning(FakeResponse(gzip.compress(body), content_encoding="gzip"), max_bytes=500).fetch("https://example.com/")
        self.assertTrue(page.truncated)
        self.assertLessEqual(len(page.text), 500)


if __name__ == "__main__":
    unittest.main()
