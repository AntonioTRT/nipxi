"""
Tests for orchestration/fault_bus.py::GlobalFaultBus -- the single
cross-thread fault-signaling primitive for Multi-Group Concurrent
Execution (see that module's docstring). Exercises real threads (no
mocking of threading primitives) since the entire point of this class
is correctness under real concurrent access.
"""

import threading
import unittest

from orchestration.fault_bus import GlobalFaultBus


class SingleThreadBehaviorTests(unittest.TestCase):
    def test_starts_untripped_with_no_origin(self):
        bus = GlobalFaultBus()
        self.assertFalse(bus.tripped)
        self.assertIsNone(bus.origin)
        self.assertEqual(bus.all_reports, [])

    def test_report_trips_the_bus_and_records_origin(self):
        bus = GlobalFaultBus()
        bus.report("B1", "SAFETY_FAULT")
        self.assertTrue(bus.tripped)
        self.assertEqual(bus.origin.group_id, "B1")
        self.assertEqual(bus.origin.reason, "SAFETY_FAULT")

    def test_group_id_none_is_valid_for_operator_or_process_level_faults(self):
        bus = GlobalFaultBus()
        bus.report(None, "OPERATOR_SIGINT")
        self.assertTrue(bus.tripped)
        self.assertIsNone(bus.origin.group_id)

    def test_first_report_wins_as_origin_later_reports_still_recorded(self):
        bus = GlobalFaultBus()
        bus.report("B1", "SAFETY_FAULT")
        bus.report("B2", "STATION_FAULT")
        self.assertEqual(bus.origin.group_id, "B1")
        self.assertEqual(len(bus.all_reports), 2)
        self.assertEqual([r.group_id for r in bus.all_reports], ["B1", "B2"])

    def test_wait_for_fault_returns_immediately_true_once_tripped(self):
        bus = GlobalFaultBus()
        bus.report("B1", "SAFETY_FAULT")
        self.assertTrue(bus.wait_for_fault(timeout=1.0))

    def test_wait_for_fault_times_out_false_when_never_tripped(self):
        bus = GlobalFaultBus()
        self.assertFalse(bus.wait_for_fault(timeout=0.05))


class ConcurrentReportingTests(unittest.TestCase):
    def test_many_threads_reporting_simultaneously_is_race_free(self):
        bus = GlobalFaultBus()
        barrier = threading.Barrier(8)

        def worker(i):
            barrier.wait()
            bus.report(f"B{i}", f"fault-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(bus.tripped)
        self.assertEqual(len(bus.all_reports), 8)
        self.assertIn(bus.origin.group_id, {f"B{i}" for i in range(8)})
        # origin must be exactly one of the recorded reports (the first
        # to acquire the lock), never a corrupted/partial value.
        self.assertIn(bus.origin, bus.all_reports)

    def test_waiting_thread_is_released_promptly_by_a_reporting_thread(self):
        bus = GlobalFaultBus()
        released = threading.Event()

        def waiter():
            if bus.wait_for_fault(timeout=5.0):
                released.set()

        t = threading.Thread(target=waiter)
        t.start()
        bus.report("B3", "RelayVerificationFailure")
        t.join(timeout=5.0)
        self.assertTrue(released.is_set())


if __name__ == "__main__":
    unittest.main()
