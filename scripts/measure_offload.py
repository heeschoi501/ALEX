"""RAM 오프로딩이 대화 체감에 실제로 얼마나 비싼지 잰다.

`PERFORMANCE.md` 2절은 오프로딩을 "12% 만 넘어가도 6.6배" 로 요약했는데, 같은 표에서
tok/s 는 43.6 에서 38.3 으로 12% 밖에 안 떨어졌다. 시간은 6.3배인데 생성 속도가 그대로면
그 차이는 토큰 생성이 아닌 다른 곳에서 온 것이다 — 모델 로딩이거나 프롬프트 처리다.

둘은 대화 체감이 전혀 다르다:
- **로딩**이면 한 번만 내고, `keep_alive` 로 상주시키면 둘째 턴부터 안 낸다.
- **프롬프트 처리**면 매 턴 낸다.
- **생성**이면 스트리밍이라 토큰이 흐르는 속도로 느껴진다.

Ollama 가 load_duration / prompt_eval_duration / eval_duration 을 나눠서 보고하므로
추정하지 않고 가른다.

사용:
  python scripts/measure_offload.py --model qwen3:8b-q4_K_M --num-ctx 8192 --turns 5
  python scripts/measure_offload.py --model gpt-oss:20b --num-ctx 8192 --turns 5
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
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

NS = 1_000_000_000

# 대화 체감을 재는 것이므로 코드 과제가 아니라 짧은 질문을 쓴다.
TURNS = [
    "안녕. 오늘 할 일을 정리하는 걸 도와줘.",
    "첫 번째는 문서 정리야. 30분 정도 걸릴 것 같아.",
    "두 번째는 코드 리뷰인데 한 시간쯤 봐야 해.",
    "점심은 12시에 먹을 거야. 순서를 어떻게 잡으면 좋을까?",
    "좋아. 그럼 오후에는 뭘 먼저 하면 될까?",
    "고마워. 지금까지 정리한 걸 세 줄로 요약해줘.",
]


def placement(host: str, model: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    for item in data.get("models", []):
        if str(item.get("name", "")) == model:
            total = int(item.get("size") or 0)
            vram = int(item.get("size_vram") or 0)
            return {"total": total, "vram": vram, "ram": max(0, total - vram)}
    return None


def chat(host: str, model: str, messages: list[dict], num_ctx: int, think: bool,
         num_predict: int, timeout: int) -> dict:
    payload = {"model": model, "messages": messages, "stream": False, "think": think,
               "keep_alive": "10m",
               "options": {"num_ctx": num_ctx, "num_predict": num_predict}}
    request = urllib.request.Request(
        f"{host}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        item = json.loads(response.read().decode("utf-8"))
    item["_wall"] = time.perf_counter() - started
    return item


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="")
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--num-predict", type=int, default=512)
    p.add_argument("--turns", type=int, default=5)
    p.add_argument("--think", action="store_true")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--host", default="")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)

    settings = load_settings()
    host = (args.host or settings.ollama_host).rstrip("/")
    model = args.model or settings.general_model

    print(f"model={model} num_ctx={args.num_ctx} num_predict={args.num_predict} "
          f"think={'on' if args.think else 'off'} turns={args.turns}")

    messages: list[dict] = []
    rows: list[dict] = []
    for i in range(1, min(args.turns, len(TURNS)) + 1):
        messages.append({"role": "user", "content": TURNS[i - 1]})
        try:
            item = chat(host, model, messages, args.num_ctx, args.think,
                        args.num_predict, args.timeout)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            print(f"  turn {i}: 실패 {exc}")
            return 1
        content = str((item.get("message") or {}).get("content") or "")
        messages.append({"role": "assistant", "content": content})

        load_s = (item.get("load_duration") or 0) / NS
        prompt_s = (item.get("prompt_eval_duration") or 0) / NS
        eval_s = (item.get("eval_duration") or 0) / NS
        eval_n = item.get("eval_count") or 0
        prompt_n = item.get("prompt_eval_count") or 0
        tps = eval_n / eval_s if eval_s else 0.0
        first_token_s = load_s + prompt_s

        rows.append({"turn": i, "wall": round(item["_wall"], 2), "load": round(load_s, 2),
                     "prompt": round(prompt_s, 2), "eval": round(eval_s, 2),
                     "eval_count": eval_n, "prompt_count": prompt_n,
                     "tok_s": round(tps, 1), "first_token": round(first_token_s, 2)})
        print(f"  turn {i}: 전체 {item['_wall']:>6.2f}s | 로딩 {load_s:>5.2f}s | "
              f"프롬프트 {prompt_s:>5.2f}s({prompt_n}tok) | 생성 {eval_s:>6.2f}s({eval_n}tok) "
              f"| {tps:>5.1f} tok/s | 첫토큰 {first_token_s:>5.2f}s")

    place = placement(host, model)
    if place:
        pct = place["vram"] / place["total"] if place["total"] else 0
        print(f"\n배치: 전체 {place['total']/1024**3:.2f}GB  "
              f"GPU {place['vram']/1024**3:.2f}GB({pct:.0%})  "
              f"RAM {place['ram']/1024**3:.2f}GB")

    warm = rows[1:]
    if warm:
        print(f"\n[첫 턴] 첫토큰 {rows[0]['first_token']:.2f}s (로딩 {rows[0]['load']:.2f}s 포함)")
        print(f"[둘째 턴부터 평균] 첫토큰 {sum(r['first_token'] for r in warm)/len(warm):.2f}s  "
              f"생성 {sum(r['tok_s'] for r in warm)/len(warm):.1f} tok/s  "
              f"전체 {sum(r['wall'] for r in warm)/len(warm):.2f}s")

    out = Path(args.out) if args.out else ROOT / "logs" / f"offload-{model.replace(':','_').replace('/','_')}-{args.num_ctx}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "num_ctx": args.num_ctx,
                               "think": args.think, "placement": place, "turns": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
