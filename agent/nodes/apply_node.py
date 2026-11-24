"""Apply node: Playwright auto-apply on supported portals, cold email for the rest.

A job is auto-applied only if its portal is in AUTO_APPLY_PORTALS and it has
an application URL. Everything else is routed to the email node. A job is
claimed before the browser opens, so it can never be attempted twice, and
the daily limit counts applications from earlier runs today.
"""
import logging
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

import config
from agent.emailing import fallback_cold_email
from agent.nodes.memory_node import today_str
from agent.state import AgentState
from agent.utils import CriticalError, safe_node
from database import db_manager as db
from tools import applier
from tools.applier import ApplyUncertain, ApplyUnsupported

logger = logging.getLogger(__name__)

Job = dict[str, Any]


def pause() -> None:
    """Wait a random time between applications to look human."""
    time.sleep(random.uniform(config.APPLY_DELAY_MIN_SECONDS, config.APPLY_DELAY_MAX_SECONDS))


def submit_application(job: Job) -> None:
    """The only function that opens a browser to apply (tests replace it)."""
    cover_letter = fallback_cold_email(job, config.USER_PROFILE)[1]
    applier.apply_to_job(job, config.USER_PROFILE, Path(config.RESUME_PATH),
                         cover_letter=cover_letter, headless=config.PLAYWRIGHT_HEADLESS)


def is_supported(job: Job) -> bool:
    """True if this job can be auto-applied."""
    return job.get("portal") in config.AUTO_APPLY_PORTALS and bool(job.get("apply_url"))


def applied_today() -> int:
    """Applications already made today, counting earlier runs."""
    stats = db.get_recent_stats(1)
    return stats[0]["total_applied"] if stats and stats[0]["date"] == today_str() else 0


def _attempt(job: Job, job_hash: str, errors: list[str]) -> str:
    """Apply once and record the outcome in SQLite. Returns the apply status."""
    try:
        submit_application(job)
    except CriticalError:
        raise
    except (ApplyUnsupported, ApplyUncertain) as exc:
        db.update_job_status(job_hash, "manual_required", notes=f"auto-apply: {exc}")
        return "manual_required"
    except Exception as exc:
        logger.warning("Auto-apply failed for %s: %s", job.get("url"), exc)
        db.update_job_status(job_hash, "failed", notes=f"auto-apply failed: {exc}")
        errors.append(f"apply: failed for {job.get('company', '')}: {exc}")
        return "failed"
    db.update_job_status(job_hash, "applied", notes="auto-applied with Playwright")
    return "applied"


@safe_node
def apply_jobs(state: AgentState) -> dict[str, Any]:
    """Auto-apply to supported jobs (within the daily limit); route the rest to email.

    Dry-run never opens a browser and never writes to SQLite.
    """
    dry_run = state.get("dry_run", True)
    results: list[Job] = []
    errors: list[str] = []
    attempts = applied = failed = 0
    attempted = False

    try:
        budget = max(0, config.MAX_APPLY_PER_DAY - applied_today())
        for job in state["jobs_to_apply"]:
            if not is_supported(job):
                results.append({**job, "apply_status": "route_email"})
                continue
            if attempts >= budget:
                results.append({**job, "apply_status": "limit_reached"})
                continue
            if dry_run:
                attempts += 1
                results.append({**job, "apply_status": "dry_run"})
                continue

            job_hash = db.make_job_hash(job["url"])
            if not db.claim_job(job_hash):          # claim BEFORE the browser opens
                results.append({**job, "apply_status": "already_handled"})
                continue
            attempts += 1
            if attempted:
                pause()
            attempted = True
            status = _attempt(job, job_hash, errors)
            applied += status == "applied"
            failed += status == "failed"
            results.append({**job, "apply_status": status})
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while applying: {exc}") from exc

    logger.info("Apply: %d applied, %d failed, %d attempts (dry_run=%s)",
                applied, failed, attempts, dry_run)
    return {
        "application_results": results,
        "total_applied": applied,
        "total_failed": failed,
        "errors": [*state["errors"], *errors],
    }