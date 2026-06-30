"""
NIPXI - Battery Test System
Entry point. Run this file to start the application.

Usage:
    python main.py
    python main.py --config config/settings.py
    python main.py --channels 1 2 3
"""

import argparse
import logging
import sys

from config.settings import Settings
from utils.errors import HardwareInitError


def parse_args():
    parser = argparse.ArgumentParser(description="NIPXI Battery Test System")
    parser.add_argument(
        "--config", default="config/settings.py", help="Path to settings file"
    )
    parser.add_argument(
        "--channels", nargs="+", type=int, help="Channel indices to test (e.g. 1 2 3)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run without connecting to hardware"
    )
    return parser.parse_args()


def setup_logging(settings: Settings):
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(settings.LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main():
    args = parse_args()
    settings = Settings()

    setup_logging(settings)
    log = logging.getLogger("nipxi.main")
    log.info("NIPXI Battery Test System starting...")

    # TODO: Initialize hardware interfaces
    # TODO: Run test sequence
    # TODO: Save and report results

    log.info("Startup complete. Application logic not yet implemented.")
    print("NIPXI ready. Implement test_control/ modules to run a test.")


if __name__ == "__main__":
    main()
