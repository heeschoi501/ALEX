"""Verify that runtime package imports stay Python-stdlib/local only.

Tests and developer scripts may import the local `jarvis` package. v0.1.x deliberately avoids
mandatory third-party runtime frameworks; this audit catches accidental imports before release.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
STDLIB = set(getattr(sys, "stdlib_module_names", ()))
LOCAL_TOP_LEVEL = {"jarvis", "scripts", "tests"}
# 별도 환경에서만 도는 파일과 그 파일에만 허용하는 패키지. ALEX 앱 환경에는 없고 필요도 없다 —
# LoRA 학습 일꾼은 C:\ALEX\tools\lora\venv 의 파이썬으로만 실행된다(ADR-084). 목록 밖 패키지는 여전히 실패.
SEPARATE_ENV = {"scripts/lora_worker.py": {"torch", "transformers", "peft", "huggingface_hub"},
                "scripts/pdf_extract_worker.py": {"pypdf"}}
# Only the isolated generated-code worker may optionally prewarm pygame fonts.
# It is never imported by the Core at startup and is not a mandatory dependency.
OPTIONAL_WORKER_IMPORTS = {"jarvis/build_runner.py": {"pygame"}}

# Python 3.10 has `sys.stdlib_module_names`; fallback is only for unusual interpreters.
FALLBACK_STDLIB = {
    "abc", "argparse", "contextlib", "ctypes", "dataclasses", "datetime", "enum", "functools",
    "hashlib", "html", "http", "json", "logging", "os", "pathlib", "platform", "re", "secrets",
    "shutil", "sqlite3", "subprocess", "sys", "tarfile", "tempfile", "threading", "time", "typing",
    "unittest", "urllib", "uuid", "webbrowser", "zipfile",
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def audit() -> list[tuple[Path, str]]:
    allowed = STDLIB or FALLBACK_STDLIB
    sibling_scripts = {path.stem for path in (ROOT / "scripts").glob("*.py")}  # `from experiences import …` 도 로컬
    failures: list[tuple[Path, str]] = []
    for folder in (ROOT / "jarvis", ROOT / "scripts"):
        for path in sorted(folder.glob("*.py")):
            relative = path.relative_to(ROOT)
            local = LOCAL_TOP_LEVEL | (sibling_scripts if folder.name == "scripts" else set())
            for root in sorted(imported_roots(path)):
                if (root not in allowed and root not in local
                        and root not in SEPARATE_ENV.get(relative.as_posix(), ())
                        and root not in OPTIONAL_WORKER_IMPORTS.get(relative.as_posix(), ())):
                    failures.append((relative, root))
    return failures


def main() -> int:
    failures = audit()
    if failures:
        for path, package in failures:
            print(f"FAIL {path}: external import {package!r}")
        return 1
    print("Dependency audit PASS: Core stdlib/local; explicit optional worker imports only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
