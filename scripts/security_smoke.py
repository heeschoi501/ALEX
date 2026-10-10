"""Offline security-boundary smoke checks for Project JARVIS.

Not a penetration test. It ensures the promises documented for v0.1.x remain mechanically true:
- project traversal blocked
- automatic context avoids obvious credential filenames
- remote Ollama is opt-in
- cloud secrets trigger hard-block
- Web API needs its runtime token
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import threading
import urllib.error
import urllib.request
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.config import ConfigError, Settings, validate
from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import Message, MockLLMProvider, OpenAIResponsesProvider, ProviderError
from jarvis.router import Destination
from jarvis.storage import Storage
from jarvis.tools import SafeFileReadTool, SafeFileSearchTool, SafeProjectContextTool, ToolError
from jarvis.web import WebAppState, make_server
from jarvis.webread import WebError, WebReader, guarded_opener


class TrackingCloud(MockLLMProvider):
    def __init__(self):
        super().__init__("tracking-cloud", "CLOUD")
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        return super().generate(messages, **kwargs)


def main() -> int:
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "project"
        project.mkdir()
        (project / "normal.py").write_text("router = 'safe'", encoding="utf-8")
        (project / "credentials.json").write_text('{"api_key":"SECRET"}', encoding="utf-8")
        (root / "outside.txt").write_text("secret", encoding="utf-8")

        read = SafeFileReadTool(project)
        try:
            read.run(path="../outside.txt")
            traversal = False
        except ToolError:
            traversal = True
        checks.append(("file traversal blocked", traversal))

        search = SafeFileSearchTool(project)
        try:
            search.run(query="../outside")
            search_traversal = False
        except ToolError:
            search_traversal = True
        checks.append(("search traversal syntax blocked", search_traversal))

        context = SafeProjectContextTool(project)
        retrieved = context.run(query="router api_key SECRET")
        checks.append(("automatic context skips credential filename", "credentials.json" not in retrieved and "SECRET" not in retrieved))

        try:
            validate(Settings(file_search_root=project, ollama_host="http://192.0.2.10:11434"))
            remote_block = False
        except ConfigError:
            remote_block = True
        checks.append(("remote Ollama blocked by default", remote_block))

        cloud = TrackingCloud()
        core = JarvisCore(
            storage=Storage(root / "security.db"),
            hardware=HardwareInfo("OS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("g", "G"),
            local_coder=MockLLMProvider("c", "C"),
            cloud_coder=cloud,
            cloud_reasoning=cloud,
            approval_callback=lambda _request: True,
            require_cloud_approval=True,
            cloud_block_sensitive=True,
        )
        sid = core.get_or_create_session(False)
        core.handle_forced(sid, "API_KEY=super-secret-value-12345 분석해줘", Destination.CLOUD_REASONING)
        checks.append(("sensitive cloud request hard-blocked before generate", cloud.calls == 0))

        core.handle_forced(
            sid,
            "AWS credential AKIAABCDEFGHIJKLMNOP 분석해줘",
            Destination.CLOUD_REASONING,
        )
        checks.append(("additional common secret shape is cloud-blocked", cloud.calls == 0))

        core.require_cloud_approval = False
        core.approval_callback = None
        core.handle_forced(sid, "api_key=still-secret-no-approval", Destination.CLOUD_REASONING)
        checks.append(("sensitive hard-block remains active when approval switch is off", cloud.calls == 0))

        reflected_secret = "sk-SMOKE-SECRET-SHOULD-NOT-APPEAR"
        cloud_http = OpenAIResponsesProvider("api-key", "model")
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/responses", 400, "bad", {},
            io.BytesIO(f'{{"error":"{reflected_secret}"}}'.encode()),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            try:
                cloud_http.generate([Message("user", "hello")])
                error_redacted = False
            except ProviderError as exc:
                error_redacted = reflected_secret not in str(exc)
        checks.append(("cloud HTTP error body is not reflected", error_redacted))

        # Web is a different surface with no interactive confirmation. It must stay fail-closed
        # even if a global/CLI setting disabled cloud approval before WebAppState is created.
        core.require_cloud_approval = False
        core.approval_callback = None
        state = WebAppState(core, sid, token="smoke-token")
        state.process("/ask-cloud 프로젝트 전체 구조를 심층 분석해줘")
        checks.append(("Web re-enables cloud approval gate even if global setting was off", cloud.calls == 0 and core.require_cloud_approval))
        server = make_server(state, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]

        rebound = urllib.request.Request(f"http://{host}:{port}/", headers={"Host": "evil.example"})
        try:
            urllib.request.urlopen(rebound, timeout=2)
            host_block = False
        except urllib.error.HTTPError as exc:
            host_block = exc.code == 403 and "smoke-token" not in exc.read().decode("utf-8", errors="replace")
        checks.append(("non-local Host blocked before Web token exposure", host_block))

        # ADR-051: 웹 읽기가 이 기계 안쪽을 읽으면 안 된다. 과녁은 방금 띄운 ALEX 자신의
        # 웹 서버다 — 살아 있고, 위에서 평범한 urllib 로는 닿았다.
        try:
            WebReader().fetch(f"http://{host}:{port}/")
            reader_block = False
        except WebError:
            reader_block = True
        checks.append(("web reader refuses loopback (ALEX's own server)", reader_block))
        try:
            guarded_opener().open(f"http://{host}:{port}/", timeout=2)
            socket_block = False
        except WebError:
            socket_block = True
        checks.append(("web reader socket guard refuses loopback even past URL check", socket_block))

        req = urllib.request.Request(
            f"http://{host}:{port}/api/chat",
            data=json.dumps({"message": "hello"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            token_block = False
        except urllib.error.HTTPError as exc:
            token_block = exc.code == 403
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        checks.append(("Web POST requires local runtime token", token_block))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
    if failed:
        print(f"Security smoke failed: {len(failed)}")
        return 1
    print(f"Security smoke PASS ({len(checks)}/{len(checks)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
