"""
Logging module.
Configures Python logging for file + console output.
All modules use logging.getLogger("nipxi.<module>").
"""

import logging
import os
from config.settings import Settings


def setup(settings: Settings):
    os.makedirs(os.path.dirname(settings.LOG_FILE), exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(settings.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("nipxi").info("Logging initialized. Level: %s", settings.LOG_LEVEL)
