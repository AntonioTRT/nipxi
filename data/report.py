"""
Report generation placeholder.
Reads from SQLite database and produces a summary report.

TODO: Implement with pandas + matplotlib or a simple text/HTML template.
"""

import logging
import os
from config.settings import Settings


class ReportGenerator:
    def __init__(self, settings: Settings):
        self.s = settings
        self.log = logging.getLogger("nipxi.report")

    def generate(self, run_id: str):
        """
        Generate a report for the given run_id.
        Output: text summary + optional plots saved to settings.REPORT_DIR
        """
        os.makedirs(self.s.REPORT_DIR, exist_ok=True)
        self.log.info("Generating report for run_id=%s", run_id)

        # TODO: query measurements table filtered by run_id
        # TODO: compute capacity (Ah) = integral of current over time per channel
        # TODO: compute energy (Wh) = integral of V*I over time
        # TODO: produce per-channel summary table
        # TODO: plot V/I vs time, optionally temperature
        # TODO: save to REPORT_DIR/<run_id>.txt or .html

        self.log.warning("Report generation not yet implemented.")
