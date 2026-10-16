"""Local engine preparation and GPU handoff; never download or generate a model."""
from __future__ import annotations

import json
import os
import subprocess
import time

from .config import Settings
from .hardware import PerformanceProfile
from . import launcher
from .providers import OllamaProvider, ProviderError, loaded_local_models, unload_local_models
from .strata_runtime import validate_strata_health


def stored_ollama_models(host: str) -> set[str]:
    """Offline preview of the bundled store, only for the launcher's fixed address."""
    if host.rstrip("/") != launcher.OLLAMA:
        return set()
    root = launcher.ROOT / "runtime" / "models" / "manifests" / "registry.ollama.ai" / "library"
    names = set()
    if not root.is_dir():
        return names
    for path in root.glob("*/*"):
        try:
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict) and isinstance(manifest.get("layers"), list) and manifest["layers"]:
                names.add(f"{path.parent.name}:{path.name}")
        except (OSError, ValueError):
            continue
    return names


def backend_available(settings: Settings, *, validate_config: bool = True) -> bool:
    host, endpoint = (settings.strata_host, "/health") if settings.local_backend == "strata" else (settings.ollama_host, "/api/tags")
    try:
        value = launcher.get_json(host + endpoint)
    except (OSError, ValueError):
        return False
    if settings.local_backend == "strata":
        if not validate_config:
            # An inactive server is tracked only to release its GPU. Its model need not match the selected one.
            return value.get("service") == "strata" and not value.get("error")
        validate_strata_health(value, settings.strata_model, settings.strata_num_ctx)
        return True
    return isinstance(value.get("models"), list)


def check_backend_install(settings: Settings) -> None:
    """Check prerequisites before releasing the current working model."""
    if backend_available(settings):
        return
    if settings.local_backend == "strata":
        try:
            launcher.strata_command(launcher.strata_dir(), int(settings.strata_host.rsplit(":", 1)[1]), settings.strata_config)
        except (RuntimeError, OSError) as exc:
            raise ValueError(str(exc)) from exc
    elif settings.ollama_host.rstrip("/") != launcher.OLLAMA:
        raise ValueError("선택한 Ollama 서버에 연결하지 못했습니다. 서버 주소의 Ollama를 먼저 실행하세요.")
    elif not (launcher.ROOT / "runtime" / "ollama" / "ollama.exe").is_file():
        raise ValueError("Ollama 실행 파일이 없습니다. setup_local.bat으로 먼저 준비하세요.")


def start_backend(settings: Settings, profile: PerformanceProfile, models: tuple[str, ...] = ()) -> None:
    """Use the normal local runtime and launch environment; no model generation."""
    if backend_available(settings):
        return
    check_backend_install(settings)
    log_dir = launcher.ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    strata = settings.local_backend == "strata"
    command = launcher.strata_command(launcher.strata_dir(), int(settings.strata_host.rsplit(":", 1)[1]),
                                      settings.strata_config) if strata else [str(launcher.ROOT / "runtime" / "ollama" / "ollama.exe"), "serve"]
    environment = os.environ.copy() if strata else launcher.ollama_environment(profile, models)
    with (log_dir / ("strata-server.log" if strata else "ollama-server.log")).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=launcher.strata_dir() if strata else launcher.ROOT, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
    runtime = launcher.ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / ("strata.pid" if strata else "ollama.pid")).write_text(str(process.pid), encoding="utf-8")
    try:
        # Strata 는 런처(ensure_strata)와 같이 15분까지 기다린다. 2026-10-16 노트북의 Qwen3.6-35B 는 다른 프로그램이
        # 20GB 를 쓰는 상태에서 켜는 데 275초가 걸렸다 — 240초면 다 올라오기 직전에 끊긴다.
        deadline = time.monotonic() + (900 if strata else 20)
        while time.monotonic() < deadline:
            if backend_available(settings):
                if not strata:
                    os.environ["JARVIS_OLLAMA_MODELS"] = environment["OLLAMA_MODELS"]
                return
            if process.poll() is not None:
                raise ValueError(f"{'Strata' if strata else 'Ollama'} 서버가 시작되지 않았습니다. 서버 로그를 확인하세요.")
            time.sleep(.25)
        raise ValueError("모델 서버 시작 시간이 초과되었습니다. 서버 로그를 확인하세요.")
    except BaseException:
        # A delayed cold start must not load onto the GPU after a failed switch.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise


def release_current(providers: list[object], *, timeout: float = 15) -> list[str]:
    """The target engine must not start until every old server confirms empty."""
    servers = {(type(p), p.host): p for p in providers if isinstance(p, OllamaProvider)}
    released = []
    try:
        for provider in servers.values():
            released.extend(unload_local_models(provider, timeout=timeout))
            deadline = time.monotonic() + timeout
            while loaded_local_models(provider, timeout=min(5, timeout)):
                if time.monotonic() >= deadline:
                    raise ProviderError("이전 모델이 아직 실행 중이어서 GPU 반환을 확인하지 못했습니다.")
                time.sleep(.2)
    except ProviderError as exc:
        raise ValueError(f"엔진 전환을 중단했습니다: {exc}") from exc
    return released
