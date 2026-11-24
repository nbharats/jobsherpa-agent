"""Tests for the daily report: HTML builder, saving, emailing and error handling."""
import smtplib
import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import config
from agent.nodes import report_node
from agent.report import format_duration, render_report
from agent.state import make_initial_state
from database import db_manager as db
from tools import email_sender
from tools.email_sender import EmailSendError

NOW = datetime(2026, 11, 24, 21, 0)

TREND = [
    {"date": "2026-11-24", "total_scraped": 138, "total_applied": 2,
     "total_emailed": 3, "total_failed": 1},
    {"date": "2026-11-23", "total_scraped": 120, "total_applied": 0,
     "total_emailed": 1, "total_failed": 0},
]


def sample_state(dry_run: bool = False) -> dict:
    state = make_initial_state(dry_run)
    state.update(total_scraped=138, total_new=4, total_applied=2, total_failed=1,
                 total_emailed=1, drip_emails_sent=2)
    state["start_time"] = NOW.timestamp() - 75
    state["scored_jobs"] = [
        {"title": "<script>alert(1)</script> Python Dev", "company": "Acme & Co",
         "portal": "remoteok", "url": "https://remoteok.com/remote-jobs/1",
         "relevance_score": 9.0, "score_source": "gemini", "score_reason": "Strong match"},
        {"title": "Flask Dev", "company": "Beta", "portal": "demo",
         "url": "javascript:alert(1)", "relevance_score": 4.0,
         "score_source": "fallback", "score_reason": "keyword fallback", "sample": True},
    ]
    state["jobs_to_apply"] = state["scored_jobs"][:1]
    state["application_results"] = [
        {"title": "Flask Dev", "portal": "demo", "url": "https://demo.jobsherpa.example/jobs/1",
         "apply_status": "applied"}]
    state["emails_sent"] = [
        {"company": "Acme", "to": "hr@acme.com", "subject": "Application for Python Dev",
         "status": "sent", "delivered_to": "me@gmail.com"}]
    state["errors"] = ["score: Gemini unavailable"]
    return state


def test_report_content_and_escaping():
    subject, page, text = render_report(sample_state(), TREND, now=NOW)
    assert "2026-11-24" in subject and "2 applied" in subject and "3 emailed" in subject
    assert "<script>" not in page and "&lt;script&gt;" in page      # web text is escaped
    assert "javascript:" not in page                                # unsafe links dropped
    assert 'href="https://remoteok.com/remote-jobs/1"' in page
    assert "Acme &amp; Co" in page and "(sample)" in page
    assert "138" in page and "1 min 15 s" in page
    assert "score: Gemini unavailable" in page
    assert "2026-11-23" in page and "2026-11-24" in page            # trend rows
    assert "delivered to test address me@gmail.com" in page
    assert 'href="https://remoteok.com"' in page                    # source credit
    assert "Remotive" not in page                                   # only sources actually used


def test_mode_badges_and_notes():
    _, live, _ = render_report(sample_state(False), [], now=NOW)
    _, dry, _ = render_report(sample_state(True), [], now=NOW, test_redirect=True)
    assert "LIVE" in live and "DRY RUN" not in live and "Last 7 days" not in live
    assert "DRY RUN" in dry and "no applications or emails were sent" in dry
    assert "redirected to a test address" in dry
    assert "DRY RUN" in render_report(sample_state(True), [], now=NOW)[0]


def test_text_version():
    _, _, text = render_report(sample_state(), TREND, now=NOW)
    assert "Jobs scraped: 138" in text and "Applications: 2" in text
    assert "- score: Gemini unavailable" in text


def test_format_duration():
    assert format_duration(5.4) == "5.4 s"
    assert format_duration(75) == "1 min 15 s"
    assert format_duration(3725) == "1 h 2 min"
    assert format_duration(-3) == "0.0 s"


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail = False

    def __init__(self, host, port, context=None, timeout=None):
        self.login_args, self.sent = None, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent.append(message)


