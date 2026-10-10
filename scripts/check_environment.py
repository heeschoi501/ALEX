"""Read-only environment check for a real Laptop/Desktop before JARVIS use."""
from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.app import NUM_CTX_BY_PROFILE, apply_profile_override, effective_local_models
from jarvis.config import ConfigError, load_settings
from jarvis.hardware import PerformanceProfile, _find_nvidia_smi, detect_hardware
from jarvis.providers import OllamaProvider
from jarvis.storage import Storage, StorageError


def main() -> int:
    print("Project JARVIS environment check")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"nvidia-smi: {_find_nvidia_smi() or 'NOT FOUND'}")
    print(f"ollama CLI: {shutil.which('ollama') or 'NOT FOUND'}")

    info = detect_hardware()
    print(f"CPU: {info.cpu}")
    print(f"RAM: {info.ram_gb} GiB")
    print(f"GPU: {info.gpu}")
    print(f"VRAM: {info.vram_gb} GiB")
    print(f"Profile: {info.profile.value}")
    print(f"Recommended num_ctx: {NUM_CTX_BY_PROFILE[info.profile]}")

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Config: ERROR\n{exc}")
        return 2

    profile_note = apply_profile_override(info, settings.force_profile)
    if profile_note:
        print(profile_note)
        print(f"Effective profile: {info.profile.value}")
        print(f"Effective num_ctx: {NUM_CTX_BY_PROFILE[info.profile]}")
    print(f"File root: {settings.file_search_root.resolve()}")
    print(f"Cloud provider: {settings.cloud_provider}")
    print(f"Cloud approval required: {settings.require_cloud_approval}")
    print(f"Cloud sensitive-data block: {settings.cloud_block_sensitive}")
    try:
        storage = Storage(settings.database_path)
        ok, detail = storage.integrity_check()
        print(f"SQLite: {'OK' if ok else 'FAIL'} ({detail})")
        print(f"SQLite stats: {storage.database_stats()}")
    except StorageError as exc:
        print(f"SQLite: ERROR ({exc})")
        return 3
    if not settings.use_ollama:
        print("Ollama mode: OFF (set JARVIS_USE_OLLAMA=1 for real local model)")
        return 0

    print(f"Ollama host: {settings.ollama_host}")
    print(f"Ollama think: {settings.ollama_think}")
    print(f"Ollama max output tokens: {settings.local_max_output_tokens}")
    print(f"Ollama keep_alive: {settings.ollama_keep_alive}")
    general_model, coder_model = effective_local_models(settings, info.profile)
    models = [("general", general_model)]
    if info.profile != PerformanceProfile.DESKTOP and settings.single_model_on_laptop:
        print("Coder route: shares general model (Laptop single-model mode)")
    else:
        models.append(("coder", coder_model))
    readiness: list[bool] = []
    for label, model in models:
        provider = OllamaProvider(
            model, settings.ollama_host, health_ttl=0,
            num_predict=settings.local_max_output_tokens,
            keep_alive=settings.ollama_keep_alive,
            think=settings.ollama_think,
        )
        ready = provider.health_check()
        readiness.append(ready)
        print(f"Ollama {label}: {model} -> {'READY' if ready else 'NOT READY'}")

    platform_text = platform.platform().lower()
    is_wsl = "wsl" in platform_text or "microsoft" in platform_text
    if is_wsl and readiness and not all(readiness):
        print(
            "WSL note: Windows 쪽 Ollama가 localhost에서 보이지 않는 환경이면 Windows host IP를 "
            "JARVIS_OLLAMA_HOST로 지정할 수 있습니다. 이 경우 신뢰한 로컬 host인지 확인하고 "
            "JARVIS_ALLOW_REMOTE_OLLAMA=1을 명시해야 합니다. API를 외부 네트워크에 공개하지 마세요."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
