"""RTL 모드 — 모델이 Verilog 를 쓰고, 린트·시뮬레이션 결과를 보고 스스로 고친다(2026-10-10, ADR-087).

AutoChip(arXiv 2311.04887)의 피드백 순환을 참고한다: 생성 → 컴파일·린트·시험 → 실패 관측 → 수정.
이미 코드가 있으면 rtl_repair의 최소 수정 요청을 사용하고, 무변경 답과 개별 시험 회귀를 거절한다.
전체 파일 답도 기존 모델 호환을 위해 받지만 같은 검증·회귀 규칙을 따른다.

프로젝트 폴더(rtl_root 아래)에 사람이 둔 것:
  rtl.json   {"top": "rv32i_cpu", "file": "rv32i_cpu.v", "spec": "spec.md",
              "test": ["{python}", "verify/run_tests.py", "{file}", "--json", "{report}"], "max_rounds": 6}
  spec.md    설계 규격
  verify/    시험기(사람이 쓴 것) — 결과 JSON: {"시험이름": {"ok": bool, "problems": [...], "about": "...", "source": "..."}}
  (선택) "deps": ["fifo3-growth", …]  — 같은 rtl_root 의 **검증된** 다른 프로젝트 설계(2026-10-22, ADR-103). 읽기 전용으로
             린트·시험에 함께 들어가고(test 명령의 "{deps}" 자리에 파일 경로들), 모델에게는 그 모듈의 포트만 보여 준다.
             검증 = 그 프로젝트 rtl-run.json 이 verified 이고 final_sha256 이 지금 파일과 같음(검증 뒤 바뀌면 다시 /rtl).
모델은 `file` 하나만 쓴다. 시험기·규격·rtl.json·의존 블록은 건드리지 못한다. 모델이 쓴 Verilog 는 시뮬레이터에서만 돈다 —
파일·명령 실행 시스템 태스크($system, $fopen 등)가 있으면 실행하지 않고 고치라고 돌려보낸다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from .providers import GenerationCancelled, Message
from .rtl_repair import apply_repair, code_tokens, repair_messages

PROJECT_FILE = "rtl.json"
MAX_ROUNDS = 6
MAX_ALLOWED_ROUNDS = 20
FORBIDDEN = re.compile(r"\$(system|fopen|fwrite|fdisplay|fstrobe|fmonitor|fclose|writemem[bh]|dumpfile|dumpvars|readmem[bh]|value\$plusargs)\b")
BLOCK = re.compile(r"```(?:verilog|systemverilog|v|sv)?[ \t]*\n(.*?)```", re.S | re.I)
OPEN_BLOCK = re.compile(r"```(?:verilog|systemverilog|v|sv)?[ \t]*\n", re.I)
RULES = ("너는 RTL 설계자다. 규격을 정확히 지키는 합성 가능한 Verilog-2001 을 쓴다. 파일 하나에 필요한 모듈을 모두 넣는다. "
         "부호 있는 연산은 signed 중간 신호나 별도 if/case로 계산한다. 삼항 연산자의 unsigned 피연산자가 "
         "산술 오른쪽 시프트의 부호를 바꿀 수 있다. 상태 쓰기 허용 신호는 해당 상태를 실제로 갱신하는 명령에서만 켠다. "
         "$system·$fopen·$display 같은 시스템 태스크나 외부 파일 include는 쓰지 않는다. "
         "설명은 세 문장 이내로 쓰고 같은 추측을 반복하지 않는다. 답은 ```verilog 블록 하나(전체 코드)를 포함한다.")


@dataclass
class RtlProject:
    root: Path
    top: str
    file: str
    spec: str
    test: list[str]
    max_rounds: int = MAX_ROUNDS
    expected_tests: tuple[str, ...] = ()
    deps: tuple[Path, ...] = ()                                   # 검증된 블록 파일(의존의 의존 먼저)
    dep_modules: dict[str, str] = field(default_factory=dict)     # 모듈 이름 → 포트 머리

    @classmethod
    def load(cls, root: Path, _stack: tuple[Path, ...] = ()) -> "RtlProject":
        root = Path(root).resolve()
        config = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("test"), list) or not config["test"]:
            raise ValueError("rtl.json의 test는 비어 있지 않은 명령 인자 목록이어야 합니다.")
        spec_file = (root / str(config.get("spec", "spec.md"))).resolve()
        if root not in spec_file.parents:
            raise ValueError("규격 파일은 프로젝트 폴더 안에 있어야 합니다.")
        expected = config.get("expected_tests", [])
        if not isinstance(expected, list) or any(not isinstance(name, str) or not name for name in expected):
            raise ValueError("expected_tests는 시험 이름 목록이어야 합니다.")
        project = cls(root=root, top=str(config["top"]), file=str(config["file"]),
                      spec=spec_file.read_text(encoding="utf-8"),
                      test=[str(part) for part in config["test"]], max_rounds=int(config.get("max_rounds", MAX_ROUNDS)),
                      expected_tests=tuple(expected))
        target = (root / project.file).resolve()
        if root not in target.parents or target.suffix.lower() not in (".v", ".sv"):
            raise ValueError(f"설계 파일은 프로젝트 폴더 안의 .v/.sv 여야 합니다: {project.file}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", project.top):
            raise ValueError(f"최상위 모듈 이름이 올바르지 않습니다: {project.top}")
        if not 1 <= project.max_rounds <= MAX_ALLOWED_ROUNDS:
            raise ValueError(f"최대 회수는 1~{MAX_ALLOWED_ROUNDS}여야 합니다.")
        names = config.get("deps", [])
        if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("deps 는 같은 RTL 폴더 안의 검증된 프로젝트 폴더 이름 목록이어야 합니다.")
        if names and "{deps}" not in project.test:
            raise ValueError('deps 를 쓰면 rtl.json 의 test 명령에 "{deps}" 를 넣어 시험기가 의존 블록 파일도 함께 컴파일하게 하세요.')
        files: list[Path] = []
        for name in names:
            dep_root = (root.parent / name).resolve()
            if dep_root.parent != root.parent or dep_root == root:
                raise ValueError(f"의존 블록은 같은 RTL 폴더의 다른 프로젝트여야 합니다: {name}")
            if dep_root in _stack + (root,):
                raise ValueError(f"의존 블록이 서로를 가리킵니다(순환): {name}")
            dep = cls.load(dep_root, _stack + (root,))
            if not verified_now(dep):
                raise ValueError(f"의존 블록 '{name}' 이 검증되지 않았거나 검증 뒤 바뀌었습니다 — 먼저 /rtl {name} 로 "
                                 "모든 시험을 통과시키세요(이미 통과한 것이면 /rtl {name} --continue 로 다시 확인).")
            for path in (*dep.deps, dep.target.resolve()):
                if path not in files:
                    files.append(path)
        project.deps = tuple(files)
        for path in files:
            project.dep_modules.update(module_headers(path.read_text(encoding="utf-8")))
        return project

    @property
    def prompt_spec(self) -> str:
        """모델에게 주는 규격 — 의존 블록이 있으면 그 포트를 붙인다(다시 정의하지 말고 인스턴스로 쓰라고)."""
        if not self.dep_modules:
            return self.spec
        headers = "\n\n".join(self.dep_modules.values())
        return (f"{self.spec}\n\n[이미 검증된 블록 — 별도 파일로 함께 컴파일된다. 이 모듈들은 다시 정의하지 말고 인스턴스로 쓴다. "
                f"그 밖의 모듈은 이 파일에 넣는다]\n```verilog\n{headers}\n```")

    @property
    def target(self) -> Path:
        return self.root / self.file


@dataclass
class RoundResult:
    number: int
    seconds: float
    passed: int = 0
    total: int = 0
    lint_warnings: int = -1      # -1 = 린트 못 돌림(도구 없음)
    problem: str = ""            # 코드 추출 실패·금지 구문·컴파일 실패 등
    change_type: str = ""
    accepted: bool = False
    regressions: list[str] = field(default_factory=list)


@dataclass
class RtlResult:
    project: Path
    verified: bool = False
    rounds: list[RoundResult] = field(default_factory=list)
    stopped: str = ""
    run_dir: Path | None = None


def tools_env(tools_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    paths = [str(tools_dir / "bin"), str(tools_dir / "lib")]
    riscv = riscv_bin()
    if riscv is not None:
        paths.append(str(riscv))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    env["VERILATOR_ROOT"] = str(tools_dir / "share" / "verilator")
    env["PYTHONUTF8"] = "1"
    return env


def riscv_bin(base: Path | None = None) -> Path | None:
    """RISC-V GCC(xPack, C:\\ALEX\\tools\\riscv) — 있으면 시험기가 riscv-none-elf-gcc 로 시험 프로그램을 만들 수 있다."""
    base = base or Path(__file__).resolve().parents[2] / "tools" / "riscv"
    found = sorted(base.glob("xpack-riscv-none-elf-gcc-*/bin"))
    return found[-1] if found else None


def _plain(code: str) -> str:
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', '', code, flags=re.S)


def defined_modules(code: str) -> set[str]:
    return set(re.findall(r"^\s*(?:macro)?module\s+([A-Za-z_][A-Za-z0-9_$]*)", _plain(code), flags=re.M))


def module_headers(code: str) -> dict[str, str]:
    """모듈 이름 → 'module 이름 (...);' 머리(포트 목록까지). 의존 블록을 모델에게 보여 줄 때."""
    headers = {}
    for match in re.finditer(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)[\s\S]*?\);", _plain(code), flags=re.M):
        headers[match.group(1)] = match.group(0).strip()[:2000]
    return headers


def verified_now(project: "RtlProject") -> bool:
    """마지막 /rtl 이 모든 시험을 통과했고, 그때의 설계 파일이 지금과 같은가."""
    try:
        record = json.loads((project.root / "rtl-run.json").read_text(encoding="utf-8"))
        current = hashlib.sha256(project.target.read_text(encoding="utf-8").encode()).hexdigest()
    except (OSError, ValueError):
        return False
    return bool(record.get("verified")) and record.get("final_sha256") == current


def extract_code(answer: str) -> str:
    blocks = BLOCK.findall(answer)
    return max(blocks, key=len).strip() + "\n" if blocks else ""


def code_problem(code: str) -> str:
    """설계에는 순수 시스템 함수만 허용한다. 벤치의 파일 I/O는 별도로 사람이 둔다."""
    plain = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', '', code, flags=re.S)
    pure = {"signed", "unsigned", "clog2", "bits", "size", "left", "right", "low", "high", "increment"}
    for task in re.findall(r"\$([A-Za-z_][A-Za-z0-9_$]*)", plain):
        if task not in pure:
            return f"설계 코드에 시스템 태스크 `${task}`가 있어 시뮬레이션하지 않았다. 빼고 전체 코드를 다시 줘."
    for directive in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)", plain):
        if directive not in {"timescale", "default_nettype", "resetall"}:
            return f"단일 파일 설계에서 외부 파일·매크로 지시문 `{directive}`는 허용하지 않는다. 직접 작성해 줘."
    return ""


def _tool_run(command: list[str], project: RtlProject, tools_dir: Path, timeout: float,
              cancel_event: threading.Event | None = None) -> subprocess.CompletedProcess:
    options = dict(cwd=project.root, env=tools_env(tools_dir), capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    if cancel_event is None:
        return subprocess.run(command, timeout=timeout, **options)
    if cancel_event.is_set():
        raise GenerationCancelled("RTL 검사를 중단했습니다.")
    options.pop("capture_output")
    options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    with subprocess.Popen(command, **options) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancel_event.is_set():
                    raise GenerationCancelled("RTL 검사를 중단했습니다.")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"),
                                    "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()


def lint(project: RtlProject, tools_dir: Path, timeout: float = 120,
         cancel_event: threading.Event | None = None) -> tuple[int, str]:
    """Verilator -Wall 경고 수와 내용. 도구가 없으면 (-1, '')."""
    exe = tools_dir / "bin" / ("verilator_bin.exe" if os.name == "nt" else "verilator_bin")
    if not exe.is_file():
        return -1, ""
    done = _tool_run([str(exe), "--lint-only", "-Wall", "-Wno-DECLFILENAME", "--top-module", project.top, project.file,
                      *(str(path) for path in project.deps)], project, tools_dir, timeout, cancel_event)
    lines = [line for line in (done.stdout + done.stderr).splitlines()
             if line.startswith(("%Warning", "%Error")) and not line.startswith("%Error: Exiting due to")]
    return len(lines) + (0 if done.returncode == 0 or lines else 1), "\n".join(lines[:20])


def run_tests(project: RtlProject, tools_dir: Path, report: Path, timeout: float = 900,
              cancel_event: threading.Event | None = None) -> tuple[dict, str]:
    """사람이 둔 시험기를 실행한다. (결과 JSON, 표준 출력 꼬리)."""
    report.unlink(missing_ok=True)
    command: list[str] = []
    for part in project.test:
        if part == "{deps}":
            command += [str(path) for path in project.deps]
        else:
            command.append(part.replace("{python}", sys.executable).replace("{file}", str(project.target))
                           .replace("{report}", str(report)))
    done = _tool_run(command, project, tools_dir, timeout, cancel_event)
    report.with_suffix(".output.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    output = (done.stdout + done.stderr)[-3000:]
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not data:
        return {}, "시험 결과 JSON이 없거나 비어 있습니다.\n" + output
    if any(not isinstance(row, dict) or type(row.get("ok")) is not bool for row in data.values()):
        return {}, "시험 결과의 ok는 명시적 boolean이어야 합니다.\n" + output
    if project.expected_tests and set(data) != set(project.expected_tests):
        return {}, "규격의 시험 목록과 결과 목록이 다릅니다.\n" + output
    if done.returncode not in (0, 1) or (done.returncode != 0 and all(row["ok"] for row in data.values())):
        return {}, f"시험기 비정상 종료(rc={done.returncode}); 통과로 판정하지 않습니다.\n" + output
    return data, output


def feedback(code_problem: str, warnings: int, lint_text: str, results: dict, output: str) -> str:
    if code_problem:
        return code_problem
    parts = []
    if not results:
        parts.append("시험기를 실행하지 못했다(컴파일 실패 가능). 출력:\n" + output[-2000:])
    else:
        failed = {name: row for name, row in results.items() if not row.get("ok")}
        parts.append(f"시험 {len(results) - len(failed)}/{len(results)} 통과.")
        for name, row in list(failed.items())[:6]:
            lines = [f"- 실패 `{name}` ({row.get('about', '')}):"]
            if row.get("first_divergence"):
                lines.append("  최초 실행 불일치(이후 결과보다 먼저 조사): " + str(row["first_divergence"]))
            lines += [f"    {problem}" for problem in row.get("problems", [])[:2 if row.get("first_divergence") else 6]]
            if row.get("why") or row.get("log"):
                lines.append(str(row.get("why", "")) + "\n" + str(row.get("log", ""))[-1000:])
            if row.get("source"):
                lines.append("  시험 프로그램(앞부분):\n" + "\n".join("    " + s for s in row["source"].splitlines()[:25]))
            parts.append("\n".join(lines))
    if warnings > 0:
        parts.append(f"Verilator -Wall 경고 {warnings}개:\n{lint_text}")
    parts.append("'마지막으로 쓴 명령'은 최종 값의 마지막 작성자이며 실제 원인보다 뒤일 수 있다. 최초 불일치가 있으면 그 명령을 먼저 조사한다. "
                 "li는 lui+addi 두 명령으로 펼쳐진다. 시험 이름보다 실제 명령어를 보고, "
                 "그 명령의 경로(디코드·즉시값·ALU 입력·쓰기 선택)를 코드에서 따라가 원인을 찾아 짧게 설명한 뒤 "
                 "필요한 부분만 고친다. 포트 규격은 그대로.")
    return "\n\n".join(parts)


def run_rtl(generate: Callable[[list[Message]], str], project: RtlProject, tools_dir: Path, *,
            progress: Callable[[str], None] | None = None, cancel_event: threading.Event | None = None,
            max_rounds: int | None = None, resume: bool = False) -> RtlResult:
    say = progress or (lambda _text: None)
    limit = project.max_rounds if max_rounds is None else max_rounds
    if not 1 <= limit <= MAX_ALLOWED_ROUNDS:
        raise ValueError(f"최대 회수는 1~{MAX_ALLOWED_ROUNDS}여야 합니다.")
    rounds_dir = project.root / "rounds" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")
    rounds_dir.mkdir(parents=True)
    result = RtlResult(project.root, run_dir=rounds_dir)
    code = project.target.read_text(encoding="utf-8") if resume and project.target.is_file() else ""
    seed = code
    (rounds_dir / "seed.v").write_text(seed, encoding="utf-8")
    last_feedback = ""
    best: tuple[set[str], str, str, int] | None = None  # 통과한 시험 이름, 코드, 피드백, 린트
    def save() -> None:
        payload = json.dumps({"verified": result.verified, "stopped": result.stopped,
                              "strategy": "focused-repair-v1",
                              "seed_sha256": hashlib.sha256(seed.encode()).hexdigest(),
                              "spec_sha256": hashlib.sha256(project.spec.encode()).hexdigest(),
                              # 검증된 블록으로 다른 프로젝트가 쓸 수 있는지 볼 때(verified_now) — 검증 순간의 파일
                              "final_sha256": hashlib.sha256(project.target.read_text(encoding="utf-8").encode()).hexdigest()
                              if result.verified and project.target.is_file() else "",
                              "deps": [str(path) for path in project.deps],
                              "run_dir": str(rounds_dir), "rounds": [vars(r) for r in result.rounds]},
                             ensure_ascii=False, indent=1)
        (rounds_dir / "rtl-run.json").write_text(payload, encoding="utf-8")
        (project.root / "rtl-run.json").write_text(payload, encoding="utf-8")

    save()
    if code:
        last_feedback = code_problem(code)
        if not last_feedback:
            try:
                warnings, lint_text = lint(project, tools_dir, cancel_event=cancel_event)
                results, output = run_tests(project, tools_dir, rounds_dir / "baseline.json", cancel_event=cancel_event)
            except Exception as exc:
                result.stopped = "사용자 취소" if isinstance(exc, GenerationCancelled) else f"이어받기 검사 중단: {type(exc).__name__}"
                save()
                raise
            last_feedback = feedback("", warnings, lint_text, results, output)
            best = ({name for name, row in results.items() if row["ok"]}, code, last_feedback, warnings)
            say(f"[RTL 이어받기] 현재 코드 시험 {len(best[0])}/{len(results)}\n")
            if results and len(best[0]) == len(results) and warnings <= 0:
                result.verified = True
                save()
                return result
    for number in range(1, limit + 1):
        if cancel_event is not None and cancel_event.is_set():
            result.stopped = "사용자 취소"
            save()
            raise GenerationCancelled(f"RTL 작업을 중단했습니다. 지금까지의 코드: {project.target}")
        started = time.monotonic()
        request = f"[설계 규격]\n{project.prompt_spec}"
        if code:
            messages = repair_messages(project.prompt_spec, code, last_feedback or '처음부터 다시 확인한다.')
        elif last_feedback:  # 아직 받아들인 코드가 없을 때도 지난 답이 거절된 이유는 알려 준다
            request += f"\n\n[지난 답의 문제]\n{last_feedback}"
            messages = [Message("system", RULES), Message("user", request)]
        else:
            messages = [Message("system", RULES), Message("user", request)]
        (rounds_dir / f"round-{number}.request.json").write_text(
            json.dumps([{"role": m.role, "content": m.content} for m in messages], ensure_ascii=False, indent=1),
            encoding="utf-8")
        try:
            answer = generate(messages)
        except Exception as exc:
            result.stopped = "사용자 취소" if isinstance(exc, GenerationCancelled) else f"생성 중단: {type(exc).__name__}"
            save()
            raise
        (rounds_dir / f"round-{number}.answer.md").write_text(answer, encoding="utf-8")
        round_result = RoundResult(number, 0.0)
        problem = ""
        try:
            patched = apply_repair(answer, code) if code else None
            new_code = patched if patched is not None else extract_code(answer)
            round_result.change_type = "patch" if patched is not None else "full"
        except ValueError as exc:
            new_code = ""
            round_result.change_type = "patch"
            problem = str(exc)
        if problem:
            pass
        elif not new_code and OPEN_BLOCK.search(answer):
            problem = ("답이 코드 중간에서 끊겼다(출력 한도). 설명은 두세 줄로 줄이고, 고친 전체 코드를 ```verilog 블록 하나로 "
                       "닫는 ``` 까지 다시 줘.")
        elif not new_code:
            problem = "답에 ```verilog 코드 블록이 없다. 전체 코드를 블록 하나로 다시 줘."
        elif code_problem(new_code):
            problem = code_problem(new_code)
        elif clash := sorted(defined_modules(new_code) & set(project.dep_modules)):
            problem = (f"검증된 블록 {', '.join(clash)} 를 이 파일에서 다시 정의했다. 그 모듈은 별도 파일로 함께 컴파일되니 "
                       "정의를 빼고 인스턴스로만 써서 전체 코드를 다시 줘.")
        elif code and code_tokens(new_code) == code_tokens(code):
            problem = "실제 RTL 연산이 이전 코드와 같다(주석·공백 변경은 수정이 아니다). 같은 답을 반복하지 말고 실패 원인을 바꾸는 수정을 줘."
        else:
            (rounds_dir / f"round-{number}.diff").write_text(
                "".join(difflib.unified_diff(code.splitlines(True), new_code.splitlines(True),
                                             fromfile="before.v", tofile="candidate.v")), encoding="utf-8")
            code = new_code
            project.target.write_text(code, encoding="utf-8")
            shutil.copyfile(project.target, rounds_dir / f"round-{number}{project.target.suffix}")
        if problem:
            round_result.problem = problem
            last_feedback = problem
            if best is not None:
                last_feedback += "\n\n[유지한 코드의 시험 결과]\n" + best[2]
        else:
            try:
                warnings, lint_text = lint(project, tools_dir, cancel_event=cancel_event)
                results, output = run_tests(project, tools_dir, rounds_dir / f"round-{number}.json", cancel_event=cancel_event)
            except Exception as exc:
                result.stopped = "사용자 취소" if isinstance(exc, GenerationCancelled) else f"검사 중단: {type(exc).__name__}"
                save()
                raise
            (rounds_dir / f"round-{number}.lint.txt").write_text(lint_text, encoding="utf-8")
            round_result.lint_warnings = warnings
            round_result.total = len(results)
            round_result.passed = sum(1 for row in results.values() if row.get("ok"))
            if not results:
                round_result.problem = "시험기 실행 실패(컴파일 오류 가능)"
            last_feedback = feedback("", warnings, lint_text, results, output)
            passed_names = {name for name, row in results.items() if row["ok"]}
            round_result.regressions = sorted(best[0] - passed_names) if best else []
            lint_regression = best is not None and best[3] == 0 and warnings > 0
            if round_result.regressions or lint_regression:
                # 총 통과 수가 같거나 늘어도 앞서 통과한 개별 계약이 깨지면 보존한다.
                round_result.problem = "기존 통과 시험 회귀: " + ", ".join(round_result.regressions) if round_result.regressions else "린트 회귀"
                last_feedback = (f"방금 고친 판은 시험 {round_result.passed}/{round_result.total} 로 앞선 최고 판"
                                 f"({len(best[0])}/{round_result.total})의 계약을 깨뜨렸다 — 되돌렸다. "
                                 f"{round_result.problem}. 거절한 판의 관측:\n{last_feedback}\n\n최고 판의 결과:\n{best[2]}")
                code = best[1]
                project.target.write_text(code, encoding="utf-8")
            else:
                round_result.accepted = True
                best = (passed_names, code, last_feedback, warnings)
        round_result.seconds = round(time.monotonic() - started, 1)
        result.rounds.append(round_result)
        save()
        lint_note = "" if round_result.lint_warnings < 0 else f", 린트 경고 {round_result.lint_warnings}"
        say(f"[RTL {number}회차] 시험 {round_result.passed}/{round_result.total}{lint_note}"
            f"{' — ' + round_result.problem if round_result.problem else ''} ({round_result.seconds}초)\n")
        if round_result.accepted and round_result.total and round_result.passed == round_result.total and round_result.lint_warnings <= 0:
            result.verified = True
            (rounds_dir / "verified-repair.json").write_text(json.dumps({
                "scope": "이 프로젝트의 고정 시험만 검증; 가중치 학습 아님",
                "holdout_verified": False,
                "seed_sha256": hashlib.sha256(seed.encode()).hexdigest(),
                "final_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "passed_tests": sorted(passed_names),
                "diff": "".join(difflib.unified_diff(seed.splitlines(True), code.splitlines(True))),
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            break
    else:
        result.stopped = f"{limit}회 안에 모든 시험을 통과하지 못함"
    save()
    return result


def answer_text(result: RtlResult, project: RtlProject) -> str:
    lines = [("RTL 설계 완료 — 모든 시험 통과" if result.verified else f"RTL 설계 미완 — {result.stopped}") + f": `{project.target}`"]
    for r in result.rounds:
        lint_note = "" if r.lint_warnings < 0 else f" · 린트 경고 {r.lint_warnings}"
        lines.append(f"- {r.number}회차: 시험 {r.passed}/{r.total}{lint_note} ({r.seconds}초){' — ' + r.problem if r.problem else ''}")
    lines.append(f"회차별 코드와 시험 결과: `{result.run_dir or project.root / 'rounds'}`. 시험 통과는 시험이 보는 범위까지만 보증합니다 — 검토가 필요합니다.")
    return "\n".join(lines)
