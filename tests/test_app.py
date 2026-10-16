"""CLI command handling tests. 실제 input() 없이 handle_command 만 검증한다."""
import tempfile
import unittest
from pathlib import Path

from jarvis.app import NUM_CTX_BY_PROFILE, build_providers, build_summary_provider, handle_command
from jarvis.config import Settings
from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider, OllamaProvider
from jarvis.storage import Storage
from jarvis.tools import SafeFileSearchTool


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        (self.path / "router.py").write_text("x", encoding="utf-8")
        self.core = JarvisCore(
            storage=Storage(self.path / "test.db"),
            hardware=HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("g", "GENERAL"),
            local_coder=MockLLMProvider("c", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD CODER"),
            cloud_reasoning=CloudStubProvider("cr", "CLOUD REASONING"),
            file_search_tool=SafeFileSearchTool(self.path),
        )
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cmd(self, text):
        return handle_command(self.core, self.sid, text)

    def test_exit_stops_loop(self):
        _, keep_going, _ = self.run_cmd("/exit")
        self.assertFalse(keep_going)

    def test_help(self):
        output, keep_going, _ = self.run_cmd("/help")
        self.assertTrue(keep_going)
        self.assertIn("/remember", output)

    def test_new_session_changes_id(self):
        _, _, new_sid = self.run_cmd("/new")
        self.assertNotEqual(new_sid, self.sid)

    def test_remember_recall_forget(self):
        self.run_cmd("/remember mode=study")
        output, _, _ = self.run_cmd("/recall mode")
        self.assertEqual(output, "study")
        output, _, _ = self.run_cmd("/forget mode")
        self.assertIn("삭제", output)

    def test_remember_requires_equals(self):
        output, _, _ = self.run_cmd("/remember modestudy")
        self.assertIn("사용법", output)

    def test_memories_listing(self):
        self.run_cmd("/remember a=1")
        output, _, _ = self.run_cmd("/memories")
        self.assertIn("a = 1", output)

    def test_find(self):
        output, _, _ = self.run_cmd("/find router")
        self.assertIn("router.py", output)

    def test_find_rejects_traversal_without_crashing(self):
        output, _, _ = self.run_cmd("/find ../etc")
        self.assertIn("경로 문자", output)

    def test_profile(self):
        output, _, _ = self.run_cmd("/profile")
        self.assertIn("LAPTOP", output)

    def test_sessions(self):
        self.core.handle(self.sid, "안녕")
        output, _, _ = self.run_cmd("/sessions")
        self.assertIn(self.sid, output)

    def test_unknown_command(self):
        output, keep_going, _ = self.run_cmd("/nope")
        self.assertTrue(keep_going)
        self.assertIn("알 수 없는 명령", output)


class ProviderBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(file_search_root=Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mock_mode_by_default(self):
        hardware = HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN)
        general, coder, notes = build_providers(self.settings, hardware)
        self.assertIsInstance(general, MockLLMProvider)
        self.assertIsInstance(coder, MockLLMProvider)
        self.assertTrue(any("Mock" in note for note in notes))

    def test_ollama_mode_uses_profile_context(self):
        self.settings.use_ollama = True
        self.settings.ollama_host = "http://127.0.0.1:9"
        hardware = HardwareInfo("OS", "CPU", 96, "RTX 5080", 16, PerformanceProfile.DESKTOP)
        general, coder, notes = build_providers(self.settings, hardware)
        self.assertIsInstance(general, OllamaProvider)
        self.assertEqual(general.num_ctx, NUM_CTX_BY_PROFILE[PerformanceProfile.DESKTOP])
        self.assertEqual(coder.model, self.settings.desktop_coder_model)
        # 서버가 없으므로 health_check 경고가 있어야 한다.
        self.assertTrue(any("health_check" in note for note in notes))

    def test_summary_uses_same_model_without_thinking_and_with_smaller_output_cap(self):
        general = OllamaProvider("qwen3.8:27b", think=True, num_ctx=16384,
                                 num_predict=6144, keep_alive="10m")
        summary = build_summary_provider(general, self.settings)
        self.assertIsInstance(summary, OllamaProvider)
        self.assertIsNot(summary, general)
        self.assertEqual(summary.model, general.model)
        self.assertEqual(summary.num_ctx, general.num_ctx)
        self.assertFalse(summary.think)
        self.assertEqual(summary.num_predict, 3000)
        self.assertIs(build_summary_provider(summary, self.settings), summary)

    def test_explicit_num_ctx_overrides_profile(self):
        self.settings.use_ollama = True
        self.settings.ollama_host = "http://127.0.0.1:9"
        self.settings.num_ctx = 8192
        hardware = HardwareInfo("OS", "CPU", 32, "RTX 4070 Laptop", 8, PerformanceProfile.LAPTOP)
        general, coder, _ = build_providers(self.settings, hardware)
        self.assertEqual(general.num_ctx, 8192)
        self.assertEqual(coder.num_ctx, 8192)

    def test_zero_num_ctx_falls_back_to_profile(self):
        self.settings.use_ollama = True
        self.settings.ollama_host = "http://127.0.0.1:9"
        self.settings.num_ctx = 0
        hardware = HardwareInfo("OS", "CPU", 32, "RTX 4070 Laptop", 8, PerformanceProfile.LAPTOP)
        general, _, _ = build_providers(self.settings, hardware)
        self.assertEqual(general.num_ctx, NUM_CTX_BY_PROFILE[PerformanceProfile.LAPTOP])

    def test_laptop_gets_smaller_context_than_desktop(self):
        self.assertLess(
            NUM_CTX_BY_PROFILE[PerformanceProfile.LAPTOP],
            NUM_CTX_BY_PROFILE[PerformanceProfile.DESKTOP],
        )


if __name__ == "__main__":
    unittest.main()


class V012CommandTests(unittest.TestCase):
    def setUp(self):
        from jarvis.tools import SafeFileReadTool

        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        (self.path / "note.md").write_text("hello read", encoding="utf-8")
        self.core = JarvisCore(
            storage=Storage(self.path / "test.db"),
            hardware=HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("g", "GENERAL"),
            local_coder=MockLLMProvider("c", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD CODER"),
            cloud_reasoning=CloudStubProvider("cr", "CLOUD REASONING"),
            file_search_tool=SafeFileSearchTool(self.path),
            file_read_tool=SafeFileReadTool(self.path),
            misroute_log_path=self.path / "logs" / "misroute.jsonl",
        )
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_command(self):
        output, _, _ = handle_command(self.core, self.sid, "/read note.md")
        self.assertIn("hello read", output)

    def test_doctor_command(self):
        output, _, _ = handle_command(self.core, self.sid, "/doctor")
        self.assertIn("DB integrity: OK", output)

    def test_wrong_command(self):
        self.core.handle(self.sid, "파이썬 함수 알려줘")
        output, _, _ = handle_command(self.core, self.sid, "/wrong local_general")
        self.assertIn("오분류", output)

    def test_route_stats_command(self):
        self.core.handle(self.sid, "파이썬 함수 알려줘")
        handle_command(self.core, self.sid, "/wrong local_general")
        output, _, _ = handle_command(self.core, self.sid, "/route-stats")
        self.assertIn("local_coder", output)

    def test_summary_command_empty(self):
        output, _, _ = handle_command(self.core, self.sid, "/summary")
        self.assertIn("아직", output)


class BackupCommandTests(unittest.TestCase):
    def test_backup_command_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = JarvisCore(
                storage=Storage(root / "jarvis.db"),
                hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
                local_general=MockLLMProvider("g", "G"),
                local_coder=MockLLMProvider("c", "C"),
                cloud_coder=CloudStubProvider("cc", "CC"),
                cloud_reasoning=CloudStubProvider("cr", "CR"),
            )
            sid = core.get_or_create_session(False)
            output, _, _ = handle_command(core, sid, "/backup")
            self.assertIn("백업 완료", output)
            self.assertTrue((root / "backups").exists())


class SessionCommandTests(unittest.TestCase):
    def test_use_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = JarvisCore(
                storage=Storage(root / "jarvis.db"),
                hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
                local_general=MockLLMProvider("g", "G"),
                local_coder=MockLLMProvider("c", "C"),
                cloud_coder=CloudStubProvider("cc", "CC"),
                cloud_reasoning=CloudStubProvider("cr", "CR"),
            )
            first = core.get_or_create_session(False)
            second = core.get_or_create_session(False)
            output, _, current = handle_command(core, second, f"/use {first}")
            self.assertEqual(current, first)
            self.assertIn("전환", output)
            output, _, _ = handle_command(core, first, "/export")
            self.assertIn("export 완료", output)
            self.assertTrue((root / "exports" / f"session-{first}.md").exists())


class LaptopSingleModelTests(unittest.TestCase):
    def test_laptop_ollama_shares_one_model_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(
                file_search_root=Path(td),
                use_ollama=True,
                ollama_host="http://127.0.0.1:9",
                single_model_on_laptop=True,
            )
            hardware = HardwareInfo("OS", "CPU", 32, "RTX 4070 Laptop", 8, PerformanceProfile.LAPTOP)
            general, coder, notes = build_providers(settings, hardware)
            self.assertIs(general, coder)
            self.assertEqual(coder.model, settings.general_model)
            self.assertTrue(any("single-model" in note for note in notes))

    def test_laptop_can_opt_into_separate_coder_model(self):
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(
                file_search_root=Path(td),
                use_ollama=True,
                ollama_host="http://127.0.0.1:9",
                single_model_on_laptop=False,
            )
            hardware = HardwareInfo("OS", "CPU", 32, "RTX 4070 Laptop", 8, PerformanceProfile.LAPTOP)
            general, coder, _ = build_providers(settings, hardware)
            self.assertIsNot(general, coder)
            self.assertEqual(coder.model, settings.coder_model)


class ProfileOverrideTests(unittest.TestCase):
    def test_apply_profile_override(self):
        from jarvis.app import apply_profile_override

        info = HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN)
        note = apply_profile_override(info, "LAPTOP")
        self.assertEqual(info.profile, PerformanceProfile.LAPTOP)
        self.assertIn("UNKNOWN -> LAPTOP", note)

    def test_empty_override_does_nothing(self):
        from jarvis.app import apply_profile_override

        info = HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN)
        self.assertIsNone(apply_profile_override(info, ""))
        self.assertEqual(info.profile, PerformanceProfile.UNKNOWN)


