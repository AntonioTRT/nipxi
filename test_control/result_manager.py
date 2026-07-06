"""
Result Manager
==============

Owns the storage backend and report generation for a test run.

Responsibilities:
  - Create and expose the StorageBackend used by TestExecutor
  - Open and close the storage backend (context manager)
  - Trigger report generation after a run completes
  - Serve as the single integration point for swapping SQLite to MiniSQL

Usage:
    from test_control.result_manager import ResultManager
    from test_control.test_executor import TestRunResult
    from config.settings import Settings

    mgr = ResultManager(settings=Settings)

    with mgr:                              # opens DataStorage
        result = executor.run(channels=[1, 2, 3])

    mgr.generate_report(result.run_id)    # after storage is closed

MiniSQL integration:
    Pass a MiniSQLStorage instance (which implements StorageBackend) via the
    storage_backend argument. No other code changes needed:

        from data.storage_minisql import MiniSQLStorage
        mgr = ResultManager(settings=Settings,
                            storage_backend=MiniSQLStorage(cfg=MINISQL_CONFIG))
"""

import logging

from config.settings import Settings
from data.storage import DataStorage, StorageBackend
from data.report import ReportGenerator


class ResultManager:
    """
    Manages measurement persistence and report generation.

    The `storage` property returns the open backend; pass it to TestExecutor
    before opening so the executor can call storage.record() during the run.

    Args:
        settings:         Settings class (class-level attributes).
        storage_backend:  Optional pre-built StorageBackend (e.g. MiniSQLStorage).
                          If None, a DataStorage (SQLite + CSV) is created automatically.
    """

    def __init__(self, settings: Settings, storage_backend: StorageBackend = None):
        self.s   = settings
        self.log = logging.getLogger("nipxi.result_manager")

        # Allow injection of an alternative backend (MiniSQL, mock, etc.).
        # Default to SQLite + CSV.
        self._storage: StorageBackend = storage_backend or DataStorage(settings=settings)
        self._report  = ReportGenerator(settings)

    # ------------------------------------------------------------------
    # Storage access
    # ------------------------------------------------------------------

    @property
    def storage(self) -> StorageBackend:
        """
        The storage backend.

        Pass this to TestExecutor before opening the context manager:
            mgr = ResultManager(Settings)
            executor = TestExecutor(hw, mgr.storage, Settings)
            with mgr:
                result = executor.run()
        """
        return self._storage

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_report(self, run_id: str):
        """
        Generate a summary report for the given run_id.

        Call this after the storage context manager has closed (data is flushed).
        The report is written to settings.REPORT_DIR.

        Args:
            run_id: The run identifier returned by TestRunResult.run_id.
        """
        self.log.info("Generating report for run_id=%s", run_id)
        self._report.generate(run_id)

    def save_run(self, result):
        """
        Post-run metadata save hook.

        Currently a no-op because measurements are already written per-sample
        inside the context manager. This method exists as a future extension
        point for saving run-level metadata (total capacity, test outcome, etc.)
        to the database or a separate run-log table.

        Args:
            result: TestRunResult from TestExecutor.run().
        """
        # Future: write run-level summary row (run_id, success, channels, duration)
        self.log.debug(
            "save_run called: run_id=%s success=%s",
            result.run_id, result.success
        )

    # ------------------------------------------------------------------
    # Context manager -- delegates to the storage backend
    # ------------------------------------------------------------------

    def __enter__(self):
        self._storage.open()
        return self

    def __exit__(self, *_):
        self._storage.close()
