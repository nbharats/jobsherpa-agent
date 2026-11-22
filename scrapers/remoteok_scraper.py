"""RemoteOK scraper using its public JSON API (no login, no browser).

RemoteOK's terms require naming Remote OK as the source and linking back to
the job page wherever jobs are shown. Each job keeps its RemoteOK URL and
portal='remoteok' so the dashboard and report can do that.
"""
import logging
from typing import Any

import requests

import config
from scrapers.common import Job, cached_fetch, html_to_text, make_job

logger = logging.getLogger(__name__)

PORTAL = "remoteok"
API_URL = "https://remoteok.com/api"
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


def _format_salary(item: dict[str, Any]) -> str:
    """Return the published salary range as text, or '' if none is given."""
    try:
        low = int(item.get("salary_min") or 0)
        high = int(item.get("salary_max") or 0)
    except (TypeError, ValueError):
        return ""
    if low and high:
        return f"{low}-{high}"
    return str(low or high) if (low or high) else ""


def parse_jobs(data: Any) -> list[Job]:
    """Convert the API response into the common job format.

    Element 0 is a legal notice, not a job. Instead of assuming its
    position, we skip anything that lacks a position and a URL.
    """
    if not isinstance(data, list):
        raise ValueError("Unexpected RemoteOK response: expected a JSON list")

    jobs: list[Job] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("position") or not item.get("url"):
            continue
        jobs.append(
            make_job(
                url=item["url"],
                title=item["position"],
                company=item.get("company") or "Unknown",
                portal=PORTAL,
                location=item.get("location") or "Remote",
                description=html_to_text(item.get("description")),
                posted_date=item.get("date") or "",
                salary=_format_salary(item),
                tags=item.get("tags") or [],
            )
        )
    return jobs


def scrape() -> list[Job]:
    """Fetch (or load from cache) and parse RemoteOK jobs. May raise on failure."""
    data = cached_fetch(PORTAL, fetch_raw, config.CACHE_TTL_HOURS)
    jobs = parse_jobs(data)
    logger.info("RemoteOK: %d jobs parsed", len(jobs))
    return jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    found = scrape()
    print(f"Fetched {len(found)} jobs")
    for job in found[:3]:
        print("-", job["title"], "|", job["company"], "|", job["url"])