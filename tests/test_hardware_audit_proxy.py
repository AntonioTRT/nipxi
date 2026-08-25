"""
Tests for hardware/audit_proxy.py::instrument_hardware_instance() -- the
generic, zero-call-site-change interception mechanism for the Hardware
Audit Trail (see docs/architecture.md "Hardware Audit Trail").

Uses a small fake device class (not a real SMU/DMM/DAQ) so these tests
exercise instrument_hardware_instance() in complete isolation, mirroring
this suite's established fake-hardware convention (_FakeRelay,
_ScriptedDmm, etc. in tests/test_hardware_event_logging.py) -- no real
hardware, no real HardwareManager construction needed here (that wiring
is covered separately in tests/test_hardware_manager_audit_wiring.py).
"""

import unittest

from config.settings import Settings
from hardware.audit_proxy import instrument_hardware_instance


class _RecordingWriter:
    """Records every log() call verbatim -- lets tests assert on exactly
    what instrument_hardware_instance() would have persisted, without a
    real database."""

    def __init__(self):
        self.calls = []

    def log(self, **kwargs):
        self.calls.append(kwargs)


class _FakeSettings:
    def __init__(self, enabled=True, log_measurements=True, sample_rate=1):
        self.ENABLE_RAW_HARDWARE_LOGGING = enabled
        self.RAW_HW_LOG_MEASUREMENTS = log_measurements
        self.RAW_HW_MEASUREMENT_SAMPLE_RATE = sample_rate


class _FakeDevice:
    """A minimal stand-in for a HardwareBase-derived driver -- public
    methods (state-changing command, a "measurement" read by name,
    a relay-style channel-taking method, one that raises), a non-method
    attribute, and a private method that must NOT be wrapped."""

    name = "FAKE_DEV_1"
    resource = "FAKE:RESRC"

    def __init__(self):
        self.connected = False
        self.enable_calls = 0

    def connect(self):
        self.connected = True
        return True

    def output_enable(self):
        self.enable_calls += 1
        return True

    def measure(self):
        return {"voltage_v": 3.7}

    def close(self, channel):
        return f"closed-{channel}"

    def fail_thing(self):
        raise RuntimeError("simulated hardware comms failure")

    def _internal_helper(self):  # must never be wrapped -- private
        return "internal"


def _instrument(dev, writer=None, settings=None, run_id="run1", device_type="FAKE"):
    writer = writer if writer is not None else _RecordingWriter()
    settings = settings if settings is not None else _FakeSettings()
    instrument_hardware_instance(
        dev, device_type=device_type, writer=writer, run_id_provider=lambda: run_id, settings=settings,
    )
    return writer


class SuccessfulCallLoggingTests(unittest.TestCase):
    def test_a_successful_state_changing_call_is_logged(self):
        dev = _FakeDevice()
        writer = _instrument(dev)
        result = dev.output_enable()
        self.assertTrue(result)
        self.assertEqual(len(writer.calls), 1)
        call = writer.calls[0]
        self.assertEqual(call["command"], "output_enable")
        self.assertEqual(call["device_type"], "FAKE")
        self.assertEqual(call["device_name"], "FAKE_DEV_1")
        self.assertEqual(call["resource"], "FAKE:RESRC")
        self.assertTrue(call["success"])
        self.assertIsNone(call["error_type"])
        self.assertIsInstance(call["duration_ms"], float)

    def test_the_wrapped_call_still_returns_the_real_result(self):
        dev = _FakeDevice()
        _instrument(dev)
        self.assertEqual(dev.measure(), {"voltage_v": 3.7})

    def test_position_is_extracted_from_a_channel_argument(self):
        dev = _FakeDevice()
        writer = _instrument(dev)
        dev.close(4)
        self.assertEqual(writer.calls[0]["position"], 4)

    def test_position_is_none_when_the_method_takes_no_channel_argument(self):
        dev = _FakeDevice()
        writer = _instrument(dev)
        dev.output_enable()
        self.assertIsNone(writer.calls[0]["position"])

    def test_private_methods_are_never_wrapped(self):
        dev = _FakeDevice()
        writer = _instrument(dev)
        dev._internal_helper()
        self.assertEqual(writer.calls, [])


