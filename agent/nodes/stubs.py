"""Temporary stub node (report). Replaced by a real node later."""
import logging
from typing import Any

from agent.state import AgentState
from agent.utils import safe_node

logger = logging.getLogger(__name__)


@safe_node
def build_report(state: AgentState) -> dict[str, Any]:
    logger.info("report (stub)")
    html = (
        "<h1>JobSherpa stub report</h1>"
        f"<p>Scraped: {state['total_scraped']}, new: {state['total_new']}, "
        f"applied: {state['total_applied']}, emailed: {state['total_emailed']}, "
        f"errors: {len(state['errors'])}, dry-run: {state['dry_run']}</p>"
    )
    return {"report_html": html}