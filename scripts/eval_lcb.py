"""LiveCodeBench v6(2025-02 이후 문제) 코드 생성 평가 — ALEX 로컬 모델(Strata) 실행과 다른 모델의 답 채점(2026-10-12).

- 문제: livecodebench/code_generation_lite 의 test6.jsonl 중 contest_date >= 2025-02-01 (AtCoder 표준 입출력 + LeetCode 함수형).
- 프롬프트: LiveCodeBench 공식 코드 생성 형식(시스템 지시 + ### Question / ### Format / ### Answer).
- 모델 조건: ALEX 를 쓸 때와 같다 — reasoning_effort=medium, max_tokens=32768, 온도 등 서버 기본, 한 번 생성(pass@1).
- 채점: 공개+비공개 시험을 모두 통과해야 정답. 답 코드는 ALEX 실행 보호(build_runner.install_guard: 네트워크·하위 프로세스·
  작업 폴더 밖 쓰기 금지) 안에서, 시험마다 새 프로세스·시간 제한으로 실행. 첫 실패에서 멈춘다.
- 비공개 시험은 base64 → zlib → pickle(문자열) 로 저장돼 있다. pickle 은 임의 코드를 실행할 수 있으므로 전역 객체를 하나도
  허용하지 않는 제한 Unpickler 로 문자열만 읽는다.

데이터: livecodebench/code_generation_lite 의 test6.jsonl(공개)을 --data 로 준다(기본 logs/eval-data/lcb/test6.jsonl).
    python scripts/eval_lcb.py --difficulty hard --limit 50 --out logs/eval/lcb-hard50.jsonl            # 로컬 모델이 풀기
    python scripts/eval_lcb.py --difficulty hard --limit 50 --export prompts.json                      # 같은 문제 내보내기
    python scripts/eval_lcb.py --difficulty hard --limit 50 --answers 답.json --out luna.jsonl          # 다른 모델 답 채점
"""
from __future__ import annotations

import argparse
import base64
from decimal import Decimal, InvalidOperation
import io
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "logs" / "eval-data" / "lcb" / "test6.jsonl"
URL = "http://127.0.0.1:8080/v1/chat/completions"
SYSTEM = ("You are an expert Python programmer. You will be given a question (problem specification) and will generate a "
          "correct Python program that matches the specification and passes all tests.")
STDIN_FORMAT = ("Read the inputs from stdin solve the problem and write the answer to stdout (do not directly test on the "
                "sample inputs). Enclose your code within delimiters as follows. Ensure that when the python program runs, it "
                "reads the inputs, runs the algorithm and writes output to STDOUT.\n```python\n# YOUR CODE HERE\n```")
FUNC_FORMAT = ("You will use the following starter code to write the solution to the problem and enclose your code within "
               "delimiters.\n```python\n{starter}\n```")
CODE = re.compile(r"```(?:python|py)?[^\n]*\n(.*?)```", re.S)
TEST_SECONDS = 10


class _StringsOnly(pickle.Unpickler):
    def find_class(self, module, name):  # 어떤 전역 객체도 만들지 않는다 — 문자열·목록·사전만
        raise pickle.UnpicklingError(f"blocked global {module}.{name}")


def tests_of(problem: dict) -> list[dict]:
    tests = json.loads(problem["public_test_cases"])
    raw = problem["private_test_cases"]
    try:
        private = json.loads(raw)
    except (ValueError, TypeError):
        private = json.loads(_StringsOnly(io.BytesIO(zlib.decompress(base64.b64decode(raw.encode("utf-8"))))).load())
    return tests + private


def prompt_of(problem: dict) -> str:
    fmt = FUNC_FORMAT.format(starter=problem["starter_code"]) if problem["starter_code"].strip() else STDIN_FORMAT
    return (f"### Question:\n{problem['question_content']}\n\n### Format: {fmt}\n\n"
            "### Answer: (use the provided format with backticks)\n\n")