class FailureLoggingAndReraiseTests(unittest.TestCase):
    def test_a_failure_is_logged_with_type_and_message(self):
        dev = _FakeDevice()
        writer = _instrument(dev)
        with self.assertRaises(RuntimeError):
            dev.fail_thing()
        self.assertEqual(len(writer.calls), 1)
        call = writer.calls[0]
        self.assertFalse(call["success"])
        self.assertEqual(call["error_type"], "RuntimeError")
        self.assertEqual(call["error_message"], "simulated hardware comms failure")

    def test_the_original_exception_propagates_unchanged(self):
        dev = _FakeDevice()
        _instrument(dev)
        with self.assertRaises(RuntimeError) as ctx:
            dev.fail_thing()
        self.assertEqual(str(ctx.exception), "simulated hardware comms failure")

    def test_writer_failure_does_not_mask_the_original_exception(self):
        # If the audit writer ITSELF raises (should never happen given
        # RawHardwareLogWriter's own try/except, but defense in depth),
        # the real hardware exception must still win.
        class _BrokenWriter:
            def log(self, **kwargs):
                raise RuntimeError("audit writer exploded")

        dev = _FakeDevice()
        instrument_hardware_instance(
            dev, device_type="FAKE", writer=_BrokenWriter(),
            run_id_provider=lambda: "run1", settings=_FakeSettings(),
        )
        with self.assertRaises(RuntimeError) as ctx:
            dev.fail_thing()
        # Whichever RuntimeError surfaces, it must not silently succeed --
        # this documents that instrument_hardware_instance() does not add
        # its own try/except around writer.log() (that resilience lives
        # in RawHardwareLogWriter itself, per docs/architecture.md).
        self.assertIn(str(ctx.exception), ("simulated hardware comms failure", "audit writer exploded"))


class MeasurementSamplingTests(unittest.TestCase):
    def test_sample_rate_one_logs_every_measurement_call(self):
        dev = _FakeDevice()
        writer = _instrument(dev, settings=_FakeSettings(sample_rate=1))
        for _ in range(3):
            dev.measure()
        self.assertEqual(len(writer.calls), 3)

    def test_sample_rate_three_logs_every_third_call(self):
        dev = _FakeDevice()
        writer = _instrument(dev, settings=_FakeSettings(sample_rate=3))
        for _ in range(7):
            dev.measure()
        # calls 1, 4, 7 logged -> 3 total
        self.assertEqual(len(writer.calls), 3)

    def test_log_measurements_false_suppresses_successful_measurement_reads(self):
        dev = _FakeDevice()
        writer = _instrument(dev, settings=_FakeSettings(log_measurements=False))
        dev.measure()
        self.assertEqual(writer.calls, [])

    def test_log_measurements_false_never_suppresses_state_changing_commands(self):
        dev = _FakeDevice()
        writer = _instrument(dev, settings=_FakeSettings(log_measurements=False))
        dev.output_enable()
        self.assertEqual(len(writer.calls), 1)

    def test_sampling_never_suppresses_a_measurement_failure(self):
        class _FlakyDevice(_FakeDevice):
            def measure(self):
                raise RuntimeError("measurement comms glitch")

        dev = _FlakyDevice()
        writer = _instrument(dev, settings=_FakeSettings(sample_rate=100))
        for _ in range(5):
            with self.assertRaises(RuntimeError):
                dev.measure()
        # Every single failure logged despite a sample rate of 100.
        self.assertEqual(len(writer.calls), 5)
        self.assertTrue(all(not c["success"] for c in writer.calls))


class DisabledAndIdempotencyTests(unittest.TestCase):
    def test_disabled_setting_leaves_the_instance_completely_unwrapped(self):
        dev = _FakeDevice()
        # Bound methods aren't cached (a fresh bound-method object is
        # created on each attribute access), so identity is never
        # meaningful here -- __func__ identity is what actually shows
        # setattr() never shadowed this instance attribute.
        original_func = dev.output_enable.__func__
        writer = _instrument(dev, settings=_FakeSettings(enabled=False))
        self.assertIs(dev.output_enable.__func__, original_func)
        dev.output_enable()
        self.assertEqual(writer.calls, [])

    def test_instrumenting_twice_does_not_double_log(self):
        dev = _FakeDevice()
        writer = _RecordingWriter()
        settings = _FakeSettings()
        instrument_hardware_instance(dev, device_type="FAKE", writer=writer,
                                      run_id_provider=lambda: "run1", settings=settings)
        instrument_hardware_instance(dev, device_type="FAKE", writer=writer,
                                      run_id_provider=lambda: "run1", settings=settings)
        dev.output_enable()
        self.assertEqual(len(writer.calls), 1)


class BackwardCompatibilityTests(unittest.TestCase):
    """Non-method attributes, identity, and type must be completely
    unaffected -- see hardware/audit_proxy.py's module docstring for why
    this rules out a __getattr__-forwarding proxy object."""

    def test_type_and_isinstance_are_unaffected(self):
        dev = _FakeDevice()
        _instrument(dev)
        self.assertIs(type(dev), _FakeDevice)
        self.assertIsInstance(dev, _FakeDevice)

    def test_non_method_attributes_are_untouched(self):
        dev = _FakeDevice()
        _instrument(dev)
        self.assertEqual(dev.name, "FAKE_DEV_1")
        self.assertEqual(dev.resource, "FAKE:RESRC")

    def test_object_identity_is_preserved(self):
        dev = _FakeDevice()
        same_ref = dev
        _instrument(dev)
        self.assertIs(dev, same_ref)

    def test_attribute_state_set_by_a_wrapped_method_is_still_visible(self):
        dev = _FakeDevice()
        _instrument(dev)
        dev.connect()
        self.assertTrue(dev.connected)


