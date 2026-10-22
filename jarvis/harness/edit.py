"""고치기 9단계 — OpenCode v1.18.34 packages/opencode/src/tool/edit.ts 의 replace() 를 파이썬으로 옮긴 것(MIT, OPENCODE_LICENSE.txt).

모델이 준 oldString 이 파일과 글자 그대로 같지 않아도(앞뒤 빈칸·들여쓰기·이스케이프·가운데 몇 줄이 조금 다름) 뜻이 같은 곳을 찾아
바꾼다. 순서대로 시도하고, 찾은 곳이 하나일 때만 바꾼다(여럿이면 앞뒤 줄을 더 달라고 돌려보낸다). 찾은 범위가 oldString 보다 지나치게
크면(엉뚱한 큰 덩어리) 거절한다. 줄 끝(CRLF/LF)은 파일 것을 지킨다(normalize/restore).
"""
from __future__ import annotations

import re
from typing import Callable, Iterator

Replacer = Callable[[str, str], Iterator[str]]
SIMILARITY = 0.65  # 앞뒤 줄이 맞는 덩어리의 가운데 줄 유사도 하한(OpenCode 와 같음)


class EditError(ValueError):
    """모델에게 그대로 돌려줄 문장(영어 — 모델이 잘 따른다)."""


def levenshtein(a: str, b: str) -> int:
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _span(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """start~end 줄(포함)의 글자 위치."""
    begin = sum(len(line) + 1 for line in lines[:start])
    finish = begin + sum(len(line) for line in lines[start:end + 1]) + (end - start)
    return begin, finish


def simple(_content: str, find: str) -> Iterator[str]:
    yield find


def line_trimmed(content: str, find: str) -> Iterator[str]:
    original, search = content.split("\n"), find.split("\n")
    if search and search[-1] == "":
        search.pop()
    for i in range(len(original) - len(search) + 1):
        if all(original[i + j].strip() == search[j].strip() for j in range(len(search))):
            begin, end = _span(original, i, i + len(search) - 1)
            yield content[begin:end]


def _middle_similarity(original: list[str], start: int, end: int, search: list[str], early: bool) -> float:
    size = end - start + 1
    checks = min(len(search) - 2, size - 2)
    if checks <= 0:
        return 1.0
    total = 0.0
    for j in range(1, min(len(search) - 1, size - 1)):
        a, b = original[start + j].strip(), search[j].strip()
        longest = max(len(a), len(b))
        if not longest:
            continue
        total += (1 - levenshtein(a, b) / longest) / (checks if early else 1)
        if early and total >= SIMILARITY:
            break
    return total if early else total / checks


def block_anchor(content: str, find: str) -> Iterator[str]:
    original, search = content.split("\n"), find.split("\n")
    if len(search) < 3:
        return
    if search[-1] == "":
        search.pop()
    first, last = search[0].strip(), search[-1].strip()
    max_delta = max(1, len(search) // 4)
    candidates = []
    for i, line in enumerate(original):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(original)):
            if original[j].strip() == last:
                if abs((j - i + 1) - len(search)) <= max_delta:
                    candidates.append((i, j))
                break
    if not candidates:
        return
    if len(candidates) == 1:
        start, end = candidates[0]
        if _middle_similarity(original, start, end, search, early=True) >= SIMILARITY:
            begin, finish = _span(original, start, end)
            yield content[begin:finish]
        return
    best = max(candidates, key=lambda c: _middle_similarity(original, c[0], c[1], search, early=False))
    if _middle_similarity(original, best[0], best[1], search, early=False) >= SIMILARITY:
        begin, finish = _span(original, *best)
        yield content[begin:finish]


def whitespace_normalized(content: str, find: str) -> Iterator[str]:
    normalize = lambda text: re.sub(r"\s+", " ", text).strip()  # noqa: E731
    target = normalize(find)
    lines = content.split("\n")
    for line in lines:
        if normalize(line) == target:
            yield line
        elif target and target in normalize(line):
            words = find.strip().split()
            if words:
                match = re.search(r"\s+".join(re.escape(word) for word in words), line)
                if match:
                    yield match.group(0)
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if normalize(block) == target:
                yield block


def _dedent(text: str) -> str:
    lines = text.split("\n")
    filled = [line for line in lines if line.strip()]
    if not filled:
        return text
    indent = min(len(line) - len(line.lstrip()) for line in filled)
    return "\n".join(line if not line.strip() else line[indent:] for line in lines)


def indentation_flexible(content: str, find: str) -> Iterator[str]:
    target = _dedent(find)
    lines, size = content.split("\n"), len(find.split("\n"))
    for i in range(len(lines) - size + 1):
        block = "\n".join(lines[i:i + size])
        if _dedent(block) == target:
            yield block


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "`": "`", "\\": "\\", "\n": "\n", "$": "$"}


