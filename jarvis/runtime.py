"""Install the official Windows portable runtime locally, with SHA-256 verification.

No administrator rights, registry edits, PATH changes or startup services.
Models are prepared separately by the launcher/setup_models.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def install(report=print) -> Path:
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("This installer is for Windows x64. See https://ollama.com/download")
    target = ROOT / "runtime" / "ollama"
    executable = target / "ollama.exe"
    if executable.is_file():
        report(f"Already installed: {executable}", flush=True)
        return executable
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        "https://api.github.com/repos/ollama/ollama/releases/latest",
        headers={"User-Agent": "Project-Jarvis-Local"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset = next(a for a in release["assets"] if a["name"] == "ollama-windows-amd64.zip")
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:") or len(expected) != 71:
        raise RuntimeError("Official release has no SHA-256 digest; refusing unchecked install.")
    archive = target.parent / "ollama-windows-amd64.zip"
    report(f"Downloading Ollama {release['tag_name']}: {asset['size']/1e9:.2f} GB", flush=True)
    digest = hashlib.sha256()
    total, milestone = 0, 0
    with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(4 * 1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            total += len(chunk)
            if total // 100_000_000 > milestone:
                milestone = total // 100_000_000
                report(f"Downloaded {total/1e9:.2f} / {asset['size']/1e9:.2f} GB", flush=True)
    if digest.hexdigest() != expected[7:]:
        raise RuntimeError("SHA-256 mismatch; archive was not extracted.")
    report("SHA-256 verified. Extracting...", flush=True)
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if not (target / entry.filename).resolve().is_relative_to(target.resolve()):
                raise RuntimeError("Unsafe archive entry")
        bundle.extractall(target)
    if not executable.is_file():
        raise RuntimeError("Archive did not contain ollama.exe")
    (target.parent / "ollama-install.json").write_text(json.dumps({
        "version": release["tag_name"], "url": asset["browser_download_url"], "sha256": digest.hexdigest(),
    }, indent=2), encoding="utf-8")
    report(f"Installed: {executable}", flush=True)
    return executable
