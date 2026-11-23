"""Tests for routing, error handling, dry-run safety, limits and deduplication.

Scrapers are faked and the database is a temporary file, so nothing here
touches the network or your real data.
"""
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from agent.graph import build_graph, route_after_filter
from agent.nodes import scrape_node, score_node, stubs
from agent.state import make_initial_state
from agent.utils import CriticalError, safe_node
from database import db_manager as db
from scrapers import run_all_scrapers
from scrapers.common import make_job

FULL = ["load_memory", "scrape", "filter", "score", "apply",
        "email", "hr_drip", "save_memory", "report"]
SKIPPED = ["load_memory", "scrape", "filter", "hr_drip", "save_memory", "report"]

RULES = dict(
    BLACKLIST_KEYWORDS=["java", "php", "sales"],
    WHITELIST_KEYWORDS=["python", "developer"],
    SENIOR_KEYWORDS=["senior", "lead", "vp", "manager"],
    MAX_YEARS_REQUIRED=2,
)
# 8 jobs: 7 pass the score threshold, shortlist 5, apply limit 2, email limit 1
BUSY = dict(JOB_SCORE_THRESHOLD=7, TOP_N_JOBS=5,
            MAX_APPLY_PER_DAY=2, MAX_COLD_EMAILS_PER_DAY=1)


def fake_jobs(count: int) -> list[dict]:
    """Jobs that pass the filter; portals alternate so some can auto-apply."""
    return [
        make_job(url=f"https://example.com/jobs/{i}", title=f"Python Developer {i}",
                 company="Acme", portal="stub" if i % 2 == 0 else "remoteok")
        for i in range(count)
    ]


def fake_gemini(prompt: str, **kwargs) -> str:
    """Deterministic stand-in for Gemini: scores 10, 9.5, 9, ... in job order."""
    ids = list(dict.fromkeys(re.findall(r'"id": "(j\d+)"', prompt)))
    return json.dumps({"scores": [
        {"id": job_id, "score": round(10 - 0.5 * i, 1), "reason": "fake"}
        for i, job_id in enumerate(ids)]})


@contextmanager
def pipeline_env(job_count: int = 3, fail_scraper: bool = False, **overrides):
    """Fake scrapers, fake Gemini, a temporary database and fixed filter rules."""
    def good():
        return fake_jobs(job_count)

    def broken():
        raise RuntimeError("portal down")

    scrapers = {"fake": good}
    if fail_scraper:
        scrapers["broken"] = broken

    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"), \
            patch.multiple(config, **{**RULES, **overrides}), \
            patch.object(scrape_node, "run_all_scrapers",
                         lambda: run_all_scrapers(scrapers)), \
            patch.object(score_node, "generate_text", fake_gemini):
        db.init_db()
        yield


@contextmanager
def external_mocks():
    """Replace the three external actions with mocks and yield them."""
    with (patch.object(stubs, "submit_application") as submit,
          patch.object(stubs, "send_cold_email") as email,
          patch.object(stubs, "send_drip_email") as drip):
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
    assert final["memory_saved"] and final["report_html"]
    assert final["errors"] == []


def test_routing_uses_job_list_not_counter():
    assert route_after_filter({"new_jobs": [], "total_new": 5}) == "hr_drip"
    assert route_after_filter({"new_jobs": [{"url": "x"}], "total_new": 0}) == "score"


def test_no_new_jobs_route():
    with pipeline_env(0), external_mocks() as (submit, email, drip):
        order, final = run_pipeline(build_graph(), dry_run=False)
    assert order == SKIPPED
    assert submit.call_count == 0 and email.call_count == 0
    assert drip.call_count == stubs.MOCK_DRIP_CONTACTS   # outreach still runs


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

    with patch.object(stubs, "load_memory", broken_memory):
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
    assert drip.call_count == stubs.MOCK_DRIP_CONTACTS


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
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All graph checks passed.")


if __name__ == "__main__":
    run()