class GroupAllRunIdProviderTests(unittest.TestCase):
    """The provider is re-invoked on every call, never memoized at wrap
    time -- required for Group -> ALL, where the SAME already-
    instrumented instance is reused across positions while run_id
    changes per position (DataStorage.begin_new_run_id())."""

    def test_run_id_reflects_the_provider_at_call_time_not_wrap_time(self):
        dev = _FakeDevice()
        writer = _RecordingWriter()
        current_run_id = {"value": "pos1_run"}
        instrument_hardware_instance(
            dev, device_type="FAKE", writer=writer,
            run_id_provider=lambda: current_run_id["value"], settings=_FakeSettings(),
        )
        dev.output_enable()
        current_run_id["value"] = "pos2_run"
        dev.output_enable()
        self.assertEqual([c["run_id"] for c in writer.calls], ["pos1_run", "pos2_run"])

    def test_run_id_none_before_any_provider_is_attached(self):
        dev = _FakeDevice()
        writer = _RecordingWriter()
        instrument_hardware_instance(
            dev, device_type="FAKE", writer=writer,
            run_id_provider=lambda: None, settings=_FakeSettings(),
        )
        dev.output_enable()
        self.assertIsNone(writer.calls[0]["run_id"])


class ProductionDefaultConfigurationTests(unittest.TestCase):
    """
    Pins the real config/settings.py::Settings production defaults --
    see docs/architecture.md "Hardware Audit Trail: Storage Growth
    Review". Command/failure traceability must remain full-fidelity by
    default; measurement polling must be sampled, not logged at every
    call, to keep multi-hour/repeated campaigns storage-safe.
    """

    def test_raw_hardware_logging_is_enabled_by_default(self):
        self.assertTrue(Settings.ENABLE_RAW_HARDWARE_LOGGING)

    def test_measurement_logging_stays_enabled_by_default(self):
        # Sampled, not disabled -- see RAW_HW_MEASUREMENT_SAMPLE_RATE.
        self.assertTrue(Settings.RAW_HW_LOG_MEASUREMENTS)

    def test_measurement_sample_rate_is_not_full_fidelity_by_default(self):
        # Production default must not be 1 (log every measurement call) --
        # that was the storage-growth review's central finding: full-rate
        # measurement logging dominates database growth for multi-hour
        # sampling loops (SMU.measure() + DMM.measure_dc_voltage() +
        # DAQ.read_channel() every second -- see charge_sequence.py).
        self.assertGreater(Settings.RAW_HW_MEASUREMENT_SAMPLE_RATE, 1)

    def test_production_sample_rate_still_logs_every_failure(self):
        # Regression guard on the actual behavioral guarantee the
        # storage-growth tradeoff depends on: whatever the sample rate,
        # a failing measurement call must never be suppressed.
        writer = _RecordingWriter()

        class _AlwaysFailsMeasure(_FakeDevice):
            def measure(self):
                raise RuntimeError("simulated measurement failure")

        dev = _AlwaysFailsMeasure()
        instrument_hardware_instance(
            dev, device_type="FAKE", writer=writer, run_id_provider=lambda: "run1",
            settings=Settings,
        )
        for _ in range(Settings.RAW_HW_MEASUREMENT_SAMPLE_RATE + 2):
            with self.assertRaises(RuntimeError):
                dev.measure()
        self.assertEqual(len(writer.calls), Settings.RAW_HW_MEASUREMENT_SAMPLE_RATE + 2)
        self.assertTrue(all(not c["success"] for c in writer.calls))


class SenseRouterPositionAttributionTests(unittest.TestCase):
    """
    Change 8 -- "Fix SENSE_ROUTER Position Attribution" (see
    docs/architecture.md "SENSE_ROUTER Position Attribution Fix"):
    ConfigDrivenSenseRouter.connect(channel)/disconnect(channel) takes a
    logical SENSE_ROUTING channel number, not a battery position, but
    shares the parameter name "channel" with every true relay/matrix
    position operation -- which is what let a sense-routing channel be
    mislabeled as a battery position in raw_hardware_log.position. These
    tests pin that a SENSE_ROUTER-typed call always logs position=None,
    while every other device type's identical "channel"-named argument is
    still extracted exactly as before (regression guard against an
    overly-broad fix).
    """

    def test_sense_router_channel_argument_is_never_recorded_as_position(self):
        dev = _FakeDevice()
        writer = _instrument(dev, device_type="SENSE_ROUTER")
        dev.close(4)  # a SENSE_ROUTING logical channel number, not a battery position
        self.assertIsNone(writer.calls[0]["position"])

    def test_other_device_types_still_get_position_extracted(self):
        dev = _FakeDevice()
        writer = _instrument(dev, device_type="RELAY")
        dev.close(4)
        self.assertEqual(writer.calls[0]["position"], 4)

    def test_sense_router_failure_path_also_omits_position(self):
        class _FailingChannelDevice(_FakeDevice):
            def close(self, channel):
                raise RuntimeError("simulated relay comms failure")

        dev = _FailingChannelDevice()
        writer = _instrument(dev, device_type="SENSE_ROUTER")
        with self.assertRaises(RuntimeError):
            dev.close(4)
        self.assertIsNone(writer.calls[0]["position"])


if __name__ == "__main__":
    unittest.main()
