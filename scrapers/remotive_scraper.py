"""Remotive scraper using its public JSON API (no login, no browser).

Remotive's terms: link back to the Remotive job page, name Remotive as the
source, poll at most ~4 times a day (the cache enforces this), and never
re-post its jobs on other job sites. The public feed is delayed ~24 hours.
"""
import logging
from typing import Any

import requests

import config
from scrapers.common import Job, cached_fetch, html_to_text, make_job

logger = logging.getLogger(__name__)

PORTAL = "remotive"
API_URL = "https://remotive.com/api/remote-jobs?category=software-dev"
USER_AGENT = "JobSherpa/1.0 (student project)"


def fetch_raw() -> Any:
    """Download the raw API response (the only function that uses the network)."""
    response = requests.get(
        API_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=config.SCRAPER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def parse_jobs(data: Any) -> list[Job]:
    """Convert the API response into the common job format."""
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Unexpected Remotive response: expected an object with a 'jobs' list")

    jobs: list[Job] = []
    for item in data["jobs"]:
        if not isinstance(item, dict) or not item.get("title") or not item.get("url"):
            continue
        jobs.append(
            make_job(
                url=item["url"],
                title=item["title"],
                company=item.get("company_name") or "Unknown",
                portal=PORTAL,
                location=item.get("candidate_required_location") or "Remote",
                description=html_to_text(item.get("description")),
                posted_date=item.get("publication_date") or "",
                salary=str(item.get("salary") or ""),
                tags=item.get("tags") or [],
            )
        )
    return jobs


def scrape() -> list[Job]:
    """Fetch (or load from cache) and parse Remotive jobs. May raise on failure."""
    data = cached_fetch(PORTAL, fetch_raw, config.CACHE_TTL_HOURS)
    jobs = parse_jobs(data)
    logger.info("Remotive: %d jobs parsed", len(jobs))
    return jobs