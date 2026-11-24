"""Tests for routing, error handling, dry-run safety, limits and deduplication.

Scrapers, Gemini, DNS and email sending are all faked, and the database is a
temporary file, so nothing here touches the network or your real data.
"""
import json
import re
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from agent.graph import build_graph, route_after_filter
from agent.nodes import apply_node, email_node, memory_node,report_node, scrape_node, score_node
from agent.state import make_initial_state
from agent.utils import CriticalError, safe_node
from database import db_manager as db
from scrapers import run_all_scrapers
from scrapers.common import make_job
from tools import email_sender

FULL = ["load_memory", "scrape", "filter", "score", "apply",
        "email", "hr_drip", "save_memory", "report"]
SKIPPED = ["load_memory", "scrape", "filter", "hr_drip", "save_memory", "report"]

RULES = dict(
    BLACKLIST_KEYWORDS=["java", "php", "sales"],
    WHITELIST_KEYWORDS=["python", "developer"],
    SENIOR_KEYWORDS=["senior", "lead", "vp", "manager"],
    MAX_YEARS_REQUIRED=2,
)

BUSY = dict(JOB_SCORE_THRESHOLD=7, TOP_N_JOBS=5,
            MAX_APPLY_PER_DAY=2, MAX_COLD_EMAILS_PER_DAY=1)

TEST_PROFILE = {
    "personal_info": {"name": "Test User", "phone": "+91-9999999999", "location": "India"},
    "education": [{"degree": "B.Tech CSE", "graduation_year": 2025}],
    "skills": {"programming_languages": ["Python"], "backend": ["Flask"]},
    "projects": [{"name": "Proj", "description": "A web app", "technologies": ["Flask"]}],
    "target_roles": ["Python Developer"],
}
EMAIL_BODY = ("Hello team, I would like to be considered for this role. I work with Python "
              "and Flask and have attached my resume for your review. Is it still open?")


def fake_gemini(prompt: str, **kwargs) -> str:
    """Deterministic stand-in for Gemini: scores 10, 9.5, 9, ... or writes emails."""
    ids = list(dict.fromkeys(re.findall(r'"id": "(j\d+)"', prompt)))
    if '"emails"' in prompt:
        return json.dumps({"emails": [
            {"id": i, "subject": "Application", "body": EMAIL_BODY} for i in ids]})
    return json.dumps({"scores": [
        {"id": job_id, "score": round(10 - 0.5 * i, 1), "reason": "fake"}
        for i, job_id in enumerate(ids)]})


def fake_finder(company: str, **kwargs):
    address = f"hr@{company.lower()}.com"
    return [{"email": address, "domain": address.split("@")[1],
             "source": "pattern", "verified": False}]


def fake_jobs(count: int) -> list[dict]:
    """Jobs that pass the filter; every other job has an application form URL."""
    jobs = []
    for i in range(count):
        job = make_job(url=f"https://example.com/jobs/{i}", title=f"Python Developer {i}",
                       company=f"Acme{i}", portal="stub" if i % 2 == 0 else "remoteok")
        if i % 2 == 0:
            job["apply_url"] = "https://example.com/apply"
        jobs.append(job)
    return jobs