def _unescape(text: str) -> str:
    return re.sub(r"\\(n|t|r|'|\"|`|\\|\n|\$)", lambda m: _ESCAPES[m.group(1)], text)


def escape_normalized(content: str, find: str) -> Iterator[str]:
    target = _unescape(find)
    if target in content:
        yield target
    lines, size = content.split("\n"), len(target.split("\n"))
    for i in range(len(lines) - size + 1):
        block = "\n".join(lines[i:i + size])
        if _unescape(block) == target:
            yield block


def multi_occurrence(content: str, find: str) -> Iterator[str]:
    start = 0
    while find:
        index = content.find(find, start)
        if index < 0:
            return
        yield find
        start = index + len(find)


def trimmed_boundary(content: str, find: str) -> Iterator[str]:
    trimmed = find.strip()
    if trimmed == find:
        return
    if trimmed in content:
        yield trimmed
    lines, size = content.split("\n"), len(find.split("\n"))
    for i in range(len(lines) - size + 1):
        block = "\n".join(lines[i:i + size])
        if block.strip() == trimmed:
            yield block


def context_aware(content: str, find: str) -> Iterator[str]:
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines[-1] == "":
        find_lines.pop()
    lines = content.split("\n")
    first, last = find_lines[0].strip(), find_lines[-1].strip()
    for i, line in enumerate(lines):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(lines)):
            if lines[j].strip() != last:
                continue
            block = lines[i:j + 1]
            if len(block) == len(find_lines):
                matching = total = 0
                for k in range(1, len(block) - 1):
                    a, b = block[k].strip(), find_lines[k].strip()
                    if a or b:
                        total += 1
                        matching += a == b
                if total == 0 or matching / total >= 0.5:
                    yield "\n".join(block)
                    return
            break


REPLACERS: tuple[Replacer, ...] = (simple, line_trimmed, block_anchor, whitespace_normalized, indentation_flexible,
                                   escape_normalized, trimmed_boundary, context_aware, multi_occurrence)


def _disproportionate(search: str, old: str) -> bool:
    old_lines, search_lines = old.count("\n") + 1, search.count("\n") + 1
    if search_lines >= max(old_lines + 3, old_lines * 2):
        return True
    if old_lines == 1:
        return False
    return len(search.strip()) > max(len(old.strip()) + 500, len(old.strip()) * 4)


def replace(content: str, old: str, new: str, replace_all: bool = False) -> str:
    """content(줄 끝 LF)에서 old 를 new 로. 못 찾거나 여럿이면 EditError."""
    if old == new:
        raise EditError("No changes to apply: oldString and newString are identical.")
    if old == "":
        raise EditError("oldString cannot be empty when editing an existing file. Provide the exact text to replace, "
                        "or use write for an intentional full-file replacement.")
    found = False
    for replacer in REPLACERS:
        for search in replacer(content, old):
            index = content.find(search)
            if index < 0 or not search:
                continue
            found = True
            if _disproportionate(search, old):
                raise EditError("Refusing replacement because the matched span is much larger than oldString. "
                                "Re-read the file and provide the full exact oldString for the intended replacement.")
            if replace_all:
                return content.replace(search, new)
            if index != content.rfind(search):
                continue
            return content[:index] + new + content[index + len(search):]
    if not found:
        raise EditError("Could not find oldString in the file. It must match exactly, including whitespace, "
                        "indentation, and line endings.")
    raise EditError("Found multiple matches for oldString. Provide more surrounding context to make the match unique.")


def line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def restore(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text
