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

MOCK_JOB_COUNT = 3        
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
def load_memory(state: AgentState) -> dict[str, Any]:
    logger.info("load_memory (stub)")
    return {"memory_context": "stub: no previous sessions"}


@safe_node
def scrape_jobs(state: AgentState) -> dict[str, Any]:
    logger.info("scrape (stub)")
    jobs = [
        {
            "url": f"https://example.com/jobs/{i}",
            "title": f"Stub Job {i}",
            "company": "Acme",
            "portal": "stub" if i % 2 == 0 else "remoteok",
        }
        for i in range(MOCK_JOB_COUNT)
    ]
    return {"raw_jobs": jobs, "total_scraped": len(jobs)}


@safe_node
def filter_jobs(state: AgentState) -> dict[str, Any]:
    logger.info("filter (stub)")
    new = state["raw_jobs"]
    return {"new_jobs": new, "total_new": len(new)}


@safe_node
def score_jobs(state: AgentState) -> dict[str, Any]:
    """Score every job, then shortlist the best TOP_N_JOBS above the threshold.

    The daily application limit is NOT applied here; apply_jobs enforces it.
    """
    logger.info("score (stub)")
    scored = [
        {**job, "relevance_score": round(10 - 0.5 * i, 1)}
        for i, job in enumerate(state["new_jobs"])
    ]
    scored.sort(key=lambda j: j["relevance_score"], reverse=True)
    shortlist = [j for j in scored if j["relevance_score"] >= config.JOB_SCORE_THRESHOLD]
    return {"scored_jobs": scored, "jobs_to_apply": shortlist[: config.TOP_N_JOBS]}


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
def save_memory(state: AgentState) -> dict[str, Any]:
    logger.info("save_memory (stub)")
    return {"memory_saved": True}


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