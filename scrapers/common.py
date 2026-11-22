"""Helpers shared by all portal scrapers."""
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

Job = dict[str, Any]


def html_to_text(html: str | None) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not html:
        return ""
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())


def make_job(
    *,
    url: str,
    title: str,
    company: str,
    portal: str,
    location: str = "",
    description: str = "",
    posted_date: str = "",
    salary: str = "",
    tags: list[str] | None = None,
) -> Job:
    """Build a job dict in the one format every scraper must return."""
    return {
        "url": url,
        "title": title,
        "company": company,
        "portal": portal,
        "location": location,
        "description": description,
        "posted_date": posted_date,
        "salary": salary,
        "tags": tags or [],
    }


def _read_cache(path: Path) -> Any | None:
    """Return cached JSON, or None if the file is missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cached_fetch(name: str, fetch: Callable[[], Any], ttl_hours: float) -> Any:
    """Return fresh cached data if it exists, otherwise call fetch() and cache it.

    If fetch() fails and an older cache exists, the stale data is used
    rather than failing the run. With no cache at all, the error propagates.
    """
    path = config.CACHE_DIR / f"{name}.json"

    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < ttl_hours:
            data = _read_cache(path)
            if data is not None:
                logger.info("Using cached %s data (%.1f h old)", name, age_hours)
                return data

    try:
        data = fetch()
    except Exception:
        stale = _read_cache(path) if path.exists() else None
        if stale is not None:
            logger.warning("Fetching %s failed; using stale cache", name, exc_info=True)
            return stale
        raise

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data