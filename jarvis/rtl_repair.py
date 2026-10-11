"""Focused RTL repair: immutable source anchors and reusable review checks.

This changes the inference workflow, not model weights. Checks are hypotheses;
only the project's trusted runner decides whether a proposed repair works.
"""
from __future__ import annotations

import json
import re

from .providers import Message

PATCH_BLOCK = re.compile(r"```json[ \t]*\n(.*?)```", re.S | re.I)
LEXEMES = re.compile(
    r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'
    r"[A-Za-z_$][A-Za-z0-9_$]*|[0-9]+(?:'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+)?|"
    r">>>|<<<|===|!==|==|!=|<=|>=|&&|\|\||<<|>>|\*\*|~\^|\^~|[^\s]",
    re.S,
)

REPAIR_RULES = (
    "너는 RTL 디버거다. 실패 관측을 코드의 원인과 연결하고 최소 수정만 한다. "
    "현재 코드의 포트와 이미 통과한 기능을 보존한다. 시험기·규격·다른 파일은 바꾸지 않는다. "
    "답은 ```json 블록 하나에 {\"diagnosis\":\"관측→원인→수정 근거 1~3문장\","
    "\"edits\":[{\"search\":\"현재 코드에서 그대로 복사한 부분\",\"replace\":\"고친 부분\"}]} 형식이다. "
    "search는 주석·공백·줄바꿈까지 정확히 일치하고 현재 코드에서 한 번만 나와야 한다. "
    "여러 수정은 서로 겹치지 않아야 하며 모두 같은 현재 코드 기준이다. "
    "전체 파일을 다시 출력하거나 코드 밖 명령을 쓰지 않는다. JSON 문자열의 줄바꿈은 \\n으로 이스케이프한다. "
    "테스트 이름이나 예제 상수를 하드코딩하지 말고 일반적인 규격을 구현한다."
)


def code_tokens(code: str) -> tuple[str, ...]:
    """Ignore comments/formatting, but retain strings, identifiers and operators."""
    return tuple(match.group(0) for match in LEXEMES.finditer(code)
                 if not match.group(0).startswith(("//", "/*")))


def apply_repair(answer: str, code: str) -> str | None:
    """None means no patch format; invalid patches fail without changing a file."""
    blocks = PATCH_BLOCK.findall(answer)
    stripped = answer.strip()
    if not blocks and not stripped.startswith("{"):
        return None
    if len(blocks) > 1:
        raise ValueError("JSON 수정 블록은 하나만 허용한다.")
    try:
        data = json.loads(blocks[0] if blocks else stripped)
    except ValueError as exc:
        raise ValueError("수정 JSON을 읽지 못했다. 줄바꿈과 따옴표를 이스케이프해 다시 줘.") from exc
    if (not isinstance(data, dict) or set(data) != {"diagnosis", "edits"}
            or not isinstance(data["diagnosis"], str) or not data["diagnosis"].strip()
            or not isinstance(data["edits"], list) or not 1 <= len(data["edits"]) <= 16):
        raise ValueError("수정 JSON은 diagnosis와 1~16개의 edits를 포함해야 한다.")
    replacements = []
    for edit in data["edits"]:
        if (not isinstance(edit, dict) or set(edit) != {"search", "replace"}
                or not isinstance(edit["search"], str) or not edit["search"]
                or not isinstance(edit["replace"], str)):
            raise ValueError("각 수정은 비어 있지 않은 search와 문자열 replace만 포함해야 한다.")
        start = code.find(edit["search"])
        if start < 0 or code.find(edit["search"], start + 1) >= 0:
            raise ValueError("search가 현재 코드에 정확히 한 번 존재하지 않는다. 현재 코드에서 다시 복사해 줘.")
        replacements.append((start, start + len(edit["search"]), edit["replace"]))
    replacements.sort()
    for left, right in zip(replacements, replacements[1:]):
        if left[1] > right[0]:
            raise ValueError("수정 범위가 겹친다. 서로 겹치지 않는 원본 부분으로 다시 줘.")
    for start, end, replacement in reversed(replacements):
        code = code[:start] + replacement + code[end:]
    return code.rstrip() + "\n"


