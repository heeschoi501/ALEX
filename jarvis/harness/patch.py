"""여러 파일 패치(apply_patch) — OpenCode v1.18.34 packages/opencode/src/patch/index.ts 를 옮긴 것(MIT, OPENCODE_LICENSE.txt).

형식(Codex 의 패치 언어):
    *** Begin Patch
    *** Add File: <path>          (+ 로 시작하는 줄이 내용)
    *** Delete File: <path>
    *** Update File: <path>       (선택: *** Move to: <new path>)
    @@ <찾을 앞 줄>                (' ' 유지, '-' 지움, '+' 넣음, '*** End of File' 은 끝에 맞춤)
    *** End Patch
고칠 자리는 4단계로 찾는다: 그대로 → 줄 끝 빈칸 무시 → 앞뒤 빈칸 무시 → 둥근 따옴표·긴 줄표 등을 ASCII 로.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


class PatchError(ValueError):
    pass


@dataclass
class Chunk:
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    context: str = ""
    end_of_file: bool = False


@dataclass
class Hunk:
    kind: str                 # add | delete | update
    path: str
    contents: str = ""
    move_path: str = ""
    chunks: list[Chunk] = field(default_factory=list)


def _strip_heredoc(text: str) -> str:
    match = re.match(r"^(?:cat\s+)?<<['\"]?(\w+)['\"]?\s*\n([\s\S]*?)\n\1\s*$", text)
    return match.group(2) if match else text


def parse(text: str) -> list[Hunk]:
    lines = _strip_heredoc(text.strip()).split("\n")
    begin = next((i for i, line in enumerate(lines) if line.strip() == "*** Begin Patch"), -1)
    end = next((i for i, line in enumerate(lines) if line.strip() == "*** End Patch"), -1)
    if begin < 0 or end < 0 or begin >= end:
        raise PatchError("Invalid patch format: missing Begin/End markers")
    hunks: list[Hunk] = []
    i = begin + 1
    while i < end:
        line = lines[i]
        if line.startswith("*** Add File:"):
            path = line[len("*** Add File:"):].strip()
            i += 1
            body = []
            while i < end and not lines[i].startswith("***"):
                if lines[i].startswith("+"):
                    body.append(lines[i][1:])
                i += 1
            hunks.append(Hunk("add", path, contents="\n".join(body)))
        elif line.startswith("*** Delete File:"):
            hunks.append(Hunk("delete", line[len("*** Delete File:"):].strip()))
            i += 1
        elif line.startswith("*** Update File:"):
            hunk = Hunk("update", line[len("*** Update File:"):].strip())
            i += 1
            if i < end and lines[i].startswith("*** Move to:"):
                hunk.move_path = lines[i][len("*** Move to:"):].strip()
                i += 1
            while i < end and not lines[i].startswith("***"):
                if not lines[i].startswith("@@"):
                    i += 1
                    continue
                chunk = Chunk(context=lines[i][2:].strip())
                i += 1
                while i < end and not lines[i].startswith("@@") and not lines[i].startswith("***"):
                    change = lines[i]
                    if change.startswith(" "):
                        chunk.old.append(change[1:])
                        chunk.new.append(change[1:])
                    elif change.startswith("-"):
                        chunk.old.append(change[1:])
                    elif change.startswith("+"):
                        chunk.new.append(change[1:])
                    i += 1
                if i < end and lines[i] == "*** End of File":
                    chunk.end_of_file = True
                    i += 1
                hunk.chunks.append(chunk)
            hunks.append(hunk)
        else:
            i += 1
    if any(not h.path for h in hunks):
        raise PatchError("Invalid patch format: a file header has no path")
    return hunks


_UNICODE = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"',
                          "„": '"', "‟": '"', "‐": "-", "‑": "-", "‒": "-", "–": "-",
                          "—": "-", "―": "-", " ": " "})


def _normal(text: str) -> str:
    return text.strip().translate(_UNICODE).replace("…", "...")


_COMPARES = (lambda a, b: a == b, lambda a, b: a.rstrip() == b.rstrip(), lambda a, b: a.strip() == b.strip(),
             lambda a, b: _normal(a) == _normal(b))


def _try(lines: list[str], pattern: list[str], start: int, same, eof: bool) -> int:
    if eof:
        from_end = len(lines) - len(pattern)
        if from_end >= start and all(same(lines[from_end + j], pattern[j]) for j in range(len(pattern))):
            return from_end
    for i in range(start, len(lines) - len(pattern) + 1):
        if all(same(lines[i + j], pattern[j]) for j in range(len(pattern))):
            return i
    return -1


def seek(lines: list[str], pattern: list[str], start: int, eof: bool = False) -> int:
    if not pattern:
        return -1
    for same in _COMPARES:
        found = _try(lines, pattern, start, same, eof)
        if found >= 0:
            return found
    return -1


def apply_chunks(path: str, chunks: list[Chunk], original: str) -> str:
    """원래 글(줄 끝 LF)에 조각들을 적용한 새 글(끝 줄바꿈 있음)."""
    lines = original.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    replacements: list[tuple[int, int, list[str]]] = []
    index = 0
    for chunk in chunks:
        if chunk.context:
            found = seek(lines, [chunk.context], index)
            if found < 0:
                raise PatchError(f"Failed to find context '{chunk.context}' in {path}")
            index = found + 1
        if not chunk.old:
            at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
            replacements.append((at, 0, chunk.new))
            continue
        pattern, new = chunk.old, chunk.new
        found = seek(lines, pattern, index, chunk.end_of_file)
        if found < 0 and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new and new[-1] == "":
                new = new[:-1]
            found = seek(lines, pattern, index, chunk.end_of_file)
        if found < 0:
            raise PatchError(f"Failed to find expected lines in {path}:\n" + "\n".join(chunk.old))
        replacements.append((found, len(pattern), new))
        index = found + len(pattern)
    result = list(lines)
    for start, length, new in sorted(replacements, key=lambda r: r[0], reverse=True):
        result[start:start + length] = new
    if not result or result[-1] != "":
        result.append("")
    return "\n".join(result)
