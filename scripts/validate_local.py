"""Opt-in real GPU chat/edit/undo/memory check. Uses isolated fixtures, no user files.

python scripts/validate_local.py --model qwen3:8b-q4_K_M
Reports actual outputs and checks. This is a smoke test, not a model leaderboard.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jarvis.core import JarvisCore
from jarvis.hardware import detect_hardware
from jarvis.providers import FailingProvider, Message, OllamaProvider
from jarvis.storage import Storage
from jarvis.tools import SafeFileReadTool, SafeFileSearchTool, SafeProjectContextTool
from jarvis.workspace import SafeWorkspaceEditor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b-q4_K_M")
    parser.add_argument("--turns", type=int, default=0, help="optional long-conversation turns")
    args = parser.parse_args()
    hardware = detect_hardware()
    report = {"model": args.model, "hardware": asdict(hardware), "checks": [], "requests": []}
    original_open = urllib.request.urlopen

    def local_open(request, *pos, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else request
        if urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("Non-local network request blocked in hardware validation")
        report["requests"].append(urlparse(url).path)
        return original_open(request, *pos, **kwargs)

    def check(name, passed, detail=None):
        report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
        print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)

    with tempfile.TemporaryDirectory(prefix="jarvis-real-") as td, patch("urllib.request.urlopen", side_effect=local_open):
        root = Path(td)
        source = root / "sample.py"
        original = b'def add(a, b):\r\n    """Return the sum of two numbers."""\r\n    return a - b\r\n'
        source.write_bytes(original)
        provider = OllamaProvider(args.model, num_ctx=4096, num_predict=1024, timeout=180)
        check("model_installed", provider.health_check())
        chunks = []
        core = JarvisCore(storage=Storage(root / "test.db"), hardware=hardware,
                          local_general=provider, local_coder=provider,
                          cloud_coder=FailingProvider(), cloud_reasoning=FailingProvider(), local_only=True,
                          response_chunk_callback=chunks.append, context_char_budget=6000,
                          file_search_tool=SafeFileSearchTool(root), file_read_tool=SafeFileReadTool(root),
                          project_context_tool=SafeProjectContextTool(root),
                          workspace_editor=SafeWorkspaceEditor(root, root / "backups"))
        sid = core.get_or_create_session(False)
        started = time.monotonic()
        answer, route = core.handle(sid, "17 곱하기 23의 결과를 한국어 한 문장으로 답해줘.")
        check("korean_chat_and_arithmetic", "391" in answer, {"answer": answer, "metrics": provider.last_metrics, "seconds": round(time.monotonic() - started, 3)})
        check("streaming_chunks", len(chunks) > 1, len(chunks))
        core.storage.remember("사용자 선호 언어", "한국어")
        check("persistent_memory", Storage(root / "test.db").recall("사용자 선호 언어") == "한국어")
        explanation = core.ask_file_local(sid, "sample.py", "add 함수의 버그를 한 문장으로 설명해줘.")
        check("file_analysis", any(word in explanation for word in ("빼", "뺄", "차", "-", "subtract")), explanation)
        try:
            proposal = core.propose_file_edit_local(sid, "sample.py", "add 함수가 두 숫자의 합을 반환하도록 빼기 버그를 고쳐줘. 함수 이름과 인자는 유지해줘.")
            check("proposal_does_not_write", source.read_bytes() == original, proposal.diff)
            tree = ast.parse(proposal.proposed_text)
            functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "add"]
            returns = [n for f in functions for n in ast.walk(f) if isinstance(n, ast.Return)]
            check("generated_code_correct_addition", any(isinstance(n.value, ast.BinOp) and isinstance(n.value.op, ast.Add) for n in returns), proposal.proposed_text)
            core.workspace_editor.apply(proposal.proposal_id)
            check("apply_saved_file", source.read_bytes() != original)
            core.workspace_editor.restore_latest("sample.py")
            check("undo_exact_original_bytes", source.read_bytes() == original)
        except Exception as exc:
            check("edit_flow", False, f"{type(exc).__name__}: {exc}")
        for i in range(args.turns):
            answer, _ = core.handle(sid, f"대화 유지 시험 {i+1}회입니다. 숫자 {i+1}만 답하세요.")
            if i % 10 == 9:
                print(f"Long conversation: {i+1}/{args.turns}", flush=True)
        if args.turns:
            summary, through = core.storage.get_session_summary(sid)
            check("rolling_summary", bool(summary) and through > 0, {"summary_chars": len(summary), "through_id": through})
        check("restart_history", Storage(root / "test.db").message_count(sid) >= 6)
        check("database_integrity", core.storage.integrity_check()[0])
    path = ROOT / "logs" / ("real-validation-" + args.model.replace(":", "_").replace("/", "_") + ".json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {path}")
    return 0 if all(c["passed"] for c in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
