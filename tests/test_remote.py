"""노트북 → 데스크톱 ALEX 연결 (ADR-052). ssh 와 데스크톱은 가짜로 띄운다 — 인터넷·SSH 서버 없이 돈다.

지키는 것:
- ALEX 는 여전히 127.0.0.1 에서만 듣는다. 이 모듈은 SSH 터널을 여는 것뿐이다.
- ssh 명령줄은 고정된 인자 목록이고, 설정값이 옵션으로 읽히지 않는다(-oProxyCommand 주입 차단).
- 노트북 쪽 포트는 127.0.0.1 로 묶는다 — 카페 와이파이의 다른 사람이 못 붙는다.
- 터널 너머가 정말 ALEX 인지 /health 로 확인하고, 아니면 브라우저를 열지 않는다.
- 안 될 때는 ssh 의 영어 오류를 무엇을 하면 되는지로 바꿔 말한다.
"""
from dataclasses import replace
import http.server
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import launcher, remote  # noqa: E402
from jarvis.config import LOCAL_FIELDS, Settings  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeAlex:
    """터널이 열린 뒤 노트북 포트에서 보이는 '데스크톱 ALEX'."""

    def __init__(self, port: int, health: dict | None, status: int = 200):
        body = json.dumps(health).encode() if health is not None else b"not alex"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeProcess:
    def __init__(self, exit_code=None):
        self.returncode = exit_code
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1


class RemoteTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.port = free_port()
        self.settings = replace(Settings(), remote_host="desktop-5080", remote_user="alexuser",
                                remote_local_port=self.port)
        self.lines: list[str] = []
        self.opened: list[str] = []
        self.servers: list[FakeAlex] = []
        self.commands: list[list[str]] = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        self._tmp.cleanup()

    def out(self, text):
        self.lines.append(text)

    @property
    def said(self) -> str:
        return "\n".join(self.lines)

    def alex_health(self, **overrides):
        data = {"app": "jarvis", "instance": "x", "local_only": True, "code": launcher.code_fingerprint()}
        data.update(overrides)
        return data

    def popen_opening_tunnel(self, health, status=200):
        def popen(command):
            self.commands.append(command)
            self.servers.append(FakeAlex(self.port, health, status))
            return FakeProcess()
        return popen

    def popen_failing(self, log_text, code=255):
        def popen(command):
            self.commands.append(command)
            Path(command[command.index("-E") + 1]).write_text(log_text, encoding="utf-8")
            return FakeProcess(exit_code=code)
        return popen

    def run_connect(self, popen, which=lambda name: r"C:\Windows\System32\OpenSSH\ssh.exe", **kw):
        return remote.connect(self.settings, open_browser=self.opened.append, popen=popen, which=which,
                              log_dir=self.dir, wait_limit=5, health_wait=0.5, out=self.out, **kw)


class TargetValidationTests(unittest.TestCase):
    def test_accepts_tailscale_names_ips_and_windows_users(self):
        for host in ("desktop", "desktop-5080", "desktop-5080.tail1234.ts.net", "100.101.102.103"):
            remote.validate_target(host, "alexuser")
        for user in ("alexuser", "alexuser.lee", "kim-cs", "김철수"):
            remote.validate_target("desktop", user)

    def test_rejects_values_that_would_become_ssh_options_or_commands(self):
        for host in ("-oProxyCommand=calc", "desk top", "host;calc", "a@b", "", "-desktop", "host/../x"):
            with self.assertRaises(remote.RemoteError, msg=host):
                remote.validate_target(host, "alexuser")
        for user in ("-oProxyCommand=calc", "a b", "user@host", "", "x;y", "-u"):
            with self.assertRaises(remote.RemoteError, msg=user):
                remote.validate_target("desktop", user)


