"""모델·컨텍스트 조합의 최적 `num_gpu`(GPU 에 올릴 층 수)를 찾는다.

Ollama 의 자동 배치는 보수적이라 VRAM 을 남긴다. 2026-09-19 실측에서 `qwen3:14b` 가
VRAM 1.7GB 를 놀리면서 4.2GB 를 RAM 에 두고 있었고, num_gpu=32 로 밀어넣으니 10.3 에서
15.2 tok/s 가 됐다(48% 개선).

**그런데 최댓값이 최적이 아니다.** 물리 VRAM 을 넘기면 Windows 드라이버가 초과분을 시스템
RAM 으로 스필하는데, 그것이 Ollama 가 명시적으로 CPU 층을 두는 것보다 느리다. 그래서
단조 증가가 아니라 **봉우리가 있는 곡선**이고, 직접 훑어야 꼭대기를 찾는다.

사용:
  python scripts/tune_gpu_layers.py --model qwen3:8b-q4_K_M --num-ctx 8192
  python scripts/tune_gpu_layers.py --model gpt-oss:20b --num-ctx 8192 --layers 0,16,20,24,28
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

from jarvis.config import load_settings  # noqa: E402

GIB = 1024 ** 3
PROBE = "파이썬에서 리스트를 정렬하는 방법을 두 가지만 짧게 알려줘."


def placement(host: str, model: str) -> tuple[int, int] | tuple[None, None]:
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None, None
    base = model.split(":")[0]
    for item in data.get("models", []):
        name = str(item.get("name", ""))
        if name == model or name.startswith(base + ":"):
            return int(item.get("size") or 0), int(item.get("size_vram") or 0)
    return None, None


def probe(host: str, model: str, num_ctx: int, num_gpu: int | None,
          num_predict: int, timeout: int) -> dict:
    options = {"num_ctx": num_ctx, "num_predict": num_predict}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    payload = {"model": model, "messages": [{"role": "user", "content": PROBE}],
               "stream": False, "think": False, "keep_alive": "5m", "options": options}
    request = urllib.request.Request(
        f"{host}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True)
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--num-predict", type=int, default=128)
    p.add_argument("--layers", default="",
                   help="쉼표로 구분한 후보. 비우면 auto 와 몇 개를 자동으로 훑는다. 0 은 auto.")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--host", default="")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)

    host = (args.host or load_settings().ollama_host).rstrip("/")
    if args.layers.strip():
        candidates: list[int | None] = [None if int(x) == 0 else int(x)
                                        for x in args.layers.split(",") if x.strip()]
    else:
        candidates = [None, 16, 20, 24, 28, 32, 36, 40, 48]

    print(f"model={args.model} num_ctx={args.num_ctx}")
    print(f"{'num_gpu':>8} {'VRAM':>8} {'RAM':>8} {'GPU%':>6} {'tok/s':>8}")
    rows: list[dict] = []
    for layers in candidates:
        label = "auto" if layers is None else str(layers)
        try:
            item = probe(host, args.model, args.num_ctx, layers, args.num_predict, args.timeout)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            print(f"{label:>8}  실패: {str(exc)[:60]}")
            continue
        total, vram = placement(host, args.model)
        count = item.get("eval_count") or 0
        seconds = (item.get("eval_duration") or 1) / 1e9
        tps = count / seconds if seconds else 0.0
        if not total:
            print(f"{label:>8}  로드 정보 없음  {tps:>8.1f}")
            continue
        rows.append({"num_gpu": layers, "total": total, "vram": vram,
                     "ram": total - vram, "gpu_pct": round(vram / total, 3),
                     "tok_s": round(tps, 1)})
        print(f"{label:>8} {vram/GIB:>7.2f}G {(total-vram)/GIB:>7.2f}G "
              f"{vram/total:>5.0%} {tps:>8.1f}")

    if rows:
        best = max(rows, key=lambda r: r["tok_s"])
        auto = next((r for r in rows if r["num_gpu"] is None), None)
        label = "auto" if best["num_gpu"] is None else str(best["num_gpu"])
        print(f"\n최적 num_gpu = {label}  ({best['tok_s']} tok/s)")
        if auto and best["num_gpu"] is not None and auto["tok_s"]:
            gain = best["tok_s"] / auto["tok_s"] - 1
            print(f"자동 배치 대비 {gain:+.0%}")
        print("주의: 이 값은 이 모델과 이 num_ctx 에서만 맞다. 둘 중 하나라도 바꾸면 다시 재라.")

    out = Path(args.out) if args.out else ROOT / "logs" / (
        f"gpulayers-{args.model.replace(':','_').replace('/','_')}-{args.num_ctx}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": args.model, "num_ctx": args.num_ctx, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
