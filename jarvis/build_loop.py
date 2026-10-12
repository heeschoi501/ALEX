"""만들기 루프 — 코딩 모델이 작업 폴더에 파일을 쓰고, 실행해 보고, 고친다(2026-10-05).

왜: 2026-10-05 테트리스 대화. ALEX 는 코드를 한 번 써서 넘기고 끝났다. 실행해 보지 않아 블록 7개 중 5개의 회전이
틀린 채 나갔고, 버그를 말해도 추측으로 고쳐(멀쩡한 코드를 원인으로 짚기도 했다) 사용자가 붙여 넣어 확인해야 했다.
코드가 대화 기록에 쌓여 두세 번 주고받으면 16K 문맥이 찼다. 모델이 바뀌어도 같은 일이 난다 — 구조 문제다.

공개 자료: Harness Engineering(arXiv 2609.00006) — 공개 코딩 에이전트 11개가 모두 직접 짠 단순 반복 루프를 쓰고, 도구는
적고 굵게(읽기·쓰기·고치기·실행) 두며, 도구 출력은 잘라서 돌려주고, 끝내기 전에 검증한다(verify-on-stop). 이 모듈은 그
최소 뼈대(90줄 하네스)를 ALEX 안전장치 안에 둔 것이다.

안전: 작업 폴더(data/builds/<id>) 밖은 읽거나 쓰지 않는다. 실행은 셸 없이 python 한 파일, 20초, 출력 끝 3,000자.
실행 전에 정적 검사로 파일 지우기·다른 프로그램 실행·네트워크·레지스트리를 막는다. 운영체제 수준 격리는 아니다 —
모델이 쓴 코드를 이 PC 에서 돌린다는 점은 developer.py 의 수정안 테스트와 같다.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import importlib.metadata
import json
from pathlib import Path, PureWindowsPath
import shutil
import sys
import tempfile
import threading
import time
from typing import Callable

from .build_runner import SCRATCH_DIR
from .providers import ContextExhausted, GenerationCancelled, Message, ProviderError

# 2026-10-05 실측(Q4 테트리스): 16번 호출·15.7분에 검사 6개 중 5개 통과 상태로 상한에 걸렸다(읽기·고치기에 호출을 썼다).
MAX_ROUNDS = 30            # 모델 호출 수. 한 호출에서 도구 여러 개를 부를 수 있다.
# 같은 날 Q4 는 16번에 15.7분, 이어서 30번에 9.5분이 걸렸다(검사 파일의 기대값 실수에서 맴돎). 기다림을 묶는다 —
# 통과하지 못해도 답에는 코드와 '계속 고쳐줘' 안내가 나간다. Q3 는 같은 일을 2분 43초에 통과했다.
MAX_SECONDS = 15 * 60      # 전체 시간 상한(진행 중인 호출은 끝까지 기다린다)
RUN_SECONDS = 20           # 한 번 실행 상한
MAX_FILE_CHARS = 200_000
MAX_FILES = 20
TOOL_OUTPUT_CHARS = 3_000
ALLOWED_SUFFIXES = {".py", ".txt", ".md", ".json", ".csv"}
HEADLESS_ENV = {"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "MPLBACKEND": "Agg",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# 모델이 쓴 코드가 이 PC 에서 하면 안 되는 일. 모듈을 통째로 막거나(import), 그 호출만 막는다.
BLOCKED_MODULES = {"subprocess", "socket", "ctypes", "winreg", "_winreg", "shutil", "requests", "urllib",
                   "http", "ftplib", "smtplib", "telnetlib", "multiprocessing", "webbrowser", "pty", "signal"}
BLOCKED_CALLS = {"os.system", "os.popen", "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename",
                 "os.renames", "os.replace", "os.kill", "os.startfile", "os.chmod", "os.chdir",
                 "__import__", "importlib.import_module"}
BLOCKED_PREFIXES = ("os.exec", "os.spawn", "os.fork")
# str.replace 처럼 흔한 이름은 넣지 않는다 — 계산기·게임 코드가 막힌다.
BLOCKED_METHODS = {"unlink", "rmdir", "rmtree", "chmod", "rename", "symlink_to"}

BUILD_TOOLS = [
    {"type": "function", "function": {
        "name": "write_file", "description": "작업 폴더에 파일을 새로 쓰거나 통째로 덮어쓴다. 처음 만들 때 쓴다.",
        "parameters": {"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string", "description": "작업 폴더 안 상대 경로. 예: tetris.py"},
            "content": {"type": "string", "description": "파일 전체 내용"}}}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "파일에서 old 와 정확히 같은 부분 한 곳을 new 로 바꾼다. 고칠 때는 이것을 쓴다.",
        "parameters": {"type": "object", "required": ["path", "old", "new"], "properties": {
            "path": {"type": "string"}, "old": {"type": "string", "description": "바꿀 부분(파일에 한 번만 있어야 한다)"},
            "new": {"type": "string", "description": "새 내용"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "작업 폴더의 파일을 읽는다(앞 12,000자).",
        "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "run_python", "description": "작업 폴더의 파이썬 파일을 실행하고 종료 코드와 출력 끝부분을 돌려준다. "
                                             "화면 없는 환경이다(pygame 은 dummy 영상 장치). 20초 제한.",
        "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "finish", "description": "검사 파일이 통과한 뒤 일을 끝낸다. 무엇을 만들었고 어떻게 확인했는지 요약한다.",
        "parameters": {"type": "object", "required": ["summary", "main_file"], "properties": {
            "summary": {"type": "string"}, "main_file": {"type": "string", "description": "사용자가 실행할 파일"}}}}},
]


def unsafe_reason(source: str) -> str | None:
    """실행하면 안 되는 코드면 이유를 돌려준다. 문법 오류는 여기서 막지 않는다(실행하면 모델이 오류를 본다)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_MODULES:
                    return f"import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in BLOCKED_MODULES:
                return f"from {node.module} import"
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in BLOCKED_CALLS or name.startswith(BLOCKED_PREFIXES):
                return f"{name}()"
            if isinstance(node.func, ast.Attribute) and node.func.attr in BLOCKED_METHODS:
                return f".{node.func.attr}()"
            if name in {"open", "io.open"} and node.args:
                target = node.args[0]
                if isinstance(target, ast.Constant) and isinstance(target.value, str) and _escapes(target.value):
                    return f"open({target.value!r}) — 작업 폴더 밖"
    return None


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _escapes(path: str) -> bool:
    candidate = Path(path.replace("\\", "/"))
    return candidate.is_absolute() or bool(PureWindowsPath(path).drive) or ":" in path or ".." in candidate.parts