class RouteFeedbackSessionResetTests(unittest.TestCase):
    def test_new_session_clears_last_route_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = JarvisCore(
                storage=Storage(root / "j.db"),
                hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
                local_general=MockLLMProvider("g", "G"),
                local_coder=MockLLMProvider("c", "C"),
                cloud_coder=CloudStubProvider("cc", "CC"),
                cloud_reasoning=CloudStubProvider("cr", "CR"),
            )
            sid = core.get_or_create_session(False)
            core.handle(sid, "안녕")
            self.assertIsNotNone(core.last_route_event_id)
            _, _, new_sid = handle_command(core, sid, "/new")
            self.assertNotEqual(new_sid, sid)
            self.assertIsNone(core.last_route_event_id)


class AskFileCommandTests(unittest.TestCase):
    def test_ask_file_command_and_usage(self):
        from jarvis.tools import SafeFileReadTool

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "code.py").write_text("x = 1", encoding="utf-8")
            core = JarvisCore(
                storage=Storage(root / "j.db"),
                hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
                local_general=MockLLMProvider("g", "G"),
                local_coder=MockLLMProvider("c", "C"),
                cloud_coder=CloudStubProvider("cc", "CC"),
                cloud_reasoning=CloudStubProvider("cr", "CR"),
                file_read_tool=SafeFileReadTool(root),
            )
            sid = core.get_or_create_session(False)
            output, keep, _ = handle_command(core, sid, "/ask-file code.py :: 이 코드 설명")
            self.assertTrue(keep)
            self.assertIn("C", output)
            bad, _, _ = handle_command(core, sid, "/ask-file code.py")
            self.assertIn("사용법", bad)


