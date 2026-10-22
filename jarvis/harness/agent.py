"""ALEX 코딩 하네스의 반복(2026-10-22, ADR-112) — OpenCode v1.18.34 session/prompt.ts·processor.ts·tool/task.ts 의 방식을 옮긴
것(MIT, OPENCODE_LICENSE.txt).

한 차례: 모델에게 지금까지의 대화와 도구 설명서를 주고 → 답 글과 도구 호출을 받고 → 도구를 실행해 결과를 tool 메시지로 붙인다.
도구 호출이 없으면 끝(그 글이 마지막 답). OpenCode 와 같은 장치:
- 모드: build(기본) · plan(읽기만 하며 계획 — plan.txt 알림, 바꾸기·명령 거절) · 하위 에이전트 general·explore(task 도구로 띄움,
  깊이 1, 같은 폴더·새 대화, 결과는 <task …><task_result> 로). task_id 로 같은 하위 에이전트를 이어 쓴다.
- question: 사람에게 묻고 멈춘다(stopped='question'). answer() 로 답을 받아 이어 간다.
- 같은 도구·같은 인자를 연달아 3번 → 되풀이 경고. 경고 뒤에도 또 하면 멈춘다.
- 단계 상한·시간 상한: 마지막 차례는 도구 없이 'MAXIMUM STEPS REACHED' 로 정리 답.
- 문맥이 차면: 오래된 도구 결과 비우기(prune) → 그래도 넘치면 앞부분 요약(compaction).
- 생각이 길어 답 자리가 모자라면 그 차례만 생각 없이. 모델 연결 오류는 2·4·8초 쉬고 다시(OpenCode retry 와 같은 배수, 3번).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import secrets
import threading
import time
from typing import Callable

from ..providers import ContextExhausted, GenerationCancelled, Message, ProviderError, ToolsUnsupported
from . import opencode_text as oc
from . import permission as perm
from . import prompt as prompts
from .tools import Workspace

DOOM_LOOP = 3
PRUNE_KEEP_CHARS = 120_000      # 최근 도구 결과는 이만큼 그대로 둔다(OpenCode prune 은 약 4만 토큰)
CLEARED = "[Old tool result content cleared]"
RETRIES = 3
LABEL = {"read": "읽기", "write": "쓰기", "edit": "고치기", "apply_patch": "패치", "glob": "찾기", "grep": "찾기",
         "bash": "실행", "todowrite": "할 일", "task": "하위 에이전트", "question": "질문", "skill": "스킬",
         "webfetch": "웹 읽기", "websearch": "웹 검색"}


@dataclass
class AgentResult:
    text: str
    stopped: str                    # done | max_steps | doom_loop | cancelled | error | question
    steps: int = 0
    seconds: float = 0.0
    tools: dict[str, int] = field(default_factory=dict)
    compactions: int = 0
    error: str = ""
    trace: list[dict] = field(default_factory=list)   # 차례마다 시간·도구·토큰(느린 원인 찾기용)
    questions: list[dict] = field(default_factory=list)   # stopped='question' 일 때 사람에게 물을 것
    todos: list[dict] = field(default_factory=list)


def _arguments(call: dict) -> tuple[str, dict | None, str]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or call.get("name") or "")
    raw = function.get("arguments", call.get("arguments", {}))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            return name, None, f"Error: the tool arguments are not valid JSON ({exc}). Send the arguments again as JSON."
    return name, raw if isinstance(raw, dict) else {}, ""


class CodingAgent:
    def __init__(self, provider, workspace: Workspace, *, max_steps: int = 60, context_chars: int = 300_000,
                 rules: str = "", lessons: str = "", progress: Callable[[str], None] | None = None,
                 cancel_event: threading.Event | None = None, deadline: float | None = None, mode: str = "build",
                 depth: int = 0, max_depth: int = 1, sleep: Callable[[float], None] = time.sleep) -> None:
        self.provider = provider
        self.workspace = workspace
        self.max_steps = max_steps
        self.context_chars = context_chars
        self.rules, self.lessons = rules, lessons
        self.say = progress or (lambda _text: None)
        self.cancel_event = cancel_event or workspace.cancel_event
        self.deadline = deadline                     # time.monotonic() 기준 — 넘으면 정리 답을 받고 끝
        self.mode, self.depth, self.max_depth = mode, depth, max_depth
        self.sleep = sleep
        self.messages: list[Message] = []
        self.compactions = 0
        self.trace: list[dict] = []
        self.subagents: dict[str, "CodingAgent"] = {}   # task_id → 하위 에이전트(이어 쓰기)
        self.step = 0
        self.started = 0.0
        self._pending_question: list[dict] | None = None

    # ── 도구 목록
    def _tool_names(self) -> list[str]:
        names = list(prompts.MODE_TOOLS.get(self.mode, prompts.MODE_TOOLS["build"]))
        ws = self.workspace
        if self.depth >= self.max_depth:
            names = [n for n in names if n != "task"]
        if not ws.skills:
            names = [n for n in names if n != "skill"]
        if ws.fetch is None:
            names = [n for n in names if n != "webfetch"]
        if ws.search is None:
            names = [n for n in names if n != "websearch"]
        # 통째로 막힌 도구(그 권한의 모든 대상이 deny)는 설명서에서 뺀다. 파일·명령 도구는 대상마다 다르니 둔다.
        always = {"read", "glob", "grep", "edit", "write", "apply_patch", "bash"}
        return [n for n in names if n in always or perm.evaluate(n, "*", ws.rules or []) != perm.DENY]

    def _specs(self) -> list[dict]:
        ws = self.workspace
        extra = ws.mcp.specs() if ws.mcp is not None and self.mode in ("build", "general") else []
        return prompts.tool_specs(self._tool_names(), allowed=perm.allowed_commands(ws.rules or []),
                                  timeout=ws.shell.timeout, extra=extra)

    # ── 문맥 관리
    def _size(self) -> int:
        return sum(len(m.content) + len(json.dumps(m.tool_calls or [], ensure_ascii=False)) for m in self.messages)

    def _prune(self) -> None:
        kept = 0
        for message in reversed(self.messages):
            if message.role != "tool" or message.content == CLEARED:
                continue
            kept += len(message.content)
            if kept > PRUNE_KEEP_CHARS:
                message.content = CLEARED

    def _compact(self) -> None:
        """앞부분 요약 — 지시문·처음 요청은 두고, 가운데를 요약 한 덩어리로, 최근 6개는 그대로."""
        head, middle, tail = self.messages[:2], self.messages[2:-6], self.messages[-6:]
        while tail and tail[0].role == "tool":  # tool 결과가 짝 잃지 않게
            middle.append(tail.pop(0))
        if not middle:
            return
        transcript = []
        for m in middle:
            if m.tool_calls:
                transcript.append("[Assistant tool calls]: " + json.dumps(m.tool_calls, ensure_ascii=False)[:2000])
            if m.content:
                transcript.append(f"[{m.role}]: {m.content[:4000]}")
        self.say("[문맥이 차서 앞부분을 요약합니다]\n")
        request = [Message("system", oc.COMPACTION),
                   Message("user", "Conversation:\n" + "\n".join(transcript)[:200_000] + "\n\n" + prompts.COMPACTION_REQUEST)]
        summary, _calls = self._call(None, request)
        self.messages = head + [Message("user", "<system-reminder>Summary of the earlier part of this session:\n"
                                        + summary.strip() + "</system-reminder>")] + tail
        self.compactions += 1

    # ── 한 차례(생각 끄고 다시·연결 오류 재시도)
    def _call(self, tools: list | None, messages: list[Message] | None = None) -> tuple[str, list]:
        conversation = messages if messages is not None else self.messages
        attempt = 0
        while True:
            attempt += 1
            try:
                try:
                    return self.provider.chat_step(conversation, tools)
                except ContextExhausted:
                    if not getattr(self.provider, "think", False):
                        raise
                    self.provider.think = False  # 그 차례만 생각 없이(ALEX _coder_model 과 같은 방식)
                    try:
                        return self.provider.chat_step(conversation, tools)
                    finally:
                        self.provider.think = True
            except (ContextExhausted, ToolsUnsupported, GenerationCancelled):
                raise
            except ProviderError as exc:
                if attempt > RETRIES or self._cancelled():
                    raise
                wait = min(2 * 2 ** (attempt - 1), 30)
                self.say(f"[모델 연결 오류 — {wait}초 뒤 다시({attempt}/{RETRIES}): {str(exc)[:120]}]\n")
                self.sleep(wait)

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    # ── 하위 에이전트(task)
    def _task(self, args: dict) -> str:
        kind = str(args.get("subagent_type") or "")
        if kind not in prompts.SUBAGENTS:
            return f"Error: Unknown agent type: {kind} is not a valid agent type"
        if self.depth >= self.max_depth:
            return f"Error: Subagent depth limit reached ({self.max_depth})."
        if problem := self.workspace.permit("task", kind):
            return problem
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "Error: prompt is required"
        task_id = str(args.get("task_id") or "")
        child = self.subagents.get(task_id) if task_id else None
        if child is None:
            task_id = "task_" + secrets.token_hex(6)
            extra = perm.EXPLORE if kind == "explore" else perm.GENERAL
            child = CodingAgent(self.provider, self.workspace.child(extra), max_steps=min(self.max_steps, 40),
                                context_chars=self.context_chars, rules=self.rules, lessons=self.lessons,
                                progress=lambda text: self.say("  " + text), cancel_event=self.cancel_event,
                                deadline=self.deadline, mode=kind, depth=self.depth + 1, max_depth=self.max_depth,
                                sleep=self.sleep)
            self.subagents[task_id] = child
            result = child.run(prompt)
        else:
            result = child.answer(prompt)
        state = "error" if result.stopped in ("error", "cancelled") else "completed"
        tag = "task_error" if state == "error" else "task_result"
        text = result.text or result.error or "(no output)"
        return "\n".join([f'<task id="{task_id}" state="{state}">', f"<summary>{args.get('description', '')}</summary>",
                          f"<{tag}>", text, f"</{tag}>", "</task>"])

    # ── 시작·이어 가기
    def start(self) -> None:
        """대화 시작(지시문만) — run() 이 부르고, 첫 요청이 하위 에이전트 명령일 때도 부른다."""
        ws = self.workspace
        self._base_rules = list(ws.rules or [])
        if self.mode == "plan":  # 읽기만 하며 계획 — 바꾸기·명령·general 하위 에이전트 거절
            ws.rules = [*self._base_rules, *perm.PLAN]
        self.messages = [Message("system", prompts.system_prompt(ws.root, rules=self.rules, lessons=self.lessons,
                                                                 mode=self.mode, skills=ws.skills))]
        self.started = time.monotonic()
        self.step = 0

    def run(self, task: str) -> AgentResult:
        self.start()
        self.messages.append(Message("user", task + ("\n\n" + oc.PLAN_REMINDER if self.mode == "plan" else "")))
        return self._loop()

    def run_subtask(self, kind: str, description: str, prompt: str, shown: str) -> AgentResult:
        """명령의 subtask(OpenCode SubtaskPart) — 하위 에이전트에게 맡기고, 요청(shown)과 결과를 이 대화에 남긴다."""
        if not self.messages:
            self.start()
        began = time.monotonic()
        output = self._task({"subagent_type": kind, "description": description, "prompt": prompt})
        self.messages += [Message("user", shown), Message("assistant", output)]
        failed = output.startswith("Error:") or 'state="error"' in output.splitlines()[0]
        stopped = "cancelled" if self._cancelled() else "error" if failed else "done"
        return AgentResult(text=output, stopped=stopped, steps=self.step, seconds=round(time.monotonic() - began, 1),
                           tools=dict(self.workspace.counts), compactions=self.compactions, trace=list(self.trace),
                           todos=list(self.workspace.todos))

    def answer(self, reply: str) -> AgentResult:
        """질문(question)에 대한 사람의 답, 또는 이어서 할 말 — 같은 대화로 이어 간다."""
        if self._pending_question is not None:
            self.messages.append(Message("tool", f"User has answered your questions: {reply}", tool_name="question"))
            self._pending_question = None
        else:
            self.messages.append(Message("user", reply))
        if not self.started:
            self.started = time.monotonic()
        return self._loop()

    def switch_to_build(self, plan_text: str = "") -> AgentResult:
        """계획 모드 → 실행(OpenCode build-switch.txt)."""
        self.mode = "build"
        self.workspace.rules = list(getattr(self, "_base_rules", self.workspace.rules or []))
        self.messages.append(Message("user", oc.BUILD_SWITCH + ("\n\nExecute this plan:\n" + plan_text if plan_text else "")))
        return self._loop()

    def switch_to_plan(self, request: str) -> AgentResult:
        """실행 → 계획 모드(같은 대화에 plan.txt 알림을 붙이고 바꾸기·명령을 막는다)."""
        self.mode = "plan"
        self._base_rules = list(getattr(self, "_base_rules", self.workspace.rules or []))
        self.workspace.rules = [*self._base_rules, *perm.PLAN]
        self.messages.append(Message("user", request + "\n\n" + oc.PLAN_REMINDER))
        return self._loop()

    def _loop(self) -> AgentResult:
        ws = self.workspace
        recent: list[str] = []
        warned = False
        text, stopped, error, questions = "", "done", "", []
        steps_here = 0
        try:
            while True:
                self.step += 1
                steps_here += 1
                ws.begin_step(self.step)
                if self._cancelled():
                    raise GenerationCancelled("코딩 작업을 중단했습니다.")
                if self._size() > self.context_chars:
                    self._prune()
                    if self._size() > self.context_chars:
                        self._compact()
                last = steps_here >= self.max_steps or (self.deadline is not None and time.monotonic() > self.deadline)
                if last:
                    self.messages.append(Message("user", prompts.MAX_STEPS_PROMPT))
                began = time.monotonic()
                specs = None if last else self._specs()
                offered = {spec["function"]["name"] for spec in specs or []}
                content, calls = self._call(specs)
                metrics = getattr(self.provider, "last_metrics", None) or {}
                self.trace.append({"step": self.step, "seconds": round(time.monotonic() - began, 1),
                                   "tools": [(_arguments(c)[0]) for c in calls],
                                   "out_tokens": metrics.get("output_tokens"), "first": metrics.get("first_token_seconds"),
                                   "prompt_tokens": metrics.get("prompt_tokens"), "text_chars": len(content)})
                if not calls or last:
                    text, stopped = content.strip(), ("max_steps" if last else "done")
                    self.messages.append(Message("assistant", content))
                    break
                self.messages.append(Message("assistant", content, tool_calls=calls))
                asked = None
                for call in calls:
                    name, args, problem = _arguments(call)
                    signature = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                    recent = (recent + [signature])[-DOOM_LOOP:]
                    if problem:
                        result = problem
                    elif name not in offered:  # 이 에이전트에게 주지 않은 도구(OpenCode 의 invalid tool 과 같은 뜻)
                        result = (f"Error: tool '{name}' is not available for this agent. Available tools: "
                                  + ", ".join(sorted(offered)))
                    elif name == "task":
                        self.say(f"[{LABEL['task']} {args.get('subagent_type', '')}: {str(args.get('description', ''))[:80]}]\n")
                        result = self._task(args)
                    elif name == "question":
                        if denied := ws.permit("question", "*"):
                            result = denied
                        else:
                            asked = [q for q in (args.get("questions") or []) if isinstance(q, dict)]
                            self._pending_question = asked
                            continue  # 답은 answer() 로
                    else:
                        target = (args.get("filePath") or args.get("pattern") or args.get("command") or args.get("url")
                                  or args.get("query") or args.get("name") or "")
                        self.say(f"[{LABEL.get(name, name)} {str(target)[:120]}]\n")
                        result = ws.run(name, args)
                    self.messages.append(Message("tool", result, tool_name=name))
                if asked is not None:
                    stopped, questions = "question", asked
                    text = "\n".join(str(q.get("question", "")) for q in asked)
                    break
                if len(recent) == DOOM_LOOP and len(set(recent)) == 1:
                    if warned:
                        stopped, text = "doom_loop", "같은 도구 호출을 되풀이해 멈췄습니다."
                        break
                    warned = True
                    self.messages.append(Message("user", prompts.DOOM_LOOP_REMINDER))
                    recent = []
        except GenerationCancelled:
            stopped, text = "cancelled", "코딩 작업을 중단했습니다."
        except ToolsUnsupported:
            stopped, error = "error", "이 모델은 도구 호출을 지원하지 않습니다."
        except ProviderError as exc:
            stopped, error = "error", str(exc)
        return AgentResult(text=text, stopped=stopped, steps=self.step, seconds=round(time.monotonic() - self.started, 1),
                           tools=dict(ws.counts), compactions=self.compactions, error=error, trace=list(self.trace),
                           questions=questions, todos=list(ws.todos))
