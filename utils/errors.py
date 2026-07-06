"""Custom exception hierarchy for NIPXI."""


class NIPXIError(Exception):
    """Base exception for all NIPXI errors."""


class HardwareInitError(NIPXIError):
    """Raised when a hardware device fails to initialize."""


class RelayError(NIPXIError):
    """Raised on relay matrix communication failure."""


class DAQError(NIPXIError):
    """Raised on DAQ read/write failure."""


class SMUError(NIPXIError):
    """Raised on SMU communication or compliance failure."""


class SafetyViolationError(NIPXIError):
    """Raised when a safety limit is exceeded. Triggers emergency stop."""


class NIPXITimeoutError(NIPXIError):
    """Raised when a test step exceeds its allowed duration."""


# Alias so existing imports of TimeoutError continue to work.
# Prefer NIPXITimeoutError in new code to avoid shadowing the Python builtin.
TimeoutError = NIPXITimeoutError


class ValidationError(NIPXIError):
    """Raised when configuration or input validation fails."""
