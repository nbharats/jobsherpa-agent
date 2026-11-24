"""Report node: builds the daily report, saves it to disk and emails it."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from agent.report import render_report
from agent.state import AgentState
from agent.utils import safe_node
from database import db_manager as db
from tools import email_sender

logger = logging.getLogger(__name__)


def send_report_email(recipient: str, subject: str, html: str, text: str) -> None:
    """The only function that emails the report (tests replace it)."""
    email_sender.send_html(recipient, subject, html, text)


def save_report(html: str, dry_run: bool) -> Path:
    """Write a dated copy plus latest.html (the dashboard shows latest.html)."""
    directory = Path(config.REPORTS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"report_{stamp}_{'dry' if dry_run else 'live'}.html"
    path.write_text(html, encoding="utf-8")
    (directory / "latest.html").write_text(html, encoding="utf-8")
    return path


@safe_node
def build_report(state: AgentState) -> dict[str, Any]:
    """Build and save the report; email it on live runs.

    Every failure here (history, saving, sending) is recoverable: it is
    recorded in the errors list and the run still finishes.
    """
    dry_run = state.get("dry_run", True)
    errors: list[str] = []

    try:
        trend = db.get_recent_stats(7)
    except sqlite3.Error as exc:
        trend = []
        errors.append(f"report: could not read history: {exc}")

    subject, html, text = render_report(
        state, trend, test_redirect=bool(config.EMAIL_REDIRECT_TO))

    try:
        logger.info("Report saved: %s", save_report(html, dry_run))
    except OSError as exc:
        errors.append(f"report: could not save the file: {exc}")

    if dry_run:
        logger.info("Dry run: report saved but not emailed")
    elif not config.REPORT_EMAIL:
        errors.append("report: REPORT_EMAIL is not set; the report was not emailed")
    else:
        try:
            send_report_email(config.REPORT_EMAIL, subject, html, text)
        except Exception as exc:
            errors.append(f"report: could not email the report: {exc}")

    for message in errors:
        logger.warning(message)
    update: dict[str, Any] = {"report_html": html}
    if errors:
        update["errors"] = [*state["errors"], *errors]
    return update