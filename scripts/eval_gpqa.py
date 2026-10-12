"""GPQA Diamond 198문제를 ALEX 의 로컬 모델(Strata)에 묻는 평가(2026-10-12).

조건은 ALEX 를 실제로 쓸 때와 같다: /v1/chat/completions, reasoning_effort=medium, max_tokens=32768(생각 + 답),
온도 등은 서버 기본. 프롬프트는 공개 평가에서 널리 쓰는 simple-evals 형식(마지막 줄 'Answer: X'), 보기 순서는
문제 번호로 정한 고정 시드로 섞는다. 문제 본문은 기록하지 않는다(데이터셋 약속: 예시를 공개하지 않는다) —
결과 파일에는 번호·분야·정답 글자·고른 글자·시간·토큰만 남는다. 중간에 멈춰도 이어서 돈다(results.jsonl).

데이터는 저장소에 넣지 않는다(데이터셋이 예시 공개를 금함). GPQA 저장소(github.com/idavidrein/gpqa) README 의 안내대로
dataset.zip 을 풀어 gpqa_diamond.csv 를 --data 로 준다(기본 logs/eval-data/gpqa/gpqa_diamond.csv — logs 는 공개 사본에서 빠진다).
    python scripts/eval_gpqa.py [--limit N] [--effort medium|high] [--out logs/eval/gpqa-results.jsonl]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "logs" / "eval-data" / "gpqa" / "gpqa_diamond.csv"
URL = "http://127.0.0.1:8080/v1/chat/completions"
TEMPLATE = ("Answer the following multiple choice question. The last line of your response should be of the following "
            "format: 'Answer: $LETTER' (without quotes) where LETTER is one of ABCD. Think step by step before "
            "answering.\n\n{question}\n\nA) {A}\nB) {B}\nC) {C}\nD) {D}")
ANSWER = re.compile(r"(?i)answer\s*[:：]\s*\**\s*\(?\s*([ABCD])\b")


def items(data: Path = DATA):
    with Path(data).open(encoding="utf-8", newline="") as f:
        for index, row in enumerate(csv.DictReader(f)):
            choices = [row["Correct Answer"], row["Incorrect Answer 1"], row["Incorrect Answer 2"], row["Incorrect Answer 3"]]
            choices = [" ".join(c.split()) for c in choices]
            order = list(range(4))
            random.Random(f"gpqa-diamond-{index}").shuffle(order)
            shown = [choices[i] for i in order]
            correct = "ABCD"[order.index(0)]
            prompt = TEMPLATE.format(question=row["Question"].strip(), A=shown[0], B=shown[1], C=shown[2], D=shown[3])
            yield {"index": index, "domain": row.get("High-level domain", ""), "correct": correct, "prompt": prompt}


def ask(prompt: str, effort: str, max_tokens: int, timeout: int, url: str = URL) -> dict:
    body = {"model": "qwen3.8-flash-next", "stream": True, "max_tokens": max_tokens, "reasoning_effort": effort,
            "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    started = time.monotonic()
    content, reasoning, usage, finish = [], [], {}, None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            data = json.loads(line[5:])
            choice = (data.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            finish = choice.get("finish_reason") or finish
            usage = data.get("usage") or usage
    return {"content": "".join(content), "reasoning_chars": len("".join(reasoning)), "seconds": round(time.monotonic() - started, 1),
            "tokens": usage.get("completion_tokens"), "finish": finish}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--out", default=str(ROOT / "logs" / "eval" / "gpqa-results.jsonl"))
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--url", default=URL)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["index"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    todo = [item for item in items(args.data) if item["index"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    for item in todo:
        try:
            reply = ask(item["prompt"], args.effort, args.max_tokens, args.timeout, args.url)
            found = ANSWER.findall(reply["content"])
            record = {"index": item["index"], "domain": item["domain"], "correct": item["correct"],
                      "picked": found[-1].upper() if found else None, "seconds": reply["seconds"], "tokens": reply["tokens"],
                      "reasoning_chars": reply["reasoning_chars"], "finish": reply["finish"], "effort": args.effort,
                      "answer_tail": reply["content"][-80:] if not found else ""}
        except Exception as exc:  # 한 문제의 연결 오류가 전체를 멈추지 않게 — 기록하고 다음으로
            record = {"index": item["index"], "domain": item["domain"], "correct": item["correct"], "picked": None,
                      "error": f"{type(exc).__name__}: {str(exc)[:160]}", "effort": args.effort}
        record["ok"] = record["picked"] == record["correct"]
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(f"{len(rows)}/198 #{item['index']} {item['domain'][:4]} {record['correct']}→{record['picked']} "
              f"{'O' if record['ok'] else 'X'} {record.get('seconds')}s {record.get('tokens')}tok "
              f"| 누적 {sum(r['ok'] for r in rows)}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
