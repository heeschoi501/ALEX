"""Small tests for helper scripts."""
import tempfile
import unittest
from pathlib import Path

from scripts.misroute_stats import load_records, summarize


class MisrouteStatsTests(unittest.TestCase):
    def test_empty(self):
        self.assertIn("No misroute", summarize([]))

    def test_summary(self):
        records = [
            {"destination": "local_coder", "correction": "local_general", "matched": "파이썬"},
            {"destination": "local_coder", "correction": "local_general", "matched": "파이썬"},
        ]
        output = summarize(records)
        self.assertIn("Total misroutes: 2", output)
        self.assertIn("local_coder -> local_general: 2", output)

    def test_malformed_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "m.jsonl"
            path.write_text('{"destination":"a"}\nnot-json\n', encoding="utf-8")
            records = load_records(path)
            self.assertEqual(len(records), 1)


class BenchmarkHelperTests(unittest.TestCase):
    def test_duration_and_tokens_per_second(self):
        from scripts.benchmark_local import _ns_to_seconds, _tokens_per_second

        self.assertEqual(_ns_to_seconds(2_500_000_000), 2.5)
        self.assertEqual(_tokens_per_second(50, 2_000_000_000), 25.0)
        self.assertIsNone(_tokens_per_second(50, 0))

    def test_laptop_both_avoids_duplicate_shared_model(self):
        from jarvis.config import Settings
        from jarvis.hardware import PerformanceProfile
        from scripts.benchmark_local import _targets

        with tempfile.TemporaryDirectory() as td:
            settings = Settings(file_search_root=Path(td), single_model_on_laptop=True)
            targets = _targets(settings, PerformanceProfile.LAPTOP, "both")
            self.assertEqual(targets, [("general", settings.general_model)])

    def test_desktop_both_includes_coder(self):
        from jarvis.config import Settings
        from jarvis.hardware import PerformanceProfile
        from scripts.benchmark_local import _targets

        with tempfile.TemporaryDirectory() as td:
            settings = Settings(file_search_root=Path(td), single_model_on_laptop=True)
            targets = _targets(settings, PerformanceProfile.DESKTOP, "both")
            self.assertEqual([label for label, _ in targets], ["general", "coder"])

    def test_optional_pygame_is_worker_only_and_training_imports_remain_blocked(self):
        from scripts import dependency_audit
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "jarvis").mkdir()
            (root / "scripts").mkdir()
            (root / "jarvis/build_runner.py").write_text("import pygame\nimport torch\n")
            (root / "jarvis/app.py").write_text("import pygame\n")
            with patch.object(dependency_audit, "ROOT", root):
                self.assertEqual(dependency_audit.audit(), [(Path("jarvis/app.py"), "pygame"),
                                                            (Path("jarvis/build_runner.py"), "torch")])

class ModelSetupTests(unittest.TestCase):
    def test_laptop_requires_only_shared_general_by_default(self):
        from jarvis.config import Settings
        from jarvis.hardware import PerformanceProfile
        from scripts.setup_models import required_models
        with tempfile.TemporaryDirectory() as td:
            s = Settings(file_search_root=Path(td))
            self.assertEqual(required_models(s, PerformanceProfile.LAPTOP), [s.general_model])

    def test_desktop_requires_profile_general_and_coder(self):
        from jarvis.config import Settings
        from jarvis.hardware import PerformanceProfile
        from scripts.setup_models import required_models
        with tempfile.TemporaryDirectory() as td:
            s = Settings(file_search_root=Path(td), desktop_general_model="general:test", desktop_coder_model="coder:test")
            self.assertEqual(required_models(s, PerformanceProfile.DESKTOP), ["general:test", "coder:test"])

    def test_desktop_default_shares_one_model(self):
        from jarvis.config import Settings
        from jarvis.hardware import PerformanceProfile
        from scripts.setup_models import required_models
        with tempfile.TemporaryDirectory() as td:
            s = Settings(file_search_root=Path(td))
            self.assertEqual(required_models(s, PerformanceProfile.DESKTOP), ["gpt-oss:20b"])

