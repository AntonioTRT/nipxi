"""
Regression test for main.py's `--show-topology` read-only reporting flag
(see docs/architecture.md "Future Architecture: main.py Integration
Seam"). Proves the three hard safety requirements from the approved
scope:

  - it never calls HardwareManager(...) / connect_all() (no hardware)
  - it never calls validate_settings()/validate_devices_or_raise() (no
    configuration validation side effects, no test execution)
  - it prints the orchestration/reporting.py report and returns cleanly

Patches main.py's own imported names directly (main_module.HardwareManager,
main_module.validate_settings, main_module.validate_devices_or_raise) so
this test fails loudly if a future change moves the --show-topology
branch below any of these calls.
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as main_module
import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from tests._logging_helpers import reenable_logging_for_this_test


class _MustNotBeCalledHardwareManager:
    def __init__(self, *args, **kwargs):
        raise AssertionError("HardwareManager must not be constructed for --show-topology")


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("configuration validation must not run for --show-topology")


class ShowTopologyFlagTests(unittest.TestCase):
    def setUp(self):
        reenable_logging_for_this_test(self)
        self._argv_patch = patch.object(sys, "argv", ["main.py", "--show-topology"])
        self._argv_patch.start()
        self.addCleanup(self._argv_patch.stop)

    def test_does_not_touch_hardware_or_validation_and_prints_report(self):
        buf = io.StringIO()
        with patch.object(main_module, "HardwareManager", _MustNotBeCalledHardwareManager), \
             patch.object(main_module, "validate_settings", _must_not_be_called), \
             patch.object(main_module, "validate_devices_or_raise", _must_not_be_called), \
             redirect_stdout(buf):
            main_module.main()  # must not raise

        output = buf.getvalue()
        for header in ("Topology Summary", "Worker Summary", "Dependency Summary",
                       "Conflict Summary", "Execution Plan Summary"):
            self.assertIn(header, output)

    def test_only_consumes_orchestration_reporting(self):
        import inspect
        src = inspect.getsource(main_module.main)
        lines = src.splitlines()
        show_topology_idx = next(i for i, l in enumerate(lines) if "args.show_topology" in l)
        return_idx = next(
            i for i in range(show_topology_idx, len(lines)) if lines[i].strip() == "return"
        )
        block = "\n".join(lines[show_topology_idx:return_idx + 1])
        self.assertIn("orchestration.reporting", block)
        self.assertNotIn("connect_all", block)
        self.assertNotIn("HardwareManager(", block)


if __name__ == "__main__":
    unittest.main()
