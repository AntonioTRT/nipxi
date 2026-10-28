"""
Storage free-space supervision.

A battery test must not run without a trace of what happened, so low disk
space is treated as a safety condition, not a housekeeping one:

  start gate     free < Settings.STORAGE_MIN_FREE_MB_START -> do not start
  runtime warn   free < Settings.STORAGE_WARN_FREE_MB      -> one warning
  runtime abort  free < Settings.STORAGE_ABORT_FREE_MB     -> StorageFailureError
                                                              (standard safety shutdown)

Free space is measured on the volume holding Settings.DATA_DIR (the monthly
telemetry/index databases). Pure and injectable (`usage_fn`, `clock`) so it
is testable without filling a disk.
"""

from __future__ import annotations

import os
import shutil
import time

from utils.errors import StorageFailureError

_MB = 1024 * 1024


def _existing_ancestor(path: str) -> str:
    path = os.path.abspath(path)
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def free_space_mb(path: str, usage_fn=shutil.disk_usage):
    """Free MB on the volume holding `path` (or its nearest existing parent);
    None if it cannot be determined -- callers must not treat unknown as low."""
    try:
        return usage_fn(_existing_ancestor(path)).free / _MB
    except OSError:
        return None


def check_start_free_space(settings, usage_fn=shutil.disk_usage):
    """(ok, free_mb). ok is False only when free space is KNOWN to be below
    the start threshold."""
    data_dir = getattr(settings, "DATA_DIR", None)
    if data_dir is None:
        return True, None
    free = free_space_mb(data_dir, usage_fn)
    minimum = getattr(settings, "STORAGE_MIN_FREE_MB_START", 0)
    return (free is None or free >= minimum), free


class StorageSpaceGuard:
    """Time-gated runtime check, called every loop iteration; does real work
    only once per Settings.STORAGE_CHECK_INTERVAL_S."""

    def __init__(self, settings, *, usage_fn=shutil.disk_usage, clock=time.monotonic):
        self._s = settings
        self._usage_fn = usage_fn
        self._clock = clock
        self._last_check = None
        self._warned = False

    def check(self, on_warn=None) -> None:
        interval = getattr(self._s, "STORAGE_CHECK_INTERVAL_S", 0)
        if not interval or interval <= 0:
            return
        now = self._clock()
        if self._last_check is not None and now - self._last_check < interval:
            return
        self._last_check = now
        data_dir = getattr(self._s, "DATA_DIR", None)
        free = free_space_mb(data_dir, self._usage_fn) if data_dir is not None else None
        if free is None:
            return
        abort_mb = getattr(self._s, "STORAGE_ABORT_FREE_MB", 0)
        warn_mb = getattr(self._s, "STORAGE_WARN_FREE_MB", 0)
        if free < abort_mb:
            raise StorageFailureError(
                f"Free storage {free:.0f} MB is below the abort threshold "
                f"({abort_mb} MB) -- stopping so no test runs without a record."
            )
        if free < warn_mb and not self._warned:
            self._warned = True
            if on_warn is not None:
                on_warn(f"Free storage {free:.0f} MB is below the warning threshold ({warn_mb} MB)")