class SshCommandTests(unittest.TestCase):
    def test_fixed_argument_list(self):
        command = remote.ssh_command("ssh", "desktop", "alexuser", 8766, 8765, None)
        self.assertEqual(command[0:2], ["ssh", "-N"])
        self.assertEqual(command[-1], "alexuser@desktop")
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("StrictHostKeyChecking=accept-new", command)

    def test_the_laptop_side_is_bound_to_loopback_only(self):
        command = remote.ssh_command("ssh", "desktop", "alexuser", 8766, 8765, None)
        self.assertEqual(command[command.index("-L") + 1], "127.0.0.1:8766:127.0.0.1:8765")

    def test_key_is_used_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "k"
            self.assertNotIn("-i", remote.ssh_command("ssh", "desktop", "alexuser", 1, 2, key))
            key.write_text("x")
            command = remote.ssh_command("ssh", "desktop", "alexuser", 1, 2, key)
            self.assertEqual(command[command.index("-i") + 1], str(key))
            self.assertIn("IdentitiesOnly=yes", command)

    def test_bad_target_never_reaches_the_command_line(self):
        with self.assertRaises(remote.RemoteError):
            remote.ssh_command("ssh", "-oProxyCommand=calc", "alexuser", 1, 2, None)


class DiagnoseTests(unittest.TestCase):
    def test_each_ssh_failure_becomes_advice(self):
        cases = {
            "ssh: Could not resolve hostname desktop: No such host is known.": "이름을 찾지 못했습니다",
            "ssh: connect to host 100.1.2.3 port 22: Connection timed out": "닿지 않습니다",
            "ssh: connect to host desktop port 22: Connection refused": "SSH 서버가 받지 않습니다",
            "alexuser@desktop: Permission denied (publickey,password).": "로그인에 실패했습니다",
            "Host key verification failed.": "신원이 바뀌었습니다",
            "bind [127.0.0.1]:8766: Address already in use": "8766 번 포트",
        }
        for log, expected in cases.items():
            self.assertIn(expected, remote.diagnose(log, 255, 8766), log)

    def test_unknown_failure_shows_the_last_ssh_line(self):
        message = remote.diagnose("line one\nsomething odd happened\n", 255, 8766)
        self.assertIn("something odd happened", message)
        self.assertIn("255", message)

    def test_outside_home_the_advice_says_remote_power_on_does_not_work(self):
        self.assertIn("집 밖에서는", remote.diagnose("Connection timed out", 255, 8766))


class ConnectTests(RemoteTestBase):
    def test_not_configured_explains_setup(self):
        self.settings = replace(self.settings, remote_host="", remote_user="")
        self.assertEqual(self.run_connect(self.popen_opening_tunnel(self.alex_health())), 2)
        self.assertIn("connect_desktop.cmd setup", self.said)
        self.assertEqual(self.commands, [])

    def test_bad_configured_host_is_refused_before_ssh(self):
        self.settings = replace(self.settings, remote_host="-oProxyCommand=calc")
        self.assertEqual(self.run_connect(self.popen_opening_tunnel(self.alex_health())), 2)
        self.assertEqual(self.commands, [])

    def test_missing_ssh_is_explained(self):
        self.assertEqual(self.run_connect(self.popen_opening_tunnel(self.alex_health()), which=lambda n: None), 2)
        self.assertIn("OpenSSH 클라이언트", self.said)

    def test_success_opens_the_browser_on_the_tunnel(self):
        self.assertEqual(self.run_connect(self.popen_opening_tunnel(self.alex_health())), 0)
        self.assertEqual(self.opened, [f"http://127.0.0.1:{self.port}"])
        self.assertIn("코드가 같습니다", self.said)

    def test_different_code_on_the_desktop_is_reported(self):
        self.run_connect(self.popen_opening_tunnel(self.alex_health(code="0000000000000000")))
        self.assertIn("코드가 다릅니다", self.said)

    def test_desktop_from_before_fingerprints_is_called_old(self):
        health = self.alex_health()
        del health["code"]
        self.run_connect(self.popen_opening_tunnel(health))
        self.assertIn("옛 버전", self.said)

    def test_ssh_failure_is_translated_and_no_browser_opens(self):
        rc = self.run_connect(self.popen_failing("ssh: connect to host desktop port 22: Connection refused"))
        self.assertEqual(rc, 1)
        self.assertIn("SSH 서버가 받지 않습니다", self.said)
        self.assertEqual(self.opened, [])

    def test_tunnel_up_but_no_alex_behind_it(self):
        processes = []

        def popen(command):
            self.servers.append(FakeAlex(self.port, None, status=502))
            processes.append(FakeProcess())
            return processes[-1]

        self.assertEqual(self.run_connect(popen), 1)
        self.assertIn("ALEX 가 응답하지 않습니다", self.said)
        self.assertTrue(processes[0].terminated)
        self.assertEqual(self.opened, [])

    def test_something_else_answering_is_not_trusted(self):
        self.run_connect(self.popen_opening_tunnel({"app": "not-jarvis"}))
        self.assertEqual(self.opened, [])

    def test_desktop_not_in_local_only_mode_is_refused(self):
        self.run_connect(self.popen_opening_tunnel(self.alex_health(local_only=False)))
        self.assertEqual(self.opened, [])

    def test_an_existing_tunnel_is_reused(self):
        self.servers.append(FakeAlex(self.port, self.alex_health()))
        self.assertEqual(self.run_connect(self.popen_opening_tunnel(self.alex_health())), 0)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.opened, [f"http://127.0.0.1:{self.port}"])

    def test_a_busy_port_that_is_not_alex_is_reported(self):
        self.servers.append(FakeAlex(self.port, None, status=404))
        self.assertEqual(self.run_connect(self.popen_opening_tunnel(self.alex_health())), 1)
        self.assertIn(f"{self.port} 번 포트", self.said)
        self.assertEqual(self.commands, [])

    def test_the_ssh_log_goes_to_a_file_not_the_prompt(self):
        self.run_connect(self.popen_opening_tunnel(self.alex_health()))
        command = self.commands[0]
        self.assertEqual(Path(command[command.index("-E") + 1]), self.dir / remote.LOG_NAME)


