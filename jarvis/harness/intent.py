"""대화에서 말로 코딩 세션을 쓴다(2026-10-23, 사용자 "자연어로 할 수 있도록 해야지").

- 코딩 세션으로 보내는 말: '코딩 세션' 을 직접 말하거나, **이 프로젝트에 실제로 있는 파일**을 집어 고치기 말(고쳐·수정·추가·바꿔·
  지워 …)을 할 때. 파일을 안 집은 코드 질문('정렬 코드 짜줘'·'이 함수 설명해줘')은 지금처럼 대화가 답한다(코드는 답으로).
- 허락을 기다리는 동안의 짧은 답: '응·네·진행' = 한 번 허락, '항상·계속 허락' = 이 세션 동안 허락, '아니·거절·하지 마' = 거절.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

EXPLICIT = re.compile(r"코딩\s*세션")
EDIT = re.compile(r"고쳐|고치|수정|추가해|추가하|바꿔|바꾸|변경해|변경하|리팩터|구현해|구현하|만들어|지워|삭제해|업데이트해|넣어|"
                  r"빼\s*줘|붙여|정리해|최적화해|버그\s*잡|테스트\s*(짜|만들|추가)")
FILE = re.compile(r"[A-Za-z0-9_.\-/\\]+\.(?:py|sv|svh|v|vh|vhd|js|ts|tsx|jsx|html|css|json|md|txt|toml|ini|cfg|yaml|yml|c|cc|cpp|h|hpp|"
                  r"rs|go|java|kt|sh|ps1|bat|xdc|sdc|tcl)(?![A-Za-z0-9_])", re.I)  # 뒤에 한글 조사(…sv에)가 붙어도
SKIP = {".git", "__pycache__", "node_modules", "logs", "data", "runtime", "tools", "venv", ".venv", "dist", "build", "backups"}
YES = re.compile(r"^\s*(응|네|예|ㅇㅇ|ㅇㅋ|오케이|ok|okay|yes|좋아|그래|진행|허락|해\s*줘|고고)\b", re.I)
ALWAYS = re.compile(r"항상|계속\s*허락|전부\s*허락|다\s*허락|모두\s*허락")
REVERT = re.compile(r"되돌려|원래대로|롤백")
# 짧은 '노·no' 는 낱말일 때만 — '노트에 …'·'no-op …' 같은 말을 거절로 읽지 않게(2026-10-26)
NO = re.compile(r"^\s*(아니|아뇨|거절|하지\s*마|취소|멈춰|중단|(?:노+|no|nope)(?![\w-]))", re.I)


def mentioned_files(text: str, root: Path, limit: int = 20000) -> list[str]:
    """글에 나온 파일 이름 중 작업 폴더에 실제로 있는 것(상대 경로 그대로 또는 이름이 같은 파일)."""
    names = {m.group(0).replace("\\", "/").strip("./") for m in FILE.finditer(text)}
    if not names:
        return []
    root = Path(root)
    found = [name for name in names if (root / name).is_file()]
    wanted = {Path(name).name.lower() for name in names if name not in found}
    seen = 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        for file in files:
            seen += 1
            if file.lower() in wanted:
                found.append((Path(directory) / file).relative_to(root).as_posix())
                wanted.discard(file.lower())
        if not wanted or seen > limit:
            break
    return sorted(found)


def is_coding_request(text: str, root: Path | None) -> bool:
    text = str(text or "").strip()
    if not text or text.startswith("/") or root is None:
        return False
    if EXPLICIT.search(text):
        return True
    return bool(EDIT.search(text)) and bool(mentioned_files(text, root))


def permission_reply(text: str) -> str:
    """허락을 기다릴 때의 짧은 답 → once·always·reject, 알 수 없으면 ''."""
    text = str(text or "").strip()
    if NO.search(text):
        return "reject"
    if ALWAYS.search(text):
        return "always"
    if YES.search(text) and len(text) <= 30:
        return "once"
    return ""
