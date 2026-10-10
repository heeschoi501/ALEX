"""Verified, manually maintained Ollama model catalog.

Catalog entries are candidates for an explicit download, not a claim that the
model will run on this device. No online discovery or download happens here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .hardware import HardwareInfo


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    tag: str
    params_b: float | None
    active_params_b: float | None
    quantization: str | None
    download_size_gb: float | None  # Decimal GB, as displayed by Ollama.
    family: str
    source_url: str
    last_verified: str
    architecture: str | None = None
    ngram_embedding_params_b: float | None = None
    runtime: str = "gguf"


# Exact Ollama tags only. Each value below is taken from its linked tag page.
# Keep unverified tags out of the download allowlist.
_CATALOG = [
    ModelCandidate(
        tag="mistral-large:123b",
        params_b=123.0,
        active_params_b=123.0,
        quantization="Q4_K_M",
        download_size_gb=73.0,
        family="mistral",
        source_url="https://ollama.com/library/mistral-large:123b",
        last_verified="2026-09-25",
        architecture="Dense",
    ),
    ModelCandidate(
        tag="joefamous/grok-1:314b-q2_K",
        params_b=316.0,  # The tag says 314b; Ollama's model details report 316B.
        active_params_b=None,
        quantization="Q2_K",
        download_size_gb=116.0,
        family="grok",
        source_url="https://ollama.com/joefamous/grok-1:314b-q2_K",
        last_verified="2026-09-25",
        architecture="MoE",
    ),
    ModelCandidate(
        tag="qwen3.8-flash-next:125b-a6b-q4_K_M",
        params_b=125.0,  # MoE body; 51B n-gram embedding parameters are additional.
        active_params_b=6.0,
        quantization="Q4_K_M",
        download_size_gb=120.0,
        family="qwen",
        source_url="https://ollama.com/library/qwen3.8-flash-next:125b-a6b-q4_K_M",
        last_verified="2026-09-25",
        architecture="MoE",
        ngram_embedding_params_b=51.0,
    ),
]


def get_candidates(hardware: HardwareInfo | None = None,
                   min_params_b: float = 122.0, max_params_b: float = 397.0) -> list[ModelCandidate]:
    """Return verified candidates in the exclusive parameter range.

    MLX variants are hidden from the default Windows/NVIDIA catalog. This is
    presentation filtering, not a runtime prohibition on installed models.
    """
    windows_or_nvidia = bool(hardware and (
        "windows" in hardware.os_name.lower() or "nvidia" in (hardware.gpu or "").lower()
    ))
    return [candidate for candidate in _CATALOG
            if candidate.params_b is not None
            and min_params_b < candidate.params_b < max_params_b
            and "deepseek" not in candidate.tag.lower()
            and "deepseek" not in candidate.family.lower()
            and not (windows_or_nvidia and
                     (candidate.runtime.lower() == "mlx" or "-mlx" in candidate.tag.lower()))]


def estimate_memory_fit(candidate: ModelCandidate, hardware: HardwareInfo) -> dict[str, str]:
    """Conservative indication only; it never gates downloads or execution.

    Reserve host RAM for the OS, VRAM for the display/runtime, and additional
    memory for Ollama and KV cache. The download size is only a proxy for model
    weights; quantization, context length and offload behavior vary.
    """
    if candidate.download_size_gb is None or hardware.ram_gb is None or hardware.vram_gb is None:
        return {"status": "unknown", "label": "메모리 추정 불가"}
    weights_gib = candidate.download_size_gb * 1_000_000_000 / (1024 ** 3)
    host_reserve = max(8.0, hardware.ram_gb * 0.15)
    gpu_reserve = max(2.0, hardware.vram_gb * 0.15)
    runtime_kv_reserve = max(6.0, weights_gib * 0.08)
    available_gib = max(0.0, hardware.ram_gb - host_reserve) + max(0.0, hardware.vram_gb - gpu_reserve)
    if available_gib < weights_gib + runtime_kv_reserve:
        return {"status": "insufficient_estimated", "label": "메모리 부족 예상"}
    return {"status": "unverified", "label": "메모리 여유 추정 · 실행 미검증"}


def get_ollama_storage_info(models_dir: Path | None) -> dict:
    """Report free disk only when the running Ollama store path is known."""
    if models_dir is None:
        return {"path": None, "free_gb": None, "error": "Ollama 저장 경로 확인 불가"}
    try:
        if not models_dir.is_dir():
            return {"path": str(models_dir), "free_gb": None, "error": "저장 경로 확인 불가 (폴더 없음)"}
        usage = shutil.disk_usage(models_dir)
        return {"path": str(models_dir), "free_gb": round(usage.free / (1024 ** 3), 1), "error": None}
    except OSError:
        return {"path": str(models_dir), "free_gb": None, "error": "저장 공간 확인 불가"}