class ModelNodeParseTests(unittest.TestCase):
    """모델 노드(ADR-124): 다른 기기의 모델 서버를 이 기기 127.0.0.1 포트에 잇는 설정."""

    def test_several_nodes_with_their_own_local_ports(self):
        nodes = remote.parse_model_nodes("desktop=alexuser@desktop-5080:8080>8080,8081>8081; spark=me@spark-1:18000>8000")
        self.assertEqual([(n.name, n.user, n.host, n.forwards) for n in nodes],
                         [("desktop", "alexuser", "desktop-5080", ((8080, 8080), (8081, 8081))),
                          ("spark", "me", "spark-1", ((18000, 8000),))])

    def test_bad_values_never_reach_ssh(self):
        for text in ("desktop=alexuser@desktop-5080", "desktop=alexuser@-oProxyCommand=calc:8080>8080",
                     "Desktop=alexuser@desktop-5080:8080>8080", "a=alexuser@x:8080>8080; a=alexuser@y:9000>9000",
                     "a=alexuser@x:8080>8080; b=alexuser@y:8080>9000", "a=alexuser@x:8765>8765", "a=alexuser@x:80>80",
                     "a=alexuser@x:8080>abc", "a=user name@x:8080>8080"):
            with self.subTest(text=text), self.assertRaises(remote.RemoteError):
                remote.parse_model_nodes(text)

    def test_empty_setting_means_the_saved_desktop_with_strata_and_embedding_ports(self):
        settings = replace(Settings(), remote_host="desktop-5080", remote_user="alexuser")
        node, = remote.model_nodes(settings)
        self.assertEqual((node.name, node.host, node.forwards), ("desktop", "desktop-5080", ((8080, 8080), (8081, 8081))))
        self.assertEqual(remote.model_nodes(Settings()), [])

    def test_the_setting_is_checked_when_settings_load(self):
        from jarvis.config import ConfigError, validate
        self.assertEqual(LOCAL_FIELDS["model_nodes"], str)
        with self.assertRaises(ConfigError):
            validate(replace(Settings(), model_nodes="desktop=alexuser@-oProxyCommand=calc:8080>8080"))
        validate(replace(Settings(), model_nodes="desktop=alexuser@desktop-5080:8080>8080"))

    def test_every_forward_is_bound_to_loopback_in_one_ssh(self):
        command = remote.tunnel_command("ssh", "desktop-5080", "alexuser", [(8080, 8080), (18000, 8000)], None)
        forwards = [command[i + 1] for i, part in enumerate(command) if part == "-L"]
        self.assertEqual(forwards, ["127.0.0.1:8080:127.0.0.1:8080", "127.0.0.1:18000:127.0.0.1:8000"])
        self.assertEqual(command[-1], "alexuser@desktop-5080")


