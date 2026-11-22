"""Tests for the filter rules and the filter node (offline, temporary DB)."""
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from agent.filters import evaluate_job, find_keyword, required_years
from agent.nodes.filter_node import filter_jobs
from agent.state import make_initial_state
from agent.utils import CriticalError
from database import db_manager as db
from scrapers.common import make_job

RULES = dict(
    BLACKLIST_KEYWORDS=["java", "php", "sales"],
    WHITELIST_KEYWORDS=["python", "react", "javascript", "software engineer", "developer"],
    SENIOR_KEYWORDS=["senior", "lead", "vp", "manager"],
    MAX_YEARS_REQUIRED=2,
)


def job(title, url, description="", tags=None):
    return make_job(url=url, title=title, company="Acme", portal="test",
                    description=description, tags=tags)


@contextmanager
def temp_db(init=True):
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"):
        if init:
            db.init_db()
        yield


def test_keyword_matching_is_whole_word():
    assert find_keyword("Java Developer", ["java"]) == "java"
    assert find_keyword("JavaScript Developer", ["java"]) is None
    assert find_keyword("HTML Developer", ["ml"]) is None
    assert find_keyword("Email Marketing", ["ai"]) is None
    assert find_keyword("AI Engineer", ["ai"]) == "ai"
    assert find_keyword("C++ Developer", ["c++"]) == "c++"
    assert find_keyword("Full-Stack Engineer", ["full stack"]) == "full stack"
    assert find_keyword("anything", [""]) is None


def test_required_years():
    assert required_years("5+ years of experience in Python") == 5
    assert required_years("3-5 years experience in Python") == 3
    assert required_years("Experience: 4 years") == 4
    assert required_years("0-2 years of experience") == 0
    assert required_years("5+ years Python, 2 years React experience") == 5
    assert required_years("We have 10 years of history. Great culture.") is None
    assert required_years("") is None


def test_evaluate_job_rules():
    with patch.multiple(config, **RULES):
        def verdict(title, desc="", tags=None):
            return evaluate_job(job(title, "https://x.com/j", desc, tags))

        assert verdict("Python Developer", "Build APIs") == (True, "")
        assert verdict("JavaScript Developer") == (True, "")          
        assert verdict("Java Developer")[1].startswith("blacklist")
        assert verdict("Senior Python Developer")[1].startswith("senior")
        assert verdict("Python Developer", "5+ years of experience")[1].startswith("experience")
        assert verdict("Junior Developer", "0-2 years of experience") == (True, "")
        assert verdict("Marketing Associate", tags=["marketing"])[1].startswith("whitelist")
        assert verdict("Software Engineer", tags=["backend"]) == (True, "")
        assert verdict("Backend Role", tags=["python"]) == (True, "")  
    with patch.multiple(config, **{**RULES, "WHITELIST_KEYWORDS": []}):
        assert evaluate_job(job("Anything Goes", "https://x.com/j")) == (True, "")


def _raw_jobs():
    return [
        job("Python Developer", "https://x.com/jobs/1"),
        job("Python Developer", "https://X.com/jobs/1/?utm_source=a"),   
        job("Java Developer", "https://x.com/jobs/2"),                   
        job("Existing Python Developer", "https://x.com/jobs/existing"),  
        job("React Developer", "not-a-url"),
        job("React Developer", "https://x.com/jobs/3"),
    ]


def test_filter_node_real_run():
    with temp_db(), patch.multiple(config, **RULES):
        db.insert_job(job("Existing Python Developer", "https://x.com/jobs/existing"))
        state = make_initial_state(dry_run=False)
        state["raw_jobs"] = _raw_jobs()

        update = filter_jobs(state)
        assert [j["url"] for j in update["new_jobs"]] == [
            "https://x.com/jobs/1", "https://x.com/jobs/3"]
        assert update["total_new"] == 2
        assert len(db.get_jobs()) == 3                   

        assert filter_jobs(state)["new_jobs"] == []


def test_filter_node_dry_run_does_not_write():
    with temp_db(), patch.multiple(config, **RULES):
        db.insert_job(job("Existing Python Developer", "https://x.com/jobs/existing"))
        state = make_initial_state(dry_run=True)
        state["raw_jobs"] = _raw_jobs()

        assert filter_jobs(state)["total_new"] == 2
        assert len(db.get_jobs()) == 1
        assert filter_jobs(state)["total_new"] == 2      


def test_database_failure_is_critical():
    with temp_db(init=False), patch.multiple(config, **RULES):
        state = make_initial_state()
        state["raw_jobs"] = [job("Python Developer", "https://x.com/jobs/1")]
        try:
            filter_jobs(state)
        except CriticalError:
            return
        raise AssertionError("a database failure did not stop the run")


TESTS = [
    test_keyword_matching_is_whole_word,
    test_required_years,
    test_evaluate_job_rules,
    test_filter_node_real_run,
    test_filter_node_dry_run_does_not_write,
    test_database_failure_is_critical,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All filter checks passed.")


if __name__ == "__main__":
    run()