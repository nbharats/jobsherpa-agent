"""JobSherpa entry point: CLI, one-off runs and the daily scheduler.

    python main.py --run-now --dry-run   full pipeline once; nothing is sent or applied
    python main.py --run-now --live      real run (needs --yes if EMAIL_REDIRECT_TO is empty)
    python main.py                       scheduler: runs daily at SCHEDULE_TIME

Runs are dry unless --live is given or DRY_RUN=false is set in .env.
"""
import argparse
import logging
import re
import sys
import time

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from agent.graph import build_graph
from agent.state import make_initial_state
from agent.utils import CriticalError
from logging_setup import setup_logging

logger = logging.getLogger(__name__)


def parse_schedule_time(text: str) -> tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute); raise ValueError if it is not a valid time."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"SCHEDULE_TIME must look like 09:00, got {text!r}")
    return int(match.group(1)), int(match.group(2))


def resolve_dry_run(dry_flag: bool, live_flag: bool) -> bool:
    """--live means real actions, --dry-run means none, otherwise DRY_RUN from .env."""
    if live_flag:
        return False
    return True if dry_flag else config.DRY_RUN


def run_once(dry_run: bool) -> int:
    """Run the full pipeline once. Returns 0 on success, 1 on a critical failure."""
    logger.info("Run started (dry_run=%s)", dry_run)
    started = time.time()
    try:
        state = build_graph().invoke(make_initial_state(dry_run))
    except CriticalError as exc:
        logger.critical("Run stopped: %s", exc)
        return 1
    except Exception:
        logger.exception("Run crashed unexpectedly")
        return 1
    logger.info(
        "Run finished in %.1fs: scraped=%d new=%d applied=%d emailed=%d drip=%d errors=%d",
        time.time() - started, state["total_scraped"], state["total_new"],
        state["total_applied"], state["total_emailed"], state["drip_emails_sent"],
        len(state["errors"]))
    for error in state["errors"]:
        logger.warning("Recorded error: %s", error)
    return 0


def build_scheduler(dry_run: bool) -> BlockingScheduler:
    """Create (but do not start) a scheduler that runs the pipeline daily."""
    hour, minute = parse_schedule_time(config.SCHEDULE_TIME)
    try:
        zone = pytz.timezone(config.TIMEZONE)
    except pytz.UnknownTimeZoneError as exc:
        raise ValueError(f"Unknown TIMEZONE: {config.TIMEZONE!r}") from exc
    scheduler = BlockingScheduler(timezone=zone)
    scheduler.add_job(
        run_once, CronTrigger(hour=hour, minute=minute, timezone=zone), args=[dry_run],
        id="daily_run", max_instances=1, coalesce=True, misfire_grace_time=3600,
        replace_existing=True)
    return scheduler


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run once or start the scheduler."""
    parser = argparse.ArgumentParser(description="JobSherpa: autonomous job search agent")
    parser.add_argument("--run-now", action="store_true", help="run once now, then exit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="no applications or emails")
    mode.add_argument("--live", action="store_true", help="perform real actions")
    parser.add_argument("--yes", action="store_true",
                        help="allow live emails to real companies (EMAIL_REDIRECT_TO empty)")
    parser.add_argument("--dashboard", action="store_true",
                        help="start the web dashboard (http://127.0.0.1:5000)")
    args = parser.parse_args(argv)

    setup_logging()
    if args.dashboard:
        from dashboard.app import run_dashboard
        run_dashboard()
        return 0
    dry_run = resolve_dry_run(args.dry_run, args.live)
    if not dry_run:
        if not config.EMAIL_REDIRECT_TO and not args.yes:
            logger.error("Refusing a live run: EMAIL_REDIRECT_TO is empty, so emails would go "
                         "to real companies. Set EMAIL_REDIRECT_TO or add --yes.")
            return 2
        logger.warning("LIVE mode: real applications and emails will be sent")

    if args.run_now:
        return run_once(dry_run)
    try:
        scheduler = build_scheduler(dry_run)
    except ValueError as exc:
        logger.error("Cannot start the scheduler: %s", exc)
        return 2
    logger.info("Scheduler started: daily at %s %s (dry_run=%s). Ctrl+C to stop.",
                config.SCHEDULE_TIME, config.TIMEZONE, dry_run)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())