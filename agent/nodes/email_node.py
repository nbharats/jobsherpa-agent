"""Email nodes: cold emails for jobs that cannot be auto-applied, plus the HR drip.

Both nodes check dry_run before any external action. In dry-run, cold emails
are still written (so you can review them) but nothing is sent or saved.
"""
import logging
import random
import sqlite3
import time
from typing import Any

import config
from agent.emailing import (EMAIL_BATCH_SIZE, build_email_prompt, drip_email,
                            fallback_cold_email, parse_emails, signature)
from agent.llm import LLMError, generate_text
from agent.nodes.memory_node import today_str
from agent.state import AgentState
from agent.utils import CriticalError, safe_node
from database import db_manager as db
from tools import email_sender
from tools.email_finder import find_hr_emails, is_valid_email
from tools.hr_csv import read_hr_csv

logger = logging.getLogger(__name__)

Job = dict[str, Any]


def pause() -> None:
    """Wait a random time between sends to respect Gmail's rate limits."""
    time.sleep(random.uniform(config.EMAIL_DELAY_MIN_SECONDS, config.EMAIL_DELAY_MAX_SECONDS))


def send_cold_email(to: str, subject: str, body: str) -> str:
    """The only function that sends a job-related email (tests replace it)."""
    return email_sender.send_email(to, subject, body)


def send_drip_email(to: str, subject: str, body: str) -> str:
    """The only function that sends a drip email (tests replace it)."""
    return email_sender.send_email(to, subject, body)


def daily_budget_left() -> int:
    """Emails still allowed today, counting earlier runs (DAILY_EMAIL_LIMIT)."""
    stats = db.get_recent_stats(1)
    used = stats[0]["total_emailed"] if stats and stats[0]["date"] == today_str() else 0
    return max(0, config.DAILY_EMAIL_LIMIT - used)


def _record(job: Job, to: str, status: str, subject: str = "", body: str = "",
            written_by: str = "", delivered_to: str = "") -> dict[str, Any]:
    return {"url": job.get("url", ""), "company": job.get("company", ""),
            "title": job.get("title", ""), "to": to, "subject": subject, "body": body,
            "written_by": written_by, "delivered_to": delivered_to, "status": status}


def _pick_targets(candidates: list[Job], budget: int,
                  dry_run: bool) -> tuple[list[tuple[Job, str]], list[dict[str, Any]]]:
    """Find an HR address for each job until `budget` jobs have one."""
    targets: list[tuple[Job, str]] = []
    records: list[dict[str, Any]] = []
    chosen: set[str] = set()
    for job in candidates:
        if len(targets) >= budget:
            break
        job_hash = db.make_job_hash(job["url"])
        provided = (job.get("hr_email") or "").strip().lower()
        if provided and is_valid_email(provided):
            contacts = [{"email": provided, "domain": provided.split("@")[1],
                         "source": "provided", "verified": True}]
        else:
            contacts = find_hr_emails(job.get("company", ""), save=not dry_run)
        if not contacts:
            records.append(_record(job, "", "no_contact"))
            if not dry_run:
                db.update_job_status(job_hash, "manual_required", notes="no HR email found")
            continue
        to = contacts[0]["email"]
        if to in chosen or db.hr_email_already_contacted(to):
            records.append(_record(job, to, "already_contacted"))
            if not dry_run:
                db.update_job_status(job_hash, "manual_required",
                                     notes="HR already contacted about another role")
            continue
        chosen.add(to)
        targets.append((job, to))
    return targets, records


