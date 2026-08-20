"""
Shared test helper: test.py:28 calls logging.disable(logging.CRITICAL) at
IMPORT time -- a deliberate, process-global choice for its own interactive-
CLI UX (suppressing noisy library logs so its own print()-based menu
output stays clean). Because `logging.disable()` is process-global, any
test that imports `test` (directly or transitively, e.g. via `main`) can
leave logging suppressed for every OTHER test module that happens to run
afterward in the same `python -m unittest discover` process, regardless of
import order between test files. Any test asserting on captured log
records must call `reenable_logging_for_this_test(self)` itself -- do not
rely on some other test file having already restored it.
"""

import logging
import unittest


def reenable_logging_for_this_test(testcase: unittest.TestCase) -> None:
    previous = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    testcase.addCleanup(logging.disable, previous)