class WorkNodeParseTests(unittest.TestCase):
    """작업 노드(ADR-125): 이 ALEX 가 코드 세션을 돌릴 다른 기기 — 윈도우·macOS·리눅스."""

    def test_windows_and_posix_nodes(self):
        laptop, mac = remote.parse_work_nodes(
            r"laptop=alexuser@laptop-1:C:\Users\alexuser\local ai project\ALEX-main; mac=me@mac-studio:/Users/me/ALEX")
        self.assertEqual((laptop.name, laptop.user, laptop.host, laptop.windows), ("laptop", "alexuser", "laptop-1", True))
        self.assertEqual((mac.alex, mac.windows), ("/Users/me/ALEX", False))

    def test_bad_values_never_reach_the_remote_command_line(self):
        for text in (r"c=alexuser@x:C:\ALEX", r"laptop=alexuser@-oProxyCommand=calc:C:\ALEX", "laptop=alexuser@x:ALEX",
                     'laptop=alexuser@x:C:\\ALEX" & calc', "laptop=alexuser@x:/a;rm -rf ~", "laptop=alexuser@x:/a`id`",
                     r"laptop=alexuser@x:C:\a; laptop=alexuser@y:C:\b", "laptop=alexuser@x:/a$HOME"):
            with self.subTest(text=text), self.assertRaises(remote.RemoteError):
                remote.parse_work_nodes(text)

    def test_the_worker_command_for_each_system(self):
        laptop, mac = remote.parse_work_nodes(r"laptop=alexuser@laptop-1:C:\local ai\ALEX-main; mac=me@mac-studio:/Users/me/ALEX")
        command = remote.node_command("ssh", laptop, None)
        self.assertEqual(command[:2], ["ssh", "-T"])
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-2:], ["alexuser@laptop-1", r'cd /d "C:\local ai\ALEX-main" && py -3.11 -X utf8 -m jarvis.harness.worknode'])
        self.assertEqual(remote.node_command("ssh", mac, None)[-1],
                         "cd '/Users/me/ALEX' && exec python3 -X utf8 -m jarvis.harness.worknode")

    def test_the_setting_is_checked_when_settings_load(self):
        from jarvis.config import ConfigError, validate
        self.assertEqual(LOCAL_FIELDS["work_nodes"], str)
        with self.assertRaises(ConfigError):
            validate(replace(Settings(), work_nodes='laptop=alexuser@x:C:\\a" & calc'))
        validate(replace(Settings(), work_nodes=r"laptop=alexuser@x:C:\ALEX"))


