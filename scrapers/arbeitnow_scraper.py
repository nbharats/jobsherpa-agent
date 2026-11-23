"""Arbeitnow scraper using its free public JSON API.

Only the first page is fetched, and only remote jobs are kept (most
Arbeitnow listings are on-site jobs in Germany). Credit Arbeitnow and link
to the job URL wherever jobs are shown.
"""
import logging
from datetime import datetime, timezone
from typing import Any

import requests

import config
from scrapers.common import Job, cached_fetch, html_to_text, make_job

logger = logging.getLogger(__name__)

PORTAL = "arbeitnow"
API_URL = "https://www.arbeitnow.com/api/job-board-api"
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


def _posted_date(value: Any) -> str:
    """Convert Arbeitnow's unix timestamp to an ISO date string."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")
    return str(value or "")


def parse_jobs(data: Any) -> list[Job]:
    """Convert the API response into the common job format (remote jobs only)."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("Unexpected Arbeitnow response: expected an object with a 'data' list")

    jobs: list[Job] = []
    for item in data["data"]:
        if not isinstance(item, dict) or not item.get("title") or not item.get("url"):
            continue
        if not item.get("remote"):
            continue
        jobs.append(
            make_job(
                url=item["url"],
                title=item["title"],
                company=item.get("company_name") or "Unknown",
                portal=PORTAL,
                location=item.get("location") or "Remote",
                description=html_to_text(item.get("description")),
                posted_date=_posted_date(item.get("created_at")),
                tags=item.get("tags") or [],
            )
        )
    return jobs


def scrape() -> list[Job]:
    """Fetch (or load from cache) and parse Arbeitnow jobs. May raise on failure."""
    data = cached_fetch(PORTAL, fetch_raw, config.CACHE_TTL_HOURS)
    jobs = parse_jobs(data)
    logger.info("Arbeitnow: %d remote jobs parsed", len(jobs))
    return jobs