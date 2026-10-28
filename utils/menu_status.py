"""
Status styling for operator menus and startup/pre-flight messages (test.py).

One three-color scheme everywhere:

  VERIFIED  green    menus: validated on real hardware
                     startup: validation passed / configuration valid / OK
  PARTIAL   yellow   menus: works and exercised, still needs broader testing
                     startup: warning / partial validation / degraded mode
  FAILED    red      startup: validation failed / startup-blocking error /
                     hardware fault
  FUTURE    neutral  menus only: unimplemented, experimental, offline-only,
                     or without real-hardware validation

Menu status describes REAL-BENCH validation history, not whether code
exists. Only the status WORD is colored; the word itself ("OK", "WARNING",
"FAIL", "PARTIAL") always carries the meaning, so output stays readable
with colors off.

Colors are plain ANSI (the same escape sequences test_control/
execution_screen.py already prints) and are suppressed when stdout is not
a terminal or NO_COLOR is set, so piped/logged output stays clean.
"""

import os
import sys

VERIFIED = "verified"
PARTIAL = "partial"
FAILED = "failed"
FUTURE = "future"

_ANSI = {VERIFIED: "\033[32m", PARTIAL: "\033[33m", FAILED: "\033[31m"}

# test.py TestResult status strings -> style
_RESULT_STYLE = {"PASS": VERIFIED, "WARNING": PARTIAL, "FAIL": FAILED}
_RESET = "\033[0m"

_vt_enabled = False


def color_enabled() -> bool:
    global _vt_enabled
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt" and not _vt_enabled:
        os.system("")  # switches the Windows console into VT/ANSI mode
        _vt_enabled = True
    return True


def style(text: str, status) -> str:
    """`text` wrapped in the status color; unchanged for FUTURE/None or when
    colors are disabled."""
    code = _ANSI.get(status)
    if code is None or not color_enabled():
        return text
    return f"{code}{text}{_RESET}"


def result_style(result_status: str):
    """Style for a test.py result status string ("PASS"/"WARNING"/"FAIL")."""
    return _RESULT_STYLE.get(result_status)


def status_line(label: str, word: str, status, indent: str = "  ") -> str:
    """'  <label>: <WORD>' with only the word colored."""
    return f"{indent}{label}: {style(word, status)}"
