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
from dataclasses import asdict, dataclass, field, replace
import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import os
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs, unquote
import webbrowser
import threading
from typing import Any

from .app import build_core, build_providers, build_summary_provider, handle_command, provider_runtime_settings, CONTEXT_CHAR_BUDGET_BY_PROFILE
from .config import ConfigError, Settings, load_settings, validate, LOCAL_FIELDS, local_config_path
from .core import JarvisCore
from .providers import GenerationCancelled, OllamaProvider, StrataProvider
from .imagegen import ImageGenError
from .router import Destination
from .launcher import code_fingerprint
from .http_control import interrupt_on_cancel
from .imagegen import IMAGE_TYPES, MAX_UPLOAD_BYTES
from .documents import download_path, FORMATS, MAX_DOWNLOAD

# 이 서버가 **불러온** 코드의 지문. 모듈을 읽는 순간에 잰다 — /health 를 받을 때 재면
# 그 사이 디스크에서 바뀐 코드를 자기 것처럼 보고하게 된다. 런처가 이 값으로 옛 서버를 알아본다.
LOADED_CODE = code_fingerprint()

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64_000

STATIC = Path(__file__).with_name("static")
PAGE = (STATIC / "index.html").read_text(encoding="utf-8")


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

    def __post_init__(self) -> None:
        # Web Alpha has no interactive confirmation surface. Even if the global CLI
        # setting disabled approval, entering the Web surface must restore fail-closed
        # Cloud behavior instead of silently inheriting that weaker policy.
        self.core.require_cloud_approval = True
        self.core.approval_callback = lambda _request: False

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

    def snapshot(self) -> dict[str, Any]:
        sid = self.session_id
        storage = self.core.storage
        providers = [self.core.providers[d] for d in (Destination.LOCAL_GENERAL, Destination.LOCAL_CODER)]
        return {
            "session_id": sid, "cloud_web_approval": False, "local_only": True,
            "sessions": [asdict(s) for s in storage.list_sessions(100)],
            **storage.message_page(sid),
            "tasks": [asdict(t) for t in storage.list_tasks(include_done=True)],
            "memories": storage.list_memories(100),
            "project_root": str(self.core.current_project_root() or ""),
            "profile": self.core.hardware.profile.value, "gpu": self.core.hardware.gpu,
            "models": [p.name for p in providers],
            "effective_settings": {
                "general": provider_runtime_settings(providers[0]),
                "coder": provider_runtime_settings(providers[1]),
                "shared": providers[0] is providers[1],
            },
            "mock": not isinstance(providers[0], OllamaProvider),
            "busy": self.active_id is not None, "request_id": self.active_id,
            "active_kind": self.active_kind, "shutting_down": self.shutting_down,
            "settings": {k: getattr(self.settings, k) for k in LOCAL_FIELDS} if self.settings else {},
        }

    def model_status(self) -> dict:
        from .model_registry import get_candidates, get_ollama_storage_info, estimate_memory_fit
        
        p = self.core.providers[Destination.LOCAL_GENERAL]
        c = self.core.providers[Destination.LOCAL_CODER]
        local = isinstance(p, OllamaProvider)
        installed = p._fetch_installed_models() if local else set()
        loaded = p._fetch_loaded_models() if local else set()
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
            "installed": sorted(installed),
            "loaded": sorted(loaded),
            "selected_general": p.model if local else "",
            "selected_coder": c.model if isinstance(c, OllamaProvider) else "",
            "catalog": catalog,
            "storage": storage_info,
            "mock": not local
        }

    def save_settings(self, changes: dict) -> dict:
        if self.settings is None:
            raise ValueError("설정 변경을 지원하지 않는 실행 모드입니다.")
        allowed = {"general_model", "coder_model", "desktop_general_model", "desktop_coder_model",
                   "auto_profile_models", "single_model_on_laptop", "local_max_output_tokens",
                   "desktop_max_output_tokens", "ollama_think", "num_ctx"}
        if not isinstance(changes, dict) or not changes or any(k not in allowed or type(v) is not LOCAL_FIELDS[k] for k, v in changes.items()):
            raise ValueError("지원하지 않는 설정 또는 잘못된 형식입니다.")
        for k in changes:
            if "JARVIS_" + k.upper() in os.environ:
                raise ValueError(f"{k}: 환경변수로 고정되어 있습니다. 환경변수를 해제하고 재시작하세요.")
        candidate = validate(replace(self.settings, **changes))
        general, coder, notes = build_providers(candidate, self.core.hardware)
        for provider in {general, coder}:
            if not provider.health_check():
                raise ValueError(f"설치되지 않은 모델입니다: {provider.name}")
        path = local_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(json.dumps({k: getattr(candidate, k) for k in LOCAL_FIELDS}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self.settings = candidate
        self.core.providers[Destination.LOCAL_GENERAL] = general
        self.core.providers[Destination.LOCAL_CODER] = coder
        self.core.local_summarizer = build_summary_provider(general, candidate)
        self.core.bind_gpu_guards()
        return {"saved": True, "notes": notes}

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

    def delete_session(self, target: Any, confirmed: Any) -> dict[str, Any]:
        """대화 목록의 [×]. 2026-09-26 사용자: "대화목록 제거 기능도 필요할거 같은데" — `/delete-session ID --yes` 를
        외워 치지 않게 한다. 지우는 범위는 그 명령과 같다(대화·메시지·라우팅 기록). 거기서 배운 것(교훈)은 남는다."""
        if confirmed is not True:
            raise ValueError("삭제 확인이 필요합니다.")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("삭제할 대화가 필요합니다.")
        target = target.strip()
        if not self.core.storage.delete_session(target):
            raise ValueError("대화를 찾을 수 없습니다. 새로고침해 주세요.")
        if (self.core.last_exchange or {}).get("session_id") == target:
            self.core.last_exchange = None  # 지운 대화의 질문·답을 메모리에도 남기지 않는다
        if target == self.session_id:
            self.session_id = self.core.get_or_create_session(resume=False)
            self.core.last_route_event_id = None
            self.core.storage.set_state("web.current_session", self.session_id)
        return {"deleted": target, "session_id": self.session_id}

    def process(self, message: str) -> dict[str, Any]:
        text = message.strip()
        if not text:
            raise ValueError("메시지가 비어 있습니다.")
        if len(text) > 32_000:
            raise ValueError("메시지가 너무 깁니다. 32000자 이하로 줄여 주세요.")

        with self.lock:
            before_count = self.core.storage.message_count(self.session_id)
            if text.startswith("/"):
                output, keep_running, new_session = handle_command(self.core, self.session_id, text)
                self.session_id = new_session
                self.core.storage.set_state("web.current_session", new_session)
                if not keep_running:  # Web server 자체를 /exit 로 종료하지 않는다.
                    output = "Web UI에서는 /exit로 서버를 종료하지 않습니다. 터미널에서 Ctrl+C를 사용하세요."
                return {
                    "response": output,
                    "route": "command",
                    "session_id": self.session_id,
                }

            response, decision = self.core.handle(self.session_id, text)
            if before_count == 0:
                self.core.storage.rename_session(self.session_id, text[:60])
            self.core.storage.set_state("web.current_session", self.session_id)
            return {
                "response": response,
                "route": decision.destination.value,
                "session_id": self.session_id,
            }


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
                "connect-src 'self'; img-src blob:; media-src blob:; frame-ancestors 'none'; base-uri 'none'",  # 그림은 토큰으로 받아 blob 으로만 보인다(ADR-073)
            )

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._security_headers("application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _upload_image(self) -> None:
            """채팅창 '그림 올리기'(ADR-074 보충) — 그림 바이트를 그대로 받는다. 토큰 필수, PNG·JPEG·WebP, 10MB 까지."""
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            if state.core.image_generator is None:
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
            if self.headers.get("X-Jarvis-Session", state.session_id) != state.session_id:
                self._json(HTTPStatus.CONFLICT, {"error": "다른 탭에서 대화가 바뀌었습니다. 새로고침 후 다시 올려 주세요."})
                return
            if not state.lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "응답 생성 중입니다. 완료 또는 중단 후 다시 시도하세요."})
                return
            try:
                reply = state.core.add_reference_picture(state.session_id, data, unquote(self.headers.get("X-File-Name", ""))[:120])
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            finally:
                state.lock.release()
            self._json(HTTPStatus.OK, {"response": reply, "session_id": state.session_id})

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Jarvis-Token", ""), state.token)

        def _valid_host(self) -> bool:
            # Binding to loopback alone does not stop DNS rebinding: a hostile domain
            # can resolve to 127.0.0.1 while keeping its Host header. Never expose the
            # tokenized page to non-local Host names.
            raw = self.headers.get("Host", "").strip().lower()
            if raw.startswith("[") and "]" in raw:
                host = raw[1:raw.index("]")]
            else:
                host = raw.split(":", 1)[0]
            return host in {"127.0.0.1", "localhost", "::1"}

        def _host_guard(self) -> bool:
            if self._valid_host():
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local host"})
            return False

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
                self.send_response(HTTPStatus.OK)
                self._security_headers("text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path in {"/app.js", "/style.css"}:
                body = (STATIC / self.path[1:]).read_bytes()
                self.send_response(HTTPStatus.OK)
                self._security_headers("text/javascript; charset=utf-8" if self.path.endswith(".js") else "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/") and not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                return
            if self.path == "/api/models":
                self._json(HTTPStatus.OK, state.model_status())
                return
            if self.path.startswith("/api/document?"):
                name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
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
                self.send_response(HTTPStatus.OK)
                self._security_headers(FORMATS[path.suffix[1:]])
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/video?"):
                # ADR-075: 앱이 만든 MP4 만 — 이름 모양을 확인하고 data/videos 바로 아래에서만 찾는다.
                generator = state.core.image_generator
                name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
                path = generator.video_path(name) if generator is not None else None
                if path is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "video not found"})
                    return
                body = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self._security_headers("video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/image?"):
                # ADR-073: 앱이 그린 PNG 만 내준다 — 이름 모양을 확인하고 data/images 바로 아래에서만 찾는다.
                generator = state.core.image_generator
                name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
                path = generator.image_path(name) if generator is not None else None
                if path is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "image not found"})
                    return
                body = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self._security_headers(IMAGE_TYPES.get(path.suffix.lower(), "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/history?"):
                try:
                    query = parse_qs(urlparse(self.path).query)
                    sid = query.get("session_id", [state.session_id])[0]
                    before = int(query["before"][0])
                    self._json(HTTPStatus.OK, state.core.storage.message_page(sid, before))
                except (KeyError, ValueError):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid history cursor"})
                return
            if self.path == "/api/state":
                if not self._authorized():
                    self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local token"})
                    return
                self._json(HTTPStatus.OK, state.snapshot())
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _stream(self, message: str) -> None:
            state.active_id = secrets.token_hex(12)
            state.active_kind = "chat"
            state.cancel_event.clear()
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
                    state.cancel_event.set()

            previous = state.core.response_chunk_callback
            previous_command_cancel_event = getattr(state.core, "command_cancel_event", None)
            local = {p for p in state.core.providers.values() if isinstance(p, OllamaProvider)}
            if isinstance(state.core.local_summarizer, OllamaProvider):
                local.add(state.core.local_summarizer)
            started = time.monotonic()
            try:
                self.send_response(HTTPStatus.OK)
                self._security_headers("application/x-ndjson; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                emit({"type": "start", "request_id": state.active_id})
                state.core.response_chunk_callback = lambda chunk: emit({"type": "chunk", "text": chunk})
                state.core.command_cancel_event = state.cancel_event
                for p in local:
                    p.cancel_event = state.cancel_event
                    p.last_metrics = {}
                result = state.process(message)
                metrics = next((p.last_metrics for p in local if p.last_metrics), {})
                emit({"type": "done", **result, "metrics": metrics, "elapsed": round(time.monotonic() - started, 2)})
            except GenerationCancelled:
                emit({"type": "done", "response": "응답 생성을 중단했습니다. 파일 변경은 적용되지 않았습니다.", "session_id": state.session_id})
            except Exception:
                emit({"type": "error", "error": "요청을 완료하지 못했습니다. 새로고침해 저장된 대화를 확인하세요."})
            finally:
                state.core.response_chunk_callback = previous
                state.core.command_cancel_event = previous_command_cancel_event
                for p in local:
                    p.cancel_event = None
                state.active_id = None
                state.active_kind = None

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._host_guard():
                return
            if self.path == "/api/image/upload":
                self._upload_image()
                return
            if self.path not in {"/api/chat", "/api/chat/stream", "/api/cancel", "/api/settings", "/api/workspace", "/api/shutdown", "/api/pull", "/api/session/delete"}:
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
                if self.path == "/api/cancel":
                    if not state.active_id or body.get("request_id") != state.active_id:
                        self._json(HTTPStatus.CONFLICT, {"error": "중단할 요청이 없습니다."})
                    else:
                        state.cancel_event.set()
                        self._json(HTTPStatus.OK, {"cancelled": True})
                    return
                if body.get("session_id", state.session_id) != state.session_id:
                    self._json(HTTPStatus.CONFLICT, {"error": "다른 탭에서 대화가 바뀌었습니다. 새로고침 후 전송하세요."})
                    return
                if not state.lock.acquire(blocking=False):
                    self._json(HTTPStatus.CONFLICT, {"error": "응답 생성 중입니다. 완료 또는 중단 후 다시 시도하세요."})
                    return
                # Normal requests release this below; streaming transfers work to its own worker.
                try:
                    if state.shutting_down:
                        self._json(HTTPStatus.CONFLICT, {"error": "앱을 종료하는 중입니다."})
                        return
                    if self.path == "/api/shutdown":
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
                    if self.path == "/api/session/delete":
                        self._json(HTTPStatus.OK, state.delete_session(body.get("target"), body.get("confirmed")))
                        return
                    if self.path == "/api/pull":
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
                        state.cancel_event.clear()
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
                        return
                    
                    message = body.get("message")
                    if not isinstance(message, str) or not message.strip() or len(message) > 32000:
                        raise ValueError("메시지는 1~32000자 문자열이어야 합니다.")
                    if self.path == "/api/chat/stream":
                        self._stream(message)
                        return
                    result = state.process(message)
                finally:
                    state.lock.release()
                message = body.get("message")
                if not isinstance(message, str):
                    raise ValueError("message 문자열이 필요합니다.")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception:
                # 브라우저에 traceback/secret/path를 노출하지 않는다. 상세 내용은 core log에서 확인.
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "ALEX 내부 오류"})
                return
            self._json(HTTPStatus.OK, result)

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
