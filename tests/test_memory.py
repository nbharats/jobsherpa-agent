"""Tests for the memory nodes (offline, temporary database)."""
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from agent.nodes import memory_node
from agent.nodes.memory_node import (build_memory_context, load_memory,
                                     save_memory, today_str)
from agent.state import make_initial_state
from agent.utils import CriticalError
from database import db_manager as db
from scrapers.common import make_job


@contextmanager
def temp_db(init: bool = False):
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"):
        if init:
            db.init_db()
        yield


def finished_state(dry_run: bool):
    """A state as it looks after a run that scraped, applied and emailed."""
    state = make_initial_state(dry_run)
    state.update(total_scraped=10, total_new=4, total_applied=2,
                 total_emailed=3, drip_emails_sent=1, total_failed=1)
    state["start_time"] = time.time() - 5
    return state


def test_context_text():
    assert build_memory_context([], {}) == "No previous runs recorded."
    stats = [{"date": "2026-11-22", "total_scraped": 10, "total_new": 4,
              "total_applied": 2, "total_emailed": 3, "total_failed": 1}]
    text = build_memory_context(stats, {"applied": 2, "new": 3})
    assert "2026-11-22: scraped 10, new 4, applied 2, emailed 3, failed 1" in text
    assert "Jobs tracked so far: 5 (2 applied, 3 new)" in text


def test_load_memory_on_empty_database():
    with temp_db():                                   # no tables yet
        update = load_memory(make_initial_state())
        assert update["memory_context"] == "No previous runs recorded."
        assert db.get_jobs() == []                    # tables were created


def test_interrupted_jobs_recovered_only_on_real_runs():
    with temp_db(init=True):
        job = make_job(url="https://x.com/jobs/1", title="T", company="C", portal="p")
        db.insert_job(job)
        job_hash = db.make_job_hash(job["url"])
        assert db.claim_job(job_hash) is True         # simulates a crash mid-action

        dry = load_memory(make_initial_state(dry_run=True))
        assert "interrupted" in dry["memory_context"]
        assert db.get_job(job_hash)["status"] == "in_progress"   # untouched

        real = load_memory(make_initial_state(dry_run=False))
        assert "interrupted" in real["memory_context"]
        assert db.get_job(job_hash)["status"] == "manual_required"


def test_dry_run_saves_nothing():
    with temp_db(init=True):
        assert save_memory(finished_state(dry_run=True)) == {"memory_saved": False}
        assert db.get_recent_stats() == []


def test_real_runs_accumulate_and_are_remembered():
    with temp_db(init=True), patch.object(memory_node, "today_str", lambda: "2026-11-23"):
        assert save_memory(finished_state(dry_run=False)) == {"memory_saved": True}
        assert save_memory(finished_state(dry_run=False)) == {"memory_saved": True}
        (row,) = db.get_recent_stats()
        assert row["date"] == "2026-11-23"
        assert row["total_scraped"] == 20 and row["total_new"] == 8
        assert row["total_applied"] == 4 and row["total_failed"] == 2
        assert row["total_emailed"] == 8              # cold emails + drip
        assert row["run_time_seconds"] >= 10

        context = load_memory(make_initial_state())["memory_context"]
        assert "2026-11-23: scraped 20, new 8, applied 4, emailed 8, failed 2" in context


def test_load_failure_is_critical():
    with patch.object(db, "init_db", side_effect=sqlite3.OperationalError("disk I/O error")):
        try:
            load_memory(make_initial_state())
        except CriticalError:
            return
        raise AssertionError("a database failure did not stop the run")


def test_save_failure_is_recoverable():
    with patch.object(db, "add_daily_stats", side_effect=sqlite3.OperationalError("locked")):
        update = save_memory(finished_state(dry_run=False))
    assert update["memory_saved"] is False
    assert "save_memory" in update["errors"][0]


def test_today_str():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_str())
    with patch.object(config, "TIMEZONE", "Not/AZone"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_str())   # falls back safely


TESTS = [
    test_context_text,
    test_load_memory_on_empty_database,
    test_interrupted_jobs_recovered_only_on_real_runs,
    test_dry_run_saves_nothing,
    test_real_runs_accumulate_and_are_remembered,
    test_load_failure_is_critical,
    test_save_failure_is_recoverable,
    test_today_str,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All memory checks passed.")


if __name__ == "__main__":
    run()