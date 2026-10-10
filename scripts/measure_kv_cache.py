"""KV 캐시 양자화(`OLLAMA_KV_CACHE_TYPE`)가 실제로 이득인지 잰다.

저장소에 2026-09-13 부터 "미검증" 으로 남아 있던 항목이다. 기대는 두 가지다.
1. 캐시가 작아진 만큼 **같은 VRAM 에 더 긴 컨텍스트**가 들어간다.
2. 캐시가 작아진 만큼 **층을 더 GPU 에 올릴 수 있어** 빨라진다.

**둘 다 공짜가 아니다.** 매 토큰 역양자화 비용이 붙고 품질도 달라질 수 있다.
그래서 VRAM 만 보지 말고 tok/s 를 함께 보고, 채택 전에 `grade_codegen.py` 로 품질을 확인한다.

이 스크립트는 **서버가 이미 원하는 캐시 타입으로 떠 있다고 가정**한다. 타입은 서버
환경변수라 프로세스를 다시 띄워야 바뀐다. 결과 JSON 에 --label 로 구분해 남기고 비교한다.

사용:
  (f16 로 뜬 서버에서)  python scripts/measure_kv_cache.py --label f16
  (q8_0 으로 뜬 서버에서) python scripts/measure_kv_cache.py --label q8_0
  python scripts/measure_kv_cache.py --compare logs/kv-f16.json logs/kv-q8_0.json
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


def probe(host: str, model: str, num_ctx: int, num_predict: int, timeout: int) -> dict:
    payload = {"model": model, "messages": [{"role": "user", "content": PROBE}],
               "stream": False, "think": False, "keep_alive": "5m",
               "options": {"num_ctx": num_ctx, "num_predict": num_predict}}
    request = urllib.request.Request(
        f"{host}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def compare(paths: list[str]) -> int:
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    labels = [r["label"] for r in runs]
    print(f"model={runs[0]['model']}\n")
    header = f"{'num_ctx':>8}" + "".join(f"{lab + ' VRAM':>14}{lab + ' GPU%':>13}{lab + ' tok/s':>13}"
                                         for lab in labels)
    print(header)
    contexts = sorted({row["num_ctx"] for r in runs for row in r["rows"]})
    for ctx in contexts:
        line = f"{ctx:>8}"
        for r in runs:
            row = next((x for x in r["rows"] if x["num_ctx"] == ctx), None)
            if not row:
                line += f"{'-':>14}{'-':>13}{'-':>13}"
                continue
            line += (f"{row['vram']/GIB:>13.2f}G{row['gpu_pct']:>12.0%}"
                     f"{row['tok_s']:>13.1f}")
        print(line)
    print("\n판단 기준: VRAM 이 줄어도 tok/s 가 떨어지면 이득이 아니다.")
    print("채택 전에 grade_codegen.py 로 품질(기준 18/20)을 반드시 확인하라.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", default="", help="f16 / q8_0 / q4_0 등 서버의 현재 캐시 타입")
    p.add_argument("--model", default="")
    p.add_argument("--contexts", default="8192,12288,16384")
    p.add_argument("--num-predict", type=int, default=128)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--host", default="")
    p.add_argument("--out", default="")
    p.add_argument("--compare", nargs="+", default=None, metavar="JSON")
    args = p.parse_args(argv)

    if args.compare:
        return compare(args.compare)
    if not args.label:
        print("--label 이 필요합니다. 서버가 어떤 캐시 타입으로 떠 있는지 적어야 비교가 됩니다.")
        return 2

    settings = load_settings()
    host = (args.host or settings.ollama_host).rstrip("/")
    model = args.model or settings.general_model

    print(f"model={model} kv_cache={args.label}")
    print(f"{'num_ctx':>8} {'전체':>8} {'VRAM':>8} {'RAM':>8} {'GPU%':>6} {'tok/s':>8}")
    rows: list[dict] = []
    for raw in args.contexts.split(","):
        ctx = int(raw)
        try:
            item = probe(host, model, ctx, args.num_predict, args.timeout)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            print(f"{ctx:>8}  실패: {str(exc)[:60]}")
            continue
        total, vram = placement(host, model)
        count = item.get("eval_count") or 0
        seconds = (item.get("eval_duration") or 1) / 1e9
        tps = count / seconds if seconds else 0.0
        if not total:
            print(f"{ctx:>8}  로드 정보 없음")
            continue
        rows.append({"num_ctx": ctx, "total": total, "vram": vram, "ram": total - vram,
                     "gpu_pct": round(vram / total, 3), "tok_s": round(tps, 1)})
        print(f"{ctx:>8} {total/GIB:>7.2f}G {vram/GIB:>7.2f}G {(total-vram)/GIB:>7.2f}G "
              f"{vram/total:>5.0%} {tps:>8.1f}")

    out = Path(args.out) if args.out else ROOT / "logs" / f"kv-{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "label": args.label, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
