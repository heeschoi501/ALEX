"""File filtering shared by OpenCode's external-model worker and coding sessions.

This limits the copied inputs; it does not sandbox the child process or its tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat

SECRET_NAMES = re.compile(r"(?i)^(\.env(\..*)?|id_rsa.*|id_ed25519.*|auth\.json|credentials.*|secrets?\..*|\.netrc|\.npmrc|\.pypirc)$")
SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".keystore", ".jks"}
SCAN_MAX_BYTES = 8_000_000
CONTENT_GENERIC = ("API key/token 형태", "password 형태", "private key 블록")
PRIVATE_KEY_BODY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=\s]{40,}")
FREE_COPY_LIMIT = 300 * 1024 * 1024
FREE_SKIP = {"__pycache__", ".git", ".opencode", ".claude", ".codex", "node_modules", ".pytest_cache",
             "logs", "data", "growth", "runtime", "dist", "tools", "venv", ".venv", ".venv-alex-study",
             ".codex-remote-attachments", "jarvis.local.json", ".env"}


def secret_file(name: str) -> bool:
    return name.lower() == "jarvis.local.json" or Path(name).suffix.lower() in SECRET_SUFFIXES or SECRET_NAMES.match(name) is not None


def is_link(path: Path) -> bool:
    """Also exclude Windows junctions/reparse points, which need not be symlinks."""
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def withhold(path: Path) -> bool:
    """Exclude secret filenames and recognizable keys in readable text; binary content is not scanned.

    Generic words such as ``password: str`` and ``api_key=`` also occur in ordinary
    source code. Only concrete key patterns and private-key bodies exclude a file.
    """
    if secret_file(path.name):
        return True
    try:
        with path.open("rb") as stream:
            head = stream.read(4096)
            if b"\0" in head:
                return False
            if path.stat().st_size > SCAN_MAX_BYTES:
                return True
            data = head + stream.read()
    except OSError:
        return True
    from .core import SENSITIVE_HINTS  # imported lazily: core also imports the worker
    text = data.decode("utf-8", errors="ignore")
    return PRIVATE_KEY_BODY.search(text) is not None or any(
        pattern.search(text) for label, pattern in SENSITIVE_HINTS if label not in CONTENT_GENERIC)


def free_path_blocked(relative: str, withheld: set[str]) -> bool:
    """Protect excluded files and entire excluded directory subtrees on import."""
    parts = Path(relative).parts
    if any(part.lower() in FREE_SKIP or secret_file(part) for part in parts):
        return True
    blocked = {os.path.normcase(p) for p in withheld}
    return any(os.path.normcase(Path(*parts[:n]).as_posix()) in blocked for n in range(1, len(parts) + 1))


def copy_for_free(source: Path, target: Path) -> list[str]:
    """Copy a bounded project snapshot without private stores, secret files or links.

    Return excluded paths so a model cannot recreate them and overwrite originals.
    The source must remain trusted and unchanged during copying; this is not an
    operating-system boundary against concurrent filesystem changes.
    """
    source = Path(source)
    withheld: list[str] = []
    allowed: set[Path] = set()
    total = 0
    for directory, dirs, files in os.walk(source, followlinks=False):
        here = Path(directory)
        for name in list(dirs):
            path = here / name
            if name.lower() in FREE_SKIP:
                dirs.remove(name)
            elif is_link(path) or secret_file(name):
                dirs.remove(name)
                withheld.append(path.relative_to(source).as_posix())
            else:
                allowed.add(path)
        for name in files:
            path = here / name
            if name.lower() in FREE_SKIP or name.endswith(".pyc"):
                continue
            if is_link(path) or not path.is_file() or withhold(path):
                withheld.append(path.relative_to(source).as_posix())
                continue
            total += path.stat().st_size
            if total > FREE_COPY_LIMIT:
                raise ValueError("복사할 파일이 300MB 를 넘습니다 — 무료 모델은 더 좁은 프로젝트 폴더로 실행해 주세요.")
            allowed.add(path)

    def ignore(directory, names):
        here = Path(directory)
        return {n for n in names if here / n not in allowed or is_link(here / n)}

    shutil.copytree(source, target, ignore=ignore)
    return sorted(withheld)