@contextmanager
def pipeline_env(job_count: int = 3, fail_scraper: bool = False, **overrides):
    """Fake scrapers, Gemini, DNS and email; a temporary database; fixed rules."""
    def good():
        return fake_jobs(job_count)

    def broken():
        raise RuntimeError("portal down")

    scrapers = {"fake": good}
    if fail_scraper:
        scrapers["broken"] = broken

    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        csv_path = Path(tmp) / "hr.csv"
        csv_path.write_text("name,email,company,designation\n"
                            "Asha,asha@acme.com,Acme,HR\nBen,ben@beta.com,Beta,Recruiter\n",
                            encoding="utf-8")
        settings = {**RULES, "USER_PROFILE": TEST_PROFILE, "DAILY_EMAIL_LIMIT": 100,
                    "DRIP_BATCH_SIZE": 2, "HR_CSV_PATH": csv_path, "EMAIL_REDIRECT_TO": "",
                    "MAX_COLD_EMAILS_PER_DAY": 3, "AUTO_APPLY_PORTALS": ['stub'],
                    "REPORTS_DIR": Path(tmp) / "reports", "REPORT_EMAIL": "me@gmail.com", 
                     **overrides, }
        stack.enter_context(patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"))
        stack.enter_context(patch.multiple(config, **settings))
        stack.enter_context(patch.object(scrape_node, "run_all_scrapers",
                                         lambda: run_all_scrapers(scrapers)))
        stack.enter_context(patch.object(score_node, "generate_text", fake_gemini))
        stack.enter_context(patch.object(email_node, "generate_text", fake_gemini))
        stack.enter_context(patch.object(email_node, "find_hr_emails", fake_finder))
        stack.enter_context(patch.object(email_node, "pause"))
        stack.enter_context(patch.object(email_sender, "sender_problem", return_value=None))
        stack.enter_context(patch.object(apply_node, "pause"))
        stack.enter_context(patch.object(report_node, "send_report_email"))
        db.init_db()
        yield


@contextmanager
def external_mocks():
    """Replace the three external actions with mocks and yield them."""
    with (patch.object(apply_node, "submit_application") as submit,
          patch.object(email_node, "send_cold_email", return_value="delivered@test.com") as email,
          patch.object(email_node, "send_drip_email", return_value="delivered@test.com") as drip):
        yield submit, email, drip


def run_pipeline(app, dry_run: bool = True):
    """Run the graph once; return (node order, final merged state)."""
    state = make_initial_state(dry_run)
    order = []
    for chunk in app.stream(state, stream_mode="updates"):
        for node, update in chunk.items():
            order.append(node)
            state.update(update)
    return order, state


def test_normal_run():
    with pipeline_env(3):
        order, final = run_pipeline(build_graph())
    assert order == FULL
    assert final["total_scraped"] == 3 and final["total_new"] == 3
    assert final["report_html"]
    assert final["memory_saved"] is False           # dry run saves no stats
    assert "No previous runs recorded." in final["memory_context"]
    assert final["errors"] == []
    assert "DRY RUN" in final["report_html"]


def test_routing_uses_job_list_not_counter():
    assert route_after_filter({"new_jobs": [], "total_new": 5}) == "hr_drip"
    assert route_after_filter({"new_jobs": [{"url": "x"}], "total_new": 0}) == "score"


def test_no_new_jobs_route():
    with pipeline_env(0), external_mocks() as (submit, email, drip):
        order, final = run_pipeline(build_graph(), dry_run=False)
    assert order == SKIPPED
    assert submit.call_count == 0 and email.call_count == 0
    assert drip.call_count == 2                     # outreach still runs


def test_all_scrapers_failing_is_recoverable():
    with pipeline_env(0, fail_scraper=True):
        order, final = run_pipeline(build_graph())
    assert len(final["errors"]) == 1 and "portal down" in final["errors"][0]
    assert order == SKIPPED          # nothing scraped, so the pipeline skips ahead
    assert final["report_html"]      # and still finishes


def test_one_failing_scraper_keeps_the_others_jobs():
    with pipeline_env(3, fail_scraper=True):
        order, final = run_pipeline(build_graph())
    assert order == FULL
    assert final["total_new"] == 3
    assert len(final["errors"]) == 1 and "broken" in final["errors"][0]


def test_already_seen_jobs_are_skipped_next_run():
    with pipeline_env(3), external_mocks():
        app = build_graph()
        order1, first = run_pipeline(app, dry_run=False)
        order2, second = run_pipeline(app, dry_run=False)
    assert order1 == FULL and first["total_new"] == 3
    assert order2 == SKIPPED and second["total_new"] == 0   # all seen yesterday


def test_critical_error_stops_the_run():
    @safe_node
    def broken_memory(state):
        raise CriticalError("database unavailable")

    with patch.object(memory_node, "load_memory", broken_memory):
        try:
            run_pipeline(build_graph())
        except CriticalError:
            pass
        else:
            raise AssertionError("critical error did not stop the run")


def test_dry_run_prevents_external_actions():
    assert make_initial_state()["dry_run"] is True       # safe by default
    with pipeline_env(8, **BUSY), external_mocks() as (submit, email, drip):
        order, final = run_pipeline(build_graph(), dry_run=True)
    assert submit.call_count == 0 and email.call_count == 0 and drip.call_count == 0
    assert final["total_applied"] == 0
    assert final["total_emailed"] == 0
    assert final["drip_emails_sent"] == 0
    statuses = [r["apply_status"] for r in final["application_results"]]
    assert statuses.count("dry_run") == 2                # still simulated
    assert [r["status"] for r in final["emails_sent"]] == ["dry_run"]   # written, not sent


def test_limits_are_respected():
    with pipeline_env(8, **BUSY), external_mocks() as (submit, email, drip):
        order, final = run_pipeline(build_graph(), dry_run=False)
    assert len(final["scored_jobs"]) == 8
    assert len(final["jobs_to_apply"]) == 5              # TOP_N_JOBS shortlist
    assert submit.call_count == 2                        # MAX_APPLY_PER_DAY
    assert final["total_applied"] == 2
    statuses = [r["apply_status"] for r in final["application_results"]]
    assert statuses.count("limit_reached") == 1
    assert email.call_count == 1                         # MAX_COLD_EMAILS_PER_DAY
    assert final["total_emailed"] == 1
    assert drip.call_count == 2 and final["drip_emails_sent"] == 2   # DRIP_BATCH_SIZE


def test_real_run_saves_daily_stats():
    with pipeline_env(3), external_mocks():
        order, final = run_pipeline(build_graph(), dry_run=False)
        (row,) = db.get_recent_stats()
    assert final["memory_saved"] is True
    assert row["total_scraped"] == 3 and row["total_new"] == 3
    assert row["total_emailed"] == 3                     # 1 cold email + 2 drip emails


TESTS = [
    test_normal_run,
    test_routing_uses_job_list_not_counter,
    test_no_new_jobs_route,
    test_all_scrapers_failing_is_recoverable,
    test_one_failing_scraper_keeps_the_others_jobs,
    test_already_seen_jobs_are_skipped_next_run,
    test_critical_error_stops_the_run,
    test_dry_run_prevents_external_actions,
    test_limits_are_respected,
    test_real_run_saves_daily_stats,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All graph checks passed.")


if __name__ == "__main__":
    run()