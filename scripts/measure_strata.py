"""Sequential synthetic latency probes; no answer grading or conversation writes.

Run with Windows Python, after telling the user. Refuses a busy ALEX/Strata.
Example: python scripts/measure_strata.py --label baseline --output logs/strata-baseline.json
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.config import load_settings


def get(url: str, headers: dict | None = None) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=5) as response:
        return json.load(response)


def memory() -> dict:
    result = {}
    if sys.platform == "win32":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (k, ctypes.c_ulonglong) for k in ("total", "available", "page_total", "page_available",
                                                "virtual_total", "virtual_available", "extended")]
        status = Memory()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            result["ram_available_gib"] = round(status.available / 2**30, 3)
            result["commit_available_gib"] = round(status.page_available / 2**30, 3)
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,power.draw",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            result["gpu_used_total_mib_power_w"] = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def idle(settings) -> dict:
    base = f"http://127.0.0.1:{settings.web_port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            page = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ConnectionRefusedError):
            raise
        state = {"alex_running": False}
    else:
        token = re.search(r'<meta name="jarvis-token" content="([A-Za-z0-9_-]+)">', page)
        if token is None:
            raise RuntimeError("Cannot authenticate the local ALEX status")
        live = get(base + "/api/state", {"X-Jarvis-Token": token.group(1)})
        if live.get("busy") or live.get("queue", {}).get("waiting"):
            raise RuntimeError("ALEX is busy; leave its request running and measure later")
        state = {"alex_running": True, "effective_settings": live.get("effective_settings"),
                 "context_limits": live.get("context_limits")}
    health = get(settings.strata_host + "/health")
    if health.get("service") != "strata" or health.get("model") != settings.strata_model:
        raise RuntimeError("The running Strata model does not match ALEX")
    if health.get("max_context") != settings.strata_num_ctx or not health.get("loaded"):
        raise RuntimeError("Load the configured context/model before measuring")
    status = get(settings.strata_host + "/status")
    if status.get("busy") or status.get("queued"):
        raise RuntimeError("Strata is busy; measure later")
    return {**state, "health": health}


def probe(settings, repeats: int, workload: str = "count") -> dict:
    state = idle(settings)
    content = "Input: " + "alpha beta gamma delta " * repeats + \
              "\nWrite the integers from 1 to 200 separated by spaces."
    if workload == "mixed":
        rows = ["A CPU worker computes expert weights from system RAM.",
                "GPU memory holds attention weights and a cache of selected experts.",
                "def enqueue(task): pending.append(task); return len(pending)",
                "사용자가 요청을 취소하면 대기 중인 작업을 제거하고 자원을 반환한다.",
                "SELECT task_id, state FROM tasks WHERE state = 'ready' ORDER BY task_id;",
                "A cache miss reads a new block; a hit reuses an existing block.",
                "설정 파일을 바꾸기 전에 이전 값을 저장하고 실행 결과를 기록한다.",
                '{"context": 16384, "workers": 16, "prefill": 512}']
        content = "Synthetic system notes:\n" + "\n".join(
            f"Record {i}: {rows[i % len(rows)]}" for i in range(repeats)) + \
            "\nExplain how a local inference server schedules work across CPU, GPU and memory. Use at least 200 words."
    payload = {"model": settings.strata_model, "stream": True, "max_tokens": 64,
               "reasoning_effort": "none", "temperature": 0, "seed": 42,
               "messages": [{"role": "system", "content": "Follow the user instruction."},
                            {"role": "user", "content": content}]}
    before = memory()
    request = urllib.request.Request(settings.strata_host + "/v1/chat/completions",
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    started, first, digest, last, finished = time.monotonic(), None, hashlib.sha256(), {}, False
    with urllib.request.urlopen(request, timeout=360) as response:
        for raw in response:
            if not raw.startswith(b"data:"):
                continue
            body = raw[5:].strip()
            if body == b"[DONE]":
                break
            chunk = json.loads(body)
            if chunk.get("error"):
                raise RuntimeError(str(chunk["error"]))
            for choice in chunk.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if content:
                    first = first if first is not None else time.monotonic() - started
                    digest.update(content.encode())
                finished |= bool(choice.get("finish_reason"))
            for key in ("usage", "timings"):
                if chunk.get(key):
                    last[key] = chunk[key]
    if not finished:
        raise RuntimeError("Incomplete stream")
    wall = time.monotonic() - started
    return {"repeats": repeats, "workload": workload, "state": state, "before": before, "after": memory(),
            "first_content_seconds": round(first, 3) if first is not None else None,
            "wall_seconds": round(wall, 3), "output_sha256": digest.hexdigest(), **last}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, nargs="+", default=[4, 128, 512])
    parser.add_argument("--workload", choices=("count", "mixed"), default="count")
    args = parser.parse_args()
    if any(not 1 <= n <= 512 for n in args.repeats):
        parser.error("repeats must be 1..512")
    settings = load_settings()
    result = {"label": args.label, "quality_evaluation": False, "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for count in args.repeats:
        run = probe(settings, count, args.workload)
        result["runs"].append(run)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(run, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
