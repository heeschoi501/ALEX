"""큰 파일을 모델 문맥 예산 안에 보여 주는 발췌 (Coding Mode 4단계, 2026-09-26 claude, ADR-067).

묶음 수정안의 '찾을 부분'은 파일 전체에서 정확히 한 곳과 맞아야 적용된다(`workspace.create_bundle`). 그래서 모델에게 파일 전체가
아니라 고칠 곳 주변만 보여 줘도 안전하다 — 보여 주지 않은 곳을 지어낸 수정안은 적용 전에 거부된다.
어디를 보일지는 지시·실패 출력에 나온 것으로 고른다(중요한 순서): 트레이스백에 나온 이 파일의 줄이 든 함수, 이름이 나온 함수·클래스·
상수, 파일 머리(import), 남는 자리에 나머지 함수·클래스의 첫 줄(개요).
"""
from __future__ import annotations

import ast
import keyword
import re

GAP = "⋯ 줄 {start}–{end} 생략 ⋯"
EXCERPT_NOTE = "[발췌 — 전체 {total}줄 중 일부. '⋯ 줄 a–b 생략 ⋯' 표시 줄은 파일에 없다]\n"
EXCERPT_RULE = ("- '[발췌' 로 시작하는 파일은 일부만 보인 것이다. '⋯ 줄 a–b 생략 ⋯' 줄은 파일에 없으니 찾을 부분에 넣지 말고, "
                "보여 준 줄을 들여쓰기까지 그대로 옮겨라. 보이지 않는 부분은 추측하지 않는다.\n")
MAX_BLOCK_LINES = 160  # 함수·클래스가 이보다 길면 걸린 줄 주변만 보인다
WINDOW = 12  # 함수 밖 줄·긴 함수 안 줄 주변 앞뒤 줄 수
MIN_SHARE = 800  # 발췌라도 이보다 좁으면 보여 줄 수 없다

_DEF = re.compile(r"^([ \t]*)(?:async[ \t]+def|def|class)[ \t]+([A-Za-z_]\w*)")
_WORD = re.compile(r"[A-Za-z_]\w{3,}")
_TRACE = re.compile(r'File "([^"]+)", line (\d+)')


class _Span:
    __slots__ = ("name", "start", "end", "depth", "kind", "first")

    def __init__(self, name: str, start: int, end: int, depth: int, kind: str, first: int | None = None) -> None:
        self.name, self.start, self.end, self.depth, self.kind = name, start, end, depth, kind
        self.first = start if first is None else first  # def/class 줄(장식자 다음)


