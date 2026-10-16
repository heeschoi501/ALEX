"""이 PC 의 ALEX 웹 포트를 바꿀 수 있는지 (2026-10-16).

노트북에서 STRATA 레이아웃 스튜디오가 8765 를 쓰고 있어 ALEX 가 켜지지 않았다(logs/autostart.log).
포트가 런처·웹 서버·종료 명령에 따로 박혀 있었다. 기본은 8765 그대로 둔다 — 데스크톱의 원격 연결
(remote_alex_port)과 Tailscale Serve 가 이 번호를 본다. jarvis.local.json 의 web_port 로만 바꾼다.
"""
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from jarvis import launcher, lifecycle
from jarvis.config import ConfigError, load_settings


class WebPortTests(unittest.TestCase):
    def settings_with(self, **local):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "local.json"
        path.write_text(json.dumps(local), encoding="utf-8")
        environment = {key: value for key, value in os.environ.items() if key != "JARVIS_WEB_PORT"}
        environment["JARVIS_CONFIG_FILE"] = str(path)
        with patch.dict(os.environ, environment, clear=True):
            return load_settings()

    def own_health(self, settings):
        return {"app": "jarvis", "instance": launcher.instance_id(settings.data_dir), "local_only": True,
                "code": launcher.code_fingerprint()}

    def test_default_stays_8765(self):
        self.assertEqual(launcher.web_url(self.settings_with()), launcher.WEB)

    def test_local_file_changes_the_port(self):
        self.assertEqual(launcher.web_url(self.settings_with(web_port=8767)), "http://127.0.0.1:8767")

    def test_desktop_tunnel_port_is_refused(self):
        with self.assertRaises(ConfigError):
            self.settings_with(web_port=8766)  # remote_local_port 기본값 — connect_desktop 창이 쓴다

    def test_privileged_port_is_refused(self):
        with self.assertRaises(ConfigError):
            self.settings_with(web_port=80)

    def test_launcher_checks_and_reuses_the_configured_port(self):
        settings = self.settings_with(web_port=8767)
        with patch.dict(os.environ, {}, clear=False), patch.object(launcher.os, "chdir"), \
                patch.object(launcher, "load_settings", return_value=settings), \
                patch.object(launcher, "get_json", return_value=self.own_health(settings)) as get_json:
            self.assertEqual(launcher.launch(report=lambda _text: None), "http://127.0.0.1:8767")
        get_json.assert_called_once_with("http://127.0.0.1:8767/health")

    def test_launcher_starts_the_web_server_on_the_configured_port(self):
        settings = self.settings_with(web_port=8767, local_backend="strata")
        checks = []

        def get_json(url, timeout=2):
            self.assertEqual(url, "http://127.0.0.1:8767/health")
            checks.append(url)
            if len(checks) == 1:
                raise OSError("web is off")
            return self.own_health(settings)

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False), \
                patch.object(launcher, "ROOT", Path(directory)), patch.object(launcher.os, "chdir"), \
                patch.object(launcher, "load_settings", return_value=settings), \
                patch.object(launcher, "get_json", side_effect=get_json), \
                patch.object(launcher, "ensure_strata"), \
                patch.object(launcher.subprocess, "Popen") as spawn:
            spawn.return_value.poll.return_value = None
            self.assertEqual(launcher.launch(report=lambda _text: None), "http://127.0.0.1:8767")
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index("--port") + 1], "8767")

    def test_other_program_on_the_port_names_the_port_and_the_setting(self):
        settings = self.settings_with(web_port=8767)
        busy = urllib.error.HTTPError("http://127.0.0.1:8767/health", 404, "Not Found", {}, None)
        with patch.dict(os.environ, {}, clear=False), patch.object(launcher.os, "chdir"), \
                patch.object(launcher, "load_settings", return_value=settings), \
                patch.object(launcher, "get_json", side_effect=busy), \
                self.assertRaises(RuntimeError) as ctx:
            launcher.launch(report=lambda _text: None)
        self.assertIn("8767", str(ctx.exception))
        self.assertIn("web_port", str(ctx.exception))

    def test_lifecycle_looks_at_the_configured_port(self):
        settings = self.settings_with(web_port=8767)
        with patch.object(lifecycle, "load_settings", return_value=settings), \
                patch.object(lifecycle.urllib.request, "urlopen", side_effect=OSError("off")) as urlopen:
            self.assertIsNone(lifecycle.health())
            self.assertFalse(lifecycle.stop_server())
        self.assertEqual({call.args[0] for call in urlopen.call_args_list}, {"http://127.0.0.1:8767/health"})


if __name__ == "__main__":
    unittest.main()
