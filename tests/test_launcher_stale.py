"""런처가 이미 떠 있는 **옛 코드** 서버를 조용히 재사용하지 않는지 (2026-09-25).

런처는 같은 프로젝트의 ALEX 가 떠 있으면 그 서버를 그대로 쓴다(두 개 띄우지 않으려고).
그런데 그 뒤에 코드가 바뀌었는지는 보지 않았다. 2026-09-25 에 웹 읽기를 넣고 런처를 다시
켰더니 07:56 부터 떠 있던 서버가 계속 응답해서 `/status` 에 새 기능이 없었다 — 사용자는
업데이트한 줄 알고 옛 버전을 쓰게 된다. ADR-049(조용히 덮어쓰이는 것 금지)와 같은 종류다.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import launcher
from jarvis.config import load_settings


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pkg = Path(self._tmp.name)
        (self.pkg / "core.py").write_text("x = 1\n", encoding="utf-8")
        (self.pkg / "static").mkdir()
        (self.pkg / "static" / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_same_content_same_fingerprint(self):
        self.assertEqual(launcher.code_fingerprint(self.pkg), launcher.code_fingerprint(self.pkg))

    def test_python_change_changes_fingerprint(self):
        before = launcher.code_fingerprint(self.pkg)
        (self.pkg / "core.py").write_text("x = 2\n", encoding="utf-8")
        self.assertNotEqual(before, launcher.code_fingerprint(self.pkg))

    def test_static_ui_change_changes_fingerprint(self):
        before = launcher.code_fingerprint(self.pkg)
        (self.pkg / "static" / "index.html").write_text("<p>new</p>", encoding="utf-8")
        self.assertNotEqual(before, launcher.code_fingerprint(self.pkg))

    def test_touching_without_changing_content_does_not_matter(self):
        """압축 해제·복사는 수정 시각만 바꾼다. 그걸로 옛 코드라고 하면 안 된다."""
        before = launcher.code_fingerprint(self.pkg)
        os.utime(self.pkg / "core.py", (1, 1))
        self.assertEqual(before, launcher.code_fingerprint(self.pkg))

    def test_bytecode_cache_is_ignored(self):
        before = launcher.code_fingerprint(self.pkg)
        (self.pkg / "__pycache__").mkdir()
        (self.pkg / "__pycache__" / "core.cpython-311.py").write_text("junk", encoding="utf-8")
        self.assertEqual(before, launcher.code_fingerprint(self.pkg))

    def test_the_running_web_module_reports_the_code_it_loaded(self):
        from jarvis import web
        self.assertEqual(web.LOADED_CODE, launcher.code_fingerprint())


class ExistingServerTests(unittest.TestCase):
    def launch_against(self, health):
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(launcher.os, "chdir"), \
             patch.object(launcher, "get_json", return_value=health):
            return launcher.launch()

    def health(self, **overrides):
        with patch.dict(os.environ, {"JARVIS_USE_OLLAMA": "1", "JARVIS_LOCAL_ONLY": "1"}):
            instance = launcher.instance_id(load_settings().data_dir)
        data = {"app": "jarvis", "instance": instance, "local_only": True,
                "code": launcher.code_fingerprint()}
        data.update(overrides)
        return data

    def test_current_server_is_reused(self):
        self.assertEqual(self.launch_against(self.health()), launcher.WEB)

    def test_server_running_older_code_is_not_silently_reused(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.launch_against(self.health(code="0000000000000000"))
        self.assertIn("앱 종료", str(ctx.exception))

    def test_server_from_before_this_check_counts_as_old(self):
        health = self.health()
        del health["code"]
        with self.assertRaises(RuntimeError):
            self.launch_against(health)


if __name__ == "__main__":
    unittest.main()
