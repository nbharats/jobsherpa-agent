"""Temporary stub nodes (apply and report). Each is replaced by a real node later.

External actions go through submit_application. Nodes call it only when
dry_run is False, and tests replace it with a mock to prove that.
"""
import logging
from typing import Any

import config
from agent.state import AgentState
from agent.utils import CriticalError, safe_node

logger = logging.getLogger(__name__)

AUTO_APPLY_PORTALS = {"stub"}  


def submit_application(job: dict[str, Any]) -> None:
    """Stub for Playwright auto-apply."""
    logger.info("Submitting application: %s", job.get("url"))


@safe_node
def apply_jobs(state: AgentState) -> dict[str, Any]:
    """Attempt auto-apply (up to MAX_APPLY_PER_DAY); route other portals to email."""
    logger.info("apply (stub)")
    dry_run = state.get("dry_run", True)
    limit = config.MAX_APPLY_PER_DAY
    attempts = applied = failed = 0
    results: list[dict[str, Any]] = []

    for job in state["jobs_to_apply"]:
        if job.get("portal") not in AUTO_APPLY_PORTALS:
            results.append({**job, "apply_status": "route_email"})
            continue
        if attempts >= limit:
            results.append({**job, "apply_status": "limit_reached"})
            continue
        attempts += 1
        if dry_run:
            results.append({**job, "apply_status": "dry_run"})
            continue
        try:
            submit_application(job)
            applied += 1
            results.append({**job, "apply_status": "applied"})
        except CriticalError:
            raise
        except Exception:
            logger.warning("Application failed for %s", job.get("url"), exc_info=True)
            failed += 1
            results.append({**job, "apply_status": "failed"})

    return {
        "application_results": results,
        "total_applied": applied,
        "total_failed": failed,
    }


@safe_node
def build_report(state: AgentState) -> dict[str, Any]:
    logger.info("report (stub)")
    html = (
        "<h1>JobSherpa stub report</h1>"
        f"<p>Scraped: {state['total_scraped']}, new: {state['total_new']}, "
        f"applied: {state['total_applied']}, emailed: {state['total_emailed']}, "
        f"errors: {len(state['errors'])}, dry-run: {state['dry_run']}</p>"
    )
    return {"report_html": html}