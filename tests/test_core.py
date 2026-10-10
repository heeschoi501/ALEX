"""Core orchestration tests: routing, fallback, tool wiring, context 구성."""
import tempfile
import unittest
from pathlib import Path

from jarvis.core import JarvisCore, redact_sensitive_text
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, FailingProvider, MockLLMProvider, ProviderError
from jarvis.router import Destination
from jarvis.storage import Storage
from jarvis.tools import SafeFileSearchTool


def make_core(temp_path, **overrides):
    storage = Storage(temp_path / "test.db")
    hardware = HardwareInfo("TestOS", "TestCPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP)
    kwargs = dict(
        local_general=MockLLMProvider("general", "GENERAL"),
        local_coder=MockLLMProvider("coder", "CODER"),
        cloud_coder=CloudStubProvider("cloud-coder", "CLOUD CODER"),
        cloud_reasoning=CloudStubProvider("cloud-reasoning", "CLOUD REASONING"),
    )
    kwargs.update(overrides)
    return JarvisCore(storage=storage, hardware=hardware, **kwargs)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        self.core = make_core(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_handle_simple_code(self):
        sid = self.core.get_or_create_session(False)
        response, decision = self.core.handle(sid, "Python 함수 하나 만들어줘")
        self.assertEqual(decision.destination, Destination.LOCAL_CODER)
        self.assertIn("CODER", response)

    def test_system_tool(self):
        sid = self.core.get_or_create_session(False)
        response, decision = self.core.handle(sid, "현재 시스템 RAM과 GPU 알려줘")
        self.assertEqual(decision.destination, Destination.TOOL_SYSTEM_INFO)
        self.assertIn("ram_gb", response)

    def test_resume_without_history(self):
        sid = self.core.get_or_create_session(False)
        response, decision = self.core.handle(sid, "지난 세션 이어서 해")
        self.assertEqual(decision.destination, Destination.SESSION_RESUME)
        self.assertIn("이어갈 이전 대화가 아직 없습니다", response)

    def test_resume_with_history(self):
        sid = self.core.get_or_create_session(False)
        self.core.handle(sid, "안녕")
        response, _ = self.core.handle(sid, "지난 세션 이어서 해")
        self.assertIn("안녕", response)

    def test_messages_are_persisted(self):
        sid = self.core.get_or_create_session(False)
        self.core.handle(sid, "안녕")
        messages = self.core.storage.get_messages(sid)
        self.assertEqual([m.role for m in messages], ["user", "assistant"])


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_falls_back_when_primary_raises(self):
        core = make_core(self.path, local_coder=FailingProvider("coder"))
        sid = core.get_or_create_session(False)
        response, decision = core.handle(sid, "Python 함수 알려줘")
        self.assertEqual(decision.destination, Destination.LOCAL_CODER)
        self.assertIn("fallback", response)
        self.assertIn("GENERAL", response)

    def test_falls_back_when_health_check_fails(self):
        core = make_core(self.path, local_general=MockLLMProvider("g", "GENERAL", healthy=False))
        sid = core.get_or_create_session(False)
        response, _ = core.handle(sid, "안녕")
        self.assertIn("fallback", response)
        self.assertIn("CODER", response)

    def test_all_providers_down(self):
        core = make_core(
            self.path,
            local_general=FailingProvider("g"),
            local_coder=FailingProvider("c"),
            cloud_coder=FailingProvider("cc"),
            cloud_reasoning=FailingProvider("cr"),
        )
        sid = core.get_or_create_session(False)
        response, _ = core.handle(sid, "안녕")
        self.assertIn("응답할 수 있는 모델이 없습니다", response)
        # 실패해도 대화는 저장되어야 한다.
        self.assertEqual(len(core.storage.get_messages(sid)), 2)


class FileSearchRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        (self.path / "project").mkdir()
        (self.path / "project" / "router.py").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_file_search_route_uses_tool(self):
        core = make_core(self.path, file_search_tool=SafeFileSearchTool(self.path / "project"))
        sid = core.get_or_create_session(False)
        response, decision = core.handle(sid, "router 파일 찾아줘")
        self.assertEqual(decision.destination, Destination.TOOL_FILE_SEARCH)
        self.assertIn("router.py", response)

    def test_file_search_without_tool_is_graceful(self):
        core = make_core(self.path)
        sid = core.get_or_create_session(False)
        response, _ = core.handle(sid, "router 파일 찾아줘")
        self.assertIn("설정되지 않았습니다", response)

    def test_query_extraction(self):
        self.assertEqual(JarvisCore._extract_file_query("router 파일 찾아줘"), "router")
        self.assertEqual(JarvisCore._extract_file_query("config 파일 검색"), "config")


class ContextBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core = make_core(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_memory_is_injected_as_system_message(self):
        self.core.storage.remember("mode", "study")
        sid = self.core.get_or_create_session(False)
        self.core.storage.add_message(sid, "user", "안녕")
        messages = self.core._build_messages(sid)
        self.assertEqual(messages[0].role, "system")
        self.assertIn("Project ALEX", messages[0].content)
        self.assertIn("mode=study", messages[1].content)

    def test_history_limit_is_respected(self):
        core = make_core(Path(self.temp_dir.name), history_limit=3, memory_context_limit=0)
        sid = core.get_or_create_session(False)
        for i in range(10):
            core.storage.add_message(sid, "user", f"m{i}")
        messages = core._build_messages(sid)
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[-1].content, "m9")


if __name__ == "__main__":
    unittest.main()


class TrackingProvider(MockLLMProvider):
    def __init__(self, name="tracking", prefix="TRACK", healthy=True):
        super().__init__(name, prefix, healthy=healthy)
        self.generate_calls = 0
        self.last_messages = []

    def generate(self, messages, **kwargs):
        self.generate_calls += 1
        self.last_messages = list(messages)
        return super().generate(messages, **kwargs)


class CloudApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cloud_generate_is_never_called_without_approval_callback(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        core = make_core(self.path, cloud_coder=cloud, require_cloud_approval=True)
        sid = core.get_or_create_session(False)
        response, decision = core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
        self.assertEqual(decision.destination, Destination.CLOUD_CODER)
        self.assertEqual(cloud.generate_calls, 0)
        self.assertIn("cloud", response.lower())
        self.assertIn("local", response.lower())

    def test_denied_cloud_falls_back_local_without_cloud_call(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        approvals = []

        def deny(request):
            approvals.append(request)
            return False

        core = make_core(
            self.path,
            cloud_coder=cloud,
            require_cloud_approval=True,
            approval_callback=deny,
        )
        sid = core.get_or_create_session(False)
        response, _ = core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
        self.assertEqual(cloud.generate_calls, 0)
        self.assertEqual(len(approvals), 1)
        self.assertIn("local", response.lower())

    def test_approved_cloud_calls_provider(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        core = make_core(
            self.path,
            cloud_coder=cloud,
            require_cloud_approval=True,
            approval_callback=lambda request: True,
        )
        sid = core.get_or_create_session(False)
        response, _ = core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
        self.assertEqual(cloud.generate_calls, 1)
        self.assertIn("CLOUD", response)

    def test_sensitive_pattern_is_reported_to_callback(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        seen = []

        def inspect_and_deny(request):
            seen.append(request)
            return False

        core = make_core(
            self.path,
            cloud_reasoning=cloud,
            require_cloud_approval=True,
            approval_callback=inspect_and_deny,
        )
        sid = core.get_or_create_session(False)
        core.handle(sid, "심층 분석 해줘. api_key=VERY_SECRET_VALUE")
        self.assertTrue(seen)
        self.assertTrue(seen[0].sensitive_warnings)
        self.assertEqual(cloud.generate_calls, 0)

    def test_cloud_approval_can_be_disabled_explicitly(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        core = make_core(
            self.path,
            cloud_coder=cloud,
            require_cloud_approval=False,
        )
        sid = core.get_or_create_session(False)
        core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
        self.assertEqual(cloud.generate_calls, 1)


class ConversationSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_old_messages_are_summarized_and_injected(self):
        summarizer = TrackingProvider("summary", "SUMMARY")
        core = make_core(
            self.path,
            local_general=summarizer,
            history_limit=4,
            memory_context_limit=0,
            summary_enabled=True,
            summary_trigger_messages=6,
        )
        sid = core.get_or_create_session(False)
        for i in range(8):
            core.storage.add_message(sid, "user", f"old-{i}")
        messages = core._build_messages(sid)
        saved, through_id = core.storage.get_session_summary(sid)
        self.assertTrue(saved)
        self.assertGreater(through_id, 0)
        self.assertGreaterEqual(summarizer.generate_calls, 1)
        self.assertTrue(any("압축 요약" in m.content and "확정 사실로 취급하지 않는다" in m.content
                            for m in messages if m.role == "system"))
        # base system + summary + recent 4
        self.assertLessEqual(len(messages), 6)

    def test_summary_uses_its_own_provider_and_treats_old_answers_as_unverified(self):
        general = TrackingProvider("general", "GENERAL")
        summarizer = TrackingProvider("summary", "SUMMARY")
        core = make_core(
            self.path,
            local_general=general,
            local_summarizer=summarizer,
            history_limit=4,
            memory_context_limit=0,
            summary_enabled=True,
            summary_trigger_messages=6,
        )
        sid = core.get_or_create_session(False)
        for i in range(8):
            core.storage.add_message(sid, "user", f"old-{i}")
        core._build_messages(sid)
        self.assertEqual(general.generate_calls, 0)
        self.assertEqual(summarizer.generate_calls, 1)
        self.assertIn("검증되지 않은 주장", summarizer.last_messages[0].content)

    def test_summary_failure_uses_extractive_fallback(self):
        core = make_core(
            self.path,
            local_general=FailingProvider("summary-down"),
            history_limit=4,
            memory_context_limit=0,
            summary_enabled=True,
            summary_trigger_messages=6,
        )
        sid = core.get_or_create_session(False)
        for i in range(8):
            core.storage.add_message(sid, "user", f"important-{i}")
        messages = core._build_messages(sid)
        summary, _ = core.storage.get_session_summary(sid)
        self.assertIn("important-0", summary)
        self.assertTrue(any("압축 요약" in m.content for m in messages if m.role == "system"))

    def test_summary_disabled_keeps_only_recent_window(self):
        core = make_core(
            self.path,
            history_limit=3,
            memory_context_limit=0,
            summary_enabled=False,
            summary_trigger_messages=6,
        )
        sid = core.get_or_create_session(False)
        for i in range(8):
            core.storage.add_message(sid, "user", f"m{i}")
        messages = core._build_messages(sid)
        self.assertEqual([m.content for m in messages[1:]], ["m5", "m6", "m7"])
        self.assertEqual(core.storage.get_session_summary(sid)[0], "")


class RouteFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        self.log = self.path / "logs" / "misroute.jsonl"
        self.core = make_core(self.path, misroute_log_path=self.log)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wrong_marks_last_route_in_db_and_jsonl(self):
        sid = self.core.get_or_create_session(False)
        self.core.handle(sid, "파이썬 함수 알려줘")
        result = self.core.mark_last_route_wrong("local_general")
        self.assertIn("오분류", result)
        event = self.core.storage.get_route_event(self.core.last_route_event_id)
        self.assertEqual(event.correction, "local_general")
        self.assertTrue(self.log.exists())
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("local_coder", text)
        self.assertIn("local_general", text)

    def test_wrong_without_request_is_safe(self):
        self.assertIn("아직", self.core.mark_last_route_wrong())

    def test_diagnostics_contains_integrity_and_providers(self):
        output = self.core.diagnostics()
        self.assertIn("DB integrity: OK", output)
        self.assertIn("local_general", output)


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_long_history_is_trimmed_to_char_budget(self):
        core = make_core(
            self.path,
            history_limit=12,
            memory_context_limit=0,
            context_char_budget=1000,
            summary_enabled=False,
        )
        sid = core.get_or_create_session(False)
        for i in range(8):
            core.storage.add_message(sid, "user", f"m{i}-" + ("x" * 400))
        messages = core._build_messages(sid)
        self.assertLessEqual(sum(len(m.content) for m in messages), 1000)
        self.assertIn("m7-", messages[-1].content)

    def test_latest_long_message_keeps_head_and_tail(self):
        core = make_core(
            self.path,
            history_limit=4,
            memory_context_limit=0,
            context_char_budget=1000,
            summary_enabled=False,
        )
        sid = core.get_or_create_session(False)
        content = "START-" + ("x" * 2000) + "-END"
        core.storage.add_message(sid, "user", content)
        messages = core._build_messages(sid)
        self.assertIn("START-", messages[-1].content)
        self.assertIn("-END", messages[-1].content)
        self.assertIn("중간 생략", messages[-1].content)


class SummaryPromptBudgetTests(unittest.TestCase):
    def test_summary_prompt_is_bounded(self):
        from jarvis.storage import SummaryMessage

        with tempfile.TemporaryDirectory() as td:
            core = make_core(Path(td), summary_max_chars=3000)
            candidates = [SummaryMessage(i + 1, "user", "x" * 5000) for i in range(40)]
            prompt = core._summary_prompt("y" * 3000, candidates)
            self.assertLessEqual(len(prompt), 16_000)


class MemoryRelevanceTests(unittest.TestCase):
    def test_relevant_old_memory_beats_newer_irrelevant_memory(self):
        with tempfile.TemporaryDirectory() as td:
            core = make_core(Path(td), memory_context_limit=1)
            core.storage.remember("python_study", "파이썬 공부는 힌트 위주")
            core.storage.remember("lunch", "오늘 점심은 김밥")  # 더 최근
            selected = core._select_memories("파이썬 공부 문제 힌트 줘")
            self.assertIn("python_study", selected)
            self.assertNotIn("lunch", selected)

    def test_no_relevance_falls_back_to_recent_memory(self):
        with tempfile.TemporaryDirectory() as td:
            core = make_core(Path(td), memory_context_limit=1)
            core.storage.remember("old", "alpha")
            core.storage.remember("new", "beta")
            selected = core._select_memories("전혀 다른 질문")
            self.assertEqual(list(selected), ["new"])


class HealthRaisingProvider(MockLLMProvider):
    def health_check(self):
        raise RuntimeError("health adapter exploded")


class V012PrivacyAndResilienceTests(unittest.TestCase):
    def test_health_check_exception_is_treated_as_unhealthy(self):
        with tempfile.TemporaryDirectory() as td:
            core = make_core(Path(td), local_general=HealthRaisingProvider("boom", "BOOM"))
            sid = core.get_or_create_session(False)
            response, _ = core.handle(sid, "안녕")
            self.assertIn("fallback", response)
            self.assertIn("CODER", response)

    def test_cloud_excludes_memory_and_summary_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            cloud = TrackingProvider("cloud", "CLOUD")
            core = make_core(
                Path(td),
                cloud_coder=cloud,
                require_cloud_approval=True,
                approval_callback=lambda request: True,
            )
            sid = core.get_or_create_session(False)
            core.storage.remember("private_pref", "do-not-send-memory")
            core.storage.update_session_summary(sid, "do-not-send-summary", through_id=0)
            core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
            joined = "\n".join(m.content for m in cloud.last_messages)
            self.assertNotIn("do-not-send-memory", joined)
            self.assertNotIn("do-not-send-summary", joined)
            self.assertIn("코드베이스 전체 구조", joined)

    def test_cloud_can_opt_in_memory_and_summary(self):
        with tempfile.TemporaryDirectory() as td:
            cloud = TrackingProvider("cloud", "CLOUD")
            seen = []
            core = make_core(
                Path(td),
                cloud_coder=cloud,
                require_cloud_approval=True,
                approval_callback=lambda request: seen.append(request) or True,
                cloud_include_memory=True,
                cloud_include_summary=True,
            )
            sid = core.get_or_create_session(False)
            core.storage.remember("project", "include-me-memory")
            core.storage.update_session_summary(sid, "include-me-summary", through_id=0)
            core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
            joined = "\n".join(m.content for m in cloud.last_messages)
            self.assertIn("include-me-memory", joined)
            self.assertIn("include-me-summary", joined)
            self.assertTrue(seen[0].memory_included)
            self.assertTrue(seen[0].summary_included)
            self.assertGreater(seen[0].context_chars, 0)


class HealthyFailingTrackingProvider(TrackingProvider):
    def generate(self, messages, **kwargs):
        self.generate_calls += 1
        self.last_messages = list(messages)
        raise ProviderError(f"{self.name} generate failed")


class CloudApprovalScopeTests(unittest.TestCase):
    def test_second_cloud_candidate_requires_its_own_approval_after_first_fails(self):
        with tempfile.TemporaryDirectory() as td:
            reasoning = HealthyFailingTrackingProvider("reasoning", "REASONING")
            coder = TrackingProvider("coder-cloud", "CODER-CLOUD")
            seen = []

            def approve_first_only(request):
                seen.append((request.destination, request.provider_name))
                return len(seen) == 1

            core = make_core(
                Path(td),
                cloud_reasoning=reasoning,
                cloud_coder=coder,
                require_cloud_approval=True,
                approval_callback=approve_first_only,
            )
            sid = core.get_or_create_session(False)
            response, decision = core.handle(sid, "이 문제를 심층 분석하고 복잡한 추론을 해줘")
            self.assertEqual(decision.destination, Destination.CLOUD_REASONING)
            self.assertEqual(reasoning.generate_calls, 1)
            self.assertEqual(coder.generate_calls, 0)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0][0], Destination.CLOUD_REASONING)
            self.assertEqual(seen[1][0], Destination.CLOUD_CODER)
            self.assertIn("local", response.lower())

    def test_first_cloud_denial_blocks_remaining_cloud_candidates_without_reprompt(self):
        with tempfile.TemporaryDirectory() as td:
            reasoning = TrackingProvider("reasoning", "REASONING")
            coder = TrackingProvider("coder-cloud", "CODER-CLOUD")
            seen = []
            core = make_core(
                Path(td),
                cloud_reasoning=reasoning,
                cloud_coder=coder,
                require_cloud_approval=True,
                approval_callback=lambda request: seen.append(request) or False,
            )
            sid = core.get_or_create_session(False)
            core.handle(sid, "이 문제를 심층 분석하고 복잡한 추론을 해줘")
            self.assertEqual(reasoning.generate_calls, 0)
            self.assertEqual(coder.generate_calls, 0)
            self.assertEqual(len(seen), 1)


class SensitiveRedactionTests(unittest.TestCase):
    def test_basic_secret_redaction(self):
        text = "api_key=SUPERSECRET123 password: hunter2 sk-abcdefghijklmnop"
        redacted = redact_sensitive_text(text)
        self.assertNotIn("SUPERSECRET123", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("sk-abcdefghijklmnop", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_cloud_preview_redacts_secret_but_warning_remains(self):
        with tempfile.TemporaryDirectory() as td:
            cloud = TrackingProvider("cloud", "CLOUD")
            seen = []
            core = make_core(
                Path(td),
                cloud_reasoning=cloud,
                approval_callback=lambda request: seen.append(request) or False,
            )
            sid = core.get_or_create_session(False)
            core.handle(sid, "심층 분석해줘 api_key=SUPERSECRET123")
            self.assertTrue(seen)
            self.assertNotIn("SUPERSECRET123", seen[0].last_user_preview)
            self.assertTrue(seen[0].sensitive_warnings)

    def test_misroute_jsonl_redacts_secret_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "misroute.jsonl"
            core = make_core(root, misroute_log_path=log)
            sid = core.get_or_create_session(False)
            core.handle(sid, "파이썬 api_key=SUPERSECRET123 함수 알려줘")
            core.mark_last_route_wrong("local_general")
            saved = log.read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET123", saved)
            self.assertIn("REDACTED", saved)


class LocalFileQuestionTests(unittest.TestCase):
    def test_ask_file_uses_local_coder_and_never_cloud(self):
        from jarvis.tools import SafeFileReadTool

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sample.py").write_text("def add(a, b): return a + b", encoding="utf-8")
            local = TrackingProvider("local-code", "LOCAL-CODE")
            cloud = TrackingProvider("cloud", "CLOUD")
            core = make_core(
                root,
                local_coder=local,
                cloud_coder=cloud,
                cloud_reasoning=cloud,
                file_read_tool=SafeFileReadTool(root),
                approval_callback=lambda request: True,
            )
            sid = core.get_or_create_session(False)
            response = core.ask_file_local(sid, "sample.py", "이 함수 설명해줘")
            self.assertIn("LOCAL-CODE", response)
            self.assertEqual(local.generate_calls, 1)
            self.assertEqual(cloud.generate_calls, 0)
            joined = "\n".join(m.content for m in local.last_messages)
            self.assertIn("def add", joined)
            self.assertIn("분석 대상 데이터", joined)
            saved = core.storage.get_messages(sid)
            saved_text = "\n".join(m.content for m in saved)
            self.assertIn("local file question", saved_text)
            self.assertNotIn("def add(a, b)", saved_text)

    def test_ask_file_local_failure_does_not_escalate_cloud(self):
        from jarvis.tools import SafeFileReadTool

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sample.py").write_text("print('x')", encoding="utf-8")
            cloud = TrackingProvider("cloud", "CLOUD")
            core = make_core(
                root,
                local_coder=FailingProvider("coder"),
                local_general=FailingProvider("general"),
                cloud_coder=cloud,
                cloud_reasoning=cloud,
                file_read_tool=SafeFileReadTool(root),
                approval_callback=lambda request: True,
            )
            sid = core.get_or_create_session(False)
            response = core.ask_file_local(sid, "sample.py", "설명해줘")
            self.assertIn("Cloud로는 보내지 않았습니다", response)
            self.assertEqual(cloud.generate_calls, 0)


class LocalFileContextBudgetTests(unittest.TestCase):
    def test_file_question_budget_prioritizes_source_and_keeps_head_tail(self):
        with tempfile.TemporaryDirectory() as td:
            core = make_core(Path(td), context_char_budget=1000)
            content = "FILE-START\n" + ("x" * 4000) + "\nFILE-END"
            messages = core._build_file_question_messages("big.py", content, "무슨 코드야?")
            joined = "\n".join(m.content for m in messages)
            self.assertLessEqual(sum(len(m.content) for m in messages), 1000)
            self.assertIn("FILE-START", joined)
            self.assertIn("FILE-END", joined)
            self.assertIn("중간 생략", joined)


class ProjectContextLocalOnlyTests(unittest.TestCase):
    def setUp(self):
        from jarvis.tools import SafeProjectContextTool

        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        (self.path / "router.py").write_text(
            "def route_request(text):\n    return 'local'  # routing logic\n",
            encoding="utf-8",
        )
        self.cloud = TrackingProvider("cloud", "CLOUD")
        self.local = TrackingProvider("local", "LOCAL")
        self.core = make_core(
            self.path,
            local_coder=self.local,
            cloud_coder=self.cloud,
            cloud_reasoning=self.cloud,
            project_context_tool=SafeProjectContextTool(self.path),
        )
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ask_project_uses_local_only(self):
        response = self.core.ask_project_local(self.sid, "routing logic route_request")
        self.assertIn("LOCAL", response)
        self.assertEqual(self.local.generate_calls, 1)
        self.assertEqual(self.cloud.generate_calls, 0)
        joined = "\n".join(m.content for m in self.local.last_messages)
        self.assertIn("router.py", joined)
        self.assertIn("분석 대상 데이터", joined)

    def test_project_source_is_not_persisted_as_second_copy(self):
        self.core.ask_project_local(self.sid, "routing logic route_request")
        stored = "\n".join(m.content for m in self.core.storage.get_messages(self.sid))
        self.assertNotIn("return 'local'", stored)
        self.assertIn("[local project question]", stored)


class CloudSensitiveHardBlockTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sensitive_data_cannot_be_overridden_by_yes_callback(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        seen = []
        core = make_core(
            self.path,
            cloud_reasoning=cloud,
            approval_callback=lambda request: seen.append(request) or True,
            cloud_block_sensitive=True,
        )
        sid = core.get_or_create_session(False)
        core.handle(sid, "심층 분석 해줘 api_key=VERY_SECRET_123")
        self.assertEqual(cloud.generate_calls, 0)
        self.assertTrue(seen)
        self.assertTrue(seen[0].hard_blocked)

    def test_explicit_sensitive_block_override_allows_normal_approval_flow(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        core = make_core(
            self.path,
            cloud_reasoning=cloud,
            approval_callback=lambda request: True,
            cloud_block_sensitive=False,
        )
        sid = core.get_or_create_session(False)
        core.handle(sid, "심층 분석 해줘 api_key=VERY_SECRET_123")
        self.assertEqual(cloud.generate_calls, 1)

    def test_disabling_approval_does_not_disable_sensitive_hard_block(self):
        cloud = TrackingProvider("cloud", "CLOUD")
        core = make_core(
            self.path,
            cloud_reasoning=cloud,
            require_cloud_approval=False,
            approval_callback=None,
            cloud_block_sensitive=True,
        )
        sid = core.get_or_create_session(False)
        response, _ = core.handle(sid, "심층 분석 해줘 api_key=VERY_SECRET_123")
        self.assertEqual(cloud.generate_calls, 0)
        self.assertIn("민감정보 Cloud 차단", response)

    def test_redaction_covers_common_token_shapes(self):
        text = (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "AIzaabcdefghijklmnopqrstuvwxyz123456 "
            "ghp_abcdefghijklmnopqrstuvwxyz123456"
        )
        redacted = redact_sensitive_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertIn("REDACTED", redacted)

    def test_redaction_covers_additional_common_secret_shapes(self):
        values = [
            "AKIAABCDEFGHIJKLMNOP",
            "glpat-abcdefghijklmnopqrstuv",
            "xoxb-1234567890-abcdefghijkl",
            "eyJabcdefghij.abcdefghijkl.abcdefghijk",
            "postgresql://jarvis:supersecret@localhost:5432/db",
        ]
        redacted = redact_sensitive_text(" ".join(values))
        for value in values:
            self.assertNotIn(value, redacted)
        self.assertGreaterEqual(redacted.count("REDACTED"), len(values))

    def test_additional_secret_shapes_hard_block_cloud(self):
        samples = [
            "AKIAABCDEFGHIJKLMNOP",
            "glpat-abcdefghijklmnopqrstuv",
            "xoxb-1234567890-abcdefghijkl",
            "eyJabcdefghij.abcdefghijkl.abcdefghijk",
            "postgresql://jarvis:supersecret@localhost:5432/db",
        ]
        for secret in samples:
            with self.subTest(secret=secret[:8]):
                cloud = TrackingProvider("cloud", "CLOUD")
                core = make_core(
                    self.path,
                    cloud_reasoning=cloud,
                    approval_callback=lambda request: True,
                    cloud_block_sensitive=True,
                )
                sid = core.get_or_create_session(False)
                core.handle(sid, f"심층 분석 해줘 {secret}")
                self.assertEqual(cloud.generate_calls, 0)


class ManualRouteOverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_forced_local_coder_bypasses_router_but_persists_route(self):
        core = make_core(self.path)
        sid = core.get_or_create_session(False)
        response, decision = core.handle_forced(sid, "그냥 안녕", Destination.LOCAL_CODER)
        self.assertEqual(decision.destination, Destination.LOCAL_CODER)
        self.assertIn("CODER", response)
        event = core.storage.get_route_event(core.last_route_event_id)
        self.assertEqual(event.matched, "manual")


class RouteEventSecretRedactionTests(unittest.TestCase):
    def test_route_event_does_not_duplicate_obvious_secret(self):
        with tempfile.TemporaryDirectory() as td:
            core = make_core(Path(td))
            sid = core.get_or_create_session(False)
            core.handle(sid, "안녕 api_key=SUPER_SECRET_VALUE")
            event = core.storage.get_route_event(core.last_route_event_id)
            self.assertNotIn("SUPER_SECRET_VALUE", event.text)
            self.assertIn("REDACTED", event.text)
            # Original conversation is still stored once because it is user-owned session history.
            self.assertIn("SUPER_SECRET_VALUE", core.storage.get_messages(sid)[0].content)


class ProjectRootSwitchTests(unittest.TestCase):
    def test_can_narrow_but_not_escape_approved_workspace(self):
        from jarvis.tools import SafeFileReadTool, SafeProjectContextTool
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "workspace"
            project = workspace / "project_a"
            project.mkdir(parents=True)
            (project / "a.py").write_text("value = 1", encoding="utf-8")
            core = make_core(
                Path(td),
                file_search_tool=SafeFileSearchTool(workspace),
                file_read_tool=SafeFileReadTool(workspace),
                project_context_tool=SafeProjectContextTool(workspace),
            )
            switched = core.set_project_root("project_a")
            self.assertEqual(switched, project.resolve())
            self.assertIn("a.py", core.file_search_tool.run(query="a.py"))
            with self.assertRaises(Exception):
                core.set_project_root("../../")
            with self.assertRaises(Exception):
                core.set_project_root(str(project.resolve()))


class CodingPromptRouteTests(unittest.TestCase):
    """코드 경로에서만 코딩 지시가 붙는다. 실측 근거는 PERFORMANCE.md 8절."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core = make_core(Path(self.temp_dir.name))
        self.session = self.core.get_or_create_session()
        self.core.storage.add_message(self.session, "user", "파이썬 함수 하나 써줘")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _systems(self, destination):
        from jarvis.core import CODING_SYSTEM_ADDENDUM
        messages = self.core._build_messages(self.session, destination)
        return CODING_SYSTEM_ADDENDUM in [m.content for m in messages if m.role == "system"]

    def test_local_coder_gets_coding_addendum(self):
        self.assertTrue(self._systems(Destination.LOCAL_CODER))

    def test_cloud_coder_gets_coding_addendum(self):
        self.assertTrue(self._systems(Destination.CLOUD_CODER))

    def test_general_route_does_not_get_it(self):
        self.assertFalse(self._systems(Destination.LOCAL_GENERAL))

    def test_no_destination_does_not_get_it(self):
        self.assertFalse(self._systems(None))
