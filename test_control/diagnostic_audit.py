"""
Generic hardware-audit instrumentation for test.py diagnostics.

Real workflows get automatic raw_hardware_log traceability because
test_control/hardware_manager.py::HardwareManager wraps every device it
constructs via hardware/audit_proxy.py::instrument_hardware_instance().
test.py's own diagnostics (Hardware Discovery, SMU/DMM/DAQ tests, Native
Relay Coexistence Probe, RelayEthernetTest, Relay Safety Self-Test, Matrix
Scan, ...) never go through HardwareManager -- each constructs its own
driver instance directly (RelayFactory.create(cfg), SMU(cfg), DMM(cfg),
DAQ(cfg)), so none of that instrumentation ever applies to them.

This module closes that gap WITHOUT inventing a second logging mechanism:
it reuses the identical data/raw_hardware_log.py::RawHardwareLogWriter and
hardware/audit_proxy.py::instrument_hardware_instance() production code
already uses, and simply calls them from test.py's own construction call
sites via the four _instrumented_*() constructors below.

Correlation tag: raw_hardware_log.run_id is an existing, nullable, non-FK
TEXT column (see data/raw_hardware_log.py's own module docstring -- it
already tolerates run_id=None for pre-run_id hardware calls). Rather than
inventing a new column, a diagnostic invocation gets a synthetic tag,
TEST_<timestamp>_<test_name>, used as that column's value for every
hardware call made during that one invocation -- correlatable, human-
readable, and completely independent of DataStorage/run_sequence/real
run_id allocation, which this module never touches.

Lifecycle: test.py::run_section() (the single function every MENU entry's
results already flow through) calls begin_diagnostic()/end_diagnostic()
around each menu selection; the four _instrumented_*() constructors read
whatever tag is currently active via current_tag(). A diagnostic that
constructs no hardware (e.g. test_configuration()) is simply unaffected --
no tag is ever written if no instrumented instance is created.
"""

from __future__ import annotations

import re
from datetime import datetime

from config.settings import Settings
from data.raw_hardware_log import RawHardwareLogWriter
from hardware.audit_proxy import instrument_hardware_instance

_ACTIVE_TAG: str | None = None


def _sanitize(text: str) -> str:
    """Collapse anything not alnum into a single underscore -- keeps the
    tag a clean, greppable token regardless of the MENU label's spaces/
    punctuation (e.g. "Test SMU (PSU)" -> "Test_SMU_PSU")."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def begin_diagnostic(test_name: str) -> str:
    """
    Generate and activate a new correlation tag for the diagnostic about
    to run. Every hardware instance instrumented via _instrumented_*()
    below while this tag is active shares it as their raw_hardware_log
    row's run_id. Returns the tag (test.py stores it alongside this
    diagnostic's test_execution row -- see data/test_execution_log.py::
    record_test_executions()'s hardware_session_tag parameter).
    """
    global _ACTIVE_TAG
    _ACTIVE_TAG = f"TEST_{datetime.now():%Y%m%d_%H%M%S}_{_sanitize(test_name)}"
    return _ACTIVE_TAG


def end_diagnostic() -> None:
    """Deactivate the current tag. Any hardware instance instrumented
    before this call keeps logging the tag it captured at instrumentation
    time (instrument_hardware_instance() re-invokes run_id_provider on
    every call, but this module's provider closes over current_tag(), not
    a snapshot -- see the module docstring); this only affects instances
    instrumented AFTER this call, which will get a fresh tag or None."""
    global _ACTIVE_TAG
    _ACTIVE_TAG = None


def current_tag() -> str | None:
    return _ACTIVE_TAG


def _instrument(instance, device_type: str):
    """Shared plumbing for every _instrumented_*() constructor below --
    the SAME writer class and interception point HardwareManager uses,
    nothing new. Idempotent (instrument_hardware_instance() no-ops on an
    already-instrumented instance), so calling this twice on the same
    object is harmless."""
    writer = RawHardwareLogWriter(Settings)
    instrument_hardware_instance(
        instance, device_type=device_type, writer=writer,
        run_id_provider=current_tag, settings=Settings,
    )
    return instance


def instrumented_relay(cfg):
    """Drop-in replacement for `RelayFactory.create(cfg)` -- same instance,
    same behavior, now audited to raw_hardware_log."""
    from hardware.relay_factory import RelayFactory
    return _instrument(RelayFactory.create(cfg), "RELAY")


def instrumented_smu(cfg):
    """Drop-in replacement for `SMU(cfg)`."""
    from hardware.smu import SMU
    return _instrument(SMU(cfg), "SMU")


def instrumented_dmm(cfg):
    """Drop-in replacement for `DMM(cfg)`."""
    from hardware.dmm import DMM
    return _instrument(DMM(cfg), "DMM")


def instrumented_daq(cfg):
    """Drop-in replacement for `DAQ(cfg)`."""
    from hardware.daq import DAQ
    return _instrument(DAQ(cfg), "DAQ")
