"""
Process-termination handling -- SIGTERM, Ctrl+Break, console close, logoff
and system shutdown.

Before this module only SIGINT (Ctrl+C) had a handler (utils/cancellation.py).
Every other way a console process gets told to stop either killed it outright
or ran no atexit hooks, leaving relays closed and the SMU output on.

Strategy (cooperative first, forced second):

  1. cancel every live CancellationToken -- the running operation unwinds
     through its normal run_guarded()/safe_cancel_shutdown() path, which is the
     only path that sequences "SMU off, THEN relay open" with full
     verification and traceability;
  2. wait up to a grace period for every registered HardwareManager to report
     is_safe() (relay and SMU disconnected);
  3. if the grace expires, force each still-connected HardwareManager through
     its emergency_shutdown() from this thread. This can race the operation
     thread on the same instrument, which is acceptable only because the
     alternative is the process being killed with the hardware energized.

Windows console events (close / logoff / shutdown / Ctrl+Break) are delivered
on a separate OS thread that does not need the main thread to be running --
the main thread may be blocked in input() or a socket read, where Python-level
signal handlers cannot run. SIGTERM (and SIGHUP on POSIX) use signal.signal
and hand the work to a daemon thread so the main thread is never blocked
waiting on itself.

Console-close grace is short (Settings.CONSOLE_CLOSE_GRACE_S) because Windows
terminates the process shortly after a close event regardless.

This is a safety NET, not the primary path: it never replaces
run_guarded()/disconnect_all(), and it cannot help against a hard kill,
BSOD or power loss.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import weakref

from config.settings import Settings
from utils.cancellation import cancel_all_tokens

_log = logging.getLogger("nipxi.termination")

_registered = []          # weakrefs to HardwareManager-like objects
_registry_lock = threading.Lock()
_installed = False
_console_handler_ref = None   # keeps the ctypes callback alive

# Windows console control events (besides Ctrl+C, left to SIGINT).
_CTRL_BREAK_EVENT, _CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT = 1, 2, 5, 6
_CONSOLE_EVENT_NAMES = {
    _CTRL_BREAK_EVENT: "Ctrl+Break", _CTRL_CLOSE_EVENT: "console close",
    _CTRL_LOGOFF_EVENT: "user logoff", _CTRL_SHUTDOWN_EVENT: "system shutdown",
}


def register_hardware(hw) -> None:
    """Register an object exposing is_safe() -> bool and
    emergency_shutdown(reason). Held weakly."""
    with _registry_lock:
        _registered.append(weakref.ref(hw))


def _live_hardware() -> list:
    with _registry_lock:
        live = [r() for r in _registered]
        _registered[:] = [r for r in _registered if r() is not None]
    return [hw for hw in live if hw is not None]


def _all_safe() -> bool:
    for hw in _live_hardware():
        try:
            if not hw.is_safe():
                return False
        except Exception:
            return False
    return True


def shutdown_now(reason: str, grace_s: float, *, poll_s: float = 0.1) -> bool:
    """Cancel running operations, wait up to `grace_s` for hardware to reach
    a safe state, then force whatever is still connected. Returns True if
    everything was safe without forcing."""
    _log.critical("Termination requested (%s) -- cancelling running operations, "
                  "grace %.1fs.", reason, grace_s)
    cancel_all_tokens(reason)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if _all_safe():
            _log.warning("Termination: hardware reached a safe state cooperatively.")
            return True
        time.sleep(poll_s)
    if _all_safe():
        return True
    _log.critical("Termination: grace expired with hardware still connected -- forcing "
                  "emergency shutdown.")
    for hw in _live_hardware():
        try:
            if not hw.is_safe():
                hw.emergency_shutdown(reason)
        except Exception as e:
            _log.critical("Termination: emergency shutdown raised: %s. Hardware may still be "
                          "energized -- physically disconnect power.", e)
    return False


def _terminate(reason: str, grace_s: float, exit_fn, exit_code: int) -> None:
    try:
        shutdown_now(reason, grace_s)
    finally:
        try:
            for handler in logging.getLogger().handlers:
                handler.flush()
            sys.stdout.flush()
        except Exception:
            pass
        exit_fn(exit_code)


def _spawn_terminate(reason: str, grace_s: float, exit_fn, exit_code: int) -> threading.Thread:
    thread = threading.Thread(
        target=_terminate, args=(reason, grace_s, exit_fn, exit_code),
        name="termination", daemon=False,
    )
    thread.start()
    return thread


def install_termination_handlers(*, exit_fn=os._exit) -> bool:
    """Install SIGTERM/SIGHUP (and, on Windows, console close/logoff/
    shutdown/Ctrl+Break) handlers. Idempotent. Must be called from the main
    thread. `exit_fn` is injectable for tests. Returns True if newly
    installed."""
    global _installed
    if _installed:
        return False
    _installed = True

    def _on_signal(signum, _frame):
        name = signal.Signals(signum).name
        _spawn_terminate(f"{name} received", Settings.TERMINATION_GRACE_S, exit_fn, 128 + signum)

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError) as e:
            _log.warning("Could not install %s handler: %s", name, e)

    if os.name == "nt":
        _install_windows_console_handler(exit_fn)
    return True


def _install_windows_console_handler(exit_fn) -> None:
    global _console_handler_ref
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return

    def _handler(ctrl_type):
        name = _CONSOLE_EVENT_NAMES.get(ctrl_type)
        if name is None:
            return False   # Ctrl+C etc. -- fall through to Python's SIGINT handling
        grace = (Settings.TERMINATION_GRACE_S if ctrl_type == _CTRL_BREAK_EVENT
                 else Settings.CONSOLE_CLOSE_GRACE_S)
        # Runs on its own OS thread; do the work here (the OS may kill the
        # process the moment this returns) and never block on the main thread.
        _terminate(f"{name} received", grace, exit_fn, 128 + ctrl_type)
        return True

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    _console_handler_ref = proto(_handler)
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, True):
        _log.warning("SetConsoleCtrlHandler failed -- console close/Ctrl+Break are not handled.")
