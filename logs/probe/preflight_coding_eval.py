"""Check saved coding tasks without importing ALEX, running tests, or using a model.

This verifies mutation anchors and Python syntax only. Passing does not prove
that the original tests pass or that the mutation causes a test failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_tasks(root: Path, tasks_file: Path, split: str = "all") -> dict:
    root = root.resolve(strict=True)
    tasks_file = tasks_file.resolve(strict=True)
    raw = tasks_file.read_bytes()
    tasks = json.loads(raw)
    if not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks):
        raise ValueError("tasks must be a list of objects")
    if split not in {"all", "train", "test"}:
        raise ValueError("invalid split")
    selected = [t for t in tasks if split == "all" or t.get("split") == split]
    if not selected:
        raise ValueError("no tasks selected")
    files: dict[Path, bytes] = {}

    def read_file(value: object) -> tuple[Path, bytes]:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ValueError("task paths must be nonempty relative POSIX paths")
        relative = Path(value)
        if relative.is_absolute() or ":" in value or ".." in relative.parts:
            raise ValueError("task path escapes the project")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("task path is not a project file")
        if path not in files:
            files[path] = path.read_bytes()
        return path, files[path]

    seen: set[str] = set()
    rows = []
    for task in selected:
        row = {"task": task.get("id"), "file": task.get("file"),
               "test": task.get("test"), "issues": []}
        try:
            ident = task.get("id")
            if not isinstance(ident, str) or not ident:
                raise ValueError("task id must be a nonempty string")
            if ident in seen:
                raise ValueError("duplicate task id")
            seen.add(ident)
            source_path, source_raw = read_file(task.get("file"))
            tests = task.get("test")
            if not isinstance(tests, str):
                raise ValueError("test must be a comma-separated path list")
            test_paths = [item.strip() for item in tests.split(",")]
            if len(set(test_paths)) != len(test_paths):
                raise ValueError("duplicate acceptance test path")
            test_files = [read_file(item) for item in test_paths]
            if source_path.suffix != ".py" or any(p.suffix != ".py" for p, _ in test_files):
                raise ValueError("expected Python source and test files")
            source = source_raw.decode("utf-8").replace("\r\n", "\n")
            compile(source, str(source_path), "exec", dont_inherit=True)
            for test_path, test_raw in test_files:
                compile(test_raw.decode("utf-8"), str(test_path), "exec", dont_inherit=True)
            edits = task.get("edits")
            if edits is None:
                edits = [[task.get("find"), task.get("replace")]]
            if not isinstance(edits, list) or not edits:
                raise ValueError("edits must be a nonempty list")
            mutated = source
            for index, edit in enumerate(edits, 1):
                if (not isinstance(edit, list) or len(edit) != 2
                        or not all(isinstance(v, str) for v in edit) or not edit[0]):
                    raise ValueError(f"edit {index}: expected nonempty find and string replacement")
                find, replacement = edit
                count = mutated.count(find)
                if count != 1:
                    raise ValueError(f"edit {index}: anchor matches {count} times (expected 1)")
                mutated = mutated.replace(find, replacement, 1)
            if mutated == source:
                raise ValueError("mutation does not change the source")
            compile(mutated, str(source_path), "exec", dont_inherit=True)
            row.update(source_sha256=sha256(source_raw),
                       test_sha256=sha256(test_files[0][1]) if len(test_files) == 1 else None,
                       test_hashes={p.relative_to(root).as_posix(): sha256(raw) for p, raw in test_files},
                       mutated_text_sha256=sha256(mutated.encode("utf-8")), edits=len(edits))
        except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
            row["issues"].append(f"{type(exc).__name__}: {exc}")
        row["static_checks_passed"] = not row["issues"]
        rows.append(row)

    changed = []
    for path, expected in [(tasks_file, raw), *files.items()]:
        try:
            if path.read_bytes() == expected:
                continue
        except OSError:
            pass
        changed.append(str(path))
    passed = sum(row["static_checks_passed"] for row in rows)
    return {"schema": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
            "root": str(root), "tasks_file": str(tasks_file), "tasks_sha256": sha256(raw),
            "split": split, "selected": len(rows), "passed": passed,
            "static_checks_passed": passed == len(rows) and not changed,
            "tests_executed": False, "model_called": False,
            "changed_during_check": changed,
            "checked_files": {path.relative_to(root).as_posix(): sha256(data)
                              for path, data in sorted(files.items())},
            "tasks": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--tasks-file", type=Path, required=True)
    parser.add_argument("--split", choices=("all", "train", "test"), default="all")
    parser.add_argument("--out", type=Path, help="new JSON report (existing files are refused)")
    args = parser.parse_args()
    try:
        report = inspect_tasks(args.root, args.tasks_file, args.split)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            with args.out.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
        else:
            print(rendered, end="")
        return 0 if report["static_checks_passed"] else 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"preflight error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
