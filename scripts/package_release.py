"""Create verified clean ZIP + tar.gz handoff archives and SHA-256 checksums."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VERSION = "0.3.0"
EXCLUDED_PARTS = {
    ".git", ".idea", ".pytest_cache", ".mypy_cache", "__pycache__", ".venv", ".venv-alex-study", "venv",
    "data", "logs", "dist", "runtime",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx"}
SENSITIVE_BASENAMES = {
    ".env", "credentials", "credentials.json", "secrets.json", "secrets.toml",
    "id_rsa", "id_ed25519", "api_key.txt", "apikey.txt", "jarvis.local.json", "ollama-release.json",
}


def _sensitive_name(name: str) -> bool:
    lower = name.lower()
    return lower in SENSITIVE_BASENAMES or lower.startswith(".env.")


def iter_release_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or _sensitive_name(path.name):
            continue
        yield path, relative


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_release_verification() -> None:
    result = subprocess.run([sys.executable, "scripts/verify_release.py"], cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit("Release verification failed; archive was not created.")


def audit_archives(*paths: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/archive_audit.py", *map(str, paths)], cwd=ROOT
    )
    if result.returncode != 0:
        raise SystemExit("Archive audit failed; inspect dist before handoff.")



def verify_extracted_zip(zip_path: Path) -> None:
    """Prove the clean archive is self-contained and tests do not rely on runtime files."""
    with tempfile.TemporaryDirectory() as td:
        extract_root = Path(td)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)
        candidates = [p for p in extract_root.iterdir() if p.is_dir()]
        if len(candidates) != 1:
            raise SystemExit("Extracted release must have exactly one top-level directory.")
        project = candidates[0]
        result = subprocess.run([sys.executable, "scripts/verify_release.py"], cwd=project)
        if result.returncode != 0:
            raise SystemExit("Extracted archive verification failed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package Project JARVIS release")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--skip-verify", action="store_true",
        help="skip full release verification (not recommended for handoff)",
    )
    parser.add_argument(
        "--skip-extracted-verify", action="store_true",
        help="skip verifying the clean ZIP after extraction (not recommended)",
    )
    args = parser.parse_args(argv)

    if not args.skip_verify:
        run_release_verification()

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    base = f"project_jarvis_v{args.version.replace('.', '_')}"
    zip_path = dist / f"{base}.zip"
    tar_path = dist / f"{base}.tar.gz"
    prefix = base
    files = list(iter_release_files())

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in files:
            archive.write(path, Path(prefix) / relative)

    with tarfile.open(tar_path, "w:gz") as archive:
        for path, relative in files:
            archive.add(path, arcname=Path(prefix) / relative, recursive=False)

    audit_archives(zip_path, tar_path)
    if not args.skip_extracted_verify:
        verify_extracted_zip(zip_path)
    checksums = dist / "SHA256SUMS.txt"
    checksums.write_text(
        f"{sha256(zip_path)}  {zip_path.name}\n{sha256(tar_path)}  {tar_path.name}\n",
        encoding="utf-8",
    )
    print(f"files: {len(files)}")
    print(f"zip: {zip_path}")
    print(f"tar: {tar_path}")
    print(f"sha256: {checksums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
