"""Benchmark configured Ollama model(s) on the user's real machine.

Measures:
- time to first streamed content chunk (perceived latency)
- wall-clock total response time
- Ollama usage metrics when present (load/prompt/eval durations and token counts)
- calculated output tokens/second
- NVIDIA GPU memory.used before/after when nvidia-smi is available

Examples:
  python scripts/benchmark_local.py
  python scripts/benchmark_local.py --target both
  python scripts/benchmark_local.py --target coder --prompt "Pandas groupby 예제를 보여줘"

This script is intentionally not part of CI because it needs the user's Ollama/GPU environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.app import NUM_CTX_BY_PROFILE, apply_profile_override, effective_local_models
from jarvis.config import load_settings
from jarvis.hardware import PerformanceProfile, _find_nvidia_smi, detect_hardware


def gpu_memory_mib() -> int | None:
    executable = _find_nvidia_smi()
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        values: list[tuple[float, int]] = []
        for line in result.stdout.strip().splitlines():
            total, used = [part.strip() for part in line.split(",", maxsplit=1)]
            values.append((float(total), int(float(used))))
        return max(values, key=lambda item: item[0])[1] if values else None
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


def _ns_to_seconds(value: object) -> float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return round(float(value) / 1_000_000_000, 4)


def _tokens_per_second(eval_count: object, eval_duration_ns: object) -> float | None:
    if not isinstance(eval_count, (int, float)) or not isinstance(eval_duration_ns, (int, float)):
        return None
    if eval_count < 0 or eval_duration_ns <= 0:
        return None
    return round(float(eval_count) / (float(eval_duration_ns) / 1_000_000_000), 2)


def benchmark(host: str, model: str, num_ctx: int, prompt: str, timeout: int, *, think: bool = False, num_predict: int = 1024, keep_alive: str = "10m") -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": think,
        "keep_alive": keep_alive,
        "options": {"num_ctx": num_ctx, "num_predict": num_predict},
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    before = gpu_memory_mib()
    started = time.perf_counter()
    first_content_at: float | None = None
    chunks: list[str] = []
    final_item: dict = {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            if not raw.strip():
                continue
            item = json.loads(raw.decode("utf-8"))
            content = str((item.get("message") or {}).get("content") or "")
            if content:
                if first_content_at is None:
                    first_content_at = time.perf_counter()
                chunks.append(content)
            if item.get("done"):
                final_item = item
    finished = time.perf_counter()
    after = gpu_memory_mib()

    eval_count = final_item.get("eval_count")
    eval_duration = final_item.get("eval_duration")
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "think": think,
        "keep_alive": keep_alive,
        "first_content_seconds": round((first_content_at - started), 3) if first_content_at else None,
        "wall_total_seconds": round(finished - started, 3),
        "response_chars": len("".join(chunks)),
        "prompt_eval_count": final_item.get("prompt_eval_count"),
        "prompt_eval_seconds": _ns_to_seconds(final_item.get("prompt_eval_duration")),
        "eval_count": eval_count,
        "eval_seconds": _ns_to_seconds(eval_duration),
        "eval_tokens_per_second": _tokens_per_second(eval_count, eval_duration),
        "model_load_seconds": _ns_to_seconds(final_item.get("load_duration")),
        "ollama_total_seconds": _ns_to_seconds(final_item.get("total_duration")),
        "done_reason": final_item.get("done_reason"),
        "gpu_memory_before_mib": before,
        "gpu_memory_after_mib": after,
        "gpu_memory_delta_mib": (after - before) if before is not None and after is not None else None,
    }


def _targets(settings, profile: PerformanceProfile, target: str) -> list[tuple[str, str]]:
    general_model, coder_model = effective_local_models(settings, profile)
    if target == "general":
        return [("general", general_model)]
    if target == "coder":
        if profile != PerformanceProfile.DESKTOP and settings.single_model_on_laptop:
            return [("coder(shared-general)", general_model)]
        return [("coder", coder_model)]

    # both: Laptop single-model이면 같은 모델을 두 번 측정해 시간을 낭비하지 않는다.
    result = [("general", general_model)]
    if not (profile != PerformanceProfile.DESKTOP and settings.single_model_on_laptop):
        result.append(("coder", coder_model))
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project JARVIS Ollama benchmark")
    parser.add_argument("--target", choices=("general", "coder", "both"), default="general")
    parser.add_argument("--repeat", type=int, default=1, help="number of measured runs per model")
    parser.add_argument(
        "--prompt",
        default="Project JARVIS 로컬 모델 성능 확인입니다. Python 리스트 중복 제거 방법을 두 가지로 짧게 설명해 주세요.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    hardware = detect_hardware()
    apply_profile_override(hardware, settings.force_profile)
    num_ctx = NUM_CTX_BY_PROFILE[hardware.profile]
    targets = _targets(settings, hardware.profile, args.target)
    print(f"profile={hardware.profile.value} num_ctx={num_ctx} target={args.target}")

    repeat = max(1, min(int(args.repeat), 20))
    results: list[dict] = []
    for label, model in targets:
        print(f"benchmarking {label}: {model} x {repeat}")
        for run_index in range(1, repeat + 1):
            try:
                result = benchmark(
                    settings.ollama_host, model, num_ctx, args.prompt, settings.request_timeout,
                    think=settings.ollama_think,
                    num_predict=settings.local_max_output_tokens,
                    keep_alive=settings.ollama_keep_alive,
                )
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                print(f"benchmark failed ({label}, run={run_index}): {exc}")
                return 2
            result["route_label"] = label
            result["run"] = run_index
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2))

    out_dir = settings.log_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    payload: object = results[0] if len(results) == 1 else results
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
