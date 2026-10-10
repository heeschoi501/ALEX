"""코딩 에이전트를 실제 ALEX 코드로 잰다(4단계 문서 5번) — 2026-09-26 claude.

ALEX 복사본에 오늘·어제 실제로 고친 버그를 되살려 넣고, 채팅 명령 그대로 /dev-start → /dev-auto → /apply-edit →
/dev-next 로 고치게 한 뒤, 복사본의 전체 unittest 로 회귀를 본다. 사용자의 ALEX 는 건드리지 않는다(복사본·임시 DB).
모델이 볼 수 있는 파일은 문맥 예산(데스크톱 24,000자) 안의 작은 모듈뿐이다 — core·app·storage·web 은 아직 못 다룬다.

    python -X utf8 logs/probe/coding_agent_eval.py --model qwen3.8:27b [--only router_shortcut] [--set small|large|all]
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.app import build_core, handle_command  # noqa: E402
from jarvis.config import load_settings, validate  # noqa: E402
from jarvis.developer import _isolated_copy, _suite_digest, _suite_report  # noqa: E402
from jarvis.workspace import SafeWorkspaceEditor  # noqa: E402

GOAL = "테스트가 통과하도록 원인을 찾아 최소한으로 고쳐라"
TASKS = [
    {"id": "router_shortcut", "file": "jarvis/router.py", "test": "tests/test_router.py",
     "find": "    hit = _find_any(normalized, SYSTEM_INFO_WORDS) if shortcut else None\n",
     "replace": "    hit = _find_any(normalized, SYSTEM_INFO_WORDS)\n"},
    {"id": "launcher_fa", "file": "jarvis/launcher.py", "test": "tests/test_launcher_ollama_env.py",
     "find": "any(model.startswith(FA_CRASH_FAMILIES) for model in models)",
     "replace": "all(model.startswith(FA_CRASH_FAMILIES) for model in models)"},
    {"id": "lessons_stem", "file": "jarvis/lessons.py", "test": "tests/test_lessons.py",
     "find": "    if pieces[0] not in terms:\n        return False\n", "replace": ""},
    {"id": "webread_ipv4_mapped", "file": "jarvis/webread.py", "test": "tests/test_webread.py",
     "find": "    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:\n"
             "        address = address.ipv4_mapped  # ::ffff:127.0.0.1 로 돌아 들어오는 경로를 막는다\n",
     "replace": ""},
]
# 문맥 예산(약 24,000자)보다 큰 파일 — 발췌 모드(ADR-067)가 있어야 풀 수 있다.
TASKS_LARGE = [
    {"id": "core_truncation_route", "file": "jarvis/core.py", "test": "tests/test_truncation_notice.py",
     "find": "        provider = self.providers.get(destination) if destination else None\n"
             "        metrics = getattr(provider, \"last_metrics\", None)\n        if not isinstance(metrics, dict):\n",
     "replace": "        provider = self.providers.get(Destination.LOCAL_GENERAL) if destination else None\n"
                "        metrics = getattr(provider, \"last_metrics\", None)\n        if not isinstance(metrics, dict):\n"},
    {"id": "developer_any_test_fails", "file": "jarvis/developer.py", "test": "tests/test_developer.py",
     "find": "            if code != 0 and worst == 0:\n                worst = code\n", "replace": ""},
    {"id": "workspace_empty_new_file", "file": "jarvis/workspace.py", "test": "tests/test_workspace_bundle.py",
     "find": "                self._check_text(relative, content, allow_empty=True)\n",
     "replace": "                self._check_text(relative, content)\n"},
    {"id": "web_delete_forgets_exchange", "file": "jarvis/web.py", "test": "tests/test_local_beta.py",
     "find": "        if (self.core.last_exchange or {}).get(\"session_id\") == target:\n"
             "            self.core.last_exchange = None  # 지운 대화의 질문·답을 메모리에도 남기지 않는다\n",
     "replace": ""},
]


def tree_hash(root: Path) -> str:
    """스냅샷의 소스·테스트 지문 — 결과마다 어떤 코드로 쟀는지 남긴다(2026-09-30 Codex 리뷰)."""
    digest = hashlib.sha256()
    for path in sorted(p for folder in ("jarvis", "tests") for p in (root / folder).rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


class EvaluationUnverified(RuntimeError):
    """검사를 완료했다는 증거가 없으면 성공/버그 감지로 집계하지 않는다."""


def test_patterns(value: str) -> list[str]:
    patterns = []
    for item in value.split(","):
        match = re.fullmatch(r"(?:tests/)?(test_[A-Za-z0-9_]+\.py)", item.strip())
        if not match or match[1] in patterns:
            raise ValueError("합격 검사는 중복 없는 tests/test_name.py 쉼표 목록이어야 합니다")
        patterns.append(match[1])
    return patterns


def unittest_failures(project: Path, pattern: str | None = None) -> tuple[int, set[str]]:
    if pattern is not None:
        patterns = test_patterns(pattern)
        if len(patterns) > 1:
            reports = [unittest_failures(project, item) for item in patterns]
            return max(code for code, _ in reports), set().union(*(failed for _, failed in reports))
        pattern = patterns[0]
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    if pattern:  # 2026-09-29 고침: 전에는 [5:5] 라 "-s -p 패턴 tests" 가 되어 사용법 오류(종료 코드 2) — 버그 감지가 늘 참이었다
        command[4:4] = ["-p", pattern]
    done = subprocess.run(command, cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=900)
    status, failed, reason = _suite_report(done.returncode, _suite_digest(done.stdout + done.stderr))
    if status == "unreadable":
        raise EvaluationUnverified(f"{pattern or 'full suite'}: {reason}")
    return done.returncode, set(failed)


def strict_success(row: dict) -> bool:
    """테스트 통과와 정상적인 제한 내 풀이 완료를 모두 요구한다."""
    return (row.get("bug_detected") is True and row.get("acceptance_passed") is True
            and row.get("suite_exit") == 0 and row.get("new_failures") == []
            and (row.get("solver", "alex") != "opencode"
                 or (row.get("solver_exit") == 0 and row.get("solver_timed_out") is False
                     and row.get("scope_violations") == [])))



# 공개 코딩 에이전트 비교(2026-10-04 claude, 사용자 "이미 다른 사람들이 남겨 둔 거 있지 않나?"): 같은 과제·같은 판정으로
# OpenCode(모델이 파일 읽기·검색·편집·명령 실행을 스스로 하는 도구 루프)를 ALEX /dev-auto 와 비교한다.
# 복사본에서 실행하며 허용 파일 밖 최종 변경은 실격이다. 복사본은 OS 보안 샌드박스가 아니다.
OPENCODE_DIR = Path(r"C:\ALEX\tools\opencode")
OPENCODE_PROMPT = ("이 프로젝트의 {file} 에 버그가 있어 {test} 의 테스트가 실패한다. 목표: {goal}.\n"
                   "합격 테스트 명령(프로젝트 폴더에서 모두 실행):\n{test_commands}\n"
                   "규칙: tests/ 아래 파일은 고치지 마라. 고칠 파일은 {file} 이다. 다른 테스트를 깨뜨리지 마라. "
                   "인터넷을 쓰지 마라. 고친 뒤 합격 테스트를 다시 돌려 통과를 확인하고 끝내라.")


def _file_hashes(root: Path) -> dict[str, str]:
    skip = {"__pycache__", ".git", ".opencode", "node_modules"}
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file() and not skip.intersection(path.relative_to(root).parts)}


def _kill_tree(process: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


def run_opencode(project: Path, task: dict, args, work: Path) -> dict:
    """OpenCode 를 격리 복사본에서 한 번 돌린다. 판정은 부른 쪽(ALEX 와 같은 unittest_failures)이 한다."""
    before = _file_hashes(project)
    (work / "oc-data").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OPENCODE_CONFIG=str(OPENCODE_DIR / "opencode.json"),
               OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_MODELS_FETCH="1", OPENCODE_DISABLE_LSP_DOWNLOAD="1",
               OPENCODE_DISABLE_SHARE="1", OPENCODE_GIT_BASH_PATH=r"C:\Program Files\Git\bin\bash.exe",
               XDG_CONFIG_HOME=str(OPENCODE_DIR / "xdg-config"), XDG_CACHE_HOME=str(OPENCODE_DIR / "xdg-cache"),
               XDG_DATA_HOME=str(work / "oc-data"), XDG_STATE_HOME=str(work / "oc-data"),
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("SHELL", None)  # 비우지 않으면 WSL bash 를 고를 수 있다
    commands = "\n".join(f"python -m unittest discover -s tests -p {name}" for name in test_patterns(task["test"]))
    prompt = OPENCODE_PROMPT.format(file=task["file"], test=task["test"], test_commands=commands, goal=GOAL)
    log_path = work / "opencode-events.jsonl"
    started = time.monotonic()
    with log_path.open("wb") as log:
        process = subprocess.Popen([str(OPENCODE_DIR / "opencode.exe"), "run", "--model", args.opencode_model,
                                    "--format", "json", prompt], cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            exit_code = process.wait(timeout=args.solver_timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            exit_code, timed_out = process.wait(), True
    after = _file_hashes(project)
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    tests_touched = [path for path in changed if path.startswith("tests/")]
    events: dict[str, int] = {}
    tools: dict[str, int] = {}
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events[str(event.get("type"))] = events.get(str(event.get("type")), 0) + 1
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            if part.get("tool"):
                tools[str(part["tool"])] = tools.get(str(part["tool"]), 0) + 1
    # 프롬프트만으로 범위를 강제할 수 없다. 테스트·설정·다른 소스 편집으로 얻은 통과는 비교에서 제외한다.
    violations = [path for path in changed if path != task["file"]]
    if not (project / task["file"]).is_file():
        violations.append(task["file"] + " (target missing)")
    evidence_log = ""
    if args.out:
        evidence = Path(args.out + ".evidence")
        evidence.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(task["id"].encode()).hexdigest()[:16]
        saved = evidence / f"{key}-opencode-events.jsonl"
        shutil.copyfile(log_path, saved)
        evidence_log = str(saved.resolve())
    return {"solver_exit": exit_code, "solver_timed_out": timed_out, "solver_seconds": round(time.monotonic() - started, 1),
            "changed_files": changed, "tests_touched": tests_touched, "event_counts": events, "tool_counts": tools,
            "scope_violations": violations, "solver_log": evidence_log, "solver_log_tail": raw[-1500:]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--set", choices=("small", "large", "all"), default="small")
    parser.add_argument("--tasks-file", default="", help="mutation_tasks.py 가 만든 과제(JSON) — --set 대신")
    parser.add_argument("--split", choices=("train", "test", "all"), default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--outcomes-out", default="", help="과제마다 앱의 결과 기록(dev-outcomes)을 이 JSONL 에 모은다(교훈 추출 재료)")
    parser.add_argument("--insights", default="", help="이 절차 교훈 파일(coding-insights.json)을 넣고 교훈 붙이기를 켠다(ADR-070 실험)")
    parser.add_argument("--api-map", action="store_true", help="수정안 요청에 쓰는 객체의 API 시그니처를 붙인다(ADR-072 실험)")
    parser.add_argument("--num-gpu", type=int, default=0, help="이 평가에서만 이 모델에 GPU 층 수(27b 최적 54, PERFORMANCE 29). 사용자 설정은 그대로")
    # ADR-076(2026-10-03 claude): 코딩 수정안만 긴 문맥으로. 0 이면 사용자 설정(jarvis.local.json)의 coding_* 값을 그대로 쓴다.
    parser.add_argument("--coding-num-ctx", type=int, default=0, help="코딩 수정안의 문맥(토큰), 예: 65536")
    parser.add_argument("--coding-num-gpu", type=int, default=0, help="코딩 문맥에서 GPU 층 수(0 = Ollama 자동)")
    parser.add_argument("--coding-context-chars", type=int, default=0,
                        help="코딩 수정안의 글자 예산(0 = 대화 예산을 문맥 비율만큼). core.py 통째로는 약 12만 자 + 지시")
    parser.add_argument("--auto-attempts", type=int, default=0, help="/dev-auto 독립 풀이 횟수(ADR-077, 0 = 사용자 설정)")
    parser.add_argument("--regression-check", action="store_true", help="/dev-auto 회귀 검사를 켠다(ADR-078)")
    parser.add_argument("--diagnostics", action="store_true", help="/dev-auto 읽기 전용 진단 근거를 켠다(ADR-080)")
    parser.add_argument("--solver", choices=("alex", "opencode"), default="alex",
                        help="누가 고치나: ALEX /dev-auto(기본) 또는 공개 에이전트 OpenCode(2026-10-04 비교)")
    parser.add_argument("--opencode-model", default="ollama/qwen3.8-alex48k", help="OpenCode 에 줄 모델(provider/model)")
    parser.add_argument("--solver-timeout", type=int, default=2700, help="공개 에이전트 한 과제 상한(초)")
    args = parser.parse_args()
    tasks = {"small": TASKS, "large": TASKS_LARGE, "all": TASKS + TASKS_LARGE}[args.set]
    if args.tasks_file:
        tasks = [t for t in json.loads(Path(args.tasks_file).read_text(encoding="utf-8"))
                 if args.split == "all" or t.get("split") == args.split]
    if args.limit:
        tasks = tasks[:args.limit]
    if args.only:
        tasks = [task for task in tasks if task["id"] == args.only]
    if not tasks:
        parser.error("선택된 과제가 없습니다")
    # 2026-10-04 밤 결정: 공개 도구 전체 비교 취소. 로컬 호환성 확인 한 과제만 허용한다.
    if args.solver == "opencode" and len(tasks) != 1:
        parser.error("OpenCode는 한 과제 확인만 지원합니다: --only TASK 또는 --limit 1")
    for task in tasks:
        test_patterns(task["test"])
    results = []
    # 고정 스냅샷: 처음 한 번 떠서 기준선과 모든 과제가 쓴다 — 과제마다 살아 있는 ROOT 를 복사하면 실험 중 코드가 바뀔 때
    # 한 결과 파일 안에 여러 버전이 섞였다(2026-09-30 Codex 리뷰). 지문은 행마다 source_hash 로 남긴다.
    snapshot_dir = tempfile.TemporaryDirectory(prefix="alex-eval-snapshot-", ignore_cleanup_errors=True)
    snapshot = Path(snapshot_dir.name) / "project"
    _isolated_copy(ROOT, snapshot)
    source_hash = tree_hash(snapshot)
    print(f"snapshot {source_hash[:16]} (jarvis/ + tests/)", flush=True)
    # 설정·교훈도 시작할 때 한 번만 읽고, 실행 조건 전체를 manifest 로 남긴다(Codex 2차 E2-01 — source_hash 는 수정 대상 소스의 지문일
    # 뿐이다. 평가하는 에이전트 코드는 이 프로세스가 시작할 때 ROOT 에서 import 한 것이다).
    base_settings = load_settings()
    coding = {key: value for key, value in (("coding_num_ctx", args.coding_num_ctx), ("coding_num_gpu_layers", args.coding_num_gpu),
                                            ("coding_context_char_budget", args.coding_context_chars),
                                            ("coding_auto_attempts", args.auto_attempts)) if value}
    if args.regression_check:
        coding["coding_regression_check"] = True
    if args.diagnostics:
        coding["coding_diagnostic_context"] = True
    if coding:
        base_settings = dataclasses.replace(base_settings, **coding)
        validate(base_settings)
    insights_bytes = Path(args.insights).read_bytes() if args.insights else b""
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    manifest = {"source_hash": source_hash, "harness_sha256": sha(Path(__file__).read_bytes()),
                "tasks_sha256": sha(Path(args.tasks_file).read_bytes()) if args.tasks_file else f"builtin:{args.set}",
                "insights_sha256": sha(insights_bytes) if insights_bytes else "",
                # 요청한 값(설정 원시값). 실제로 쓴 값은 첫 과제의 core 에서 effective 로 덧붙인다(Codex 3차 R3-03)
                "requested": {"model": args.model, "num_gpu_arg": args.num_gpu, "api_map": args.api_map,
                              "num_ctx_setting": base_settings.desktop_num_ctx or base_settings.num_ctx,
                              "think_setting": base_settings.ollama_think,
                              "max_output_setting": base_settings.desktop_max_output_tokens or base_settings.local_max_output_tokens,
                              "coding_num_ctx": base_settings.coding_num_ctx,
                              "coding_num_gpu": base_settings.coding_num_gpu_layers,
                              "coding_context_chars": base_settings.coding_context_char_budget,
                              "auto_attempts": base_settings.coding_auto_attempts,
                              "regression_check": base_settings.coding_regression_check,
                              "diagnostics": base_settings.coding_diagnostic_context,
                              "solver": args.solver,
                              **({"opencode_model": args.opencode_model, "solver_timeout": args.solver_timeout,
                                  "opencode_sha256": sha((OPENCODE_DIR / "opencode.exe").read_bytes()),
                                  "opencode_config_sha256": sha((OPENCODE_DIR / "opencode.json").read_bytes()),
                                  "prompt": OPENCODE_PROMPT} if args.solver == "opencode" else {})},
                "python": sys.version.split()[0], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    manifest_id = sha(json.dumps(manifest, sort_keys=True).encode())[:16]
    print(f"manifest {manifest_id}: {json.dumps(manifest, ensure_ascii=False)}", flush=True)
    if args.out:
        Path(args.out + ".manifest.json").write_text(json.dumps({"manifest_id": manifest_id, **manifest}, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="alex-eval-base-", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp) / "project"
        _isolated_copy(snapshot, base)
        code, baseline_failed = unittest_failures(base)
        print(f"baseline copy: suite exit={code}, failing={sorted(baseline_failed)}", flush=True)
    for task in tasks:
        if args.only and task["id"] != args.only:
            continue
        row = {"task": task["id"], "model": args.opencode_model if args.solver == "opencode" else args.model, "solver": args.solver,
               "source_hash": source_hash, "manifest": manifest_id,
               **{k: task[k] for k in ("kind", "split", "baseline_id", "dataset") if k in task}}
        with tempfile.TemporaryDirectory(prefix="alex-eval-", ignore_cleanup_errors=True) as tmp:
            project = Path(tmp) / "project"
            _isolated_copy(snapshot, project)
            target = project / task["file"]
            source = target.read_text(encoding="utf-8")
            # 원래 실패하던 합격 검사는 변이가 잡혔다는 증거가 될 수 없다.
            clean_code, clean_failed = unittest_failures(project, task["test"])
            row["acceptance_baseline_failures"] = sorted(clean_failed)
            if clean_code != 0:
                row.update(bug_detected=False, note="합격 검사 기준선 실패: 과제/환경 확인 필요")
                results.append(row)
                if args.out:
                    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"{task['id']}: skipped (acceptance baseline failed)", flush=True)
                continue
            mutated = source
            edits = task.get("edits") or [[task["find"], task["replace"]]]  # 어려운 과제는 버그 여러 곳
            # 과제를 만든 뒤 코드가 바뀌어 버그를 심을 자리가 없으면 건너뛴다(2026-10-10: 전에는 assert 로 평가 전체가 멈췄다)
            if any(mutated.count(find) != 1 for find, _ in edits):
                row.update(bug_detected=False, note="변이 자리 없음: 과제를 만든 뒤 코드가 바뀜")
                results.append(row)
                if args.out:
                    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"{task['id']}: skipped (mutation anchor missing)", flush=True)
                continue
            for find, replace in edits:
                mutated = mutated.replace(find, replace, 1)
            target.write_text(mutated, encoding="utf-8")
            code, failed = unittest_failures(project, task["test"])
            row["bug_detected"] = code != 0
            row["detected_failures"] = sorted(failed)  # 2026-09-29: webread 과제는 3.11.9 에서 원래 통과 — 무엇이 실패했는지 남긴다
            if code == 0:
                row["note"] = "되살린 버그를 테스트가 잡지 못해 건너뜀"
                results.append(row)
                if args.out:
                    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"{task['id']}: skipped (test does not catch the bug)", flush=True)
                continue
            if args.solver == "opencode":
                row.update(run_opencode(project, task, args, Path(tmp)))
                if row["scope_violations"] or row["solver_timed_out"] or row["solver_exit"] != 0:
                    row.update(acceptance_passed=False, suite_exit=None, new_failures=None,
                               note="풀이 미완료 또는 허용 범위 밖 변경: 성공 집계 제외")
                    results.append(row)
                    if args.out:
                        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                    print(f"{task['id']}: invalid solver result", flush=True)
                    continue
                code, _ = unittest_failures(project, task["test"])
                row["acceptance_passed"] = code == 0
                row["restored_original"] = target.read_text(encoding="utf-8").replace("\r\n", "\n") == source.replace("\r\n", "\n")
                code, failed = unittest_failures(project)
                row["suite_exit"] = code
                row["new_failures"] = sorted(failed - baseline_failed)
                row["rounds"] = sum(row["tool_counts"].values())
                row["seconds"] = row["solver_seconds"]
                results.append(row)
                print(f"{task['id']}: passed={row['acceptance_passed']} rounds={row['rounds']} "
                      f"regressions={len(row['new_failures'])} same_as_original={row['restored_original']} "
                      f"tests_touched={len(row['tests_touched'])} changed={row['changed_files']} {row['seconds']}s", flush=True)
                if args.out:
                    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                continue
            settings = dataclasses.replace(base_settings, data_dir=Path(tmp) / "data", log_dir=Path(tmp) / "logs",
                                           general_model=args.model, coder_model=args.model,
                                           desktop_general_model=args.model, desktop_coder_model=args.model,
                                           coding_insights_enabled=bool(args.insights), coding_api_map_enabled=args.api_map,
                                           **({"num_gpu_layers": args.num_gpu, "num_gpu_layers_model": args.model} if args.num_gpu else {}))
            if args.insights:
                (Path(tmp) / "data").mkdir(parents=True, exist_ok=True)
                (Path(tmp) / "data" / "coding-insights.json").write_bytes(insights_bytes)
            core, _ = build_core(settings, approval_callback=lambda _r: False)
            if "effective" not in manifest:  # 실제 provider 가 쓰는 값 — 요청값과 다를 수 있다(0 = 자동, 문맥 예산으로 잘린 출력 등)
                coder = next((p for d, p in core.providers.items() if getattr(d, "value", "") == "local_coder"), None)
                manifest["effective"] = {key: getattr(coder, key, None) for key in ("model", "num_ctx", "num_predict", "num_gpu", "think",
                                                                                   "timeout", "keep_alive")}
                manifest["effective"].update(profile=core.hardware.profile.value, context_char_budget=core.context_char_budget,
                                             coding_num_ctx=core.coding_num_ctx, coding_num_gpu=core.coding_num_gpu,
                                             coding_context_char_budget=core.coding_context_char_budget,
                                             auto_attempts=core.coding_auto_attempts,
                                             regression_check=core.coding_regression_check,
                                             diagnostics=core.coding_diagnostic_context)
                if args.out:
                    Path(args.out + ".manifest.json").write_text(
                        json.dumps({"manifest_id": manifest_id, **manifest}, ensure_ascii=False, indent=1), encoding="utf-8")
            core.workspace_editor = SafeWorkspaceEditor(project, Path(tmp) / "backups", storage=core.storage)
            core.current_project_root = lambda project=project: project
            session = core.get_or_create_session(False)
            started = time.monotonic()

            def say(text):
                return handle_command(core, session, text)[0]

            # 회차마다 모델이 낸 수정안을 남긴다(2026-09-29: 실패 원인을 보려면 마지막 출력 600자로는 모자랐다)
            round_diffs: list[str] = []
            propose = core.propose_bundle_local

            def recording_propose(*args, **kwargs):
                try:
                    bundle = propose(*args, **kwargs)
                except Exception as exc:  # 거부된 회차도 원문을 남긴다(2026-10-04 claude)
                    round_diffs.append(f"REJECTED: {str(exc)[:300]}\n" + str(getattr(exc, 'model_output', ''))[:1500])
                    raise
                round_diffs.append(bundle.diff[:3000])
                return bundle

            core.propose_bundle_local = recording_propose
            say(f"/dev-start {task['file']} :: {task['test']} :: {GOAL}")
            reply = say("/dev-auto")
            row["journal"] = "\n".join(line for line in reply.splitlines() if "회차" in line or line.startswith("  →"))[:4000]
            row["round_diffs"] = round_diffs
            row["rounds"] = len(re.findall(r"회차 테스트", reply))
            match = re.search(r"(?m)^편집 제안 ID: ([0-9a-f]{12})$", reply)
            if match:
                row["proposed_diff"] = reply[reply.find("--- a/"):][:1500]
                say(f"/apply-edit {match.group(1)} --yes")
                row["acceptance_passed"] = "테스트 통과" in say("/dev-next")
                fixed = target.read_text(encoding="utf-8")
                row["restored_original"] = fixed.replace("\r\n", "\n") == source.replace("\r\n", "\n")
                code, failed = unittest_failures(project)
                row["suite_exit"] = code
                row["new_failures"] = sorted(failed - baseline_failed)
            else:
                row["acceptance_passed"] = False
                row["stopped"] = reply[-600:]
            row["seconds"] = round(time.monotonic() - started, 1)
            # 앱이 남긴 결과 기록(ADR-069)을 함께 싣는다 — 평가와 앱이 같은 증거를 본다
            from jarvis.developer import outcome_status, read_outcomes
            recorded = read_outcomes(core.storage, 1)
            if recorded:
                row["outcome_status"] = outcome_status(recorded[0][1])
                row["outcome_events"] = [e.get("type") for e in recorded[0][1]]
                if args.outcomes_out:
                    with open(args.outcomes_out, "a", encoding="utf-8") as handle:
                        for event in recorded[0][1]:
                            handle.write(json.dumps({**event, "task": task["id"]}, ensure_ascii=False) + "\n")
                        if "suite_exit" in row:  # 평가가 잰 전체 검사도 같은 작업의 사건으로 — 교훈 추출이 회귀를 보게(Codex 리뷰 R05)
                            applied = next((e for e in reversed(recorded[0][1]) if e.get("type") == "applied"), {})
                            handle.write(json.dumps({"type": "suite", "run_id": recorded[0][0], "task": task["id"],
                                                     "digest": applied.get("digest", ""), "proposal_id": applied.get("proposal_id", ""),
                                                     "result": "PASS" if row["suite_exit"] == 0 and not row["new_failures"] else "FAIL",
                                                     "exit": row["suite_exit"], "new_failures": row["new_failures"],
                                                     "source": "eval-harness"}, ensure_ascii=False) + "\n")
            for handler in list(logging.getLogger("jarvis").handlers):
                handler.close()
                logging.getLogger("jarvis").removeHandler(handler)
        results.append(row)
        print(f"{task['id']}: passed={row.get('acceptance_passed')} rounds={row.get('rounds')} "
              f"regressions={len(row.get('new_failures', []))} same_as_original={row.get('restored_original')} "
              f"{row.get('seconds')}s", flush=True)
        if args.out:  # 과제마다 저장한다 — 중간에 끊겨도 끝난 과제 결과는 남는다
            Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    # 엄격한 성공(2026-09-30 Codex 리뷰): 버그가 잡혔고, 합격 기준 통과, 전체 테스트 종료 코드 0, 새 실패 목록이 명시적으로 비어 있을 때만
    solved = sum(strict_success(r) for r in results)
    print(f"\n{solved}/{sum(1 for r in results if r.get('bug_detected'))} bugs fixed without regressions")
    if args.out:
        print("wrote", args.out)
    snapshot_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
