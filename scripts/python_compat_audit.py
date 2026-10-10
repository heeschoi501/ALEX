"""Parse Project JARVIS Python sources using the Python 3.10 grammar.

This is not a substitute for running on a real Python 3.10 interpreter. It does,
however, prevent accidental use of newer syntax while the project declares
``requires-python >=3.10`` and the current CI/container happens to use Python 3.13.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = ("jarvis", "scripts", "tests", "study")
TARGET = (3, 10)


def main() -> int:
    failures: list[str] = []
    checked = 0
    for root_name in SOURCE_ROOTS:
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            checked += 1
            try:
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(path.relative_to(ROOT)), feature_version=TARGET)
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
                failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        print("Python 3.10 syntax compatibility audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Python 3.10 syntax compatibility audit PASS ({checked} files parsed)")
    if sys.version_info[:2] == TARGET:
        print("Running interpreter is also Python 3.10.")
    else:
        print(
            f"Note: parser host is Python {sys.version_info.major}.{sys.version_info.minor}; "
            "real Python 3.10 runtime validation is still required on a target machine."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