class ModelsTests(RemoteTestBase):
    def setUp(self):
        super().setUp()
        self.strata, self.embed = free_port(), free_port()
        # 그 기기 쪽은 ALEX 의 기본 자리(Strata 8080·임베딩 8081), 이 기기 쪽은 빈 포트
        self.settings = replace(self.settings, model_nodes=f"desktop=alexuser@desktop-5080:{self.strata}>8080,{self.embed}>8081")
        self.started: list[dict] = []
        self.processes: list[FakeProcess] = []
        patcher = patch.object(remote, "WEB", f"http://127.0.0.1:{free_port()}")
        patcher.start()
        self.addCleanup(patcher.stop)

    def popen_models(self, strata_health):
        def popen(command):
            self.commands.append(command)
            self.servers.append(FakeAlex(self.strata, strata_health))
            self.servers.append(FakeAlex(self.embed, {}))
            process = FakeProcess()
            self.processes.append(process)
            return process
        return popen

    def start_alex(self, environment):
        self.started.append(environment)
        for process in self.processes:  # 사용자가 창을 닫은 것처럼 연결을 끝낸다
            process.returncode = 0

    def run_models(self, popen, which=lambda name: "ssh"):
        return remote.models(self.settings, popen=popen, which=which, start_alex=self.start_alex,
                             log_dir=self.dir, wait_limit=5, health_wait=0.5, out=self.out)

    def test_desktop_model_starts_this_alex_on_strata_without_touching_its_settings_file(self):
        code = self.run_models(self.popen_models({"service": "strata", "model": "qwen3.8-flash-next-iq3_s"}))
        self.assertEqual(code, 0)
        # 이 기기 쪽 주소를 환경변수로 넘긴다 — jarvis.local.json 의 strata_host 등을 손으로 맞추지 않는다(2026-10-30 노트북)
        self.assertEqual(self.started, [{
            "JARVIS_LOCAL_BACKEND": "strata", "JARVIS_STRATA_HOST": f"http://127.0.0.1:{self.strata}",
            "JARVIS_MODEL_NODE": "desktop(alexuser@desktop-5080)",
            "JARVIS_EMBEDDING_ENABLED": "1", "JARVIS_EMBEDDING_HOST": f"http://127.0.0.1:{self.embed}"}])
        forwards = [self.commands[0][i + 1] for i, part in enumerate(self.commands[0]) if part == "-L"]
        self.assertEqual(forwards, [f"127.0.0.1:{self.strata}:127.0.0.1:8080", f"127.0.0.1:{self.embed}:127.0.0.1:8081"])
        self.assertIn("qwen3.8-flash-next-iq3_s", self.said)

    def test_the_default_desktop_link_moves_off_a_busy_port_by_itself(self):
        node = remote.parse_model_nodes(f"desktop=alexuser@desktop-5080:{self.strata}>8080,{self.embed}>8081")[0]
        self.servers.append(FakeAlex(self.strata, {"service": "strata"}))   # 이 기기의 같은 번호를 이미 무언가가 쓴다
        moved = remote.with_free_ports(node)
        self.assertNotEqual(moved.forwards[0][0], self.strata)
        self.assertEqual(moved.forwards[0][1], 8080)
        self.assertEqual(moved.forwards[1], (self.embed, 8081))       # 비어 있는 포트는 그대로
        self.assertFalse(remote.port_open(moved.forwards[0][0]))

    def test_no_model_server_behind_the_tunnel_starts_nothing(self):
        self.assertEqual(self.run_models(self.popen_models({"app": "jarvis"})), 1)
        self.assertEqual(self.started, [])
        self.assertTrue(all(p.terminated for p in self.processes))
        self.assertIn("모델 서버(Strata)가 응답하지 않습니다", self.said)

    def test_a_running_alex_here_must_be_closed_first(self):
        with patch.object(remote, "WEB", f"http://127.0.0.1:{self.port}"):
            self.servers.append(FakeAlex(self.port, self.alex_health()))
            self.assertEqual(self.run_models(self.popen_models({"service": "strata"})), 2)
        self.assertEqual(self.commands, [])
        self.assertIn("이미 켜져 있습니다", self.said)

    def test_a_local_model_server_on_the_same_port_is_reported(self):
        self.servers.append(FakeAlex(self.strata, {"service": "strata"}))
        self.assertEqual(self.run_models(self.popen_models({"service": "strata"})), 1)
        self.assertEqual(self.commands, [])
        self.assertIn(f"{self.strata}", self.said)

    def test_ssh_failure_is_translated(self):
        self.assertEqual(self.run_models(self.popen_failing("ssh: connect to host desktop-5080 port 22: Connection refused")), 1)
        self.assertIn("SSH 서버가 받지 않습니다", self.said)
        self.assertEqual(self.started, [])

    def test_not_configured_explains_setup(self):
        self.settings = replace(self.settings, remote_host="", remote_user="", model_nodes="")
        self.assertEqual(self.run_models(self.popen_models({"service": "strata"})), 2)
        self.assertIn("connect_desktop.cmd setup", self.said)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.config = self.dir / "jarvis.local.json"
        self.config.write_text(json.dumps({"general_model": "gpt-oss:20b", "web_enabled": True}), encoding="utf-8")
        self.key = self.dir / ".ssh" / remote.KEY_NAME
        self.calls = []
        self.lines = []

    def tearDown(self):
        self._tmp.cleanup()

    def fake_keygen(self, command, check):
        self.calls.append(command)
        path = Path(command[command.index("-f") + 1])
        path.write_text("PRIVATE", encoding="utf-8")
        path.with_name(path.name + ".pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITEST alex-laptop\n")

    def run_setup(self, host="100.101.102.103", user="alexuser"):
        with patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(self.config)}):
            return remote.setup(host, user, run=self.fake_keygen, which=lambda n: "ssh-keygen",
                                config_path=self.config, key=self.key, out=self.lines.append)

    def test_makes_a_key_saves_the_target_and_prints_the_desktop_command(self):
        self.assertEqual(self.run_setup(), 0)
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual((saved["remote_host"], saved["remote_user"]), ("100.101.102.103", "alexuser"))
        said = "\n".join(self.lines)
        self.assertIn('-PublicKey "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITEST alex-laptop"', said)
        self.assertIn("remote_setup_desktop.ps1", said)

    def test_the_key_comment_is_ascii(self):
        """컴퓨터 이름이 한글이면 데스크톱 키 파일(ASCII)에서 깨진다."""
        self.run_setup()
        command = self.calls[0]
        self.assertEqual(command[command.index("-C") + 1], "alex-laptop")
        self.assertEqual(command[command.index("-N") + 1], "")

    def test_other_settings_survive_and_a_backup_is_left(self):
        self.run_setup()
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["general_model"], "gpt-oss:20b")
        self.assertTrue(list(self.dir.glob("jarvis.local.json.bak-remote-*")))

    def test_an_existing_key_is_reused(self):
        self.run_setup()
        self.run_setup()
        self.assertEqual(len(self.calls), 1)

    def test_bad_target_changes_nothing(self):
        before = self.config.read_text(encoding="utf-8")
        self.assertEqual(self.run_setup(host="-oProxyCommand=calc"), 2)
        self.assertEqual(self.config.read_text(encoding="utf-8"), before)
        self.assertEqual(self.calls, [])


