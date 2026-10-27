"""
Startup/pre-flight status output: one green/yellow/red scheme
(utils/menu_status.py), only the status word colored, readable with colors
off (NO_COLOR / not a terminal).
"""

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from test_control.safety_sweep import run_startup_safety_sweep
from utils import menu_status

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def _force_color(enabled: bool):
    return mock.patch.object(menu_status, "color_enabled", lambda: enabled)


def _run_preflight(config_results, device_results):
    out = io.StringIO()
    with mock.patch.object(test_module, "test_configuration", lambda: config_results), \
            mock.patch.object(test_module, "test_device_validation", lambda: device_results), \
            redirect_stdout(out):
        results, ok = test_module.preflight_check()
    return out.getvalue(), ok


def _pass(module):
    return test_module._ok(module, "x", "ref", "fine")


class MenuStatusStyleTests(unittest.TestCase):
    def test_three_colors(self):
        with _force_color(True):
            self.assertEqual(menu_status.style("OK", menu_status.VERIFIED), f"{GREEN}OK{RESET}")
            self.assertEqual(menu_status.style("PARTIAL", menu_status.PARTIAL), f"{YELLOW}PARTIAL{RESET}")
            self.assertEqual(menu_status.style("FAIL", menu_status.FAILED), f"{RED}FAIL{RESET}")
            self.assertEqual(menu_status.style("x", menu_status.FUTURE), "x")

    def test_no_color_env_or_non_terminal_leaves_text_unchanged(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}), mock.patch("sys.stdout.isatty", return_value=True):
            self.assertFalse(menu_status.color_enabled())
            self.assertEqual(menu_status.style("OK", menu_status.VERIFIED), "OK")
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch("sys.stdout.isatty", return_value=False):
            os.environ.pop("NO_COLOR", None)
            self.assertFalse(menu_status.color_enabled())

    def test_result_styles(self):
        self.assertEqual(menu_status.result_style("PASS"), menu_status.VERIFIED)
        self.assertEqual(menu_status.result_style("WARNING"), menu_status.PARTIAL)
        self.assertEqual(menu_status.result_style("FAIL"), menu_status.FAILED)


class PreflightOutputTests(unittest.TestCase):
    def test_all_pass_prints_green_ok_per_area(self):
        with _force_color(True):
            out, ok = _run_preflight([_pass("Configuration")], [_pass("Device Validation")])
        self.assertTrue(ok)
        self.assertIn(f"  Configuration: {GREEN}OK{RESET}", out)
        self.assertIn(f"  Devices: {GREEN}OK{RESET}", out)

    def test_plain_text_when_colors_are_off(self):
        with _force_color(False):
            out, ok = _run_preflight([_pass("Configuration")], [_pass("Device Validation")])
        self.assertTrue(ok)
        self.assertEqual(out, "  Configuration: OK\n  Devices: OK\n")

    def test_warning_is_yellow_and_does_not_block(self):
        warn = test_module._warn("Configuration", "RELAY_COM_PORT", "ref", "Default port in use\nmore")
        with _force_color(True):
            out, ok = _run_preflight([_pass("Configuration"), warn], [_pass("Device Validation")])
        self.assertTrue(ok)
        self.assertIn(f"  Configuration: {YELLOW}WARNING{RESET}", out)
        self.assertIn(f"{YELLOW}[WARN]{RESET} RELAY_COM_PORT: Default port in use", out)
        self.assertIn(f"  Devices: {GREEN}OK{RESET}", out)

    def test_failure_is_red_and_blocks(self):
        fail = test_module._fail("Device Validation", "config/devices.py", "ref", "Duplicate IP address")
        with _force_color(True):
            out, ok = _run_preflight([_pass("Configuration")], [fail])
        self.assertFalse(ok)
        self.assertIn(f"  Configuration: {GREEN}OK{RESET}", out)
        self.assertIn(f"  Devices: {RED}FAIL{RESET}", out)
        self.assertIn(f"{RED}[FAIL]{RESET} config/devices.py: Duplicate IP address", out)

    def test_failure_text_is_readable_without_colors(self):
        fail = test_module._fail("Device Validation", "config/devices.py", "ref", "Duplicate IP address")
        with _force_color(False):
            out, _ = _run_preflight([_pass("Configuration")], [fail])
        self.assertIn("  Devices: FAIL\n", out)
        self.assertIn("    [FAIL] config/devices.py: Duplicate IP address", out)
        self.assertNotIn("\033", out)


class ResultTagTests(unittest.TestCase):
    def test_print_detail_colors_only_the_tag(self):
        result = test_module._fail("M", "Relay 4", "ref", "boom")
        out = io.StringIO()
        with _force_color(True), redirect_stdout(out):
            result.print_detail()
        self.assertIn(f"  {RED}[FAIL]{RESET} Relay 4", out.getvalue())
        out = io.StringIO()
        with _force_color(False), redirect_stdout(out):
            result.print_detail()
        self.assertTrue(out.getvalue().startswith("  [FAIL] Relay 4"))


class SafetySweepReportTests(unittest.TestCase):
    class _Relay:
        def __init__(self, fail_connect=False):
            self.fail_connect, self.opened = fail_connect, False

        def connect(self):
            if self.fail_connect:
                raise RuntimeError("not reachable")

        def open_all(self):
            self.opened = True

        def disconnect(self):
            pass

    def test_clean_sweep_returns_nothing_unavailable(self):
        relay = self._Relay()
        result = run_startup_safety_sweep(smu_assignments={}, relay_matrix_configs={"R1": {}},
                                          relay_factory_create=lambda cfg: relay)
        self.assertEqual(result, [])
        self.assertTrue(relay.opened)

    def test_partial_sweep_names_the_devices_that_were_not_swept(self):
        relays = {"R1": self._Relay(), "R2": self._Relay(fail_connect=True)}
        result = run_startup_safety_sweep(
            smu_assignments={}, relay_matrix_configs={"R1": "R1", "R2": "R2"},
            relay_factory_create=lambda cfg: relays[cfg])
        self.assertEqual(result, ["Relay R2"])


if __name__ == "__main__":
    unittest.main()
