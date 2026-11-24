"""Tests for the demo portal and its sample jobs."""
from pathlib import Path
from unittest.mock import patch

import config
from agent.filters import evaluate_job
from database import db_manager as db
from scrapers import demo_scraper

DEFAULT_RULES = dict(
    BLACKLIST_KEYWORDS=["java", "php", "wordpress", "sales", "bpo"],
    WHITELIST_KEYWORDS=["python", "react", "flask", "javascript", "full stack",
                        "software engineer", "developer", "frontend", "backend",
                        "ai", "ml", "machine learning"],
    SENIOR_KEYWORDS=["senior", "sr", "lead", "principal", "staff", "director", "vp",
                     "vice president", "head of", "chief", "cto", "manager", "architect"],
    MAX_YEARS_REQUIRED=2,
)


def test_disabled_by_default():
    with patch.object(config, "ENABLE_DEMO_PORTAL", False):
        assert demo_scraper.scrape() == []


def test_sample_jobs_load_and_are_valid():
    with patch.object(config, "ENABLE_DEMO_PORTAL", True):
        jobs = demo_scraper.scrape()
    assert len(jobs) == 6 and len({j["url"] for j in jobs}) == 6
    portals = [j["portal"] for j in jobs]
    assert portals.count("demo") == 3 and portals.count("demo-board") == 3
    assert demo_scraper.FORM_FILE.is_file()
    for job in jobs:
        assert job["sample"] is True and "SAMPLE" in job["description"]
        assert len(db.make_job_hash(job["url"])) == 16
        if job["portal"] == "demo":
            assert job["apply_url"].startswith("file:///")
        else:
            assert job["hr_email"].endswith(".example.org")


def test_sample_jobs_pass_the_filter():
    with patch.object(config, "ENABLE_DEMO_PORTAL", True), patch.multiple(config, **DEFAULT_RULES):
        for job in demo_scraper.scrape():
            assert evaluate_job(job) == (True, ""), job["title"]


TESTS = [test_disabled_by_default, test_sample_jobs_load_and_are_valid,
         test_sample_jobs_pass_the_filter]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All demo checks passed.")


if __name__ == "__main__":
    run()