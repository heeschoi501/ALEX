"""채팅 코드 답의 뒤쪽 실행 확인 — 코드는 답으로 주고, ALEX 가 보이지 않는 임시 폴더에서 한 번 돌려 본다(2026-10-05).

사용자(2026-10-05 08:0x): "코드를 짜서 나한테 주는 게 아니라 그냥 직접 만들어버리면 어쩌자는 거야", "그냥 코드를 짜서 달라고 한 건데".
클라우드 채팅(ChatGPT·Claude.ai)처럼 코드는 답으로 주고, 확인은 자기 쪽 격리 공간에서만 한다. 사용자 쪽에는 파일을 남기지 않는다.
같은 날 실측: 한 번에 쓴 테트리스는 Q3 9번 중 6번이 시작하자마자 실제 버그로 죽었다(`logs/claude-fixes-20261005/prompt-ab/`).
시작 직후 몇 초만 돌려 봐도 그런 버그는 대부분 드러난다.

실행은 만들기 루프와 같은 보호(`BuildWorkspace.run` → 정적 차단 + build_runner 의 실행 중 감시, 셸 없음, 시간·출력 제한)를 쓴다.
운영체제 수준 격리는 아니다. 창을 띄우는 프로그램(tkinter·turtle 등)·입력을 기다리는 프로그램·설치 안 된 패키지는 실행하지 않고
"확인 못 함" 으로 알린다(문법은 확인한다).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import threading

from .build_loop import BuildWorkspace, unsafe_reason
from .providers import GenerationCancelled

CHECK_SECONDS = 6
MAX_REPAIRS = 2
WINDOW_MODULES = {"tkinter", "turtle", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "kivy", "pyglet", "arcade"}
CODE_BLOCK = re.compile(r"```([^\n`]*)\n(.*?)```", re.S)


@dataclass
class CodeCheck:
    status: str          # ok_finished · ok_running · failed · unverifiable · partial
    detail: str = ""
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("ok_finished", "ok_running")


def extract_python(answer: str) -> str | None:
    """답에서 실행할 파이썬 코드 블록 하나(가장 긴 것). bash 등 다른 언어 블록은 고르지 않는다."""
    blocks = []
    for match in CODE_BLOCK.finditer(answer or ""):
        language, body = match.group(1).strip().lower(), match.group(2)
        if language in ("python", "py", "python3") or (language == "" and _parses(body)):
            blocks.append(body)
    if not blocks:
        return None
    code = max(blocks, key=len)
    return code if code.strip() else None


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def is_complete_program(tree: ast.Module) -> bool:
    """단독으로 실행해 볼 만한 프로그램인가. 표·함수 하나만 고친 조각은 실행해도 아무것도 확인하지 못한다
    (2026-10-05 08:22: 회전표 조각만 준 답에 '실행 확인: 오류 없이 끝남' 이 붙었다)."""
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # 문서 문자열
        if isinstance(node, (ast.Expr, ast.For, ast.While, ast.If, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)):
            return True
    return False


def _imports(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def check_code(code: str, cancel_event: threading.Event | None = None, *, seconds: float = CHECK_SECONDS) -> CodeCheck:
    try:
        tree = ast.parse(code)
        compile(code, "main.py", "exec")
    except SyntaxError as exc:
        return CodeCheck("failed", f"문법 오류 {exc.lineno}행: {exc.msg}", f"SyntaxError: {exc.msg} (line {exc.lineno})\n{exc.text or ''}")
    if not is_complete_program(tree):
        return CodeCheck("partial", "일부만 담은 코드라 따로 실행해 볼 수 없습니다")
    reason = unsafe_reason(code)
    if reason:
        return CodeCheck("unverifiable", f"실행 보호 규칙에 걸리는 코드라 실행하지 않았습니다({reason})")
    if re.search(r"\binput\s*\(", code):
        # 실행하면 입력을 기다리거나(콘솔) 바로 EOFError 가 난다(웹 서버) — 어느 쪽도 코드의 버그가 아니다.
        return CodeCheck("unverifiable", "사용자 입력을 받는 프로그램이라 자동으로 실행하지 않았습니다")
    imports = _imports(tree)
    windows = sorted(imports & WINDOW_MODULES)
    if windows:
        return CodeCheck("unverifiable", f"창을 띄우는 프로그램({', '.join(windows)})이라 화면 없이 실행할 수 없습니다")
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    missing = sorted(name for name in imports
                     if name not in stdlib and name != "__future__" and importlib.util.find_spec(name) is None)
    if missing:
        return CodeCheck("unverifiable", f"{', '.join(missing)} 이(가) ALEX 파이썬에 설치돼 있지 않아 실행하지 못했습니다")
    with tempfile.TemporaryDirectory(prefix="alex-code-check-") as tmp:
        workspace = BuildWorkspace(Path(tmp))
        workspace.write("main.py", code)
        status, output = workspace.run("main.py", cancel_event, limit_seconds=seconds)
    if status == 130:
        raise GenerationCancelled("실행 확인을 중단했습니다.")
    if status == 0:
        return CodeCheck("ok_finished", "오류 없이 끝났습니다", output)
    if status == 124:
        return CodeCheck("ok_running", f"시작 후 {seconds:.0f}초 동안 오류 없이 실행됐습니다", output)
    if "EOFError" in output and "input(" in code:
        return CodeCheck("unverifiable", "입력을 기다리는 프로그램이라 자동으로 끝까지 실행하지 못했습니다", output)
    if "ALEX 실행 보호" in output:
        return CodeCheck("unverifiable", "실행 보호 규칙(작업 폴더 밖 접근 등)에 걸려 확인하지 못했습니다", output)
    if "ModuleNotFoundError" in output:
        return CodeCheck("unverifiable", "필요한 모듈이 없어 실행하지 못했습니다", output)
    last = next((line.strip() for line in reversed(output.strip().splitlines()) if line.strip()), f"종료 코드 {status}")
    return CodeCheck("failed", last[:200], output)


def repair_request(result: CodeCheck) -> str:
    return ("위 코드를 실행해 봤더니 시작하자마자 오류가 났습니다.\n```\n" + result.output[-2_500:].strip() + "\n```\n"
            "추측하지 말고 이 오류의 원인을 고친 **전체 코드**를 코드 블록 하나로 다시 주세요. 설명은 두세 줄로 짧게.")


def footer(result: CodeCheck, fixed: list[str]) -> str:
    lines = []
    if fixed:
        lines.append("처음 코드는 실행하자마자 오류가 나서 ALEX가 고쳤습니다: " + " / ".join(fixed))
    if result.ok:
        lines.append(f"실행 확인: ALEX가 이 코드를 따로 실행해 봤고 {result.detail}(화면 없이 확인, 파일은 남기지 않음).")
    elif result.status == "unverifiable":
        lines.append(f"실행 확인 못 함: {result.detail}. 문법은 확인했습니다.")
    elif result.status == "partial":
        lines.append(f"실행 확인 안 함: {result.detail}. 고친 전체 코드가 필요하면 '전체 코드 줘' 라고 해 주세요.")
    else:
        lines.append(f"실행 확인 안 됨: 고쳐 봤지만 여전히 오류가 납니다 — {result.detail}")
    return "\n\n[" + "]\n[".join(lines) + "]"
