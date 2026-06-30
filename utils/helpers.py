"""Common utility helpers."""

import os
import time


def ensure_dirs(*paths: str):
    """Create directories if they do not exist."""
    for p in paths:
        os.makedirs(p, exist_ok=True)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def wait_with_log(seconds: float, message: str = "Waiting"):
    import logging
    log = logging.getLogger("nipxi.helpers")
    log.debug("%s %.1f s...", message, seconds)
    time.sleep(seconds)


def format_duration(seconds: float) -> str:
    """Return human-readable duration string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
