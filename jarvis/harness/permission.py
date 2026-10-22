"""권한 규칙 — OpenCode v1.18.34 permission/evaluate 와 같은 방식(MIT): (권한, 대상 모양, 동작) 규칙 목록에서 **마지막으로 맞는 것**이
이긴다. 맞는 게 없으면 'ask'. 동작: allow(그냥) · ask(사람에게 묻기 — 물을 사람이 없으면 거절) · deny(거절).

권한 이름: read·glob·grep·edit(write·edit·apply_patch)·bash·webfetch·websearch·task·question·skill·mcp__서버__도구.
ALEX 권한 단계(ADR-095)와 잇기: tier_rules('read'|'ask'|'auto'). 일꾼(/dev-auto·/build 의 복사본)은 worker_rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch

ALLOW, ASK, DENY = "allow", "ask", "deny"


@dataclass(frozen=True)
class Rule:
    permission: str
    pattern: str = "*"
    action: str = ALLOW


def evaluate(permission: str, pattern: str, rules: list[Rule]) -> str:
    for rule in reversed(rules):
        if fnmatch.fnmatchcase(permission, rule.permission) and fnmatch.fnmatchcase(pattern, rule.pattern):
            return rule.action
    return ASK


def shell_rules(allowed: tuple[str, ...], otherwise: str = DENY) -> list[Rule]:
    return [Rule("bash", "*", otherwise), *(Rule("bash", pattern, ALLOW) for pattern in allowed)]


DEFAULT_SHELL = ("python -m unittest", "python -m unittest *", "python -m py_compile *")


def worker_rules(allowed: tuple[str, ...] = DEFAULT_SHELL) -> list[Rule]:
    """사람이 없는 일꾼: 복사본 안 파일은 자유, 명령은 시험 실행만, 인터넷·외부 도구·질문은 없음."""
    return [Rule("*", "*", ALLOW), *shell_rules(allowed), Rule("webfetch", "*", DENY), Rule("websearch", "*", DENY),
            Rule("mcp__*", "*", DENY), Rule("question", "*", DENY)]


def tier_rules(tier: str, allowed: tuple[str, ...] = DEFAULT_SHELL) -> list[Rule]:
    """ALEX 대화의 권한 단계 → 규칙. read: 읽기·찾기만 / ask: 바꾸기·실행은 묻기 / auto: 바꾸기는 그냥, 허락 목록 밖 명령은 묻기."""
    base = [Rule("*", "*", ALLOW), Rule("webfetch", "*", ASK), Rule("websearch", "*", ALLOW), Rule("mcp__*", "*", ASK)]
    if tier == "read":
        return base + [Rule("edit", "*", DENY), Rule("bash", "*", DENY), Rule("mcp__*", "*", DENY)]
    if tier == "auto":
        return base + [Rule("edit", "*", ALLOW), *shell_rules(allowed, otherwise=ASK)]
    return base + [Rule("edit", "*", ASK), Rule("bash", "*", ASK)]


# 하위 에이전트·모드별 덧붙임(OpenCode agent.ts 와 같은 뜻)
EXPLORE = [Rule("*", "*", DENY), Rule("read", "*", ALLOW), Rule("glob", "*", ALLOW), Rule("grep", "*", ALLOW),
           Rule("webfetch", "*", ALLOW), Rule("websearch", "*", ALLOW)]
GENERAL = [Rule("todowrite", "*", DENY), Rule("task", "*", DENY), Rule("question", "*", DENY)]
PLAN = [Rule("edit", "*", DENY), Rule("bash", "*", DENY), Rule("task", "general", DENY)]


def allowed_commands(rules: list[Rule]) -> list[str]:
    """설명서에 적을 '그냥 되는' 명령 모양."""
    return [r.pattern for r in rules if r.permission == "bash" and r.action == ALLOW and r.pattern != "*"]
