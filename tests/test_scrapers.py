"""Offline tests for the scraper layer: parsing, caching, isolation, DB fit."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import config
from database import db_manager as db
from scrapers import run_all_scrapers
from scrapers.common import cached_fetch
from scrapers.remoteok_scraper import parse_jobs

SAMPLE = [
    {"legal": "Terms: link back to Remote OK and name it as the source."},
    {
        "id": "1", "position": "Python Developer", "company": "Acme",
        "location": "Worldwide", "tags": ["python", "flask"],
        "description": "<p>Build <b>APIs</b></p><ul><li>Flask</li></ul>",
        "date": "2026-11-18T08:00:00+00:00",
        "url": "https://remoteOK.com/remote-jobs/remote-python-developer-acme-1",
        "salary_min": 60000, "salary_max": 90000,
    },
    {"id": "2", "position": "Job without a URL", "company": "Ghost"},
    {
        "id": "3", "position": "Flask Dev", "company": "Beta", "description": "",
        "url": "https://remoteok.com/remote-jobs/flask-dev-beta-3",
        "salary_min": 0, "salary_max": 0,
    },
]


def test_parse_jobs():
    jobs = parse_jobs(SAMPLE)
    assert len(jobs) == 2                       # legal notice and URL-less job skipped
    first, second = jobs
    assert first["title"] == "Python Developer" and first["portal"] == "remoteok"
    assert first["description"] == "Build APIs Flask"
    assert first["salary"] == "60000-90000" and first["tags"] == ["python", "flask"]
    assert second["location"] == "Remote" and second["salary"] == ""
    for job in jobs:                            # the common job format
        assert {"url", "title", "company", "portal", "description"} <= job.keys()


def test_parse_rejects_bad_response():
    try:
        parse_jobs({"error": "rate limited"})
    except ValueError:
        return
    raise AssertionError("a non-list response was accepted")


def test_cache_behaviour():
    calls = []

    def fetch():
        calls.append(1)
        return {"n": len(calls)}

    with tempfile.TemporaryDirectory() as tmp, patch.object(config, "CACHE_DIR", Path(tmp)):
        assert cached_fetch("demo", fetch, 12) == {"n": 1}
        assert cached_fetch("demo", fetch, 12) == {"n": 1}     # served from cache
        assert len(calls) == 1
        assert cached_fetch("demo", fetch, 0) == {"n": 2}      # expired: refetched

        def broken():
            raise RuntimeError("network down")

        old = time.time() - 48 * 3600
        os.utime(Path(tmp) / "demo.json", (old, old))
        assert cached_fetch("demo", broken, 12) == {"n": 2}    # stale cache beats failure

    with tempfile.TemporaryDirectory() as tmp, patch.object(config, "CACHE_DIR", Path(tmp)):
        try:
            cached_fetch("empty", broken, 12)
        except RuntimeError:
            return
        raise AssertionError("failure with no cache did not propagate")


def test_one_failing_scraper_does_not_stop_others():
    def good():
        return parse_jobs(SAMPLE)

    def bad():
        raise RuntimeError("portal down")

    jobs, errors = run_all_scrapers({"good": good, "bad": bad})
    assert len(jobs) == 2
    assert len(errors) == 1 and "bad: portal down" in errors[0]


def test_jobs_fit_the_database():
    with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"):
        db.init_db()
        jobs = parse_jobs(SAMPLE)
        assert all(db.insert_job(job) for job in jobs)         # all new
        assert not any(db.insert_job(job) for job in jobs)     # all duplicates now
        assert len(db.get_jobs()) == 2


TESTS = [
    test_parse_jobs,
    test_parse_rejects_bad_response,
    test_cache_behaviour,
    test_one_failing_scraper_does_not_stop_others,
    test_jobs_fit_the_database,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All scraper checks passed.")


if __name__ == "__main__":
    run()