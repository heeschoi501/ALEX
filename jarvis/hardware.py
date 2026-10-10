"""Hardware detection and simple performance profile selection.

No GPU package is required. NVIDIA information is read from nvidia-smi when
available; otherwise detection falls back gracefully.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import platform
import shutil
import subprocess


class PerformanceProfile(str, Enum):
    LAPTOP = "LAPTOP"
    DESKTOP = "DESKTOP"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class HardwareInfo:
    os_name: str
    cpu: str
    ram_gb: float | None
    gpu: str | None
    vram_gb: float | None
    profile: PerformanceProfile = PerformanceProfile.UNKNOWN


def _detect_ram_gb() -> float | None:
    """Return total RAM in GiB using stdlib where possible."""
    try:
        if os.name == "posix" and hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round((pages * page_size) / (1024**3), 1)
    except (ValueError, OSError, TypeError):
        pass

    # Windows fallback without adding psutil as a mandatory dependency.
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / (1024**3), 1)
    except (AttributeError, OSError):
        pass

    return None


def _find_nvidia_smi() -> str | None:
    """Return an nvidia-smi executable path, including the common WSL location."""
    found = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
    if found:
        return found
    # WSL2 can expose the Windows NVIDIA utility here even when PATH is minimal.
    wsl_path = "/usr/lib/wsl/lib/nvidia-smi"
    if os.path.isfile(wsl_path) and os.access(wsl_path, os.X_OK):
        return wsl_path
    return None


def _detect_nvidia() -> tuple[str | None, float | None]:
    """Read NVIDIA GPU name/VRAM and choose the GPU with the largest VRAM.

    Multi-GPU machines should not accidentally profile themselves from a small display GPU.
    """
    executable = _find_nvidia_smi()
    if not executable:
        return None, None

    cmd = [
        executable,
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=True)
        detected: list[tuple[str, float]] = []
        for line in result.stdout.strip().splitlines():
            name, memory_mib = [part.strip() for part in line.split(",", maxsplit=1)]
            detected.append((name, float(memory_mib)))
        if not detected:
            return None, None
        name, memory_mib = max(detected, key=lambda item: item[1])
        return name, round(memory_mib / 1024, 1)
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None, None


def choose_profile(info: HardwareInfo) -> PerformanceProfile:
    """Select a conservative profile based on RAM, GPU name and VRAM.

    WSL2 commonly exposes only a fraction of host RAM, so the known target GPUs are
    allowed lower *visible* RAM thresholds there. VRAM still has to match the class;
    arbitrary small-RAM native machines remain conservative.
    """
    gpu = (info.gpu or "").lower()
    ram = info.ram_gb or 0
    vram = info.vram_gb or 0
    os_name = (info.os_name or "").lower()
    is_wsl = "wsl" in os_name or "microsoft" in os_name

    # Known desktop target. WSL may show ~half of the 96 GiB host RAM.
    if "5080" in gpu and vram >= 15 and (ram >= 64 or (is_wsl and ram >= 24)):
        return PerformanceProfile.DESKTOP

    # Generic desktop-class fallback still requires ample visible host RAM.
    if vram >= 15 and ram >= 64:
        return PerformanceProfile.DESKTOP

    # Known laptop target. A 32 GiB Windows host can appear as ~16 GiB inside WSL2.
    if "4070" in gpu and vram >= 7 and (ram >= 24 or (is_wsl and ram >= 12)):
        return PerformanceProfile.LAPTOP

    # Conservative fallback for other >=7 GiB GPUs.
    if vram >= 7 and ram >= 24:
        return PerformanceProfile.LAPTOP

    return PerformanceProfile.UNKNOWN


def detect_hardware() -> HardwareInfo:
    """Collect hardware information without crashing on unsupported systems."""
    gpu, vram = _detect_nvidia()
    info = HardwareInfo(
        os_name=platform.platform(),
        cpu=platform.processor() or platform.machine() or "Unknown CPU",
        ram_gb=_detect_ram_gb(),
        gpu=gpu,
        vram_gb=vram,
    )
    info.profile = choose_profile(info)
    return info
