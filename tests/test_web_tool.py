"""모델이 스스로 부르는 검색 도구 (ADR-056, 2026-09-25).

사용자가 "모르겠으면 너가 직접 확인해서 답해줘야지" 라고 했는데 ALEX 는 "'검색해줘' 라고 치시면 된다,
나는 호출 못 한다" 고만 했다. 이제 대화·코딩 경로에서 모델에게 web_search 도구를 준다.

지키는 것:
- 밖으로 나가는 것은 모델이 지은 검색어뿐이고, 개인 정보처럼 보이면 나가지 않는다.
- 한 질문에 두 번까지만 찾는다. 도구 차례는 MAX_TOOL_ROUNDS 번이고, 마지막은 도구 없이 답을 받는다.
- 무엇을 찾았고 어디서 왔는지는 모델이 아니라 앱이 답 끝에 붙인다.
- 도구를 모르는 모델은 도구 없이 답한다.
"""
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.core import WEB_MATERIAL_HEADER, WEB_SEARCH_TOOL, JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import (  # noqa: E402
    MAX_TOOL_ROUNDS, Message, MockLLMProvider, OllamaProvider, ProviderError, ToolsUnsupported)
from jarvis.router import Destination  # noqa: E402
from jarvis.storage import Storage  # noqa: E402
from jarvis.webread import Page, SearchResult, WebError  # noqa: E402

CALL = {"function": {"name": "web_search", "arguments": {"query": "Qwen3.8 training data cutoff"}}}


class ScriptedOllama(OllamaProvider):
    """generate_stream 만 바꾼다. 한 차례마다 (tool_calls, content) 또는 예외를 낸다."""

    def __init__(self, *script):
        super().__init__("qwen3.8:27b", num_ctx=16384, num_predict=2048)
        self.script = list(script)
        self.seen: list[tuple[list[Message], object]] = []

    def generate_stream(self, messages, **kwargs):
        self.seen.append((list(messages), kwargs.get("tools")))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        calls, content = step
        self.last_metrics = {"done_reason": "stop"}
        self.last_tool_calls = list(calls)
        return iter([content] if content else [])


class ProviderLoopTests(unittest.TestCase):
    def setUp(self):
        self.asked = []

    def runner(self, name, arguments):
        self.asked.append((name, arguments))
        return "자료"

    def ask(self, provider):
        return provider.generate([Message("user", "모르면 직접 확인해서 답해줘")], on_chunk=lambda _c: None,
                                 tools=[WEB_SEARCH_TOOL], tool_runner=self.runner)

    def test_the_tool_result_goes_back_and_the_model_answers(self):
        provider = ScriptedOllama(([CALL], ""), ([], "답입니다"))
        self.assertEqual(self.ask(provider), "답입니다")
        self.assertEqual(self.asked, [("web_search", {"query": "Qwen3.8 training data cutoff"})])
        second, _ = provider.seen[1]
        self.assertEqual((second[-2].role, second[-2].tool_calls), ("assistant", [CALL]))
        self.assertEqual((second[-1].role, second[-1].tool_name, second[-1].content), ("tool", "web_search", "자료"))

    def test_arguments_given_as_a_json_string_are_read(self):
        call = {"function": {"name": "web_search", "arguments": '{"query": "q"}'}}
        self.ask(ScriptedOllama(([call], ""), ([], "답")))
        self.assertEqual(self.asked, [("web_search", {"query": "q"})])

    def test_a_model_without_tools_answers_without_them(self):
        provider = ScriptedOllama(ToolsUnsupported("no tools"), ([], "도구 없이 답"), ([], "다음 답"))
        self.assertEqual(self.ask(provider), "도구 없이 답")
        self.assertIsNone(provider.seen[1][1])
        self.ask(provider)  # 두 번째 질문부터는 처음부터 도구를 주지 않는다
        self.assertIsNone(provider.seen[2][1])

    def test_tools_stay_offered_on_every_turn(self):
        """gpt-oss 는 도구를 뺀 차례에도 도구를 부르며 본문을 비웠다. 도구를 둔 채로는 답했다(2026-09-25)."""
        provider = ScriptedOllama(*([([CALL], "")] * MAX_TOOL_ROUNDS), ([], "답"))
        self.assertEqual(self.ask(provider), "답")
        self.assertEqual(len(self.asked), MAX_TOOL_ROUNDS)
        self.assertTrue(all(tools == [WEB_SEARCH_TOOL] for _, tools in provider.seen))

    def test_a_model_that_only_keeps_calling_is_cut_off(self):
        provider = ScriptedOllama(*([([CALL], "")] * (MAX_TOOL_ROUNDS + 1)))
        with self.assertRaises(ProviderError) as ctx:
            self.ask(provider)
        self.assertIn("검색만 되풀이", str(ctx.exception))
        self.assertEqual(len(self.asked), MAX_TOOL_ROUNDS)

    def test_text_that_comes_with_a_call_at_the_limit_is_kept(self):
        provider = ScriptedOllama(*([([CALL], "")] * MAX_TOOL_ROUNDS), ([CALL], "답을 썼습니다"))
        self.assertEqual(self.ask(provider), "답을 썼습니다")

    def test_no_tools_offered_means_the_old_path(self):
        provider = ScriptedOllama(([], "그냥 답"))
        self.assertEqual(provider.generate([Message("user", "hi")], on_chunk=lambda _c: None), "그냥 답")
        self.assertIsNone(provider.seen[0][1])

    def test_payload_carries_tools_and_tool_messages(self):
        payload = OllamaProvider("m")._chat_payload(
            [Message("assistant", "", tool_calls=[CALL]), Message("tool", "자료", tool_name="web_search")],
            stream=True, tools=[WEB_SEARCH_TOOL])
        self.assertEqual(payload["tools"], [WEB_SEARCH_TOOL])
        self.assertEqual(payload["messages"][0]["tool_calls"], [CALL])
        self.assertEqual(payload["messages"][1]["tool_name"], "web_search")
        self.assertNotIn("tools", OllamaProvider("m")._chat_payload([Message("user", "hi")], stream=True))

    def test_ollama_400_does_not_support_tools_is_recognised(self):
        body = io.BytesIO(b'{"error":"registry.ollama.ai/library/x does not support tools"}')
        error = urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 400, "Bad Request", {}, body)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ToolsUnsupported):
                list(OllamaProvider("x").generate_stream([Message("user", "hi")], tools=[WEB_SEARCH_TOOL]))