def api_summary(workspace: "BuildWorkspace", limit: int = 2_000) -> str:
    """작업 폴더 프로그램(검사 파일 제외)에 실제로 있는 함수·클래스·메서드. 실행이 실패하면 함께 돌려준다.

    2026-10-05 실측: Q4·Q3 모두 검사 파일이 프로그램에 없는 메서드(drop·soft_drop)를 불러 실패했고, 검사와 프로그램을 번갈아 고치다
    호출 상한에 걸렸다. 실제 API 를 보여 주면 어느 쪽을 맞출지 사실로 정할 수 있다(developer 의 API 요약과 같은 생각)."""
    lines: list[str] = []
    for name in workspace.files():
        if not name.endswith(".py") or _is_test(name):
            continue
        try:
            tree = ast.parse((workspace.root / name).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            lines.append(f"{name}: 문법 오류 {exc.lineno}행")
            continue
        items = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                items.append(f"def {node.name}({', '.join(a.arg for a in node.args.args)})")
            elif isinstance(node, ast.ClassDef):
                methods = [f"{m.name}({', '.join(a.arg for a in m.args.args[1:])})" for m in node.body
                           if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                items.append(f"class {node.name}: " + ", ".join(methods))
        if items:
            lines.append(f"{name}: " + " | ".join(items))
    text = "\n".join(lines)
    return text[:limit] + ("…" if len(text) > limit else "")


def installed_packages(limit: int = 40) -> list[str]:
    names = set()
    for dist in importlib.metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if name and not name.lower().startswith(("pip", "setuptools", "wheel")):
            names.add(name)
    return sorted(names, key=str.lower)[:limit]


@dataclass
class BuildResult:
    workspace: Path
    files: list[str]
    verified: bool
    summary: str = ""
    main_file: str = ""
    runs: list[tuple[str, int]] = field(default_factory=list)
    rounds: int = 0
    stopped: str = ""


class BuildWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_run_checks = 0

    def resolve(self, path: object) -> Path:
        text = str(path or "").strip().replace("\\", "/")
        parts = text.split("/")
        if self.root.name in parts[:-1]:  # 2026-10-05 07:59: 모델이 작업 폴더 이름을 다시 붙여 하위 폴더가 겹겹이 생겼다
            text = "/".join(parts[parts.index(self.root.name) + 1:])
        if not text or _escapes(text):
            raise ValueError(f"작업 폴더 안의 상대 경로만 쓸 수 있다: {text!r}")
        raw = self.root / text
        if any(p.is_symlink() for p in (raw, *raw.parents) if p != self.root and self.root in p.parents):
            raise ValueError("연결된 경로는 작업 파일로 사용할 수 없다.")
        target = raw.resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError(f"작업 폴더 밖이다: {text!r}")
        if target.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"이 확장자는 쓸 수 없다({', '.join(sorted(ALLOWED_SUFFIXES))}): {text!r}")
        if target.is_file() and target.stat().st_nlink > 1:
            raise ValueError("하드 링크는 작업 파일로 사용할 수 없다.")
        return target

    def files(self) -> list[str]:
        names = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                       if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES and "__pycache__" not in p.parts
                       and SCRATCH_DIR not in p.relative_to(self.root).parts)
        for name in names:
            self.resolve(name)
        return names

    def source_digest(self) -> str:
        digest = hashlib.sha256()
        for name in self.files():
            if name.lower().endswith(".py"):
                digest.update(name.encode() + b"\0" + self.resolve(name).read_bytes())
        return digest.hexdigest()

    def write(self, path: object, content: object) -> str:
        target = self.resolve(path)
        text = str(content if content is not None else "")
        if len(text) > MAX_FILE_CHARS:
            return f"너무 크다({len(text)}자 > {MAX_FILE_CHARS}자). 나눠서 써라."
        if not target.exists() and len(self.files()) >= MAX_FILES:
            return f"파일은 {MAX_FILES}개까지다."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return f"{target.relative_to(self.root).as_posix()} 저장({len(text)}자, {text.count(chr(10)) + 1}줄)."

    def edit(self, path: object, old: object, new: object) -> str:
        target = self.resolve(path)
        if not target.is_file():
            return f"{path} 가 없다. write_file 로 먼저 만든다."
        text = target.read_text(encoding="utf-8")
        old, new = str(old or ""), str(new if new is not None else "")
        count = text.count(old) if old else 0
        if count != 1:
            return (f"old 와 정확히 같은 부분이 {count}곳이다. 한 곳만 맞도록 앞뒤 줄을 더 넣거나 read_file 로 "
                    "현재 내용을 확인한다.")
        replacement = text.replace(old, new, 1)
        if len(replacement) > MAX_FILE_CHARS:
            return f"너무 크다({len(replacement)}자 > {MAX_FILE_CHARS}자). 나눠서 써라."
        target.write_text(replacement, encoding="utf-8")
        return f"{target.relative_to(self.root).as_posix()} 고침({len(old)}자 → {len(new)}자)."

    def read(self, path: object) -> str:
        target = self.resolve(path)
        if not target.is_file():
            return f"{path} 가 없다. 있는 파일: {', '.join(self.files()) or '(없음)'}"
        text = target.read_text(encoding="utf-8", errors="replace")
        return text[:12_000] + (f"\n…(뒤 {len(text) - 12_000}자 생략)" if len(text) > 12_000 else "")

    def run(self, path: object, cancel_event: threading.Event | None,
            limit_seconds: float | None = None) -> tuple[int, str]:
        from .developer import _run_bounded  # 같은 실행기: 셸 없이, 시간·출력 제한, 중단 지원

        self.last_run_checks = 0
        _check_cancelled(cancel_event)
        target = self.resolve(path)
        if target.suffix.lower() != ".py" or not target.is_file():
            return 2, f"{path} 는 실행할 수 있는 .py 파일이 아니다."
        for name in self.files():
            if name.endswith(".py"):
                reason = unsafe_reason((self.root / name).read_text(encoding="utf-8", errors="replace"))
                if reason:
                    return 2, (f"실행하지 않았다: {name} 에 이 실행 환경에서 막힌 코드가 있다 — {reason}. "
                               "파일 지우기·다른 프로그램 실행·네트워크·작업 폴더 밖 접근 없이 다시 쓴다.")
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe" and (python.parent / "python.exe").is_file():
            python = python.parent / "python.exe"
        runner = Path(__file__).with_name("build_runner.py")
        # 실행마다 빈 임시 폴더를 TEMP 로 준다 — 그 안에서만 지우기가 허용된다(build_runner.SCRATCH_DIR). 끝나면 부모가 치운다.
        scratch = self.root / SCRATCH_DIR
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir()
        scratch_env = {"TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch)}
        try:
            with tempfile.TemporaryDirectory(prefix="alex-build-evidence-") as tmp:
                evidence = Path(tmp) / "checks.json"
                command = [str(python), "-I", "-X", "utf8", "-B", str(runner), str(target), str(self.root), str(evidence)]
                status, output = _run_bounded(command, self.root, cancel_event, "실행", limit_seconds=limit_seconds or RUN_SECONDS,
                                              extra_env={**HEADLESS_ENV, **scratch_env}, inherit_environment=False)
                try:
                    checks = json.loads(evidence.read_text(encoding="utf-8")).get("checks", 0)
                    self.last_run_checks = checks if type(checks) is int and checks > 0 else 0
                except (OSError, ValueError, AttributeError):
                    pass
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if status == 124:
            output += f"\n[{RUN_SECONDS}초 안에 끝나지 않아 멈췄다. 게임 루프처럼 계속 도는 프로그램은 검사 파일에서 " \
                      "몇 프레임만 돌리거나 로직만 따로 검사한다.]"
        return status, output[-TOOL_OUTPUT_CHARS:]


def system_prompt(packages: list[str]) -> str:
    return (
        "너는 ALEX 의 만들기 모드다. 사용자가 부탁한 프로그램을 작업 폴더에 직접 만들고, 실행해서 확인하고, 고친다.\n"
        "순서:\n"
        "1. write_file 로 프로그램을 쓴다.\n"
        "2. 핵심 동작을 확인하는 검사 파일(test_*.py)을 쓴다. 예: 테트리스면 블록이 회전해도 같은 모양인지(칸 4개, "
        "회전 4번에 원래대로), 줄이 차면 지워지는지, 벽을 넘지 않는지. assert 로 확인하고 성공하면 'OK' 를 출력한다. "
        "화면·키보드 없이 로직만 검사할 수 있게 프로그램을 짠다(로직과 화면 그리기를 나누고, import 할 때 창을 띄우지 "
        "않게 `if __name__ == '__main__':` 아래에서만 실행).\n"
        "3. run_python 으로 검사 파일을 실행한다. 실패하면 오류를 읽고 edit_file 로 고친 뒤 다시 실행한다. 추측하지 말고 "
        "출력에 나온 사실로 고친다. 검사 파일은 프로그램에 실제로 있는 함수·메서드만 부른다(실패하면 실제 목록을 함께 보여 준다).\n"
        "4. 모든 검사 파일에서 assert 또는 unittest 검사가 실제 실행되어 통과하면 finish 를 부른다. "
        "검사 없는 실행, print(\"OK\")만 있는 파일은 통과가 아니다. 소스를 고치면 모든 검사를 다시 실행한다.\n"
        "규칙: 회전·좌표 같은 표는 손으로 쓰지 말고 계산으로 만든다. 파일 지우기·다른 프로그램 실행(subprocess)·네트워크·"
        "작업 폴더 밖 접근은 막혀 있다. 이 PC 의 Python "
        f"{sys.version_info.major}.{sys.version_info.minor} 에 설치된 패키지: {', '.join(packages) or '(표준 라이브러리만)'}. "
        "여기 없는 패키지(예: pygame)가 필요하면 그 패키지를 쓰는 화면 부분은 그대로 두되, 검사는 그 패키지 없이 "
        "돌아가는 로직 모듈만 대상으로 한다. 대답과 요약은 한국어로 한다."
    )


def run_build(provider, task: str, workspace: BuildWorkspace, *,
              progress: Callable[[str], None] | None = None,
              cancel_event: threading.Event | None = None,
              max_rounds: int = MAX_ROUNDS, max_seconds: float = MAX_SECONDS) -> BuildResult:
    """모델이 도구로 파일을 쓰고 실행하며 고치게 한다. 검사가 통과해야 끝낸다(verify-on-stop)."""
    say = progress or (lambda _text: None)
    existing = workspace.files()
    intro = task if not existing else (
        f"{task}\n\n작업 폴더에 이미 있는 파일: {', '.join(existing)}. read_file 로 현재 내용을 보고 고친다.")
    conversation: list[Message] = [Message("system", system_prompt(installed_packages())), Message("user", intro)]
    result = BuildResult(workspace.root, existing, False)
    passed: dict[str, str] = {}
    last_run: tuple[str, int] | None = None
    nudges = 0
    started = time.monotonic()

    def verified() -> bool:
        tests = [f for f in workspace.files() if _is_test(f)]
        current = workspace.source_digest()
        return bool(tests) and all(passed.get(name) == current for name in tests)

    for round_number in range(1, max_rounds + 1):
        _check_cancelled(cancel_event)
        if time.monotonic() - started > max_seconds:
            result.stopped = f"{max_seconds // 60:.0f}분 상한"
            break
        result.rounds = round_number
        try:
            content, calls = _ask(provider, conversation)
        except GenerationCancelled:
            raise
        except ProviderError as exc:
            result.stopped = f"모델 호출 실패: {exc}"
            break  # Keep created files and report an unverified result, not a one-shot fallback.
        _check_cancelled(cancel_event)
        if not calls:
            if verified():
                result.summary = content.strip()
                result.verified = True
                break
            nudges += 1
            if nudges > 3:
                result.stopped = "모델이 도구를 부르지 않음"
                result.summary = content.strip()
                break
            conversation.append(Message("assistant", content))
            conversation.append(Message("user", _nudge(workspace, last_run, False)))
            continue
        conversation.append(Message("assistant", content, tool_calls=_compact_calls(calls)))
        finished = False
        for call in calls:
            _check_cancelled(cancel_event)
            if time.monotonic() - started > max_seconds:
                result.stopped = f"{max_seconds // 60:.0f}분 상한"
                break
            name, args = _call_parts(call)
            if name == "finish":
                main = str(args.get("main_file", "")).strip()
                if verified() and main in workspace.files() and not _is_test(main):
                    result.summary = str(args.get("summary", "")).strip()
                    result.main_file = main
                    result.verified = finished = True
                    observation = "끝."
                else:
                    say("[끝내기 거절: 모든 검사 통과와 실행 파일 확인 필요]\n")
                    observation = ("아직 끝낼 수 없다. 현재 코드에서 모든 검사 파일의 실제 검사를 실행해야 한다. "
                                   "main_file은 작업 폴더에 있는 프로그램 파일이어야 한다. "
                                   + _nudge(workspace, last_run, False))
            else:
                before = workspace.source_digest()
                observation, changed, run = _apply(workspace, name, args, cancel_event, say)
                if changed:
                    passed.clear()
                if run is not None:
                    last_run = run
                    result.runs.append(run)
                    after = workspace.source_digest()
                    if run[1] != 0 or before != after:
                        passed.clear()
                    if _is_test(run[0]):
                        passed.pop(run[0], None)
                        if run[1] == 0 and workspace.last_run_checks > 0 and before == after:
                            passed[run[0]] = after
                        elif run[1] == 0:
                            observation += "\n검사 통과로 기록하지 않았다: 실제 검사가 없거나 실행 중 Python 소스가 바뀌었다."
            conversation.append(Message("tool", observation[:TOOL_OUTPUT_CHARS], tool_name=name))
            if finished:
                break
        _compact_history(conversation)
        if finished or result.stopped:
            break
    else:
        result.stopped = f"{max_rounds}번 상한"
    result.files = workspace.files()
    return result


def _check_cancelled(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise GenerationCancelled("만들기 작업을 중단했습니다.")


def _is_test(path: str) -> bool:
    name = Path(str(path)).name
    return name.startswith("test") and name.endswith(".py")


def _ask(provider, conversation: list[Message]) -> tuple[str, list[dict]]:
    provider.last_tool_calls = []
    try:
        content = provider._collect_stream(conversation, None, tools=BUILD_TOOLS)
    except GenerationCancelled:
        raise
    except ContextExhausted:
        if not getattr(provider, "think", False):
            raise
        provider.think = False  # 생각이 답 자리까지 먹으면 이번 차례는 생각 없이
        try:
            content = provider._collect_stream(conversation, None, tools=BUILD_TOOLS)
        finally:
            provider.think = True
    return content, [c for c in provider.last_tool_calls if isinstance(c, dict)]


def _call_parts(call: dict) -> tuple[str, dict]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    return str(function.get("name", "")), arguments if isinstance(arguments, dict) else {}


def _apply(workspace: BuildWorkspace, name: str, args: dict, cancel_event, say) -> tuple[str, bool, tuple[str, int] | None]:
    try:
        if name == "write_file":
            say(f"[파일 쓰기: {args.get('path')}]\n")
            return workspace.write(args.get("path"), args.get("content")), True, None
        if name == "edit_file":
            say(f"[파일 고치기: {args.get('path')}]\n")
            return workspace.edit(args.get("path"), args.get("old"), args.get("new")), True, None
        if name == "read_file":
            say(f"[파일 읽기: {args.get('path')}]\n")
            return workspace.read(args.get("path")), False, None
        if name == "run_python":
            path = workspace.resolve(args.get("path")).relative_to(workspace.root).as_posix()
            say(f"[실행: {path}]\n")
            status, output = workspace.run(path, cancel_event)
            if status == 130:
                raise GenerationCancelled("만들기 실행을 중단했습니다.")
            say(f"[실행 결과: 종료 코드 {status}]\n")
            observation = f"종료 코드 {status}\n출력(끝부분):\n{output}"
            if status != 0:
                summary = api_summary(workspace)
                if summary:
                    observation += ("\n\n프로그램에 실제로 있는 함수·메서드(검사는 이것만 부른다. 없는 것을 부르면 검사를 고치고, "
                                    "필요한 기능이 빠졌으면 프로그램에 넣는다):\n" + summary)
            return observation, False, (path, status)
    except (ValueError, OSError) as exc:
        return f"{name} 실패: {exc}", False, (str(args.get("path", "")), 2) if name == "run_python" else None
    return f"'{name}' 라는 도구는 없다. write_file·edit_file·read_file·run_python·finish 만 쓴다.", False, None


def _nudge(workspace: BuildWorkspace, last_run: tuple[str, int] | None, verified: bool) -> str:
    files = ", ".join(workspace.files()) or "(아직 없음)"
    if not workspace.files():
        return "아직 파일이 없다. write_file 로 프로그램과 검사 파일(test_*.py)을 만든다."
    if last_run is None:
        return f"아직 실행해서 확인하지 않았다(파일: {files}). 검사 파일을 run_python 으로 실행한다."
    if not verified:
        return (f"현재 코드의 모든 검사 통과 근거가 아직 없다(최근 {last_run[0]} → {last_run[1]}). 검사 파일(test_*.py)을 "
                "다시 실행해 종료 코드 0 을 확인한 뒤 finish 를 부른다.")
    return "검사가 통과했다. finish 로 무엇을 만들었고 어떻게 확인했는지 요약한다."


def _compact_calls(calls: list[dict]) -> list[dict]:
    """파일 전체 내용을 대화에 계속 싣지 않는다 — 16K 문맥이 두세 번 쓰기로 찬다. 파일에는 저장돼 있다."""
    compact = []
    for call in calls:
        name, arguments = _call_parts(call)
        function = {"name": name}
        if isinstance(arguments, dict):
            arguments = dict(arguments)
            for key in ("content", "old", "new"):
                value = arguments.get(key)
                if isinstance(value, str) and len(value) > 600:
                    arguments[key] = f"<{len(value)}자 — 파일에 반영됨, 필요하면 read_file>"
            function["arguments"] = arguments
        compact.append({**call, "function": function})
    return compact


def _compact_history(conversation: list[Message], keep: int = 4) -> None:
    """오래된 도구 출력(읽은 파일·실행 출력)은 최근 몇 개만 남기고 줄인다(문맥 초과 방지, 단순 임계 압축)."""
    tool_indexes = [i for i, m in enumerate(conversation) if m.role == "tool"]
    for index in tool_indexes[:-keep]:
        message = conversation[index]
        if len(message.content) > 300:
            conversation[index] = Message("tool", message.content[:200] + "\n…(이전 출력 생략)",
                                          tool_name=message.tool_name)


def answer_text(result: BuildResult, *, max_code_chars: int = 30_000) -> str:
    """사용자에게 보일 답: 검증 결과·파일 위치·실행할 파일 코드."""
    status = ("검사 통과(종료 코드 0)" if result.verified
              else f"검사를 통과하지 못했습니다{' — ' + result.stopped if result.stopped else ''}")
    runs = ", ".join(f"{Path(path).name}→{code}" for path, code in result.runs[-6:]) or "없음"
    lines = [f"**만들기 결과: {status}**", f"- 작업 폴더: `{result.workspace}`",
             f"- 파일: {', '.join(result.files) or '없음'}", f"- 실행 기록(최근): {runs} / 모델 호출 {result.rounds}번"]
    if result.summary:
        lines += ["", result.summary]
    main = result.main_file if result.main_file in result.files else next(
        (f for f in result.files if f.endswith(".py") and not Path(f).name.startswith("test")), "")
    if main:
        code = (result.workspace / main).read_text(encoding="utf-8", errors="replace")
        if len(code) <= max_code_chars:
            lines += ["", f"`{main}`:", "```python", code.rstrip(), "```"]
    if not result.verified:
        lines += ["", "검사가 통과하지 않은 코드입니다. 이어서 \"계속 고쳐줘\" 라고 하면 같은 작업 폴더에서 이어서 고칩니다."]
    return "\n".join(lines)


MAKE_WORDS = ("만들어", "작성해", "짜줘", "짜 줘", "짜봐", "구현해", "개발해", "코딩해", "만들자")
BUILD_OBJECTS = ("게임", "프로그램", "코드", "스크립트", "앱", "계산기", "도구", "봇", "파이썬", "python", "함수", "클래스")
EXPLAIN_WORDS = ("설명", "뭐야", "무엇인", "차이", "방법 알려", "어떻게 만드")
FIX_WORDS = ("고쳐", "수정", "안돼", "안 돼", "안되", "안 되", "바껴", "바뀌", "이상해", "버그", "에러", "오류", "계속",
             "다시", "추가해", "넣어", "빼줘", "바꿔", "동작", "작동")


def is_build_request(text: str) -> bool:
    """새 프로그램을 만들어 달라는 부탁인가. 설명·방법 질문은 예전처럼 글로 답한다."""
    lowered = text.lower()
    return (any(word in lowered for word in MAKE_WORDS) and any(word in lowered for word in BUILD_OBJECTS)
            and not any(word in lowered for word in EXPLAIN_WORDS))


def is_build_followup(text: str) -> bool:
    """만들던 프로그램을 고치거나 이어 만들어 달라는 말인가("회전이 안 돼", "점수 표시 추가해")."""
    lowered = text.lower()
    return any(word in lowered for word in FIX_WORDS) and not any(word in lowered for word in EXPLAIN_WORDS)
