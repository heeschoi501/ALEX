"""Content evidence for reusing an actual unittest run. Missing/oversized input fails closed."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

SKIP = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.venv', 'venv',
        '.venv-alex-study', '.alex-tool-output', '.codex-remote-attachments', 'logs', 'data',
        'runtime', 'models', 'tools', 'dist', '.opencode', '.claude', '.codex'}


def input_digest(root: Path) -> str:
    """Hash project code, tests and fixtures (not model/private/runtime stores), including names/deletions.

    No mtime/size cache: an external same-size edit must invalidate the result. Conservative caps
    make unusually large projects rerun checks instead of blocking on a huge fingerprint.
    """
    digest, total, count = hashlib.sha256(), 0, 0
    try:
        for directory, dirs, files in os.walk(root, followlinks=False):
            if any((Path(directory) / d).is_symlink() for d in dirs if d not in SKIP):
                return ''
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(directory) / d).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if name.endswith('.pyc') or name.startswith('.env') or name == 'jarvis.local.json':
                    continue
                if path.is_symlink():
                    return ''  # linked inputs can change outside this snapshot
                size = path.stat().st_size
                total, count = total + size, count + 1
                if total > 64 * 1024 * 1024 or count > 16000:
                    return ''
                data = path.read_bytes()
                rel = path.relative_to(root).as_posix().encode('utf-8')
                digest.update(len(rel).to_bytes(4, 'big') + rel)
                digest.update(len(data).to_bytes(8, 'big') + data)
        return digest.hexdigest()
    except OSError:
        return ''
