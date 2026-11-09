"""Configure logging to a rotating file plus the terminal."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app import APP_ID
from app.paths import STATE_DIR

DEFAULT_LOG_DIR = STATE_DIR
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(level: str, log_dir: Path = DEFAULT_LOG_DIR) -> Path:
    """Send log records to <APP_ID>.log and stderr. Returns the log file path.

    The thread name is in every line because PJSIP will call us from its own
    threads; seeing which thread logged something makes those bugs findable.
    """
    # Owner-only folder: SIP traces show who called whom. Locking the folder
    # covers every file in it, including ones log rotation creates later.
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    log_file = log_dir / f"{APP_ID}.log"

    # Rotate at 5 MB, keep 3 old files, so SIP traces can't fill the disk.
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    console_handler = logging.StreamHandler()

    formatter = logging.Formatter(LOG_FORMAT)
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    return log_file
