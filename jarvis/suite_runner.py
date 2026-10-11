"""Trusted unittest CLI with complete, stable failure identities on Python 3.10+."""
from __future__ import annotations

import unittest
import os
import sys


class StableTextResult(unittest.TextTestResult):
    def getDescription(self, test):
        parent = getattr(test, "test_case", test)
        identity = parent.id()
        name = getattr(parent, "_testMethodName", "runTest")
        # _SubTest.id() includes its parent's full ID followed by every parameter.
        suffix = test.id()[len(identity):] if parent is not test else ""
        return f"{name} ({identity}){suffix}"

    def printErrors(self):
        if self.dots or self.showAll:
            self.stream.writeln()
            self.stream.flush()
        self.printErrorList("ERROR", self.errors)
        self.printErrorList("FAIL", self.failures)
        # Python 3.10 omits this list; the summary alone cannot identify regressions.
        if self.unexpectedSuccesses:
            self.stream.writeln(self.separator1)
            for test in self.unexpectedSuccesses:
                self.stream.writeln(f"UNEXPECTED SUCCESS: {self.getDescription(test)}")


class StableSummaryStream:
    """Keep the old zero-test summary; the parent rejects zero executed tests."""
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        return self.stream.write("OK" if text == "NO TESTS RAN" else text)

    def writeln(self, text=None):
        if text is not None:
            self.write(text)
        self.write("\n")

    def __getattr__(self, name):
        return getattr(self.stream, name)


class StableTextRunner(unittest.TextTestRunner):
    resultclass = StableTextResult

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream = StableSummaryStream(self.stream)


if __name__ == "__main__":
    # Match `python -m unittest`: sibling packages belong to the selected workspace,
    # even when this trusted CLI lives in a different checkout.
    sys.path.insert(0, os.getcwd())
    os.environ["PYTHON_COLORS"] = "0"
    program = unittest.main(module=None, testRunner=StableTextRunner, exit=False)
    # Match 3.10/3.11's raw exit status. _run_unittest and the suite merger keep
    # their strict executed-test-count guard (125 for an empty/all-skipped suite).
    sys.exit(0 if program.result.wasSuccessful() else 1)
