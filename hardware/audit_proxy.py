"""
Automatic hardware-audit instrumentation -- see docs/architecture.md
"Hardware Audit Trail: Interception Point". Wraps every PUBLIC method of
an already-constructed hardware driver instance (SMU, DMM, DAQ,
RelayBase/any concrete relay driver, ConfigDrivenSenseRouter, and any
FUTURE driver -- e.g. a PXI Relay Matrix -- built on the same
hardware/base.py::HardwareBase convention) so that every call is timed,
its outcome recorded to raw_hardware_log (data/raw_hardware_log.py), and
the original call's behavior (return value, or the exact exception
raised) is otherwise completely unchanged.

Design choice -- instance-level method shadowing, NOT a wrapping
proxy/decorator object: `instrument_hardware_instance()` replaces
specific bound-method ATTRIBUTES on the real instance in place
(`setattr(instance, name, wrapped)`), rather than substituting a
`__getattr__`-forwarding proxy object in HardwareManager's place of the
real one. This matters because callers throughout this codebase read
non-method attributes directly off hardware objects (e.g.
`self.smu.model`, `self.dmm.resource`, `dev.connected`, and
`self._ntc_daq is self._daq` identity comparisons in
test_control/hardware_manager.py) and because
`self._relay.__class__.__name__` is read for a log message in
HardwareManager.__init__() -- a `__getattr__` proxy would need special-
casing for all of that (and for `isinstance()`/`type()`) to stay
transparent; shadowing individual method attributes on the SAME object
leaves `type()`/`isinstance()`/`__class__`/`is`-identity/every
non-method attribute completely untouched, because the object being
passed around never changes identity at all -- only specific methods on
it start doing one extra thing before/after delegating to the original
implementation.

Only PUBLIC methods (no leading underscore) are wrapped -- this captures
exactly the caller-facing action surface (open(), close(), connect(),
output_enable(), measure(), read_channel(), ...) without also logging a
method's own internal implementation calls (e.g. RelayBase.open()
calling its own _open_impl()), which would double-count one caller-
visible action as two audit rows.
"""

from __future__ import annotations

import inspect
import time

# Method names treated as "repetitive measurement reads" for sampling
# purposes (see docs/architecture.md "Measurement Handling" and
# config/settings.py::RAW_HW_LOG_MEASUREMENTS/RAW_HW_MEASUREMENT_SAMPLE_RATE).
# Deliberately an explicit allow-list, not an inferred/heuristic
# classification -- anything NOT in this set (every state-changing
# command, connect/disconnect, query/verify method) is always logged in
# full regardless of sampling configuration, which is the safe default:
# a new method added to a driver later is always-logged until someone
# deliberately opts it into sampling here.
MEASUREMENT_METHOD_NAMES = frozenset({"measure", "measure_dc_voltage", "read_channel"})

# Parameter names checked, in priority order, to opportunistically
# recover a position/channel for the raw_hardware_log row. This is a
# best-effort convenience, not a guarantee -- see docs/architecture.md
# "Known Deviations": SMU/DMM methods take no position argument at all
# (they act on whatever the relay currently has connected), so their
# audit rows have position=NULL; only relay/matrix-style calls
# (open(channel)/close(channel)/query(channel)) populate it. This was a
# deliberate choice to avoid the alternative -- threading a position
# argument through every test-sequence call site -- which would violate
# the "zero changes at call sites" / "do not modify business logic"
# constraints this feature was built under.
_POSITION_PARAM_NAMES = ("channel", "relay_address", "position")


def _extract_position(sig: inspect.Signature, args: tuple, kwargs: dict):
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except TypeError:
        return None
    for name in _POSITION_PARAM_NAMES:
        if name in bound.arguments:
            value = bound.arguments[name]
            if isinstance(value, int):
                return value
    return None


