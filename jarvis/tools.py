"""Safe tools included in ALEX.

보안 원칙(ADR-006):
- 임의 shell 실행 없음
- 파일 "이름"만 검색하고 내용은 읽지 않음
- 승인된 루트 밖으로는 절대 나가지 않음 (symlink 포함)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re

from .hardware import HardwareInfo


class ToolError(RuntimeError):
    """Tool 실행 실패를 Core 가 잡을 수 있게 하는 단일 예외."""


class BaseTool(ABC):
    name = ""
    description = ""

    @abstractmethod
    def run(self, **kwargs: object) -> str:
        raise NotImplementedError


class SystemInfoTool(BaseTool):
    name = "system_info"
    description = "Show current date/time plus detected OS, CPU, RAM, GPU, VRAM and profile."

    def __init__(self, hardware: HardwareInfo) -> None:
        self.hardware = hardware

    def run(self, **kwargs: object) -> str:
        # 시계가 빠져 있었다. 이 도구가 "이 기계에 대해 아는 것" 을 보고하는 자리인데
        # 정작 날짜가 없어서, 2026-09-21 에 모델이 학습 데이터의 날짜를 사실처럼 말했다.
        now = datetime.now().astimezone()
        lines = [f"now: {now:%Y-%m-%d %H:%M:%S %z}"]
        info = asdict(self.hardware)
        info["profile"] = self.hardware.profile.value
        lines += [f"{key}: {value}" for key, value in info.items()]
        return "\n".join(lines)


# 검색에서 항상 건너뛰는 디렉터리. 결과 품질과 속도를 함께 지킨다.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".mypy_cache", "data", "logs", "runtime", "dist"}
FORBIDDEN_QUERY_CHARS = ("/", "\\", "..", "~", ":")


class SafeFileSearchTool(BaseTool):
    """승인된 루트 디렉터리 안에서 "파일 이름"만 검색한다.

    v0.1.1 에서 강화한 부분:
    - 검색어에 경로 문자(`/`, `\\`, `..`)가 있으면 거부한다.
    - symlink 로 루트 밖을 가리키는 항목은 결과에서 제외한다.
    - 권한 오류가 나도 죽지 않고 건너뛴다.
    - 결과 개수를 제한한다.
    """

    name = "file_search"
    description = "Search file names within an approved project directory."

    def __init__(self, allowed_root: Path, max_results: int = 20, max_scanned_files: int = 20_000) -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.max_results = max(1, int(max_results))
        self.max_scanned_files = max(1, int(max_scanned_files))

    def _is_inside_root(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved == self.allowed_root or self.allowed_root in resolved.parents

    def run(self, **kwargs: object) -> str:
        query = str(kwargs.get("query", "")).strip().lower()
        if not query:
            return "검색어가 필요합니다. 예: /find router"

        for bad in FORBIDDEN_QUERY_CHARS:
            if bad in query:
                raise ToolError(
                    f"검색어에 경로 문자({bad})는 사용할 수 없습니다. 파일 이름 일부만 입력하세요."
                )

        if not self.allowed_root.is_dir():
            raise ToolError(f"검색 루트가 존재하지 않습니다: {self.allowed_root}")

        matches: list[str] = []
        scanned = 0
        scan_limited = False
        for path in self._walk(self.allowed_root):
            scanned += 1
            if scanned > self.max_scanned_files:
                scan_limited = True
                break
            if len(matches) >= self.max_results:
                matches.append("... (결과가 더 있습니다. 검색어를 좁혀 주세요.)")
                break
            if query in path.name.lower() and self._is_inside_root(path):
                matches.append(str(path.relative_to(self.allowed_root)))

        if scan_limited:
            matches.append(f"... (안전을 위해 {self.max_scanned_files}개 파일에서 검색을 중단했습니다.)")
        if not matches:
            return f"'{query}' 와(과) 일치하는 파일이 없습니다. (루트: {self.allowed_root})"
        return "\n".join(matches)

    def _walk(self, root: Path):
        """SKIP_DIRS 와 권한 오류를 건너뛰며 파일만 내놓는 생성자(generator)."""
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except (PermissionError, OSError):
                continue
            for entry in entries:
                # Directory symlink는 root 내부를 가리켜도 cycle(a -> parent -> a)을 만들 수 있어
                # 탐색 대상에서 제외한다. File symlink만 root 내부일 때 허용한다.
                if entry.is_symlink():
                    if not self._is_inside_root(entry):
                        continue
                    try:
                        if entry.is_dir():
                            continue
                    except OSError:
                        continue
                try:
                    if entry.is_dir():
                        if entry.name in SKIP_DIRS or entry.name.startswith("."):
                            continue
                        stack.append(entry)
                    elif entry.is_file():
                        yield entry
                except OSError:
                    continue

# 읽기 허용 확장자는 의도적으로 텍스트/코드 파일 위주로 제한한다.
READABLE_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".csv", ".sql", ".html", ".css", ".js", ".ts", ".java", ".c", ".h", ".cpp",
    ".hpp", ".rs", ".go", ".sh", ".ps1", ".bat",
}


class SafeProjectTreeTool(BaseTool):
    """Show a bounded project tree without reading file contents."""

    name = "project_tree"
    description = "Show a bounded directory/file tree inside the approved project root."

    def __init__(self, allowed_root: Path, max_entries: int = 300, max_depth: int = 4) -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.max_entries = max(10, int(max_entries))
        self.max_depth = max(1, min(int(max_depth), 8))

    def run(self, **kwargs: object) -> str:
        try:
            requested_depth = int(kwargs.get("depth", self.max_depth))
        except (TypeError, ValueError) as exc:
            raise ToolError("tree depth 는 정수여야 합니다.") from exc
        depth_limit = max(1, min(requested_depth, self.max_depth))
        if not self.allowed_root.is_dir():
            raise ToolError(f"프로젝트 루트가 존재하지 않습니다: {self.allowed_root}")

        lines = [f"{self.allowed_root.name}/"]
        count = 0
        truncated = False

        def walk(directory: Path, depth: int, prefix: str) -> None:
            nonlocal count, truncated
            if depth > depth_limit or truncated:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except (OSError, PermissionError):
                return
            visible = []
            for entry in entries:
                if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                    continue
                if entry.is_symlink():
                    # Tree is navigational only; skip symlinks to avoid confusing aliases/cycles.
                    continue
                visible.append(entry)
            for idx, entry in enumerate(visible):
                if count >= self.max_entries:
                    truncated = True
                    return
                count += 1
                branch = "└── " if idx == len(visible) - 1 else "├── "
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{prefix}{branch}{entry.name}{suffix}")
                if entry.is_dir() and depth < depth_limit:
                    child_prefix = prefix + ("    " if idx == len(visible) - 1 else "│   ")
                    walk(entry, depth + 1, child_prefix)
                    if truncated:
                        return

        walk(self.allowed_root, 1, "")
        if truncated:
            lines.append(f"... [최대 {self.max_entries}개 항목에서 중단]")
        return "\n".join(lines)


class SafeFileReadTool(BaseTool):
    """승인된 루트 안의 작은 텍스트 파일을 명시적 경로로 읽는다.

    검색과 달리 파일 내용은 민감할 수 있으므로 자연어 자동 실행이 아니라 CLI `/read` 같은
    명시적 사용자 동작에서만 호출하는 것을 기본으로 한다.
    """

    name = "file_read"
    description = "Read a small text/code file within an approved project directory."

    def __init__(self, allowed_root: Path, max_bytes: int = 100_000) -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.max_bytes = max(1, int(max_bytes))

    def _resolve_safe(self, relative_path: str) -> Path:
        raw = relative_path.strip()
        if not raw:
            raise ToolError("읽을 파일 경로가 필요합니다. 예: /read jarvis/router.py")
        requested = Path(raw)
        if requested.is_absolute():
            raise ToolError("절대 경로는 읽을 수 없습니다. 승인된 루트 기준 상대 경로를 사용하세요.")
        try:
            resolved = (self.allowed_root / requested).resolve()
        except OSError as exc:
            raise ToolError(f"경로를 해석하지 못했습니다: {exc}") from exc
        if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
            raise ToolError("승인된 파일 루트 밖으로 나갈 수 없습니다.")
        return resolved

    def run(self, **kwargs: object) -> str:
        target = self._resolve_safe(str(kwargs.get("path", "")))
        if not target.exists() or not target.is_file():
            raise ToolError(f"파일이 없습니다: {target.relative_to(self.allowed_root)}")
        if target.is_symlink():
            # resolve 결과는 이미 root 내부임을 확인했지만 명시적으로 한 번 더 의도를 드러낸다.
            resolved = target.resolve()
            if self.allowed_root not in resolved.parents and resolved != self.allowed_root:
                raise ToolError("루트 밖을 가리키는 symlink 는 읽을 수 없습니다.")
        if target.suffix.lower() not in READABLE_EXTENSIONS:
            raise ToolError(f"이 확장자는 읽기 허용 목록에 없습니다: {target.suffix or '(없음)'}")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise ToolError(f"파일 크기를 확인하지 못했습니다: {exc}") from exc
        if size > self.max_bytes:
            raise ToolError(
                f"파일이 너무 큽니다: {size} bytes (최대 {self.max_bytes} bytes). "
                "필요한 부분만 작은 파일로 나누거나 제한을 명시적으로 조정하세요."
            )
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("UTF-8 텍스트 파일만 읽을 수 있습니다.") from exc
        except OSError as exc:
            raise ToolError(f"파일을 읽지 못했습니다: {exc}") from exc


# 자연어 질문에서 검색어를 뽑을 때 제외할 일반 단어. 완전한 형태소 분석이 아니라
# "작고 설명 가능한" lexical retrieval을 위한 최소 목록이다.
AUTO_CONTEXT_SENSITIVE_NAME_PARTS = (
    "credential", "credentials", "secret", "secrets", "token", "tokens",
    "apikey", "api_key", "private_key", "id_rsa", "id_ed25519",
)


PROJECT_QUERY_STOPWORDS = {
    "이", "그", "저", "것", "거", "좀", "줘", "해줘", "알려줘", "알려", "설명", "어디",
    "파일", "코드", "프로젝트", "전체", "관련", "대한", "하는", "있는", "에서", "으로",
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "is", "are", "how", "what",
    "where", "file", "code", "project", "please", "show", "find", "explain",
}


@dataclass(slots=True)
class ProjectContextResult:
    """Bounded local retrieval result used by `/grep` and `/ask-project`."""

    text: str
    files_scanned: int
    bytes_scanned: int
    matched_files: int
    truncated: bool


class SafeProjectContextTool(BaseTool):
    """승인된 프로젝트에서 관련 **텍스트 조각**만 Local 용도로 찾는다.

    이것은 Vector DB/RAG 프레임워크가 아니다. 작은 코드베이스에서 먼저 쓸 수 있는
    bounded lexical retrieval이다. 네트워크 호출을 하지 않으며, Core의 `/ask-project`
    경로에서는 Cloud fallback 자체를 사용하지 않는다.

    안전 경계:
    - SafeFileSearchTool과 같은 root/symlink/skip-dir 규칙 사용
    - 허용 확장자만 읽기
    - 파일 수/파일 크기/총 scan bytes/context chars 제한
    - UTF-8이 아닌 파일은 건너뜀
    """

    name = "project_context"
    description = "Find bounded relevant code/text snippets inside an approved project root."

    def __init__(
        self,
        allowed_root: Path,
        *,
        max_files: int = 1500,
        max_total_bytes: int = 5_000_000,
        max_file_bytes: int = 200_000,
        max_context_chars: int = 12_000,
        max_results: int = 8,
        context_lines: int = 3,
    ) -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.max_files = max(1, int(max_files))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_context_chars = max(500, int(max_context_chars))
        self.max_results = max(1, int(max_results))
        self.context_lines = max(0, int(context_lines))
        self._walker = SafeFileSearchTool(
            self.allowed_root,
            max_results=self.max_results,
            max_scanned_files=self.max_files,
        )
        # Small in-memory cache avoids rereading unchanged source files on every `/ask-project`.
        # Cache keys never leave the approved root and are bounded by max_files.
        self._content_cache: dict[Path, tuple[int, int, str]] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    @staticmethod
    def _terms(query: str) -> list[str]:
        raw = re.findall(r"[0-9A-Za-z_가-힣.\-]{2,}", query.lower())
        terms: list[str] = []
        for token in raw:
            token = token.strip(".-_")
            if len(token) < 2 or token in PROJECT_QUERY_STOPWORDS:
                continue
            if token not in terms:
                terms.append(token)
        return terms[:12]

    @staticmethod
    def _line_score(line: str, terms: list[str]) -> int:
        lowered = line.lower()
        return sum(min(4, lowered.count(term)) for term in terms)

    def search(self, query: str) -> ProjectContextResult:
        terms = self._terms(query)
        if not terms:
            return ProjectContextResult(
                "검색에 사용할 핵심 단어가 없습니다. 더 구체적인 이름/함수/개념을 넣어 주세요.",
                0,
                0,
                0,
                False,
            )
        if not self.allowed_root.is_dir():
            raise ToolError(f"프로젝트 루트가 존재하지 않습니다: {self.allowed_root}")

        scored: list[tuple[int, str, list[str], int]] = []
        files_scanned = 0
        bytes_scanned = 0
        truncated = False

        for path in self._walker._walk(self.allowed_root):
            if files_scanned >= self.max_files or bytes_scanned >= self.max_total_bytes:
                truncated = True
                break
            if path.suffix.lower() not in READABLE_EXTENSIONS:
                continue
            name_lower = path.name.lower()
            if any(part in name_lower for part in AUTO_CONTEXT_SENSITIVE_NAME_PARTS):
                # `/read` remains available for explicit user selection; automatic retrieval is stricter.
                continue
            try:
                stat = path.stat()
                size = stat.st_size
                mtime_ns = stat.st_mtime_ns
            except OSError:
                continue
            if size <= 0 or size > self.max_file_bytes:
                continue
            if bytes_scanned + size > self.max_total_bytes:
                truncated = True
                break

            files_scanned += 1
            bytes_scanned += size
            cached = self._content_cache.get(path)
            if cached is not None and cached[0] == mtime_ns and cached[1] == size:
                content = cached[2]
                self.cache_hits += 1
            else:
                try:
                    content = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                self.cache_misses += 1
                if path not in self._content_cache and len(self._content_cache) >= self.max_files:
                    self._content_cache.pop(next(iter(self._content_cache)), None)
                self._content_cache[path] = (mtime_ns, size, content)

            lowered = content.lower()
            relative = str(path.relative_to(self.allowed_root))
            filename = path.name.lower()
            score = sum(min(20, lowered.count(term)) for term in terms)
            score += sum(8 for term in terms if term in filename)
            if score <= 0:
                continue
            scored.append((score, relative, content.splitlines(), size))

        scored.sort(key=lambda item: (-item[0], item[1]))
        blocks: list[str] = []
        used_chars = 0
        matched_files = 0

        for score, relative, lines, _size in scored[: self.max_results]:
            if not lines:
                continue
            ranked_lines = sorted(
                ((self._line_score(line, terms), idx) for idx, line in enumerate(lines)),
                reverse=True,
            )
            best_score, best_idx = ranked_lines[0]
            if best_score <= 0:
                best_idx = 0
            start = max(0, best_idx - self.context_lines)
            end = min(len(lines), best_idx + self.context_lines + 1)
            numbered = "\n".join(
                f"{idx + 1:>5}: {lines[idx]}" for idx in range(start, end)
            )
            block = f"### {relative}  [score={score}]\n{numbered}"
            if used_chars + len(block) > self.max_context_chars:
                remaining = self.max_context_chars - used_chars
                if remaining > 200:
                    blocks.append(block[:remaining] + "\n... [context truncated]")
                    matched_files += 1
                truncated = True
                break
            blocks.append(block)
            matched_files += 1
            used_chars += len(block)

        if not blocks:
            text = (
                f"검색어 {terms!r} 와 직접 일치하는 코드/텍스트를 찾지 못했습니다. "
                "정확한 함수명/파일명/오류 문자열을 넣어 다시 검색해 보세요."
            )
        else:
            header = (
                f"검색어={terms!r} | scanned_files={files_scanned} | "
                f"scanned_bytes={bytes_scanned} | matched_files={matched_files}"
            )
            text = header + "\n\n" + "\n\n".join(blocks)
            if truncated:
                text += "\n\n[안전 한도 때문에 검색/컨텍스트 일부가 생략되었습니다.]"

        return ProjectContextResult(text, files_scanned, bytes_scanned, matched_files, truncated)

    def run(self, **kwargs: object) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "검색어가 필요합니다. 예: /grep route fallback"
        return self.search(query).text
