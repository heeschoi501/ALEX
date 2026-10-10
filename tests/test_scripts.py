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
            s = Settings(file_search_root=Path(td))
            self.assertEqual(required_models(s, PerformanceProfile.DESKTOP), [s.desktop_general_model, s.desktop_coder_model])

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
