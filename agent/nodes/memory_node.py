"""Memory nodes: load past context at the start, save today's summary at the end."""
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config
from agent.state import AgentState
from agent.utils import CriticalError, safe_node
from database import db_manager as db

logger = logging.getLogger(__name__)

RECENT_RUNS = 3


def today_str() -> str:
    """Today's date (YYYY-MM-DD) in the configured timezone, else local time."""
    try:
        return datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now().date().isoformat()


def build_memory_context(recent_stats: list[dict[str, Any]],
                         status_counts: dict[str, int]) -> str:
    """Summarise previous runs and tracked jobs as plain text."""
    if recent_stats:
        lines = ["Recent runs (newest first):"]
        for s in recent_stats:
            lines.append(
                f"- {s['date']}: scraped {s['total_scraped']}, new {s['total_new']}, "
                f"applied {s['total_applied']}, emailed {s['total_emailed']}, "
                f"failed {s['total_failed']}"
            )
    else:
        lines = ["No previous runs recorded."]
    if status_counts:
        breakdown = ", ".join(f"{n} {status}" for status, n in sorted(status_counts.items()))
        lines.append(f"Jobs tracked so far: {sum(status_counts.values())} ({breakdown})")
    return "\n".join(lines)


@safe_node
def load_memory(state: AgentState) -> dict[str, Any]:
    """Prepare the database and load context from previous runs.

    Jobs left 'in_progress' by a crashed run are flagged 'manual_required'
    (we cannot know whether the action went through). In dry-run mode they
    are only reported. A database failure is critical.
    """
    dry_run = state.get("dry_run", True)
    try:
        db.init_db()
        counts = db.count_jobs_by_status()
        interrupted = counts.get("in_progress", 0)
        if interrupted and not dry_run:
            db.recover_interrupted_jobs()
            counts = db.count_jobs_by_status()
        recent = db.get_recent_stats(RECENT_RUNS)
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while loading memory: {exc}") from exc

    context = build_memory_context(recent, counts)
    if interrupted:
        outcome = "found (left unchanged in dry-run)" if dry_run else "flagged as manual_required"
        context += f"\n{interrupted} interrupted job(s) {outcome}: verify them manually"
    logger.info("Memory loaded: %s", context.replace("\n", " | "))
    return {"memory_context": context}


@safe_node
def save_memory(state: AgentState) -> dict[str, Any]:
    """Add today's numbers to daily_stats (real runs only).

    Failing to save statistics is recoverable: the real record of every
    action lives in the applications table.
    """
    if state.get("dry_run", True):
        logger.info("Dry run: daily stats not saved")
        return {"memory_saved": False}
    try:
        db.add_daily_stats(
            today_str(),
            scraped=state["total_scraped"],
            new=state["total_new"],
            applied=state["total_applied"],
            emailed=state["total_emailed"] + state["drip_emails_sent"],
            failed=state["total_failed"],
            run_time_seconds=round(time.time() - state["start_time"], 1),
        )
    except sqlite3.Error as exc:
        logger.exception("Could not save daily stats")
        return {"memory_saved": False,
                "errors": [*state["errors"], f"save_memory: {exc}"]}
    return {"memory_saved": True}