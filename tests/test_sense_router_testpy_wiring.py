"""
Source-level regression test for test.py::_run_charge_or_discharge()'s
live SenseRouter wiring (see docs/architecture.md "Future Architecture:
Battery Sense Routing", "Live wiring"). Mirrors the established
source-inspection technique (tests/test_cancellation.py,
tests/test_post_isolation_zeroing_ordering.py) for the same reason: the
full behavioral proof (connect/disconnect ordering, backward
compatibility) already lives in tests/test_sense_routing_live_wiring.py
against ChargeSequence directly; this file only confirms test.py's own
construction/teardown call sites are wired correctly, without needing a
full HardwareManager-level integration harness.
"""

import inspect
import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)


class RunChargeOrDischargeSenseRouterWiringTests(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(test_module._run_charge_or_discharge)
        self.lines = self.src.splitlines()

    def _first_index(self, needle):
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if needle in line and stripped and not stripped.startswith("#"):
                return i
        return None

    def test_sense_router_initialized_to_none_before_the_main_try_block(self):
        init_idx = self._first_index("sense_router = None")
        construct_idx = self._first_index("sense_router = ConfigDrivenSenseRouter()")
        self.assertIsNotNone(init_idx, "expected 'sense_router = None' before use")
        self.assertIsNotNone(construct_idx, "expected the ConfigDrivenSenseRouter construction")
        self.assertLess(init_idx, construct_idx)

    def test_sense_router_only_constructed_when_sense_channel_is_configured(self):
        gate_idx = self._first_index("if sense_channel is not None:")
        construct_idx = self._first_index("sense_router = ConfigDrivenSenseRouter()")
        self.assertIsNotNone(gate_idx)
        self.assertIsNotNone(construct_idx)
        self.assertLess(gate_idx, construct_idx)

    def test_sequence_construction_passes_sense_router_and_sense_channel(self):
        src_after_construct = self.src[self.src.index("sequence = sequence_cls("):]
        self.assertIn("sense_router=sense_router", src_after_construct[:600])
        self.assertIn("sense_channel=sense_channel", src_after_construct[:600])

    def test_sense_router_shutdown_happens_in_finally_before_storage_close(self):
        shutdown_idx = self._first_index("sense_router.shutdown()")
        storage_close_idx = self._first_index("storage.close()")
        self.assertIsNotNone(shutdown_idx, "expected sense_router.shutdown() in the finally block")
        self.assertIsNotNone(storage_close_idx)
        self.assertLess(shutdown_idx, storage_close_idx)

    def test_shutdown_is_guarded_by_a_none_check(self):
        idx = self._first_index("sense_router.shutdown()")
        # The nearest preceding "if sense_router is not None:" guards it.
        preceding = "\n".join(self.lines[max(0, idx - 3):idx])
        self.assertIn("if sense_router is not None:", preceding)


if __name__ == "__main__":
    unittest.main()