def repair_messages(spec: str, code: str, feedback: str) -> list[Message]:
    """Compile observations plus relevant general HDL rules, without golden code."""
    checks = [
        "최초 불일치 명령/입력에서 실제로 어떤 상태가 바뀌었는지부터 확인한다. "
        "뒤에 나타나는 오류는 앞선 잘못된 상태의 결과일 수 있다.",
    ]
    plain = " ".join(code_tokens(code))
    focus = []
    if ">>>" in plain:
        checks.append(
            "산술 오른쪽 시프트: >>>가 있어도 피연산자가 unsigned면 0으로 채운다. "
            "삼항식에서 한쪽이 unsigned이면 $signed(a) >>> n 쪽도 unsigned 문맥을 받을 수 있다. "
            "signed 중간 신호에서 시프트를 먼저 계산하거나 if/case에서 두 결과를 별도로 대입한다. "
            "산술/논리 시프트 선택 디코드도 각각 확인한다."
        )
        focus.extend(i for i, line in enumerate(code.splitlines()) if ">>>" in line or "is_sra" in line)
    if re.search(r"\bRV32I\b", spec, re.I) and re.search(r"\b(SB|SH|SW|BEQ|BNE|BLT|BGE)\b", feedback):
        checks.append(
            "RV32I 상태 쓰기: STORE와 BRANCH의 inst[11:7]은 rd가 아니다. "
            "wb_data의 default=0과 rd!=0 검사만으로는 레지스터 쓰기를 차단하지 못한다. "
            "실제로 rd를 쓰는 명령의 디코드에서만 쓰기 허용을 켜는지 확인한다."
        )
        focus.extend(i for i, line in enumerate(code.splitlines())
                     if re.search(r"\brf\[.*?\]\s*<=|\bif\s*\(.*?\brd\b|\bwb_data\b", line))
    if re.search(r"\bFIFO\b", spec, re.I):
        checks.append(
            "FIFO: 상승엣지 전 full/empty로 실제 수락한 read/write를 결정하고, "
            "동시 수락 때 count는 유지한다. 같은 always에서 count에 두 번 nonblocking 대입하면 "
            "뒤의 대입만 남는다. 깊이가 2의 거듭제곱이 아니면 포인터 비트 overflow만으로는 "
            "올바른 wrap이 되지 않는다. full/empty에서 동시 요청의 허용 정책은 반드시 규격을 따른다."
        )
        focus.extend(i for i, line in enumerate(code.splitlines())
                     if re.search(r"\b(count|head|tail|push|pop|full|empty)\b", line))
    if re.search(r"register file|레지스터\s*파일", spec, re.I):
        checks.append(
            "레지스터 파일: reset과 write가 같은 엣지에서 활성화될 때 규격의 우선순위를 지킨다. "
            "reset 블록 뒤 독립 if(write)가 있으면 같은 레지스터의 reset 값을 덮어쓸 수 있다. "
            "규격이 지정한 읽기 전용 주소에는 쓰기를 금지하고 양쪽 read port도 동일한 계약을 지킨다. "
            "write enable이 없을 때 저장 상태는 유지한다."
        )
        focus.extend(i for i, line in enumerate(code.splitlines())
                     if re.search(r"\b(rst|reset|we|regs|qa|qb)\b", line))
    lines = code.splitlines()
    indices = sorted({j for i in focus for j in range(max(0, i - 1), min(len(lines), i + 2))})[:42]
    evidence = "\n".join(f"L{i + 1}: {lines[i]}" for i in indices)
    request = (f"[설계 규격]\n{spec}\n\n[지금 코드]\n```verilog\n{code}```"
               f"\n\n[시험 결과]\n{feedback}\n\n[관련 검토 규칙: 원인 가설이며 시험으로 확인]\n"
               + "\n".join(f"- {check}" for check in checks))
    if evidence:
        request += "\n\n[조사할 코드 위치: 줄 번호는 설명용, search에는 넣지 않음]\n" + evidence
    return [Message("system", REPAIR_RULES), Message("user", request)]
