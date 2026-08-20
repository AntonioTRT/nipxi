"""
NIPXI - Battery Test System
============================
Entry point. Thin orchestration layer only.

Responsibilities (this file):
  1. Parse command-line arguments
  2. Validate configuration
  3. Initialize logging
  4. Create managers (HardwareManager, ResultManager, TestExecutor)
  5. Run the test
  6. Handle top-level exceptions

All business logic lives in the managers:
  - HardwareManager  (test_control/hardware_manager.py)  -- device lifecycle
  - TestExecutor     (test_control/test_executor.py)     -- test sequences
  - ResultManager    (test_control/result_manager.py)    -- storage + reports

Usage:
    python main.py
    python main.py --channels 1 2 3     # test only channels 1, 2, 3
    python main.py --dry-run            # config validation only, no hardware
"""

import argparse
import logging
import signal
import sys

from config.settings import Settings
from config import devices as dev_cfg
from config.system_mode import get_mode_policy
from data.logger import setup as setup_logging
from utils.cancellation import CancellationToken
from utils.errors import HardwareInitError, ValidationError, DeviceConfigError
from utils.stop_reason import StopReason
from utils.validators import validate_settings
from utils.device_validator import validate_devices_or_raise
from test_control.hardware_manager import HardwareManager
from test_control.test_executor import TestExecutor
from test_control.result_manager import ResultManager


def parse_args():
    parser = argparse.ArgumentParser(description="NIPXI Battery Test System")
    parser.add_argument(
        "--channels", nargs="+", type=int,
        help="Channel indices to test, e.g. --channels 1 2 3"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate configuration only -- do not connect to hardware"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # --- 1. Logging (before anything else so errors are captured) ----------
    setup_logging(Settings)
    log = logging.getLogger("nipxi.main")
    log.info("NIPXI %s starting.", Settings.VERSION)

    mode_policy = get_mode_policy(Settings)
    log.info("System mode: %s -- %s", mode_policy.mode.value, mode_policy.description)

    # --- 2. Configuration validation -----------------------------------------
    # Settings first (voltages/currents/timeouts), then every configured
    # device in config/devices.py (existence, required fields, duplicate
    # addresses/ports/names, relay count consistency, factory type). Both
    # run before any hardware communication is attempted -- a bad
    # configuration must fail here, not surface as a confusing connect()
    # error deep inside HardwareManager.
    try:
        validate_settings(Settings)
    except ValidationError as e:
        log.error("Configuration error: %s", e)
        sys.exit(1)

    try:
        validate_devices_or_raise(dev_cfg)
    except DeviceConfigError as e:
        log.error("%s", e)
        sys.exit(1)

    if args.dry_run:
        log.info("Dry run: configuration is valid. Exiting without connecting hardware.")
        return

    # --- 3. Hardware -------------------------------------------------------
    # Production relay is the Numato Relay Matrix (Ethernet) -- RELAY_CONFIG
    # (serial) is kept only for bench diagnostics via test.py.
    hw = HardwareManager(Settings, relay_cfg=dev_cfg.NUMATO_RELAY_MATRIX_CONFIG)

    # Safe Cancellation (see docs/architecture.md "Safe Cancellation
    # Architecture"): Ctrl+C no longer raises KeyboardInterrupt while this
    # handler is installed -- it instead requests a cooperative, checkpoint-
    # based cancellation via `token`. Every long-running loop underneath
    # executor.run() (BatteryTestSequence, ChargeCycle, DischargeCycle)
    # polls this same token and unwinds through its existing PMU-off/
    # relay-open safety logic (see safety_monitor.py::safe_cancel_shutdown())
    # rather than an uncontrolled interrupt landing on an arbitrary line.
    #
    # Installed BEFORE hw.connect_all() (moved here from just before
    # executor.run() -- see docs/architecture.md's Ctrl+C review): a raw
    # KeyboardInterrupt during hardware connect previously bypassed
    # hw.disconnect_all() entirely, relying solely on the atexit-registered
    # backstop (HardwareManager._atexit_smu_shutdown/_atexit_relay_shutdown)
    # -- which only fires if the process actually exits. Restored to
    # Python's default in the finally below so Ctrl+C at any later input()
    # prompt (there are none after this point today, but this keeps the
    # window of altered behavior no wider than necessary) behaves normally
    # again.
    token = CancellationToken(owner="main")
    previous_sigint_handler = signal.signal(
        signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
    )

    # --- 4/5. Run the test + shutdown --------------------------------------
    # Shutdown (hw.disconnect_all()) is always attempted, no matter how the
    # block below exits (normal completion, a cancellation, KeyboardInterrupt,
    # any other exception, or a HardwareInitError from connect_all() itself) --
    # Python's finally always runs. disconnect_all() itself never raises by
    # design (every step is individually caught and logged), but this is
    # wrapped defensively anyway so a shutdown failure is never silently
    # lost or allowed to replace/mask the exception already propagating --
    # see docs/architecture.md "Emergency Shutdown Strategy".
    try:
        try:
            try:
                hw.connect_all()
            except HardwareInitError as e:
                log.error("Hardware initialization failed: %s", e)
                sys.exit(1)

            # In DEVELOPMENT/VALIDATION, connect_all() does not raise for a
            # merely missing device (see config/system_mode.py) -- surface
            # what's actually available before running anything, so a
            # laptop run without the PXI chassis attached is obvious from
            # the log, not a silent surprise.
            missing = [name for name, status in hw.hardware_status.items() if not status["connected"]]
            if missing:
                log.warning("Proceeding with missing hardware (%s mode): %s",
                            mode_policy.mode.value, missing)

            result_mgr = ResultManager(settings=Settings)
            executor   = TestExecutor(hw=hw, storage=result_mgr.storage, settings=Settings)

            with result_mgr:
                result = executor.run(channels=args.channels, token=token)

            result_mgr.generate_report(result.run_id)

            if result.success:
                log.info("Test complete. %s", result.summary())
            elif result.stop_reason == StopReason.CANCELLED:
                # Operator action, not a failure -- distinct exit code from
                # both success (0) and a genuine failure (1/2).
                log.warning("Test cancelled by operator. %s", result.summary())
                sys.exit(3)
            else:
                log.warning("Test finished with issues. %s", result.summary())
                sys.exit(2)

        except KeyboardInterrupt:
            # Defensive fallback only -- should not normally fire while the
            # SIGINT handler above is installed. Kept in case a
            # KeyboardInterrupt is still raised from somewhere the handler
            # doesn't cover.
            log.warning("Test interrupted by user (Ctrl+C).")

        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            sys.exit(1)

        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    finally:
        try:
            hw.disconnect_all()
        except Exception as shutdown_err:
            log.critical(
                "Hardware shutdown during exit failed: %s. Hardware may still "
                "be energized -- physically disconnect power if this cannot "
                "be resolved immediately.", shutdown_err, exc_info=True,
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