def write_emails(jobs: list[Job], profile: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (subject, body, written_by) for every job; one Gemini call per batch."""
    drafts: list[tuple[str, str, str]] = []
    for start in range(0, len(jobs), EMAIL_BATCH_SIZE):
        batch = jobs[start:start + EMAIL_BATCH_SIZE]
        generated: dict[str, tuple[str, str]] = {}
        try:
            reply = generate_text(build_email_prompt(batch, profile),
                                  temperature=0.6, json_output=True)
            generated = parse_emails(reply, {f"j{i}" for i in range(len(batch))})
        except (LLMError, ValueError) as exc:
            logger.warning("Email writing fell back to the template: %s", exc)
        for i, job in enumerate(batch):
            if f"j{i}" in generated:
                subject, body = generated[f"j{i}"]
                source = "gemini"
            else:
                subject, body = fallback_cold_email(job, profile)
                source = "fallback"
            drafts.append((subject, f"{body}\n\n{signature(profile)}", source))
    return drafts


@safe_node
def send_emails(state: AgentState) -> dict[str, Any]:
    """Cold-email jobs that could not be auto-applied, within the daily limits."""
    dry_run = state.get("dry_run", True)
    candidates = [r for r in state.get("application_results", [])
                  if r.get("apply_status") == "route_email"]
    if not candidates:
        return {"emails_sent": [], "total_emailed": 0}

    try:
        budget = min(config.MAX_COLD_EMAILS_PER_DAY, daily_budget_left())
        targets, records = _pick_targets(candidates, budget, dry_run)
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while preparing emails: {exc}") from exc
    if not targets:
        return {"emails_sent": records, "total_emailed": 0}

    drafts = write_emails([job for job, _ in targets], config.USER_PROFILE)
    problem = None if dry_run else email_sender.sender_problem()
    errors: list[str] = []
    if problem:
        errors.append(f"email: {problem}; no emails were sent")

    sent = 0
    attempted = False
    try:
        for (job, to), (subject, body, source) in zip(targets, drafts):
            record = _record(job, to, "dry_run", subject, body, source)
            records.append(record)
            if dry_run:
                continue
            if problem:
                record["status"] = "skipped"
                continue
            job_hash = db.make_job_hash(job["url"])
            if not db.claim_job(job_hash):          # claim BEFORE sending: never twice
                record["status"] = "already_handled"
                continue
            if attempted:
                pause()
            attempted = True
            try:
                record["delivered_to"] = send_cold_email(to, subject, body)
            except Exception as exc:
                logger.warning("Email to %s failed: %s", to, exc)
                db.update_job_status(job_hash, "failed", hr_email=to, notes=f"email failed: {exc}")
                db.log_email("cold", to, "", job.get("company", ""), subject, body,
                             "failed", job_hash)
                record["status"] = "failed"
                errors.append(f"email: failed for {job.get('company', '')}: {exc}")
                continue
            db.update_job_status(job_hash, "email_sent", hr_email=to,
                                 notes="test redirect" if config.EMAIL_REDIRECT_TO else None)
            db.log_email("cold", to, record["delivered_to"], job.get("company", ""),
                         subject, body, "sent", job_hash)
            record["status"] = "sent"
            sent += 1
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while sending emails: {exc}") from exc

    logger.info("Cold emails: %d sent, %d records (dry_run=%s)", sent, len(records), dry_run)
    return {"emails_sent": records, "total_emailed": sent,
            "errors": [*state["errors"], *errors]}


def _drip_batch(rows: list[dict[str, str]], budget: int, dry_run: bool) -> list[dict[str, str]]:
    """Pick the next pending contacts. Dry-run only reads the database."""
    if budget <= 0:
        return []
    if not dry_run:
        for row in rows:
            db.add_drip_contact(row["email"], row["name"], row["company"], row["designation"])
        return db.get_next_drip_batch(budget)
    batch: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if row["email"] in seen:
            continue
        seen.add(row["email"])
        existing = db.get_drip_contact(row["email"])
        if existing and existing["status"] != "pending":
            continue
        batch.append(row)
        if len(batch) >= budget:
            break
    return batch


@safe_node
def hr_drip_campaign(state: AgentState) -> dict[str, Any]:
    """Email the next few contacts from the HR CSV; each contact is emailed once."""
    dry_run = state.get("dry_run", True)
    rows, skipped = read_hr_csv(config.HR_CSV_PATH)
    if skipped:
        logger.warning("HR CSV: %d row(s) skipped because of an invalid email", skipped)
    if not rows:
        logger.info("HR drip: no contacts found in %s", config.HR_CSV_PATH)
        return {"drip_emails_sent": 0}

    try:
        budget = min(config.DRIP_BATCH_SIZE,
                     max(0, daily_budget_left() - state["total_emailed"]))
        batch = _drip_batch(rows, budget, dry_run)
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while preparing the drip: {exc}") from exc
    if not batch:
        return {"drip_emails_sent": 0}

    profile = config.USER_PROFILE
    problem = None if dry_run else email_sender.sender_problem()
    sent = 0
    attempted = False
    try:
        for contact in batch:
            subject, body = drip_email(contact, profile)
            body = f"{body}\n\n{signature(profile)}"
            if dry_run:
                logger.info("Dry run: would send a drip email to %s", contact["email"])
                continue
            if problem:
                continue
            if attempted:
                pause()
            attempted = True
            try:
                delivered = send_drip_email(contact["email"], subject, body)
            except Exception as exc:
                logger.warning("Drip email to %s failed: %s", contact["email"], exc)
                db.mark_drip_result(contact["email"], "failed")     # never retried
                db.log_email("drip", contact["email"], "", contact["company"], subject, body, "failed")
                continue
            db.mark_drip_result(contact["email"], "sent")
            db.log_email("drip", contact["email"], delivered, contact["company"], subject, body, "sent")
            sent += 1
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error during the drip: {exc}") from exc

    errors = [f"hr_drip: {problem}; no drip emails were sent"] if problem else []
    return {"drip_emails_sent": sent, "errors": [*state["errors"], *errors]}