class ProjectContextCommandTests(unittest.TestCase):
    def setUp(self):
        from jarvis.tools import SafeProjectContextTool

        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        (self.path / "router.py").write_text(
            "def route_request(text):\n    return text  # router keyword\n", encoding="utf-8"
        )
        self.core = JarvisCore(
            storage=Storage(self.path / "db.sqlite"),
            hardware=HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("g", "GENERAL"),
            local_coder=MockLLMProvider("c", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD"),
            cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            project_context_tool=SafeProjectContextTool(self.path),
        )
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_grep_command(self):
        output, keep, _ = handle_command(self.core, self.sid, "/grep router keyword")
        self.assertTrue(keep)
        self.assertIn("router.py", output)

    def test_ask_project_command(self):
        output, keep, _ = handle_command(self.core, self.sid, "/ask-project router keyword 설명")
        self.assertTrue(keep)
        self.assertIn("CODER", output)


class SessionRenameCommandTests(unittest.TestCase):
    def test_rename_updates_session_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = JarvisCore(
                storage=Storage(root / "db.sqlite"),
                hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
                local_general=MockLLMProvider("g", "G"),
                local_coder=MockLLMProvider("c", "C"),
                cloud_coder=CloudStubProvider("cc", "CC"),
                cloud_reasoning=CloudStubProvider("cr", "CR"),
            )
            sid = core.get_or_create_session(False)
            output, _, _ = handle_command(core, sid, "/rename My Project")
            self.assertIn("My Project", output)
            listing, _, _ = handle_command(core, sid, "/sessions")
            self.assertIn("title=My Project", listing)


class CloudProviderBuildTests(unittest.TestCase):
    def test_stub_is_default(self):
        from jarvis.app import build_cloud_providers
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(local_only=False, file_search_root=Path(td))
            coder, reasoning, notes = build_cloud_providers(settings)
            self.assertIsInstance(coder, CloudStubProvider)
            self.assertIn("STUB", " ".join(notes))

    def test_openai_requires_env_key(self):
        from jarvis.app import build_cloud_providers
        from jarvis.config import ConfigError
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(local_only=False, 
                file_search_root=Path(td), cloud_provider="openai",
                cloud_coder_model="coder", cloud_reasoning_model="reasoner"
            )
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ConfigError):
                    build_cloud_providers(settings)

    def test_openai_builds_without_exposing_key_in_notes(self):
        from jarvis.app import build_cloud_providers
        from jarvis.providers import OpenAIResponsesProvider
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(local_only=False, 
                file_search_root=Path(td), cloud_provider="openai",
                cloud_coder_model="coder-model", cloud_reasoning_model="reason-model"
            )
            with patch.dict("os.environ", {"OPENAI_API_KEY": "TOP_SECRET"}, clear=True):
                coder, reasoning, notes = build_cloud_providers(settings)
            self.assertIsInstance(coder, OpenAIResponsesProvider)
            self.assertNotIn("TOP_SECRET", " ".join(notes))


class ManualRouteCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cloud = CloudStubProvider("cc", "CLOUD")
        self.core = JarvisCore(
            storage=Storage(self.root / "db.sqlite"),
            hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
            local_general=MockLLMProvider("g", "GENERAL"),
            local_coder=MockLLMProvider("c", "CODER"),
            cloud_coder=self.cloud,
            cloud_reasoning=CloudStubProvider("cr", "REASON"),
            require_cloud_approval=True,
            approval_callback=lambda request: False,
        )
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_route_preview_does_not_add_messages(self):
        output, _, _ = handle_command(self.core, self.sid, "/route Python 함수")
        self.assertIn("local_coder", output)
        self.assertEqual(self.core.storage.message_count(self.sid), 0)

    def test_ask_local_forces_local(self):
        output, _, _ = handle_command(self.core, self.sid, "/ask-local 코드베이스 전체 리팩터링")
        self.assertIn("CODER", output)

    def test_ask_cloud_still_respects_approval(self):
        output, _, _ = handle_command(self.core, self.sid, "/ask-cloud Python 함수")
        self.assertIn("local", output.lower())


class ProjectRootCommandTests(unittest.TestCase):
    def test_project_command_switches_within_approved_root(self):
        from jarvis.tools import SafeFileReadTool, SafeProjectContextTool
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "p1").mkdir()
            core = JarvisCore(
                storage=Storage(root / "db.sqlite"),
                hardware=HardwareInfo("OS", "CPU", 32, None, None, PerformanceProfile.UNKNOWN),
                local_general=MockLLMProvider("g", "G"),
                local_coder=MockLLMProvider("c", "C"),
                cloud_coder=CloudStubProvider("cc", "CC"),
                cloud_reasoning=CloudStubProvider("cr", "CR"),
                file_search_tool=SafeFileSearchTool(root),
                file_read_tool=SafeFileReadTool(root),
                project_context_tool=SafeProjectContextTool(root),
            )
            sid = core.get_or_create_session(False)
            output, _, _ = handle_command(core, sid, "/project p1")
            self.assertIn("p1", output)
            current, _, _ = handle_command(core, sid, "/project")
            self.assertIn("p1", current)

