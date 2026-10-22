"""명령(OpenCode command/index.ts·config/command.ts·session/prompt.ts 의 command 와 같은 방식, MIT) — '/이름 인자' 로 정해 둔 지시문을 보낸다.

- 기본 명령: init(AGENTS.md 만들기·다듬기 — initialize.txt), review(바뀐 내용 검토 — review.txt, 하위 에이전트로).
- 사용자 명령: 작업 폴더의 .alex/commands·.opencode/command·.opencode/commands 아래 *.md(머리에 description·agent·subtask,
  본문이 지시문). 하위 폴더는 'a/b' 이름.
- 인자: $1·$2 … (마지막 번호는 남은 인자 전부), $ARGUMENTS(인자 전체). 자리표시가 없고 인자가 있으면 끝에 붙인다.
  !`명령` 은 그 명령의 출력으로 — ALEX 는 셸 없이 하네스의 bash 도구로 돌린다(권한 규칙대로, 막히면 그 거절 글).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

from . import opencode_text as oc

COMMAND_DIRS = (".alex/commands", ".opencode/command", ".opencode/commands")
PLACEHOLDER = re.compile(r"\$(\d+)")
SHELL = re.compile(r"!`([^`]+)`")
ARGS = re.compile(r"""(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)""", re.I)


@dataclass
class Command:
    name: str
    description: str
    template: str
    agent: str = ""          # build·plan·general·explore — 비우면 지금 모드
    subtask: bool = False    # 하위 에이전트로 돌리고 결과만 대화에
    source: str = "command"


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    head = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not head:
        return {}, text
    meta = {k: v.strip().strip("\"'") for k, v in re.findall(r"^(\w+):\s*(.*?)\s*$", head.group(1), re.M)}
    return meta, text[head.end():]


def find_commands(root: Path) -> dict[str, Command]:
    commands = {
        "init": Command("init", "guided AGENTS.md setup", oc.INITIALIZE.replace("${path}", str(root))),
        "review": Command("review", "review changes [commit|branch|pr], defaults to uncommitted",
                          oc.REVIEW.replace("${path}", str(root)), subtask=True),
    }
    for folder in COMMAND_DIRS:
        base = Path(root) / folder
        if not base.is_dir():
            continue
        for file in sorted(base.rglob("*.md")):
            meta, body = _frontmatter(file.read_text(encoding="utf-8", errors="replace"))
            name = file.relative_to(base).with_suffix("").as_posix()
            commands[name] = Command(name, meta.get("description", ""), body.strip(), meta.get("agent", ""),
                                     meta.get("subtask", "").lower() == "true", "file")
    return commands


def expand(template: str, arguments: str, run_shell: Callable[[str], str] | None = None) -> str:
    args = [a.strip("\"'") for a in ARGS.findall(arguments or "")]
    numbers = [int(n) for n in PLACEHOLDER.findall(template)]
    last = max(numbers, default=0)

    def place(match: re.Match) -> str:
        position = int(match.group(1))
        if position - 1 >= len(args):
            return ""
        return " ".join(args[position - 1:]) if position == last else args[position - 1]

    text = PLACEHOLDER.sub(place, template)
    uses_all = "$ARGUMENTS" in template
    text = text.replace("$ARGUMENTS", arguments or "")
    if not numbers and not uses_all and (arguments or "").strip():
        text += "\n\n" + arguments
    if run_shell is not None:
        text = SHELL.sub(lambda m: run_shell(m.group(1)), text)
    return text.strip()
