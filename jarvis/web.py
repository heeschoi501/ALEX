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
import os
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs, unquote
import webbrowser
import threading
from typing import Any

from .app import (build_core, build_providers, build_summary_provider, engine_context_limits, handle_command,
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

# 여러 사람이 쓰기(2026-10-11). 이 PC 에서 열면 주인(관리자)으로 바로 들어오고, 밖(Tailscale)에서는 로그인한다.
SESSION_COOKIE = "alex_session"
MANUAL_COOKIE = "alex_manual"  # 이 PC 에서 '사용자 전환'을 누른 브라우저 — 주인으로 자동 로그인하지 않는다
LOGIN_LOCK_AFTER, LOGIN_LOCK_SECONDS = 5, 60
ARTIFACT_RE = re.compile(r"\]\(alex-(?:image|video|document):([A-Za-z0-9._-]{1,120})\)")
# 관리자가 아닌 사용자가 쓸 수 있는 명령. 이 PC 의 파일·설정·학습·개발 도구에 손대는 명령은 뺀다.
MEMBER_COMMANDS = frozenset({
    "/help", "/new", "/use", "/rename", "/delete-session", "/sessions", "/summary", "/export", "/document",
    "/image", "/image-prompt", "/video-prompt", "/characters", "/memories", "/remember", "/recall", "/forget",
    "/task-add", "/tasks", "/task-done", "/task-open", "/task-delete", "/knowledge", "/knowledge-search",
    "/ask-knowledge", "/search", "/web", "/good", "/bad",
})
def provisional_title(text: str) -> str:
    """첫 답이 오기 전까지 쓸 임시 제목 — 첫 메시지를 한 줄로 줄이고 40자 넘으면 낱말 경계에서 자른다."""
    clean = " ".join(text.split())
    if len(clean) <= 40:
        return clean
    cut = clean[:40]
    space = cut.rfind(" ")
    return (cut[:space] if space >= 20 else cut) + "…"


ADMIN_PATHS = frozenset({"/api/settings", "/api/workspace", "/api/shutdown", "/api/pull", "/api/users"})


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
                    "requests": [{"request_id": t.request_id, "session_id": t.session_id,
                                  "status": "running" if t is self.running else "queued",
                                  "position": 0 if t is self.running else max(i, 1)}
                                 for i, t in enumerate(self.queue) if t.user_id == viewer.id]}

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
            if backend_available(replace(self.settings, local_backend=other), validate_config=False):
                provider = OllamaProvider(self.settings.general_model, host=self.settings.ollama_host) if other == "ollama" else StrataProvider(self.settings.strata_model, host=self.settings.strata_host)
                self.inactive_providers.append(provider)
                self.core.local_engine_guard = self._check_inactive_engines

    def close_owned_services(self) -> dict[str, Any]:
        """Shared Ollama and pre-existing ComfyUI services remain independent."""
        services = {"ollama": "preserved", "comfyui": "not_owned"}
        warnings = []
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

    def save_settings(self, changes: dict) -> dict:
        if self.settings is None:
            raise ValueError("설정 변경을 지원하지 않는 실행 모드입니다.")
        allowed = {"general_model", "coder_model", "desktop_general_model", "desktop_coder_model",
                   "auto_profile_models", "single_model_on_laptop", "local_max_output_tokens",
                   "desktop_max_output_tokens", "ollama_think", "num_ctx", "local_backend", "strata_max_output_tokens",
                   "strata_reasoning_budget_tokens"}
        if not isinstance(changes, dict) or not changes or any(k not in allowed or type(v) is not LOCAL_FIELDS[k] for k, v in changes.items()):
            raise ValueError("지원하지 않는 설정 또는 잘못된 형식입니다.")
        if changes.get("local_backend", self.settings.local_backend) == "strata" and any(k not in {
                "local_max_output_tokens", "desktop_max_output_tokens", "ollama_think", "local_backend",
                "strata_max_output_tokens", "strata_reasoning_budget_tokens"} for k in changes):
            raise ValueError("Strata는 서버에 설정된 모델 하나를 대화·코딩에 공유합니다. "
                             "여기서는 출력 길이와 Thinking·생각 상한만 변경할 수 있습니다. "
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
            # OpenCode must follow the selected coding engine/model as well.
            worker = self.core.opencode_worker
            if worker is not None:
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

    def process(self, message: str, attachments: list[str] | None = None, viewer: Viewer | None = None,
                session_id: str | None = None) -> dict[str, Any]:
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
            return self._process_in_session(message, attachments, viewer, sid)

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
                if not viewer.is_admin and text.split(None, 1)[0] not in MEMBER_COMMANDS:
                    output, keep_running, new_session = "이 명령은 관리자만 쓸 수 있습니다.", True, sid
                else:
                    output, keep_running, new_session = handle_command(self.core, sid, text)
                if not self.owns(viewer, new_session):
                    new_session = sid
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
            if route in ("/api/document", "/api/video", "/api/image") and (
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
            if route == "/api/state":
                self._json(HTTPStatus.OK, state.snapshot(viewer, query.get("session_id", [None])[0]))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        # ------------------------------------------------------------ 대화(대기열)
        def _wait_turn(self, ticket: Ticket, emit) -> bool:
            """차례가 오면 모델 잠금(state.lock)을 잡고 True. 기다리는 동안 앞에 몇 명이 있는지 알린다."""
            last, sent_at = None, 0.0
            while True:
                with state.queue_cv:
                    if ticket.cancel.is_set() or state.shutting_down:
                        return False
                    ahead = state.queue.index(ticket)
                    if ahead == 0 and state.lock.acquire(blocking=False):
                        return True
                    position = max(ahead, 1)
                now = time.monotonic()
                if position != last or now - sent_at > 10:  # 10초마다 한 번은 보내서 끊긴 연결을 알아챈다
                    emit({"type": "queue", "position": position})
                    last, sent_at = position, now
                with state.queue_cv:
                    state.queue_cv.wait(timeout=1.0)

        def _stream(self, message: str, attachments: list[str] | None = None, viewer: Viewer | None = None,
                    session_id: str | None = None, ticket: Ticket | None = None) -> None:
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

            previous = None
            previous_command_cancel_event = None
            previous_provider_cancel_events: dict[OllamaProvider, Any] = {}
            local: set[OllamaProvider] = set()
            acquired = False
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
                # the model lock. Snapshot the current providers here too: an
                # engine/settings operation may have replaced them while waiting.
                # (대기 중인 요청의 취소/끊김은 실행 중인 다른 방의 콜백과 취소 신호를 건드리지 않는다.)
                previous = state.core.response_chunk_callback
                previous_command_cancel_event = getattr(state.core, "command_cancel_event", None)
                local = {p for p in state.core.providers.values() if isinstance(p, OllamaProvider)}
                if isinstance(state.core.local_summarizer, OllamaProvider):
                    local.add(state.core.local_summarizer)
                previous_provider_cancel_events = {p: p.cancel_event for p in local}
                state.running = ticket
                state.active_id, state.active_kind = ticket.request_id, "chat"
                state.cancel_event = ticket.cancel
                started = time.monotonic()
                emit({"type": "running", "request_id": ticket.request_id, "session_id": session_id})
                state.core.response_chunk_callback = lambda chunk: emit({"type": "chunk", "text": chunk})
                state.core.command_cancel_event = state.cancel_event
                for p in local:
                    p.cancel_event = state.cancel_event
                    p.last_metrics = {}
                result = state.process(message, attachments, viewer=viewer, session_id=session_id)
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
                if acquired:
                    state.core.response_chunk_callback = previous
                    state.core.command_cancel_event = previous_command_cancel_event
                    for p, previous_cancel_event in previous_provider_cancel_events.items():
                        p.cancel_event = previous_cancel_event
                    state.running = None
                    state.active_id = None
                    state.active_kind = None
                    state.lock.release()
                with state.queue_cv:
                    if ticket in state.queue:
                        state.queue.remove(ticket)
                    state.queue_cv.notify_all()

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
            if self.path not in {"/api/project", "/api/chat", "/api/chat/stream", "/api/cancel", "/api/settings", "/api/workspace",
                                 "/api/shutdown", "/api/pull", "/api/session/delete", "/api/login", "/api/logout",
                                 "/api/local-owner", "/api/password", "/api/users", "/api/memory", "/api/task",
                                 "/api/session/new", "/api/session/rename"}:
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
                if self.path == "/api/session/new":  # 새 대화는 모델을 쓰지 않으니 차례를 기다리지 않는다
                    project_id = str(body.get("project_id") or "")
                    if project_id and state.core.storage.get_project(project_id, owner=viewer.id) is None:
                        raise ValueError("프로젝트를 찾을 수 없습니다.")
                    with state.queue_cv:
                        sid = state.core.storage.empty_session(viewer.id, project_id)
                        if sid and any(t.session_id == sid for t in state.queue):
                            sid = None  # 아직 메시지를 쓰기 전인 대기 중 방을 '새 대화'로 재사용하지 않는다
                        sid = sid or state.core.storage.create_session(viewer.id, project_id=project_id)
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
                if self.path == "/api/chat/stream":
                    # 접수할 때 방을 고정한다. 기다리는 동안 사용자가 다른 방을 골라도 바뀌지 않는다.
                    session_id = session_id or state.current_session(viewer)
                    message, attachments = self._chat_body(body, viewer, session_id)
                    if state.shutting_down:
                        self._json(HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."})
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
                        else:
                            ticket = Ticket(viewer.id, session_id=session_id)
                            state.queue.append(ticket)
                    if ticket is None:
                        self._json(HTTPStatus.CONFLICT, {"error": rejection})
                        return
                    self._stream(message, attachments, viewer, session_id, ticket)
                    return
                if not state.lock.acquire(blocking=False):
                    self._json(HTTPStatus.CONFLICT, {"error": "응답 생성 중입니다. 완료 또는 중단 후 다시 시도하세요."})
                    return
                # Normal requests release this below.
                try:
                    if state.shutting_down:
                        self._json(HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."})
                        return
                    if self.path == "/api/shutdown":
                        if state.queue:
                            self._json(HTTPStatus.CONFLICT, {"error": "다른 사용자의 요청이 처리 중입니다. 끝난 뒤 종료하세요."})
                            return
                        state.shutting_down = True
                        try:
                            cleanup = state.close_owned_services()
                            self._json(HTTPStatus.OK, {"stopped": True, **cleanup})
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
                    message, attachments = self._chat_body(body, viewer, session_id)
                    result = state.process(message, attachments, viewer=viewer, session_id=session_id)
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

        def _chat_body(self, body: dict, viewer: Viewer, session_id: str | None) -> tuple[str, list[str]]:
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
            return message, attachments

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
    parser.add_argument("--port", type=int, default=None, help=f"비우면 설정의 web_port(기본 {DEFAULT_PORT})")
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
    server = make_server(state, settings.web_port if args.port is None else args.port)
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
