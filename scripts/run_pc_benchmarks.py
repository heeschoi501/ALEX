"""이 PC 에서 ALEX 성능 평가를 한 번에 돌리고 결과를 한 폴더에 모은다(2026-10-26 claude).

클라우드에는 모델(그래픽카드)이 없어 실제 성능은 이 PC 에서만 잴 수 있다. 사용자가 명령 하나로 돌리고, 결과 폴더를 그대로
넘기면 비교 보고서를 쓸 수 있게 한다. 각 평가는 기존 스크립트를 그대로 부른다(채점 방식·조건을 바꾸지 않는다).

    python -X utf8 scripts/run_pc_benchmarks.py --dry-run              # 무엇을 돌릴지·준비가 됐는지만 본다
    python -X utf8 scripts/run_pc_benchmarks.py                        # 준비된 평가 전부
    python -X utf8 scripts/run_pc_benchmarks.py --only devauto --model qwen3.8-flash-next
    python -X utf8 scripts/run_pc_benchmarks.py --summarize logs/bench-20261027-0900   # 이미 돈 결과만 다시 요약

평가:
- devauto: 자동 수정(/dev-auto) — 지금 소스에 다시 맞춘 변이 과제(logs/probe/history_tasks_contracts_v4_20261026.json).
  일꾼은 이 PC 설정(coding_worker)을 따른다. 엄격 성공(전체 검사 종료 0)과 '목표 통과·새 회귀 없음'을 따로 센다.
- lcb: LiveCodeBench v6 hard(기본 20문제). 데이터 logs/eval-data/lcb/test6.jsonl, Strata 서버 필요.
- gpqa: GPQA Diamond(기본 전부 198문제). 데이터 logs/eval-data/gpqa/gpqa_diamond.csv, Strata 서버 필요.
준비가 안 된 평가는 이유를 적고 건너뛴다. 중간에 멈춰도 같은 --out-dir 로 다시 돌리면 lcb·gpqa 는 이어서 돈다.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "logs" / "probe" / "history_tasks_contracts_v4_20261026.json"
LCB_DATA = ROOT / "logs" / "eval-data" / "lcb" / "test6.jsonl"
GPQA_DATA = ROOT / "logs" / "eval-data" / "gpqa" / "gpqa_diamond.csv"
STRATA = "http://127.0.0.1:8080"
STEPS = ("devauto", "lcb", "gpqa")


def strata_ready(host: str = STRATA) -> str:
    """빈 문자열이면 준비됨, 아니면 이유."""
    try:
        with urllib.request.urlopen(f"{host}/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — 연결 실패·형식 오류 모두 '준비 안 됨'
        return f"Strata 서버({host})에 연결하지 못함: {type(exc).__name__}"
    if not isinstance(health, dict) or health.get("service") != "strata":
        return f"{host} 가 Strata 서버가 아님"
    return ""


def default_model() -> str:
    sys.path.insert(0, str(ROOT))
    from jarvis.config import load_settings
    settings = load_settings()
    return settings.desktop_coder_model or settings.coder_model


def plan(args, out: Path, ready=strata_ready) -> list[dict]:
    """[{name, command, skip}] — skip 이 비어 있으면 돌린다."""
    python = [sys.executable, "-X", "utf8"]
    steps = []
    for name in args.only or STEPS:
        if name == "devauto":
            command = python + [str(ROOT / "logs" / "probe" / "coding_agent_eval.py"), "--model", args.model or "(설정의 코딩 모델)",
                                "--tasks-file", str(TASKS), "--out", str(out / "devauto.json")]
            skip = "" if TASKS.is_file() else f"과제 파일 없음: {TASKS}"
        elif name == "lcb":
            command = python + [str(ROOT / "scripts" / "eval_lcb.py"), "--difficulty", "hard", "--limit", str(args.lcb_limit),
                                "--data", str(LCB_DATA), "--out", str(out / "lcb.jsonl")]
            skip = "" if LCB_DATA.is_file() else f"데이터 없음: {LCB_DATA}"
            skip = skip or ready()
        else:
            command = python + [str(ROOT / "scripts" / "eval_gpqa.py"), "--data", str(GPQA_DATA), "--out", str(out / "gpqa.jsonl"),
                                *(["--limit", str(args.gpqa_limit)] if args.gpqa_limit else [])]
            skip = "" if GPQA_DATA.is_file() else f"데이터 없음: {GPQA_DATA}"
            skip = skip or ready()
        steps.append({"name": name, "command": command, "skip": skip})
    return steps


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(out: Path) -> dict:
    """결과 폴더 → 숫자 요약. 없는 평가는 빠진다."""
    summary: dict = {}
    devauto = out / "devauto.json"
    if devauto.is_file():
        rows = json.loads(devauto.read_text(encoding="utf-8"))
        valid = [r for r in rows if r.get("bug_detected") is True]
        strict = sum(1 for r in valid if r.get("acceptance_passed") is True and r.get("suite_exit") == 0
                     and r.get("new_failures") == [])
        clean = sum(1 for r in valid if r.get("acceptance_passed") is True and r.get("new_failures") == [])
        summary["devauto"] = {"tasks": len(rows), "valid": len(valid), "strict_success": strict,
                              "passed_without_new_failures": clean,
                              "skipped": {r["task"]: r.get("note", "") for r in rows if r.get("bug_detected") is not True},
                              "seconds": round(sum(float(r.get("seconds") or 0) for r in valid), 1)}
    lcb = _jsonl(out / "lcb.jsonl")
    if lcb:
        summary["lcb"] = {"problems": len(lcb), "passed": sum(1 for r in lcb if r.get("passed")),
                          "no_code": sum(1 for r in lcb if r.get("reason") == "no code block"),
                          "seconds": round(sum(float(r.get("seconds") or 0) for r in lcb), 1)}
    gpqa = _jsonl(out / "gpqa.jsonl")
    if gpqa:
        domains: dict[str, list[int]] = {}
        for r in gpqa:
            domains.setdefault(r.get("domain") or "?", [0, 0])
            domains[r.get("domain") or "?"][0] += bool(r.get("ok"))
            domains[r.get("domain") or "?"][1] += 1
        summary["gpqa"] = {"questions": len(gpqa), "correct": sum(1 for r in gpqa if r.get("ok")),
                           "errors": sum(1 for r in gpqa if r.get("error")),
                           "by_domain": {k: f"{v[0]}/{v[1]}" for k, v in sorted(domains.items())}}
    return summary


def report(summary: dict, steps: list[dict]) -> str:
    lines = ["# ALEX PC 평가 결과", ""]
    for step in steps:
        state = f"건너뜀 — {step['skip']}" if step.get("skip") else f"종료 코드 {step.get('exit')} · {step.get('seconds')}초"
        lines.append(f"- {step['name']}: {state}")
    lines.append("")
    if d := summary.get("devauto"):
        lines.append(f"자동 수정: 유효 {d['valid']}/{d['tasks']}과제 · 엄격 성공 {d['strict_success']} · "
                     f"목표 통과·새 회귀 없음 {d['passed_without_new_failures']} · {d['seconds']}초")
    if d := summary.get("lcb"):
        lines.append(f"LiveCodeBench hard: {d['passed']}/{d['problems']} (코드 없음 {d['no_code']}) · {d['seconds']}초")
    if d := summary.get("gpqa"):
        lines.append(f"GPQA Diamond: {d['correct']}/{d['questions']} (오류 {d['errors']}) · 분야별 {d['by_domain']}")
    lines.append("")
    lines.append("한 번 잰 값이다. 같은 조건으로 여러 번 재기 전에는 우열을 일반화하지 않는다.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", choices=STEPS, help="이 평가만(여러 번 줄 수 있다)")
    parser.add_argument("--model", default="", help="devauto 에 줄 모델(비우면 설정의 코딩 모델)")
    parser.add_argument("--lcb-limit", type=int, default=20)
    parser.add_argument("--gpqa-limit", type=int, default=0, help="0 이면 198문제 전부")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--dry-run", action="store_true", help="계획과 준비 상태만 보여 주고 돌리지 않는다")
    parser.add_argument("--summarize", default="", help="이미 돈 결과 폴더를 요약만 한다")
    args = parser.parse_args(argv)
    if args.summarize:
        out = Path(args.summarize)
        summary = summarize(out)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0 if summary else 1
    out = Path(args.out_dir) if args.out_dir else ROOT / "logs" / f"bench-{datetime.now():%Y%m%d-%H%M}"
    if not args.dry_run and not args.model and "devauto" in (args.only or STEPS):
        args.model = default_model()
    steps = plan(args, out)
    for step in steps:
        print(f"[{step['name']}] {'건너뜀: ' + step['skip'] if step['skip'] else 'run: ' + ' '.join(step['command'])}", flush=True)
    if args.dry_run:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    for step in steps:
        if step["skip"]:
            continue
        started = time.monotonic()
        with (out / f"{step['name']}.log").open("w", encoding="utf-8") as log:
            done = subprocess.run(step["command"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        step.update(exit=done.returncode, seconds=round(time.monotonic() - started, 1))
        print(f"[{step['name']}] 종료 코드 {done.returncode}, {step['seconds']}초", flush=True)
    summary = summarize(out)
    (out / "summary.json").write_text(json.dumps({"steps": steps, "summary": summary}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (out / "summary.md").write_text(report(summary, steps), encoding="utf-8")
    print(report(summary, steps))
    print(f"결과 폴더: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
