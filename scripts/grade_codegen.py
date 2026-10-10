"""Grade local code generation by EXECUTING the model's output and scoring it.

`benchmark_local.py` measures speed. This measures correctness: it asks the model for a
`calc(expr)` function, runs the generated code, and scores 7 cases. It is the harness behind
`PERFORMANCE.md` section 8, which previously existed only as prose.

SECURITY — read before use
--------------------------
This script executes code written by a language model. That is exactly what `SECURITY.md`
forbids *the application* from doing on a user's behalf. The boundary this respects:

- The app never executes generated code. That remains true and is untouched.
- This is a manual measurement tool, run by a developer who typed the command, like
  `benchmark_local.py`. It is not imported by the app, not wired into CI, and refuses to
  run generated code unless `--execute` is passed explicitly.
- Generated code runs in a separate `python` subprocess with a wall-clock timeout, so a
  hang or `sys.exit` cannot take down this process.

It is still not a sandbox. The subprocess has the same privileges as you. Run it only
against your own local models.

Examples:
  python scripts/grade_codegen.py --dry-run          # show prompt/cases, generate nothing
  python scripts/grade_codegen.py --execute          # 1 run, current config
  python scripts/grade_codegen.py --execute -n 5     # 5 runs, report mean
  python scripts/grade_codegen.py --execute --no-coding-prompt --model qwen2.5-coder:7b
  python scripts/grade_codegen.py --execute -n 10 --think --repeat-penalty 1.2
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The task prompt is Korean; the Windows console defaults to cp949 and would raise.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
        pass

from jarvis.app import NUM_CTX_BY_PROFILE, apply_profile_override, effective_local_models
from jarvis.config import load_settings
from jarvis.core import CODING_SYSTEM_ADDENDUM
from jarvis.hardware import detect_hardware

TASK = (
    "파이썬으로 `calc(expr)` 함수 하나를 작성하십시오. 문자열 수식을 받아 숫자를 반환합니다.\n"
    "다음을 지원해야 합니다: 사칙연산, `**`, `sqrt`, `log`(상용로그, 밑 10), `ln`(자연로그),\n"
    "`factorial`, 그리고 `sin`/`cos`/`tan` — **삼각함수의 인자는 도(degree) 단위입니다.**\n"
    "코드 블록 하나만 출력하고 설명은 붙이지 마십시오. 함수 이름은 반드시 `calc` 입니다."
)

# (수식, 기대값). PERFORMANCE.md 8절의 7케이스와 같습니다.
CASES: list[tuple[str, float]] = [
    ("1+2", 3.0),
    ("sqrt(9)", 3.0),
    ("sin(30)", 0.5),        # 도 단위. 가장 많이 틀리는 케이스입니다.
    ("log(100)", 2.0),       # 상용로그
    ("2**10", 1024.0),
    ("factorial(5)", 120.0),
    ("ln(1)", 0.0),
]

# 생성 코드를 채점하는 러너. 케이스마다 따로 감싸 하나가 죽어도 나머지를 계속 봅니다.
RUNNER = '''
import json, sys, math
_RESULTS = []
{code}
_CASES = {cases!r}
for _expr, _want in _CASES:
    try:
        _got = calc(_expr)
        _ok = isinstance(_got, (int, float)) and abs(float(_got) - _want) < 1e-6
        _RESULTS.append({{"expr": _expr, "ok": bool(_ok), "got": repr(_got)}})
    except Exception as _e:
        _RESULTS.append({{"expr": _expr, "ok": False, "got": f"{{type(_e).__name__}}: {{_e}}"}})
sys.stdout.write("@@GRADE@@" + json.dumps(_RESULTS, ensure_ascii=False))
'''


_FENCE_OPEN = re.compile(r"```(?:python|py)?[^\S\n]*\n", re.IGNORECASE)
_DEF_CALC = re.compile(r"def\s+calc\s*\(")


def extract_code(text: str) -> tuple[str, str]:
    """모델 응답에서 파이썬 코드를 꺼냅니다. (코드, 추출방식) 을 돌려줍니다.

    추출방식을 함께 돌려주는 이유는 2026-09-19 측정에서 드러났다. 예산이 소진돼 응답이
    코드 블록 중간에서 잘리면 닫는 ``` 가 없다. 예전 구현은 그때 본문 전체를 그대로
    반환했고, 맨 앞의 ```python 줄 때문에 **1행에서 SyntaxError** 가 났다. 진짜 원인인
    잘림이 문법 오류로 가려져, 실패 사유 집계가 거짓이 됐다.
    """
    closed = re.findall(r"```(?:python|py)?[^\S\n]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if closed:
        # calc 를 정의한 블록을 고릅니다. 모델이 사용 예시를 따로 내는 경우가 많습니다.
        for block in closed:
            if _DEF_CALC.search(block):
                return block, "fenced"
        return closed[0], "fenced"

    # 닫히지 않은 펜스: 마지막 여는 펜스 뒤를 코드로 봅니다. 잘렸어도 그때까지 쓴 코드는
    # 살려서 채점해야 '무엇이 틀렸는지' 가 남습니다.
    opens = list(_FENCE_OPEN.finditer(text))
    if opens:
        return text[opens[-1].end():], "unclosed-fence"

    if _DEF_CALC.search(text):
        return text, "bare"
    return text, "no-code"


# 2026-09-19 측정에서 think ON 실패 2건이 전부 '생각만 하다 예산 소진' 이었다
# (thinking 29,772자 / 32,046자 대 성공 평균 14,662자, 코드 0자, repetition 0%).
# CODING_SYSTEM_ADDENDUM 의 "코드를 내놓기 전에 예시를 하나씩 되짚어 본다" 가 그 방향을
# 부추긴다고 보고, 검산을 코드 뒤로 미루는 지시를 따로 만들어 효과를 잰다.
BRIEF_THINKING_ADDENDUM = (
    "추론은 짧게 한다.\n"
    "- 접근 방법을 정했으면 더 따지지 말고 곧바로 코드를 작성한다.\n"
    "- 같은 점검을 두 번 반복하지 않는다. 대안을 여러 개 비교하지 않는다.\n"
    "- 검산은 코드를 다 쓴 뒤에 한 번만 한다. 코드를 쓰기 전에 하지 않는다."
)


def brief_already_applied() -> bool:
    """앱의 코딩 지시에 ADR-046 간결 지시가 이미 들어가 있는지."""
    return all(line.strip() in CODING_SYSTEM_ADDENDUM
               for line in BRIEF_THINKING_ADDENDUM.splitlines() if line.strip())


def build_messages(coding_prompt: bool, history: list[dict] | None = None,
                   brief: bool = False) -> list[dict]:
    messages: list[dict] = []
    if coding_prompt:
        content = CODING_SYSTEM_ADDENDUM
        # ADR-046 채택으로 이 지시는 앱의 CODING_SYSTEM_ADDENDUM 에 들어갔다. 두 번 붙이면
        # 측정 대상이 달라지므로, 이미 있으면 --brief-thinking 은 아무 것도 하지 않는다.
        if brief and not brief_already_applied():
            content = content + "\n\n" + BRIEF_THINKING_ADDENDUM
        messages.append({"role": "system", "content": content})
    elif brief:
        messages.append({"role": "system", "content": BRIEF_THINKING_ADDENDUM})
    messages.append({"role": "user", "content": TASK})
    messages.extend(history or [])
    return messages


def refine_prompt(results: list[dict]) -> str:
    """실패한 케이스만 모아 모델에게 돌려줄 오류 보고를 만든다."""
    lines = ["작성하신 코드를 실제로 실행했더니 다음 케이스가 틀렸습니다.", ""]
    for r in results:
        if not r["ok"]:
            lines.append(f"  calc({r['expr']!r}) -> {r['got']}")
    lines += [
        "",
        "원인을 고쳐 `calc` 전체를 다시 작성하십시오. 코드 블록 하나만 출력하십시오.",
        "특히 삼각함수 인자는 도(degree) 단위이고, log 는 밑 10 입니다.",
    ]
    return "\n".join(lines)


def generate(host: str, model: str, num_ctx: int, num_predict: int, think: bool,
             messages: list[dict], timeout: int, options: dict | None = None) -> tuple[str, dict]:
    """모델을 한 번 호출합니다. (본문, 진단) 을 돌려줍니다.

    진단을 함께 돌려주는 이유: 예전에는 content 만 받아서, 실패했을 때 잘린 것인지
    모델이 틀린 코드를 쓴 것인지 구분할 수 없었다. Ollama 는 done_reason 으로 그걸
    알려준다 — "length" 면 num_predict 소진이고 "stop" 이면 모델이 스스로 끝낸 것이다.
    추측하지 않고 이 값을 본다.
    """
    opts = {"num_ctx": num_ctx, "num_predict": num_predict}
    opts.update(options or {})
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "keep_alive": "10m",
        "options": opts,
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        item = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    message = item.get("message") or {}
    content = str(message.get("content") or "")
    thinking = str(message.get("thinking") or "")
    diag = {
        "seconds": round(elapsed, 2),
        "done_reason": str(item.get("done_reason") or ""),
        "eval_count": item.get("eval_count"),
        "prompt_eval_count": item.get("prompt_eval_count"),
        "thinking_chars": len(thinking),
        "content_chars": len(content),
    }
    return content, diag


def repetition_ratio(code: str) -> float:
    """비어 있지 않은 줄 가운데 중복이 차지하는 비율입니다.

    2026-09-18 실패 두 건이 같은 re.sub 줄을 208번/134번 반복하다 예산을 소진했다.
    예산을 올려도 더 오래 반복할 뿐이라, 이것은 잘림과 구분해서 세야 하는 실패다.
    """
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    if not lines:
        return 0.0
    return round(1.0 - len(set(lines)) / len(lines), 3)


def grade(code: str, exec_timeout: int) -> list[dict]:
    """생성 코드를 별도 프로세스에서 실행해 채점합니다."""
    if not _DEF_CALC.search(code):
        # 예전에는 이 경우도 실행해서 케이스마다 NameError 를 냈다. 코드가 아예 없는 것과
        # 코드는 있는데 틀린 것은 전혀 다른 실패라 사유를 구분해 적는다.
        reason = "no code emitted" if not code.strip() else "no def calc() in output"
        return [{"expr": e, "ok": False, "got": reason} for e, _ in CASES]
    script = RUNNER.format(code=textwrap.dedent(code), cases=CASES)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.py"
        path.write_text(script, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True, timeout=exec_timeout, cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return [{"expr": e, "ok": False, "got": "timeout"} for e, _ in CASES]
    marker = proc.stdout.find("@@GRADE@@")
    if marker < 0:
        # import 실패나 문법오류로 아예 뜨지 못한 경우입니다.
        reason = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return [{"expr": e, "ok": False, "got": reason} for e, _ in CASES]
    return json.loads(proc.stdout[marker + len("@@GRADE@@"):])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--execute", action="store_true",
                   help="actually run the generated code and grade it (required to score)")
    p.add_argument("--dry-run", action="store_true", help="show prompt and cases without calling the model")
    p.add_argument("-n", "--runs", type=int, default=1,
                   help="repeat count (default 1). Never judge a model from a single sample.")
    p.add_argument("--model", default="", help="defaults to the configured coder model")
    p.add_argument("--num-ctx", type=int, default=0, help="0 follows config/profile")
    p.add_argument("--num-predict", type=int, default=0,
                   help="0 follows config (local_max_output_tokens); raise it when thinking "
                        "eats the budget before the code is emitted")
    p.add_argument("--no-coding-prompt", action="store_true", help="measure without CODING_SYSTEM_ADDENDUM")
    p.add_argument("--think", action="store_true", help="enable thinking mode (ADR-034: off by default)")
    p.add_argument("--refine", type=int, default=0, metavar="N",
                   help="feed failures back and regenerate up to N times (ADR-036 measurement)")
    p.add_argument("--host", default="", help="defaults to configured ollama_host")
    p.add_argument("--timeout", type=int, default=0, help="0 uses configured request_timeout")
    p.add_argument("--brief-thinking", action="store_true",
                   help="append BRIEF_THINKING_ADDENDUM. Since ADR-046 was adopted this is "
                        "already part of the app's coding prompt, so the flag is a no-op there; "
                        "it still applies with --no-coding-prompt.")
    p.add_argument("--repeat-penalty", type=float, default=0.0,
                   help="Ollama repeat_penalty (0 keeps Ollama's default 1.1). Raise it when "
                        "failures show a high repetition ratio; too high degrades code.")
    p.add_argument("--seed", type=int, default=None,
                   help="Ollama seed. Without it runs are not reproducible, which is why "
                        "single-run comparisons of this benchmark are meaningless.")
    p.add_argument("--temperature", type=float, default=None, help="Ollama temperature")
    p.add_argument("--exec-timeout", type=int, default=10)
    p.add_argument("--out", default="", help="result JSON path (default logs/codegen-grade-<ts>.json)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    settings = load_settings()
    hardware = detect_hardware()
    apply_profile_override(hardware, settings.force_profile)
    profile = hardware.profile
    _general, coder = effective_local_models(settings, profile)
    model = args.model or coder
    num_ctx = args.num_ctx or settings.num_ctx or NUM_CTX_BY_PROFILE[profile]
    num_predict = args.num_predict or settings.local_max_output_tokens
    coding_prompt = not args.no_coding_prompt
    host = args.host or settings.ollama_host
    timeout = args.timeout or settings.request_timeout

    sampling: dict = {}
    if args.repeat_penalty:
        sampling["repeat_penalty"] = args.repeat_penalty
    if args.seed is not None:
        sampling["seed"] = args.seed
    if args.temperature is not None:
        sampling["temperature"] = args.temperature

    extra = "".join(f" {k}={v}" for k, v in sampling.items())
    if args.brief_thinking:
        extra += " brief_thinking=on"
    print(f"model={model} num_ctx={num_ctx} num_predict={num_predict} "
          f"think={'on' if args.think else 'off'} coding_prompt={'on' if coding_prompt else 'off'}"
          f"{extra}")
    print(f"{len(CASES)} cases: " + ", ".join(e for e, _ in CASES))

    if args.dry_run:
        print("\n--- task prompt ---")
        print(TASK)
        return 0

    if not args.execute:
        print("\nRefusing to grade without --execute: grading runs model-written code.")
        print("Read the SECURITY note at the top of this file, then pass --execute.")
        return 2

    runs: list[dict] = []
    for i in range(1, args.runs + 1):
        history: list[dict] = []
        trajectory: list[int] = []
        diagnostics: list[dict] = []
        total_seconds = 0.0
        code = ""
        results: list[dict] = []
        failed = False
        # attempt 0 은 최초 생성, 이후는 오류 되먹임 재생성입니다 (ADR-036).
        for attempt in range(args.refine + 1):
            try:
                text, diag = generate(host, model, num_ctx, num_predict, args.think,
                                      build_messages(coding_prompt, history, args.brief_thinking),
                                      timeout, sampling)
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                print(f"  run {i} attempt {attempt}: generation failed: {exc}")
                runs.append({"run": i, "error": str(exc), "score": 0})
                failed = True
                break
            elapsed = diag["seconds"]
            total_seconds += elapsed
            code, how = extract_code(text)
            diag["extraction"] = how
            diag["repetition"] = repetition_ratio(code)
            diagnostics.append(diag)
            results = grade(code, args.exec_timeout)
            score = sum(1 for r in results if r["ok"])
            trajectory.append(score)
            label = "gen " if attempt == 0 else f"fix{attempt}"
            print(f"  run {i} {label}: {score}/{len(CASES)}  ({elapsed:.1f}s)")
            if score < len(CASES):
                # 왜 실패했는지를 추측이 아니라 값으로 남깁니다.
                print(f"      why: done={diag['done_reason'] or '?'} extract={how} "
                      f"repeat={diag['repetition']:.0%} "
                      f"think_chars={diag['thinking_chars']} code_chars={len(code)}")
            for r in results:
                print(f"      {'PASS' if r['ok'] else 'FAIL'}  {r['expr']:<14} -> {r['got']}")
            if score == len(CASES) or attempt == args.refine:
                break
            history.append({"role": "assistant", "content": text})
            history.append({"role": "user", "content": refine_prompt(results)})
        if failed:
            continue
        runs.append({"run": i, "seconds": round(total_seconds, 2), "score": trajectory[-1],
                     "trajectory": trajectory, "attempts": len(trajectory),
                     "results": results, "code": code,
                     "diagnostics": diagnostics, "raw": text})
        if len(trajectory) > 1:
            print(f"  run {i}: {' -> '.join(f'{t}/{len(CASES)}' for t in trajectory)}")

    scored = [r for r in runs if "score" in r]
    mean = sum(r["score"] for r in scored) / len(scored) if scored else 0.0
    perfect = sum(1 for r in scored if r["score"] == len(CASES))
    print(f"\nmean {mean:.1f}/{len(CASES)}  perfect {perfect}/{len(scored)} runs")

    # 실패를 원인별로 셉니다. 평균 점수만으로는 '예산이 모자랐다' 와 '모델이 틀렸다' 가
    # 구분되지 않고, 그 둘은 고치는 방법이 서로 다릅니다.
    buckets: dict[str, int] = {}
    for r in scored:
        if r["score"] == len(CASES):
            continue
        d = (r.get("diagnostics") or [{}])[-1]
        if d.get("repetition", 0) >= 0.5:
            cause = "repetition loop"
        elif d.get("done_reason") == "length":
            cause = "budget exhausted"
        elif not r.get("code", "").strip():
            cause = "no code emitted"
        else:
            cause = "wrong code"
        buckets[cause] = buckets.get(cause, 0) + 1
    for r in runs:
        if "error" in r:
            buckets["request failed"] = buckets.get("request failed", 0) + 1
    if buckets:
        print("failures by cause: " + ", ".join(f"{k} {v}" for k, v in sorted(buckets.items())))
    if args.refine:
        firsts = [r["trajectory"][0] for r in scored if r.get("trajectory")]
        if firsts:
            first_mean = sum(firsts) / len(firsts)
            print(f"first attempt mean {first_mean:.1f}/{len(CASES)} "
                  f"-> after {args.refine} refinement(s) {mean:.1f}/{len(CASES)}")

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model, "num_ctx": num_ctx, "num_predict": num_predict,
        "think": args.think, "coding_prompt": coding_prompt, "refine": args.refine,
        "sampling": sampling, "brief_thinking": args.brief_thinking,
        "cases": [e for e, _ in CASES],
        "mean_score": round(mean, 2), "perfect_runs": perfect, "total_runs": len(scored),
        "runs": runs,
    }
    out = Path(args.out) if args.out else ROOT / "logs" / f"codegen-grade-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