def _python_spans(text: str) -> list[_Span] | None:
    """함수·클래스(장식자 포함)와 모듈 수준 대입의 줄 범위. 0부터 센 [start, end). 파이썬이 아니거나 문법 오류면 None."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    spans: list[_Span] = []

    def visit(node: ast.AST, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list]) - 1
                spans.append(_Span(child.name, start, child.end_lineno or child.lineno, depth, "def", child.lineno - 1))
                visit(child, depth + 1)
            elif depth == 0 and isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        spans.append(_Span(target.id, child.lineno - 1, child.end_lineno or child.lineno, 0, "const"))
            elif depth == 0 and isinstance(child, (ast.If, ast.Try, ast.With)):
                visit(child, depth)  # 모듈 수준 if/try 안의 정의도 센다

    visit(tree, 0)
    return spans


def _text_spans(lines: list[str]) -> list[_Span]:
    """파이썬으로 못 읽는 파일: 들여쓰기로 def/class 범위를 어림한다."""
    spans = []
    for number, line in enumerate(lines):
        match = _DEF.match(line)
        if not match:
            continue
        indent = len(match.group(1).expandtabs())
        end = number + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped and len(lines[end]) - len(lines[end].lstrip()) <= indent and not stripped.startswith((")", "#")):
                break
            end += 1
        while end > number + 1 and not lines[end - 1].strip():
            end -= 1
        spans.append(_Span(match.group(2), number, end, indent, "def"))
    return spans


def _head_end(lines: list[str], limit: int = 40) -> int:
    """파일 머리(import 줄)가 끝나는 줄."""
    end = 0
    for number, line in enumerate(lines[:limit]):
        if line.startswith(("import ", "from ")):
            end = number + 1
    return end


def _trace_lines(relative_path: str, hints: str, total: int) -> list[int]:
    target = relative_path.replace("\\", "/").lower()
    target = target[2:] if target.startswith("./") else target.lstrip("/")
    numbers: list[int] = []
    for path, line in _TRACE.findall(hints):
        normalized = path.replace("\\", "/").lower()
        if normalized == target or normalized.endswith("/" + target):
            number = int(line) - 1
            if 0 <= number < total and number not in numbers:
                numbers.append(number)
    return numbers


def _ranges(relative_path: str, lines: list[str], text: str, hints: str) -> list[tuple[int, int]]:
    spans = _python_spans(text) if relative_path.lower().endswith(".py") else None
    if spans is None:
        spans = _text_spans(lines)
    defs = [s for s in spans if s.kind == "def"]
    ranges: list[tuple[int, int]] = []

    def around(number: int) -> tuple[int, int]:
        inside = [s for s in defs if s.start <= number < s.end]
        if inside:
            span = max(inside, key=lambda s: s.depth)
            if span.end - span.start <= MAX_BLOCK_LINES:
                return span.start, span.end
        return max(0, number - WINDOW), min(len(lines), number + WINDOW + 1)

    for number in _trace_lines(relative_path, hints, len(lines)):
        ranges.append(around(number))
    words = {w for w in _WORD.findall(hints) if not keyword.iskeyword(w) and not w.startswith("__")}
    named: dict[str, list[_Span]] = {}
    for span in spans:
        if span.name in words:
            named.setdefault(span.name, []).append(span)
    for name in sorted(named, key=hints.find):
        if len(named[name]) <= 2:  # 여러 곳에 있는 흔한 이름(read 등)은 단서가 못 된다
            for span in named[name]:
                ranges.append((span.start, min(span.end, span.start + MAX_BLOCK_LINES)))
    head = _head_end(lines)
    if head:
        ranges.append((0, head))
    for span in _ranked_bodies(defs, lines, hints):  # 단서 낱말을 많이 공유하는 함수 본문
        ranges.append((span.start, span.end))
    for span in sorted(defs, key=lambda s: (s.depth, s.start)):  # 개요: 나머지 정의의 첫 줄
        ranges.append((span.first, span.first + 1))
    return ranges


_TOKEN = re.compile(r"[A-Za-z]+|[가-힣]{2,}")
_STOP = {"self", "none", "true", "false", "return", "import", "from", "the", "and", "for", "with", "not", "str", "int",
         "traceback", "most", "recent", "call", "last", "file", "line", "tests", "test", "error", "fail", "assert"}


def _stems(text: str) -> set[str]:
    """식별자는 _·대소문자 경계로 쪼개고, 짧은 꼬리(s·ing·ed)를 떼어 deleting/delete, tests/test 가 만나게 한다."""
    found = set()
    for token in _TOKEN.findall(re.sub(r"([a-z])([A-Z])", r"\1 \2", text)):
        token = token.lower()
        for tail in ("ing", "ed", "es", "s", "e"):
            if token.isascii() and token.endswith(tail) and len(token) - len(tail) >= 4:
                token = token[: -len(tail)]
                break
        if len(token) >= 3 and token not in _STOP:
            found.add(token)
    return found


def _ranked_bodies(defs: list[_Span], lines: list[str], hints: str) -> list[_Span]:
    """함수(가장 안쪽 정의)를 단서와 겹치는 드문 낱말의 가중합으로 줄 세운다. 한 곳에만 나오는 낱말일수록 무겁다."""
    import math

    leaves = [s for s in defs if not any(o is not s and s.start <= o.start and o.end <= s.end for o in defs)]
    leaves = [s for s in leaves if s.end - s.start <= MAX_BLOCK_LINES]
    if not leaves:
        return []
    query = _stems(hints)
    bags = [_stems("\n".join(lines[s.start:s.end])) for s in leaves]
    frequency: dict[str, int] = {}
    for bag in bags:
        for word in bag & query:
            frequency[word] = frequency.get(word, 0) + 1
    scored = []
    for span, bag in zip(leaves, bags):
        score = sum(math.log(1 + len(leaves) / frequency[word]) for word in bag & query)
        if score > 0:
            scored.append((score / (1 + (span.end - span.start) / 80), span))
    scored.sort(key=lambda item: -item[0])
    return [span for _, span in scored]


def _render(lines: list[str], chosen: list[tuple[int, int]]) -> str:
    merged: list[list[int]] = []
    for start, end in sorted(chosen):
        if merged and start <= merged[-1][1] + 2:  # 두 줄 이하 틈은 그냥 붙인다
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            parts.append(GAP.format(start=cursor + 1, end=start))
        parts.extend(lines[start:end])
        cursor = end
    if cursor < len(lines):
        parts.append(GAP.format(start=cursor + 1, end=len(lines)))
    return "\n".join(parts)


def excerpt(relative_path: str, text: str, hints: str, limit: int) -> str:
    """text 를 limit 자 안의 발췌로 줄인다. 넘치는 범위는 건너뛰고 덜 중요한 다음 범위를 넣는다."""
    lines = text.splitlines()
    chosen: list[tuple[int, int]] = []
    for start, end in _ranges(relative_path, lines, text, hints):
        if any(s <= start and end <= e for s, e in chosen):
            continue
        if len(_render(lines, chosen + [(start, end)])) <= limit:
            chosen.append((start, end))
    return _render(lines, chosen)


def fit_files(originals: dict[str, str], room: int, hints: str) -> tuple[str, list[str]] | None:
    """파일들을 room 자 안에 넣는다 — 작은 파일은 통째로, 큰 파일은 발췌로. (본문, 발췌한 파일 목록), 너무 좁으면 None."""
    pieces: dict[str, str] = {}
    excerpted: list[str] = []
    left = room
    order = sorted(originals, key=lambda path: len(originals[path]))
    for index, path in enumerate(order):
        text = originals[path]
        wrapper = len(f"\nFILE: {path}\n---\n\n---\n")
        share = left // (len(order) - index) - wrapper
        if len(text) > share:
            note = EXCERPT_NOTE.format(total=len(text.splitlines()))
            if share - len(note) < MIN_SHARE:
                return None
            text = note + excerpt(path, text, hints, share - len(note))
            excerpted.append(path)
        pieces[path] = f"\nFILE: {path}\n---\n{text}\n---\n"
        left -= len(pieces[path])
    return "".join(pieces[path] for path in originals), excerpted
