"""
Tests for Phase A Item 1 -- config/devices.py::STATION_INFO (see
docs/architecture.md "Station Identity"). STATION_INFO represents the
PHYSICAL RACK, never the PC running the software -- these tests pin its
shape and confirm hostname/IP are NOT part of it.
"""

import unittest

import config.devices as dev_cfg


class StationInfoShapeTests(unittest.TestCase):
    def test_station_info_exists_and_has_required_keys(self):
        self.assertIn("station_id", dev_cfg.STATION_INFO)
        self.assertIn("station_name", dev_cfg.STATION_INFO)
        self.assertIn("location", dev_cfg.STATION_INFO)

    def test_station_id_is_a_short_stable_token(self):
        station_id = dev_cfg.STATION_INFO["station_id"]
        self.assertIsInstance(station_id, str)
        self.assertTrue(station_id)
        self.assertNotIn(" ", station_id, "station_id is used as a run_id prefix -- must not contain spaces")

    def test_station_identity_is_not_a_pc_identity(self):
        # Station identity represents the physical rack, not the PC --
        # hostname/IP must never be part of the stored identity dict (see
        # module docstring: "remain a live, runtime-only diagnostic").
        self.assertNotIn("hostname", dev_cfg.STATION_INFO)
        self.assertNotIn("ip_address", dev_cfg.STATION_INFO)
        self.assertNotIn("ip", dev_cfg.STATION_INFO)


class StationInfoUsedByDataStorageTests(unittest.TestCase):
    """Confirms STATION_INFO is actually consulted, not just defined --
    full round-trip coverage lives in tests/test_run_sequence.py; this is
    the narrow "is it wired in at all" check."""

    def test_run_id_is_prefixed_with_station_id(self):
        import os
        import shutil
        import tempfile

        from data.storage import DataStorage

        tmp_dir = tempfile.mkdtemp()
        try:
            settings = type("_S", (), {
                "DATA_DIR": tmp_dir, "CSV_DIR": os.path.join(tmp_dir, "csv"),
            })
            storage = DataStorage(settings=settings)
            storage.open()
            try:
                self.assertTrue(storage.run_id.startswith(dev_cfg.STATION_INFO["station_id"] + "-"))
            finally:
                storage.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
