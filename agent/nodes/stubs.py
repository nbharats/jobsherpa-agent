"""Temporary stub nodes. Each is replaced by a real node file later.

External actions (applying, emailing) go through the three small functions
below. Nodes call them only when dry_run is False, and tests replace them
with mocks to prove that.
"""
import logging
from typing import Any

import config
from agent.state import AgentState
from agent.utils import CriticalError, safe_node

logger = logging.getLogger(__name__)


MOCK_DRIP_CONTACTS = 2
AUTO_APPLY_PORTALS = {"stub"}  


# ---------- external actions (the only places that touch the outside world) ----------

def submit_application(job: dict[str, Any]) -> None:
    """Stub for Playwright auto-apply."""
    logger.info("Submitting application: %s", job.get("url"))


def send_cold_email(job: dict[str, Any]) -> None:
    """Stub for the LLM-written cold email via Gmail SMTP."""
    logger.info("Sending cold email for: %s", job.get("url"))


def send_drip_email(contact: str) -> None:
    """Stub for one HR drip email."""
    logger.info("Sending drip email to: %s", contact)


# ---------- nodes ----------


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
def send_emails(state: AgentState) -> dict[str, Any]:
    """Cold-email jobs that could not be auto-applied (up to the daily limit)."""
    logger.info("email (stub)")
    dry_run = state.get("dry_run", True)
    targets = [
        r for r in state.get("application_results", [])
        if r.get("apply_status") == "route_email"
    ][: config.MAX_COLD_EMAILS_PER_DAY]

    records: list[dict[str, Any]] = []
    sent = 0
    for job in targets:
        if dry_run:
            records.append({"url": job["url"], "status": "dry_run"})
            continue
        try:
            send_cold_email(job)
            sent += 1
            records.append({"url": job["url"], "status": "sent"})
        except CriticalError:
            raise
        except Exception:
            logger.warning("Email failed for %s", job.get("url"), exc_info=True)
            records.append({"url": job["url"], "status": "failed"})

    return {"emails_sent": records, "total_emailed": sent}


@safe_node
def hr_drip_campaign(state: AgentState) -> dict[str, Any]:
    logger.info("hr_drip (stub)")
    if state.get("dry_run", True):
        return {"drip_emails_sent": 0}

    sent = 0
    for contact in (f"hr{i}@example.com" for i in range(MOCK_DRIP_CONTACTS)):
        try:
            send_drip_email(contact)
            sent += 1
        except CriticalError:
            raise
        except Exception:
            logger.warning("Drip email failed for %s", contact, exc_info=True)
    return {"drip_emails_sent": sent}


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