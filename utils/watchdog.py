"""
Loop watchdog -- forces hardware safe when a sampling loop stops making
progress.

Every other safety mechanism in this codebase is cooperative: it runs on the
loop's own thread, so a blocking instrument call (a hung socket read, a stuck
NI driver call) disables ALL of them at once while the SMU output stays on and
the relay stays closed. This watchdog is the one mechanism that does not
depend on the loop: a daemon thread that fires `on_expire` if `pet()` has not
been called for `timeout_s`.

Armed lazily by the FIRST pet(), so code that never pets (every operation
that has not been wired to it) is unaffected. pet() raises
WatchdogTimeoutError if the watchdog already fired, so a loop that eventually
unblocks stops instead of resuming an operation whose hardware was already
forced safe from the watchdog thread.

on_expire runs on the watchdog thread, concurrently with the (hung) loop
thread -- it must only do hardware-safing, never touch loop state.
"""

from __future__ import annotations

import logging
import threading
import time

from utils.errors import WatchdogTimeoutError

_log = logging.getLogger("nipxi.watchdog")


class LoopWatchdog:
    def __init__(self, timeout_s: float, on_expire, *, name: str = "loop", clock=time.monotonic):
        self.timeout_s = float(timeout_s)
        self.name = name
        self._on_expire = on_expire
        self._clock = clock
        self._last = None
        self._thread = None
        self._stop = threading.Event()
        self._expired = threading.Event()
        self._lock = threading.Lock()

    @property
    def expired(self) -> bool:
        return self._expired.is_set()

    @property
    def armed(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def pet(self) -> None:
        if self._expired.is_set():
            raise WatchdogTimeoutError(
                f"Watchdog '{self.name}' expired (no progress for {self.timeout_s:.0f}s) -- "
                f"hardware was forced safe from the watchdog thread."
            )
        self._last = self._clock()
        if self._thread is None and self.timeout_s > 0:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(
                        target=self._run, name=f"watchdog-{self.name}", daemon=True,
                    )
                    self._thread.start()

    def stop(self) -> None:
        """Disarm. Safe to call repeatedly, and from the handler path."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _run(self) -> None:
        poll = max(0.05, min(1.0, self.timeout_s / 4.0))
        while not self._stop.wait(poll):
            stalled_s = self._clock() - self._last
            if stalled_s > self.timeout_s:
                self._expired.set()
                reason = f"watchdog '{self.name}': no loop progress for {stalled_s:.0f}s"
                _log.critical("%s -- forcing hardware safe.", reason)
                try:
                    self._on_expire(reason)
                except Exception as e:
                    _log.critical("Watchdog '%s' safing action raised: %s. Hardware may still "
                                  "be energized -- physically disconnect power.", self.name, e)
                return