def _should_log_measurement(settings, counters: dict, key) -> bool:
    """
    True iff this particular measurement-method call should be logged,
    per RAW_HW_LOG_MEASUREMENTS/RAW_HW_MEASUREMENT_SAMPLE_RATE. Only
    called for SUCCESSFUL measurement-method calls -- failures are
    always logged unconditionally by the caller, never subject to this
    gate (see docs/architecture.md "Failure Handling": sampling must
    never suppress the one moment an audit trail matters most).
    """
    if not getattr(settings, "RAW_HW_LOG_MEASUREMENTS", True):
        return False
    rate = max(1, int(getattr(settings, "RAW_HW_MEASUREMENT_SAMPLE_RATE", 1)))
    count = counters.get(key, 0)
    counters[key] = count + 1
    return count % rate == 0  # zero-indexed: always logs the 1st, (rate+1)th, ... call
    # (rate=1 -> count % 1 == 0 for every count -> logs every call, unlike a
    # 1-indexed "count % rate == 1" scheme, which is never true when rate=1)


def instrument_hardware_instance(instance, *, device_type: str, writer,
                                  run_id_provider, settings) -> None:
    """
    Wrap every public method of `instance` (a HardwareBase subclass
    instance, or any object with a comparable public method surface --
    e.g. ConfigDrivenSenseRouter, which is not itself HardwareBase-
    derived but is constructed the same "once, at a known site" way) so
    every call is audited to `writer` (a data.raw_hardware_log.
    RawHardwareLogWriter). Idempotent -- calling this twice on the same
    instance (e.g. because a group's NTC DAQ resolves to the SAME
    instance as its main DAQ -- see HardwareManager.__init__()'s
    `self._ntc_daq is self._daq` case) is a safe no-op the second time.

    No-ops entirely if `settings.ENABLE_RAW_HARDWARE_LOGGING` is False --
    the instance is returned to callers completely unmodified, so
    disabling the feature has zero runtime overhead, not just "logs
    nothing."

    `run_id_provider` is a zero-arg callable invoked FRESH on every
    wrapped call (never memoized here) -- HardwareManager's own run_id
    isn't known at construction time in the real call order (Hardware
    Manager connects before DataStorage is opened in every real
    workflow -- see test.py), and Group -> ALL reassigns run_id
    per-position on the SAME already-instrumented instances via
    DataStorage.begin_new_run_id(). A provider that returns None (the
    default, before anything attaches a real one) simply logs
    run_id=NULL, which the schema supports explicitly for exactly this
    pre-run-id startup/shutdown case.
    """
    if getattr(instance, "_nipxi_audit_instrumented", False):
        return
    if not getattr(settings, "ENABLE_RAW_HARDWARE_LOGGING", True):
        return

    sample_counters: dict = {}

    for name, bound_method in inspect.getmembers(instance, predicate=inspect.ismethod):
        if name.startswith("_"):
            continue
        try:
            sig = inspect.signature(bound_method)
        except (TypeError, ValueError):
            continue  # a method whose signature can't be introspected -- skip, don't guess

        is_measurement = name in MEASUREMENT_METHOD_NAMES

        def _make_wrapper(original=bound_method, command=name, sig=sig, is_measurement=is_measurement):
            def _wrapped(*args, **kwargs):
                start = time.monotonic()
                try:
                    result = original(*args, **kwargs)
                except Exception as exc:
                    duration_ms = (time.monotonic() - start) * 1000.0
                    writer.log(
                        run_id=run_id_provider(), position=_extract_position(sig, args, kwargs),
                        device_type=device_type, device_name=getattr(instance, "name", device_type),
                        resource=getattr(instance, "resource", None), command=command,
                        command_parameters={"args": args, "kwargs": kwargs}, response=None,
                        success=False, duration_ms=duration_ms,
                        error_type=type(exc).__name__, error_message=str(exc),
                    )
                    raise
                duration_ms = (time.monotonic() - start) * 1000.0
                if not is_measurement or _should_log_measurement(settings, sample_counters, command):
                    writer.log(
                        run_id=run_id_provider(), position=_extract_position(sig, args, kwargs),
                        device_type=device_type, device_name=getattr(instance, "name", device_type),
                        resource=getattr(instance, "resource", None), command=command,
                        command_parameters={"args": args, "kwargs": kwargs}, response=result,
                        success=True, duration_ms=duration_ms, error_type=None, error_message=None,
                    )
                return result
            return _wrapped

        setattr(instance, name, _make_wrapper())

    instance._nipxi_audit_instrumented = True
