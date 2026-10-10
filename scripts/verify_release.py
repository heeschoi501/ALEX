"""Run reproducible release verification with only the Python standard library."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(label: str, cmd: list[str]) -> bool:
    print(f"\n=== {label} ===")
    print("$", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=ROOT)
    if completed.returncode == 0:
        print(f"[PASS] {label}")
        return True
    print(f"[FAIL] {label} (rc={completed.returncode})")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Project JARVIS release")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--test-workers", type=int, choices=range(1, 9), default=None,
                        help="Override unit test workers; 1 keeps serial discovery")
    args = parser.parse_args(argv)

    python = sys.executable
    checks = [
        ("compileall", [python, "-m", "compileall", "-q", "jarvis", "scripts", "tests"]),
        ("Python 3.10 syntax compatibility", [python, "scripts/python_compat_audit.py"]),
        ("dependency audit", [python, "scripts/dependency_audit.py"]),
        ("router golden corpus", [python, "scripts/router_eval.py"]),
        ("security smoke", [python, "scripts/security_smoke.py"]),
        ("alpha smoke", [python, "scripts/alpha_smoke.py"]),
        ("beta smoke", [python, "scripts/beta_smoke.py"]),
    ]
    if not args.skip_tests:
        test_command = [python, "-W", "error::ResourceWarning", "scripts/run_suite.py"]
        if args.test_workers is not None:
            test_command.extend(("--workers", str(args.test_workers)))
        checks.insert(1, ("unit tests + ResourceWarning guard", test_command))

    results = [run(label, cmd) for label, cmd in checks]
    passed = all(results)
    print("\nRELEASE VERIFICATION:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
