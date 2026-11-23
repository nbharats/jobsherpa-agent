"""Offline tests for the Remotive and Arbeitnow parsers and the registry."""
from scrapers import SCRAPERS
from scrapers.arbeitnow_scraper import parse_jobs as parse_arbeitnow
from scrapers.remotive_scraper import parse_jobs as parse_remotive

REMOTIVE_SAMPLE = {
    "0-legal-notice": "Remotive API Legal Notice",
    "job-count": 3,
    "jobs": [
        {
            "id": 1, "url": "https://remotive.com/remote-jobs/software-dev/python-developer-1",
            "title": "Python Developer", "company_name": "Acme",
            "category": "Software Development", "tags": ["python", "flask"],
            "job_type": "full_time", "publication_date": "2026-11-21T10:00:00",
            "candidate_required_location": "Worldwide", "salary": "$50k-$70k",
            "description": "<p>Build <b>APIs</b></p>",
        },
        {"id": 2, "title": "Job without a URL", "company_name": "Ghost"},
        {
            "id": 3, "url": "https://remotive.com/remote-jobs/software-dev/junior-dev-3",
            "title": "Junior Dev", "company_name": "Beta",
            "candidate_required_location": "", "salary": "", "description": "",
        },
    ],
}

ARBEITNOW_SAMPLE = {
    "data": [
        {
            "slug": "a", "company_name": "Acme GmbH", "title": "Backend Developer",
            "description": "<p>Python <i>APIs</i></p>", "remote": True,
            "url": "https://www.arbeitnow.com/jobs/companies/acme/backend-developer-1",
            "tags": ["python"], "job_types": ["full time"],
            "location": "Berlin", "created_at": 1700000000,
        },
        {
            "slug": "b", "company_name": "Onsite AG", "title": "Office Developer",
            "description": "", "remote": False,
            "url": "https://www.arbeitnow.com/jobs/companies/onsite/office-developer-2",
            "tags": [], "job_types": [], "location": "Munich", "created_at": 1700000000,
        },
        {"slug": "c", "company_name": "Ghost", "title": "No URL", "remote": True},
    ],
    "links": {},
    "meta": {},
}


def test_parse_remotive():
    jobs = parse_remotive(REMOTIVE_SAMPLE)
    assert len(jobs) == 2                        # the URL-less job is skipped
    first, second = jobs
    assert first["portal"] == "remotive" and first["title"] == "Python Developer"
    assert first["company"] == "Acme" and first["location"] == "Worldwide"
    assert first["description"] == "Build APIs" and first["salary"] == "$50k-$70k"
    assert first["tags"] == ["python", "flask"]
    assert second["location"] == "Remote" and second["salary"] == ""


def test_parse_arbeitnow_keeps_remote_only():
    jobs = parse_arbeitnow(ARBEITNOW_SAMPLE)
    assert len(jobs) == 1                        # on-site and URL-less jobs dropped
    job = jobs[0]
    assert job["portal"] == "arbeitnow" and job["company"] == "Acme GmbH"
    assert job["description"] == "Python APIs" and job["location"] == "Berlin"
    assert job["posted_date"] == "2023-11-14T22:13:20+00:00"


def test_parsers_reject_bad_responses():
    for parse, bad in ((parse_remotive, []), (parse_remotive, {"jobs": "x"}),
                       (parse_arbeitnow, {"error": "x"}), (parse_arbeitnow, [])):
        try:
            parse(bad)
        except ValueError:
            continue
        raise AssertionError(f"{parse.__module__} accepted {bad!r}")


def test_registry_lists_three_portals():
    assert set(SCRAPERS) == {"remoteok", "remotive", "arbeitnow"}


TESTS = [
    test_parse_remotive,
    test_parse_arbeitnow_keeps_remote_only,
    test_parsers_reject_bad_responses,
    test_registry_lists_three_portals,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All portal checks passed.")


if __name__ == "__main__":
    run()