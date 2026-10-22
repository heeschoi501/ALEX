"""코딩 세션(서버 API 의 몸통, ADR-112) — OpenCode 서버의 session·prompt_async·event·permission·question·abort·diff·revert·
todo 와 같은 일을 ALEX 안에서 한다. 웹(web.py)은 이걸 HTTP 로 내보내기만 한다.

- 세션 = CodingAgent 하나 + Workspace 하나(작업 폴더에서 바로 — 바꾸기는 권한 단계대로, 차례마다 되돌릴 수 있게 남긴다).
- prompt(): 뒤 스레드에서 돈다. 모델을 부를 때마다 model_turn(세션) 으로 차례를 잡는다(웹이 대화와 같은 대기열을 준다) —
  도구를 실행하거나 사람의 허락을 기다리는 동안엔 모델을 다른 대화에 내준다.
- 권한 'ask' → 사건(permission.asked)을 내고 사람이 답할 때까지(최대 10분) 기다린다. once·always·reject.
  always 는 그 세션에 '그 모양 허락' 규칙을 덧붙인다(OpenCode 와 같은 뜻).
- question → 사건(question.asked)을 내고 멈춘다. answer() 로 이어 간다.
- 사건(event)은 번호가 붙은 목록 — events(since) 로 그 뒤 것만 받는다(화면은 SSE 로 오래 붙어 받는다).
- 세션 대화는 store 폴더에 JSON 으로 남아 앱을 다시 켜도 이어 간다. 되돌리기 기록(차례마다 바꾸기 전 내용)도 같이 —
  내용은 '<세션>.blobs/<sha256>' 에 한 번씩만(같은 내용은 같은 파일).
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
import hashlib
import json
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, ContextManager

from ..providers import Message
from . import commands as cmds
from . import permission as perm
from .agent import AgentResult, CodingAgent
from .tools import ShellPolicy, Workspace

PERMISSION_WAIT = 600.0
KEEP_EVENTS = 2000
DELTA_CHARS, DELTA_SECONDS = 120, 0.3   # 모델 글 조각을 이만큼 모아 한 사건으로


@dataclass
class PendingPermission:
    id: str
    permission: str
    pattern: str
    detail: dict
    answered: threading.Event = field(default_factory=threading.Event)
    response: str = ""            # once | always | reject


@dataclass
class CodeSession:
    id: str
    owner: str
    root: Path
    tier: str
    title: str = ""
    status: str = "idle"          # idle | running | question | error
    created: float = field(default_factory=time.time)
    agent: CodingAgent | None = None
    workspace: Workspace | None = None
    events: list[dict] = field(default_factory=list)
    first_event: int = 0
    permissions: dict[str, PendingPermission] = field(default_factory=dict)
    last: AgentResult | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Condition = field(default_factory=threading.Condition)

    def public(self) -> dict:
        last = self.last
        return {"id": self.id, "title": self.title, "directory": str(self.root), "status": self.status, "tier": self.tier,
                "mode": self.agent.mode if self.agent else "build", "created": self.created,
                "next_event": self.first_event + len(self.events),
                "todos": self.workspace.todos if self.workspace else [],
                "permissions": [{"id": p.id, "permission": p.permission, "pattern": p.pattern, "detail": p.detail}
                                for p in self.permissions.values() if not p.answered.is_set()],
                "questions": last.questions if last and self.status == "question" else [],
                "last": None if last is None else {"stopped": last.stopped, "text": last.text, "steps": last.steps,
                                                   "seconds": last.seconds, "tools": last.tools, "error": last.error}}


class _Gated:
    """모델 부르기 한 번마다 차례를 잡는 대리인 — 그 밖의 속성(think·last_metrics …)은 지금의 모델 것을 그대로 쓴다."""

    def __init__(self, getter: Callable[[], object], turn: Callable[[], ContextManager], on_delta: Callable[[str], None]):
        object.__setattr__(self, "_getter", getter)
        object.__setattr__(self, "_turn", turn)
        object.__setattr__(self, "_on_delta", on_delta)

    def chat_step(self, messages, tools=None, on_chunk=None):
        with self._turn():
            return self._getter().chat_step(messages, tools, on_chunk or self._on_delta)

    def __getattr__(self, name):
        return getattr(self._getter(), name)

    def __setattr__(self, name, value):
        setattr(self._getter(), name, value)


class CodeSessions:
    def __init__(self, provider_getter: Callable[[], object], *,
                 model_turn: Callable[[CodeSession], ContextManager] | None = None, store: Path | None = None,
                 shell: ShellPolicy | None = None, fetch=None, search=None, mcp=None,
                 lessons: Callable[[], str] | None = None, max_steps: int = 80, logger=None) -> None:
        self.provider_getter = provider_getter
        self.model_turn = model_turn or (lambda _session: nullcontext())
        self.store = Path(store) if store else None
        self.shell = shell or ShellPolicy()
        self.fetch, self.search, self.mcp, self.lessons = fetch, search, mcp, lessons
        self.max_steps = max_steps
        self.logger = logger
        self.sessions: dict[str, CodeSession] = {}
        self._load()

    # ── 사건
    def _emit(self, session: CodeSession, kind: str, **data) -> None:
        with session.lock:
            session.events.append({"n": session.first_event + len(session.events), "type": kind, "time": time.time(), **data})
            if len(session.events) > KEEP_EVENTS:
                drop = len(session.events) - KEEP_EVENTS
                session.events = session.events[drop:]
                session.first_event += drop
            session.lock.notify_all()

    def events(self, session_id: str, since: int = 0, wait: float = 25.0, owner: str | None = None) -> list[dict]:
        """since 번부터의 사건 — 없으면 wait 초까지 기다린다."""
        session = self.get(session_id, owner)
        deadline = time.monotonic() + wait
        with session.lock:
            while True:
                fresh = [e for e in session.events if e["n"] >= since]
                if fresh or time.monotonic() >= deadline:
                    return fresh
                session.lock.wait(timeout=max(0.05, deadline - time.monotonic()))

    # ── 세션
    def _workspace(self, session: CodeSession) -> Workspace:
        return Workspace(session.root, shell=self.shell, rules=perm.tier_rules(session.tier, self.shell.allow),
                         ask=lambda p, pattern, detail: self._ask(session, p, pattern, detail),
                         fetch=self.fetch, search=self.search, mcp=self.mcp, cancel_event=session.cancel)

    def create(self, owner: str, root: Path, tier: str = "ask", title: str = "") -> CodeSession:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError(f"작업 폴더가 없습니다: {root}")
        session = CodeSession("ses_" + secrets.token_hex(8), owner, root, tier if tier in ("read", "ask", "auto") else "ask",
                              title=str(title or "")[:80])
        session.workspace = self._workspace(session)
        self.sessions[session.id] = session
        self._emit(session, "session.created", info=session.public())
        self._save(session)
        return session

    def get(self, session_id: str, owner: str | None = None) -> CodeSession:
        session = self.sessions.get(session_id)
        if session is None or (owner is not None and session.owner != owner):
            raise KeyError("코딩 세션을 찾지 못했습니다.")
        return session

    def list(self, owner: str) -> list[dict]:
        return [s.public() for s in sorted(self.sessions.values(), key=lambda s: -s.created) if s.owner == owner]

    def delete(self, session_id: str, owner: str | None = None) -> None:
        session = self.get(session_id, owner)
        if session.status == "running":
            raise ValueError("일하는 중인 세션은 지울 수 없습니다. 중단한 뒤 지워 주세요.")
        self.sessions.pop(session.id, None)
        if self.store is not None:
            (self.store / f"{session.id}.json").unlink(missing_ok=True)
            shutil.rmtree(self.store / f"{session.id}.blobs", ignore_errors=True)

    def messages(self, session_id: str, owner: str | None = None) -> list[dict]:
        """대화(시스템 지시문 빼고) — 화면에 다시 그릴 때."""
        session = self.get(session_id, owner)
        if session.agent is None:
            return []
        return [{"role": m.role, "content": m.content, "tool_calls": m.tool_calls, "tool_name": m.tool_name}
                for m in session.agent.messages if m.role != "system"]

    # ── 권한
    def _ask(self, session: CodeSession, permission: str, pattern: str, detail: dict) -> bool:
        request = PendingPermission("per_" + secrets.token_hex(6), permission, pattern, detail)
        session.permissions[request.id] = request
        self._emit(session, "permission.asked", request={"id": request.id, "permission": permission, "pattern": pattern,
                                                         "detail": detail})
        if not request.answered.wait(PERMISSION_WAIT) or session.cancel.is_set():
            request.response = request.response or "reject"
            request.answered.set()
        if request.response == "always" and session.workspace is not None:
            rule = perm.Rule(permission, pattern, perm.ALLOW)
            session.workspace.rules = [*(session.workspace.rules or []), rule]
            if session.agent is not None and hasattr(session.agent, "_base_rules"):
                session.agent._base_rules.append(rule)   # 계획→실행 전환 뒤에도 남게
        self._emit(session, "permission.replied", id=request.id, response=request.response)
        session.permissions.pop(request.id, None)
        return request.response in ("once", "always")

    def reply_permission(self, session_id: str, request_id: str, response: str, owner: str | None = None) -> None:
        session = self.get(session_id, owner)
        request = session.permissions.get(request_id)
        if request is None or request.answered.is_set():
            raise KeyError("기다리는 권한 요청이 없습니다.")
        if response not in ("once", "always", "reject"):
            raise ValueError("response 는 once·always·reject 중 하나여야 합니다.")
        request.response = response
        request.answered.set()

    # ── 일 시키기
    def commands(self, session_id: str, owner: str | None = None) -> list[dict]:
        session = self.get(session_id, owner)
        return [{"name": c.name, "description": c.description, "source": c.source, "subtask": c.subtask}
                for c in cmds.find_commands(session.root).values()]

    def command(self, session_id: str, name: str, arguments: str = "", *, owner: str | None = None, mode: str = "") -> None:
        """'/이름 인자'(OpenCode session.command) — 지시문을 채워 보낸다. agent 가 general·explore 거나 subtask 면 하위 에이전트로."""
        session = self.get(session_id, owner)
        found = cmds.find_commands(session.root).get(str(name or "").lstrip("/"))
        if found is None:
            names = ", ".join("/" + n for n in cmds.find_commands(session.root))
            raise KeyError(f"명령을 찾지 못했습니다: /{name}. 있는 명령: {names}")
        text = cmds.expand(found.template, arguments)
        shown = f"/{found.name} {arguments}".strip()
        sub = found.agent in ("general", "explore") or found.subtask
        self.prompt(session_id, text, owner=owner, mode=found.agent if found.agent in ("build", "plan") else mode,
                    shown=shown, subtask=(found.agent if found.agent in ("general", "explore") else "general") if sub else "",
                    description=found.description or shown, shell=True)

    def prompt(self, session_id: str, text: str, *, owner: str | None = None, mode: str = "", shown: str = "",
               subtask: str = "", description: str = "", shell: bool = False) -> None:
        """mode: build·plan — 비우면 지금 모드 그대로. 질문(question)에 멈춘 세션이면 이 글이 답이 된다.
        명령에서 올 때: shown(화면·대화에 보일 '/이름 인자'), subtask(하위 에이전트 종류), shell(!`명령` 을 bash 도구로 채움)."""
        session = self.get(session_id, owner)
        if session.status == "running":
            raise ValueError("이 세션은 아직 일하는 중입니다. 끝나거나 중단한 뒤 보내 주세요.")
        if not str(text or "").strip():
            raise ValueError("요청이 비었습니다.")
        if mode not in ("", "build", "plan"):
            raise ValueError("mode 는 build·plan 중 하나여야 합니다.")
        session.status = "running"
        session.cancel.clear()
        if not session.title:
            session.title = text.strip().splitlines()[0][:60]
        self._emit(session, "message.user", text=shown or text, mode=mode or (session.agent.mode if session.agent else "build"))
        buffer: list[str] = []
        flushed = [time.monotonic()]

        def flush() -> None:
            if buffer:
                self._emit(session, "message.delta", text="".join(buffer))
                buffer.clear()
            flushed[0] = time.monotonic()

        def delta(chunk: str) -> None:
            buffer.append(str(chunk))
            if sum(map(len, buffer)) >= DELTA_CHARS or time.monotonic() - flushed[0] >= DELTA_SECONDS:
                flush()

        def say(note: str) -> None:   # 도구 실행 알림('[읽기 a.py]' …)
            flush()
            self._emit(session, "progress", text=note)

        @contextmanager
        def turn():
            with self.model_turn(session):
                try:
                    yield
                finally:
                    flush()

        provider = _Gated(self.provider_getter, turn, delta)

        def work() -> AgentResult:
            nonlocal text
            if shell:  # 명령의 !`…` — 셸 없이 하네스 bash 도구로(권한 규칙대로)
                text = cmds.SHELL.sub(lambda m: session.workspace.run("bash", {"command": m.group(1)}), text)
            agent = session.agent
            if subtask:
                if agent is None:
                    agent = session.agent = CodingAgent(provider, session.workspace, max_steps=self.max_steps,
                                                        cancel_event=session.cancel, mode=mode or "build",
                                                        lessons=self.lessons() if self.lessons else "")
                agent.provider, agent.say = provider, say
                return agent.run_subtask(subtask, description, text, shown or text)
            if agent is None:
                session.agent = CodingAgent(provider, session.workspace, max_steps=self.max_steps, progress=say,
                                            cancel_event=session.cancel, mode=mode or "build",
                                            lessons=self.lessons() if self.lessons else "")
                return session.agent.run(text)
            agent.provider, agent.say = provider, say
            if mode == "build" and agent.mode == "plan":
                return agent.switch_to_build(text)
            if mode == "plan" and agent.mode == "build":
                return agent.switch_to_plan(text)
            return agent.answer(text)

        def run() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 — 세션에 남기고 화면에 알린다
                if self.logger is not None:
                    self.logger.exception("code session failed")
                result = AgentResult(text="", stopped="error", error=str(exc))
            flush()
            session.last = result
            session.status = {"question": "question", "error": "error"}.get(result.stopped, "idle")
            if self.logger is not None:
                self.logger.info("code session %s: stopped=%s steps=%s seconds=%s tools=%s", session.id, result.stopped,
                                 result.steps, result.seconds, result.tools)
            self._save(session)
            self._emit(session, "question.asked" if result.stopped == "question" else "session.idle",
                       result=session.public()["last"], questions=result.questions, todos=result.todos)

        threading.Thread(target=run, daemon=True, name=f"alex-code-{session.id[-6:]}").start()

    def abort(self, session_id: str, owner: str | None = None) -> None:
        session = self.get(session_id, owner)
        session.cancel.set()
        for request in list(session.permissions.values()):
            if not request.answered.is_set():
                request.response = "reject"
                request.answered.set()
        self._emit(session, "session.aborted")

    def diff(self, session_id: str, owner: str | None = None) -> str:
        session = self.get(session_id, owner)
        return session.workspace.diff() if session.workspace else ""

    def todo(self, session_id: str, owner: str | None = None) -> list[dict]:
        session = self.get(session_id, owner)
        return list(session.workspace.todos) if session.workspace else []

    def revert(self, session_id: str, step: int = 0, owner: str | None = None) -> list[str]:
        session = self.get(session_id, owner)
        if session.status == "running":
            raise ValueError("일하는 중에는 되돌릴 수 없습니다. 중단한 뒤 되돌려 주세요.")
        restored = session.workspace.revert(int(step)) if session.workspace else []
        self._save(session)
        self._emit(session, "session.reverted", step=int(step), files=restored)
        return restored

    # ── 남기기·불러오기
    def _save(self, session: CodeSession) -> None:
        if self.store is None:
            return
        agent = session.agent
        data = {"id": session.id, "owner": session.owner, "root": str(session.root), "tier": session.tier,
                "title": session.title, "created": session.created, "mode": agent.mode if agent else "build",
                "step": agent.step if agent else 0, "todos": session.workspace.todos if session.workspace else [],
                "messages": [asdict(m) for m in agent.messages] if agent else [],
                "pending_question": agent._pending_question if agent else None,
                "last": None if session.last is None else {k: v for k, v in asdict(session.last).items() if k != "trace"}}
        try:
            self.store.mkdir(parents=True, exist_ok=True)
            data["history"] = self._save_history(session)
            target = self.store / f"{session.id}.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.replace(target)
        except OSError as exc:
            if self.logger is not None:
                self.logger.warning("코딩 세션 저장 실패: %s", exc)

    def _save_history(self, session: CodeSession) -> list:
        blobs = self.store / f"{session.id}.blobs"
        out = []
        for step, record in (session.workspace.history if session.workspace else []):
            saved = {}
            for key, before in record.items():
                if before is None:
                    saved[key] = None
                    continue
                digest = hashlib.sha256(before).hexdigest()
                blob = blobs / digest
                if not blob.exists():
                    blobs.mkdir(exist_ok=True)
                    blob.write_bytes(before)
                saved[key] = digest
            out.append([step, saved])
        used = {digest for _, saved in out for digest in saved.values() if digest}
        for blob in blobs.glob("*") if blobs.is_dir() else []:   # 되돌려서 안 쓰게 된 내용은 지운다
            if blob.name not in used:
                blob.unlink(missing_ok=True)
        return out

    def _load_history(self, session: CodeSession, rows: list) -> None:
        blobs = self.store / f"{session.id}.blobs"
        history = []
        for step, saved in rows or []:
            try:
                history.append((int(step), {key: None if digest is None else (blobs / digest).read_bytes()
                                            for key, digest in saved.items()}))
            except (OSError, TypeError, ValueError, AttributeError):
                history = []   # 조각이 없어졌으면 되돌리기 기록 전체를 버린다(반쪽 되돌리기보다 안전)
                break
        session.workspace.history = history

    def _load(self) -> None:
        if self.store is None or not self.store.is_dir():
            return
        for file in sorted(self.store.glob("ses_*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                session = CodeSession(data["id"], data["owner"], Path(data["root"]), data.get("tier", "ask"),
                                      title=data.get("title", ""), created=float(data.get("created") or 0))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not session.root.is_dir():
                continue
            session.workspace = self._workspace(session)
            session.workspace.todos = list(data.get("todos") or [])
            self._load_history(session, data.get("history"))
            if data.get("messages"):
                agent = CodingAgent(None, session.workspace, max_steps=self.max_steps, cancel_event=session.cancel,
                                    mode=data.get("mode", "build"), lessons=self.lessons() if self.lessons else "")
                agent.messages = [Message(m["role"], m.get("content", ""), m.get("tool_calls"), m.get("tool_name", ""))
                                  for m in data["messages"] if isinstance(m, dict) and m.get("role")]
                agent.step = int(data.get("step") or 0)
                agent._pending_question = data.get("pending_question")
                agent._base_rules = list(session.workspace.rules or [])
                if agent.mode == "plan":
                    session.workspace.rules = [*agent._base_rules, *perm.PLAN]
                session.agent = agent
            if isinstance(data.get("last"), dict):
                last = data["last"]
                session.last = AgentResult(**{k: last[k] for k in ("text", "stopped", "steps", "seconds", "tools",
                                                                   "compactions", "error", "questions", "todos") if k in last})
                session.status = "question" if session.last.stopped == "question" else "idle"
            self.sessions[session.id] = session
