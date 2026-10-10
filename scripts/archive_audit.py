"""Audit a Project JARVIS release archive without extracting it.

Checks that handoff archives contain the expected source/docs and do not leak
runtime state such as data/, logs/, caches, or nested dist artifacts.
"""
from __future__ import annotations

import argparse
import re
import stat
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

FORBIDDEN_PARTS = {
    ".git", ".idea", ".pytest_cache", ".mypy_cache", "__pycache__",
    ".venv", ".venv-alex-study", "venv", "data", "logs", "dist",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx"}
SENSITIVE_BASENAMES = {
    ".env", "credentials", "credentials.json", "secrets.json", "secrets.toml",
    "id_rsa", "id_ed25519", "api_key.txt", "apikey.txt",
}


def _sensitive_name(name: str) -> bool:
    lower = name.lower()
    return lower in SENSITIVE_BASENAMES or lower.startswith(".env.")
REQUIRED_RELATIVE = {
    "README.md",
    "RELEASE_NOTES.md",
    "CLAUDE_NEXT.md",
    "CLAUDE_START_PROMPT.md",
    "DECISIONS.md",
    "TODO.md",
    "TEST_RESULTS.md",
    "SECURITY.md",
    "STUDY_GUIDE.md",
    "HARDWARE_VALIDATION.md",
    "study/README.md",
    "pyproject.toml",
    "jarvis/__init__.py",
    "jarvis/app.py",
    "jarvis/workspace.py",
    "jarvis/portable.py",
    "jarvis/device.py",
    "scripts/verify_release.py",
    "scripts/beta_smoke.py",
    "scripts/python_compat_audit.py",
}


def _members(path: Path) -> list[str]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [n.rstrip("/") for n in archive.namelist() if n and not n.endswith("/")]
    if path.name.endswith(".tar.gz") or path.suffix.lower() in {".tgz", ".tar"}:
        with tarfile.open(path, "r:*") as archive:
            return [m.name.rstrip("/") for m in archive.getmembers() if m.isfile()]
    raise ValueError(f"지원하지 않는 archive 형식입니다: {path}")


def _link_members(path: Path) -> list[str]:
    """Return archive members that are symlinks/hardlinks instead of regular files."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            result: list[str] = []
            for info in archive.infolist():
                mode = (info.external_attr >> 16) & 0xFFFF
                if info.create_system == 3 and stat.S_ISLNK(mode):
                    result.append(info.filename)
            return result
    if path.name.endswith(".tar.gz") or path.suffix.lower() in {".tgz", ".tar"}:
        with tarfile.open(path, "r:*") as archive:
            return [m.name for m in archive.getmembers() if m.issym() or m.islnk()]
    return []


def audit(path: Path) -> tuple[bool, list[str]]:
    issues: list[str] = []
    names = _members(path)
    for link_name in _link_members(path):
        issues.append(f"archive link 항목 포함: {link_name}")
    if not names:
        return False, ["archive가 비어 있습니다."]

    roots = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
    if len(roots) != 1:
        issues.append(f"top-level directory가 하나가 아닙니다: {sorted(roots)}")
        prefix = ""
    else:
        prefix = next(iter(roots))

    relative_names: set[str] = set()
    for raw in names:
        p = PurePosixPath(raw)
        # Archive audit may later be run on files we did not create ourselves.
        # Reject path shapes that could escape an extraction directory on POSIX/Windows.
        if p.is_absolute() or "\\" in raw or ".." in p.parts or (p.parts and re.fullmatch(r"[A-Za-z]:", p.parts[0])):
            issues.append(f"위험한 archive 경로 포함: {raw}")
            continue
        parts = p.parts[1:] if prefix and p.parts and p.parts[0] == prefix else p.parts
        if any(part in FORBIDDEN_PARTS for part in parts):
            issues.append(f"금지된 runtime/cache 경로 포함: {raw}")
        if p.suffix.lower() in FORBIDDEN_SUFFIXES:
            issues.append(f"금지된/sensitive suffix 파일 포함: {raw}")
        if _sensitive_name(p.name):
            issues.append(f"민감한 파일명 포함: {raw}")
        if parts:
            relative_names.add(PurePosixPath(*parts).as_posix())

    missing = sorted(REQUIRED_RELATIVE - relative_names)
    if missing:
        issues.append("필수 파일 누락: " + ", ".join(missing))
    return not issues, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Project JARVIS release ZIP/TAR")
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args(argv)
    all_ok = True
    for path in args.archives:
        try:
            ok, issues = audit(path)
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            ok, issues = False, [str(exc)]
        if ok:
            print(f"[PASS] {path}")
        else:
            all_ok = False
            print(f"[FAIL] {path}")
            for issue in issues:
                print(f"  - {issue}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
