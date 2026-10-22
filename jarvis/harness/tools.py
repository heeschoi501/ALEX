"""ALEX 코딩 하네스의 도구(2026-10-22, ADR-112) — OpenCode v1.18.34 의 read·write·edit·apply_patch·glob·grep·bash·todowrite·
skill·webfetch·websearch 와 같은 입력·출력 모양(MIT, OPENCODE_LICENSE.txt). 모델이 OpenCode 에서 받던 것과 같은 결과를 받아야
같은 능력이 나온다. (task·question 은 반복(agent.py)이 직접 다룬다 — 하위 에이전트를 띄우고, 사람에게 묻고 멈춘다.)

ALEX 가 지키는 것: 작업 폴더(root) 밖은 읽지도 쓰지도 않는다(잘라 둔 도구 출력 폴더만 읽기 예외). 이미 있는 파일은 읽은 뒤에만
쓰고 고친다. 모든 도구는 권한 규칙(permission.py — 마지막으로 맞는 규칙, allow·ask·deny)을 거친다. 셸 명령은 셸 없이
(파이프·리다이렉션 거절) 실행한다. 바꾸기 전 내용을 차례마다 남겨 되돌릴 수 있다(snapshot). 실패는 'Error: …' 글로 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from . import edit as editing
from . import patch as patching
from .diagnostics import diagnose
from .permission import ALLOW, ASK, DEFAULT_SHELL, Rule, evaluate, worker_rules

MAX_LINES = 2000          # 읽기·출력 기본 줄 수(OpenCode Truncate.MAX_LINES)
MAX_BYTES = 50 * 1024     # 출력 상한(OpenCode Truncate.MAX_BYTES)
MAX_LINE_LENGTH = 2000
SEARCH_LIMIT = 100
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv", ".alex-tool-output"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".7z", ".exe", ".dll", ".so",
                   ".pyc", ".bin", ".onnx", ".gguf", ".safetensors", ".mp4", ".mp3", ".wav", ".db", ".sqlite"}
SKILL_DIRS = (".alex/skills", ".opencode/skill", ".opencode/skills", ".claude/skills")


class ToolError(Exception):
    pass


@dataclass
class ShellPolicy:
    """명령 실행 설정. 허락 목록은 권한 규칙(bash)으로 바뀐다. 'python' 은 ALEX 의 파이썬으로 바꾼다."""
    allow: tuple[str, ...] = DEFAULT_SHELL
    timeout: float = 120.0
    max_timeout: float = 600.0


@dataclass
class Skill:
    name: str
    description: str
    path: Path


def find_skills(*folders: Path) -> dict[str, Skill]:
    """SKILL.md(머리에 name·description)를 찾는다 — OpenCode·Claude 와 같은 모양."""
    skills: dict[str, Skill] = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for file in sorted(folder.glob("*/SKILL.md")):
            text = file.read_text(encoding="utf-8", errors="replace")
            head = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
            meta = dict(re.findall(r"^(\w+):\s*(.+?)\s*$", head.group(1), re.M)) if head else {}
            name = meta.get("name", file.parent.name).strip().strip("\"'")
            skills.setdefault(name, Skill(name, meta.get("description", "").strip().strip("\"'"), file))
    return skills


@dataclass
class Workspace:
    root: Path
    shell: ShellPolicy = field(default_factory=ShellPolicy)
    output_dir: Path | None = None              # 잘린 도구 출력 전체를 두는 곳(읽기 허용)
    cancel_event: threading.Event | None = None
    rules: list[Rule] | None = None             # 없으면 worker_rules(shell.allow)
    ask: Callable[[str, str, dict], bool] | None = None   # 'ask' 규칙일 때 사람에게(없으면 거절)
    fetch: Callable[[str, str], str] | None = None         # webfetch(주소, 형식) — ALEX 웹 읽기
    search: Callable[[str], str] | None = None             # websearch(질문) — ALEX 웹 검색
    mcp: Any = None                                        # MCP 연결(mcp.MCPHub) — 도구 목록·호출
    skills: dict[str, Skill] = field(default_factory=dict)
    read_times: dict[str, float] = field(default_factory=dict)
    todos: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    history: list[tuple[int, dict[str, bytes | None]]] = field(default_factory=list)  # (차례, 바꾸기 전 내용)
    _outputs: int = 0
    _step: int = 1   # 반복 밖에서 바로 쓸 때도 차례 1 — revert(0) 이 처음으로 돌린다
    read_max_lines: int = MAX_LINES  # 일꾼은 짧게 읽고 offset으로 이어 읽어 문맥 낭비를 줄인다.
    edits: int = 0                   # 파일을 바꾼 횟수(쓰기·고치기·패치·되돌리기) — 시험 실행이 마지막 수정 뒤인지 가린다
    test_runs: list[dict] = field(default_factory=list)   # bash 로 실제 실행한 명령·종료 코드·그때의 edits(일꾼 독립 검사가 씀)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if self.output_dir is None:
            self.output_dir = self.root.parent / f".{self.root.name}-alex-tool-output"
        if self.rules is None:
            self.rules = worker_rules(self.shell.allow)
        if not self.skills:
            self.skills = find_skills(*(self.root / d for d in SKILL_DIRS))

    def child(self, extra_rules: list[Rule]) -> "Workspace":
        """하위 에이전트용 — 같은 폴더·명령 설정·되돌리기 기록, 읽은 기록은 새로, 규칙은 덧붙인 것."""
        kid = Workspace(self.root, shell=self.shell, output_dir=self.output_dir, cancel_event=self.cancel_event,
                        rules=[*(self.rules or []), *extra_rules], ask=self.ask, fetch=self.fetch, search=self.search,
                        mcp=self.mcp, skills=self.skills)
        kid.history, kid.counts = self.history, self.counts
        return kid

    # ── 경로
    def resolve(self, raw: Any, *, allow_output: bool = False) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError("filePath is required")
        candidate = Path(raw.strip())
        path = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        inside = path == self.root or self.root in path.parents
        if allow_output and self.output_dir is not None:
            out = Path(self.output_dir).resolve()
            inside = inside or path == out or out in path.parents
        if not inside:
            raise ToolError(f"Path is outside the workspace ({self.root}): {raw}")
        return path

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    # ── 권한
    def permit(self, permission: str, pattern: str, detail: dict | None = None) -> str:
        """'' 이면 허락, 아니면 모델에게 돌려줄 거절 글."""
        action = evaluate(permission, pattern, self.rules or [])
        if action == ALLOW:
            return ""
        if action == ASK and self.ask is not None and self.ask(permission, pattern, detail or {}):
            return ""
        how = "denied by the user" if action == ASK and self.ask is not None else "not allowed in this workspace"
        if permission == "bash":
            allowed = [r.pattern for r in (self.rules or []) if r.permission == "bash" and r.action == ALLOW and r.pattern != "*"]
            return (f"Error: this command is {how}." + (" Allowed commands: " + ", ".join(f"`{p}`" for p in allowed)
                                                         if allowed else ""))
        return f"Error: permission '{permission}' for '{pattern}' is {how}. Do not retry it; choose another way."

    # ── 되돌리기(OpenCode snapshot 과 같은 뜻 — 차례마다 바꾸기 전 내용)
    def begin_step(self, step: int) -> None:
        self._step = step

    def _remember(self, path: Path) -> None:
        self.edits += 1
        if not self.history or self.history[-1][0] != self._step:
            self.history.append((self._step, {}))
        saved = self.history[-1][1]
        key = str(path)
        if key not in saved:  # 이 차례에서 처음 바꿀 때의 내용(차례마다 — 차례 단위로 되돌린다)
            saved[key] = path.read_bytes() if path.is_file() else None

    def revert(self, to_step: int = 0) -> list[str]:
        """to_step 차례 뒤의 변경을 되돌린다(0 이면 처음으로). 되돌린 파일 목록."""
        restored = []
        while self.history and self.history[-1][0] > to_step:
            _, record = self.history.pop()
            for key, before in record.items():
                path = Path(key)
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(before)
                restored.append(self.relative(path))
                self.read_times.pop(key, None)
        self.edits += 1 if restored else 0
        return restored

    def diff(self) -> str:
        """처음과 지금의 차이(통합 diff)."""
        first: dict[str, bytes | None] = {}
        for _, record in self.history:
            for key, before in record.items():
                first.setdefault(key, before)
        out = []
        for key, before in sorted(first.items()):
            path = Path(key)
            old = (before or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            new = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if path.is_file() else []
            name = self.relative(path)
            out.extend(difflib.unified_diff(old, new, f"a/{name}" if before is not None else "/dev/null",
                                            f"b/{name}" if path.is_file() else "/dev/null"))
        return "".join(out)

    # ── 출력 자르기(OpenCode Truncate.output)
    def truncate(self, text: str, *, direction: str = "head") -> str:
        lines = text.split("\n")
        if len(lines) <= MAX_LINES and len(text.encode("utf-8")) <= MAX_BYTES:
            return text
        self._outputs += 1
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        # 독립 검사가 나란히 돌 때도 이름이 겹치지 않게 끝에 무작위 4자리
        saved = Path(self.output_dir) / f"tool_{int(time.time())}_{self._outputs}_{os.urandom(2).hex()}.txt"
        saved.write_text(text, encoding="utf-8")
        kept, size = [], 0
        source = lines if direction == "head" else list(reversed(lines))
        for line in source:
            size += len(line.encode("utf-8")) + 1
            if len(kept) >= MAX_LINES or size > MAX_BYTES:
                break
            kept.append(line)
        if direction != "head":
            kept.reverse()
        preview = "\n".join(kept)
        hint = (f"\n\n(Output truncated: showing the {'first' if direction == 'head' else 'last'} {len(kept)} of "
                f"{len(lines)} lines. The full output was saved to {saved}. Use the read tool with offset, or grep, "
                "to inspect the rest.)")
        return (preview + hint) if direction == "head" else (hint.strip() + "\n\n" + preview)

    # ── 읽기·쓰기·고치기
    def read(self, args: dict) -> str:
        path = self.resolve(args.get("filePath"), allow_output=True)
        if not path.exists():
            near = sorted(p.name for p in path.parent.glob("*") if p.name.lower().startswith(path.stem[:3].lower()))[:5] \
                if path.parent.is_dir() else []
            return f"Error: File not found: {path}" + (f"\nDid you mean one of these?\n" + "\n".join(near) if near else "")
        offset = max(1, int(args.get("offset") or 1))
        limit = min(max(1, self.read_max_lines), max(1, int(args.get("limit") or self.read_max_lines)))
        if path.is_dir():
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            names = [p.name + ("/" if p.is_dir() else "") for p in items if p.name not in SKIP_DIRS]
            window = names[offset - 1:offset - 1 + limit]
            more = offset - 1 + len(window) < len(names)
            return "\n".join([f"<path>{path}</path>", "<type>directory</type>", "<entries>", *window,
                              f"(Showing {len(window)} of {len(names)} entries. Use offset={offset + len(window)} to continue.)"
                              if more else f"({len(names)} entries)", "</entries>"])
        data = path.read_bytes()
        if path.suffix.lower() in BINARY_SUFFIXES or b"\x00" in data[:8192]:
            return f"Error: Cannot read binary file: {path}"
        text = data.decode("utf-8", errors="replace").lstrip("﻿")
        lines = text.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self.read_times[str(path)] = path.stat().st_mtime
        chosen, size, capped = [], 0, False
        for number in range(offset, min(len(lines), offset - 1 + limit) + 1):
            line = lines[number - 1]
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + f"... (line truncated to {MAX_LINE_LENGTH} chars)"
            row = f"{number}: {line}"
            size += len(row.encode("utf-8")) + 1
            if size > MAX_BYTES:
                capped = True
                break
            chosen.append(row)
        last = offset + len(chosen) - 1
        output = "\n".join([f"<path>{path}</path>", "<type>file</type>", "<content>"]) + "\n" + "\n".join(chosen)
        if capped:
            output += f"\n\n(Output capped at 50 KB. Showing lines {offset}-{last}. Use offset={last + 1} to continue.)"
        elif last < len(lines):
            output += f"\n\n(Showing lines {offset}-{last} of {len(lines)}. Use offset={last + 1} to continue.)"
        else:
            output += f"\n\n(End of file - total {len(lines)} lines)"
        return output + "\n</content>"

    def _must_have_read(self, path: Path) -> str:
        seen = self.read_times.get(str(path))
        if seen is None:
            return f"Error: You must read file {path} before overwriting it. Use the Read tool first"
        if path.stat().st_mtime > seen + 1e-6:
            return (f"Error: File {path} has been modified since it was last read. Please read the file again "
                    "before modifying it.")
        return ""

    def _write(self, path: Path, text: str) -> None:
        self._remember(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        self.read_times[str(path)] = path.stat().st_mtime

    def _preview(self, path: Path, before: str, after: str) -> str:
        """허락을 물을 때 보여 줄 바뀔 내용(통합 diff, OpenCode 의 permission metadata.diff 와 같은 뜻)."""
        name = self.relative(path)
        text = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                            f"a/{name}" if before else "/dev/null", f"b/{name}"))
        return text[:8000] + ("\n… (생략)" if len(text) > 8000 else "")

    def apply_external(self, rel: str, text: str | None, tool: str = "opencode") -> str:
        """다른 일꾼(OpenCode 무료 모델 세션)이 복사본에서 만든 변경을 작업 폴더에 들인다 — 권한(diff 미리보기)·되돌리기 기록은
        write 와 같은 길. text 가 None 이면 지우기. '' 이면 들임, 아니면 거절 글."""
        path = self.resolve(rel)
        before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if problem := self.permit("edit", self.relative(path), {"tool": tool, "filePath": str(path),
                                                                 "diff": self._preview(path, before, text or "")}):
            return problem
        self.counts["edit"] = self.counts.get("edit", 0) + 1
        if text is None:
            self._remember(path)
            path.unlink(missing_ok=True)
        else:
            self._write(path, text)
        return ""

    def write(self, args: dict) -> str:
        path = self.resolve(args.get("filePath"))
        content = args.get("content")
        if not isinstance(content, str):
            return "Error: content is required"
        if path.is_dir():
            return f"Error: Path is a directory, not a file: {path}"
        if path.exists() and (problem := self._must_have_read(path)):
            return problem
        before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if problem := self.permit("edit", self.relative(path), {"tool": "write", "filePath": str(path),
                                                                 "diff": self._preview(path, before, content)}):
            return problem
        self._write(path, content)
        return "Wrote file successfully." + diagnose(path)

    def edit(self, args: dict) -> str:
        path = self.resolve(args.get("filePath"))
        old, new = args.get("oldString"), args.get("newString")
        if not isinstance(old, str) or not isinstance(new, str):
            return "Error: oldString and newString are required"
        if old == new:
            return "Error: No changes to apply: oldString and newString are identical."
        if path.is_dir():
            return f"Error: Path is a directory, not a file: {path}"
        if not path.exists():
            if old == "":  # 빈 oldString 으로 새 파일 만들기(OpenCode 와 같음)
                if problem := self.permit("edit", self.relative(path), {"tool": "edit", "filePath": str(path),
                                                                         "diff": self._preview(path, "", new)}):
                    return problem
                self._write(path, new)
                return "Edit applied successfully." + diagnose(path)
            return f"Error: File {path} not found"
        if problem := self._must_have_read(path):
            return problem
        raw = path.read_bytes().decode("utf-8", errors="replace")
        bom = raw.startswith("﻿")
        source = raw[1:] if bom else raw
        ending = editing.line_ending(source)
        try:
            updated = editing.replace(editing.normalize(source), editing.normalize(old), editing.normalize(new),
                                      bool(args.get("replaceAll")))
        except editing.EditError as exc:
            return f"Error: {exc}"
        if problem := self.permit("edit", self.relative(path), {"tool": "edit", "filePath": str(path),
                                                                 "diff": self._preview(path, editing.normalize(source), updated)}):
            return problem
        self._write(path, ("﻿" if bom else "") + editing.restore(updated, ending))
        return "Edit applied successfully." + diagnose(path)

    def apply_patch(self, args: dict) -> str:
        text = args.get("patchText")
        if not isinstance(text, str) or not text.strip():
            return "Error: patchText is required"
        try:
            hunks = patching.parse(text)
        except patching.PatchError as exc:
            return f"Error: {exc}"
        if not hunks:
            return "Error: patch rejected: empty patch"
        planned: list[tuple[str, Path, str | None, Path | None]] = []  # (표시, 경로, 새 내용 또는 None=지움, 옮길 곳)
        for hunk in hunks:
            path = self.resolve(hunk.path)
            if hunk.kind == "add":
                planned.append(("A", path, hunk.contents + ("\n" if hunk.contents and not hunk.contents.endswith("\n") else ""), None))
            elif hunk.kind == "delete":
                if not path.is_file():
                    return f"Error: patch rejected: file to delete not found: {path}"
                planned.append(("D", path, None, None))
            else:
                if not path.is_file():
                    return f"Error: patch rejected: file to update not found: {path}"
                raw = path.read_text(encoding="utf-8", errors="replace")
                bom = raw.startswith("﻿")
                source = raw[1:] if bom else raw
                try:
                    updated = patching.apply_chunks(hunk.path, hunk.chunks, editing.normalize(source))
                except patching.PatchError as exc:
                    return f"Error: patch rejected: {exc}"
                target = self.resolve(hunk.move_path) if hunk.move_path else None
                planned.append(("M", path, ("﻿" if bom else "") + editing.restore(updated, editing.line_ending(source)),
                                target))
        for mark, path, _content, target in planned:
            for where in (path, target):
                detail = {"tool": "apply_patch", "diff": str(text)[:8000]}
                if where is not None and (problem := self.permit("edit", self.relative(where), detail)):
                    return problem
        lines, notes = ["Success. Updated the following files:"], ""
        for mark, path, content, target in planned:
            if content is None:
                self._remember(path)
                path.unlink()
                lines.append(f"D {self.relative(path)}")
                continue
            final = target or path
            if target is not None:
                self._remember(path)
                path.unlink()
            self._write(final, content)
            lines.append(f"{'A' if mark == 'A' else 'M'} {self.relative(final)}")
            notes += diagnose(final)
        return "\n".join(lines) + notes

    # ── 찾기
    def _files(self, base: Path):
        for directory, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                yield Path(directory) / name

    def glob(self, args: dict) -> str:
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            return "Error: pattern is required"
        base = self.resolve(args.get("path") or str(self.root))
        found = [p for p in self._files(base)
                 if fnmatch.fnmatch(p.relative_to(base).as_posix(), pattern)
                 or ("/" not in pattern and fnmatch.fnmatch(p.name, pattern))
                 or (pattern.startswith("**/") and fnmatch.fnmatch(p.relative_to(base).as_posix(), pattern[3:]))]
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not found:
            return "No files found"
        lines = [str(p) for p in found[:SEARCH_LIMIT]]
        if len(found) > SEARCH_LIMIT:
            lines += ["", f"(Results are truncated: showing first {SEARCH_LIMIT} results. Consider using a more specific "
                          "path or pattern.)"]
        return "\n".join(lines)

    def grep(self, args: dict) -> str:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return "Error: pattern is required"
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"
        base = self.resolve(args.get("path") or str(self.root))
        include = str(args.get("include") or "").strip()
        includes = [include] if include else []
        if "{" in include and include.endswith("}"):  # "*.{ts,tsx}" → *.ts, *.tsx
            stem = include.split("{", 1)[0]
            includes = [stem + ext.strip() for ext in include.split("{", 1)[1].rstrip("}").split(",")]
        files = [base] if base.is_file() else list(self._files(base))
        matches: dict[Path, list[tuple[int, str]]] = {}
        total = 0
        for path in files:
            if includes and not any(fnmatch.fnmatch(path.name, inc) for inc in includes):
                continue
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.setdefault(path, []).append((number, line[:MAX_LINE_LENGTH]))
                    total += 1
            if total >= SEARCH_LIMIT * 5:
                break
        if not matches:
            return "No files found"
        out, shown = [], 0
        for path in sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True):
            if shown >= SEARCH_LIMIT:
                break
            out.append(f"{path}:")
            for number, line in matches[path]:
                if shown >= SEARCH_LIMIT:
                    break
                out.append(f"  Line {number}: {line}")
                shown += 1
            out.append("")
        more = total > shown
        head = f"Found {total} matches" + (" (more matches available)" if more else "")
        tail = ["(Results truncated. Consider using a more specific path or pattern.)"] if more else []
        return "\n".join([head, *out, *tail]).rstrip()

    # ── 명령
    def bash(self, args: dict) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            return "Error: command is required"
        if re.search(r"[|&;<>`$]|\r|\n", command):
            return ("Error: shell operators (pipes, redirection, chaining, substitution) are not allowed. "
                    "Run one command at a time.")
        try:
            words = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            return f"Error: could not parse command: {exc}"
        words = [w.strip('"') for w in words]
        if words and words[0] in ("python", "python3", "py"):
            words[0] = "python"
        if problem := self.permit("bash", " ".join(words), {"command": command, "description": args.get("description", "")}):
            return problem
        timeout = min(float(args.get("timeout") or self.shell.timeout * 1000) / 1000, self.shell.max_timeout)
        if timeout <= 0:
            timeout = self.shell.timeout
        run = [sys.executable if words[0] == "python" else words[0], *words[1:]]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        # ALEX 의 시험 실행(developer.py)과 같은 UTF-8 모드 — 아니면 기본 인코딩으로 읽는 시험이 한글에서 가짜로 깨진다(2026-10-23)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            process = subprocess.Popen(run, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, env=env, creationflags=flags)
        except OSError as exc:
            return f"Error: could not run command: {exc}"
        chunks: list[bytes] = []
        reader = threading.Thread(target=lambda: chunks.append(process.stdout.read() if process.stdout else b""), daemon=True)
        reader.start()
        started, timed_out = time.monotonic(), False
        try:
            while process.poll() is None:
                if self._cancelled():
                    process.kill()
                    return "Error: cancelled by the user"
                if time.monotonic() - started > timeout:
                    process.kill()
                    timed_out = True
                    break
                time.sleep(0.1)
            reader.join(10)
            process.wait(5)
        finally:
            if process.stdout is not None:
                process.stdout.close()
        output = b"".join(chunks).decode("utf-8", errors="replace").replace("\r\n", "\n").rstrip()
        self.test_runs.append({"words": words, "exit": None if timed_out else process.returncode, "edits": self.edits,
                               "tail": output[-2000:]})
        output += (f"\n\n(Command timed out after {timeout:.0f} seconds and was terminated.)" if timed_out
                   else f"\n\n(exit code {process.returncode})")
        return self.truncate(output.strip(), direction="tail")

    # ── 할 일·스킬·웹
    def todowrite(self, args: dict) -> str:
        todos = args.get("todos")
        if not isinstance(todos, list):
            return "Error: todos must be a list"
        clean = [{"content": str(item["content"]).strip()[:300], "status": str(item.get("status") or "pending"),
                  "priority": str(item.get("priority") or "medium")}
                 for item in todos if isinstance(item, dict) and str(item.get("content", "")).strip()]
        self.todos = clean
        remaining = sum(1 for t in clean if t["status"] not in ("completed", "cancelled"))
        return f"{remaining} todos\n" + json.dumps(clean, ensure_ascii=False, indent=2)

    def skill(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        found = self.skills.get(name)
        if found is None:
            return f"Error: unknown skill '{name}'. Available skills: " + (", ".join(sorted(self.skills)) or "none")
        if problem := self.permit("skill", name):
            return problem
        body = found.path.read_text(encoding="utf-8", errors="replace")
        return (f"<skill_content name=\"{name}\">\n# Skill: {name}\n\n{body.strip()}\n\nBase directory for this skill: "
                f"{found.path.parent}\nRelative paths in this skill are relative to this base directory.\n</skill_content>")

    def webfetch(self, args: dict) -> str:
        url = str(args.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            return "Error: URL must start with http:// or https://"
        if self.fetch is None:
            return "Error: web fetching is not available in this workspace"
        if problem := self.permit("webfetch", url):
            return problem
        return self.truncate(self.fetch(url, str(args.get("format") or "markdown")))

    def websearch(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "Error: query is required"
        if self.search is None:
            return "Error: web search is not available in this workspace"
        if problem := self.permit("websearch", query):
            return problem
        return self.truncate(self.search(query))

    def run(self, name: str, args: dict) -> str:
        """도구 하나 실행 — 실패는 'Error: …' 글로."""
        handler: Callable[[dict], str] | None = {
            "read": self.read, "write": self.write, "edit": self.edit, "apply_patch": self.apply_patch, "glob": self.glob,
            "grep": self.grep, "bash": self.bash, "todowrite": self.todowrite, "skill": self.skill,
            "webfetch": self.webfetch, "websearch": self.websearch}.get(name)
        self.counts[name] = self.counts.get(name, 0) + 1
        args = args if isinstance(args, dict) else {}
        try:
            if handler is None and self.mcp is not None and name.startswith("mcp__"):
                if problem := self.permit(name, "*", args):
                    return problem
                return self.truncate(self.mcp.call(name, args))
            if handler is None:
                return f"Error: unknown tool '{name}'."
            if name in ("read", "glob", "grep"):
                target = args.get("filePath") or args.get("path") or str(self.root)
                if problem := self.permit(name, str(target)):
                    return problem
            result = handler(args)
        except ToolError as exc:
            return f"Error: {exc}"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
        return self.truncate(result) if name in ("read", "glob", "grep") and not result.startswith("<path>") else result
