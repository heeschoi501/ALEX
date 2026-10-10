"""Self-checker for the Project JARVIS Python practice files.

It intentionally contains test cases, not solution implementations. Run it after
editing ``study/practice.py`` or ``study/practice_v013.py``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from study import practice, practice_v013

Check = tuple[str, Callable[[], bool]]


def basic_checks() -> list[Check]:
    return [
        (
            "count_routes",
            lambda: practice.count_routes(["안녕", "Python 함수 알려줘", "현재 GPU 알려줘"])
            == {"local_general": 1, "local_coder": 1, "tool/system_info": 1},
        ),
        ("memory_pass", lambda: practice.memory_pass(2) == "FAIL" and practice.memory_pass(3) == "PASS"),
        (
            "normalize_ratio",
            lambda: practice.normalize_ratio(5, 0, 10) == 0.5 and practice.normalize_ratio(1, 1, 1) == 0.0,
        ),
        (
            "parse_route_from_log",
            lambda: practice.parse_route_from_log("INFO route=local_coder reason=simple") == "local_coder"
            and practice.parse_route_from_log("INFO no route here") is None,
        ),
        (
            "is_valid_memory_key",
            lambda: practice.is_valid_memory_key("project_1")
            and not practice.is_valid_memory_key("1project")
            and not practice.is_valid_memory_key("a"),
        ),
    ]


def v013_checks() -> list[Check]:
    def path_boundary() -> bool:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            inside = root / "src" / "a.py"
            outside = root.parent / "outside.py"
            return practice_v013.is_inside_root(root, inside) and not practice_v013.is_inside_root(root, outside)

    def human_bytes_raises() -> bool:
        try:
            practice_v013.human_bytes(-1)
        except ValueError:
            return True
        return False

    return [
        (
            "choose_profile_model",
            lambda: practice_v013.choose_profile_model(" desktop ", "8b", "14b") == "14b"
            and practice_v013.choose_profile_model("laptop", "8b", "14b") == "8b",
        ),
        ("is_inside_root", path_boundary),
        (
            "redact_simple_secret",
            lambda: "ABC123" not in practice_v013.redact_simple_secret("token=ABC123 hello")
            and "REDACTED" in practice_v013.redact_simple_secret("password=ABC123"),
        ),
        (
            "top_counts",
            lambda: practice_v013.top_counts(["b", "a", "b", "a", "c", "b"], 2)
            == [("b", 3), ("a", 2)],
        ),
        (
            "bounded_tail",
            lambda: practice_v013.bounded_tail(["aa", "bbb", "cccc", "dd"], 2, 6) == ["cccc", "dd"]
            and practice_v013.bounded_tail(["123456789"], 3, 4) == ["6789"],
        ),
        (
            "should_use_cache",
            lambda: practice_v013.should_use_cache(10, 20, 10, 20)
            and not practice_v013.should_use_cache(10, 20, 11, 20),
        ),
        (
            "human_bytes",
            lambda: practice_v013.human_bytes(512) == "512 B"
            and practice_v013.human_bytes(1536) == "1.5 KiB"
            and human_bytes_raises(),
        ),
        (
            "unique_preserve_order",
            lambda: practice_v013.unique_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"],
        ),
    ]


def run(checks: list[Check]) -> int:
    passed = 0
    for name, check in checks:
        try:
            ok = bool(check())
        except NotImplementedError:
            ok = False
            detail = "아직 구현하지 않음"
        except Exception as exc:  # Study checker should show the learner the failing exception.
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        else:
            detail = "PASS" if ok else "결과가 기대값과 다름"
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        passed += int(ok)
    print(f"\nStudy score: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Project JARVIS Python practice answers")
    parser.add_argument("--set", choices=("basic", "v013", "all"), default="all")
    args = parser.parse_args(argv)
    checks: list[Check] = []
    if args.set in {"basic", "all"}:
        checks.extend(basic_checks())
    if args.set in {"v013", "all"}:
        checks.extend(v013_checks())
    return run(checks)


if __name__ == "__main__":
    raise SystemExit(main())
