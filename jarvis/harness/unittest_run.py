"""Standalone adapter for `python -m unittest`: preserve its CLI/output and record executed files.

Only the parent harness reads the JSON; stdout claims are never used as per-file evidence.
This script deliberately imports no ALEX modules so target projects need not install ALEX.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys
import unittest


def main() -> None:
    evidence = Path(sys.argv[1])
    root = Path.cwd().resolve()
    records: dict[str, dict] = {}

    class Result(unittest.TextTestResult):
        def _record(self, test, outcome):
            try:
                rel = Path(inspect.getfile(type(test))).resolve().relative_to(root).as_posix()
            except (TypeError, OSError, ValueError):
                return
            row = records.setdefault(rel, {'ran': 0, 'success': 0, 'skipped': 0, 'ids': []})
            row[outcome] += 1
            if outcome == 'ran':
                row['ids'].append(test.id())

        def startTest(self, test):
            self._record(test, 'ran')
            super().startTest(test)

        def addSuccess(self, test):
            self._record(test, 'success')
            super().addSuccess(test)

        def addSkip(self, test, reason):
            self._record(test, 'skipped')
            super().addSkip(test, reason)

    class Runner(unittest.TextTestRunner):
        resultclass = Result

    # Python's -m invocation puts cwd first. Running this adapter by path must do the same.
    sys.path[0] = str(root)
    program = unittest.main(module=None, argv=['python -m unittest', *sys.argv[2:]],
                            testRunner=Runner, exit=False)
    evidence.write_text(json.dumps({'files': records, 'ran': program.result.testsRun,
                                     'successful': program.result.wasSuccessful()}), encoding='utf-8')
    raise SystemExit(0 if program.result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
