"""Fail a model comparison early if local settings override its isolation flags."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import load_settings


def main() -> int:
    settings = load_settings()
    actual = (settings.web_tool_enabled, settings.auto_lessons_enabled, settings.lesson_context_limit)
    expected = (False, False, 0)
    if actual != expected:
        print(f"benchmark isolation FAILED: web_tool, auto_lessons, lesson_limit={actual}; expected {expected}")
        return 1
    print("benchmark isolation OK: web_tool=False auto_lessons=False lesson_limit=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
