"""검증 쓰기 모드(/rtl-tests, 2026-10-22, ADR-103) — 모델이 **시험기**를 쓰고, 그 시험기가 얼마나 잘 잡는지 변이로 잰다.

사용자가 고른 보강(10-22). 설계(/rtl)와 검증을 나눈다:
- 모델은 설계 코드를 보지 않는다 — 규격과 포트(그리고 검증된 블록의 포트)만 보고 스스로 확인하는 시험기 `verify/alex_tb.v`
  (모듈 alex_tb)를 쓴다. 설계 파일·사람이 둔 시험기는 건드리지 않는다.
- 이 PC 의 iverilog 로 '시험기 + 지금 설계(+ 의존 블록)' 를 돌린다.
  · 컴파일이 안 되면 → 오류를 주고 다시 쓰게 한다.
  · 시험기가 지금 설계에서 **실패**하면 → 멈추고 보고한다. 설계 버그일 수도, 시험기가 규격을 다르게 읽었을 수도 있다 —
    모델이 시험기를 설계에 맞춰 고치면 버그를 덮을 수 있으므로 사람이 판단한다.
  · 통과하면 → yosys `mutate`(YosysHQ mcy 와 같은 방식)로 설계에 작은 고장 N개를 넣어 각각 돌린다. 시험기가 실패(또는
    시간 초과)하면 '잡음'. 잡은 비율이 목표 미만이면 놓친 고장의 위치(파일·행·종류)를 주고 확인을 보강하게 한다.
- 이 PC 실측(2026-10-22, oss-cad-suite 2026-09-24): 8비트 카운터에 변이 12개, 간단한 시험기가 8개를 잡음, 변이당 약 1초.
변이 점수는 '이 시험기가 설계의 바뀐 동작을 알아채는가' 이지 설계가 맞다는 보증이 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

from .providers import GenerationCancelled, Message
from .eda import _exe
from .rtl_loop import RtlProject, _tool_run, defined_modules, extract_code, module_headers

TB_FILE = "verify/alex_tb.v"
TB_TOP = "alex_tb"
PASS, FAIL = "ALEX_TB_PASS", "ALEX_TB_FAIL"
MUTANTS = 20
TARGET = 0.8
MAX_ROUNDS = 4
SIM_TIMEOUT = 60
TB_FORBIDDEN = re.compile(r"\$(system|fopen|fwrite|fdisplay|fstrobe|fmonitor|fclose|writemem[bh]|readmem[bh]|dumpfile|"
                          r"dumpvars|value\$plusargs|test\$plusargs|sformat|swrite)\b")
TB_RULES = (
    "너는 하드웨어 검증 엔지니어다. 설계 코드는 보지 않고 규격과 포트만 보고, 스스로 판정하는 Verilog-2005 시험기를 쓴다.\n"
    f"- 최상위 모듈 이름은 `{TB_TOP}`(포트 없음). 설계 최상위를 dut 로 인스턴스한다.\n"
    "- 클록·리셋을 만들고, 규격의 동작마다 입력을 넣고 기대값과 출력을 비교한다(경계값·리셋 직후·동시에 일어나는 일 포함).\n"
    f"- 틀리면 `$display(\"{FAIL}: 무엇 기대=%0d 실제=%0d\", …)`, 끝에 하나도 안 틀렸으면 `$display(\"{PASS}\")` 후 `$finish`.\n"
    "- 무한히 돌지 않게 전체를 수천 클록 안에 끝낸다. 파일 입출력·$system·$dumpfile·`include 는 쓰지 않는다.\n"
    "- 설명은 세 문장 이내, 답에 ```verilog 블록 하나(시험기 전체)."
)


@dataclass
class TbRound:
    number: int
    seconds: float = 0.0
    compiled: bool = False
    design_pass: bool | None = None
    fails: list[str] = field(default_factory=list)
    killed: int = 0
    total: int = 0
    survivors: list[str] = field(default_factory=list)
    problem: str = ""


@dataclass
class TbResult:
    project: Path
    done: bool = False
    rounds: list[TbRound] = field(default_factory=list)
    stopped: str = ""
    run_dir: Path | None = None
    found_bug: bool = False


def tb_problem(code: str, top: str) -> str:
    plain = re.sub(r'//[^\n]*|/\*.*?\*/', '', code, flags=re.S)
    if task := TB_FORBIDDEN.search(plain):
        return f"시험기에 허용하지 않는 시스템 태스크 `${task.group(1)}` 가 있다. 빼고 전체 시험기를 다시 줘."
    if re.search(r"`include|`define", plain):
        return "시험기에 `include·`define 은 쓰지 않는다. 직접 적어서 다시 줘."
    if TB_TOP not in defined_modules(code):
        return f"최상위 시험기 모듈 이름이 `{TB_TOP}` 가 아니다. 이름을 바꿔 다시 줘."
    if not re.search(rf"\b{re.escape(top)}\b\s*(#\s*\(.*?\)\s*)?[A-Za-z_]\w*\s*\(", plain, flags=re.S):
        return f"시험기가 설계 최상위 `{top}` 를 인스턴스하지 않는다."
    return ""


def simulate(project: RtlProject, tools_dir: Path, tb: Path, design: list[Path], out: Path,
             cancel_event: threading.Event | None = None) -> tuple[bool, bool, list[str], str]:
    """(컴파일됨, 통과, 실패 줄들, 출력 꼬리). 시간 초과는 통과가 아님."""
    vvp = out / "sim.vvp"
    vvp.unlink(missing_ok=True)
    compiled = _tool_run([str(_exe(tools_dir, "iverilog")), "-g2012", "-s", TB_TOP, "-o", str(vvp), str(tb),
                          *(str(path) for path in design)], project, tools_dir, 120, cancel_event)
    if compiled.returncode != 0 or not vvp.is_file():
        return False, False, [], (compiled.stdout + compiled.stderr)[-2000:]
    try:
        done = _tool_run([str(_exe(tools_dir, "vvp")), "-n", str(vvp)], project, tools_dir, SIM_TIMEOUT, cancel_event)
    except subprocess.TimeoutExpired:
        return True, False, [f"시뮬레이션이 {SIM_TIMEOUT}초 안에 끝나지 않음($finish 없음?)"], ""
    output = done.stdout + done.stderr
    fails = [line.strip() for line in output.splitlines() if FAIL in line][:10]
    return True, PASS in output and not fails, fails, output[-2000:]


def _bare(project: RtlProject, path: Path) -> str:
    """yosys mutate -o 는 따옴표를 경로의 일부로 읽는다 — 프로젝트 폴더 기준 상대 경로를 따옴표 없이(빈칸 없는 것만)."""
    relative = path.relative_to(project.root).as_posix()
    if " " in relative:
        raise ValueError(f"경로에 빈칸이 있어 yosys 에 넘길 수 없습니다: {relative}")
    return relative


def mutation_list(project: RtlProject, tools_dir: Path, out: Path, count: int, seed: int,
                  cancel_event: threading.Event | None = None) -> list[str]:
    """설계 파일의 모듈 안에서만 고장 후보(yosys mutate -list). 의존 블록은 검증됐으니 건드리지 않는다."""
    listing = out / "mutations.txt"
    modules = " ".join(sorted(defined_modules(project.target.read_text(encoding="utf-8"))))
    sources = " ".join(f'"{path}"' for path in (*project.deps, project.target))
    _tool_run([str(_exe(tools_dir, "yosys")), "-q", "-p",
               f"read_verilog -sv {sources}; prep -top {project.top}; mutate -list {count} -seed {seed} -o {_bare(project, listing)} {modules}"],
              project, tools_dir, 300, cancel_event)
    return listing.read_text(encoding="utf-8").splitlines() if listing.is_file() else []


def describe(mutation: str) -> str:
    """'mutate -mode inv … -src counter.v:4.14-4.16 …' → 'counter.v 4행: inv(en)'."""
    mode = re.search(r"-mode (\S+)", mutation)
    src = re.search(r"-src (\S+):(\d+)\.\d+", mutation)  # 경로에 C: 가 있을 수 있다
    wire = re.search(r"-wire (\S+)", mutation)
    name = re.split(r"[\\/]", src.group(1))[-1] if src else ""
    where = f"{name} {src.group(2)}행" if src else "위치 모름"
    signal = wire.group(1).lstrip("\\") if wire else ""
    return f"{where}: {mode.group(1) if mode else '?'}" + (f"({signal})" if signal else "")


def mutation_score(project: RtlProject, tools_dir: Path, tb: Path, out: Path, mutations: list[str],
                   cancel_event: threading.Event | None = None) -> tuple[int, int, list[str]]:
    """(잡은 수, 돌린 수, 놓친 고장 설명). 적용·컴파일이 안 되는 변이는 세지 않는다. 시간 초과는 잡은 것."""
    sources = " ".join(f'"{path}"' for path in (*project.deps, project.target))
    mutant = out / "mutant.v"
    killed = total = 0
    survivors: list[str] = []
    for mutation in mutations:
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled("검증 쓰기를 중단했습니다.")
        mutant.unlink(missing_ok=True)
        applied = _tool_run([str(_exe(tools_dir, "yosys")), "-q", "-p",
                             f"read_verilog -sv {sources}; prep -top {project.top}; {mutation}; opt_clean; "
                             f"write_verilog -noattr {_bare(project, mutant)}"], project, tools_dir, 120, cancel_event)
        if applied.returncode != 0 or not mutant.is_file():
            continue
        compiled, passed, _fails, _tail = simulate(project, tools_dir, tb, [mutant], out, cancel_event)
        if not compiled:
            continue
        total += 1
        if passed:
            survivors.append(describe(mutation))
        else:
            killed += 1
    return killed, total, survivors


def run_tb(generate: Callable[[list[Message]], str], project: RtlProject, tools_dir: Path, *,
           progress: Callable[[str], None] | None = None, cancel_event: threading.Event | None = None,
           max_rounds: int = MAX_ROUNDS, mutants: int = MUTANTS, target: float = TARGET) -> TbResult:
    say = progress or (lambda _text: None)
    run_dir = project.root / "rounds-tb" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True)
    result = TbResult(project.root, run_dir=run_dir)
    tb = project.root / TB_FILE
    tb.parent.mkdir(parents=True, exist_ok=True)
    if tb.is_file():
        shutil.copyfile(tb, run_dir / "before_alex_tb.v")
    design = [*project.deps, project.target]
    ports = module_headers(project.target.read_text(encoding="utf-8")).get(project.top, f"module {project.top} (…);")
    mutations: list[str] | None = None

    def save() -> None:
        (project.root / "rtl-tb-run.json").write_text(json.dumps({
            "done": result.done, "found_bug": result.found_bug, "stopped": result.stopped, "run_dir": str(run_dir),
            "rounds": [vars(r) for r in result.rounds]}, ensure_ascii=False, indent=1), encoding="utf-8")

    feedback = ""
    for number in range(1, max_rounds + 1):
        if cancel_event is not None and cancel_event.is_set():
            result.stopped = "사용자 취소"
            save()
            raise GenerationCancelled("검증 쓰기를 중단했습니다.")
        started = time.monotonic()
        request = (f"[설계 규격]\n{project.prompt_spec}\n\n[설계 최상위 포트 — 코드는 보여 주지 않는다]\n```verilog\n{ports}\n```")
        if feedback:
            request += f"\n\n[지난 시험기에 대한 결과]\n{feedback}"
        messages = [Message("system", TB_RULES), Message("user", request)]
        (run_dir / f"round-{number}.request.json").write_text(
            json.dumps([{"role": m.role, "content": m.content} for m in messages], ensure_ascii=False, indent=1), encoding="utf-8")
        answer = generate(messages)
        (run_dir / f"round-{number}.answer.md").write_text(answer, encoding="utf-8")
        current = TbRound(number)
        code = extract_code(answer)
        problem = "답에 ```verilog 시험기 블록이 없다. 전체 시험기를 블록 하나로 다시 줘." if not code else tb_problem(code, project.top)
        if problem:
            current.problem = feedback = problem
        else:
            tb.write_text(code, encoding="utf-8")
            shutil.copyfile(tb, run_dir / f"round-{number}_alex_tb.v")
            compiled, passed, fails, tail = simulate(project, tools_dir, tb, design, run_dir, cancel_event)
            current.compiled, current.design_pass, current.fails = compiled, passed if compiled else None, fails
            if not compiled:
                current.problem = "컴파일 실패"
                feedback = "iverilog 컴파일 오류(시험기를 고쳐라):\n" + tail
            elif not passed:
                current.problem = "지금 설계에서 시험기 실패 — 사람이 판단"
                result.found_bug = bool(fails)
                result.stopped = ("시험기가 지금 설계에서 실패했습니다. 설계 버그일 수도, 시험기가 규격을 다르게 읽었을 수도 있어 "
                                  "멈췄습니다(시험기를 설계에 맞춰 고치면 버그를 덮을 수 있음).")
                current.seconds = round(time.monotonic() - started, 1)
                result.rounds.append(current)
                save()
                say(f"[검증 {number}회차] 지금 설계에서 실패 {len(fails)}건 — 멈춤\n")
                return result
            else:
                if mutations is None:
                    mutations = mutation_list(project, tools_dir, run_dir, mutants, 1, cancel_event)
                say(f"[검증 {number}회차] 설계에서 통과 — 고장 {len(mutations)}개로 시험기 확인 중\n")
                current.killed, current.total, current.survivors = mutation_score(
                    project, tools_dir, tb, run_dir, mutations, cancel_event)
                score = current.killed / current.total if current.total else 0.0
                if current.total and score >= target:
                    result.done = True
                    current.seconds = round(time.monotonic() - started, 1)
                    result.rounds.append(current)
                    save()
                    say(f"[검증 {number}회차] 고장 {current.killed}/{current.total} 잡음 — 목표 {int(target * 100)}% 달성\n")
                    return result
                feedback = (f"시험기는 지금 설계에서 통과했다(유지). 그런데 설계에 일부러 넣은 고장 {current.total}개 중 "
                            f"{current.killed}개만 잡았다. 놓친 고장 위치(파일 행: 종류 — inv=반전, const0/1=고정, cnot=조건 반전):\n"
                            + "\n".join(f"- {line}" for line in current.survivors[:12])
                            + "\n이 동작들이 규격대로인지 확인하는 검사를 더해 전체 시험기를 다시 줘. 지금 설계에서는 계속 통과해야 한다.")
        current.seconds = round(time.monotonic() - started, 1)
        result.rounds.append(current)
        save()
        note = f"고장 {current.killed}/{current.total} 잡음" if current.total else (current.problem or "")
        say(f"[검증 {number}회차] {note} ({current.seconds}초)\n")
    result.stopped = f"{max_rounds}회 안에 목표({int(target * 100)}%)에 못 미침"
    save()
    return result


def answer_text(result: TbResult, project: RtlProject) -> str:
    last = result.rounds[-1] if result.rounds else None
    if result.done:
        head = f"검증 시험기 완성 — 고장 {last.killed}/{last.total} 잡음: `{project.root / TB_FILE}`"
    elif result.found_bug and last is not None:
        head = "시험기가 지금 설계에서 실패했습니다 — 설계 버그 후보(사람 확인 필요):\n" + "\n".join(f"  {line}" for line in last.fails)
    else:
        head = f"검증 시험기 미완 — {result.stopped}"
    lines = [head]
    for r in result.rounds:
        status = (f"고장 {r.killed}/{r.total} 잡음" if r.total else
                  "설계에서 통과" if r.design_pass else r.problem or "?")
        lines.append(f"- {r.number}회차: {status} ({r.seconds}초)")
    if last is not None and last.survivors and not result.found_bug:
        lines.append("놓친 고장: " + ", ".join(last.survivors[:8]))
    lines.append(f"회차별 시험기·결과: `{result.run_dir}`. 변이 점수는 시험기가 동작 변화를 알아채는 정도이지 설계가 맞다는 보증이 아닙니다.")
    return "\n".join(lines)
