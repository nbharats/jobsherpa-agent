"""Scraper registry. Add each new portal's scrape function here."""
import logging
from collections.abc import Callable

from scrapers import remoteok_scraper, remotive_scraper, arbeitnow_scraper
from scrapers.common import Job

logger = logging.getLogger(__name__)

ScrapeFunc = Callable[[], list[Job]]

SCRAPERS: dict[str, ScrapeFunc] = {
    "remoteok": remoteok_scraper.scrape,
    "remotive": remotive_scraper.scrape,
    "arbeitnow": arbeitnow_scraper.scrape,
}


def run_all_scrapers(scrapers: dict[str, ScrapeFunc] | None = None) -> tuple[list[Job], list[str]]:
    """Run every scraper independently.

    One scraper failing never stops the others: its error is returned as
    a string in the second element, and the rest of the jobs are kept.
    """
    jobs: list[Job] = []
    errors: list[str] = []
    for name, scrape in (scrapers or SCRAPERS).items():
        try:
            found = scrape()
        except Exception as exc:
            logger.exception("Scraper '%s' failed; continuing", name)
            errors.append(f"{name}: {exc}")
            continue
        logger.info("Scraper '%s' returned %d jobs", name, len(found))
        jobs.extend(found)
    return jobs, errors