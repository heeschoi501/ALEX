"""EDA 실행기(2026-10-22, ADR-103) — /rtl 프로젝트 하나에 정해진 오픈소스 흐름만 돌리고 결과를 요약한다.

사용자가 고른 보강(10-22 "미뤄둔 것과 보강할 것 지금 진행해"): 시뮬레이션 시험(/rtl) 말고도 칩 설계에서 늘 보는 세 가지 —
- synth : yosys 일반 합성(`synth -top`) → 셀 수·종류, 경고, 래치(의도하지 않은 래치는 흔한 버그)
- formal: 프로젝트(또는 verify/)의 *.sby 를 SymbiYosys 로 → PASS/FAIL/UNKNOWN, 실패면 반례 파형 위치
- fpga  : yosys `synth_ecp5` → nextpnr-ecp5(ECP5-85F, 핀 배정 없이) → 최고 주파수(clock 별)·LUT/FF/블록RAM/DSP 사용량.
          ASIC 이 아니라 FPGA 기준 추정이다 — 설계끼리 비교·회귀 확인용.
명령은 이 셋뿐이다(임의 명령 없음). 입력은 rtl.json 의 설계 파일과 검증된 의존 블록(deps), 결과는 프로젝트의 reports/<종류>-<시각>/.
oss-cad-suite 2026-09-24(yosys 0.69) 로 이 PC 에서 확인: 8비트 카운터 합성 22셀 0.5초, sby k-귀납 증명 1초, nextpnr 371MHz 0.9초.
(작업 폴더가 \\\\wsl.localhost 같은 네트워크 경로면 sby 가 실패한다 — ALEX 의 rtl_root 는 C: 드라이브.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import threading

from .rtl_loop import RtlProject, _tool_run

KINDS = ("synth", "formal", "fpga")
LABEL = {"synth": "합성(yosys)", "formal": "형식 검증(sby)", "fpga": "FPGA 배치·타이밍(nextpnr-ecp5)"}
FPGA_DEVICE = ("--85k", "--package", "CABGA381")
FPGA_TARGET_MHZ = 100
UTIL_NAMES = {"TRELLIS_COMB": "LUT", "TRELLIS_FF": "FF", "DP16KD": "블록RAM", "MULT18X18D": "DSP", "TRELLIS_IO": "핀"}


@dataclass
class EdaResult:
    kind: str
    ok: bool
    lines: list[str] = field(default_factory=list)
    folder: Path | None = None


def _exe(tools_dir: Path, name: str) -> Path:
    return Path(tools_dir) / "bin" / f"{name}.exe" if (Path(tools_dir) / "bin" / f"{name}.exe").is_file() \
        else Path(tools_dir) / "bin" / name


def _sources(project: RtlProject) -> str:
    files = [*(str(path) for path in project.deps), str(project.target)]
    return " ".join(f'"{path}"' for path in files)


def _out(project: RtlProject, kind: str) -> Path:
    folder = project.root / "reports" / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def parse_stat(log: str) -> tuple[int, list[tuple[str, int]]]:
    """마지막 'Printing statistics' 에서 (전체 셀 수, [(셀 종류, 개수)…]). 계층이 있으면 design hierarchy 의 합계."""
    tail = log[log.rfind("Printing statistics"):] if "Printing statistics" in log else log
    if "=== design hierarchy ===" in tail:
        tail = tail[tail.rfind("=== design hierarchy ==="):]
    total = re.search(r"^\s+(\d+)\s+cells\s*$", tail, flags=re.M)
    kinds = []
    if total:
        for line in tail[total.end():].splitlines()[1:]:
            match = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
            if not match:
                break
            kinds.append((match.group(2), int(match.group(1))))
    return (int(total.group(1)) if total else -1), kinds


def synth(project: RtlProject, tools_dir: Path, cancel_event: threading.Event | None = None,
          timeout: float = 600) -> EdaResult:
    out = _out(project, "synth")
    log = out / "synth.log"
    done = _tool_run([str(_exe(tools_dir, "yosys")), "-q", "-l", str(log), "-p",
                      f"read_verilog -sv {_sources(project)}; synth -top {project.top}; check; stat"],
                     project, tools_dir, timeout, cancel_event)
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else done.stdout + done.stderr
    if done.returncode != 0:
        errors = [line for line in (done.stderr + done.stdout + text).splitlines() if "ERROR" in line][:3]
        return EdaResult("synth", False, ["합성 실패: " + ("; ".join(errors) or f"종료 코드 {done.returncode}")], out)
    total, kinds = parse_stat(text)
    warnings = [line.strip() for line in text.splitlines() if line.startswith("Warning:")]
    latches = sorted({m.group(1) for m in re.finditer(r"Latch inferred for signal [`']?([^'\s]+)", text)})
    lines = [f"셀 {total:,}개" + (" — " + ", ".join(f"{name} {count}" for name, count in
                                                 sorted(kinds, key=lambda item: -item[1])[:8]) if kinds else "")]
    lines.append(f"경고 {len(warnings)}개" + ("" if not warnings else ": " + " / ".join(w[:160] for w in warnings[:3])))
    if latches:
        lines.append("래치가 생겼습니다(의도한 것이 아니면 버그): " + ", ".join(latches[:8]))
    return EdaResult("synth", not latches, lines, out)


def formal(project: RtlProject, tools_dir: Path, cancel_event: threading.Event | None = None,
           timeout: float = 1800) -> EdaResult:
    files = sorted({*project.root.glob("*.sby"), *(project.root / "verify").glob("*.sby")})
    if not files:
        return EdaResult("formal", False, [
            "형식 검증 설정(.sby)이 없습니다. 프로젝트 폴더나 verify/ 에 두면 돌립니다. 예:",
            "[options] mode prove / [engines] smtbmc / [script] read -formal 설계.v / prep -top 최상위 / [files] 설계.v",
            "설계 안 `ifdef FORMAL … assert(…) … `endif 로 성질을 적습니다."])
    lines, ok = [], True
    for sby in files:
        done = _tool_run([str(_exe(tools_dir, "sby")), "-f", str(sby.relative_to(project.root))],
                         project, tools_dir, timeout, cancel_event)
        output = done.stdout + done.stderr
        status = re.findall(r"DONE \((\w+)", output)
        verdict = status[-1] if status else f"오류(종료 코드 {done.returncode})"
        ok = ok and verdict == "PASS"
        detail = []
        for line in output.splitlines():
            line = re.sub(r"^.*?\] ", "", line)
            line = re.sub(r"##\s+\d+:\d+:\d+\s+", "", line).strip()
            if "Assert failed" in line:
                detail.append(re.sub(r"\s*\(_witness_[^)]*\)", "", line))
            elif line.endswith(".vcd") and "Writing trace to VCD file:" in line:
                stage, path = line.split(":", 1)[0], line.rsplit(": ", 1)[-1]
                detail.append(f"반례 파형({stage}): {sby.parent.name if sby.parent != project.root else ''}"
                              f"{'/' if sby.parent != project.root else ''}{sby.stem}/{path}")
        if verdict == "FAIL" and "returned pass for basecase" in output and "returned FAIL for induction" in output:
            detail.append("처음 몇 단계(basecase)는 통과 — 귀납만 실패: 실제 버그이거나, 도달할 수 없는 상태를 막는 성질(assert/assume)이 더 필요")
        lines.append(f"{sby.name}: {verdict}" + ("" if not detail else " — " + " / ".join(dict.fromkeys(detail[:5]))))
    return EdaResult("formal", ok, lines, project.root)


def fpga(project: RtlProject, tools_dir: Path, cancel_event: threading.Event | None = None,
         timeout: float = 1800, target_mhz: int = FPGA_TARGET_MHZ) -> EdaResult:
    out = _out(project, "fpga")
    netlist, report, log = out / "netlist.json", out / "pnr.json", out / "pnr.log"
    done = _tool_run([str(_exe(tools_dir, "yosys")), "-q", "-l", str(out / "synth_ecp5.log"), "-p",
                      f"read_verilog -sv {_sources(project)}; synth_ecp5 -top {project.top} -json \"{netlist}\""],
                     project, tools_dir, timeout, cancel_event)
    if done.returncode != 0 or not netlist.is_file():
        return EdaResult("fpga", False, [f"FPGA 합성 실패(종료 코드 {done.returncode}): {(done.stderr or done.stdout)[-300:]}"], out)
    done = _tool_run([str(_exe(tools_dir, "nextpnr-ecp5")), *FPGA_DEVICE, "--json", str(netlist), "--lpf-allow-unconstrained",
                      "--timing-allow-fail", "--report", str(report), "--seed", "1", "--freq", str(target_mhz), "--log", str(log)],
                     project, tools_dir, timeout, cancel_event)
    if done.returncode != 0 or not report.is_file():
        reason = [line for line in (done.stdout + done.stderr).splitlines() if line.startswith("ERROR")][:2]
        return EdaResult("fpga", False, ["배치·배선 실패: " + ("; ".join(reason) or f"종료 코드 {done.returncode}")
                                        + " (핀이 ECP5-85F 365개보다 많으면 최상위 포트를 줄여야 합니다)"], out)
    data = json.loads(report.read_text(encoding="utf-8"))
    clocks = data.get("fmax", {})
    lines = [", ".join(f"{re.sub(r'^.*?glbnet.|.TRELLIS.*$', '', name) or name} 최고 {value.get('achieved', 0):.0f}MHz"
                       f"(목표 {value.get('constraint', target_mhz):.0f})" for name, value in clocks.items())
             or "클록이 없는 조합 회로입니다(최고 주파수 없음)"]
    util = data.get("utilization", {})
    lines.append(", ".join(f"{UTIL_NAMES[key]} {util[key]['used']:,}/{util[key]['available']:,}"
                           for key in UTIL_NAMES if key in util and util[key].get("used")) or "사용량 정보 없음")
    lines.append("ECP5-85F 기준 추정(ASIC 아님) — 설계끼리 비교·회귀 확인용")
    ok = all(value.get("achieved", 0) >= value.get("constraint", 0) for value in clocks.values())
    return EdaResult("fpga", ok, lines, out)


RUNNERS = {"synth": synth, "formal": formal, "fpga": fpga}


def answer_text(project: RtlProject, results: list[EdaResult]) -> str:
    lines = [f"EDA 결과 — `{project.root.name}` (최상위 {project.top}"
             + (f", 검증된 블록 {len(project.deps)}개 함께" if project.deps else "") + ")"]
    for result in results:
        mark = "✓" if result.ok else "✗"
        lines.append(f"\n**{mark} {LABEL[result.kind]}**")
        lines += [f"- {line}" for line in result.lines]
        if result.folder is not None and result.kind != "formal":
            lines.append(f"- 기록: `{result.folder}`")
    return "\n".join(lines)
