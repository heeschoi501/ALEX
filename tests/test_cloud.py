"""어려운 일은 클라우드로(ADR-102, 2026-10-22). 실제 Claude Code·Codex 대신 가짜 에이전트(파이썬 스크립트)로 흐름을 본다.

사용자 결정: 둘 다·기본 Claude, 요청할 때만, 복사본에서 고치기 → ALEX 확인 → 승인해야 적용.
나누기: 이름을 말하면 그쪽, 아니면 기본, 한쪽이 한도·오류면 다른 쪽이 대신."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jarvis.app import handle_command
from jarvis.cloudagent import CloudAgents, copy_project, parse_request
from jarvis.core import CLOUD_HEADER, JarvisCore, SENSITIVE_HINTS
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.permissions import READ_ONLY_BLOCKED
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.storage import Storage
from jarvis.tools import SafeFileSearchTool
from jarvis.web import WebAppState
from jarvis.workspace import SafeWorkspaceEditor

FAKE = r'''
import json, pathlib, sys, time
mode, work, last = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
prompt = sys.stdin.buffer.read().decode("utf-8")
(work.parent / f"prompt-{mode}.txt").write_text(prompt, encoding="utf-8")
if mode == "limit":
    print("You've hit your usage limit. Try again later."); sys.exit(1)
if mode == "login":
    print(json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth session expired"})); sys.exit(1)
if mode == "slow":
    time.sleep(30)
if mode == "fix":
    calc = work / "calc.py"
    calc.write_text(calc.read_text(encoding="utf-8").replace("a - b", "a + b"), encoding="utf-8")
    (work / "notes.md").write_text("# 메모\n고쳤습니다\n", encoding="utf-8")
if mode == "wait-fix":
    time.sleep(1.5)
    calc = work / "calc.py"
    calc.write_text(calc.read_text(encoding="utf-8").replace("a - b", "a + b"), encoding="utf-8")
seen = sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())
last.write_text("1) calc.py 의 add 를 고쳤습니다\n본 파일: " + ", ".join(seen), encoding="utf-8")
'''


def agents(base: Path, claude: str, codex: str = "fix", timeout: float = 60) -> CloudAgents:
    script = base / "fake_agent.py"
    script.write_text(FAKE, encoding="utf-8")
    return CloudAgents("claude", timeout, commands={"claude": [sys.executable, str(script), claude],
                                                    "codex": [sys.executable, str(script), codex]})


def make_core(base: Path, project: Path) -> JarvisCore:
    storage = Storage(base / "alex.db")
    return JarvisCore(
        storage=storage, hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
        local_general=MockLLMProvider("general", "GENERAL"), local_coder=MockLLMProvider("coder", "CODER"),
        cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
        file_search_tool=SafeFileSearchTool(project),
        workspace_editor=SafeWorkspaceEditor(project, base / "backups", storage=storage))


class ParseTests(unittest.TestCase):
    def test_commands_and_spoken_requests(self):
        self.assertEqual(parse_request("/cloud 계산기 버그 고쳐 줘"), (None, "", "계산기 버그 고쳐 줘"))
        self.assertEqual(parse_request("/cloud codex @src 고쳐"), ("codex", "src", "고쳐"))
        self.assertEqual(parse_request("/cloud 클로드 정리해"), ("claude", "", "정리해"))
        self.assertEqual(parse_request("이거 클라우드로 넘겨줘")[0], None)
        self.assertEqual(parse_request("코덱스로 넘겨서 고쳐 줘")[0], "codex")
        self.assertEqual(parse_request("이 문제 클로드한테 맡겨줘")[0], "claude")
        self.assertEqual(parse_request("Claude Code로 보내 줘")[0], "claude")

    def test_questions_are_not_requests(self):
        for text in ("클라우드로 넘겨도 돼?", "클라우드로 넘기는 건 어떻게 해?", "/cloudy", "코덱스가 뭐야"):
            self.assertIsNone(parse_request(text), text)

    def test_order(self):
        cloud = CloudAgents("claude")
        self.assertEqual(cloud.order(None), ["claude", "codex"])
        self.assertEqual(cloud.order("codex"), ["codex", "claude"])
        self.assertEqual(CloudAgents("codex").order(None), ["codex", "claude"])


class CopyTests(unittest.TestCase):
    def test_records_secrets_and_big_files_stay_home(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "p", Path(temp) / "c"
            for path, text in {"main.py": "print(1)\n", "data/jarvis.db": "x", "logs/run.log": "x", ".env": "A=1",
                               "pkg/data/table.csv": "a,b\n", "config.py": "api_key = 'sk-abcdefghijklmnop'\n",
                               "__pycache__/m.pyc": "x", "pic.png": "x"}.items():
                (source / path).parent.mkdir(parents=True, exist_ok=True)
                (source / path).write_text(text, encoding="utf-8")
            (source / "big.txt").write_bytes(b"a" * 2_100_000)
            report = copy_project(source, target, [p for _l, p in SENSITIVE_HINTS])
            copied = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
            self.assertEqual(copied, ["main.py", "pkg/data/table.csv"])  # 맨 위 data 만 뺀다
            self.assertEqual(sorted(report.secret_files), [".env", "config.py"])
            self.assertEqual(report.files, 2)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        (self.project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.project / "secret.py").write_text("password = 'hunter22'\n", encoding="utf-8")
        self.core = make_core(self.base, self.project)
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp.cleanup()

    def test_limit_moves_to_the_other_agent_and_the_fix_waits_for_approval(self):
        self.core.cloud_agents = agents(self.base, "limit", "fix")
        reply = self.core.handle_cloud(self.sid, "/cloud 계산기 더하기가 틀려요")
        self.assertTrue(reply.startswith(f"{CLOUD_HEADER} — Codex"))
        self.assertIn("- 먼저 맡긴 쪽: Claude Code: 사용량 한도 → Codex 가 했습니다", reply)
        self.assertIn("비밀값이 보여 뺀 파일 1개: secret.py", reply)
        self.assertIn("✓ 파이썬 문법 1개 통과", reply)
        self.assertNotIn("secret.py", reply.split("### Codex 의 보고", 1)[1].split("###", 1)[0])  # 복사본에 없었다
        self.assertIn("return a - b", (self.project / "calc.py").read_text(encoding="utf-8"))  # 아직 원본 그대로
        proposal = reply.split("편집 제안 ID: ", 1)[1].split("\n", 1)[0]
        handle_command(self.core, self.sid, f"/apply-edit {proposal} --yes")
        self.assertIn("return a + b", (self.project / "calc.py").read_text(encoding="utf-8"))
        self.assertTrue((self.project / "notes.md").is_file())
        messages = self.core.storage.message_page(self.sid)["messages"]
        self.assertEqual(messages[0]["content"], "/cloud 계산기 더하기가 틀려요")
        self.assertTrue(messages[1]["content"].startswith(CLOUD_HEADER))
        log = [json.loads(line) for line in (self.base / "cloud_handoffs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((log[-1]["agent"], log[-1]["verified"], log[-1]["files"]), ("codex", "pass", 2))
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("alex-cloud-*/project/calc.py")))  # 복사본은 지운다

    def test_named_agent_goes_first(self):
        self.core.cloud_agents = agents(self.base, "limit", "fix")
        reply = self.core.handle_cloud(self.sid, "코덱스로 넘겨서 더하기 고쳐 줘")
        self.assertTrue(reply.startswith(f"{CLOUD_HEADER} — Codex"))
        self.assertNotIn("먼저 맡긴 쪽", reply)

    def test_original_changed_meanwhile_is_left_out(self):
        self.core.cloud_agents = agents(self.base, "wait-fix")
        task = self.core.cloud_prepare(self.sid, "/cloud 고쳐")
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("run", self.core.cloud_run(task)))
        thread.start()
        time.sleep(0.5)
        (self.project / "calc.py").write_text("def add(a, b):\n    return b - a\n", encoding="utf-8")  # 사용자가 그사이 고침
        thread.join(30)
        reply = self.core.cloud_finish(self.sid, task, holder["run"])
        self.assertIn("원본이 그사이 바뀌어 뺀 파일: calc.py", reply)
        self.assertNotIn("편집 제안 ID", reply)

    def test_nothing_is_sent_when_blocked(self):
        self.core.cloud_agents = agents(self.base, "fix")
        self.core.permission_mode = "read"
        self.assertEqual(self.core.handle_cloud(self.sid, "/cloud 고쳐"), READ_ONLY_BLOCKED)
        self.core.permission_mode = "ask"
        reply = self.core.handle_cloud(self.sid, "/cloud 이 키로 해 줘 api_key = abcdefg123")
        self.assertIn("비밀값으로 보이는 것", reply)
        self.assertFalse((self.base / "cloud_handoffs.jsonl").exists())
        self.assertIn("사용법", self.core.handle_cloud(self.core.storage.create_session(), "/cloud"))

    def test_recent_talk_goes_along_but_not_after_private_documents(self):
        self.core.cloud_agents = agents(self.base, "fix")
        self.core.storage.add_message(self.sid, "user", "add 가 빼기를 해요")
        self.core.storage.add_message(self.sid, "assistant", "확인해 보겠습니다")
        task = self.core.cloud_prepare(self.sid, "이거 클라우드로 넘겨줘")
        self.assertIn("사용자: add 가 빼기를 해요", task.prompt)
        self.core.cloud_finish(self.sid, task, None, "시험")
        self.core._mark_knowledge_session(self.sid)
        task = self.core.cloud_prepare(self.sid, "이거 클라우드로 넘겨줘")
        self.assertNotIn("add 가 빼기를 해요", task.prompt)
        self.core.cloud_finish(self.sid, task, None, "시험")

    def test_expired_login_says_how_to_fix_it(self):
        self.core.cloud_agents = agents(self.base, "login", "fix")
        reply = self.core.handle_cloud(self.sid, "/cloud 고쳐")
        self.assertIn("Claude Code: 로그인 필요 — Windows 터미널에서 claude 를 열고 /login → Codex 가 했습니다", reply)

    def test_failure_on_both_sides_is_reported(self):
        self.core.cloud_agents = agents(self.base, "limit", "limit")
        reply = self.core.handle_cloud(self.sid, "/cloud 고쳐")
        self.assertIn("결과 없음", reply)
        self.assertIn("Codex: 사용량 한도", reply)


class DefaultTests(unittest.TestCase):
    def test_default_is_kept_in_the_local_config(self):
        from unittest import mock
        from jarvis.app import cloud_default
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config = base / "jarvis.local.json"
            config.write_text(json.dumps({"strata_num_ctx": 196608}), encoding="utf-8")
            core = make_core(base, base)
            with mock.patch("jarvis.app.local_config_path", return_value=config):
                self.assertIn("클라우드 기본: Claude Code", cloud_default(core, ""))
                self.assertIn("Codex 로 바꿨습니다", cloud_default(core, "codex"))
                self.assertIn("사용법", cloud_default(core, "gemini"))
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"strata_num_ctx": 196608, "cloud_agent": "codex"})
            self.assertEqual(core.cloud_agents.order(None)[0], "codex")
        self.assertIsNone(parse_request("/cloud-default codex"))


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        project = self.base / "project"
        project.mkdir()
        (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        core = make_core(self.base, project)
        self.state = WebAppState(core, core.get_or_create_session(False), token="t")
        self.viewer = self.state.owner_viewer()

    def tearDown(self):
        self.state.shutting_down = True
        self.temp.cleanup()

    def wait(self, job_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state.jobs[job_id]["status"] not in ("queued", "running"):
                for thread in threading.enumerate():
                    if thread.name == f"alex-job-{job_id[:6]}":
                        thread.join(10)
                return self.state.jobs[job_id]
            time.sleep(0.05)
        raise AssertionError(self.state.jobs[job_id])

    def test_alex_is_free_while_the_cloud_works(self):
        self.state.core.cloud_agents = agents(self.base, "wait-fix")
        job = self.state.start_job(self.viewer, self.state.session_id, "/cloud 고쳐", None)
        ticket = next(t for t in self.state.queue if t.request_id == job["id"])
        deadline = time.monotonic() + 10
        while not ticket.external and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ticket.external)
        self.assertTrue(self.state.lock.acquire(timeout=1))  # 클라우드가 일하는 동안 모델 잠금은 비어 있다
        self.state.lock.release()
        self.assertEqual(self.state.queue_info(self.viewer)["requests"][0]["status"], "running")
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done")
        last = self.state.core.storage.message_page(self.state.session_id)["messages"][-1]["content"]
        self.assertTrue(last.startswith(CLOUD_HEADER))
        self.assertIn("편집 제안 ID", last)

    def test_cancel_while_the_cloud_works(self):
        self.state.core.cloud_agents = agents(self.base, "slow")
        job = self.state.start_job(self.viewer, self.state.session_id, "/cloud 오래 걸리는 일", None)
        ticket = next(t for t in self.state.queue if t.request_id == job["id"])
        deadline = time.monotonic() + 10
        while not ticket.external and time.monotonic() < deadline:
            time.sleep(0.02)
        ticket.cancel.set()
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "cancelled")
        last = self.state.core.storage.message_page(self.state.session_id)["messages"][-1]["content"]
        self.assertIn("사용자가 중단했습니다", last)

    def test_read_only_room_does_not_send(self):
        self.state.core.cloud_agents = agents(self.base, "fix")
        job = self.state.start_job(self.viewer, self.state.session_id, "/cloud 고쳐", "read")
        done = self.wait(job["id"])
        self.assertEqual(done["result"], READ_ONLY_BLOCKED[:600])
        self.assertFalse((self.base / "cloud_handoffs.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
