"""Show or explicitly pull the Ollama models required by the effective hardware profile.

Default is DRY RUN. Downloading multi-GB models requires `--pull` so JARVIS never surprises the
user with a large network/storage operation.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.app import apply_profile_override, effective_local_models
from jarvis.config import load_settings
from jarvis.hardware import PerformanceProfile, detect_hardware


def required_models(settings, profile: PerformanceProfile) -> list[str]:
    general, coder = effective_local_models(settings, profile)
    models = [general]
    if not (profile != PerformanceProfile.DESKTOP and settings.single_model_on_laptop):
        models.append(coder)
    return list(dict.fromkeys(models))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project JARVIS Ollama model setup")
    parser.add_argument("--pull", action="store_true", help="actually run `ollama pull` for required models")
    args = parser.parse_args(argv)

    settings = load_settings()
    hardware = detect_hardware()
    apply_profile_override(hardware, settings.force_profile)
    models = required_models(settings, hardware.profile)
    print(f"Effective profile: {hardware.profile.value}")
    print("Required models:")
    for model in models:
        print(f"  - {model}")

    if not args.pull:
        print("\nDry run only. To download them explicitly: python scripts/setup_models.py --pull")
        return 0

    executable = shutil.which("ollama") or shutil.which("ollama.exe")
    if not executable:
        print("Ollama CLI를 찾지 못했습니다. 먼저 Ollama를 설치하고 PATH를 확인하세요.")
        return 2
    for model in models:
        print(f"\n=== ollama pull {model} ===")
        try:
            subprocess.run([executable, "pull", model], check=True)
        except subprocess.CalledProcessError as exc:
            print(f"모델 다운로드 실패: {model} (exit={exc.returncode})")
            return exc.returncode or 3
    print("\n모델 준비 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