class BackgroundLaunchTests(unittest.TestCase):
    def test_failure_is_logged_not_shown(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(launcher, "launch", side_effect=RuntimeError("ollama down")):
            self.assertEqual(launcher.run_background(Path(tmp)), 1)
            self.assertIn("ollama down", (Path(tmp) / "autostart.log").read_text(encoding="utf-8"))

    def test_success_opens_no_browser(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(launcher, "launch", return_value="http://127.0.0.1:8765") as launch, \
             patch.object(launcher.webbrowser, "open") as browser:
            self.assertEqual(launcher.run_background(Path(tmp)), 0)
            launch.assert_called_once()
            self.assertFalse(launch.call_args.args[0], "모델 다운로드는 하지 않는다")
            browser.assert_not_called()


class ScriptFileTests(unittest.TestCase):
    def test_windows_entry_files_are_ascii(self):
        """.cmd/.vbs 는 cp949 로 읽힌다. 한글이 들어가면 깨진다."""
        for name in ("connect_desktop.cmd", "start_alex_background.vbs"):
            data = (ROOT / name).read_bytes()
            self.assertTrue(all(b < 128 for b in data), name)

    def test_desktop_script_has_a_bom_for_powershell_5(self):
        self.assertTrue((ROOT / "scripts" / "remote_setup_desktop.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_desktop_script_limits_ssh_to_tailscale_and_uses_sids(self):
        text = (ROOT / "scripts" / "remote_setup_desktop.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"100.64.0.0/10", "fd7a:115c:a1e0::/48"', text)
        self.assertIn("*S-1-5-32-544:F", text)
        self.assertIn("*S-1-5-18:F", text)
        self.assertIn("-Encoding ASCII", text)

    def test_desktop_script_does_not_start_alex_as_administrator(self):
        text = (ROOT / "scripts" / "remote_setup_desktop.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("Start-Process", text)

    def test_remote_settings_survive_a_web_settings_save(self):
        """웹 화면의 설정 저장은 LOCAL_FIELDS 전체를 다시 쓴다. 여기 없으면 원격 설정이 지워진다."""
        for key in ("remote_host", "remote_user", "remote_local_port", "remote_alex_port"):
            self.assertIn(key, LOCAL_FIELDS)


if __name__ == "__main__":
    unittest.main()
