"""SafeFileSearchTool security tests."""
import os
import tempfile
import unittest
from pathlib import Path

from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.tools import SafeFileSearchTool, SystemInfoTool, ToolError


class SystemInfoToolTests(unittest.TestCase):
    def test_contains_profile(self):
        info = HardwareInfo("OS", "CPU", 32, "RTX 4070", 8, PerformanceProfile.LAPTOP)
        output = SystemInfoTool(info).run()
        self.assertIn("profile: LAPTOP", output)
        self.assertIn("vram_gb: 8", output)


class SafeFileSearchToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "project"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "router.py").write_text("x", encoding="utf-8")
        (self.root / "readme.md").write_text("x", encoding="utf-8")
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "router.pyc").write_text("x", encoding="utf-8")
        self.outside = Path(self.temp_dir.name) / "secret"
        self.outside.mkdir()
        (self.outside / "router_secret.txt").write_text("x", encoding="utf-8")
        self.tool = SafeFileSearchTool(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_finds_file(self):
        self.assertIn("router.py", self.tool.run(query="router"))

    def test_skips_pycache(self):
        self.assertNotIn(".pyc", self.tool.run(query="router"))

    def test_empty_query(self):
        self.assertIn("검색어", self.tool.run(query=""))

    def test_path_traversal_blocked(self):
        for bad in ["../secret", "/etc/passwd", "..", "~/.ssh"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError):
                    self.tool.run(query=bad)

    def test_no_match_message(self):
        self.assertIn("일치하는 파일이 없습니다", self.tool.run(query="zzzz"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_symlink_escape_blocked(self):
        try:
            (self.root / "link").symlink_to(self.outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not permitted in this environment")
        self.assertNotIn("router_secret", self.tool.run(query="router"))

    def test_max_results_limit(self):
        many = self.root / "many"
        many.mkdir()
        for i in range(30):
            (many / f"item{i}.txt").write_text("x", encoding="utf-8")
        output = SafeFileSearchTool(self.root, max_results=5).run(query="item")
        self.assertLessEqual(len([line for line in output.splitlines() if line.startswith("many")]), 5)

    def test_missing_root_raises(self):
        tool = SafeFileSearchTool(self.root / "gone")
        with self.assertRaises(ToolError):
            tool.run(query="router")


if __name__ == "__main__":
    unittest.main()


class SafeFileReadToolTests(unittest.TestCase):
    def setUp(self):
        from jarvis.tools import SafeFileReadTool

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir()
        (self.root / "code.py").write_text("print('hello')", encoding="utf-8")
        (self.root / "blob.bin").write_bytes(b"abc")
        (self.root / "large.txt").write_text("x" * 100, encoding="utf-8")
        self.tool = SafeFileReadTool(self.root, max_bytes=50)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_small_text_file(self):
        self.assertIn("hello", self.tool.run(path="code.py"))

    def test_blocks_parent_escape(self):
        with self.assertRaises(ToolError):
            self.tool.run(path="../secret.txt")

    def test_blocks_absolute_path(self):
        with self.assertRaises(ToolError):
            self.tool.run(path=str((self.root / "code.py").resolve()))

    def test_blocks_unknown_extension(self):
        with self.assertRaises(ToolError):
            self.tool.run(path="blob.bin")

    def test_blocks_large_file(self):
        with self.assertRaises(ToolError):
            self.tool.run(path="large.txt")


class SafeFileSearchSymlinkCycleTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_directory_symlink_cycle_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            sub = root / "sub"
            sub.mkdir(parents=True)
            (sub / "target.txt").write_text("x", encoding="utf-8")
            try:
                (sub / "back").symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink not permitted")
            output = SafeFileSearchTool(root).run(query="target")
            self.assertEqual(output.count("target.txt"), 1)


class SafeFileSearchBoundedScanTests(unittest.TestCase):
    def test_scan_limit_stops_unbounded_walk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(10):
                (root / f"f{i}.txt").write_text("x", encoding="utf-8")
            output = SafeFileSearchTool(root, max_scanned_files=2).run(query="no-match")
            self.assertIn("검색을 중단", output)


class SafeProjectContextToolTests(unittest.TestCase):
    def setUp(self):
        from jarvis.tools import SafeProjectContextTool

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "project"
        (self.root / "jarvis").mkdir(parents=True)
        (self.root / "jarvis" / "router.py").write_text(
            "def route_request(text):\n    # cloud fallback routing\n    return text\n",
            encoding="utf-8",
        )
        (self.root / "jarvis" / "storage.py").write_text(
            "class Storage:\n    pass  # sqlite persistence\n",
            encoding="utf-8",
        )
        (self.root / "blob.bin").write_bytes(b"cloud fallback secret")
        self.tool = SafeProjectContextTool(
            self.root,
            max_files=20,
            max_total_bytes=10_000,
            max_context_chars=2000,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_finds_relevant_code_with_line_numbers(self):
        result = self.tool.search("cloud fallback route")
        self.assertGreaterEqual(result.matched_files, 1)
        self.assertIn("router.py", result.text)
        self.assertIn("cloud fallback", result.text)
        self.assertRegex(result.text, r"\s+2:")

    def test_binary_extension_is_not_read(self):
        result = self.tool.search("secret")
        self.assertNotIn("blob.bin", result.text)

    def test_generic_query_asks_for_more_specific_terms(self):
        result = self.tool.search("파일 코드 프로젝트")
        self.assertEqual(result.matched_files, 0)
        self.assertIn("핵심 단어", result.text)

    def test_context_limit_marks_truncation(self):
        for i in range(8):
            (self.root / f"m{i}.py").write_text(
                ("target_word = 1\n" * 100), encoding="utf-8"
            )
        from jarvis.tools import SafeProjectContextTool
        tool = SafeProjectContextTool(
            self.root,
            max_files=50,
            max_total_bytes=100_000,
            max_context_chars=500,
            max_results=8,
        )
        result = tool.search("target_word")
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 1100)


def _make_sensitive_context_root(root: Path):
    (root / "credentials.json").write_text(
        '{"token":"VERYSECRET", "router":"routing"}', encoding="utf-8"
    )
    (root / "normal.py").write_text("router = 'routing'", encoding="utf-8")


class AutomaticContextSecretSkipTests(unittest.TestCase):
    def test_sensitive_filename_is_not_automatically_retrieved(self):
        from jarvis.tools import SafeProjectContextTool
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_sensitive_context_root(root)
            output = SafeProjectContextTool(root).run(query="router routing")
            self.assertIn("normal.py", output)
            self.assertNotIn("credentials.json", output)
            self.assertNotIn("VERYSECRET", output)

class SafeProjectTreeToolTests(unittest.TestCase):
    def test_tree_is_bounded_and_skips_hidden_data(self):
        from jarvis.tools import SafeProjectTreeTool
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            (root / "src" / "nested").mkdir(parents=True)
            (root / "src" / "main.py").write_text("x", encoding="utf-8")
            (root / "src" / "nested" / "deep.py").write_text("x", encoding="utf-8")
            (root / ".env").write_text("SECRET=x", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "jarvis.db").write_text("x", encoding="utf-8")
            output = SafeProjectTreeTool(root, max_depth=3).run(depth=2)
            self.assertIn("src/", output)
            self.assertIn("main.py", output)
            self.assertNotIn("deep.py", output)
            self.assertNotIn(".env", output)
            self.assertNotIn("jarvis.db", output)

    def test_tree_rejects_non_integer_depth(self):
        from jarvis.tools import SafeProjectTreeTool
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ToolError):
                SafeProjectTreeTool(Path(td)).run(depth="abc")

class ProjectContextCacheTests(unittest.TestCase):
    def test_second_search_reuses_unchanged_content_and_change_invalidates(self):
        from jarvis.tools import SafeProjectContextTool
        import time
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "router.py"
            path.write_text("router alpha", encoding="utf-8")
            tool = SafeProjectContextTool(root, max_files=10, max_total_bytes=10000)
            tool.search("router")
            misses = tool.cache_misses
            tool.search("router")
            self.assertGreater(tool.cache_hits, 0)
            self.assertEqual(tool.cache_misses, misses)
            # Size change guarantees invalidation even on coarse-mtime filesystems.
            path.write_text("router alpha changed and longer", encoding="utf-8")
            tool.search("router")
            self.assertGreater(tool.cache_misses, misses)
