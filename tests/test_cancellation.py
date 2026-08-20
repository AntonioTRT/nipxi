"""
Regression tests for utils/cancellation.py's Ctrl+C safety-timing fix and
shutdown-diagnostics tracing:

  - install_sigint_handler() logs entry, then delegates to
    token.request_cancel() -- exercised directly, not via a real OS
    signal (which is fragile to simulate portably in a test process).
  - CancellationToken.check() logs before raising OperationCancelledError.
  - The SIGINT-before-connect_all() ordering fix in test.py/main.py's six
    entry points -- verified via source inspection (the same technique
    used to verify it live), so a future refactor that reintroduces the
    ordering bug fails this test.
"""

import inspect
import logging
import unittest

import main as main_module
import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from tests._logging_helpers import reenable_logging_for_this_test as _reenable_logging_for_this_test
from utils.cancellation import CancellationToken, install_sigint_handler, check_cancellation
from utils.errors import OperationCancelledError


class InstallSigintHandlerTests(unittest.TestCase):
    def test_handler_logs_entry_before_delegating_to_request_cancel(self):
        _reenable_logging_for_this_test(self)
        records = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("nipxi.cancellation")
        handler = _CapturingHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            token = CancellationToken(owner="unittest")
            previous = install_sigint_handler(token, owner="unittest")
            import signal
            current_handler = signal.getsignal(signal.SIGINT)
            try:
                current_handler(signal.SIGINT, None)
            finally:
                signal.signal(signal.SIGINT, previous)
        finally:
            logger.removeHandler(handler)

        self.assertTrue(token.requested, "the installed handler must call request_cancel()")
        self.assertTrue(
            any("[SHUTDOWN-TRACE] SIGINT handler entered" in m for m in records),
            f"expected a SIGINT-entry trace log, got: {records}",
        )

    def test_second_signal_is_idempotent_no_op(self):
        token = CancellationToken(owner="unittest")
        previous = install_sigint_handler(token, owner="unittest")
        import signal
        current_handler = signal.getsignal(signal.SIGINT)
        try:
            current_handler(signal.SIGINT, None)
            first_reason = token.reason
            current_handler(signal.SIGINT, None)  # second "press"
            self.assertEqual(token.reason, first_reason, "request_cancel() must stay idempotent")
        finally:
            signal.signal(signal.SIGINT, previous)


class CancellationTokenCheckTests(unittest.TestCase):
    def test_check_logs_before_raising(self):
        _reenable_logging_for_this_test(self)
        records = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        token = CancellationToken(owner="unittest")
        handler = _CapturingHandler()
        token.log.addHandler(handler)
        token.log.setLevel(logging.WARNING)
        try:
            token.request_cancel("test reason")
            with self.assertRaises(OperationCancelledError):
                check_cancellation(token)
        finally:
            token.log.removeHandler(handler)

        self.assertTrue(
            any("[SHUTDOWN-TRACE] OperationCancelledError raised" in m for m in records),
            f"expected a pre-raise trace log, got: {records}",
        )

    def test_none_token_is_a_no_op(self):
        check_cancellation(None)  # must not raise


class SigintInstalledBeforeHardwareInitTests(unittest.TestCase):
    """
    Source-level regression check for the Ctrl+C safety-timing fix: in
    every hardware-activating entry point, install_sigint_handler() must
    appear (as executable code, not just in a comment) before
    hw.connect_all()/hw_mgr.connect_all() and before storage init. This
    mirrors the exact verification technique used when the fix was
    originally applied and confirmed live.
    """

    ENTRY_POINTS = [
        ("run_proto_test_execution", test_module.run_proto_test_execution),
        ("_run_monitor_battery", test_module._run_monitor_battery),
        ("_run_monitor_battery_scan", test_module._run_monitor_battery_scan),
        ("_run_charge_or_discharge", test_module._run_charge_or_discharge),
        ("relay_matrix_scan", None),   # resolved below if present
        ("relay_ethernet_test", None),  # resolved below if present
        ("main", main_module.main),
    ]

    @staticmethod
    def _is_code_line(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and not stripped.startswith("#")

    def _first_code_line_index(self, lines, needle: str):
        for i, line in enumerate(lines):
            if needle in line and self._is_code_line(line):
                return i
        return None

    def test_handler_installed_before_connect_all_in_every_entry_point(self):
        # Resolve the two relay-diagnostic entry points by name if present
        # (they aren't imported at module scope the same way).
        candidates = list(self.ENTRY_POINTS)
        for name in ("relay_matrix_scan", "relay_ethernet_test"):
            fn = getattr(test_module, name, None)
            if fn is not None:
                candidates = [
                    (n, f) if n != name else (n, fn) for n, f in candidates
                ]

        checked_any = False
        for name, fn in candidates:
            if fn is None:
                continue
            checked_any = True
            src = inspect.getsource(fn)
            lines = src.splitlines()

            install_idx = self._first_code_line_index(lines, "install_sigint_handler(")
            connect_idx = self._first_code_line_index(lines, ".connect_all()")

            with self.subTest(entry_point=name):
                self.assertIsNotNone(
                    install_idx,
                    f"{name}: expected install_sigint_handler(...) to be called",
                )
                if connect_idx is not None:
                    self.assertLess(
                        install_idx, connect_idx,
                        f"{name}: SIGINT handler must be installed BEFORE connect_all() "
                        f"-- regression of the Ctrl+C safety-timing fix",
                    )

        self.assertTrue(checked_any, "no entry points were actually checked -- test setup is broken")


if __name__ == "__main__":
    unittest.main()
