"""Logging: console plus a daily rotating file in logs/."""
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import config

FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
NOISY_LOGGERS = ("httpx", "httpx2", "httpcore")
HANDLER_PREFIX = "jobsherpa-"


def setup_logging(log_dir: Path | None = None, level: str | None = None) -> None:
    """Log to the console and to logs/jobsherpa.log (a new file every midnight).

    Safe to call twice: handlers are only added once.
    """
    root = logging.getLogger()
    if any((h.get_name() or "").startswith(HANDLER_PREFIX) for h in root.handlers):
        return
    directory = Path(log_dir or config.LOGS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)

    file_handler = TimedRotatingFileHandler(
        directory / "jobsherpa.log", when="midnight", backupCount=30, encoding="utf-8")
    file_handler.set_name(HANDLER_PREFIX + "file")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")
    console = logging.StreamHandler(sys.stderr)
    console.set_name(HANDLER_PREFIX + "console")
    for handler in (file_handler, console):
        handler.setFormatter(formatter)
        root.addHandler(handler)

    wanted = getattr(logging, (level or config.LOG_LEVEL).upper(), logging.INFO)
    root.setLevel(wanted if isinstance(wanted, int) else logging.INFO)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """Remove and close the handlers added by setup_logging (used by tests)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if (handler.get_name() or "").startswith(HANDLER_PREFIX):
            root.removeHandler(handler)
            handler.close()