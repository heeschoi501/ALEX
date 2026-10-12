"""Compare exact model IDs on the same 18 historical Python tasks.

No model is called by --dry-run. --execute opts into generation and guarded
execution. This is a one-shot model benchmark, not an autonomous agent benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.build_runner import install_guard
from jarvis.providers import Message, OllamaProvider, OpenAIResponsesProvider, ProviderError, StrataProvider
from scripts.grade_python import TASKS, HARD_TASKS, EXPERT_TASKS, extract_code

ALL_TASKS = TASKS + HARD_TASKS + EXPERT_TASKS
TASK_MAP = {task["name"]: task for task in ALL_TASKS}
SUITE_HASH = hashlib.sha256(json.dumps(ALL_TASKS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
SYSTEM = "요구사항을 만족하는 Python 표준 라이브러리 코드를 코드 블록 하나로 출력하십시오. 설명은 생략하십시오."


def export_prompts():
    return {"suite_sha256": SUITE_HASH, "system": SYSTEM,
            "tasks": [{"name": task["name"], "prompt": task["prompt"]} for task in ALL_TASKS]}


def worker(request_path: Path, evidence: Path):
    request = json.loads(request_path.read_text(encoding="utf-8"))
    task = TASK_MAP[request["task"]]
    workspace = request_path.parent.resolve()
    namespace = {"__name__": "__benchmark__"}
    with evidence.open("w", encoding="utf-8") as record:
        install_guard(workspace)
        try:
            fixture = compile(task.get("setup", ""), "<fixture>", "exec")
            candidate = compile(request["code"], "<candidate>", "exec")
            checks = compile(task["tests"], "<checks>", "exec")
            # Fix the checks before the candidate executes.
            execute = exec
            execute(fixture, namespace)
            execute(candidate, namespace)
            execute(checks, namespace)
        except BaseException as exc:
            json.dump({"passed": False, "detail": type(exc).__name__ + ": " + str(exc)[:160]}, record)
        else:
            json.dump({"passed": True, "detail": ""}, record)


def score_reply(reply: str, task_name: str, timeout: int = 15):
    if task_name not in TASK_MAP:
        raise ValueError("Unknown task: " + task_name)
    code, extraction = extract_code(reply)
    with tempfile.TemporaryDirectory(prefix="alex-model-check-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        request, evidence = workspace / "request.json", root / "evidence.json"
        request.write_text(json.dumps({"task": task_name, "code": code}), encoding="utf-8")
        try:
            child_env = {key: value for key, value in os.environ.items()
                         if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}}
            child_env.update(TMPDIR=str(workspace), TEMP=str(workspace), TMP=str(workspace))
            result = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()),
                                     "--worker", str(request), str(evidence)], cwd=workspace,
                                    env=child_env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"passed": False, "detail": "timeout", "extraction": extraction}
        try:
            verdict = json.loads(evidence.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            verdict = {"passed": False, "detail": "no completed check evidence"}
        verdict["passed"] = verdict.get("passed") is True and result.returncode == 0
        verdict["extraction"] = extraction
        return verdict


def provider_for(backend: str, model: str, host: str, timeout: int, output_tokens: int):
    if backend == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OPENAI_API_KEY is required; no model call was made.")
        return OpenAIResponsesProvider(key, model, timeout=timeout, max_output_tokens=output_tokens)
    cls = StrataProvider if backend == "strata" else OllamaProvider
    return cls(model, host=host or ("http://127.0.0.1:8080" if backend == "strata" else "http://127.0.0.1:11434"),
               timeout=timeout, num_ctx=16384, num_predict=output_tokens, think=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("openai", "strata", "ollama"))
    parser.add_argument("--model", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--answers", type=Path, help="JSON: model + answers [{task, reply, run}]")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--output-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--out", type=Path, default=ROOT / "logs/model-compare/results.json")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.output_tokens < 1 or args.timeout < 1:
        parser.error("runs, output-tokens and timeout must be positive")
    if args.dry_run:
        print(json.dumps(export_prompts(), ensure_ascii=False, indent=2))
        return 0
    if not args.execute:
        parser.error("Use --dry-run or explicitly opt into model/code execution with --execute")
    if bool(args.provider) == bool(args.answers):
        parser.error("Choose exactly one of --provider or --answers")
    supplied = {}
    if args.answers:
        data = json.loads(args.answers.read_text(encoding="utf-8"))
        if data.get("suite_sha256") != SUITE_HASH:
            parser.error("Answer suite hash does not match the current prompts and checks")
        model = str(data.get("model") or "").strip()
        for answer in data.get("answers", []):
            key = (answer["task"], int(answer.get("run", 1)))
            if key in supplied or key[0] not in TASK_MAP or not 1 <= key[1] <= args.runs:
                parser.error("Unknown, duplicate or out-of-range answer")
            supplied[key] = answer
        provider = None
    else:
        model = args.model or ("gpt-5.6-luna" if args.provider == "openai" else "")
        if not model:
            parser.error("Local comparisons require the exact --model ID")
        try:
            provider = provider_for(args.provider, model, args.host, args.timeout, args.output_tokens)
        except ValueError as exc:
            parser.error(str(exc))
    if not model:
        parser.error("An exact model label is required")
    output = {"suite_sha256": SUITE_HASH, "model": model, "provider": args.provider or "saved-answers",
              "identity": "requested ID" if provider else "caller-reported ID",
              "one_shot": True, "runs": args.runs, "output_tokens": args.output_tokens, "results": []}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for run in range(1, args.runs + 1):
        for task in ALL_TASKS:
            started = time.monotonic()
            try:
                missing = not provider and (task["name"], run) not in supplied
                reply = provider.generate([Message("system", SYSTEM), Message("user", task["prompt"])]) if provider else supplied.get((task["name"], run), {}).get("reply", "")
                seconds = round(time.monotonic() - started, 3) if provider else None
                verdict = ({"passed": False, "detail": "missing answer", "extraction": "missing-answer"}
                           if missing else score_reply(reply, task["name"]))
            except ProviderError as exc:
                reply, seconds = "", round(time.monotonic() - started, 3)
                verdict = {"passed": False, "detail": str(exc), "extraction": "generation-error"}
            output["results"].append({"task": task["name"], "run": run, "seconds": seconds,
                                      "reply": reply, **verdict})
            output["passed"] = sum(result["passed"] for result in output["results"])
            output["total"] = len(output["results"])
            output["unavailable"] = sum(result["extraction"] in {"generation-error", "missing-answer"}
                                        for result in output["results"])
            output["measurement_status"] = ("unmeasured" if output["unavailable"] == output["total"]
                                            else "partial" if output["unavailable"] else "complete")
            args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{model} {task['name']} run={run}: {'PASS' if verdict['passed'] else 'FAIL'}")
    print(f"{output['passed']}/{output['total']}; {args.out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit(main())
