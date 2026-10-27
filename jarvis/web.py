"""Tiny loopback-only Web UI for Project ALEX.

Why this exists:
- A daily-use personal AI benefits from a chat-shaped UI.
- v0.1.x should not pull in Flask/FastAPI just for a local alpha.
- The Web UI must not become a second, weaker cloud-approval path.

Security boundaries:
- binds to 127.0.0.1 only
- POST requires a per-process CSRF token and application/json
- request body has a small hard limit
- no CORS headers
- Cloud approval callback always returns False in this UI
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
import html
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import shutil
import os
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs, unquote
import webbrowser
import threading
from typing import Any, Callable

from .app import (auto_apply_proposals, build_core, build_providers, build_summary_provider, engine_context_limits, handle_command,
                  provider_runtime_settings, CONTEXT_CHAR_BUDGET_BY_PROFILE)
from .config import ConfigError, Settings, load_settings, validate, LOCAL_FIELDS, local_config_path
from .core import JarvisCore
from .providers import GenerationCancelled, MockLLMProvider, OllamaProvider, StrataProvider
from .imagegen import ImageGenError
from .router import Destination
from .launcher import code_fingerprint
from .http_control import interrupt_on_cancel
from .imagegen import IMAGE_TYPES, MAX_UPLOAD_BYTES
from .documents import download_path, FORMATS, MAX_DOWNLOAD
from .vision import ImageAttachmentStore, MAX_ATTACHMENTS
from .knowledge import KnowledgeError, MAX_DOCUMENT_BYTES, import_notice
from . import accounts
from .storage import OWNER, StorageError, UserAccount
from .cloudagent import parse_request as parse_cloud_request
from .fetcher import downloaded_path
from .live import (LANG_LABEL, TRANSLATE, LiveError, SpeechRecognizer, check_wav, clean_translation, meaningful, tidy,
                   translate_messages)
from .permissions import (DEFAULT_PERMISSION, PERMISSION_MODES, READ_ONLY_BLOCKED, READ_ONLY_BLOCKED_COMMANDS,
                          clean_permission)

# 이 서버가 **불러온** 코드의 지문. 모듈을 읽는 순간에 잰다 — /health 를 받을 때 재면
# 그 사이 디스크에서 바뀐 코드를 자기 것처럼 보고하게 된다. 런처가 이 값으로 옛 서버를 알아본다.
LOADED_CODE = code_fingerprint()

LOOPBACK_HOST = "127.0.0.1"
APP_FILES = {"/manifest.webmanifest": "application/manifest+json", "/sw.js": "text/javascript; charset=utf-8",
             "/icon-192.png": "image/png", "/icon-512.png": "image/png"}
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64_000

STATIC = Path(__file__).with_name("static")
PAGE = (STATIC / "index.html").read_text(encoding="utf-8")
LIVE_PAGE = (STATIC / "live.html").read_text(encoding="utf-8")  # 실시간 통역(ADR-111)
CODE_PAGE = (STATIC / "code.html").read_text(encoding="utf-8")  # 코딩 세션 화면(ADR-112)
CODE_FILES = {"/code.js": "text/javascript; charset=utf-8", "/code.css": "text/css; charset=utf-8"}
LIVE_FILES = {"/live.js": "text/javascript; charset=utf-8", "/live-worklet.js": "text/javascript; charset=utf-8",
              "/live.css": "text/css; charset=utf-8"}

# 여러 사람이 쓰기(2026-10-11). 이 PC 에서 열면 주인(관리자)으로 바로 들어오고, 밖(Tailscale)에서는 로그인한다.
SESSION_COOKIE = "alex_session"
MANUAL_COOKIE = "alex_manual"  # 이 PC 에서 '사용자 전환'을 누른 브라우저 — 주인으로 자동 로그인하지 않는다
LOGIN_LOCK_AFTER, LOGIN_LOCK_SECONDS = 5, 60
ARTIFACT_RE = re.compile(r"\]\(alex-(?:image|video|document|download):([A-Za-z0-9._%-]{1,600})\)")
MAX_CHAT_DOCUMENTS = 5  # 한 번에 첨부하는 문서 수(ADR-096)
# 관리자가 아닌 사용자가 쓸 수 있는 명령. 이 PC 의 파일·설정·학습·개발 도구에 손대는 명령은 뺀다.
MEMBER_COMMANDS = frozenset({
    "/help", "/new", "/use", "/rename", "/delete-session", "/sessions", "/chat-search", "/summary", "/export", "/document",
    "/image", "/image-prompt", "/video-prompt", "/characters", "/memories", "/remember", "/recall", "/forget",
    "/task-add", "/tasks", "/task-done", "/task-open", "/task-delete", "/knowledge", "/knowledge-search",
    "/ask-knowledge", "/search", "/web", "/good", "/bad",
})
def provisional_title(text: str) -> str:
    """첫 답이 오기 전까지 쓸 임시 제목 — 첫 메시지를 한 줄로 줄이고 40자 넘으면 낱말 경계에서 자른다.
    문서를 붙였으면 '[첨부 문서: …]' 표시 줄은 빼고, 질문이 없으면 첫 문서 이름으로."""
    documents = re.findall(r"(?m)^\[첨부 문서: (.+?) · \d+쪽 · [\d,]+자\]$", text)
    clean = " ".join(re.sub(r"(?m)^\[첨부 문서: .+\]$", "", text).split()) or (documents[0] if documents else "")
    if len(clean) <= 40:
        return clean
    cut = clean[:40]
    space = cut.rfind(" ")
    return (cut[:space] if space >= 20 else cut) + "…"


ADMIN_PATHS = frozenset({"/api/settings", "/api/workspace", "/api/shutdown", "/api/restart", "/api/pull", "/api/users", "/api/coding"})


@dataclass(frozen=True)
class Viewer:
    """이 요청을 보낸 사람."""
    user: UserAccount
    remote: bool = False

    @property
    def id(self) -> str:
        return self.user.user_id

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.user.name, "login": self.user.login, "role": self.user.role,
                "has_password": bool(self.user.password_hash), "remote": self.remote}


@dataclass
class Ticket:
    """채팅방 하나의 요청. 화면 이동과 무관하게 같은 방의 요청을 식별한다."""
    user_id: str
    request_id: str = field(default_factory=lambda: secrets.token_hex(12))
    cancel: threading.Event = field(default_factory=threading.Event)
    session_id: str = ""
    background: bool = False  # 뒤 작업(ADR-101): 브라우저 연결과 상관없이 끝까지, 기다리는 대화 요청보다 뒤에
    external: bool = False    # 클라우드가 일하는 중(ADR-102) — 모델 잠금 없이 '하는 중'


MAX_SERVED_DOWNLOAD = 2_000_000_000  # 화면으로 내주는 받은 파일 상한(그보다 크면 폴더에서 직접)
AUTO_LEARN_EVERY = 600       # 쉬는 중 자동 학습을 살피는 간격(초)
AUTO_LEARN_MIN = 3           # 아직 안 배운 코딩 기록이 이만큼 쌓이면
JOB_KEEP = 20                # 사람마다 남겨 두는 뒤 작업 기록 수
JOB_PROGRESS_CHARS = 1500    # 진행 중 화면에 보이는 마지막 글자 수
JOB_RESULT_CHARS = 600       # 끝난 작업 목록에 보이는 답 앞부분
JOB_MESSAGE_CHARS = 4000     # '다시 맡기기'(ADR-110)용으로 남기는 요청 원문
JOB_STATE_CHARS = 9500       # app_state 한 칸(1만 자) 안에 — 넘으면 끝난 것의 진행 글부터 줄이고, 그래도 넘으면 오래된 것부터 뺀다
# 뒤 작업이 대화에 비켜 준 뒤 되돌리지 않는 코어 값 — 끼어든 대화가 바꾼 작업 폴더는 그대로 둔다(ADR-110)
YIELD_KEEP = ("project_root", "workspace_editor", "vscode_launcher", "project_tree_tool", "project_context_tool",
              "file_search_tool", "file_read_tool", "_knowledge_store")


def _provider_backend(provider: object) -> str:
    # Strata inherits Ollama's tool loop, but uses its own inference server.
    if isinstance(provider, StrataProvider):
        return "strata"
    if isinstance(provider, OllamaProvider):
        return "ollama"
    if isinstance(provider, MockLLMProvider):
        return "mock"
    return "unknown"


@dataclass
class WebAppState:
    core: JarvisCore
    session_id: str
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    lock: threading.RLock = field(default_factory=threading.RLock)

    settings: Settings | None = None
    active_id: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    active_kind: str | None = None
    shutting_down: bool = False
    inactive_providers: list[OllamaProvider] = field(default_factory=list)
    handoff_blocked: bool = False
    attachments: ImageAttachmentStore = field(init=False)
    # 여러 사람이 쓰기(2026-10-11): session_id 는 주인(관리자)이 보던 대화, current 는 다른 사람들이 보던 대화.
    current: dict[str, str] = field(default_factory=dict)
    queue: list[Ticket] = field(default_factory=list)
    queue_cv: threading.Condition = field(default_factory=threading.Condition)
    running: Ticket | None = None
    exchange_owner: str = OWNER
    exchange_session: str = ""
    exchanges: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)
    login_failures: dict[str, tuple[int, float]] = field(default_factory=dict)
    login_lock: threading.Lock = field(default_factory=threading.Lock)
    year_report: threading.Thread | None = None
    jobs: dict[str, dict] = field(default_factory=dict)       # 뒤 작업 기록(id → 상태). 사람마다 app_state 'jobs:<id>' 에도
    jobs_loaded: set[str] = field(default_factory=set)
    # 실시간 통역(ADR-111): 받아쓰기 일꾼(처음 쓸 때 띄움), 방마다 앞 발화 두 개
    speech: SpeechRecognizer = field(default_factory=lambda: SpeechRecognizer(Path(__file__).resolve().parents[2] / "tools" / "stt"))
    live_history: dict[str, list[str]] = field(default_factory=dict)
    code: Any = None   # 코딩 세션(ADR-112 — OpenCode 서버 API 와 같은 일, harness.sessions.CodeSessions)
    code_links: dict[str, dict] = field(default_factory=dict)   # 대화 id → 말로 연 코딩 세션 {code, since, fresh}(2026-10-23)

    def owner_viewer(self) -> Viewer:
        return Viewer(self.core.storage.get_user(OWNER))  # type: ignore[arg-type]

    def owns(self, viewer: Viewer, session_id: Any) -> bool:
        return isinstance(session_id, str) and bool(session_id) and self.core.storage.session_owner(session_id) == viewer.id

    def current_session(self, viewer: Viewer) -> str:
        if viewer.id == OWNER:
            return self.session_id
        sid = self.current.get(viewer.id) or self.core.storage.get_state(f"web.current_session.{viewer.id}")
        if not self.owns(viewer, sid):
            sid = self.core.get_or_create_session(resume=True, owner=viewer.id)
        self.current[viewer.id] = sid
        return sid

    def set_current(self, viewer: Viewer, session_id: str) -> None:
        if viewer.id == OWNER:
            self.session_id = session_id
            self.core.storage.set_state("web.current_session", session_id)
        else:
            self.current[viewer.id] = session_id
            self.core.storage.set_state(f"web.current_session.{viewer.id}", session_id)

    def ticket_for(self, user_id: str, session_id: str | None = None) -> Ticket | None:
        with self.queue_cv:
            return next((t for t in self.queue if t.user_id == user_id
                         and (session_id is None or t.session_id == session_id)), None)

    def queue_info(self, viewer: Viewer, session_id: str | None = None) -> dict[str, Any]:
        with self.queue_cv:
            mine = next((i for i, t in enumerate(self.queue) if t.user_id == viewer.id
                         and (session_id is None or t.session_id == session_id)), None)
            return {"running": self.running is not None or self.active_id is not None,
                    "waiting": max(0, len(self.queue) - (1 if self.running is not None else 0)),
                    "position": mine,
                    "requests": [{"request_id": t.request_id, "session_id": t.session_id, "background": t.background,
                                  "status": "running" if t is self.running or t.external else "queued",
                                  "position": 0 if t is self.running else max(i, 1)}
                                 for i, t in enumerate(self.queue) if t.user_id == viewer.id]}

    # ---------------------------------------------------------------- 차례(대기열)
    def may_run(self, ticket: Ticket) -> bool:
        """queue_cv 를 잡은 채 부른다. 대화 요청이 먼저 — 기다리는 뒤 작업은 대화 요청이 하나도 없을 때 들어간다
        (이미 도는 뒤 작업은 끝까지 간다. 모델은 한 번에 한 요청만)."""
        ahead = self.queue[:self.queue.index(ticket)]
        if ticket.background:
            return not any(not t.background for t in self.queue) and not any(t.background for t in ahead)
        return not any(not t.background for t in ahead)

    def position(self, ticket: Ticket) -> int:
        """queue_cv 를 잡은 채 부른다. 이 요청보다 먼저 처리될 요청 수(최소 1 — 부를 때는 이미 못 들어간 것)."""
        ahead = self.queue[:self.queue.index(ticket)]
        if ticket.background:
            count = sum(1 for t in self.queue if t is not ticket and (not t.background or t in ahead or t is self.running))
        else:
            count = sum(1 for t in ahead if not t.background or t is self.running)
        return max(count, 1)

    def wait_turn(self, ticket: Ticket, on_position=None) -> bool:
        """차례가 오면 모델 잠금(lock)을 잡고 True. 기다리는 동안 순번이 바뀌거나 10초마다 on_position(순번)."""
        last, sent_at = None, 0.0
        while True:
            with self.queue_cv:
                if ticket.cancel.is_set() or self.shutting_down:
                    return False
                if self.may_run(ticket) and self.lock.acquire(blocking=False):
                    return True
                position = self.position(ticket)
            now = time.monotonic()
            if on_position is not None and (position != last or now - sent_at > 10):
                on_position(position)
                last, sent_at = position, now
            with self.queue_cv:
                self.queue_cv.wait(timeout=1.0)

    @contextmanager
    def ticket_running(self, ticket: Ticket, kind: str, on_chunk):
        """차례가 온 요청(lock 을 잡은 상태)을 처리하는 동안 공유 콜백·취소 신호를 이 요청 것으로 바꾼다.
        끝나면 되돌리고 잠금을 푼다. 기다리는 동안 엔진이 바뀌었을 수 있으니 지금의 모델들을 여기서 잡는다."""
        core = self.core
        previous = core.response_chunk_callback
        previous_command_cancel = getattr(core, "command_cancel_event", None)
        local = {p for p in core.providers.values() if isinstance(p, OllamaProvider)}
        if isinstance(core.local_summarizer, OllamaProvider):
            local.add(core.local_summarizer)
        previous_cancel = {p: p.cancel_event for p in local}
        self.running = ticket
        self.active_id, self.active_kind = ticket.request_id, kind
        self.cancel_event = ticket.cancel
        core.response_chunk_callback = on_chunk
        core.command_cancel_event = ticket.cancel
        for p in local:
            p.cancel_event = ticket.cancel
            p.last_metrics = {}
        try:
            yield local
        finally:
            core.response_chunk_callback = previous
            core.command_cancel_event = previous_command_cancel
            for p, event in previous_cancel.items():
                p.cancel_event = event
            self.running = None
            self.active_id = self.active_kind = None
            self.lock.release()

    # ---------------------------------------------------------------- 뒤 작업(ADR-101)
    def start_job(self, viewer: Viewer, session_id: str, message: str, permission: str | None) -> dict:
        """대기열에 넣고 바로 돌아온다. 브라우저를 닫아도 계속하고, 끝나면 그 대화에 답이 남고 화면이 알린다."""
        self.job_list(viewer)  # 예전 기록을 먼저 읽어 둔다(저장할 때 덮지 않게)
        ticket = Ticket(viewer.id, session_id=session_id, background=True)
        now = datetime.now(timezone.utc).isoformat()
        job = {"id": ticket.request_id, "owner": viewer.id, "session_id": session_id,
               "title": self.core.storage.session_title(session_id) or "", "request": message.strip()[:200],
               "status": "queued", "created": now, "started": "", "finished": "", "progress": "", "result": "",
               "message": message.strip()[:JOB_MESSAGE_CHARS], "permission": permission}
        with self.queue_cv:
            self.queue.append(ticket)
            self.jobs[ticket.request_id] = job
            self.queue_cv.notify_all()
        self._save_jobs(viewer.id)
        accepted = self._public_job(job)
        threading.Thread(target=self._run_job, args=(ticket, viewer, message, permission), daemon=True,
                         name=f"alex-job-{ticket.request_id[:6]}").start()
        return accepted

    def _run_job(self, ticket: Ticket, viewer: Viewer, message: str, permission: str | None) -> None:
        job = self.jobs[ticket.request_id]

        def chunk(text: str) -> None:
            job["progress"] = (job["progress"] + text)[-JOB_PROGRESS_CHARS:]
        if parse_cloud_request(message) is not None:
            self._run_cloud_job(ticket, viewer, message, permission, job, chunk)
            return
        try:
            if not self.wait_turn(ticket):
                job["status"] = "cancelled"
                return
            job.update(status="running", started=datetime.now(timezone.utc).isoformat())
            self._save_jobs(viewer.id)
            with self.ticket_running(ticket, "background", chunk) as local:
                self.core.turn_yield = self._turn_yield(ticket, job, chunk, local)
                try:
                    result = self.process(message, None, viewer=viewer, session_id=ticket.session_id, permission=permission)
                finally:
                    self.core.turn_yield = None
            job.update(status="done", result=str(result.get("response", ""))[:JOB_RESULT_CHARS],
                       title=self.core.storage.session_title(ticket.session_id) or job["title"])
        except GenerationCancelled:
            job["status"] = "cancelled"
        except Exception as exc:  # noqa: BLE001 — 뒤 작업의 실패는 기록하고 그 대화에 알린다
            self.core.logger.exception("background job failed")
            job.update(status="failed", result=str(exc)[:JOB_RESULT_CHARS])
            try:
                self.core.storage.add_message(ticket.session_id, "assistant",
                                              f"뒤 작업이 실패했습니다: {job['request']}\n\n{exc}")
            except StorageError:
                pass
        finally:
            job["finished"] = datetime.now(timezone.utc).isoformat()
            with self.queue_cv:
                if ticket in self.queue:
                    self.queue.remove(ticket)
                self.queue_cv.notify_all()
            self._save_jobs(viewer.id)
            self.archive_sync()

    def _run_cloud_job(self, ticket: Ticket, viewer: Viewer, message: str, permission: str | None, job: dict,
                       chunk) -> None:
        """클라우드로 넘기기(ADR-102)를 세 단계로: 준비(잠금) → 클라우드가 일함(잠금 없음 — 그동안 ALEX 와 대화 가능)
        → ALEX 확인·수정안(잠금). 실패·중단해도 복사본을 지우고 그 대화에 이유를 남긴다."""
        sid, task, run, error = ticket.session_id, None, None, ""
        try:
            if not self.wait_turn(ticket):
                job["status"] = "cancelled"
                return
            job.update(status="running", started=datetime.now(timezone.utc).isoformat())
            self._save_jobs(viewer.id)
            with self.ticket_running(ticket, "background", chunk), self.acting_as(viewer, sid):
                self.core.permission_mode = (self.set_permission(viewer, sid, permission) if permission is not None
                                             else self.session_permission(viewer, sid))
                try:
                    task = self.core.cloud_prepare(sid, message)
                finally:
                    self.core.permission_mode = DEFAULT_PERMISSION
            if isinstance(task, str):
                job.update(status="done", result=task[:JOB_RESULT_CHARS])
                task = None
                return
            ticket.external = True
            try:
                run = self.core.cloud_run(task, ticket.cancel, chunk)
            except GenerationCancelled:
                error = "사용자가 중단했습니다."
            except Exception as exc:  # noqa: BLE001 — 이유를 남기고 복사본은 아래에서 지운다
                self.core.logger.exception("cloud agent failed")
                error = str(exc)
            finally:
                ticket.external = False
            # 마무리는 중단됐어도 해야 한다(복사본 지우기·답 남기기) — 취소 신호 없는 새 차례로
            ticket.cancel = threading.Event()
            if not self.wait_turn(ticket):  # 앱을 끄는 중 — 복사본만 지우고(아래 finally) 끝낸다
                job["status"] = "interrupted"
                return
            with self.ticket_running(ticket, "background", chunk):
                answer = self.core.cloud_finish(sid, task, run, error)
            task = None
            job.update(status="cancelled" if error == "사용자가 중단했습니다." else "done",
                       result=answer[:JOB_RESULT_CHARS])
        except Exception as exc:  # noqa: BLE001
            self.core.logger.exception("cloud job failed")
            job.update(status="failed", result=str(exc)[:JOB_RESULT_CHARS])
        finally:
            if task is not None:  # 준비 뒤 예기치 않게 끝났으면 복사본만이라도 지운다
                shutil.rmtree(task.temp, ignore_errors=True)
            job["finished"] = datetime.now(timezone.utc).isoformat()
            with self.queue_cv:
                if ticket in self.queue:
                    self.queue.remove(ticket)
                self.queue_cv.notify_all()
            self._save_jobs(viewer.id)
            self.archive_sync()

    def _turn_yield(self, ticket: Ticket, job: dict, chunk, local) -> Callable[[], None]:
        """뒤 작업이 단계 사이(긴 문서 요약 조각·RTL 회차·EDA 흐름)에서 부른다(ADR-110). 기다리는 대화 요청이 있으면 모델 잠금을
        잠시 내주고, 대화가 다 끝나면 다시 잡는다. 코어는 하나라서 끼어든 대화가 바꾼 요청별 값(권한·직전 문답·이번 차례 안내·
        콜백·취소 신호 …)을 되돌려 뒤 작업이 제 상태로 이어 가게 한다. 끼어든 대화의 직전 문답은 그 방 몫으로 남긴다."""
        core = self.core

        def yield_turn() -> None:
            with self.queue_cv:
                if self.shutting_down or ticket.cancel.is_set() or not any(not t.background for t in self.queue):
                    return
            saved_core = dict(vars(core))
            saved_web = (self.running, self.active_id, self.active_kind, self.cancel_event)
            saved_exchange = (self.exchange_owner, self.exchange_session)
            saved_cancel = {p: p.cancel_event for p in local}
            chunk("\n[대화 요청이 와서 잠시 비켜 줍니다 — 끝나면 이어서 합니다]\n")
            job["status"] = "paused"
            self.running, self.active_id, self.active_kind = None, None, None
            # 처리 안에서 'with self.lock' 으로 한 번 더 잡혀 있다(RLock) — 겹친 만큼 모두 놓았다가 그대로 되잡는다
            # (threading.Condition 이 쓰는 것과 같은 _release_save/_acquire_restore).
            held = self.lock._release_save()
            with self.queue_cv:
                self.queue_cv.notify_all()
            while True:
                with self.queue_cv:
                    if not any(not t.background for t in self.queue) and self.lock.acquire(blocking=False):
                        self.lock.release()
                        break
                    self.queue_cv.wait(timeout=1.0)
            self.lock._acquire_restore(held)
            if (self.exchange_owner, self.exchange_session) != saved_exchange and self.exchange_session:
                self.exchanges[(self.exchange_owner, self.exchange_session)] = (core.last_exchange, core.last_route_event_id)
            self.exchanges.pop(saved_exchange, None)
            kept = {name: getattr(core, name) for name in YIELD_KEEP if hasattr(core, name)}
            core.__dict__.update(saved_core)
            core.__dict__.update(kept)
            self.exchange_owner, self.exchange_session = saved_exchange
            self.running, self.active_id, self.active_kind, self.cancel_event = saved_web
            for provider, event in saved_cancel.items():
                provider.cancel_event = event
            job["status"] = "running"
            chunk("[이어서 합니다]\n")
        return yield_turn

    # ---------------------------------------------------------------- 실시간 통역(ADR-111)
    def live_start(self, viewer: Viewer) -> dict:
        """통역 한 번 = 새 대화 하나('실시간 통역 10-22 15:30'). 받아쓰기 일꾼을 미리 띄운다."""
        if not self.speech.available():
            raise ValueError("받아쓰기 도구가 설치되어 있지 않습니다(C:\\ALEX\\tools\\stt — ADR-111).")
        sid = self.core.storage.create_session(viewer.id)
        self.core.storage.rename_session(sid, f"실시간 통역 {datetime.now():%m-%d %H:%M}")
        try:
            self.speech.warm()
        except LiveError as exc:
            raise ValueError(str(exc)) from exc
        return {"session_id": sid, "languages": "중국어·영어·일본어 → 한국어"}

    def live_translate(self, viewer: Viewer, session_id: str, text: str, lang: str) -> str:
        """모델 잠금을 잡은 상태에서 부른다. 생각 끈 대화 모델로 자막 한두 줄."""
        history = self.live_history.setdefault(session_id, [])
        try:
            glossary = self.core.storage.list_memories(owner=viewer.id)
        except StorageError:
            glossary = {}
        reply = self.core.local_summarizer.generate(translate_messages(text, lang, history, glossary))
        history.append(text)
        del history[:-2]
        return clean_translation(reply)

    def job_message(self, viewer: Viewer, job_id: str) -> tuple[str, str, str | None]:
        """'다시 맡기기'(ADR-110) — 끝나지 못한 이 사람의 뒤 작업의 (대화, 요청 원문, 권한)."""
        self.job_list(viewer)
        job = self.jobs.get(str(job_id))
        if job is None or job.get("owner") != viewer.id:
            raise ValueError("그 뒤 작업을 찾지 못했습니다.")
        if job.get("status") not in ("failed", "cancelled", "interrupted"):
            raise ValueError("끝나지 못한 뒤 작업(실패·취소·끊김)만 다시 맡길 수 있습니다.")
        message = job.get("message") or (job.get("request", "") if len(job.get("request", "")) < 200 else "")
        if not message:
            raise ValueError("이 뒤 작업은 요청 원문이 남아 있지 않아 다시 맡길 수 없습니다. 입력창에 다시 써 주세요.")
        if not self.owns(viewer, job.get("session_id", "")):
            raise ValueError("그 대화가 지워져 다시 맡길 수 없습니다.")
        permission = job.get("permission")
        return job["session_id"], message, permission if permission in (None, "read", "ask", "auto") else None

    def job_list(self, viewer: Viewer) -> list[dict]:
        """이 사람의 뒤 작업(최근 순, 최대 JOB_KEEP). ALEX 가 다시 시작되기 전에 끝나지 못한 것은 '끊김'."""
        if viewer.id not in self.jobs_loaded:
            self.jobs_loaded.add(viewer.id)
            try:
                saved = json.loads(self.core.storage.get_state(f"jobs:{viewer.id}") or "[]")
            except (ValueError, StorageError):
                saved = []
            for job in saved if isinstance(saved, list) else []:
                if isinstance(job, dict) and job.get("id") and job["id"] not in self.jobs:
                    if job.get("status") in ("queued", "running", "paused"):
                        job["status"] = "interrupted"
                    self.jobs[job["id"]] = job
        mine = [job for job in self.jobs.values() if job.get("owner") == viewer.id]
        mine.sort(key=lambda job: job.get("created", ""), reverse=True)
        return [self._public_job(job) for job in mine[:JOB_KEEP]]

    def _public_job(self, job: dict) -> dict:
        return {key: job.get(key, "") for key in ("id", "session_id", "title", "request", "status", "created", "started",
                                                  "finished", "progress", "result")}

    def _save_jobs(self, owner: str) -> None:
        mine = sorted((job for job in self.jobs.values() if job.get("owner") == owner),
                      key=lambda job: job.get("created", ""), reverse=True)[:JOB_KEEP]
        # 한 칸 1만 자 제한(ADR-110 에서 발견 — 진행 글 1,500자짜리 작업이 몇 개만 쌓여도 저장이 실패했다):
        # 끝난 작업은 진행 글을 빼고, 끝난 것(done)은 원문도 빼고, 그래도 넘으면 오래된 것부터 뺀다.
        mine = [{**job, "progress": "" if job.get("status") in ("done", "failed", "cancelled", "interrupted") else job.get("progress", ""),
                 "message": "" if job.get("status") == "done" else job.get("message", "")} for job in mine]
        text = json.dumps(mine, ensure_ascii=False)
        while len(text) > JOB_STATE_CHARS and mine:
            mine.pop()
            text = json.dumps(mine, ensure_ascii=False)
        try:
            self.core.storage.set_state(f"jobs:{owner}", text)
        except StorageError as exc:
            self.core.logger.warning("뒤 작업 기록 저장 실패: %s", exc)

    @contextmanager
    def acting_as(self, viewer: Viewer, session_id: str = ""):
        """한 사람의 요청을 처리하는 동안만 그 사람의 문맥으로 바꾼다. 모델은 한 번에 한 요청만 처리하므로(state.lock)
        코어 하나를 차례로 나눠 쓴다. 직전 문답(교정·그림 고치기에 쓰는)은 사람과 방마다 따로 두고, 관리자가 아니면
        이 PC 의 파일·명령에 닿는 길과 자동 학습·코드 실행 확인을 끈다."""
        core = self.core
        key = (viewer.id, session_id)
        previous = (self.exchange_owner, self.exchange_session)
        if previous != key:
            if self.exchange_session:
                self.exchanges[previous] = (core.last_exchange, core.last_route_event_id)
            core.last_exchange, core.last_route_event_id = self.exchanges.pop(key, (None, None))
            self.exchange_owner, self.exchange_session = key
        saved = (core.member_mode, core.auto_lessons, core.code_check)
        if not viewer.is_admin:
            core.member_mode, core.auto_lessons, core.code_check = True, False, False
        try:
            yield
        finally:
            core.member_mode, core.auto_lessons, core.code_check = saved

    def __post_init__(self) -> None:
        data_dir = self.settings.data_dir if self.settings is not None else self.core.storage.db_path.parent
        self.attachments = ImageAttachmentStore(data_dir / "attachments")
        # Web Alpha has no interactive confirmation surface. Even if the global CLI
        # setting disabled approval, entering the Web surface must restore fail-closed
        # Cloud behavior instead of silently inheriting that weaker policy.
        self.core.require_cloud_approval = True
        self.core.approval_callback = lambda _request: False
        if self.settings is not None and self.settings.use_ollama:
            from .model_switch import backend_available
            other = "ollama" if self.settings.local_backend == "strata" else "strata"
            if backend_available(replace(self.settings, local_backend=other)):
                provider = OllamaProvider(self.settings.general_model, host=self.settings.ollama_host) if other == "ollama" else StrataProvider(self.settings.strata_model, host=self.settings.strata_host)
                self.inactive_providers.append(provider)
                self.core.local_engine_guard = self._check_inactive_engines
        from .harness.mcp import MCPHub
        from .harness.sessions import CodeSessions
        logger = getattr(self.core, "logger", None)
        hub = None
        try:
            hub = MCPHub.from_config(data_dir / "mcp.json")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if logger is not None:
                logger.warning("MCP 설정을 읽지 못했습니다: %s", exc)
        if hub is not None:
            threading.Thread(target=hub.start, daemon=True, name="alex-mcp-start").start()
        self.code = CodeSessions(lambda: self.core.providers.get(Destination.LOCAL_CODER), model_turn=self.code_turn,
                                 store=data_dir / "code_sessions", fetch=self._code_fetch, search=self._code_search,
                                 mcp=hub, timeout=getattr(self.settings, 'opencode_timeout', 900), logger=logger,
                                 lessons=getattr(self.core, "coding_insight_text", None),  # 절차 교훈(ADR-070, 켤 때만)
                                 outcome=self._record_code_outcome,
                                 opencode_home=Path(self.settings.opencode_dir) if getattr(self.settings, "opencode_dir", "") else None)
        if self.settings is not None:  # 실제 앱에서만(시험의 상태 객체는 빼고) — 쉬는 중 자동 학습
            threading.Thread(target=self._auto_learn_loop, daemon=True, name="alex-auto-learn").start()

    # ---------------------------------------------------------------- 코딩 기록으로 배우기(2026-10-23)
    def _record_code_outcome(self, event: dict) -> None:
        """코딩 세션 결과를 자동 수정 기록과 같은 곳(dev-outcomes.jsonl)에 — /dev-learn 이 함께 읽는다."""
        storage = getattr(self.core, "storage", None)
        if storage is not None:
            from .developer import record_outcome
            record_outcome(storage, event)

    def _auto_learn_loop(self) -> None:
        while not self.shutting_down:
            time.sleep(AUTO_LEARN_EVERY)
            try:
                self.maybe_auto_learn()
            except Exception:  # noqa: BLE001 — 다음 차례에 다시
                self.core.logger.exception("auto learn failed")

    def maybe_auto_learn(self) -> str:
        """설정(coding_auto_learn_enabled)이 켜져 있고, 기다리는 요청이 없고, 아직 안 배운 기록이 AUTO_LEARN_MIN 건 이상이면
        뒤 작업 차례(대화 요청이 오면 그쪽이 먼저)로 /dev-learn 을 한 번 돌린다. 모델 한 번 부르기 — GPU 긴 작업 아님."""
        if not getattr(self.core, "coding_auto_learn", False) or self.shutting_down:
            return ""
        with self.queue_cv:
            if self.queue or self.running is not None or self.active_id is not None:
                return ""
        from .developer import DeveloperMode
        if DeveloperMode(self.core).learning_pending() < AUTO_LEARN_MIN:
            return ""
        ticket = Ticket(OWNER, background=True)
        with self.queue_cv:
            self.queue.append(ticket)
            self.queue_cv.notify_all()
        try:
            if not self.wait_turn(ticket):
                return ""
            with self.ticket_running(ticket, "learn", lambda _chunk: None):
                reply = DeveloperMode(self.core).learn()
            self.core.logger.info("auto learn: %s", reply.splitlines()[0] if reply else "")
            return reply
        finally:
            with self.queue_cv:
                if ticket in self.queue:
                    self.queue.remove(ticket)
                self.queue_cv.notify_all()

    # ---------------------------------------------------------------- 코딩 세션(ADR-112)
    @contextmanager
    def code_turn(self, session):
        """코딩 세션이 모델을 한 번 부르는 동안만 차례를 잡는다(대화 요청과 같은 대기열). 도구 실행·허락 기다리는 동안엔 내준다."""
        from .http_control import RequestControl
        from .providers import GenerationTimeout
        control = RequestControl(session.cancel, deadline=session.deadline)
        ticket = Ticket(session.owner, session_id=session.id, cancel=control)
        with self.queue_cv:
            self.queue.append(ticket)
            self.queue_cv.notify_all()
        try:
            if not self.wait_turn(ticket):
                if control.expired and not control.cancelled:
                    raise GenerationTimeout('대기열에서 코딩 요청의 제한 시간이 끝났습니다.')
                raise GenerationCancelled("코딩 작업을 중단했습니다.")
            with self.ticket_running(ticket, "code", lambda _chunk: None):
                yield
        finally:
            with self.queue_cv:
                if ticket in self.queue:
                    self.queue.remove(ticket)
                self.queue_cv.notify_all()

    def code_root(self, raw: Any) -> Path:
        """코딩 세션 폴더 — 시작할 때 승인한 파일 루트 안만. 비우면 지금 프로젝트 폴더."""
        approved = getattr(self.core, "_approved_file_root", None)
        if approved is None:
            raise ValueError("승인된 파일 루트가 없어 코딩 세션을 열 수 없습니다.")
        if not raw:
            return self.core.current_project_root() or approved
        path = Path(str(raw))
        path = (path if path.is_absolute() else approved / path).resolve()
        if path != approved and approved not in path.parents:
            raise ValueError("승인된 파일 루트 밖의 폴더는 열 수 없습니다.")
        return path

    def code_chat_plan(self, viewer: Viewer, chat_sid: str, message: str) -> dict | None:
        """대화에서 말로 코딩 세션(2026-10-23, 사용자 "자연어로 할 수 있도록"). 이 대화에 이어진 세션이 허락·질문을 기다리면
        이번 말이 그 답이고, 방금 끝난 세션이면 '되돌려줘'·이어서 고치기, 아니면 새 코딩 요청인지(프로젝트 파일 + 고치기 말)."""
        # Explicit Office requests and replies to the newest Office task win over an older code link.
        if self.core.office_edits.matches(chat_sid, message):
            if link := self.code_links.get(chat_sid):
                link['fresh'] = False
            return None
        self.core.office_edits.deactivate(chat_sid)
        from .research import request as research_request
        if research_request(message) is not None:
            return None
        from .harness.intent import EDIT, REVERT, is_coding_request, permission_reply
        if self.code is None or not viewer.is_admin:
            return None
        link = self.code_links.get(chat_sid)
        session = self.code.sessions.get(link["code"]) if link else None
        if session is not None:
            pending = [p for p in session.permissions.values() if not p.answered.is_set()]
            if pending and (reply := permission_reply(message)):   # 응·항상·아니가 아니면 평소 대화로(허락은 계속 기다림)
                return {"kind": "permission", "session": session, "request": pending[0], "reply": reply}
            if session.status == "running" and not pending:
                return {"kind": "busy", "session": session}
            if session.status == "question":
                return {"kind": "answer", "session": session}
            if link.get("fresh") and REVERT.search(message):
                return {"kind": "revert", "session": session}
            if link.get("fresh") and EDIT.search(message):
                return {"kind": "continue", "session": session}
        try:
            root = self.code_root("")
        except ValueError:
            return None
        if is_coding_request(message, root):
            return {"kind": "new", "root": root}
        if link:
            link["fresh"] = False   # 다른 이야기를 했으면 '이어서 고치기'·'되돌려줘' 는 더 이상 이 세션 뜻이 아니다
        return None

    def _code_fetch(self, url: str, _format: str = "markdown") -> str:
        from .webread import WebError
        if self.core.web_reader is None:
            return "Error: web access is turned off in ALEX settings"
        try:
            page = self.core.web_reader.fetch(url)
        except WebError as exc:
            return f"Error: {exc}"
        return (f"# {page.title}\n" if page.title else "") + f"{page.url}\n\n{page.text}" + (
            "\n\n(page truncated)" if page.truncated else "")

    def _code_search(self, query: str) -> str:
        from .webread import WebError
        if self.core.web_reader is None:
            return "Error: web access is turned off in ALEX settings"
        try:
            results = self.core.web_reader.search(query)
        except WebError as exc:
            return f"Error: {exc}"
        return "\n\n".join(f"{i}. {r.title}\n{r.url}\n{r.snippet}" for i, r in enumerate(results, 1)) or "No results found."

    def spawn_restarter(self) -> None:
        """'다시 켜기' — 창 없는 실행기(launcher --restart)를 이 웹과 떨어진 프로세스로 띄운다. 실행기는 이 웹이 꺼지길
        기다렸다가 새 코드로 웹만 다시 켠다(Strata·임베딩은 그대로)."""
        import subprocess
        import sys
        python = Path(sys.executable)
        windowed = python.with_name("pythonw.exe")
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        subprocess.Popen([str(windowed if windowed.exists() else python), "-m", "jarvis.launcher", "--restart"],
                         cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)

    def close_owned_services(self) -> dict[str, Any]:
        """Shared Ollama and pre-existing ComfyUI services remain independent."""
        services = {"ollama": "preserved", "comfyui": "not_owned"}
        warnings = []
        self.speech.close()  # 실시간 통역 받아쓰기 일꾼(ADR-111)
        if self.code is not None and self.code.mcp is not None:
            self.code.mcp.close()
        if self.core.image_generator is not None:
            try:
                if self.core.image_generator.close():
                    services["comfyui"] = "stopped"
            except ImageGenError as exc:
                services["comfyui"] = "failed"
                warnings.append(str(exc))
        return {"services": services, "warnings": warnings}

    def snapshot(self, viewer: Viewer | None = None, session_id: str | None = None) -> dict[str, Any]:
        viewer = viewer or self.owner_viewer()
        if session_id and self.owns(viewer, session_id):
            sid = session_id
            if sid != self.current_session(viewer):
                self.set_current(viewer, sid)  # 목록에서 고른 대화 — 다시 열면 여기서 이어진다
        else:
            sid = self.current_session(viewer)
        storage = self.core.storage
        providers = [self.core.providers[d] for d in (Destination.LOCAL_GENERAL, Destination.LOCAL_CODER)]
        mine = self.ticket_for(viewer.id, sid)
        data = {
            "session_id": sid, "title": storage.session_title(sid), "cloud_web_approval": False, "local_only": True,
            "permission": self.session_permission(viewer, sid),
            "user": viewer.public(), "is_admin": viewer.is_admin,
            # 왼쪽 목록은 프로젝트 밖 대화만. 프로젝트 대화는 그 프로젝트 화면에 모인다(Claude·ChatGPT 프로젝트처럼).
            "sessions": [asdict(s) for s in storage.list_sessions(100, owner=viewer.id, project_id="")],
            "projects": [{key: p[key] for key in ("id", "name", "session_count", "updated_at")}
                         for p in storage.list_projects(viewer.id)],
            "project": ({key: project[key] for key in ("id", "name")}
                        if (project := storage.session_project(sid)) else None),
            **storage.message_page(sid),
            "tasks": [asdict(t) for t in storage.list_tasks(include_done=True, owner=viewer.id)],
            "memories": storage.list_memories(100, owner=viewer.id),
            "profile": self.core.hardware.profile.value,
            "models": [p.name for p in providers],
            "backends": {
                "general": _provider_backend(providers[0]),
                "coder": _provider_backend(providers[1]),
            },
            "mock": not isinstance(providers[0], OllamaProvider),
            "busy": self.active_id is not None,
            # 다른 사람의 요청 번호는 내주지 않는다 — 중단은 자기 요청만
            "request_id": mine.request_id if mine else None,
            "queue": self.queue_info(viewer, sid),
            "jobs": self.job_list(viewer),
            "active_kind": self.active_kind, "shutting_down": self.shutting_down,
        }
        if not viewer.is_admin:
            return data
        data.update({
            "project_root": str(self.core.current_project_root() or ""), "gpu": self.core.hardware.gpu,
            "effective_settings": {
                "general": provider_runtime_settings(providers[0]),
                "coder": provider_runtime_settings(providers[1]),
                "shared": providers[0] is providers[1],
            },
            "settings": {k: getattr(self.settings, k) for k in LOCAL_FIELDS} if self.settings else {},
            # 지금 적용 중인 입력 한도와, 엔진을 고르면 바뀔 한도(설정 창에 미리 보인다)
            "context_limits": {"chars": self.core.context_char_budget, "history": self.core.history_limit},
            "engine_limits": {backend: dict(zip(("chars", "history"), engine_context_limits(
                replace(self.settings, local_backend=backend), self.core.hardware.profile)[:2]))
                for backend in ("strata", "ollama")} if self.settings else {},
        })
        return data

    def model_status(self, backend: str | None = None) -> dict:
        from .model_registry import get_candidates, get_ollama_storage_info, estimate_memory_fit
        from .model_switch import stored_ollama_models
        
        p = self.core.providers[Destination.LOCAL_GENERAL]
        c = self.core.providers[Destination.LOCAL_CODER]
        preview = backend is not None and backend != _provider_backend(p)
        if backend is not None and backend not in {"strata", "ollama"}:
            raise ValueError("지원하지 않는 추론 엔진입니다.")
        if preview:
            if self.settings is None:
                raise ValueError("설정 변경을 지원하지 않는 실행 모드입니다.")
            p, c, _ = build_providers(replace(self.settings, local_backend=backend), self.core.hardware)
        local = isinstance(p, OllamaProvider)
        installed = p._fetch_installed_models() if local else set()
        loaded = p._fetch_loaded_models() if local else set()
        offline = False
        can_start = False
        startup_error = ""
        if preview and isinstance(p, StrataProvider) and not installed:
            from .model_switch import check_backend_install
            try:
                check_backend_install(replace(self.settings, local_backend="strata"))
                can_start = True
            except ValueError as exc:
                startup_error = str(exc)
        if preview and isinstance(p, OllamaProvider) and not isinstance(p, StrataProvider) and not installed:
            installed = stored_ollama_models(p.host)
            offline = bool(installed)
        candidates = get_candidates(self.core.hardware)
        
        # Only a store path confirmed by the launcher is safe to label as Ollama's.
        # An already-running external Ollama may use a different drive entirely.
        models_dir_env = os.environ.get("JARVIS_OLLAMA_MODELS")
        models_dir = Path(models_dir_env) if models_dir_env else None
        storage_info = get_ollama_storage_info(models_dir)
        
        catalog = []
        for cand in candidates:
            catalog.append({
                "tag": cand.tag,
                "params_b": cand.params_b,
                "active_params_b": cand.active_params_b,
                "quantization": cand.quantization,
                "download_size_gb": cand.download_size_gb,
                "family": cand.family,
                "source_url": cand.source_url,
                "last_verified": cand.last_verified,
                "architecture": cand.architecture,
                "ngram_embedding_params_b": cand.ngram_embedding_params_b,
                "memory_estimate": estimate_memory_fit(cand, self.core.hardware),
            })
            
        # Manual refresh bypasses the health cache after a model download.
        if local:
            p._last_health_at = 0
        if isinstance(c, OllamaProvider):
            c._last_health_at = 0
            
        return {
            "backend": _provider_backend(p),
            "installed": sorted(installed),
            "loaded": sorted(loaded),
            "selected_general": p.model if local else "",
            "selected_coder": c.model if isinstance(c, OllamaProvider) else "",
            "catalog": catalog,
            "storage": storage_info,
            "mock": not local,
            "offline_store": offline, "preview": preview,
            "can_start": can_start, "startup_error": startup_error,
        }

    def _check_inactive_engines(self) -> None:
        from .providers import ProviderError, loaded_local_models
        for provider in self.inactive_providers:
            try:
                remaining = loaded_local_models(provider)
            except ProviderError as exc:
                self.handoff_blocked = True
                raise ProviderError("이전에 사용한 엔진의 GPU 반환 상태를 확인하지 못했습니다. "
                                    "모델 설정에서 엔진 전환을 다시 시도하세요.") from exc
            if remaining:
                self.handoff_blocked = True
                raise ProviderError("다른 엔진에 모델이 올라가 있어 생성을 중단했습니다. "
                                    "모델 설정에서 엔진 전환을 다시 시도하세요.")

    def free_models(self) -> list[dict]:
        """OpenCode 무료 모델(jarvis/freemodels.py) — 하루 한 번 새로 받아 data 에 둔다."""
        from .freemodels import load_free_models
        data_dir = self.settings.data_dir if self.settings is not None else self.core.storage.db_path.parent
        return load_free_models(Path(data_dir) / "free-models.json")

    def coding_status(self) -> dict:
        """설정 화면의 '코딩 에이전트 · API' 칸(관리자) — 일꾼·교훈·자동 학습·배울 기록 수."""
        from .developer import DeveloperMode
        try:
            pending = DeveloperMode(self.core).learning_pending()
        except Exception:  # noqa: BLE001 — 기록 파일을 못 읽어도 화면은 뜬다
            pending = 0
        return {"worker": getattr(self.settings, "coding_worker", "opencode") if self.settings else "opencode",
                "free_model": getattr(self.settings, "coding_free_model", "") if self.settings else "",
                "free_models": self.free_models(),
                "insights": bool(getattr(self.core, "coding_insights", False)),
                "auto_learn": bool(getattr(self.core, "coding_auto_learn", False)), "pending": pending,
                "auto_learn_min": AUTO_LEARN_MIN, "sessions": len(self.code.sessions) if self.code else 0}

    def save_coding_settings(self, changes: dict) -> dict:
        """교훈 붙이기·쉬는 중 자동 학습·코딩 모델(OpenCode 무료 모델) — 저장하고 바로 적용(다시 켤 필요 없음)."""
        if self.settings is None:
            raise ValueError("설정 변경을 지원하지 않는 실행 모드입니다.")
        allowed = {"coding_insights_enabled": bool, "coding_auto_learn_enabled": bool, "coding_free_model": str}
        if not isinstance(changes, dict) or not changes or any(k not in allowed or type(v) is not allowed[k] for k, v in changes.items()):
            raise ValueError("지원하지 않는 설정 또는 잘못된 형식입니다.")
        if "coding_free_model" in changes and (self.active_id is not None or self.queue):
            raise ValueError("작업 중에는 코딩 모델을 바꿀 수 없습니다. 끝난 뒤 바꿔 주세요.")
        for k in changes:
            if "JARVIS_" + k.upper() in os.environ:
                raise ValueError(f"{k}: 환경변수로 고정되어 있습니다. 환경변수를 해제하고 재시작하세요.")
        candidate = validate(replace(self.settings, **changes))
        path = local_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({k: getattr(candidate, k) for k in LOCAL_FIELDS}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        self.settings = candidate
        self.core.coding_insights = candidate.coding_insights_enabled
        self.core.coding_auto_learn = candidate.coding_auto_learn_enabled
        if "coding_free_model" in changes:
            from .app import apply_coding_worker
            apply_coding_worker(self.core, candidate)
        return self.coding_status()

    def save_settings(self, changes: dict) -> dict:
        if self.settings is None:
            raise ValueError("설정 변경을 지원하지 않는 실행 모드입니다.")
        allowed = {"general_model", "coder_model", "desktop_general_model", "desktop_coder_model",
                   "auto_profile_models", "single_model_on_laptop", "local_max_output_tokens",
                   "desktop_max_output_tokens", "ollama_think", "num_ctx", "local_backend", "strata_max_output_tokens",
                   "strata_reasoning_effort"}
        if not isinstance(changes, dict) or not changes or any(k not in allowed or type(v) is not LOCAL_FIELDS[k] for k, v in changes.items()):
            raise ValueError("지원하지 않는 설정 또는 잘못된 형식입니다.")
        if changes.get("local_backend", self.settings.local_backend) == "strata" and any(k not in {
                "local_max_output_tokens", "desktop_max_output_tokens", "ollama_think", "local_backend",
                "strata_max_output_tokens", "strata_reasoning_effort"} for k in changes):
            raise ValueError("Strata는 서버에 설정된 모델 하나를 대화·코딩에 공유합니다. "
                             "여기서는 출력 길이와 Thinking·추론 수준만 변경할 수 있습니다. "
                             "다른 모델 선택은 Ollama 모드에서 지원합니다.")
        for k in changes:
            if "JARVIS_" + k.upper() in os.environ:
                raise ValueError(f"{k}: 환경변수로 고정되어 있습니다. 환경변수를 해제하고 재시작하세요.")
        candidate = validate(replace(self.settings, **changes))
        switching = candidate.local_backend != self.settings.local_backend or bool(self.handoff_blocked and "local_backend" in changes)
        from .model_switch import check_backend_install, release_current, start_backend
        from .app import effective_local_models
        from .opencode_worker import OpenCodeWorker
        previous = [p for p in self.core.providers.values() if isinstance(p, OllamaProvider)]
        released = []
        general = coder = None
        owns_switch = False
        path = local_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            if switching:
                if self.active_id is not None:
                    raise ValueError("작업 중에는 추론 엔진을 전환할 수 없습니다.")
                check_backend_install(candidate)
                self.active_id = secrets.token_hex(12)
                self.active_kind = "model_switch"
                owns_switch = True
                released = release_current([*previous, *self.inactive_providers])
                start_backend(candidate, self.core.hardware.profile, effective_local_models(candidate, self.core.hardware.profile))
            general, coder, notes = build_providers(candidate, self.core.hardware)
            for provider in {general, coder}:
                if not provider.health_check():
                    raise ValueError(f"선택한 엔진/모델을 사용할 수 없습니다: {provider.name}")
            # OpenCode는 새 모델로 다시 만든다. 하네스는 provider_getter로 아래의 교체를 그대로 따른다.
            worker = self.core.opencode_worker
            if isinstance(worker, OpenCodeWorker) and not worker.free:  # 무료 모델 일꾼은 이 PC 모델과 상관없어 그대로
                if candidate.local_backend == "ollama":
                    candidate = replace(candidate, opencode_model=coder.model)
                worker = OpenCodeWorker(worker.home, coder.model, context=coder.num_ctx,
                                       timeout=candidate.opencode_timeout, host=coder.host,
                                       backend=candidate.local_backend, command=worker.command)
            temporary.write_text(json.dumps({k: getattr(candidate, k) for k in LOCAL_FIELDS}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except Exception:
            if switching and general is not None:
                # Keep the old settings/providers; prevent a ready target from competing with them.
                try:
                    release_current([general, coder])
                finally:
                    targets = [p for p in (general, coder) if isinstance(p, OllamaProvider)]
                    self.inactive_providers = list({(type(p), p.host): p for p in [*self.inactive_providers, *targets]}.values())
                    self.handoff_blocked = True
                    self.core.local_engine_guard = self._check_inactive_engines
            raise
        finally:
            temporary.unlink(missing_ok=True)
            if owns_switch:
                self.active_id = None
                self.active_kind = None
        if switching:
            self.inactive_providers = list({(type(p), p.host): p for p in [*previous, *self.inactive_providers]
                                           if not (type(p) is type(general) and p.host == general.host)}.values())
            self.core.local_engine_guard = self._check_inactive_engines
            self.handoff_blocked = False
        self.settings = candidate
        # 엔진을 바꾸면 입력 한도도 그 엔진 값으로(Strata 64K 창 ↔ Ollama 16K 기준)
        (self.core.context_char_budget, self.core.history_limit,
         self.core.summary_trigger_messages) = engine_context_limits(candidate, self.core.hardware.profile)
        self.core.providers[Destination.LOCAL_GENERAL] = general
        self.core.providers[Destination.LOCAL_CODER] = coder
        self.core.local_summarizer = build_summary_provider(general, candidate)
        self.core.opencode_worker = worker
        self.core.bind_gpu_guards()
        return {"saved": True, "notes": notes, "released_models": released}

    def register_workspace(self, path: str, confirmed: bool) -> dict:
        """Only the explicit UI endpoint can register a new root; model text cannot."""
        if confirmed is not True or not isinstance(path, str) or not path.strip():
            raise ValueError("프로젝트 경로와 명시적 확인이 필요합니다.")
        root = Path(path).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("존재하는 프로젝트 폴더의 전체 경로를 입력하세요.")
        root = root.resolve()
        self.core.storage.set_state("web.workspace_root", str(root))
        self.core._approved_file_root = root
        self.core.set_project_root(".")
        if self.settings:
            self.settings = replace(self.settings, file_search_root=root)
        return {"project_root": str(root)}

    def delete_session(self, target: Any, confirmed: Any, viewer: Viewer | None = None,
                       viewing: Any = None) -> dict[str, Any]:
        """대화 목록의 [×]. 2026-09-26 사용자: "대화목록 제거 기능도 필요할거 같은데" — `/delete-session ID --yes` 를
        외워 치지 않게 한다. 지우는 범위는 그 명령과 같다(대화·메시지·라우팅 기록). 거기서 배운 것(교훈)은 남는다."""
        viewer = viewer or self.owner_viewer()
        if confirmed is not True:
            raise ValueError("삭제 확인이 필요합니다.")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("삭제할 대화가 필요합니다.")
        target = target.strip()
        current = viewing if self.owns(viewer, viewing) else self.current_session(viewer)
        # 다른 사람의 대화는 없는 것과 같다
        with self.queue_cv:
            if self.ticket_for(viewer.id, target) is not None:
                raise ValueError("이 대화에 처리 중인 요청이 있습니다. 완료 또는 중단 후 삭제하세요.")
            if not self.owns(viewer, target) or not self.core.storage.delete_session(target):
                raise ValueError("대화를 찾을 수 없습니다. 새로고침해 주세요.")
        self.core.forget_session_documents(target)  # 그 대화에만 올린 문서도 지운다(ADR-096)
        for exchange in (self.core.last_exchange, *(e[0] for e in self.exchanges.copy().values())):
            if (exchange or {}).get("session_id") == target:
                exchange.clear()  # 지운 대화의 질문·답을 메모리에도 남기지 않는다
        if not self.core.last_exchange:
            self.core.last_exchange = None
        if target == current:
            current = self.core.get_or_create_session(resume=False, owner=viewer.id)
            if self.exchange_owner == viewer.id:
                self.core.last_route_event_id = None
            self.set_current(viewer, current)
        elif viewer.id == OWNER and target == self.session_id:
            self.set_current(viewer, current)
        return {"deleted": target, "session_id": current}

    def archive_sync(self) -> None:
        """월별 대화 기록에 새 메시지를 덧붙인다(ADR-100). 기록이 실패해도 대화는 계속한다.
        새해가 되어 지난해 보고서가 없으면 뒤에서 만든다 — 요약 한 번마다 lock 을 잡으므로 그사이 대화가 끼어들 수 있다."""
        try:
            self.core.archive.sync()
            year = self.core.archive.needs_year_report()
        except Exception as exc:  # noqa: BLE001 — 기록 실패가 답을 막지 않게
            self.core.logger.warning("월별 대화 기록 실패: %s", exc)
            return
        if year and not (self.year_report and self.year_report.is_alive()):
            self.year_report = threading.Thread(target=self._write_year_report, args=(year,), daemon=True,
                                                name="alex-year-report")
            self.year_report.start()

    def _write_year_report(self, year: int) -> None:
        summarizer = self.core.local_summarizer

        def generate(messages):
            with self.lock:
                return summarizer.generate(messages)
        try:
            path = self.core.archive.year_report(year, generate)
            self.core.logger.info("연간 보고서: %s", path)
        except Exception as exc:  # noqa: BLE001 — 다음 대화 뒤에 다시 시도
            self.core.logger.warning("연간 보고서 실패: %s", exc)

    # ---------------------------------------------------------------- 대화에 올린 문서(ADR-096)
    def attach_documents(self, sid: str, message: str, documents: list[str]) -> str:
        """보내기 전 첨부(draft)를 이 대화의 자료로 옮기고, 질문 끝에 '[첨부 문서: …]' 줄을 붙인다(대화 기록에 남는다)."""
        if message.strip().startswith("/"):
            raise ValueError("문서 첨부와 명령어는 따로 보내 주세요.")
        lines = []
        for document_id in documents:
            record = self.core.knowledge.move_scope(document_id, f"draft:{sid}", f"session:{sid}")
            title = re.sub(r"[\[\]\r\n]", " ", record["title"])[:120]
            lines.append(f"[첨부 문서: {title} · {record['pages']}쪽 · {record['chars']:,}자]")
        return message.rstrip() + "\n\n" + "\n".join(lines)

    def discard_document(self, viewer: Viewer, sid: Any, document_id: Any) -> dict[str, Any]:
        """보내기 전에 첨부를 빼면 지운다(다른 곳에서도 쓰는 자료면 이 첨부에서만 뺀다)."""
        if not isinstance(sid, str) or not self.owns(viewer, sid):
            raise ValueError("대화를 찾을 수 없습니다. 새로고침해 주세요.")
        self.core.knowledge.forget(str(document_id or ""), scope=f"draft:{sid}")
        return {"discarded": document_id}

    # ---------------------------------------------------------------- 권한(ADR-095)
    def session_permission(self, viewer: Viewer, sid: str) -> str:
        """이 대화의 권한. 저장된 값이 없거나 이 사람이 고를 수 없는 값이면 기본(확인 후 실행)."""
        mode = self.core.storage.get_state(f"permission:{sid}") or DEFAULT_PERMISSION
        if mode not in PERMISSION_MODES or (mode == "auto" and not viewer.is_admin):
            return DEFAULT_PERMISSION
        return mode

    def set_permission(self, viewer: Viewer, sid: str, value: Any) -> str:
        mode = clean_permission(value)
        if mode == "auto" and not viewer.is_admin:
            raise ValueError("자동 실행은 관리자만 고를 수 있습니다.")
        if not self.owns(viewer, sid):
            raise ValueError("대화를 찾을 수 없습니다. 새로고침해 주세요.")
        self.core.storage.set_state(f"permission:{sid}", mode)
        return mode

    def reset_permission(self, sid: str) -> None:
        """새 대화는 언제나 기본 권한으로 시작한다(빈 대화를 다시 쓸 때 예전 '자동 실행' 이 남지 않게)."""
        if self.core.storage.get_state(f"permission:{sid}") not in (None, DEFAULT_PERMISSION):
            self.core.storage.set_state(f"permission:{sid}", DEFAULT_PERMISSION)

    def process(self, message: str, attachments: list[str] | None = None, viewer: Viewer | None = None,
                session_id: str | None = None, permission: str | None = None,
                documents: list[str] | None = None) -> dict[str, Any]:
        viewer = viewer or self.owner_viewer()
        text = message.strip()
        if not text:
            raise ValueError("메시지가 비어 있습니다.")
        if len(text) > 32_000:
            raise ValueError("메시지가 너무 깁니다. 32000자 이하로 줄여 주세요.")

        with self.lock:
            user = self.core.storage.get_user(viewer.id)
            if user is None or user.disabled:
                raise PermissionError("사용 중지된 계정입니다. 다시 로그인해 주세요.")
            viewer = Viewer(user, viewer.remote)
            sid = session_id or self.current_session(viewer)
            if not self.owns(viewer, sid):
                raise ValueError("대화를 찾을 수 없습니다. 새로고침해 주세요.")
            # 보낼 때 고른 권한으로 처리한다(기다리는 동안 바꿔도 이 요청에는 보낼 때 값). 끝나면 기본으로.
            self.core.permission_mode = (self.set_permission(viewer, sid, permission) if permission is not None
                                         else self.session_permission(viewer, sid))
            try:
                if documents:
                    message = self.attach_documents(sid, message, documents)
                return self._process_in_session(message, attachments, viewer, sid)
            finally:
                self.core.permission_mode = DEFAULT_PERMISSION
                self.archive_sync()

    def _process_in_session(self, message: str, attachments: list[str] | None, viewer: Viewer,
                            sid: str) -> dict[str, Any]:
        text = message.strip()
        with self.acting_as(viewer, sid):
            before_count = self.core.storage.message_count(sid)
            if attachments:
                if text.startswith("/"):
                    raise ValueError("이미지 첨부와 명령어는 따로 보내 주세요. 이미지에는 일반 질문을 입력해 주세요.")
                images = [self.attachments.load(sid, name) for name in attachments]
                response, decision = self.core.handle_images(sid, text, images)
                if before_count == 0:
                    self.core.storage.rename_session(sid, provisional_title(text))
                result = {"response": response, "route": decision.destination.value, "session_id": sid,
                          "attachment_error": self.core._answer_failed, "new_conversation": before_count == 0}
            elif text.startswith("/"):
                if text.startswith("/delete-session ") and text.endswith(" --yes"):
                    target = text.removeprefix("/delete-session ")[:-6].strip()
                    target = sid if target == "current" else target
                    with self.queue_cv:
                        if any(t.user_id == viewer.id and t.session_id == target and t is not self.running
                               for t in self.queue):
                            return {"response": "이 대화에 처리 중인 요청이 있습니다. 완료 또는 중단 후 삭제하세요.",
                                    "route": "command", "session_id": sid}
                command = text.split(None, 1)[0]
                if not viewer.is_admin and command not in MEMBER_COMMANDS:
                    output, keep_running, new_session = "이 명령은 관리자만 쓸 수 있습니다.", True, sid
                elif self.core.permission_mode == "read" and command in READ_ONLY_BLOCKED_COMMANDS:
                    output, keep_running, new_session = READ_ONLY_BLOCKED, True, sid
                else:
                    output, keep_running, new_session = handle_command(self.core, sid, text)
                    if self.core.permission_mode == "auto":
                        output = auto_apply_proposals(self.core, output)
                if not self.owns(viewer, new_session):
                    new_session = sid
                elif new_session != sid:
                    self.reset_permission(new_session)  # /new·/use 로 옮긴 대화는 기본 권한에서
                # 다른 방을 보는 동안 끝난 요청이 현재 대화 선택을 되돌리지 않는다.
                if new_session != sid and self.current_session(viewer) == sid:
                    self.set_current(viewer, new_session)
                if not keep_running:  # Web server 자체를 /exit 로 종료하지 않는다.
                    output = "Web UI에서는 /exit로 서버를 종료하지 않습니다. 터미널에서 Ctrl+C를 사용하세요."
                result = {"response": output, "route": "command", "session_id": new_session}
            else:
                response, decision = self.core.handle(sid, text)
                if before_count == 0:
                    self.core.storage.rename_session(sid, provisional_title(text))
                result = {"response": response, "route": decision.destination.value, "session_id": sid,
                          "new_conversation": before_count == 0}
            # 이 답에 나온 그림·영상·문서는 이 사람 것이다 — 다른 사람이 이름을 맞혀도 받지 못한다
            self.core.storage.record_artifacts(ARTIFACT_RE.findall(result["response"]), viewer.id)
            return result

    def auto_title(self, result: dict[str, Any], question: str) -> str:
        """새 대화의 첫 답이 끝난 뒤 모델이 제목을 짓는다(state.lock 안에서 — 다음 사람은 1~2초 더 기다린다)."""
        if not result.get("new_conversation") or self.core._answer_failed:
            return ""
        title = self.core.suggest_title(question, result.get("response", ""))
        if title:
            self.core.storage.rename_session(result["session_id"], title)
        return title

    # ---------------------------------------------------------------- 계정
    def login(self, login: Any, password: Any, remote: bool) -> tuple[Viewer, str]:
        # Password hashing releases the GIL. Keep failure counts and login issuance
        # together without taking the model lock or losing concurrent failures.
        with self.login_lock:
            return self._login_locked(login, password, remote)

    def _login_locked(self, login: Any, password: Any, remote: bool) -> tuple[Viewer, str]:
        name = str(login or "").strip().lower()
        user = self.core.storage.user_by_login(name) if name else None
        # Unknown names share one bucket: random names cannot grow the cache or
        # clear the lock of an existing account. NUL cannot be a real login ID.
        failure_key = name if user is not None else "\0unknown"
        failures, until = self.login_failures.get(failure_key, (0, 0.0))
        if until > time.monotonic():
            raise PermissionError("로그인 시도가 너무 많습니다. 1분 뒤 다시 시도하세요.")
        if until:
            failures = 0  # 잠금이 풀리면 다시 다섯 번
        if (user is None or user.disabled or not user.password_hash
                or not accounts.verify_password(str(password or ""), user.password_hash)):
            failures += 1
            self.login_failures[failure_key] = (failures, time.monotonic() + LOGIN_LOCK_SECONDS
                                         if failures >= LOGIN_LOCK_AFTER else 0.0)
            raise PermissionError("아이디 또는 비밀번호가 맞지 않습니다.")
        self.login_failures.pop(failure_key, None)
        token = accounts.new_session_token()
        expires = datetime.now(timezone.utc) + timedelta(days=accounts.SESSION_DAYS)
        self.core.storage.create_auth_session(accounts.token_hash(token), user.user_id, expires.isoformat())
        return Viewer(user, remote), token

    def change_password(self, viewer: Viewer, current: Any, new: Any) -> None:
        user = self.core.storage.get_user(viewer.id)
        if user is None:
            raise ValueError("계정을 찾을 수 없습니다.")
        if user.password_hash and not accounts.verify_password(str(current or ""), user.password_hash):
            raise ValueError("지금 비밀번호가 맞지 않습니다.")
        self.core.storage.update_user(viewer.id, password_hash=accounts.hash_password(str(new or "")))

    # ---------------------------------------------------------------- 프로젝트
    def project_view(self, viewer: Viewer, project_id: Any) -> dict[str, Any]:
        project = self.core.storage.get_project(str(project_id or ""), owner=viewer.id)
        if project is None:
            raise ValueError("프로젝트를 찾을 수 없습니다.")
        files = self.core.knowledge.documents(scope=f"project:{project['id']}")
        return {"project": project,
                "sessions": [asdict(s) for s in self.core.storage.list_sessions(200, owner=viewer.id,
                                                                                  project_id=project["id"])],
                "files": [{key: d[key] for key in ("id", "title", "pages", "chars", "created_at")} for d in files]}

    def manage_project(self, viewer: Viewer, body: dict) -> dict[str, Any]:
        storage, action = self.core.storage, body.get("action")
        if action == "create":
            project_id = storage.create_project(viewer.id, body.get("name") or "", body.get("instructions") or "")
            return self.project_view(viewer, project_id)
        project = storage.get_project(str(body.get("id") or ""), owner=viewer.id)
        if project is None:
            raise ValueError("프로젝트를 찾을 수 없습니다.")
        if action == "update":
            storage.update_project(project["id"], viewer.id, body.get("name"), body.get("instructions"))
        elif action == "new_chat":
            sid = storage.create_session(viewer.id, project_id=project["id"])
            self.set_current(viewer, sid)
            return {"session_id": sid}
        elif action == "unlink_file":
            try:
                self.core.knowledge.forget(str(body.get("document_id") or ""), scope=f"project:{project['id']}")
            except KnowledgeError as exc:
                raise ValueError(str(exc)) from exc
        elif action == "delete":
            if body.get("confirmed") is not True:
                raise ValueError("삭제 확인이 필요합니다.")
            for document in self.core.knowledge.documents(scope=f"project:{project['id']}"):
                self.core.knowledge.forget(document["id"], scope=f"project:{project['id']}")
            removed = storage.delete_project(project["id"], viewer.id)
            current = self.current_session(viewer) if viewer.id != OWNER else self.session_id
            if current in removed or not self.owns(viewer, current):
                self.set_current(viewer, self.core.get_or_create_session(resume=False, owner=viewer.id))
            return {"deleted": project["id"], "session_id": self.current_session(viewer)}
        else:
            raise ValueError("알 수 없는 작업입니다.")
        return self.project_view(viewer, project["id"])

    def users(self) -> list[dict[str, Any]]:
        return [{"id": u.user_id, "name": u.name, "login": u.login, "role": u.role, "disabled": u.disabled,
                 "has_password": bool(u.password_hash), "tailscale_login": u.tailscale_login,
                 "created_at": u.created_at} for u in self.core.storage.list_users()]

    def manage_user(self, viewer: Viewer, body: dict) -> dict[str, Any]:
        """관리자: 사용자 만들기·이름/역할/Tailscale 연결 바꾸기·비밀번호 다시 정하기·사용 중지."""
        storage, action = self.core.storage, body.get("action")
        if action == "create":
            user = storage.create_user(accounts.clean_name(body.get("name")), accounts.clean_login(body.get("login")),
                                       accounts.hash_password(str(body.get("password") or "")),
                                       role=body.get("role") if body.get("role") in accounts.ROLES else "member",
                                       tailscale_login=str(body.get("tailscale_login") or ""))
            return {"created": user.user_id, "users": self.users()}
        target = body.get("id")
        user = storage.get_user(target) if isinstance(target, str) else None
        if user is None:
            raise ValueError("사용자를 찾을 수 없습니다.")
        if action == "update":
            fields: dict[str, Any] = {}
            if "name" in body:
                fields["name"] = accounts.clean_name(body["name"])
            if "tailscale_login" in body:
                fields["tailscale_login"] = str(body["tailscale_login"] or "")
            if "role" in body:
                if body["role"] not in accounts.ROLES or (user.user_id in (OWNER, viewer.id) and body["role"] != "admin"):
                    raise ValueError("이 PC 주인과 자기 자신의 관리자 권한은 뺄 수 없습니다.")
                fields["role"] = body["role"]
            if not fields:
                raise ValueError("바꿀 내용이 없습니다.")
            storage.update_user(user.user_id, **fields)
        elif action == "password":
            storage.update_user(user.user_id, password_hash=accounts.hash_password(str(body.get("password") or "")))
        elif action in ("disable", "enable"):
            if user.user_id in (OWNER, viewer.id):
                raise ValueError("이 PC 주인과 자기 자신은 사용 중지할 수 없습니다.")
            storage.update_user(user.user_id, disabled=int(action == "disable"))
        else:
            raise ValueError("알 수 없는 작업입니다.")
        return {"users": self.users()}


def _handler_factory(state: WebAppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JarvisLocal/0.3.0"

        def log_message(self, format: str, *args: object) -> None:
            # HTTP access log에 query/body/token을 남기지 않는다.
            return

        def _security_headers(self, content_type: str) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; "
                "connect-src 'self'; img-src blob:; media-src blob:; frame-ancestors 'none'; base-uri 'none'; "
                "manifest-src 'self'; worker-src 'self'",  # 그림은 토큰으로 받아 blob 으로만 보인다(ADR-073). 앱 아이콘은 설치 때 브라우저가 따로 받는다
            )

        def _json(self, status: int, payload: dict[str, Any], cookies: tuple[str, ...] = ()) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._security_headers("application/json; charset=utf-8")
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_bytes(self, content_type: str, body: bytes, disposition: str = "") -> None:
            self.send_response(HTTPStatus.OK)
            self._security_headers(content_type)
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ------------------------------------------------------------ 누가 보냈나
        def _host(self) -> str:
            raw = self.headers.get("Host", "").strip().lower()
            if raw.startswith("[") and "]" in raw:
                return raw[1:raw.index("]")]
            return raw.split(":", 1)[0]

        def _proxied(self) -> bool:
            # Tailscale Serve 가 넘겨준 요청에는 이 머리글이 붙는다. 이 PC 의 브라우저는 붙이지 않는다 —
            # 밖에서 Host 를 127.0.0.1 로 꾸며 보내도 이 PC 주인으로 들어오지 못하게 한다.
            return any(self.headers.get(name) for name in ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host",
                                                           "Forwarded", "Tailscale-User-Login"))

        def _is_remote(self) -> bool:
            settings = state.settings
            return bool(settings and settings.remote_web_host) and self._host() == settings.remote_web_host.lower()

        def _valid_host(self) -> bool:
            # Binding to loopback alone does not stop DNS rebinding: a hostile domain
            # can resolve to 127.0.0.1 while keeping its Host header. Never expose the
            # tokenized page to non-local Host names.
            host = self._host()
            if host in {"127.0.0.1", "localhost", "::1"}:
                return not self._proxied()
            return self._remote_app_user(host) is not None

        def _remote_app_user(self, host: str) -> str | None:
            """휴대폰·밖에서 앱처럼 쓰기(2026-10-11): Tailscale Serve 로 들어온 허용된 사람의 요청만 받는다.
            설정한 ts.net 주소이고, Tailscale 이 붙인 로그인 머리글이 허용 목록(remote_web_users)에 있거나 ALEX 계정에
            연결된 Tailscale 계정일 때. Serve 는 이 PC 안에서 127.0.0.1 로 넘겨주므로 ALEX 는 계속 loopback 에서만 듣는다.
            Funnel(공개 인터넷)·태그 기기에는 이 머리글이 없다."""
            settings = state.settings
            if settings is None or not settings.remote_web_host or host != settings.remote_web_host.lower():
                return None
            login = self.headers.get("Tailscale-User-Login", "").strip().lower()
            if not login:
                return None
            allowed = {user.strip().lower() for user in settings.remote_web_users.split(",") if user.strip()}
            if login in allowed:
                return login
            user = state.core.storage.user_by_tailscale(login)
            return login if user is not None and not user.disabled else None

        def _host_guard(self) -> bool:
            if self._valid_host():
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local host"})
            return False

        def _cookies(self) -> dict[str, str]:
            try:
                jar = SimpleCookie(self.headers.get("Cookie", ""))
            except CookieError:
                return {}
            return {key: morsel.value for key, morsel in jar.items()}

        def _viewer(self) -> Viewer | None:
            """로그인 쿠키 → (밖) 연결된 Tailscale 계정 → (이 PC) 주인 순서로 찾는다. 없으면 로그인 화면."""
            storage = state.core.storage
            remote = self._is_remote()
            cookies = self._cookies()
            token = cookies.get(SESSION_COOKIE)
            if token:
                user_id = storage.auth_session_user(accounts.token_hash(token))
                user = storage.get_user(user_id) if user_id else None
                if user is not None and not user.disabled:
                    return Viewer(user, remote)
                if not remote:
                    return None  # an invalid/disabled login never becomes the local owner
            if remote:
                user = storage.user_by_tailscale(self.headers.get("Tailscale-User-Login", ""))
                return Viewer(user, True) if user is not None and not user.disabled else None
            if cookies.get(MANUAL_COOKIE) == "1":
                return None
            return state.owner_viewer()

        def _require_viewer(self, admin: bool = False) -> Viewer | None:
            viewer = self._viewer()
            if viewer is None:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "로그인이 필요합니다.", "login_required": True})
                return None
            if admin and not viewer.is_admin:
                self._json(HTTPStatus.FORBIDDEN, {"error": "관리자만 할 수 있습니다.", "admin_only": True})
                return None
            return viewer

        def _session_cookie(self, token: str | None) -> str:
            secure = "; Secure" if self._is_remote() else ""
            if token is None:
                return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure}"
            return (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                    f"Max-Age={accounts.SESSION_DAYS * 86400}{secure}")

        def _manual_cookie(self, on: bool) -> str:
            return f"{MANUAL_COOKIE}={'1' if on else ''}; Path=/; SameSite=Strict; Max-Age={31536000 if on else 0}"

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Jarvis-Token", ""), state.token)

        def _session_header(self, viewer: Viewer) -> str | None:
            """올리기 요청의 대화 — 그 사람의 대화가 아니면 None."""
            sid = self.headers.get("X-Jarvis-Session") or state.current_session(viewer)
            return sid if state.owns(viewer, sid) else None

        # ------------------------------------------------------------ 올리기
        def _upload_image(self, *, attachment: bool = False) -> None:
            """채팅창 '그림 올리기'(ADR-074 보충) — 그림 바이트를 그대로 받는다. 토큰 필수, PNG·JPEG·WebP, 10MB 까지."""
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            viewer = self._require_viewer()
            if viewer is None:
                return
            if not attachment and state.core.image_generator is None:
                self._json(HTTPStatus.CONFLICT, {"error": "그림 그리기가 꺼져 있습니다."})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "PNG·JPEG·WebP 그림만 올릴 수 있습니다."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
                return
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "그림이 너무 큽니다(10MB 이하)."})
                return
            data = self.rfile.read(length)
            if len(data) != length:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "이미지 전송이 끝나기 전에 연결이 끊겼습니다."})
                return
            sid = self._session_header(viewer)
            if sid is None:
                self._json(HTTPStatus.CONFLICT, {"error": "다른 탭에서 대화가 바뀌었습니다. 새로고침 후 다시 올려 주세요."})
                return
            try:
                if attachment:
                    # 첨부는 파일로만 저장한다 — 다른 사람의 답을 기다리지 않는다
                    image = state.attachments.store(sid, data, content_type)
                    self._json(HTTPStatus.OK, {"attachment": {"name": image.name, "width": image.width,
                                                              "height": image.height, "mime": image.mime,
                                                              "bytes": len(image.data)}, "session_id": sid})
                    return
                if not state.lock.acquire(blocking=False):
                    self._json(HTTPStatus.CONFLICT, {"error": "응답 생성 중입니다. 완료 또는 중단 후 다시 시도하세요."})
                    return
                try:
                    with state.acting_as(viewer, sid):
                        reply = state.core.add_reference_picture(sid, data, unquote(self.headers.get("X-File-Name", ""))[:120])
                finally:
                    state.lock.release()
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except OSError:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "이미지를 저장하지 못했습니다. 저장 공간을 확인해 주세요."})
                return
            self._json(HTTPStatus.OK, {"response": reply, "session_id": sid})

        def _upload_project_file(self) -> None:
            """프로젝트 자료 올리기 — 그 프로젝트 대화에서만 참고한다. 주인이면 누구나(관리자가 아니어도)."""
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            viewer = self._require_viewer()
            if viewer is None:
                return
            project = state.core.storage.get_project(self.headers.get("X-Project-Id", ""), owner=viewer.id)
            if project is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "프로젝트를 찾을 수 없습니다."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
                return
            if length <= 0 or length > MAX_DOCUMENT_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "자료는 20MB 이하여야 합니다."})
                return
            try:
                name = unquote(self.headers.get("X-File-Name", ""))
                state.core.knowledge._validate_name(name)
                data = self.rfile.read(length)
                if len(data) != length:
                    raise KnowledgeError("파일 전송이 완료되지 않았습니다.")
                record = state.core.knowledge.import_bytes(data, name, scope=f"project:{project['id']}")
                self._json(HTTPStatus.OK, {"document": {key: record[key] for key in ("id", "title", "pages", "chars")},
                                           **state.project_view(viewer, project["id"])})
            except (KnowledgeError, OSError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def _upload_document(self) -> None:
            """채팅창 클립으로 문서 올리기(ADR-096) — 그 대화에서만 참고한다. 보내기 전에는 draft 로 두었다가 보낼 때 대화로 옮긴다.
            누구나 자기 대화에. 모델 차례를 기다리지 않는다(PDF 는 별도 일꾼이 읽는다)."""
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            viewer = self._require_viewer()
            if viewer is None:
                return
            sid = self._session_header(viewer)
            if sid is None:
                self._json(HTTPStatus.CONFLICT, {"error": "대화가 바뀌었습니다. 새로고침 후 다시 올려 주세요."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
                return
            if length <= 0 or length > MAX_DOCUMENT_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "문서는 파일당 20MB 이하여야 합니다."})
                return
            try:
                name = unquote(self.headers.get("X-File-Name", ""))
                state.core.knowledge._validate_name(name)
                data = self.rfile.read(length)
                if len(data) != length:
                    raise KnowledgeError("파일 전송이 완료되지 않았습니다.")
                record = state.core.knowledge.import_bytes(data, name, source_path="대화 첨부", scope=f"draft:{sid}")
                self._json(HTTPStatus.OK, {"document": {key: record[key] for key in ("id", "title", "pages", "chars")}
                                           | {"empty_pages": json.loads(record["empty_pages"])}})
            except (KnowledgeError, OSError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def _upload_knowledge(self) -> None:
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            viewer = self._require_viewer(admin=True)  # 학습 자료는 모두가 함께 쓰므로 관리자만 더한다
            if viewer is None:
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
                return
            if length <= 0 or length > MAX_DOCUMENT_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "자료는 20MB 이하여야 합니다."})
                return
            if not state.lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "응답이 끝난 뒤 자료를 저장하세요."})
                return
            status = HTTPStatus.OK
            try:
                if state.shutting_down:
                    status, payload = HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."}
                elif self.headers.get("X-Jarvis-Session", "") == "" or self._session_header(viewer) is None:
                    status, payload = HTTPStatus.CONFLICT, {"error": "대화가 바뀌었습니다. 새로고침 후 다시 올려 주세요."}
                else:
                    name = unquote(self.headers.get("X-File-Name", ""))
                    state.core.knowledge._validate_name(name)
                    data = self.rfile.read(length)
                    if len(data) != length:
                        raise KnowledgeError("파일 전송이 완료되지 않았습니다.")
                    record = state.core.knowledge.import_bytes(data, name)
                    payload = {"response": import_notice(record), "document": {
                        key: record[key] for key in ("id", "title", "pages", "chars", "duplicate")}}
            except (KnowledgeError, OSError) as exc:
                status, payload = HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            finally:
                state.lock.release()
            self._json(status, payload)  # client can send the next request once it receives this

        # ------------------------------------------------------------ GET
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._host_guard():
                return
            if self.path == "/health":
                from .launcher import instance_id
                self._json(HTTPStatus.OK, {"app": "jarvis", "instance": instance_id(state.core.storage.db_path.parent),
                                           "local_only": state.core.local_only, "code": LOADED_CODE})
                return
            if self.path == "/":
                body = PAGE.replace("__TOKEN__", html.escape(state.token, quote=True)).encode("utf-8")
                self._send_bytes("text/html; charset=utf-8", body)
                return
            if self.path == "/live":  # 실시간 통역 화면(ADR-111) — 폰·AI 안경 브라우저용
                self._send_bytes("text/html; charset=utf-8",
                                 LIVE_PAGE.replace("__TOKEN__", html.escape(state.token, quote=True)).encode("utf-8"))
                return
            if self.path == "/code":  # 코딩 세션 화면(ADR-112) — 관리자만 쓰는 API 를 부른다
                self._send_bytes("text/html; charset=utf-8",
                                 CODE_PAGE.replace("__TOKEN__", html.escape(state.token, quote=True)).encode("utf-8"))
                return
            if self.path in CODE_FILES:
                self._send_bytes(CODE_FILES[self.path], (STATIC / self.path[1:]).read_bytes())
                return
            if self.path in LIVE_FILES:
                self._send_bytes(LIVE_FILES[self.path], (STATIC / self.path[1:]).read_bytes())
                return
            if self.path in APP_FILES:  # 홈 화면에 설치하는 앱(PWA) 파일: 매니페스트·서비스 워커·아이콘
                self._send_bytes(APP_FILES[self.path], (STATIC / self.path[1:]).read_bytes())
                return
            if self.path in {"/app.js", "/style.css", "/markdown.js"}:
                self._send_bytes("text/javascript; charset=utf-8" if self.path.endswith(".js") else "text/css; charset=utf-8",
                                 (STATIC / self.path[1:]).read_bytes())
                return
            if not self.path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            route, query = urlparse(self.path).path, parse_qs(urlparse(self.path).query)
            if route == "/api/me":
                viewer = self._viewer()
                remote = self._is_remote()
                self._json(HTTPStatus.OK, {"user": viewer.public() if viewer else None, "remote": remote,
                                           "can_use_owner": not remote})
                return
            if route.startswith("/api/code/"):
                self._code_api("GET", route, query, {})
                return
            if route == "/api/coding":  # 설정의 '코딩 에이전트 · API' 칸(관리자)
                viewer = self._require_viewer(admin=True)
                if viewer is not None:
                    self._json(HTTPStatus.OK, state.coding_status())
                return
            viewer = self._require_viewer(admin=route == "/api/users")
            if viewer is None:
                return
            if route == "/api/users":
                self._json(HTTPStatus.OK, {"users": state.users()})
                return
            if route == "/api/project":
                try:
                    self._json(HTTPStatus.OK, state.project_view(viewer, query.get("id", [""])[0]))
                except (ValueError, KnowledgeError) as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            if route == "/api/models":
                try:
                    backend = query.get("backend", [None])[0] if viewer.is_admin else None
                    status = state.model_status(backend)
                    if not viewer.is_admin:  # 모델 이름과 준비 상태만 — 카탈로그·저장 경로는 관리자 화면
                        status = {key: status[key] for key in ("backend", "selected_general", "mock")} | {
                            "installed": [m for m in status["installed"] if m == status["selected_general"]]}
                    self._json(HTTPStatus.OK, status)
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            name = query.get("name", [""])[0]
            if route in ("/api/document", "/api/video", "/api/image", "/api/downloaded") and (
                    not name or state.core.storage.artifact_owner(name) != viewer.id):
                self._json(HTTPStatus.NOT_FOUND, {"error": route.rsplit("/", 1)[1] + " not found"})
                return
            if route == "/api/document":
                try:
                    path = download_path(state.core.storage.db_path.parent, name)
                    if path is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "document not found"})
                        return
                    with path.open("rb") as stream:
                        body = stream.read(MAX_DOWNLOAD + 1)
                    if len(body) > MAX_DOWNLOAD:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "document not found"})
                        return
                except OSError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "document not found"})
                    return
                self._send_bytes(FORMATS[path.suffix[1:]], body, f'attachment; filename="{name}"')
                return
            if route == "/api/downloaded":
                # ADR-104: 말로 내려받은 파일 — data/downloads 바로 아래의 실제 파일만, 언제나 '저장' 으로(브라우저에서 열지 않음)
                path = downloaded_path(state.core.storage.db_path.parent / "downloads", unquote(name))
                if path is None or path.stat().st_size > MAX_SERVED_DOWNLOAD:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "file not found"})
                    return
                self._send_bytes("application/octet-stream", path.read_bytes(),
                                 "attachment; filename*=UTF-8''" + name)
                return
            if route == "/api/video":
                # ADR-075: 앱이 만든 MP4 만 — 이름 모양을 확인하고 data/videos 바로 아래에서만 찾는다.
                generator = state.core.image_generator
                path = generator.video_path(name) if generator is not None else None
                if path is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "video not found"})
                    return
                self._send_bytes("video/mp4", path.read_bytes())
                return
            if route == "/api/image":
                # ADR-073: 앱이 그린 PNG 만 내준다 — 이름 모양을 확인하고 data/images 바로 아래에서만 찾는다.
                generator = state.core.image_generator
                path = generator.image_path(name) if generator is not None else None
                if path is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "image not found"})
                    return
                self._send_bytes(IMAGE_TYPES.get(path.suffix.lower(), "application/octet-stream"), path.read_bytes())
                return
            sid = query.get("session_id", [None])[0] or state.current_session(viewer)
            if route == "/api/attachment":
                if not state.owns(viewer, sid):
                    self._json(HTTPStatus.CONFLICT, {"error": "대화가 바뀌었습니다. 새로고침해 주세요."})
                    return
                try:
                    image = state.attachments.load(sid, name)
                except (ValueError, OSError):
                    self._json(HTTPStatus.NOT_FOUND, {"error": "첨부 이미지를 찾을 수 없습니다."})
                    return
                self._send_bytes(image.mime, image.data)
                return
            if route == "/api/history":
                try:
                    if not state.owns(viewer, sid):
                        raise ValueError("not yours")
                    self._json(HTTPStatus.OK, state.core.storage.message_page(sid, int(query["before"][0])))
                except (KeyError, ValueError):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid history cursor"})
                return
            if route == "/api/chat-search":   # 옆 목록 '대화 검색' 이 메시지 본문까지(2026-10-26) — 내 대화만
                text = (query.get("q", [""])[0] or "").strip()[:200]
                self._json(HTTPStatus.OK, {"results": state.core.storage.search_messages(text, owner=viewer.id) if text else []})
                return
            if route == "/api/state":
                self._json(HTTPStatus.OK, state.snapshot(viewer, query.get("session_id", [None])[0]))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        # ------------------------------------------------------------ 대화(대기열)
        def _live_utterance(self) -> None:
            """실시간 통역의 말 한 토막(ADR-111): WAV → 받아쓰기(모델 잠금 없이, CPU) → heard → (한국어가 아니면) 차례를 기다려
            번역 → translation. 줄마다 JSON(NDJSON). 기록을 켜면 그 통역 대화에 원문·번역을 남긴다."""
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            viewer = self._require_viewer()
            if viewer is None:
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "audio/wav":
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "audio/wav 만 받습니다."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= 2_000_000:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "소리 토막이 비었거나 너무 큽니다."})
                return
            data = self.rfile.read(length)
            sid = self.headers.get("X-Live-Session", "")
            if not state.owns(viewer, sid):
                self._json(HTTPStatus.CONFLICT, {"error": "통역 대화를 찾지 못했습니다. 다시 시작해 주세요."})
                return
            try:
                check_wav(data)
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if state.shutting_down:
                self._json(HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."})
                return
            record = self.headers.get("X-Live-Record", "1") == "1"
            ticket, disconnected = None, False

            def emit(payload: dict) -> None:
                nonlocal disconnected
                if disconnected:
                    return
                try:
                    self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    disconnected = True
                    if ticket is not None:
                        ticket.cancel.set()
            self.send_response(HTTPStatus.OK)
            self._security_headers("application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                text, lang, took = state.speech.recognize(data)
                text = tidy(text, lang)
                if not meaningful(text):
                    emit({"type": "done", "empty": True, "stt": took})
                    return
                emit({"type": "heard", "text": text, "lang": lang, "label": LANG_LABEL.get(lang, lang), "stt": took})
                if lang not in TRANSLATE:  # 한국어(또는 모르는 말)는 받아 적은 그대로
                    if record:
                        state.core.storage.add_message(sid, "user", f"[{LANG_LABEL.get(lang, lang)}] {text}")
                    emit({"type": "done", "stt": took})
                    return
                ticket = Ticket(viewer.id, session_id=sid)
                with state.queue_cv:
                    state.queue.append(ticket)
                waited_from = time.monotonic()
                if not self._wait_turn(ticket, emit):
                    emit({"type": "done", "cancelled": True})
                    return
                with state.ticket_running(ticket, "live", lambda _chunk: None):
                    started = time.monotonic()
                    korean = state.live_translate(viewer, sid, text, lang)
                    finished = time.monotonic()
                    if record:
                        state.core.storage.add_message(sid, "user", f"[{LANG_LABEL[lang]}] {text}")
                        state.core.storage.add_message(sid, "assistant", korean)
                emit({"type": "translation", "text": korean, "stt": took, "wait": round(started - waited_from, 2),
                      "translate": round(finished - started, 2)})
                emit({"type": "done"})
                state.core.logger.info("live: %s %.2fs stt, %.2fs wait, %.2fs translate", lang, took,
                                       started - waited_from, finished - started)
            except GenerationCancelled:
                emit({"type": "done", "cancelled": True})
            except LiveError as exc:
                emit({"type": "error", "error": str(exc)})
            except Exception:  # noqa: BLE001
                state.core.logger.exception("live utterance failed")
                emit({"type": "error", "error": "이 토막을 처리하지 못했습니다."})
            finally:
                if ticket is not None:
                    with state.queue_cv:
                        if ticket in state.queue:
                            state.queue.remove(ticket)
                        state.queue_cv.notify_all()

        # ------------------------------------------------------------ 코딩 세션 API(ADR-112, OpenCode 서버와 같은 일)
        def _code_post(self) -> None:
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
                return
            if length < 0 or length > MAX_BODY_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body too large"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON object가 필요합니다."})
                return
            if not isinstance(body, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON object가 필요합니다."})
                return
            self._code_api("POST", urlparse(self.path).path, {}, body)

        def _code_api(self, method: str, route: str, query: dict, body: dict) -> None:
            """/api/code/session[/{id}[/message|diff|todo|command|prompt_async|abort|revert|delete|permissions/{pid}]] · /api/code/event.
            이 PC 의 파일을 바꾸고 명령을 실행하므로 관리자만."""
            viewer = self._require_viewer(admin=True)
            if viewer is None:
                return
            code, me = state.code, viewer.id
            parts = [unquote(p) for p in route[len("/api/code/"):].split("/") if p]
            try:
                if parts == ["event"] and method == "GET":
                    self._code_events(viewer, query)
                    return
                if parts == ["path"] and method == "GET":  # 새 세션을 열 수 있는 곳(OpenCode GET /path 와 같은 뜻)·고를 수 있는 모델
                    approved = getattr(state.core, "_approved_file_root", None)
                    self._json(HTTPStatus.OK, {"root": str(approved or ""),
                                               "directory": str(state.core.current_project_root() or approved or ""),
                                               "models": state.free_models()})
                    return
                if not parts or parts[0] != "session":
                    raise LookupError("not found")
                if len(parts) == 1:
                    if method == "GET":
                        self._json(HTTPStatus.OK, {"sessions": code.list(me)})
                    else:
                        session = code.create(me, state.code_root(body.get("directory")),
                                              str(body.get("tier") or state.session_permission(viewer, state.current_session(viewer))),
                                              str(body.get("title") or ""), model=str(body.get("model") or ""))
                        self._json(HTTPStatus.OK, {"session": session.public()})
                    return
                sid, action = parts[1], "/".join(parts[2:])
                if method == "GET" and action == "":
                    self._json(HTTPStatus.OK, {"session": code.get(sid, me).public()})
                elif method == "GET" and action == "message":
                    self._json(HTTPStatus.OK, {"messages": code.messages(sid, me)})
                elif method == "GET" and action == "diff":
                    self._json(HTTPStatus.OK, {"diff": code.diff(sid, me)})
                elif method == "GET" and action == "todo":
                    self._json(HTTPStatus.OK, {"todos": code.todo(sid, me)})
                elif method == "GET" and action == "command":
                    self._json(HTTPStatus.OK, {"commands": code.commands(sid, me)})
                elif method == "POST" and action == "command":  # OpenCode 와 같은 모양: {"command": "init", "arguments": ""}
                    code.command(sid, str(body.get("command") or ""), str(body.get("arguments") or ""), owner=me,
                                 mode=str(body.get("mode") or body.get("agent") or ""))
                    self._json(HTTPStatus.OK, {"accepted": True, "session": code.get(sid, me).public()})
                elif method == "POST" and action in ("prompt_async", "message"):
                    text = body.get("text")
                    if text is None:  # OpenCode 모양: {"parts": [{"type": "text", "text": "…"}]}
                        text = "\n".join(str(p.get("text", "")) for p in body.get("parts") or []
                                         if isinstance(p, dict) and p.get("type", "text") == "text")
                    code.prompt(sid, str(text or ""), owner=me, mode=str(body.get("mode") or body.get("agent") or ""))
                    self._json(HTTPStatus.OK, {"accepted": True, "session": code.get(sid, me).public()})
                elif method == "POST" and action == "abort":
                    code.abort(sid, me)
                    self._json(HTTPStatus.OK, {"aborted": True})
                elif method == "POST" and action == "revert":
                    self._json(HTTPStatus.OK, {"files": code.revert(sid, int(body.get("step") or 0), me)})
                elif method == "POST" and action == "delete":
                    code.delete(sid, me)
                    self._json(HTTPStatus.OK, {"deleted": sid})
                elif method == "POST" and len(parts) == 4 and parts[2] == "permissions":
                    code.reply_permission(sid, parts[3], str(body.get("response") or ""), me)
                    self._json(HTTPStatus.OK, {"replied": True})
                else:
                    raise LookupError("not found")
            except LookupError as exc:  # KeyError 도 여기(없는 세션·권한 요청)
                self._json(HTTPStatus.NOT_FOUND, {"error": exc.args[0] if exc.args else "not found"})
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def _code_chat_stream(self, viewer: Viewer, chat_sid: str, message: str, plan: dict, permission: str | None) -> None:
            """말로 연 코딩 세션의 한 차례를 대화로 흘려 보낸다. 허락이 필요하거나 질문이 오거나 끝나면 이 차례를 마치고(채팅이 다시
            입력을 받게) 대화에 남긴다. 허락은 다음 말('응'·'항상'·'아니')로 받는다. 이 웹 요청은 모델 차례를 잡지 않는다 —
            세션이 모델을 부를 때마다 대기열에서 차례를 잡는다."""
            code, storage = state.code, state.core.storage
            host = self.headers.get("Host") or "127.0.0.1:8765"
            link_url = lambda sid: f"{'https' if self._is_remote() else 'http'}://{host}/code?session={sid}"  # noqa: E731
            self.send_response(HTTPStatus.OK)
            self._security_headers("application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            gone = False

            def emit(payload: dict) -> None:
                nonlocal gone
                if gone:
                    return
                try:
                    self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    gone = True   # 화면이 떠나도 세션은 계속 — 끝나면 대화에 남긴다

            def finish(text: str) -> None:
                storage.add_message(chat_sid, "assistant", text)
                emit({"type": "done", "response": text, "session_id": chat_sid})

            storage.add_message(chat_sid, "user", message)
            kind, session = plan["kind"], plan.get("session")
            emit({"type": "start", "request_id": "code-" + secrets.token_hex(6), "session_id": chat_sid})
            try:
                if kind == "busy":
                    finish(f"코딩 세션이 아직 일하는 중입니다. 끝나면 이 대화에 결과를 남깁니다. 진행 화면: {link_url(session.id)}")
                    return
                if kind == "permission":
                    code.reply_permission(session.id, plan["request"].id, plan["reply"])
                    emit({"type": "chunk", "text": {"once": "[허락했습니다]\n", "always": "[이 세션 동안 허락합니다]\n",
                                                    "reject": "[거절했습니다]\n"}[plan["reply"]]})
                elif kind == "revert":
                    files = code.revert(session.id, 0)
                    state.code_links[chat_sid]["fresh"] = False
                    finish("이번 코딩 세션의 변경을 처음 상태로 되돌렸습니다: " + (", ".join(files) or "바뀐 파일 없음"))
                    return
                elif kind in ("answer", "continue"):
                    code.prompt(session.id, message)
                else:
                    tier = permission or state.session_permission(viewer, chat_sid)
                    model = getattr(state.settings, "coding_free_model", "") if state.settings else ""
                    session = code.create(viewer.id, plan["root"], tier, title=message.strip().splitlines()[0][:60], model=model)
                    state.code_links[chat_sid] = {"code": session.id, "since": 0, "fresh": True}
                    who = f"OpenCode 무료 {model[9:]}(opencode.ai)" if model else "이 PC 모델"
                    emit({"type": "chunk", "text": f"[코딩 세션 시작 — 폴더 {session.root} · 권한 "
                                                   f"{ {'read': '읽기 전용', 'ask': '확인 후 실행', 'auto': '자동 실행'}.get(tier, tier) } · {who}]\n"})
                    code.prompt(session.id, message)
                link = state.code_links[chat_sid]
                since = link["since"]
                while True:
                    events = code.events(session.id, since, wait=15.0)
                    if not events:
                        emit({"type": "chunk", "text": ""})   # 끊긴 화면 알아채기
                        continue
                    final = ""
                    for event in events:
                        since = event["n"] + 1
                        if event["type"] in ("progress", "message.delta"):
                            emit({"type": "chunk", "text": event["text"]})
                        elif event["type"] == "permission.asked":
                            request = event["request"]
                            diff = str((request.get("detail") or {}).get("diff") or "").strip()
                            what = {"edit": "파일을 바꾸려", "bash": "명령을 실행하려"}.get(request["permission"], request["permission"] + " 하려")
                            final = (f"{what} 합니다: `{request['pattern']}`\n" + (f"```diff\n{diff[:4000]}\n```\n" if diff else "")
                                     + "진행할까요? '응'(이번만) · '항상'(이 세션 동안) · '아니'(거절)")
                        elif event["type"] == "question.asked":
                            final = "코딩 세션이 묻습니다:\n" + "\n".join(f"- {q.get('question', '')}" for q in event.get("questions") or [])
                        elif event["type"] == "session.idle":
                            result = event.get("result") or {}
                            changed = re.findall(r"^\+\+\+ b/(.+)$", code.diff(session.id), re.M)
                            checks = (session.last.verification if session.last is not None else None) or []
                            check = {"verified": "통과", "unverified": "통과 못 함", "not_found": "연결된 시험 없음"}.get(
                                checks[-1].get("status"), "") if checks else ""
                            final = ((result.get("text") or result.get("error") or "코딩 세션이 끝났습니다.").strip()
                                     + ("\n\n바뀐 파일: " + ", ".join(changed) if changed else "")
                                     + (f"\n독립 검사: {check}" if check else "")
                                     + (f"\n자세히·diff: {link_url(session.id)}" if changed else "")
                                     + ("\n되돌리려면 '되돌려줘' 라고 하세요." if changed else ""))
                        if final:
                            break
                    if final:
                        link["since"], link["fresh"] = since, True
                        finish(final)
                        return
            except (KeyError, ValueError) as exc:
                finish(f"코딩 세션을 시작하지 못했습니다: {exc}")

        def _code_events(self, viewer: Viewer, query: dict) -> None:
            """사건 흐름(SSE, OpenCode /event 와 같은 뜻) — 'data: {사건}' 줄. 15초마다 빈 줄로 끊긴 연결을 알아챈다.
            ?session=…&since=N(이 번호부터). 토큰 머리글이 필요해 브라우저는 EventSource 대신 fetch 로 읽는다."""
            sid = query.get("session", [""])[0]
            since = int(query.get("since", ["0"])[0] or 0)
            state.code.get(sid, viewer.id)
            self.send_response(HTTPStatus.OK)
            self._security_headers("text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while not state.shutting_down:
                    batch = state.code.events(sid, since, wait=15.0, owner=viewer.id)
                    out = [f"id: {e['n']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in batch] or [": ping\n\n"]
                    if batch:
                        since = batch[-1]["n"] + 1
                    self.wfile.write("".join(out).encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, KeyError):
                return

        def _wait_turn(self, ticket: Ticket, emit) -> bool:
            """차례가 오면 모델 잠금(state.lock)을 잡고 True. 기다리는 동안 앞에 몇 명이 있는지 알린다
            (10초마다 한 번은 보내서 끊긴 연결을 알아챈다)."""
            return state.wait_turn(ticket, lambda position: emit({"type": "queue", "position": position}))

        def _stream(self, message: str, attachments: list[str] | None = None, viewer: Viewer | None = None,
                    session_id: str | None = None, ticket: Ticket | None = None, permission: str | None = None,
                    documents: list[str] | None = None) -> None:
            viewer = viewer or state.owner_viewer()
            session_id = session_id or state.current_session(viewer)
            if ticket is None:
                ticket = Ticket(viewer.id, session_id=session_id)
                with state.queue_cv:
                    state.queue.append(ticket)
            disconnected = False

            def emit(payload: dict) -> None:
                nonlocal disconnected
                if disconnected:
                    return
                try:
                    self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    disconnected = True
                    ticket.cancel.set()

            waited_from = time.monotonic()
            try:
                self.send_response(HTTPStatus.OK)
                self._security_headers("application/x-ndjson; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                emit({"type": "start", "request_id": ticket.request_id, "session_id": session_id})
                acquired = self._wait_turn(ticket, emit)
                if not acquired:
                    emit({"type": "done", "cancelled": True, "session_id": session_id or state.current_session(viewer),
                          "response": "앱을 종료하는 중이라 요청을 보내지 못했습니다." if state.shutting_down
                          else "기다리던 요청을 취소했습니다."})
                    return
                # A queued request does not own these shared fields until it gets
                # the model lock (ticket_running 이 그때의 모델들을 잡고, 끝나면 되돌리고 잠금을 푼다).
                # (대기 중인 요청의 취소/끊김은 실행 중인 다른 방의 콜백과 취소 신호를 건드리지 않는다.)
                with state.ticket_running(ticket, "chat", lambda chunk: emit({"type": "chunk", "text": chunk})) as local:
                    started = time.monotonic()
                    emit({"type": "running", "request_id": ticket.request_id, "session_id": session_id})
                    result = state.process(message, attachments, viewer=viewer, session_id=session_id,
                                           permission=permission, documents=documents)
                    saved = state.core.storage.message_page(result["session_id"], limit=1)["messages"]
                    metrics = next((p.last_metrics for p in local if p.last_metrics), {})
                    emit({"type": "done", **result, "metrics": metrics, "elapsed": round(time.monotonic() - started, 2),
                          "waited": round(started - waited_from, 1), "last_message_id": saved[-1]["id"] if saved else 0})
                    if not disconnected and (title := state.auto_title(result, message)):
                        emit({"type": "title", "session_id": result["session_id"], "title": title})
            except GenerationCancelled:
                emit({"type": "done", "response": "응답 생성을 중단했습니다. 파일 변경은 적용되지 않았습니다.",
                      "session_id": session_id or state.current_session(viewer)})
            except Exception:
                emit({"type": "error", "error": "요청을 완료하지 못했습니다. 새로고침해 저장된 대화를 확인하세요."})
            finally:
                with state.queue_cv:
                    if ticket in state.queue:
                        state.queue.remove(ticket)
                    state.queue_cv.notify_all()

        def _job_stream(self, job: dict, session_id: str) -> None:
            """보통 전송으로 온 요청을 뒤 작업으로 돌렸다고 알리는 짧은 스트림(화면은 보통 답처럼 받는다)."""
            self.send_response(HTTPStatus.OK)
            self._security_headers("application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            text = ("클라우드에 넘기는 일을 뒤 작업으로 시작했습니다. 클라우드가 일하는 동안에도 ALEX 와 계속 대화할 수 있고, "
                    "창을 닫아도 됩니다. 끝나면 이 대화에 결과(ALEX 확인·수정안)를 남기고 알려 드립니다. (계정 메뉴 → 뒤 작업)")
            for payload in ({"type": "start", "request_id": job["id"], "session_id": session_id},
                            {"type": "done", "response": text, "session_id": session_id, "background_job": job["id"]}):
                try:
                    self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        def _cancel(self, viewer: Viewer, request_id: Any) -> None:
            if state.active_id and request_id == state.active_id and (
                    viewer.is_admin or (state.running is not None and state.running.user_id == viewer.id)):
                state.cancel_event.set()
                self._json(HTTPStatus.OK, {"cancelled": True})
                return
            with state.queue_cv:
                waiting = next((t for t in state.queue if t.request_id == request_id
                                and (t.user_id == viewer.id or viewer.is_admin)), None)
                if waiting is not None:
                    waiting.cancel.set()
                    state.queue_cv.notify_all()
            if waiting is None:
                self._json(HTTPStatus.CONFLICT, {"error": "중단할 요청이 없습니다."})
            else:
                self._json(HTTPStatus.OK, {"cancelled": True})

        # ------------------------------------------------------------ POST
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._host_guard():
                return
            if self.path == "/api/knowledge/upload":
                self._upload_knowledge()
                return
            if self.path == "/api/image/upload":
                self._upload_image()
                return
            if self.path == "/api/attachment/upload":
                self._upload_image(attachment=True)
                return
            if self.path == "/api/project/upload":
                self._upload_project_file()
                return
            if self.path == "/api/document/upload":
                self._upload_document()
                return
            if self.path == "/api/live/utterance":
                self._live_utterance()
                return
            if self.path.startswith("/api/code/"):
                self._code_post()
                return
            if self.path not in {"/api/project", "/api/chat", "/api/chat/stream", "/api/job", "/api/cancel", "/api/settings", "/api/workspace",
                                 "/api/shutdown", "/api/pull", "/api/session/delete", "/api/login", "/api/logout",
                                 "/api/local-owner", "/api/password", "/api/users", "/api/memory", "/api/task",
                                 "/api/session/new", "/api/session/rename", "/api/session/permission",
                                 "/api/document/discard", "/api/live/start", "/api/coding", "/api/restart"}:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body too large or empty"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("JSON object가 필요합니다.")
                if self._account_routes(body):
                    return
                viewer = self._require_viewer(admin=self.path in ADMIN_PATHS)
                if viewer is None:
                    return
                if self.path == "/api/cancel":
                    self._cancel(viewer, body.get("request_id"))
                    return
                if self.path == "/api/coding":  # 교훈 붙이기·쉬는 중 자동 학습(관리자, 바로 적용)
                    self._json(HTTPStatus.OK, state.save_coding_settings(body))
                    return
                if self.path == "/api/project":
                    if body.get("action") == "delete" and state.ticket_for(viewer.id) is not None:
                        self._json(HTTPStatus.CONFLICT, {"error": "응답이 끝난 뒤 프로젝트를 지우세요."})
                        return
                    self._json(HTTPStatus.OK, state.manage_project(viewer, body))
                    return
                if self.path == "/api/session/rename":  # 제목 직접 바꾸기
                    target = body.get("session_id")
                    if not state.owns(viewer, target):
                        raise ValueError("대화를 찾을 수 없습니다. 새로고침해 주세요.")
                    state.core.storage.rename_session(target, str(body.get("title") or ""))
                    self._json(HTTPStatus.OK, {"session_id": target, "title": state.core.storage.session_title(target)})
                    return
                if self.path == "/api/document/discard":  # 보내기 전 첨부 빼기(ADR-096)
                    self._json(HTTPStatus.OK, state.discard_document(viewer, body.get("session_id"), body.get("document_id")))
                    return
                if self.path == "/api/session/permission":  # 입력창의 권한 고르기(ADR-095)
                    target = body.get("session_id")
                    self._json(HTTPStatus.OK, {"session_id": target,
                                               "permission": state.set_permission(viewer, target, body.get("permission"))})
                    return
                if self.path == "/api/live/start":  # 실시간 통역 시작(ADR-111) — 새 통역 대화, 받아쓰기 일꾼 미리 띄움
                    self._json(HTTPStatus.OK, state.live_start(viewer))
                    return
                if self.path == "/api/session/new":  # 새 대화는 모델을 쓰지 않으니 차례를 기다리지 않는다
                    project_id = str(body.get("project_id") or "")
                    if project_id and state.core.storage.get_project(project_id, owner=viewer.id) is None:
                        raise ValueError("프로젝트를 찾을 수 없습니다.")
                    with state.queue_cv:
                        sid = state.core.storage.empty_session(viewer.id, project_id)
                        if sid and any(t.session_id == sid for t in state.queue):
                            sid = None  # 아직 메시지를 쓰기 전인 대기 중 방을 '새 대화'로 재사용하지 않는다
                        sid = sid or state.core.storage.create_session(viewer.id, project_id=project_id)
                    state.reset_permission(sid)
                    state.set_current(viewer, sid)
                    self._json(HTTPStatus.OK, {"session_id": sid})
                    return
                if self.path == "/api/password":
                    state.change_password(viewer, body.get("current"), body.get("password"))
                    cookies: tuple[str, ...] = ()
                    if self._cookies().get(SESSION_COOKIE):  # 비밀번호를 바꾸면 다른 기기의 로그인은 끝나고, 이 기기는 이어진다
                        token = accounts.new_session_token()
                        expires = datetime.now(timezone.utc) + timedelta(days=accounts.SESSION_DAYS)
                        state.core.storage.create_auth_session(accounts.token_hash(token), viewer.id, expires.isoformat())
                        cookies = (self._session_cookie(token),)
                    self._json(HTTPStatus.OK, {"changed": True}, cookies=cookies)
                    return
                if self.path in ("/api/memory", "/api/task", "/api/users"):
                    self._json(HTTPStatus.OK, self._personal(viewer, body))
                    return
                if self.path == "/api/session/delete":
                    # 실행/대기 중인 방을 지워 요청이 갈 곳을 잃지 않게 한다. 다른 방의 삭제는 허용한다.
                    mine, running = state.ticket_for(viewer.id, body.get("target")), state.running
                    locked = running is None and state.lock.acquire(blocking=False)
                    if mine is not None or (running is None and not locked):
                        if locked:
                            state.lock.release()
                        self._json(HTTPStatus.CONFLICT, {"error": "이 대화에 처리 중인 요청이 있습니다. 완료 또는 중단 후 삭제하세요."})
                        return
                    try:
                        self._json(HTTPStatus.OK, state.delete_session(body.get("target"), body.get("confirmed"),
                                                                       viewer, body.get("session_id")))
                    finally:
                        if locked:
                            state.lock.release()
                    return
                session_id = body.get("session_id")
                if session_id is not None and not state.owns(viewer, session_id):
                    self._json(HTTPStatus.CONFLICT, {"error": "다른 탭에서 대화가 바뀌었습니다. 새로고침 후 전송하세요."})
                    return
                if self.path in ("/api/chat/stream", "/api/job"):
                    # 접수할 때 방을 고정한다. 기다리는 동안 사용자가 다른 방을 골라도 바뀌지 않는다.
                    session_id = session_id or state.current_session(viewer)
                    if self.path == "/api/job" and body.get("retry"):  # '다시 맡기기'(ADR-110) — 남겨 둔 원문·권한으로
                        session_id, message, permission = state.job_message(viewer, body["retry"])
                        attachments, documents = [], []
                    else:
                        message, attachments, documents = self._chat_body(body, viewer, session_id)
                        permission = self._permission(body, viewer)  # 차례를 기다리기 전에 형식·자격을 확인
                    if state.shutting_down:
                        self._json(HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."})
                        return
                    cloud = self.path == "/api/chat/stream" and parse_cloud_request(message) is not None
                    if cloud and not viewer.is_admin:
                        self._json(HTTPStatus.CONFLICT, {"error": "클라우드로 넘기기는 관리자만 쓸 수 있습니다."})
                        return
                    if (self.path == "/api/chat/stream" and not cloud and not attachments and not documents
                            and (plan := state.code_chat_plan(viewer, session_id, message)) is not None):
                        self._code_chat_stream(viewer, session_id, message, plan, permission)  # 말로 코딩 세션(2026-10-23)
                        return
                    with state.queue_cv:
                        if not state.owns(viewer, session_id):
                            self._json(HTTPStatus.CONFLICT, {"error": "대화가 삭제되었습니다. 새 대화를 열어 주세요."})
                            return
                        mine = [t for t in state.queue if t.user_id == viewer.id]
                        if any(t.session_id == session_id for t in mine):
                            ticket = None
                            rejection = "이 대화의 앞선 요청이 끝난 뒤 보내 주세요. 다른 대화에서는 전송할 수 있습니다."
                        elif len(mine) >= 4:
                            ticket = None
                            # HTTP/1.1 브라우저 연결을 전부 긴 스트림으로 채우면 방 이동·중단도 막힌다.
                            rejection = "동시에 최대 4개 대화의 요청을 받을 수 있습니다. 하나가 끝난 뒤 다시 보내 주세요."
                        elif (self.path == "/api/job" or cloud) and (attachments or documents):
                            ticket = None
                            rejection = "뒤 작업에는 첨부를 붙일 수 없습니다. 첨부는 보통 전송으로 보내 주세요."
                        elif self.path == "/api/job" or cloud:
                            ticket = Ticket(viewer.id, session_id=session_id, background=True)  # 자리만 확인 — start_job 이 만든다
                        else:
                            ticket = Ticket(viewer.id, session_id=session_id)
                            state.queue.append(ticket)
                    if ticket is None:
                        self._json(HTTPStatus.CONFLICT, {"error": rejection})
                        return
                    if self.path == "/api/job":
                        self._json(HTTPStatus.OK, {"job": state.start_job(viewer, session_id, message, permission)})
                        return
                    if cloud:  # 클라우드로 넘기기는 언제나 뒤 작업 — 클라우드가 일하는 동안 ALEX 와 계속 대화할 수 있다
                        job = state.start_job(viewer, session_id, message, permission)
                        self._job_stream(job, session_id)
                        return
                    self._stream(message, attachments, viewer, session_id, ticket, permission, documents)
                    return
                if not state.lock.acquire(blocking=False):
                    self._json(HTTPStatus.CONFLICT, {"error": "응답 생성 중입니다. 완료 또는 중단 후 다시 시도하세요."})
                    return
                # Normal requests release this below.
                try:
                    if state.shutting_down:
                        self._json(HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."})
                        return
                    if self.path in ("/api/shutdown", "/api/restart"):
                        if state.queue:
                            self._json(HTTPStatus.CONFLICT, {"error": "다른 사용자의 요청이 처리 중입니다. 끝난 뒤 종료하세요."})
                            return
                        if state.code is not None and any(s.status == "running" for s in state.code.sessions.values()):
                            # 코딩 세션은 모델을 부를 때만 대기열에 있다 — 도구 실행 중에도 끊지 않게(2026-10-23)
                            self._json(HTTPStatus.CONFLICT, {"error": "코딩 세션이 일하는 중입니다. 끝나거나 중단한 뒤 다시 하세요."})
                            return
                        if self.path == "/api/restart":  # '다시 켜기' — 실행기를 따로 띄워 두고 이 웹은 꺼진다(2026-10-23)
                            state.spawn_restarter()
                        state.shutting_down = True
                        try:
                            cleanup = state.close_owned_services()
                            self._json(HTTPStatus.OK, {"stopped": True, "restarting": self.path == "/api/restart", **cleanup})
                        except ConnectionError:
                            pass  # Closing the browser must not cancel an accepted shutdown.
                        finally:
                            threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                    if self.path == "/api/workspace":
                        self._json(HTTPStatus.OK, state.register_workspace(body.get("path"), body.get("confirmed")))
                        return
                    if self.path == "/api/settings":
                        self._json(HTTPStatus.OK, state.save_settings(body.get("settings", {})))
                        return
                    if self.path == "/api/pull":
                        self._pull(body)
                        return
                    message, attachments, documents = self._chat_body(body, viewer, session_id)
                    result = state.process(message, attachments, viewer=viewer, session_id=session_id,
                                           permission=self._permission(body, viewer), documents=documents)
                    if title := state.auto_title(result, message):
                        result["title"] = title
                finally:
                    state.lock.release()
            except PermissionError as exc:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
                return
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, StorageError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception:
                # 브라우저에 traceback/secret/path를 노출하지 않는다. 상세 내용은 core log에서 확인.
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "ALEX 내부 오류"})
                return
            self._json(HTTPStatus.OK, result)

        @staticmethod
        def _permission(body: dict, viewer: Viewer) -> str | None:
            """보낼 때 고른 권한(없으면 대화에 저장된 값). 자동 실행은 관리자만."""
            value = body.get("permission")
            if value is None:
                return None
            mode = clean_permission(value)
            if mode == "auto" and not viewer.is_admin:
                raise ValueError("자동 실행은 관리자만 고를 수 있습니다.")
            return mode

        def _chat_body(self, body: dict, viewer: Viewer, session_id: str | None) -> tuple[str, list[str], list[str]]:
            message = body.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > 32000:
                raise ValueError("메시지는 1~32000자 문자열이어야 합니다.")
            attachments = body.get("attachments", [])
            if (not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS
                    or any(not isinstance(name, str) for name in attachments)
                    or len(set(attachments)) != len(attachments)):
                raise ValueError("이미지는 한 번에 최대 3개까지 첨부할 수 있습니다.")
            if attachments and message.strip().startswith("/"):
                raise ValueError("이미지 첨부에는 일반 질문을 입력해 주세요. 명령어는 따로 보내 주세요.")
            sid = session_id or state.current_session(viewer)
            for name in attachments:
                state.attachments.load(sid, name)  # Validate ownership before opening a stream.
            documents = body.get("documents", [])
            if (not isinstance(documents, list) or len(documents) > MAX_CHAT_DOCUMENTS
                    or any(not isinstance(d, str) or not re.fullmatch(r"[a-f0-9]{8,24}", d) for d in documents)
                    or len(set(documents)) != len(documents)):
                raise ValueError(f"문서는 한 번에 최대 {MAX_CHAT_DOCUMENTS}개까지 첨부할 수 있습니다.")
            if documents and message.strip().startswith("/"):
                raise ValueError("문서 첨부와 명령어는 따로 보내 주세요.")
            for document_id in documents:  # 이 대화에 올린 것인지 차례를 기다리기 전에 확인
                state.core.knowledge.resolve_id(document_id, (f"draft:{sid}", f"session:{sid}"))
            return message, attachments, documents

        def _account_routes(self, body: dict) -> bool:
            """로그인·로그아웃·이 PC 주인으로 돌아가기. 처리했으면 True."""
            if self.path == "/api/login":
                viewer, token = state.login(body.get("login"), body.get("password"), self._is_remote())
                self._json(HTTPStatus.OK, {"user": viewer.public()},
                           cookies=(self._session_cookie(token), self._manual_cookie(not self._is_remote())))
                return True
            if self.path == "/api/logout":
                token = self._cookies().get(SESSION_COOKIE)
                if token:
                    state.core.storage.delete_auth_session(accounts.token_hash(token))
                cookies = (self._session_cookie(None),) + (() if self._is_remote() else (self._manual_cookie(True),))
                self._json(HTTPStatus.OK, {"logged_out": True}, cookies=cookies)
                return True
            if self.path == "/api/local-owner":
                if self._is_remote():
                    raise PermissionError("이 PC 에서만 쓸 수 있습니다.")
                self._json(HTTPStatus.OK, {"user": state.owner_viewer().public()},
                           cookies=(self._session_cookie(None), self._manual_cookie(False)))
                return True
            return False

        def _personal(self, viewer: Viewer, body: dict) -> dict[str, Any]:
            """기억·할 일·내 비밀번호·(관리자) 사용자 관리. 모델을 쓰지 않으니 다른 사람의 답을 기다리지 않는다."""
            storage, action = state.core.storage, body.get("action")
            if self.path == "/api/memory":
                key = str(body.get("key") or "")
                if action == "add":
                    if "=" in key:
                        raise ValueError("기억 이름에는 = 를 쓸 수 없습니다.")
                    storage.remember(key, str(body.get("value") or ""), owner=viewer.id)
                elif action == "delete":
                    storage.forget(key, owner=viewer.id)
                else:
                    raise ValueError("알 수 없는 작업입니다.")
                return {"memories": storage.list_memories(100, owner=viewer.id)}
            if self.path == "/api/task":
                if action == "add":
                    storage.add_task(str(body.get("text") or ""), owner=viewer.id)
                elif action in ("done", "open", "delete") and isinstance(body.get("id"), int):
                    if action == "delete":
                        storage.delete_task(body["id"], owner=viewer.id)
                    else:
                        storage.set_task_status(body["id"], action, owner=viewer.id)
                else:
                    raise ValueError("알 수 없는 작업입니다.")
                return {"tasks": [asdict(t) for t in storage.list_tasks(include_done=True, owner=viewer.id)]}
            if self.path == "/api/password":
                state.change_password(viewer, body.get("current"), body.get("password"))
                return {"changed": True}
            return state.manage_user(viewer, body)

        def _pull(self, body: dict) -> None:
            from .model_registry import get_candidates
            tag = body.get("model")
            if not isinstance(tag, str) or not tag:
                raise ValueError("모델 태그가 필요합니다.")
            if body.get("confirmed") is not True:
                raise ValueError("다운로드 확인이 필요합니다.")
            valid_tags = {cand.tag for cand in get_candidates()}
            if tag not in valid_tags:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "카탈로그에 등록된 모델만 다운로드할 수 있습니다."})
                return
            # Strata(ADR-086)는 Ollama 모델을 받지 않는다 — 다운로드는 Ollama 연결만
            local_provider = next((p for p in state.core.providers.values()
                                   if isinstance(p, OllamaProvider) and not isinstance(p, StrataProvider)), None)
            if local_provider is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Ollama 연결이 없습니다(Strata 모드에서는 모델 다운로드를 쓰지 않습니다)."})
                return

            state.active_id = secrets.token_hex(12)
            state.active_kind = "download"
            state.cancel_event = threading.Event()
            try:
                self.send_response(HTTPStatus.OK)
                self._security_headers("application/x-ndjson; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                def emit(event: dict) -> None:
                    self.wfile.write(json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n")
                    self.wfile.flush()

                emit({"type": "start", "request_id": state.active_id})
                import urllib.request
                payload = json.dumps({"model": tag, "stream": True}).encode("utf-8")
                request = urllib.request.Request(
                    f"{local_provider.host}/api/pull", data=payload,
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=600) as response, interrupt_on_cancel(response, state.cancel_event):
                        for line in response:
                            if state.cancel_event.is_set():
                                break
                            self.wfile.write(line)
                            self.wfile.flush()
                        if state.cancel_event.is_set():
                            emit({"status": "cancelled"})
                except (BrokenPipeError, ConnectionResetError):
                    state.cancel_event.set()
                except Exception as exc:
                    try:
                        emit({"status": "cancelled"} if state.cancel_event.is_set() else
                             {"error": "모델 다운로드 연결이 끊겼거나 Ollama 요청이 실패했습니다."})
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        state.cancel_event.set()
            finally:
                state.active_id = None
                state.active_kind = None

    return Handler


def make_server(state: WebAppState, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    if not (0 <= int(port) <= 65535):
        raise ValueError("port는 0~65535 범위여야 합니다.")
    return ThreadingHTTPServer((LOOPBACK_HOST, int(port)), _handler_factory(state))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Project ALEX loopback-only Local Web Alpha")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="서버 준비 후 브라우저 열기")
    args = parser.parse_args(argv)

    try:
        settings: Settings = load_settings()
        from .storage import Storage
        saved_root = Storage(settings.database_path).get_state("web.workspace_root")
        if saved_root and "JARVIS_FILE_SEARCH_ROOT" not in os.environ and Path(saved_root).is_dir():
            settings = replace(settings, file_search_root=Path(saved_root))
        # Web Alpha intentionally cannot approve Cloud. Cloud routes fall back locally.
        core, notes = build_core(settings, approval_callback=lambda _request: False)
    except ConfigError as exc:
        raise SystemExit(f"설정 오류: {exc}") from exc

    saved = core.storage.get_state("web.current_session")
    session_id = saved if saved and core.storage.session_exists(saved) else core.get_or_create_session(resume=True)
    state = WebAppState(core=core, session_id=session_id, settings=settings)
    state.archive_sync()  # 꺼져 있던 동안 쌓인 메시지(터미널 대화 등)도 월별 기록에
    server = make_server(state, args.port)
    host, port = server.server_address[:2]
    print(f"Project ALEX Local Web: http://{host}:{port}")
    print("Cloud approval: DISABLED in Web Alpha (Cloud route -> Local fallback)")
    for note in notes:
        print(f"- {note}")
    print("종료: Ctrl+C")
    if args.open:
        webbrowser.open(f"http://{host}:{port}")
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()
        for warning in state.close_owned_services()["warnings"]:
            print(f"종료 정리: {warning}")


if __name__ == "__main__":
    main()
