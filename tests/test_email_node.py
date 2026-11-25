"""Tests for the email nodes: dry-run safety, limits, dedup, failures and the drip."""
import json
import re
import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from agent.llm import LLMError
from agent.nodes import email_node
from agent.state import make_initial_state
from agent.utils import CriticalError
from database import db_manager as db
from scrapers.common import make_job
from tools import email_sender
from tools.email_sender import EmailSendError

PROFILE = {
    "personal_info": {"name": "Test User", "phone": "+91-9999999999"},
    "education": [{"degree": "B.Tech CSE", "graduation_year": 2025}],
    "skills": {"programming_languages": ["Python"], "backend": ["Flask"]},
    "projects": [{"name": "Proj", "description": "A web app", "technologies": ["Flask"]}],
    "target_roles": ["Python Developer"],
}
GOOD_BODY = ("Hello team, I would like to be considered for this role. I work with Python "
             "and Flask and have attached my resume for your review. Is it still open?")
DRIP_ROWS = ["Asha,asha@acme.com,Acme,HR", "Ben,ben@beta.com,Beta,Recruiter",
             "Bad,not-an-email,X,Y", "Cara,cara@gamma.com,Gamma,HR"]


def fake_emails(prompt: str, **kwargs) -> str:
    ids = list(dict.fromkeys(re.findall(r'"id": "(j\d+)"', prompt)))
    return json.dumps({"emails": [{"id": i, "subject": f"Application {i}", "body": GOOD_BODY}
                                  for i in ids]})


def default_finder(company: str, **kwargs):
    address = f"hr@{company.lower().replace(' ', '')}.com"
    return [{"email": address, "domain": address.split("@")[1], "source": "pattern", "verified": False}]


