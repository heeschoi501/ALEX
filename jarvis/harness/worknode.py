"""작업 노드(ADR-125) — 메인 ALEX 가 다른 기기(노트북·데스크톱·Mac·DGX Spark)에서 코드 세션을 돌린다.

2026-10-30 사용자: "데스크탑의 알렉스가 노트북에서 작업하는게 목적" · "맥에서도 데스크탑이랑 연결될텐데 그러면 맥에서 작업을
시켜야하는거잖아". 메인이 어느 기기든(지금 데스크톱, 나중에 Mac·Spark) 다른 기기에 일을 시킨다.

- 그 기기: `python -m jarvis.harness.worknode` — 메인이 SSH 로 띄운다. 표준 입출력으로 한 줄 JSON 을 주고받는다.
  그 기기의 CodeSessions 가 코드 세션을 그대로 돌린다(파일·명령·Unity 가 그 기기의 것, 권한 단계·되돌리기 그대로).
- 모델은 메인 것: 그 기기의 에이전트가 모델을 부르면 메인에 묻는다(NodeModel). 메인은 자기 모델 차례(model_turn)·생각 설정·
  중단으로 답한다. 그래서 그 기기에는 모델도 GPU 도 필요 없다.
- 메인: NodeLink 가 SSH 프로세스 하나를 잡고, 그 기기의 사건(진행·허락 요청·끝)을 메인 세션 사건으로 옮긴다.
  허락 응답·중단·되돌리기 같은 세션 일은 call() 로 그대로 넘긴다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .. import providers
from ..providers import Message

PROTOCOL = 1
# 메인이 그 기기의 CodeSessions 에 넘기는 일 — 모두 (세션 id, …) 를 받는다.
METHODS = frozenset({"public", "messages", "reply_permission", "commands", "command", "prompt", "abort", "diff", "todo", "revert"})
# 메인의 모델 오류를 그 기기에서 같은 종류로 다시 던진다 — 에이전트가 '생각 끄고 다시'·재시도를 그대로 한다.
ERRORS = {cls.__name__: cls for cls in (providers.ThinkingTooLong, providers.ContextExhausted, providers.ToolsUnsupported,
                                        providers.GenerationCancelled, providers.GenerationTimeout, providers.ProviderError)}
NODE_OWNER = "node"


class NodeError(RuntimeError):
    """작업 노드 연결 실패 — 사용자에게 그대로 보여 줘도 되는 글."""


@dataclass(frozen=True)
class NodeRoot:
    """코드 세션 폴더가 다른 기기에 있다 — 그 기기 이름, 그 기기의 폴더, 일꾼을 띄울 명령(ssh …)."""
    node: str
    path: str
    command: tuple[str, ...]


def message_dict(message: Message) -> dict:
    data = asdict(message)
    data["images"] = list(data.get("images") or ())
    return data


def message_from(data: dict) -> Message:
    return Message(role=str(data.get("role", "")), content=str(data.get("content", "")), tool_calls=data.get("tool_calls"),
                   tool_name=str(data.get("tool_name", "")), images=tuple(data.get("images") or ()),
                   local_only=bool(data.get("local_only")))


# ── 그 기기 쪽
class NodeModel:
    """그 기기의 코딩 에이전트가 쓰는 모델 — 부를 때마다 메인에 묻는다."""

    def __init__(self, server: "NodeServer", think: bool = False):
        self.server = server
        self.think = think

    def chat_step_controlled(self, messages, tools=None, on_chunk=None, *, cancel_event=None, deadline=None, think=None):
        return self.server.ask_model(messages, tools, think, deadline, cancel_event)

    def chat_step(self, messages, tools=None, on_chunk=None):
        return self.server.ask_model(messages, tools, None, None, None)


class NodeServer:
    def __init__(self, stdin=None, stdout=None, *, sessions_factory: Callable | None = None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.waiting: dict[int, dict] = {}
        self.answered = threading.Condition()
        self.model = NodeModel(self)
        if sessions_factory is None:
            from .sessions import CodeSessions
            sessions_factory = lambda getter: CodeSessions(getter, store=None)  # noqa: E731 — 기록은 메인이 남긴다
        self.sessions = sessions_factory(lambda: self.model)
        self.sid = ""
        self.closed = threading.Event()

    def send(self, data: dict) -> None:
        with self.lock:
            self.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
            self.stdout.flush()

    def ask_model(self, messages, tools, think, deadline, cancel_event) -> tuple[str, list]:
        ident = next(self.ids)
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        with self.answered:
            self.waiting[ident] = {}
        self.send({"type": "model", "id": ident, "messages": [message_dict(m) for m in messages], "tools": tools,
                   "think": think, "deadline_in": left})
        told = False
        with self.answered:
            while not self.waiting[ident]:
                if self.closed.is_set():
                    raise providers.GenerationCancelled("메인 ALEX 와 연결이 끊겼습니다.")
                if not told and cancel_event is not None and cancel_event.is_set():
                    self.send({"type": "model.cancel", "id": ident})   # 메인이 그 생성을 멈추고 오류로 답한다
                    told = True
                self.answered.wait(0.2)
            answer = self.waiting.pop(ident)
        if "error" in answer:
            raise ERRORS.get(answer.get("kind"), providers.ProviderError)(answer["error"])
        return str(answer.get("text", "")), list(answer.get("tool_calls") or [])

    def _pump(self) -> None:
        """그 기기 세션의 사건을 메인으로 — 사건마다 그때의 세션 상태(public)를 같이 보낸다."""
        since = 0
        while not self.closed.is_set():
            try:
                fresh = self.sessions.events(self.sid, since, wait=2.0)
            except KeyError:
                return
            for event in fresh:
                self.send({"type": "event", "event": event, "info": self.sessions.get(self.sid).public()})
                since = event["n"] + 1

    def _handle(self, message: dict) -> None:
        op, ident = message.get("op"), message.get("id")
        try:
            if op == "hello":
                from ..launcher import code_fingerprint
                self.model.think = bool(message.get("think"))
                value = {"protocol": PROTOCOL, "platform": sys.platform, "code": code_fingerprint()}
            elif op == "open":
                if self.sid:
                    raise NodeError("이 연결에는 이미 세션이 있습니다.")
                session = self.sessions.create(NODE_OWNER, Path(str(message["root"])), str(message.get("tier") or "ask"),
                                               str(message.get("title") or ""))
                self.sid = session.id
                threading.Thread(target=self._pump, daemon=True, name="alex-node-pump").start()
                value = session.public()
            elif op == "call":
                method = str(message.get("method"))
                if method not in METHODS or not self.sid:
                    raise NodeError(f"그 기기에서 할 수 없는 일입니다: {method}")
                value = getattr(self.sessions, method)(self.sid, *message.get("args", []), **message.get("kwargs", {}))
            elif op in ("model.result", "model.error"):
                with self.answered:
                    if message.get("id") in self.waiting:
                        self.waiting[message["id"]] = (message if op == "model.result"
                                                       else {"error": str(message.get("error") or "모델 오류"),
                                                             "kind": message.get("kind")})
                        self.answered.notify_all()
                return
            else:
                raise NodeError(f"모르는 요청입니다: {op}")
        except Exception as exc:  # noqa: BLE001 — 메인에 그대로 알린다(그 기기 창은 아무도 안 본다)
            self.send({"type": "reply", "id": ident, "error": str(exc) or type(exc).__name__, "kind": type(exc).__name__})
            return
        self.send({"type": "reply", "id": ident, "value": value})

    def serve(self) -> int:
        try:
            for line in self.stdin:
                if not line.strip():
                    continue
                message = json.loads(line)
                if message.get("op") == "close":
                    break
                if message.get("op") == "call" and message.get("method") in ("prompt", "command"):
                    # 일 시키기는 곧 돌아오지만(세션이 뒤 스레드에서 돈다) 읽기 줄을 막지 않게 따로
                    threading.Thread(target=self._handle, args=(message,), daemon=True).start()
                else:
                    self._handle(message)
        finally:
            self.closed.set()
            with self.answered:
                self.answered.notify_all()
            if self.sid:
                try:
                    self.sessions.abort(self.sid)
                except KeyError:
                    pass
        return 0


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, OSError):
            pass
    return NodeServer().serve()


# ── 메인 쪽
class NodeLink:
    """메인 ALEX 쪽 — 그 기기의 일꾼과 SSH 연결 하나(세션 하나)."""

    def __init__(self, command: list[str], *, on_event: Callable[[dict], None],
                 on_model: Callable[[dict], tuple[str, list]], on_model_cancel: Callable[[int], None] | None = None,
                 popen=subprocess.Popen, cwd: Path | None = None):
        self.on_event, self.on_model, self.on_model_cancel = on_event, on_model, on_model_cancel
        self.process = popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             encoding="utf-8", errors="replace", bufsize=1, cwd=cwd)
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.replies: dict[int, dict | None] = {}
        self.done = threading.Condition()
        self.errors: list[str] = []
        self.threads = [threading.Thread(target=self._read, daemon=True, name="alex-node-link"),
                        threading.Thread(target=self._drain, daemon=True, name="alex-node-stderr")]
        for thread in self.threads:
            thread.start()

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def _drain(self) -> None:
        for line in self.process.stderr:
            self.errors = (self.errors + [line.rstrip()])[-20:]

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                self.errors = (self.errors + [line.rstrip()])[-20:]
                continue
            kind = message.get("type")
            if kind == "reply":
                with self.done:
                    self.replies[message.get("id")] = message
                    self.done.notify_all()
            elif kind == "event":
                self.on_event(message)
            elif kind == "model":
                threading.Thread(target=self._model, args=(message,), daemon=True, name="alex-node-model").start()
            elif kind == "model.cancel" and self.on_model_cancel is not None:
                self.on_model_cancel(message.get("id"))
        with self.done:
            self.done.notify_all()

    def _model(self, message: dict) -> None:
        try:
            text, calls = self.on_model(message)
            self._send({"op": "model.result", "id": message.get("id"), "text": text, "tool_calls": calls})
        except Exception as exc:  # noqa: BLE001 — 종류를 같이 넘겨 그 기기에서 같은 오류로 다시 던진다
            self._send({"op": "model.error", "id": message.get("id"), "error": str(exc) or type(exc).__name__,
                        "kind": type(exc).__name__})

    def _send(self, data: dict) -> None:
        with self.lock:
            if not self.alive:
                raise NodeError(self.failure())
            self.process.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def failure(self) -> str:
        tail = " / ".join(line for line in self.errors[-3:] if line.strip())
        return "작업 노드와 연결이 끊겼습니다." + (f" ({tail})" if tail else "")

    def request(self, data: dict, timeout: float = 60.0):
        ident = next(self.ids)
        with self.done:
            self.replies[ident] = None
        self._send({**data, "id": ident})
        deadline = time.monotonic() + timeout
        with self.done:
            while self.replies.get(ident) is None:
                if not self.alive and self.replies.get(ident) is None:
                    self.replies.pop(ident, None)
                    raise NodeError(self.failure())
                if time.monotonic() >= deadline:
                    self.replies.pop(ident, None)
                    raise NodeError("작업 노드가 제때 답하지 않았습니다.")
                self.done.wait(0.2)
            reply = self.replies.pop(ident)
        if "error" in reply:
            error = reply["error"]
            raise {"KeyError": KeyError, "ValueError": ValueError}.get(reply.get("kind"), NodeError)(error)
        return reply.get("value")

    def hello(self, think: bool, timeout: float = 60.0) -> dict:
        return self.request({"op": "hello", "protocol": PROTOCOL, "think": bool(think)}, timeout)

    def open(self, root: str, tier: str, title: str) -> dict:
        return self.request({"op": "open", "root": root, "tier": tier, "title": title})

    def call(self, method: str, *args, **kwargs):
        return self.request({"op": "call", "method": method, "args": list(args), "kwargs": kwargs})

    def close(self) -> None:
        try:
            self._send({"op": "close"})
        except (NodeError, OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        for thread in self.threads:   # 읽던 줄이 끝(EOF)까지 간 뒤에 통로를 닫는다
            thread.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
