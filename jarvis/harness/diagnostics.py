"""고친 직후 오류 알려 주기 — OpenCode 는 언어 서버(LSP) 진단의 '오류'만 'LSP errors detected in this file, please fix:' 로
도구 결과 끝에 붙인다(lsp/diagnostic.ts 형식 그대로, 파일당 20개). ALEX 는 이 PC 의 검사 도구로 같은 일을 한다(ADR-112):
- 파이썬: compile(문법) + ruff(C:\\ALEX\\tools\\lint\\ruff.exe 0.16.10) 의 확실한 오류만 — 정의 안 된 이름(F821·F822·F823),
  함수 밖 return/yield(F706·F704). 안 쓴 import 같은 경고는 알리지 않는다(OpenCode 도 오류만).
- Verilog/SystemVerilog: yosys read_verilog(문법만 — 다른 파일 모듈을 못 찾는 오류가 섞이지 않게).
- JSON: json.loads. 도구가 없으면 그 검사만 건너뛴다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

TOOLS = {
    "ruff": Path(__file__).resolve().parents[3] / "tools" / "lint" / ("ruff.exe" if os.name == "nt" else "ruff"),
    "yosys": Path(r"C:\oss-cad-suite\bin\yosys.exe") if os.name == "nt" else Path("/usr/bin/yosys"),
}
RUFF_ERRORS = "F821,F822,F823,F706,F704"


def set_tool(name: str, path: Path | None) -> None:
    if path is not None:
        TOOLS[name] = Path(path)


def _report(path: Path, errors: list[tuple[int, int, str]]) -> str:
    if not errors:
        return ""
    rows = "\n".join(f"ERROR [{line}:{col}] {message}" for line, col, message in errors[:20])
    more = f"\n... and {len(errors) - 20} more" if len(errors) > 20 else ""
    return f"\n\nLSP errors detected in this file, please fix:\n<diagnostics file=\"{path}\">\n{rows}{more}\n</diagnostics>"


def _run(command: list[str], cwd: Path, env: dict | None = None) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=20, creationflags=flags, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout + done.stderr


def python_errors(path: Path, text: str) -> list[tuple[int, int, str]]:
    try:
        compile(text, str(path), "exec", dont_inherit=True)
    except SyntaxError as exc:
        return [(exc.lineno or 1, exc.offset or 1, exc.msg)]
    ruff = TOOLS.get("ruff")
    if not ruff or not ruff.is_file():
        return []
    out = _run([str(ruff), "check", "--isolated", "--no-cache", "--output-format", "json", "--select", RUFF_ERRORS,
                str(path)], path.parent)
    try:
        items = json.loads(out[out.find("["):]) if "[" in out else []
    except ValueError:
        return []
    return [(int(i["location"]["row"]), int(i["location"]["column"]), f"{i['message']} ({i['code']})")
            for i in items if isinstance(i, dict) and i.get("location")]


def verilog_errors(path: Path) -> list[tuple[int, int, str]]:
    yosys = TOOLS.get("yosys")
    if not yosys or not yosys.is_file():
        return []
    from ..rtl_loop import tools_env  # oss-cad-suite 는 자기 bin·lib 가 PATH 에 있어야 DLL 을 찾는다(RTL 실행부와 같게)
    out = _run([str(yosys), "-q", "-p", f"read_verilog -sv \"{path.name}\""], path.parent, tools_env(yosys.parent.parent))
    errors = []
    for line in out.splitlines():
        # yosys 0.6x: 'bad.v:2: ERROR: syntax error, unexpected ...' · 옛 모양: 'ERROR: Parser error in line bad.v:2: ...'
        match = re.search(r"([^\s:]+):(\d+):\s*ERROR:\s*(.*)$", line) or re.search(
            r"ERROR:\s*.*?in line\s+[^\s:]+:(\d+):\s*(.*)$", line)
        if match and match.lastindex == 3:
            errors.append((int(match.group(2)), 1, match.group(3).strip()))
        elif match:
            errors.append((int(match.group(1)), 1, match.group(2).strip()))
        elif "ERROR:" in line:
            errors.append((1, 1, line.split("ERROR:", 1)[1].strip()))
    return errors


def diagnose(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    if suffix == ".py":
        return _report(path, python_errors(path, text))
    if suffix in (".v", ".sv", ".vh", ".svh"):
        return _report(path, verilog_errors(path))
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return _report(path, [(exc.lineno, exc.colno, exc.msg)])
    return ""
