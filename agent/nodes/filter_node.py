"""Filter node: rule-based filtering plus hash-based duplicate detection."""
import logging
import sqlite3
from collections import Counter
from typing import Any

from agent.filters import evaluate_job
from agent.state import AgentState
from agent.utils import CriticalError, safe_node
from database import db_manager as db

logger = logging.getLogger(__name__)


@safe_node
def filter_jobs(state: AgentState) -> dict[str, Any]:
    """Keep only new jobs that pass every rule.

    Real run: each surviving job is inserted into SQLite, which is what
    makes it 'seen' tomorrow. Dry run: the database is only read, so a
    test run never uses up jobs that a real run should process.
    A database failure is critical: without it we cannot prevent duplicates.
    """
    dry_run = state.get("dry_run", True)
    new_jobs: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    seen: set[str] = set()
    duplicates = 0

    try:
        for job in state["raw_jobs"]:
            keep, reason = evaluate_job(job)
            if not keep:
                rejected[reason.split(":")[0]] += 1
                continue

            try:
                job_hash = db.make_job_hash(job.get("url"))
            except ValueError:
                rejected["invalid_url"] += 1
                continue

            if job_hash in seen:          
                duplicates += 1
                continue
            seen.add(job_hash)

            is_new = (not db.job_exists(job_hash)) if dry_run else db.insert_job(job)
            if is_new:
                new_jobs.append(job)
            else:
                duplicates += 1
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while filtering: {exc}") from exc

    logger.info(
        "Filter: %d raw -> %d new (%d duplicates, rejected: %s)",
        len(state["raw_jobs"]), len(new_jobs), duplicates, dict(rejected) or "none",
    )
    return {"new_jobs": new_jobs, "total_new": len(new_jobs)}