class ArchiveAuditTests(unittest.TestCase):
    def _write_zip(self, path: Path, files: dict[str, str]):
        import zipfile
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)

    def test_clean_archive_passes(self):
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "release.zip"
            files = {f"project_jarvis_v0_2_0/{name}": "x" for name in REQUIRED_RELATIVE}
            self._write_zip(archive_path, files)
            ok, issues = audit(archive_path)
            self.assertTrue(ok, issues)

    def test_public_archive_does_not_require_private_handoff_notes(self):
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "public.zip"
            public_files = REQUIRED_RELATIVE - {"CLAUDE_NEXT.md", "CLAUDE_START_PROMPT.md"}
            self._write_zip(archive_path, {f"alex/{name}": "x" for name in public_files})
            ok, issues = audit(archive_path)
            self.assertTrue(ok, issues)

    def test_runtime_and_private_configuration_are_rejected(self):
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "public.zip"
            for name in ("runtime/state.json", "node_modules/vendor.js", "test-results/trace.zip",
                         "playwright-report/index.html", "jarvis.local.json", "ollama-release.json",
                         "CLAUDE_NEXT.md", "CLAUDE_START_PROMPT.md"):
                with self.subTest(name=name):
                    self._write_zip(archive_path, {**{f"alex/{f}": "x" for f in REQUIRED_RELATIVE},
                                                  f"alex/{name}": "private"})
                    ok, issues = audit(archive_path)
                    self.assertFalse(ok, issues)

    def test_release_export_excludes_private_handoff_and_runtime(self):
        from scripts import package_release
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("README.md", "CLAUDE_NEXT.md", "CLAUDE_START_PROMPT.md",
                         "jarvis.local.json", "runtime/state.json", "node_modules/vendor.js",
                         "playwright-report/index.html", "test-results/trace.zip"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            with patch.object(package_release, "ROOT", root):
                exported = {str(relative) for _, relative in package_release.iter_release_files()}
            self.assertEqual(exported, {"README.md"})

    def test_runtime_data_is_rejected(self):
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "release.zip"
            files = {f"project_jarvis_v0_2_0/{name}": "x" for name in REQUIRED_RELATIVE}
            files["project_jarvis_v0_2_0/data/jarvis.db"] = "secret runtime state"
            self._write_zip(archive_path, files)
            ok, issues = audit(archive_path)
            self.assertFalse(ok)
            self.assertTrue(any("금지된" in issue for issue in issues))

    def test_parent_traversal_member_is_rejected(self):
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "release.zip"
            files = {f"project_jarvis_v0_2_0/{name}": "x" for name in REQUIRED_RELATIVE}
            files["project_jarvis_v0_2_0/../outside.txt"] = "escape"
            self._write_zip(archive_path, files)
            ok, issues = audit(archive_path)
            self.assertFalse(ok)
            self.assertTrue(any("위험한 archive 경로" in issue for issue in issues))

    def test_backslash_member_is_rejected(self):
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "release.zip"
            files = {f"project_jarvis_v0_2_0/{name}": "x" for name in REQUIRED_RELATIVE}
            files[r"project_jarvis_v0_2_0\..\outside.txt"] = "escape"
            self._write_zip(archive_path, files)
            ok, issues = audit(archive_path)
            self.assertFalse(ok)
            self.assertTrue(any("위험한 archive 경로" in issue for issue in issues))

class ArchiveLinkTests(unittest.TestCase):
    def test_zip_symlink_is_rejected(self):
        import stat
        import zipfile
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name in REQUIRED_RELATIVE:
                    archive.writestr(f"project_jarvis_v0_2_0/{name}", "x")
                info = zipfile.ZipInfo("project_jarvis_v0_2_0/link")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "../outside")
            ok, issues = audit(archive_path)
            self.assertFalse(ok)
            self.assertTrue(any("archive link" in issue for issue in issues))


class ArchiveSensitiveFileTests(unittest.TestCase):
    def test_dotenv_is_rejected(self):
        import zipfile
        from scripts.archive_audit import REQUIRED_RELATIVE, audit
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name in REQUIRED_RELATIVE:
                    archive.writestr(f"project_jarvis_v0_2_0/{name}", "x")
                archive.writestr("project_jarvis_v0_2_0/.env", "OPENAI_API_KEY=secret")
            ok, issues = audit(archive_path)
            self.assertFalse(ok)
            self.assertTrue(any("민감한 파일명" in issue for issue in issues))
