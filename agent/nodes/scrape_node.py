"""Scrape node: runs every registered scraper and collects their jobs."""
import logging
from typing import Any

from agent.state import AgentState
from agent.utils import safe_node
from scrapers import run_all_scrapers

logger = logging.getLogger(__name__)


@safe_node
def scrape_jobs(state: AgentState) -> dict[str, Any]:
    """Run all scrapers; a failing scraper is recorded, the others still count.

    Scraping only reads public data, so it runs the same in dry-run mode.
    """
    jobs, scraper_errors = run_all_scrapers()
    logger.info("Scrape: %d jobs, %d scraper errors", len(jobs), len(scraper_errors))
    return {
        "raw_jobs": jobs,
        "total_scraped": len(jobs),
        "errors": [*state["errors"], *scraper_errors],
    }