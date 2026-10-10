"""Run release tests through the same bounded suite runner as /dev-auto."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import developer  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    # The shared suite runner includes Korean diagnostics; redirected Windows
    # consoles can otherwise fail while printing an unverified-test result.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run ALEX unit tests with bounded parallel discovery")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=developer.SUITE_WORKERS,
                        help="1 uses standard serial discovery; default reuses the /dev-auto worker count")
    args = parser.parse_args(argv)
    developer.SUITE_WORKERS = args.workers
    code, output = developer._default_suite_runner(args.workspace.resolve())
    print(output, end="" if output.endswith("\n") else "\n")
    kind, _failures, reason = developer._suite_report(code, output)
    if kind != "ok":
        if reason:
            print(f"Suite was not verified: {reason}", file=sys.stderr)
        return code if code else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