@contextmanager
def email_env(limit=3, daily=20, problem=None, drip_batch=2, llm=fake_emails, finder=None):
    """Temporary database, fixed settings and mocked outside world."""
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        csv_path = Path(tmp) / "hr.csv"
        csv_path.write_text("name,email,company,designation\n" + "\n".join(DRIP_ROWS) + "\n",
                            encoding="utf-8")
        stack.enter_context(patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"))
        stack.enter_context(patch.multiple(
            config, USER_PROFILE=PROFILE, MAX_COLD_EMAILS_PER_DAY=limit,
            DAILY_EMAIL_LIMIT=daily, DRIP_BATCH_SIZE=drip_batch, HR_CSV_PATH=csv_path,
            EMAIL_REDIRECT_TO=""))
        stack.enter_context(patch.object(email_node, "today_str", lambda: "2026-11-23"))
        stack.enter_context(patch.object(email_node, "generate_text", llm))
        stack.enter_context(patch.object(email_sender, "sender_problem", return_value=problem))
        mocks = SimpleNamespace(
            finder=stack.enter_context(patch.object(
                email_node, "find_hr_emails", side_effect=finder or default_finder)),
            cold=stack.enter_context(patch.object(
                email_node, "send_cold_email", return_value="delivered@test.com")),
            drip=stack.enter_context(patch.object(
                email_node, "send_drip_email", return_value="delivered@test.com")),
            pause=stack.enter_context(patch.object(email_node, "pause")),
        )
        db.init_db()
        yield mocks


def job(n: int, company: str | None = None) -> dict:
    return make_job(url=f"https://x.com/jobs/{n}", title=f"Python Developer {n}",
                    company=company or f"Company{n}", portal="remoteok",
                    description="Flask APIs and SQL")


def cold_state(jobs: list[dict], dry_run: bool, insert: bool = True):
    """A state after the apply node; real runs have the jobs in SQLite as 'new'."""
    if insert and not dry_run:
        for item in jobs:
            db.insert_job(item)
    state = make_initial_state(dry_run)
    state["application_results"] = [{**item, "apply_status": "route_email"} for item in jobs]
    return state


def status_of(item: dict) -> str:
    return db.get_job(db.make_job_hash(item["url"]))["status"]


def test_dry_run_writes_and_sends_nothing():
    jobs = [job(1), job(2)]
    with email_env() as m:
        update = email_node.send_emails(cold_state(jobs, dry_run=True))
        assert m.cold.call_count == 0 and update["total_emailed"] == 0
        assert m.finder.call_args.kwargs["save"] is False
        records = update["emails_sent"]
        assert [r["status"] for r in records] == ["dry_run", "dry_run"]
        assert all(r["subject"] and r["written_by"] == "gemini" for r in records)
        assert all("Best regards" in r["body"] for r in records)
        assert db.get_jobs() == []


def test_real_run_respects_limit():
    jobs = [job(n) for n in range(4)]
    with email_env(limit=2) as m:
        update = email_node.send_emails(cold_state(jobs, dry_run=False))
        assert m.cold.call_count == 2 and update["total_emailed"] == 2
        assert m.pause.call_count == 1                          # between the two sends
        assert [status_of(j) for j in jobs] == ["email_sent", "email_sent", "new", "new"]
        assert db.get_job(db.make_job_hash(jobs[0]["url"]))["hr_email"] == "hr@company0.com"
        assert [r["status"] for r in update["emails_sent"]] == ["sent", "sent"]


def test_no_contact_is_flagged_not_counted():
    def finder(company, **kwargs):
        return [] if company == "Ghost" else default_finder(company)

    jobs = [job(1, "Ghost"), job(2), job(3)]
    with email_env(limit=1, finder=finder) as m:
        update = email_node.send_emails(cold_state(jobs, dry_run=False))
        assert m.cold.call_count == 1
        assert [status_of(j) for j in jobs] == ["manual_required", "email_sent", "new"]
        assert update["emails_sent"][0]["status"] == "no_contact"


def test_same_hr_never_contacted_twice():
    first = [job(1, "Acme"), job(2, "Acme")]
    with email_env() as m:
        update = email_node.send_emails(cold_state(first, dry_run=False))
        assert m.cold.call_count == 1                           # second job, same HR
        assert [status_of(j) for j in first] == ["email_sent", "manual_required"]
        assert "already_contacted" in [r["status"] for r in update["emails_sent"]]

        later = [job(3, "Acme")]                                # a later run, same company
        email_node.send_emails(cold_state(later, dry_run=False))
        assert m.cold.call_count == 1
        assert status_of(later[0]) == "manual_required"


def test_send_failure_is_isolated():
    jobs = [job(1), job(2)]
    with email_env() as m:
        m.cold.side_effect = [EmailSendError("boom"), "delivered@test.com"]
        update = email_node.send_emails(cold_state(jobs, dry_run=False))
        assert [status_of(j) for j in jobs] == ["failed", "email_sent"]
        assert update["total_emailed"] == 1
        assert any("boom" in e for e in update["errors"])
        assert m.pause.call_count == 1


def test_missing_credentials_send_nothing():
    jobs = [job(1), job(2)]
    with email_env(problem="Gmail credentials are missing in .env") as m:
        update = email_node.send_emails(cold_state(jobs, dry_run=False))
        assert m.cold.call_count == 0 and update["total_emailed"] == 0
        assert [status_of(j) for j in jobs] == ["new", "new"]
        assert [r["status"] for r in update["emails_sent"]] == ["skipped", "skipped"]
        assert any("credentials" in e for e in update["errors"])


def test_llm_failure_uses_template():
    def failing(prompt, **kwargs):
        raise LLMError("quota")

    for llm in (failing, lambda prompt, **kw: "total garbage"):
        with email_env(llm=llm) as m:
            update = email_node.send_emails(cold_state([job(1)], dry_run=False))
            assert m.cold.call_count == 1
            assert update["emails_sent"][0]["written_by"] == "fallback"
            assert update["emails_sent"][0]["body"].startswith("Hello Company1 hiring team,")


def test_daily_budget_includes_earlier_runs():
    jobs = [job(n) for n in range(3)]
    with email_env(limit=3, daily=20) as m:
        db.add_daily_stats("2026-11-23", emailed=19)
        db.add_daily_stats("2026-11-22", emailed=50)            # yesterday does not count
        update = email_node.send_emails(cold_state(jobs, dry_run=False))
        assert m.cold.call_count == 1 and update["total_emailed"] == 1


def test_database_failure_is_critical():
    with email_env():
        with patch.object(db, "get_recent_stats", side_effect=sqlite3.OperationalError("locked")):
            try:
                email_node.send_emails(cold_state([job(1)], dry_run=True))
            except CriticalError:
                return
            raise AssertionError("a database failure did not stop the run")


def test_drip_dry_run_sends_nothing():
    with email_env() as m:
        update = email_node.hr_drip_campaign(make_initial_state(dry_run=True))
        assert update["drip_emails_sent"] == 0 and m.drip.call_count == 0
        assert db.get_next_drip_batch(10) == []                 # nothing imported


def test_drip_real_run_sends_each_contact_once():
    with email_env(drip_batch=2) as m:
        state = make_initial_state(dry_run=False)
        assert email_node.hr_drip_campaign(state)["drip_emails_sent"] == 2
        assert db.get_drip_contact("asha@acme.com")["status"] == "sent"
        assert db.get_drip_contact("ben@beta.com")["status"] == "sent"
        assert db.get_drip_contact("cara@gamma.com")["status"] == "pending"
        assert email_node.hr_drip_campaign(state)["drip_emails_sent"] == 1     # only Cara left
        assert email_node.hr_drip_campaign(state)["drip_emails_sent"] == 0     # nobody repeats
        assert m.drip.call_count == 3 and m.pause.call_count == 1


def test_drip_respects_daily_budget():
    with email_env(daily=3, drip_batch=2) as m:
        state = make_initial_state(dry_run=False)
        state["total_emailed"] = 2                              # cold emails already sent today
        assert email_node.hr_drip_campaign(state)["drip_emails_sent"] == 1
        assert m.drip.call_count == 1


def test_drip_dry_run_skips_handled_contacts():
    with email_env(drip_batch=2) as m:
        email_node.hr_drip_campaign(make_initial_state(dry_run=False))     # Asha and Ben sent
        with patch.object(email_node, "drip_email", wraps=email_node.drip_email) as spy:
            email_node.hr_drip_campaign(make_initial_state(dry_run=True))
        assert [c.args[0]["email"] for c in spy.call_args_list] == ["cara@gamma.com"]
        assert m.drip.call_count == 2                           # the dry run sent nothing


def test_drip_failure_and_missing_credentials():
    with email_env(drip_batch=2) as m:
        m.drip.side_effect = [EmailSendError("boom"), "delivered@test.com"]
        update = email_node.hr_drip_campaign(make_initial_state(dry_run=False))
        assert update["drip_emails_sent"] == 1
        assert db.get_drip_contact("asha@acme.com")["status"] == "failed"      # never retried
        assert db.get_drip_contact("ben@beta.com")["status"] == "sent"

    with email_env(problem="Gmail credentials are missing in .env") as m:
        update = email_node.hr_drip_campaign(make_initial_state(dry_run=False))
        assert update["drip_emails_sent"] == 0 and m.drip.call_count == 0
        assert any("credentials" in e for e in update["errors"])
        assert db.get_drip_contact("asha@acme.com")["status"] == "pending"

def test_provided_hr_email_skips_lookup():
    sample = {**job(1), "hr_email": "Careers@Nimbus-Demo.example.org"}
    with email_env() as m:
        update = email_node.send_emails(cold_state([sample], dry_run=False))
        assert m.finder.call_count == 0
        assert m.cold.call_args.args[0] == "careers@nimbus-demo.example.org"
        assert update["total_emailed"] == 1

def test_real_sends_are_logged():
    jobs = [job(1), job(2)]
    with email_env(limit=2, drip_batch=1) as m:
        m.cold.side_effect = [EmailSendError("boom"), "delivered@test.com"]
        email_node.send_emails(cold_state(jobs, dry_run=False))
        email_node.hr_drip_campaign(make_initial_state(dry_run=False))
        log = db.get_email_log() 
        print([(r["kind"], r["status"]) for r in log])                               # newest first
        assert [(r["kind"], r["status"]) for r in log] == [
            ("drip", "sent"), ("cold", "sent"), ("cold", "failed")]
        assert log[1]["delivered_to"] == "delivered@test.com" and log[1]["job_hash"]
        assert all(r["subject"] and r["body"] for r in log)
    with email_env() as m:
        email_node.send_emails(cold_state([job(3)], dry_run=True))
        assert db.get_email_log() == []                         # dry run logs nothing

TESTS = [
    test_dry_run_writes_and_sends_nothing,
    test_real_run_respects_limit,
    test_no_contact_is_flagged_not_counted,
    test_same_hr_never_contacted_twice,
    test_send_failure_is_isolated,
    test_missing_credentials_send_nothing,
    test_llm_failure_uses_template,
    test_daily_budget_includes_earlier_runs,
    test_database_failure_is_critical,
    test_drip_dry_run_sends_nothing,
    test_drip_real_run_sends_each_contact_once,
    test_drip_respects_daily_budget,
    test_drip_dry_run_skips_handled_contacts,
    test_drip_failure_and_missing_credentials,
    test_provided_hr_email_skips_lookup,
    test_real_sends_are_logged,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All email node checks passed.")


if __name__ == "__main__":
    run()