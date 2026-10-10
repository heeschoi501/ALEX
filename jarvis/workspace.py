"""Safe, approval-based workspace editing for Project ALEX.

The model can *propose* a replacement for an existing text/code file, but it
cannot write by itself. Applying a proposal is a separate explicit action.
Every applied edit is atomic and the previous file is backed up first.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

from .tools import READABLE_EXTENSIONS, ToolError
from .storage import Storage, StorageError


@dataclass(slots=True)
class EditProposal:
    proposal_id: str
    relative_path: str
    original_sha256: str
    original_text: str
    proposed_text: str
    diff: str


# ── 변경 묶음 (Coding Mode 4단계 2번, 2026-09-26 claude) ──────────────────────────
# 여러 파일의 부분 수정·새 파일을 한 수정안으로 묶고, 모두 적용하거나 하나도 적용하지 않는다.
# 모델은 줄 번호 diff 대신 "찾을 부분 → 바꿀 내용" 블록을 쓴다. 찾을 부분이 파일에 정확히 한 번 있어야 하므로
# 틀린 수정안은 적용 전에 거부된다. 모델 출력은 실행하지 않고 텍스트로만 해석한다.
MAX_BUNDLE_FILES = 8
# 사용자만 고치는 파일(프로젝트 루트) — 모델의 수정안이 만들거나 바꾸면 거부한다. alex-checks.json 은 /dev-check 가 실행할
# 명령 목록이라, 모델이 명령을 추가할 수 있으면 '모델 출력을 실행하지 않는다' 원칙이 깨진다(2026-09-30 claude).
PROTECTED_FILES = frozenset({"alex-checks.json"})
MAX_NEW_DIR_DEPTH = 3   # 새 파일을 위해 한 번에 새로 만들 수 있는 폴더 단계
BUNDLE_MANIFEST = ".alex-bundle.json"
_SEARCH, _DIVIDER, _REPLACE = "<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"
_CONTENT, _END = "<<<<<<< CONTENT", ">>>>>>> END"
# 모델은 표시를 조금씩 틀린다(gpt-oss:120b 실측: <<<<<< 여섯 개, 파일 이름 다음 --- 줄). 꺾쇠·등호 5~9개를 같은 표시로 본다.
_MARKER = re.compile(r"(<{5,9}|={5,9}|>{5,9})[ \t]*(SEARCH|REPLACE|CONTENT|END)?")


def _marker(line: str) -> str | None:
    match = _MARKER.fullmatch(line.strip())
    if not match:
        return None
    sign, word = match.group(1)[0], match.group(2)
    return {("<", "SEARCH"): _SEARCH, ("=", None): _DIVIDER, (">", "REPLACE"): _REPLACE,
            ("<", "CONTENT"): _CONTENT, (">", "END"): _END}.get((sign, word))


@dataclass(slots=True)
class FileChange:
    relative_path: str
    action: str            # "modify" | "create"
    original_sha256: str   # create 는 ""
    original_text: str     # create 는 ""
    proposed_text: str


@dataclass(slots=True)
class ChangeBundle:
    proposal_id: str
    changes: list[FileChange]
    diff: str


def parse_change_blocks(text: str) -> list[dict]:
    """모델 출력의 블록을 변경 명세로 바꾼다. 경로·내용 검사는 편집기가 한다.

        FILE: 경로                 NEW FILE: 경로
        <<<<<<< SEARCH             <<<<<<< CONTENT
        (지금 있는 줄들)            (새 파일 전체)
        =======                    >>>>>>> END
        (바꿀 줄들)
        >>>>>>> REPLACE
    """
    lines = text.replace("\r\n", "\n").split("\n")
    specs: dict[str, dict] = {}
    current: str | None = None

    def take_until(start: int, *ends: str) -> tuple[list[str], int, str]:
        body: list[str] = []
        for index in range(start, len(lines)):
            if _marker(lines[index]) in ends:
                return body, index, lines[index].strip()
            body.append(lines[index])
        raise ToolError(f"변경 블록이 닫히지 않았습니다({' 또는 '.join(ends)} 없음). 파일은 변경하지 않았습니다.")

    def path_of(line: str, prefix: str) -> str:
        path = line[len(prefix):].strip().strip("`'\"").strip()
        # '/calc.py'·'./calc.py' 는 프로젝트 기준으로 본다. 프로젝트 밖은 편집기가 어차피 막는다.
        if path.startswith("./"):
            path = path[2:]
        elif path.startswith("/") and not path.startswith("//"):
            path = path[1:]
        if not path:
            raise ToolError(f"'{prefix}' 뒤에 파일 경로가 없습니다.")
        return path

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("NEW FILE:"):
            path = path_of(line, "NEW FILE:")
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index].strip().startswith("```")
                                          or lines[index].strip() == "---"):
                index += 1
            if index >= len(lines) or _marker(lines[index]) != _CONTENT:
                raise ToolError(f"NEW FILE: {path} 다음에 {_CONTENT} 가 없습니다.")
            body, index, _ = take_until(index + 1, _END)
            if path in specs:
                raise ToolError(f"같은 파일이 두 번 나옵니다: {path}")
            specs[path] = {"path": path, "create": "\n".join(body) + "\n"}
            current = None
        elif line.startswith("FILE:"):
            current = path_of(line, "FILE:")
            if "create" in specs.get(current, {}):
                raise ToolError(f"같은 파일이 두 번 나옵니다: {current}")
            specs.setdefault(current, {"path": current, "edits": []})
        elif _marker(line) == _SEARCH:
            if current is None:
                raise ToolError(f"{_SEARCH} 앞에 FILE: 경로가 없습니다.")
            search, index, _ = take_until(index + 1, _DIVIDER)
            replace, index, _ = take_until(index + 1, _REPLACE)
            specs[current]["edits"].append(("\n".join(search), "\n".join(replace)))
        index += 1
    specs = {path: spec for path, spec in specs.items() if spec.get("edits") or "create" in spec}
    if not specs:
        raise ToolError("모델 출력에서 변경 블록(FILE:/NEW FILE:)을 찾지 못했습니다. 파일은 변경하지 않았습니다.")
    return list(specs.values())


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _loose_replace(text: str, search: str, replace: str) -> tuple[str | None, int]:
    """찾을 부분이 글자 그대로는 없을 때 너그럽게 한 번 더 찾는다(2026-10-04 claude, ADR-079). (바뀐 글, 맞은 곳 수).

    27b 수정안의 거부 30회 중 9회가 '찾을 부분 없음'이었고, 기록에 남는 첫 줄은 대부분 파일에 그대로 있었다 — 뒤 줄의 공백·들여쓰기·빈 줄이
    어긋난 것이다(Aider 의 너그러운 찾기와 같은 생각). 두 단계: ①줄마다 앞뒤 공백을 빼고 같고, 들여쓰기 차이가 모든 줄에서 같음
    ②거기에 빈 줄 차이까지 무시. **정확히 한 곳과 맞을 때만** 바꾸고, 바꿀 내용은 그 들여쓰기 차이만큼 옮긴다. 줄은 LF 로 다룬다.
    """
    lines = text.split("\n")
    wanted = search.strip("\n").split("\n")
    if not any(line.strip() for line in wanted):
        return None, 0
    solid = [line for line in wanted if line.strip()]

    def shift(window: list[str], pattern: list[str]) -> int | None:
        deltas = {_indent(a) - _indent(b) for a, b in zip(window, pattern) if a.strip()}
        return deltas.pop() if len(deltas) == 1 else None

    matches: list[tuple[int, int, int]] = []  # (시작 줄, 끝 줄(제외), 들여쓰기 차이)
    for start in range(len(lines) - len(wanted) + 1):  # ① 같은 줄 수
        window = lines[start:start + len(wanted)]
        if all(a.strip() == b.strip() for a, b in zip(window, wanted)):
            delta = shift(window, wanted)
            if delta is not None:
                matches.append((start, start + len(wanted), delta))
    if not matches:  # ② 빈 줄은 건너뛰고 비교
        for start, line in enumerate(lines):
            if line.strip() != solid[0].strip():
                continue
            picked, cursor = [], start
            for want in solid:
                while cursor < len(lines) and not lines[cursor].strip():
                    cursor += 1
                if cursor >= len(lines) or lines[cursor].strip() != want.strip():
                    break
                picked.append(lines[cursor])
                cursor += 1
            else:
                delta = shift(picked, solid)
                if delta is not None:
                    matches.append((start, cursor, delta))
    if len(matches) != 1:
        return None, len(matches)
    start, end, delta = matches[0]
    moved = []
    for line in replace.strip("\n").split("\n") if replace.strip("\n") else []:
        if not line.strip() or delta == 0:
            moved.append(line)
        elif delta > 0:
            moved.append(" " * delta + line)
        else:
            moved.append(line[min(-delta, _indent(line)):])
    return "\n".join(lines[:start] + moved + lines[end:]), 1


class SafeWorkspaceEditor:
    """Create/apply reversible edits inside one approved project root.

    Safety properties:
    - only existing UTF-8 files with allowed extensions
    - root traversal/symlink escape blocked
    - proposal and apply are separate operations
    - stale proposals are rejected if the file changed meanwhile
    - original is backed up before an atomic os.replace()
    - bounded file and proposal sizes
    """

    def __init__(self, allowed_root: Path, backup_root: Path, max_bytes: int = 200_000,
                 *, storage: Storage | None = None) -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.backup_base_root = Path(backup_root).resolve()
        namespace = hashlib.sha256(os.path.normcase(str(self.allowed_root)).encode("utf-8")).hexdigest()[:20]
        self.backup_root = self.backup_base_root / namespace
        self.max_bytes = max(1, int(max_bytes))
        self.storage = storage
        self.workspace_key = os.path.normcase(str(self.allowed_root))
        self._pending: dict[str, EditProposal] = {}
        self._pending_bundles: dict[str, ChangeBundle] = {}
        self._last_backup_stamp: datetime | None = None

    def _next_backup_stamp(self) -> str:
        """직전 백업보다 엄격하게 새로운 폴더 이름을 돌려준다.

        Windows 시계 해상도는 약 15ms 라서 ``apply`` 직후의 ``restore_latest`` 가 같은
        ``%Y%m%dT%H%M%S%fZ`` 를 받는다. 그러면 undo 백업이 직전 백업을 덮어써서 되돌리기
        이력이 조용히 사라진다. 동률일 때만 1마이크로초 밀어 항상 새 폴더를 만든다.
        """
        now = datetime.now(timezone.utc)
        if self._last_backup_stamp is not None and now <= self._last_backup_stamp:
            now = self._last_backup_stamp + timedelta(microseconds=1)
        self._last_backup_stamp = now
        return now.strftime("%Y%m%dT%H%M%S%fZ")

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _resolve(self, relative_path: str) -> Path:
        raw = relative_path.strip()
        if not raw:
            raise ToolError("파일 경로가 필요합니다.")
        requested = Path(raw)
        if requested.is_absolute():
            raise ToolError("절대 경로는 수정할 수 없습니다.")
        try:
            target = (self.allowed_root / requested).resolve()
        except OSError as exc:
            raise ToolError(f"경로를 해석하지 못했습니다: {exc}") from exc
        if target != self.allowed_root and self.allowed_root not in target.parents:
            raise ToolError("승인된 프로젝트 루트 밖의 파일은 수정할 수 없습니다.")
        if target.parent == self.allowed_root and target.name.lower() in PROTECTED_FILES:
            raise ToolError(f"{target.name} 은(는) 사용자만 고치는 파일입니다(검사 명령 목록).")
        if not target.exists() or not target.is_file():
            raise ToolError("v0.1.4 편집기는 기존 파일만 수정합니다. 새 파일 생성은 지원하지 않습니다.")
        if target.suffix.lower() not in READABLE_EXTENSIONS:
            raise ToolError(f"이 확장자는 수정 허용 목록에 없습니다: {target.suffix or '(없음)'}")
        try:
            if target.stat().st_size > self.max_bytes:
                raise ToolError(f"파일이 너무 큽니다. 최대 {self.max_bytes} bytes까지 수정할 수 있습니다.")
        except OSError as exc:
            raise ToolError(f"파일 크기를 확인하지 못했습니다: {exc}") from exc
        return target

    def read(self, relative_path: str) -> str:
        target = self._resolve(relative_path)
        try:
            with target.open("r", encoding="utf-8", newline="") as handle:
                return handle.read()
        except UnicodeDecodeError as exc:
            raise ToolError("UTF-8 텍스트 파일만 수정할 수 있습니다.") from exc
        except OSError as exc:
            raise ToolError(f"파일을 읽지 못했습니다: {exc}") from exc

    @staticmethod
    def normalize_model_replacement(text: str) -> str:
        """Accept plain replacement text or one markdown code fence.

        The edit model is instructed to return the full file. We deliberately
        do not execute patches/shell commands from its output.
        """
        raw = text.strip()
        fenced = re.fullmatch(r"```[^\n]*\n(.*)\n```", raw, flags=re.DOTALL)
        if fenced:
            raw = fenced.group(1)
        # Keep a final newline for normal source/text files.
        if raw and not raw.endswith("\n"):
            raw += "\n"
        return raw

    def create_proposal(self, relative_path: str, proposed_text: str, *, expected_original: str | None = None) -> EditProposal:
        target = self._resolve(relative_path)
        original = self.read(relative_path)
        if expected_original is not None and original != expected_original:
            raise ToolError("모델이 수정안을 만드는 동안 파일이 변경되었습니다. 새 원본으로 다시 요청하세요.")
        proposed = self.normalize_model_replacement(proposed_text)
        if "\r\n" in original:
            proposed = proposed.replace("\r\n", "\n").replace("\n", "\r\n")
        if not proposed.strip():
            raise ToolError("모델이 빈 파일을 제안했습니다. 안전을 위해 거부했습니다.")
        if len(proposed.encode("utf-8")) > self.max_bytes:
            raise ToolError(f"제안된 파일이 너무 큽니다. 최대 {self.max_bytes} bytes입니다.")
        if proposed == original:
            raise ToolError("제안 내용이 현재 파일과 동일합니다.")
        if target.suffix.lower() == ".py":
            try:
                ast.parse(proposed, filename=str(target))
            except SyntaxError as exc:
                location = f"line {exc.lineno}" if exc.lineno else "unknown line"
                raise ToolError(
                    f"모델이 제안한 Python 코드에 문법 오류가 있어 proposal을 거부했습니다: "
                    f"{location}: {exc.msg}"
                ) from exc
        relative = str(target.relative_to(self.allowed_root))
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        proposal = EditProposal(
            proposal_id=secrets.token_hex(6),
            relative_path=relative,
            original_sha256=self._digest(original),
            original_text=original,
            proposed_text=proposed,
            diff=diff,
        )
        if self.storage is not None:
            try:
                self.storage.save_edit_proposal(self.workspace_key, proposal.proposal_id,
                                                json.dumps(asdict(proposal), ensure_ascii=False))
            except StorageError as exc:
                raise ToolError(f"수정안을 저장하지 못했습니다. 파일 변경 없음: {exc}") from exc
        self._pending[proposal.proposal_id] = proposal
        # Prevent an unbounded in-memory queue.
        while len(self._pending) > 20:
            self._pending.pop(next(iter(self._pending)))
        return proposal

    def get(self, proposal_id: str) -> EditProposal | None:
        proposal_id = proposal_id.strip()
        if self.storage is None:
            return self._pending.get(proposal_id)
        try:
            raw = self.storage.get_edit_proposal(self.workspace_key, proposal_id)
            if raw is None:
                return None
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("kind") == "bundle":
                return None  # 변경 묶음은 get_bundle 로 읽는다
            proposal = EditProposal(**data)
            if (any(not isinstance(value, str) for value in asdict(proposal).values())
                    or proposal.proposal_id != proposal_id
                    or len(proposal.original_text.encode("utf-8")) > self.max_bytes
                    or len(proposal.proposed_text.encode("utf-8")) > self.max_bytes
                    or not proposal.proposed_text.strip()
                    or self._digest(proposal.original_text) != proposal.original_sha256):
                raise ValueError("invalid proposal")
            # Rebuild the display from the exact persisted replacement.
            proposal.diff = "".join(difflib.unified_diff(
                proposal.original_text.splitlines(keepends=True),
                proposal.proposed_text.splitlines(keepends=True),
                fromfile=f"a/{proposal.relative_path}", tofile=f"b/{proposal.relative_path}"))
            return proposal
        except (TypeError, ValueError, StorageError) as exc:
            raise ToolError(f"저장된 수정안을 읽지 못했습니다: {exc}") from exc

    def discard(self, proposal_id: str) -> bool:
        if self.storage is not None:
            try:
                removed = self.storage.delete_edit_proposal(self.workspace_key, proposal_id.strip())
            except StorageError as exc:
                raise ToolError(f"수정안 폐기 기록을 저장하지 못했습니다: {exc}") from exc
            self._pending.pop(proposal_id.strip(), None)
            self._pending_bundles.pop(proposal_id.strip(), None)
            return removed
        single = self._pending.pop(proposal_id.strip(), None) is not None
        return self._pending_bundles.pop(proposal_id.strip(), None) is not None or single

    # ── 변경 묶음 ────────────────────────────────────────────────────────────
    def _resolve_new(self, relative_path: str) -> Path:
        """새로 만들 파일의 경로. 이미 있는 폴더 안, 허용 확장자, 아직 없는 파일만."""
        raw = relative_path.strip()
        if not raw:
            raise ToolError("파일 경로가 필요합니다.")
        requested = Path(raw)
        if requested.is_absolute() or ".." in requested.parts:
            raise ToolError(f"새 파일은 프로젝트 안의 상대 경로여야 합니다: {raw}")
        if any(part.startswith(".") for part in requested.parts):
            raise ToolError(f"숨김 파일·폴더는 만들지 않습니다: {raw}")
        try:
            target = (self.allowed_root / requested).resolve()
        except OSError as exc:
            raise ToolError(f"경로를 해석하지 못했습니다: {exc}") from exc
        if self.allowed_root not in target.parents:
            raise ToolError("승인된 프로젝트 루트 밖에는 파일을 만들 수 없습니다.")
        if target.parent == self.allowed_root and target.name.lower() in PROTECTED_FILES:
            raise ToolError(f"{target.name} 은(는) 사용자만 만드는 파일입니다(검사 명령 목록).")
        if target.exists():
            raise ToolError(f"이미 있는 파일입니다 — 새 파일로 만들 수 없습니다: {raw}")
        missing = self._missing_dirs(target.parent)
        if len(missing) > MAX_NEW_DIR_DEPTH:
            raise ToolError(f"새 폴더는 한 번에 {MAX_NEW_DIR_DEPTH}단계까지만 만듭니다: {requested.parent}")
        if target.suffix.lower() not in READABLE_EXTENSIONS:
            raise ToolError(f"이 확장자는 만들 수 없습니다: {target.suffix or '(없음)'}")
        return target

    def _missing_dirs(self, folder: Path) -> list[Path]:
        """아직 없는 폴더들(위에서 아래 순서). 중간에 파일이 있으면 거부한다. 루트 밖은 _resolve_new 가 이미 막았다."""
        missing: list[Path] = []
        current = folder
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        if not current.is_dir():
            raise ToolError(f"폴더 자리에 파일이 있습니다: {current.relative_to(self.allowed_root) if self.allowed_root in current.parents else current}")
        return list(reversed(missing))

    @staticmethod
    def _remove_empty_dirs(folders: list[Path]) -> list[Path]:
        """새로 만들었던 폴더를 아래에서 위로 지운다. 비어 있지 않으면(사용자가 뭔가 넣었으면) 남긴다."""
        left = []
        for folder in reversed(folders):
            try:
                folder.rmdir()
            except OSError:
                if folder.exists():
                    left.append(folder)
        return left

    def _check_text(self, relative: str, text: str, *, allow_empty: bool = False) -> None:
        # 기존 파일을 통째로 비우는 수정은 막는다. 새로 만드는 빈 파일(__init__.py 등)은 흔하고 정상이다(2026-09-26 실측).
        if not text.strip() and not allow_empty:
            raise ToolError(f"{relative}: 빈 파일이 됩니다. 안전을 위해 거부했습니다.")
        if len(text.encode("utf-8")) > self.max_bytes:
            raise ToolError(f"{relative}: 너무 큽니다. 최대 {self.max_bytes} bytes입니다.")
        if relative.lower().endswith(".py"):
            try:
                ast.parse(text, filename=relative)
            except SyntaxError as exc:
                raise ToolError(f"{relative}: Python 문법 오류로 거부했습니다 — line {exc.lineno}: {exc.msg}") from exc

    @staticmethod
    def _bundle_diff(changes: list[FileChange]) -> str:
        parts = []
        for change in changes:
            shown = change.relative_path.replace("\\", "/")
            parts.append("".join(difflib.unified_diff(
                change.original_text.splitlines(keepends=True), change.proposed_text.splitlines(keepends=True),
                fromfile="/dev/null" if change.action == "create" else f"a/{shown}", tofile=f"b/{shown}")))
        return "".join(parts)

    def create_bundle(self, specs: list[dict], *, expected_originals: dict[str, str] | None = None) -> ChangeBundle:
        """`parse_change_blocks` 의 명세로 묶음 수정안을 만든다. 파일은 바꾸지 않는다."""
        if not specs:
            raise ToolError("변경할 파일이 없습니다.")
        if len(specs) > MAX_BUNDLE_FILES:
            raise ToolError(f"한 묶음은 최대 {MAX_BUNDLE_FILES}개 파일입니다.")
        changes: list[FileChange] = []
        seen: set[str] = set()
        for spec in specs:
            try:
                target = self._resolve_new(spec["path"]) if "create" in spec else self._resolve(spec["path"])
            except ToolError as exc:
                raise ToolError(f"{spec['path']}: {exc}") from exc
            if "create" in spec:
                relative = str(target.relative_to(self.allowed_root))
                content = str(spec["create"])
                content = content if content.endswith("\n") else content + "\n"
                self._check_text(relative, content, allow_empty=True)
                change = FileChange(relative, "create", "", "", content)
            else:
                relative = str(target.relative_to(self.allowed_root))
                original = self.read(relative)
                expected = (expected_originals or {}).get(relative, (expected_originals or {}).get(spec["path"]))
                if expected is not None and original != expected:
                    raise ToolError(f"{relative}: 모델이 수정안을 만드는 동안 파일이 바뀌었습니다. 다시 요청하세요.")
                working = original
                crlf = "\r\n" in original
                if "content" in spec:
                    # 파일 전체 교체 — 격리 복사본에서 통과한 결과를 실제 프로젝트에 옮길 때 쓴다(/dev-auto).
                    working = str(spec["content"]).replace("\r\n", "\n")
                    working = working if working.endswith("\n") else working + "\n"
                    if crlf:
                        working = working.replace("\n", "\r\n")
                for search, replace in spec.get("edits", []):
                    search, replace = (part.replace("\r\n", "\n") for part in (search, replace))
                    if crlf:
                        search, replace = (part.replace("\n", "\r\n") for part in (search, replace))
                    if not search.strip():
                        # 찾을 부분이 비었으면 파일 끝에 덧붙인다(널리 쓰이는 관례, gpt-oss:120b 실측). diff 로 보인다.
                        if not replace.strip():
                            raise ToolError(f"{relative}: 찾을 부분과 바꿀 내용이 모두 비어 있습니다.")
                        newline = "\r\n" if crlf else "\n"
                        gap = newline * (3 if relative.lower().endswith(".py") else 2)
                        working = working.rstrip("\r\n") + gap + replace.strip("\r\n") + newline
                        continue
                    found = working.count(search)
                    loose = 0
                    if found == 0:  # 글자 그대로는 없다 — 공백·들여쓰기·빈 줄 차이만 무시하고 한 곳과 맞으면 받는다(ADR-079)
                        loosened, loose = _loose_replace(working.replace("\r\n", "\n"), search.replace("\r\n", "\n"),
                                                         replace.replace("\r\n", "\n"))
                        if loosened is not None:
                            working = loosened.replace("\n", "\r\n") if crlf else loosened
                            continue
                    if found != 1:
                        first = search.strip().splitlines()[0][:80]
                        hint = f"(공백·빈 줄을 무시하면 {loose}곳 — 앞뒤 줄을 더 넣어 한 곳으로 좁혀라)" if loose > 1 else ""
                        raise ToolError(f"{relative}: 찾을 부분을 {'찾지 못했습니다' if found == 0 else f'{found}곳에서 찾았습니다(한 곳이어야 합니다)'}"
                                        f"{hint} — '{first}'. 파일은 변경하지 않았습니다.")
                    working = working.replace(search, replace, 1)
                if working == original:
                    raise ToolError(f"{relative}: 바뀐 내용이 없습니다.")
                self._check_text(relative, working)
                change = FileChange(relative, "modify", self._digest(original), original, working)
            if relative in seen:
                raise ToolError(f"같은 파일이 두 번 나옵니다: {relative}")
            seen.add(relative)
            changes.append(change)
        bundle = ChangeBundle(secrets.token_hex(6), changes, self._bundle_diff(changes))
        if self.storage is not None:
            payload = {"kind": "bundle", "proposal_id": bundle.proposal_id, "changes": [asdict(c) for c in changes]}
            try:
                self.storage.save_edit_proposal(self.workspace_key, bundle.proposal_id,
                                                json.dumps(payload, ensure_ascii=False))
            except StorageError as exc:
                raise ToolError(f"수정안을 저장하지 못했습니다. 파일 변경 없음: {exc}") from exc
        self._pending_bundles[bundle.proposal_id] = bundle
        while len(self._pending_bundles) > 20:
            self._pending_bundles.pop(next(iter(self._pending_bundles)))
        return bundle

    def get_bundle(self, proposal_id: str) -> ChangeBundle | None:
        proposal_id = proposal_id.strip()
        if self.storage is None:
            return self._pending_bundles.get(proposal_id)
        try:
            raw = self.storage.get_edit_proposal(self.workspace_key, proposal_id)
            if raw is None:
                return None
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("kind") != "bundle":
                return None
            changes = [FileChange(**item) for item in data["changes"]]
            if (data.get("proposal_id") != proposal_id or not changes or len(changes) > MAX_BUNDLE_FILES
                    or any(not isinstance(value, str) for c in changes for value in asdict(c).values())
                    or any(c.action not in ("modify", "create") for c in changes)
                    or any(c.action == "modify" and self._digest(c.original_text) != c.original_sha256 for c in changes)
                    or any(len(c.proposed_text.encode("utf-8")) > self.max_bytes
                           or (not c.proposed_text.strip() and c.action != "create") for c in changes)):
                raise ValueError("invalid bundle")
            return ChangeBundle(proposal_id, changes, self._bundle_diff(changes))
        except (KeyError, TypeError, ValueError, StorageError) as exc:
            raise ToolError(f"저장된 묶음 수정안을 읽지 못했습니다: {exc}") from exc

    @staticmethod
    def _write_temp(target: Path, data: bytes, mode_from: Path | None) -> str:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.jarvis-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if mode_from is not None:
                shutil.copymode(mode_from, temp_name)
        except OSError:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return temp_name

    def apply_bundle(self, proposal_id: str) -> tuple[list[Path], Path]:
        """묶음을 모두 적용하거나 하나도 적용하지 않는다. 원본은 한 백업 폴더에, 되돌리기 목록과 함께 남긴다."""
        bundle = self.get_bundle(proposal_id)
        if bundle is None:
            raise ToolError("묶음 수정안을 찾을 수 없습니다. 다시 /edit-files 로 만드세요.")
        planned: list[tuple[FileChange, Path]] = []
        for change in bundle.changes:
            if change.action == "modify":
                target = self._resolve(change.relative_path)
                if self._digest(self.read(change.relative_path)) != change.original_sha256:
                    raise ToolError(f"{change.relative_path}: 제안 후 파일이 바뀌었습니다. 오래된 묶음은 적용할 수 없습니다(아무것도 바꾸지 않음).")
            else:
                target = self._resolve_new(change.relative_path)
            self._check_text(change.relative_path, change.proposed_text, allow_empty=change.action == "create")
            planned.append((change, target))

        folder = self.backup_root / self._next_backup_stamp()
        manifest = {"bundle_id": bundle.proposal_id,
                    "modified": [c.relative_path for c, _ in planned if c.action == "modify"],
                    "created": [c.relative_path for c, _ in planned if c.action == "create"],
                    "applied_sha256": {c.relative_path: self._digest(c.proposed_text) for c, _ in planned}}
        new_dirs: list[Path] = []
        for change, target in planned:
            if change.action == "create":
                new_dirs += [d for d in self._missing_dirs(target.parent) if d not in new_dirs]
        manifest["created_dirs"] = [str(d.relative_to(self.allowed_root)) for d in new_dirs]
        temps: list[tuple[FileChange, Path, str]] = []
        made_dirs: list[Path] = []
        try:
            for change, target in planned:
                if change.action == "modify":
                    backup = folder / change.relative_path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / BUNDLE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            for new_dir in new_dirs:
                new_dir.mkdir()
                made_dirs.append(new_dir)
            for change, target in planned:
                temps.append((change, target, self._write_temp(
                    target, change.proposed_text.encode("utf-8"), target if change.action == "modify" else None)))
        except OSError as exc:
            for *_, temp in temps:
                Path(temp).unlink(missing_ok=True)
            self._remove_empty_dirs(made_dirs)
            raise ToolError(f"적용 준비(백업·임시 파일)에 실패해 아무것도 바꾸지 않았습니다: {exc}") from exc

        done: list[tuple[FileChange, Path]] = []
        try:
            for change, target, temp in temps:
                if change.action == "create" and target.exists():
                    raise OSError(f"그 사이 생긴 파일: {change.relative_path}")
                os.replace(temp, target)
                done.append((change, target))
        except OSError as exc:
            failures = []
            for change, target in reversed(done):
                try:
                    if change.action == "modify":
                        os.replace(self._write_temp(target, (folder / change.relative_path).read_bytes(), target), target)
                    else:
                        target.unlink()
                except OSError as undo_exc:
                    failures.append(f"{change.relative_path}: {undo_exc}")
            for change, target, temp in temps[len(done):]:
                Path(temp).unlink(missing_ok=True)
            self._remove_empty_dirs(made_dirs)
            if failures:
                raise ToolError(f"적용 중 실패했고 일부를 되돌리지 못했습니다 — 백업 {folder}: " + "; ".join(failures)) from exc
            raise ToolError(f"적용 중 실패하여 이미 바꾼 파일을 모두 되돌렸습니다: {exc}") from exc

        try:
            self.discard(bundle.proposal_id)
        except ToolError as exc:
            raise ToolError(f"묶음 적용과 백업은 완료했지만 수정안 기록 정리에 실패했습니다. {exc}") from exc
        return [target for _, target in planned], folder

    def undo_bundle(self, proposal_id: str) -> list[str]:
        """적용한 묶음을 통째로 되돌린다. 적용 뒤 바뀐 파일이 하나라도 있으면 아무것도 하지 않는다."""
        proposal_id = proposal_id.strip()
        folders = sorted(self.backup_root.iterdir(), reverse=True) if self.backup_root.exists() else []
        folder = manifest = None
        for candidate in folders:
            path = candidate / BUNDLE_MANIFEST
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if data.get("bundle_id") == proposal_id and not data.get("undone"):
                    folder, manifest = candidate, data
                    break
        if folder is None or manifest is None:
            raise ToolError("되돌릴 묶음 적용 기록을 찾지 못했습니다(이미 되돌렸거나 ID가 다릅니다).")
        for relative, digest in manifest["applied_sha256"].items():
            try:
                current = self.read(relative)
            except ToolError as exc:
                raise ToolError(f"{relative}: 지금 읽을 수 없어 되돌리지 않았습니다(아무것도 바꾸지 않음) — {exc}") from exc
            if self._digest(current) != digest:
                raise ToolError(f"{relative}: 묶음 적용 뒤 바뀌어 되돌리지 않았습니다(아무것도 바꾸지 않음).")
        undo_folder = self.backup_root / self._next_backup_stamp()
        restored: list[str] = []
        try:
            for relative in manifest["applied_sha256"]:
                keep = undo_folder / relative
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self._resolve(relative), keep)
            for relative in manifest["modified"]:
                target = self._resolve(relative)
                os.replace(self._write_temp(target, (folder / relative).read_bytes(), target), target)
                restored.append(f"복원: {relative}")
            for relative in manifest["created"]:
                self._resolve(relative).unlink()
                restored.append(f"삭제(새로 만들었던 파일): {relative}")
            folders = [self.allowed_root / relative for relative in manifest.get("created_dirs", [])]
            left = self._remove_empty_dirs(folders)
            restored += [f"삭제(새로 만들었던 폴더): {f.relative_to(self.allowed_root)}" for f in folders if f not in left]
            restored += [f"남김(다른 파일이 있는 새 폴더): {f.relative_to(self.allowed_root)}" for f in left]
        except (OSError, ToolError) as exc:
            raise ToolError(f"되돌리는 중 실패했습니다. 되돌리기 직전 상태는 {undo_folder} 에 있습니다: {exc}") from exc
        manifest["undone"] = True
        (folder / BUNDLE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        return restored

    def apply(self, proposal_id: str) -> tuple[Path, Path]:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise ToolError("편집 제안을 찾을 수 없습니다. 다시 /edit-file 로 제안부터 만드세요.")
        target = self._resolve(proposal.relative_path)
        current = self.read(proposal.relative_path)
        if self._digest(current) != proposal.original_sha256:
            raise ToolError("제안 후 파일이 변경되었습니다. 오래된 제안은 적용할 수 없습니다.")
        if target.suffix.lower() == ".py":
            try:
                ast.parse(proposal.proposed_text, filename=str(target))
            except SyntaxError as exc:
                raise ToolError("저장된 수정안의 Python 문법이 올바르지 않습니다.") from exc

        timestamp = self._next_backup_stamp()
        backup = self.backup_root / timestamp / proposal.relative_path
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(target, backup)
        except OSError as exc:
            raise ToolError(f"원본 백업에 실패하여 수정하지 않았습니다: {exc}") from exc

        # Atomic replacement in the same directory keeps a crash from leaving a
        # half-written source file.
        fd = -1
        temp_name = ""
        try:
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.jarvis-", dir=str(target.parent))
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                fd = -1
                handle.write(proposal.proposed_text)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copymode(target, temp_name)
            os.replace(temp_name, target)
            temp_name = ""
        except OSError as exc:
            raise ToolError(f"원자적 파일 교체에 실패했습니다. 백업은 유지됩니다: {exc}") from exc
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

        try:
            self.discard(proposal.proposal_id)
        except ToolError as exc:
            raise ToolError(f"파일 적용과 백업은 완료했지만 수정안 기록 정리에 실패했습니다. {exc}") from exc
        return target, backup

    def latest_backup(self, relative_path: str) -> Path | None:
        target = self._resolve(relative_path)
        relative = target.relative_to(self.allowed_root)
        # File names may contain glob metacharacters. Match literal paths only.
        candidates = sorted((folder / relative for folder in self.backup_root.iterdir()
                             if folder.is_dir() and (folder / relative).is_file()), reverse=True) if self.backup_root.exists() else []
        return candidates[0] if candidates else None

    def restore_latest(self, relative_path: str) -> tuple[Path, Path]:
        target = self._resolve(relative_path)
        backup = self.latest_backup(relative_path)
        if backup is None or not backup.is_file():
            raise ToolError("복구할 이전 ALEX 백업이 없습니다.")
        try:
            data = backup.read_bytes()
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError(f"백업을 읽지 못했습니다: {exc}") from exc
        # Preserve the current version before undoing so undo itself is reversible.
        timestamp = self._next_backup_stamp()
        undo_backup = self.backup_root / timestamp / target.relative_to(self.allowed_root)
        undo_backup.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.jarvis-restore-{secrets.token_hex(4)}")
        try:
            shutil.copy2(target, undo_backup)
            with temp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copymode(target, temp)
            os.replace(temp, target)
        except OSError as exc:
            raise ToolError(f"복구에 실패했습니다: {exc}") from exc
        finally:
            temp.unlink(missing_ok=True)
        return target, backup
