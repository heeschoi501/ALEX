"""Summarize logs/misroute.jsonl without changing router rules.

Usage:
    python scripts/misroute_stats.py
    python scripts/misroute_stats.py path/to/misroute.jsonl
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            print(f"warning: malformed JSONL line skipped: {line_number}", file=sys.stderr)
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def summarize(records: list[dict]) -> str:
    if not records:
        return "No misroute records yet."
    predicted = Counter(str(r.get("destination", "unknown")) for r in records)
    corrections = Counter(str(r.get("correction", "unspecified")) for r in records)
    matched = Counter(str(r.get("matched", "(none)")) or "(none)" for r in records)
    pairs = Counter(
        (str(r.get("destination", "unknown")), str(r.get("correction", "unspecified")))
        for r in records
    )

    lines = [f"Total misroutes: {len(records)}", "", "Predicted destinations:"]
    lines.extend(f"  {name}: {count}" for name, count in predicted.most_common())
    lines.append("\nCorrections:")
    lines.extend(f"  {name}: {count}" for name, count in corrections.most_common())
    lines.append("\nMatched keywords:")
    lines.extend(f"  {name}: {count}" for name, count in matched.most_common(10))
    lines.append("\nTop predicted -> correction pairs:")
    lines.extend(f"  {a} -> {b}: {count}" for (a, b), count in pairs.most_common(10))
    return "\n".join(lines)


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/misroute.jsonl")
    print(summarize(load_records(path)))


if __name__ == "__main__":
    main()
