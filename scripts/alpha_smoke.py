"""Offline end-to-end smoke test for the JARVIS Alpha flow.

No network, no Cloud, no user data. It creates a temporary project and exercises
routing, session persistence, memory, safe file retrieval, project context, backup,
and resume behavior using Mock providers.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.providers import CloudStubProvider, MockLLMProvider
from jarvis.router import Destination
from jarvis.storage import Storage
from jarvis.tools import SafeFileReadTool, SafeFileSearchTool, SafeProjectContextTool


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "project"
        project.mkdir()
        (project / "router.py").write_text(
            "def route_request(text):\n    return 'local_coder'  # routing destination\n",
            encoding="utf-8",
        )
        db = root / "data" / "jarvis.db"
        storage = Storage(db)
        hardware = HardwareInfo(
            "SmokeOS", "SmokeCPU", 32, "RTX 4070 Laptop", 8, PerformanceProfile.LAPTOP
        )
        core = JarvisCore(
            storage=storage,
            hardware=hardware,
            local_general=MockLLMProvider("general", "GENERAL"),
            local_coder=MockLLMProvider("coder", "CODER"),
            cloud_coder=CloudStubProvider("cloud-coder", "CLOUD"),
            cloud_reasoning=CloudStubProvider("cloud-reason", "CLOUD"),
            file_search_tool=SafeFileSearchTool(project),
            file_read_tool=SafeFileReadTool(project),
            project_context_tool=SafeProjectContextTool(project),
            approval_callback=lambda request: False,
        )

        sid = core.get_or_create_session(False)
        response, decision = core.handle(sid, "Python 리스트 중복 제거 코드 알려줘")
        check(decision.destination == Destination.LOCAL_CODER, "simple code routes local_coder")
        check("CODER" in response, "local coder answers")

        core.storage.remember("study_mode", "hint-first")
        check(core.storage.recall("study_mode") == "hint-first", "persistent memory roundtrip")
        check("router.py" in core.file_search_tool.run(query="router"), "safe filename search")
        check("route_request" in core.file_read_tool.run(path="router.py"), "safe file read")
        project_answer = core.ask_project_local(sid, "routing destination route_request")
        check("CODER" in project_answer, "multi-file local project question")

        cloud_response, cloud_decision = core.handle(sid, "코드베이스 전체 구조 리팩터링 해줘")
        check(cloud_decision.destination == Destination.CLOUD_CODER, "complex code routes cloud candidate")
        check("local" in cloud_response.lower(), "cloud denial falls back local")

        backup = core.storage.backup_database()
        check(backup.exists(), "SQLite online backup")
        core.storage.rename_session(sid, "Smoke Session")
        check(core.storage.session_title(sid) == "Smoke Session", "session title roundtrip")
        check(core.get_or_create_session(True) == sid, "session resume finds latest")
        ok, detail = core.storage.integrity_check()
        check(ok, f"database integrity ({detail})")

    print("\nAlpha smoke test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
