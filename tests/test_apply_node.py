"""Tests for the apply node (the browser is mocked)."""
import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from agent.nodes import apply_node
from agent.state import make_initial_state
from agent.utils import CriticalError
from database import db_manager as db
from scrapers.common import make_job
from tools.applier import ApplyError, ApplyUncertain, ApplyUnsupported


@contextmanager
def apply_env(limit: int = 2):
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        stack.enter_context(patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"))
        stack.enter_context(patch.multiple(config, MAX_APPLY_PER_DAY=limit,
                                           AUTO_APPLY_PORTALS=["demo"]))
        stack.enter_context(patch.object(apply_node, "today_str", lambda: "2026-11-24"))
        mocks = SimpleNamespace(
            submit=stack.enter_context(patch.object(apply_node, "submit_application")),
            pause=stack.enter_context(patch.object(apply_node, "pause")))
        db.init_db()
        yield mocks


def job(n: int, portal: str = "demo", apply_url: bool = True) -> dict:
    item = make_job(url=f"https://x.com/jobs/{n}", title=f"Dev {n}",
                    company=f"Co{n}", portal=portal)
    if apply_url:
        item["apply_url"] = "file:///form.html"
    return item


def state_for(jobs: list[dict], dry_run: bool):
    if not dry_run:
        for item in jobs:
            db.insert_job(item)
    state = make_initial_state(dry_run)
    state["jobs_to_apply"] = jobs
    return state


def status_of(item: dict) -> str:
    return db.get_job(db.make_job_hash(item["url"]))["status"]


def statuses(update: dict) -> list[str]:
    return [r["apply_status"] for r in update["application_results"]]


def test_unsupported_jobs_route_to_email():
    jobs = [job(1, "remoteok"), job(2, "demo", apply_url=False)]
    with apply_env() as m:
        update = apply_node.apply_jobs(state_for(jobs, dry_run=False))
        assert statuses(update) == ["route_email", "route_email"]
        assert m.submit.call_count == 0
        assert [status_of(j) for j in jobs] == ["new", "new"]


def test_dry_run_never_applies_or_writes():
    with apply_env(limit=2) as m:
        update = apply_node.apply_jobs(state_for([job(1), job(2), job(3)], dry_run=True))
        assert statuses(update) == ["dry_run", "dry_run", "limit_reached"]
        assert m.submit.call_count == 0 and m.pause.call_count == 0
        assert db.get_jobs() == [] and update["total_applied"] == 0


def test_real_run_applies_and_records():
    jobs = [job(1), job(2)]
    with apply_env() as m:
        update = apply_node.apply_jobs(state_for(jobs, dry_run=False))
        assert statuses(update) == ["applied", "applied"] and update["total_applied"] == 2
        assert m.submit.call_count == 2 and m.pause.call_count == 1
        assert [status_of(j) for j in jobs] == ["applied", "applied"]
        assert db.get_job(db.make_job_hash(jobs[0]["url"]))["applied_date"]


def test_limit_is_respected():
    jobs = [job(1), job(2), job(3)]
    with apply_env(limit=2) as m:
        update = apply_node.apply_jobs(state_for(jobs, dry_run=False))
        assert statuses(update) == ["applied", "applied", "limit_reached"]
        assert m.submit.call_count == 2
        assert status_of(jobs[2]) == "new"                    # still available tomorrow


def test_unsupported_jobs_do_not_use_the_limit():
    jobs = [job(1, "remoteok"), job(2), job(3)]
    with apply_env(limit=1) as m:
        update = apply_node.apply_jobs(state_for(jobs, dry_run=False))
        assert statuses(update) == ["route_email", "applied", "limit_reached"]


def test_budget_counts_earlier_runs():
    with apply_env(limit=2) as m:
        db.add_daily_stats("2026-11-24", applied=1)
        db.add_daily_stats("2026-11-23", applied=9)           # yesterday does not count
        update = apply_node.apply_jobs(state_for([job(1), job(2)], dry_run=False))
        assert statuses(update) == ["applied", "limit_reached"]
        assert m.submit.call_count == 1


def test_outcomes_are_recorded_separately():
    jobs = [job(n) for n in range(4)]
    with apply_env(limit=4) as m:
        m.submit.side_effect = [ApplyError("boom"), ApplyUnsupported("form needs a person"),
                                ApplyUncertain("no confirmation"), None]
        update = apply_node.apply_jobs(state_for(jobs, dry_run=False))
        assert statuses(update) == ["failed", "manual_required", "manual_required", "applied"]
        assert [status_of(j) for j in jobs] == ["failed", "manual_required",
                                                "manual_required", "applied"]
        assert update["total_failed"] == 1 and update["total_applied"] == 1
        assert "boom" in db.get_job(db.make_job_hash(jobs[0]["url"]))["notes"]
        assert any("boom" in e for e in update["errors"])


def test_already_handled_jobs_are_not_retried():
    item = job(1)
    with apply_env() as m:
        db.insert_job(item)
        db.update_job_status(db.make_job_hash(item["url"]), "applied")
        update = apply_node.apply_jobs(state_for([item], dry_run=False))
        assert statuses(update) == ["already_handled"] and m.submit.call_count == 0


def test_database_failure_is_critical():
    with apply_env():
        with patch.object(db, "get_recent_stats", side_effect=sqlite3.OperationalError("locked")):
            try:
                apply_node.apply_jobs(state_for([job(1)], dry_run=True))
            except CriticalError:
                return
            raise AssertionError("a database failure did not stop the run")


TESTS = [
    test_unsupported_jobs_route_to_email,
    test_dry_run_never_applies_or_writes,
    test_real_run_applies_and_records,
    test_limit_is_respected,
    test_unsupported_jobs_do_not_use_the_limit,
    test_budget_counts_earlier_runs,
    test_outcomes_are_recorded_separately,
    test_already_handled_jobs_are_not_retried,
    test_database_failure_is_critical,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All apply node checks passed.")


if __name__ == "__main__":
    run()