def generate(prompt: str, effort: str, max_tokens: int, timeout: int, url: str = URL) -> dict:
    body = {"model": "qwen3.8-flash-next", "stream": True, "max_tokens": max_tokens, "reasoning_effort": effort,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    started = time.monotonic()
    content, usage, finish = [], {}, None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            data = json.loads(line[5:])
            choice = (data.get("choices") or [{}])[0]
            if (choice.get("delta") or {}).get("content"):
                content.append(choice["delta"]["content"])
            finish = choice.get("finish_reason") or finish
            usage = data.get("usage") or usage
    return {"content": "".join(content), "seconds": round(time.monotonic() - started, 1),
            "tokens": usage.get("completion_tokens"), "finish": finish}


# 시험 하나를 실행하는 자식 프로세스 본문. 실행 보호를 켠 뒤 답 코드를 돌린다.
CHILD = r'''
import json, sys, io
from pathlib import Path
sys.path.insert(0, ROOT)
from jarvis.build_runner import install_guard
workspace = Path(WORKSPACE).resolve()
code = (workspace / "solution.py").read_text(encoding="utf-8")
mode, func = sys.argv[1], sys.argv[2]
if mode == "functional":
    args = [json.loads(line) for line in sys.stdin.read().split("\n") if line.strip()]
install_guard(workspace)
namespace = {"__name__": "__main__" if mode == "stdin" else "__solution__"}
if mode == "stdin":
    exec(compile(code, "solution.py", "exec"), namespace)
else:
    from typing import *  # noqa: F401,F403 — LeetCode 시작 코드는 List·Optional 등을 import 없이 쓴다
    import collections, heapq, bisect, math, itertools, functools  # noqa: E401
    namespace.update({k: v for k, v in globals().items() if not k.startswith("_")})
    exec(compile(code, "solution.py", "exec"), namespace)
    result = getattr(namespace["Solution"](), func)(*args)
    sys.stdout.write("\n@@RESULT@@" + json.dumps(result))
'''


def _decimals(line: str):
    """한 줄의 수들을 Decimal 로. 수가 아니거나 nan·inf 가 있으면 None."""
    try:
        values = [Decimal(token) for token in line.split()]
    except InvalidOperation:
        return None
    return values if all(value.is_finite() for value in values) else None


def same(expected: str, actual: str) -> bool:
    """LiveCodeBench 공식 채점과 같은 비교: 줄 단위로 앞뒤 공백을 지우고 같으면 정답, 다르면 줄의 수들을 Decimal 로 바꿔
    **정확히** 같아야 정답(1 과 1.0 은 같음). 허용 오차는 두지 않는다.
    2026-10-12 Codex 지적: 예전 상대 오차 비교는 1000000 ↔ 1000001 과 1 ↔ nan 을 통과시켰다."""
    exp = [line.strip() for line in expected.strip().splitlines()]
    act = [line.strip() for line in actual.strip().splitlines()]
    if exp == act:
        return True
    if len(exp) != len(act):
        return False
    for e, a in zip(exp, act):
        if e == a:
            continue
        de, da = _decimals(e), _decimals(a)
        if de is None or da is None or de != da:
            return False
    return True


def judge(problem: dict, code: str) -> tuple[bool, int, str]:
    tests = tests_of(problem)
    func = json.loads(problem["metadata"] or "{}").get("func_name", "")
    with tempfile.TemporaryDirectory(prefix="alex-lcb-") as tmp:
        workspace = Path(tmp)
        (workspace / "solution.py").write_text(code, encoding="utf-8")
        (workspace / ".alex-tmp").mkdir()
        child = workspace / "child.py"
        child.write_text(f"ROOT = {str(ROOT)!r}\nWORKSPACE = {str(workspace)!r}\n" + CHILD, encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "LANG", "LC_ALL"}}
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", TEMP=str(workspace / ".alex-tmp"),
                   TMP=str(workspace / ".alex-tmp"), TMPDIR=str(workspace / ".alex-tmp"))
        for number, test in enumerate(tests, 1):
            mode = test.get("testtype", "stdin")
            try:
                result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(child), mode, func], input=test["input"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                                        cwd=workspace, env=env, timeout=TEST_SECONDS)
            except subprocess.TimeoutExpired:
                return False, len(tests), f"test {number}: time limit {TEST_SECONDS}s"
            if result.returncode != 0:
                return False, len(tests), f"test {number}: error {result.stderr.strip().splitlines()[-1][:120] if result.stderr.strip() else result.returncode}"
            if mode == "functional":
                marker = result.stdout.rfind("\n@@RESULT@@")
                if marker < 0:
                    return False, len(tests), f"test {number}: no result"
                try:
                    ok = json.loads(result.stdout[marker + 11:]) == json.loads(test["output"])
                except ValueError:
                    ok = False
            else:
                ok = same(test["output"], result.stdout)
            if not ok:
                return False, len(tests), f"test {number}: wrong answer"
    return True, len(tests), ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--since", default="2025-02-01")
    parser.add_argument("--difficulty", default="", help="easy·medium·hard 중 하나만(비우면 전부)")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--out", default=str(ROOT / "logs" / "eval" / "lcb-results.jsonl"))
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--url", default=URL)
    parser.add_argument("--export", default="", help="고른 문제의 프롬프트만 이 파일로 내보내고 끝(다른 모델에 같은 문제를 줄 때)")
    parser.add_argument("--answers", default="", help="다른 모델의 답 JSON {model, answers:[{question_id, reply}]} 을 같은 채점기로 채점")
    args = parser.parse_args()
    problems = [json.loads(line) for line in Path(args.data).open(encoding="utf-8")]
    problems = sorted((p for p in problems if p["contest_date"][:10] >= args.since
                       and (not args.difficulty or p["difficulty"] == args.difficulty)),
                      key=lambda p: (p["contest_date"], p["question_id"]))
    if args.limit:
        problems = problems[:args.limit]
    if args.export:
        Path(args.export).write_text(json.dumps({"system": SYSTEM, "selection": vars(args) | {"export": None},
            "problems": [{"question_id": p["question_id"], "platform": p["platform"], "difficulty": p["difficulty"],
                          "date": p["contest_date"][:10], "prompt": prompt_of(p)} for p in problems]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("exported", len(problems))
        return
    supplied = {}
    if args.answers:
        data = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        supplied = {str(a["question_id"]): a.get("reply", "") for a in data.get("answers", [])}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["question_id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    todo = [p for p in problems if p["question_id"] not in done]
    for problem in todo:
        record = {"question_id": problem["question_id"], "platform": problem["platform"], "difficulty": problem["difficulty"],
                  "date": problem["contest_date"][:10], "effort": args.effort}
        try:
            reply = ({"content": supplied.get(problem["question_id"], ""), "seconds": None, "tokens": None, "finish": "supplied"}
                     if args.answers else generate(prompt_of(problem), args.effort, args.max_tokens, args.timeout, args.url))
            blocks = CODE.findall(reply["content"])
            record.update(seconds=reply["seconds"], tokens=reply["tokens"], finish=reply["finish"])
            if not blocks:
                record.update(passed=False, reason="no code block")
            else:
                started = time.monotonic()
                passed, count, reason = judge(problem, blocks[-1])
                record.update(passed=passed, tests=count, reason=reason, judge_seconds=round(time.monotonic() - started, 1),
                              code=blocks[-1])
        except Exception as exc:  # 한 문제의 오류가 전체를 멈추지 않게
            record.update(passed=False, reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(f"{len(rows)}/{len(problems)} {problem['platform'][:4]} {problem['difficulty'][:4]} {problem['question_id']} "
              f"{'O' if record['passed'] else 'X'} {record.get('seconds')}s {record.get('tokens')}tok {record.get('reason', '')[:60]}"
              f" | 누적 {sum(r['passed'] for r in rows)}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
