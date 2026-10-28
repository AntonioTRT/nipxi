"""
Frozen-measurement detection.

A reading can be finite, in range, and still be stale: an instrument that
keeps answering with its last value defeats every limit check built on it
(safety.check() would keep passing on a voltage that stopped being real).
The existing driver checks (finite / in range) cannot see that.

FrozenSignalDetector flags a channel whose value stayed within `epsilon`
of itself for `window` consecutive samples. It is deliberately dumb -- no
statistics, no physics; the CALLER decides when a signal is expected to
move (see BatteryOperationSequence._frozen_check()) and resets the detector
otherwise. Thresholds live in Settings.FROZEN_* and are uncalibrated
starting values.
"""

from __future__ import annotations


class FrozenSignalDetector:
    def __init__(self, window: int, epsilon: float = 0.0):
        if window < 2:
            raise ValueError("window must be >= 2")
        self.window = window
        self.epsilon = epsilon
        self.reset()

    def reset(self) -> None:
        self._lo = None
        self._hi = None
        self.count = 0

    def update(self, value) -> bool:
        """Feed one sample. Returns True once `window` consecutive samples
        all lie within `epsilon` of each other. None is ignored."""
        if value is None:
            return False
        if self._lo is None:
            self._lo = self._hi = value
            self.count = 1
        else:
            lo, hi = min(self._lo, value), max(self._hi, value)
            if hi - lo <= self.epsilon:
                self._lo, self._hi = lo, hi
                self.count += 1
            else:
                self._lo = self._hi = value
                self.count = 1
        return self.count >= self.window