class DeleteSessionCommandTests(unittest.TestCase):
    def test_delete_requires_yes_and_current_creates_replacement(self):
        from jarvis.app import handle_command
        import tempfile
        from pathlib import Path
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import MockLLMProvider, CloudStubProvider
        from jarvis.storage import Storage
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td) / "x.db")
            core = JarvisCore(
                storage=storage,
                hardware=HardwareInfo("OS","CPU",32,"GPU",8,PerformanceProfile.LAPTOP),
                local_general=MockLLMProvider("g","G"),
                local_coder=MockLLMProvider("c","C"),
                cloud_coder=CloudStubProvider("cc","CC"),
                cloud_reasoning=CloudStubProvider("cr","CR"),
            )
            sid = core.get_or_create_session(False)
            out, keep, same = handle_command(core, sid, f"/delete-session {sid}")
            self.assertIn("확인", out)
            self.assertEqual(same, sid)
            out, keep, new_sid = handle_command(core, sid, "/delete-session current --yes")
            self.assertTrue(keep)
            self.assertNotEqual(new_sid, sid)
            self.assertFalse(storage.session_exists(sid))
            self.assertTrue(storage.session_exists(new_sid))

class ProfileAwareModelSelectionTests(unittest.TestCase):
    def test_laptop_uses_minimum_models(self):
        from jarvis.app import effective_local_models
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(file_search_root=Path(td))
            self.assertEqual(
                effective_local_models(settings, PerformanceProfile.LAPTOP),
                ("qwen3:8b-q4_K_M", "qwen3:8b-q4_K_M"),
            )

    def test_desktop_automatically_selects_desktop_models(self):
        from jarvis.app import effective_local_models
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(file_search_root=Path(td))
            self.assertEqual(
                effective_local_models(settings, PerformanceProfile.DESKTOP),
                ("gpt-oss:20b", "gpt-oss:20b"),
            )

    def test_profile_model_auto_selection_can_be_disabled(self):
        from jarvis.app import effective_local_models
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(
                file_search_root=Path(td),
                auto_profile_models=False,
                general_model="custom-general",
                coder_model="custom-coder",
            )
            self.assertEqual(
                effective_local_models(settings, PerformanceProfile.DESKTOP),
                ("custom-general", "custom-coder"),
            )