class FakeWeb:
    def __init__(self, fail=False):
        self.fail = fail
        self.queries: list[str] = []

    def search(self, query):
        self.queries.append(query)
        if self.fail:
            raise WebError("검색 서버에 닿지 못했다")
        return [SearchResult("Qwen3.8 모델 카드", "https://example.com/qwen3.8", "지식 기준 시점 …"),
                SearchResult("릴리스 노트", "https://example.com/notes", "2026년 8월 공개")]

    def fetch(self, url):
        if url.endswith("qwen3.8"):
            return Page(url=url, title="Qwen3.8 모델 카드", text="본문: 학습 데이터 기준 시점은 …")
        raise WebError("읽지 못함")


class SearchingProvider(MockLLMProvider):
    """모델 대신 검색어를 정해 두고 앱의 tool_runner 를 부른다."""

    def __init__(self, *queries):
        super().__init__("general", "G")
        self.queries = queries
        self.kwargs: dict = {}
        self.material: list[str] = []

    def generate(self, messages, **kwargs):
        self.kwargs = kwargs
        if "tool_runner" in kwargs:
            self.material = [kwargs["tool_runner"]("web_search", {"query": q}) for q in self.queries]
        return "답입니다 [1]"


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.web = FakeWeb()

    def core(self, provider, **overrides):
        options = dict(
            storage=Storage(Path(self._tmp.name) / "t.db"),
            hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                  profile=PerformanceProfile.DESKTOP),
            local_general=provider, local_coder=provider,
            cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
            local_only=True, web_reader=self.web,
        )
        options.update(overrides)
        return JarvisCore(**options)

    def ask(self, core, text="모르겠으면 너가 직접 확인해서 답해줘"):
        return core.handle(core.get_or_create_session(resume=False), text)[0]

    def test_the_model_can_search_and_the_app_adds_query_and_sources(self):
        provider = SearchingProvider("qwen3.8 학습 시점")
        answer = self.ask(self.core(provider))
        self.assertEqual(self.web.queries, ["qwen3.8 학습 시점"])
        self.assertTrue(provider.material[0].startswith(WEB_MATERIAL_HEADER))
        self.assertIn("본문: 학습 데이터 기준 시점", provider.material[0])
        self.assertIn("출처 — ALEX 가 스스로 검색: 'qwen3.8 학습 시점'", answer)
        self.assertIn("[1] Qwen3.8 모델 카드 — https://example.com/qwen3.8", answer)

    def test_the_screen_is_told_while_searching(self):
        seen = []
        self.ask(self.core(SearchingProvider("qwen3.8 학습 시점"), response_chunk_callback=seen.append))
        self.assertIn("[웹에서 찾는 중: 'qwen3.8 학습 시점']\n\n", seen)

    def test_a_query_that_looks_private_never_leaves(self):
        provider = SearchingProvider(r"C:\Users\alexuser\secret.txt 내용", "user@example.com 비밀번호", "계좌 123456789012")
        answer = self.ask(self.core(provider))
        self.assertEqual(self.web.queries, [])
        self.assertTrue(all("검색하지 않았다" in m for m in provider.material))
        self.assertNotIn("출처 — ALEX", answer)

    def test_at_most_two_searches_per_question(self):
        provider = SearchingProvider("첫째 검색", "둘째 검색", "셋째 검색")
        self.ask(self.core(provider))
        self.assertEqual(self.web.queries, ["첫째 검색", "둘째 검색"])
        self.assertIn("더 검색하지 않는다", provider.material[2])

    def test_a_failed_search_is_said_in_the_answer(self):
        self.web = FakeWeb(fail=True)
        answer = self.ask(self.core(SearchingProvider("q 검색")))
        self.assertIn("스스로 검색했지만 자료를 가져오지 못했습니다: 'q 검색'", answer)

    def test_switches_turn_the_tool_off(self):
        provider = SearchingProvider()
        self.ask(self.core(provider, web_tool_enabled=False))
        self.assertNotIn("tools", provider.kwargs)
        self.ask(self.core(provider, web_reader=None))
        self.assertNotIn("tools", provider.kwargs)

    def test_the_model_is_told_it_can_search_only_where_it_can(self):
        core = self.core(SearchingProvider())
        self.assertIn("web_search 도구로 직접 찾아서", core._environment_line(Destination.LOCAL_GENERAL))
        self.assertIn("검색 명령을 치라고 하지 않는다", core._environment_line(Destination.LOCAL_CODER))
        self.assertIn("'○○ 검색해줘' 라고 말하면", core._environment_line(None))
        off = self.core(SearchingProvider(), web_tool_enabled=False)
        self.assertNotIn("web_search", off._environment_line(Destination.LOCAL_GENERAL))


if __name__ == "__main__":
    unittest.main()
