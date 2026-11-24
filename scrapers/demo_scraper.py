"""Demo portal: clearly labelled SAMPLE jobs for demonstrations and testing.

Disabled unless ENABLE_DEMO_PORTAL=true. Jobs on the 'demo' portal link to a
local sample application form (demo/apply_form.html). Jobs on 'demo-board'
have no form, so they are routed to cold email, to a reserved example.org
address that cannot reach a real person.
"""
import json
import logging
from pathlib import Path
from typing import Any

import config
from scrapers.common import Job, make_job

logger = logging.getLogger(__name__)

DEMO_DIR = Path(config.BASE_DIR) / "demo"
JOBS_FILE = DEMO_DIR / "jobs.json"
FORM_FILE = DEMO_DIR / "apply_form.html"


def parse_demo_jobs(data: Any, form_url: str) -> list[Job]:
    """Convert the sample file into the common job format."""
    if not isinstance(data, list):
        raise ValueError("The demo jobs file must contain a JSON list")

    jobs: list[Job] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("url") or not item.get("title"):
            continue
        job = make_job(
            url=item["url"],
            title=item["title"],
            company=item.get("company") or "Unknown",
            portal=item.get("portal") or "demo",
            location=item.get("location") or "Remote",
            description=item.get("description") or "",
            posted_date=item.get("posted_date") or "",
            tags=item.get("tags") or [],
        )
        job["sample"] = True
        if job["portal"] == "demo":
            job["apply_url"] = form_url
        if item.get("hr_email"):
            job["hr_email"] = item["hr_email"]
        jobs.append(job)
    return jobs


def scrape() -> list[Job]:
    """Return the sample jobs, or nothing when the demo portal is disabled."""
    if not config.ENABLE_DEMO_PORTAL:
        return []
    data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = parse_demo_jobs(data, FORM_FILE.resolve().as_uri())
    logger.info("Demo portal: %d SAMPLE jobs loaded", len(jobs))
    return jobs