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
import sys

from config.settings import Settings
from config import devices as dev_cfg
from data.logger import setup as setup_logging
from utils.errors import HardwareInitError, ValidationError, DeviceConfigError
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

    try:
        hw.connect_all()
    except HardwareInitError as e:
        log.error("Hardware initialization failed: %s", e)
        sys.exit(1)

    # --- 4. Run the test ---------------------------------------------------
    result_mgr = ResultManager(settings=Settings)
    executor   = TestExecutor(hw=hw, storage=result_mgr.storage, settings=Settings)

    try:
        with result_mgr:
            result = executor.run(channels=args.channels)

        result_mgr.generate_report(result.run_id)

        if result.success:
            log.info("Test complete. %s", result.summary())
        else:
            log.warning("Test finished with issues. %s", result.summary())
            sys.exit(2)

    except KeyboardInterrupt:
        log.warning("Test interrupted by user (Ctrl+C).")

    except Exception as e:
        log.error("Unexpected error: %s", e, exc_info=True)
        sys.exit(1)

    # --- 5. Shutdown ---------------------------------------------------------
    # Always attempted, no matter how the try block above exits (normal
    # completion, KeyboardInterrupt, any other exception) -- Python's
    # finally always runs. disconnect_all() itself never raises by design
    # (every step is individually caught and logged), but this is wrapped
    # defensively anyway so a shutdown failure is never silently lost or
    # allowed to replace/mask the exception already propagating -- see
    # docs/architecture.md "Emergency Shutdown Strategy".
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