def test_send_html():
    settings = dict(GMAIL_ADDRESS="me@gmail.com", GMAIL_APP_PASSWORD="abcd efgh",
                    SMTP_HOST="smtp.gmail.com", SMTP_PORT=465)
    with patch.multiple(config, **settings), \
            patch.object(email_sender.smtplib, "SMTP_SSL", FakeSMTP):
        FakeSMTP.instances.clear()
        email_sender.send_html("me@gmail.com", "Subj\nline", "<b>hi</b>", "hi")
        smtp = FakeSMTP.instances[0]
        message = smtp.sent[0]
        assert smtp.login_args == ("me@gmail.com", "abcdefgh")
        assert message["Subject"] == "Subj line" and message["To"] == "me@gmail.com"
        assert "<b>hi</b>" in message.get_body(("html",)).get_content()
        assert "hi" in message.get_body(("plain",)).get_content()
        assert list(message.iter_attachments()) == []                # no resume attached

        FakeSMTP.fail = True
        try:
            email_sender.send_html("me@gmail.com", "s", "<b>x</b>", "x")
        except EmailSendError:
            pass
        else:
            raise AssertionError("an SMTP failure was not reported")
        finally:
            FakeSMTP.fail = False
    with patch.multiple(config, GMAIL_ADDRESS="", GMAIL_APP_PASSWORD=""):
        try:
            email_sender.send_html("me@gmail.com", "s", "<b>x</b>", "x")
        except EmailSendError:
            return
        raise AssertionError("missing credentials were accepted")


@contextmanager
def report_env(report_email: str = "me@gmail.com", redirect: str = ""):
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        reports = Path(tmp) / "reports"
        stack.enter_context(patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"))
        stack.enter_context(patch.multiple(config, REPORTS_DIR=reports,
                                           REPORT_EMAIL=report_email, EMAIL_REDIRECT_TO=redirect))
        send = stack.enter_context(patch.object(report_node, "send_report_email"))
        db.init_db()
        yield send, reports


def test_node_dry_run_saves_but_does_not_send():
    with report_env() as (send, reports):
        update = report_node.build_report(sample_state(dry_run=True))
        assert send.call_count == 0 and "errors" not in update
        assert "DRY RUN" in update["report_html"]
        assert (reports / "latest.html").is_file()
        assert len(list(reports.glob("report_*_dry.html"))) == 1


def test_node_live_emails_the_report():
    with report_env() as (send, reports):
        update = report_node.build_report(sample_state(dry_run=False))
        assert send.call_count == 1 and "errors" not in update
        recipient, subject, html, text = send.call_args.args
        assert recipient == "me@gmail.com" and "2 applied" in subject
        assert "JobSherpa daily report" in html and "Jobs scraped" in text
        assert len(list(reports.glob("report_*_live.html"))) == 1


def test_node_live_without_report_email():
    with report_env(report_email="") as (send, reports):
        update = report_node.build_report(sample_state(dry_run=False))
        assert send.call_count == 0
        assert any("REPORT_EMAIL" in e for e in update["errors"])
        assert update["report_html"]


def test_node_send_failure_is_recoverable():
    with report_env() as (send, reports):
        send.side_effect = EmailSendError("boom")
        update = report_node.build_report(sample_state(dry_run=False))
        assert any("boom" in e for e in update["errors"]) and update["report_html"]
        assert "score: Gemini unavailable" in update["errors"][0]    # earlier errors kept


def test_node_save_failure_is_recoverable():
    with report_env() as (send, reports):
        with patch.object(report_node, "save_report", side_effect=OSError("disk full")):
            update = report_node.build_report(sample_state(dry_run=False))
        assert any("could not save" in e for e in update["errors"])
        assert send.call_count == 1                                  # still emailed


def test_node_history_failure_is_recoverable():
    with report_env() as (send, reports):
        with patch.object(db, "get_recent_stats", side_effect=sqlite3.OperationalError("locked")):
            update = report_node.build_report(sample_state(dry_run=True))
        assert any("history" in e for e in update["errors"]) and update["report_html"]
        assert "Last 7 days" not in update["report_html"]


def test_trend_comes_from_the_database():
    with report_env():
        db.add_daily_stats("2026-11-22", scraped=10, applied=1, emailed=1)
        update = report_node.build_report(sample_state(dry_run=True))
        assert "2026-11-22" in update["report_html"]


TESTS = [
    test_report_content_and_escaping,
    test_mode_badges_and_notes,
    test_text_version,
    test_format_duration,
    test_send_html,
    test_node_dry_run_saves_but_does_not_send,
    test_node_live_emails_the_report,
    test_node_live_without_report_email,
    test_node_send_failure_is_recoverable,
    test_node_save_failure_is_recoverable,
    test_node_history_failure_is_recoverable,
    test_trend_comes_from_the_database,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All report checks passed.")


if __name__ == "__main__":
    run()