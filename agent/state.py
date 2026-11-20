"""Shared state passed between all LangGraph nodes."""
import time
from typing import Any, TypedDict

Job = dict[str, Any]


class AgentState(TypedDict):
    """The single dictionary every node reads from and writes to."""

    # Control fields 
    dry_run: bool            
    start_time: float       
    errors: list[str]        

    # Memory
    memory_context: str
    memory_saved: bool

    # Scrape
    raw_jobs: list[Job]
    total_scraped: int

    # Filter
    new_jobs: list[Job]
    total_new: int

    # Score
    scored_jobs: list[Job]  
    jobs_to_apply: list[Job]  

    # Apply
    application_results: list[Job]
    total_applied: int
    total_failed: int

    # Email
    emails_sent: list[Job]
    total_emailed: int

    # HR drip
    drip_emails_sent: int

    # Report
    report_html: str


def make_initial_state(dry_run: bool = True) -> AgentState:
    """Return a state with every field set to a safe empty value.

    Dry-run is on by default: real actions must be switched on explicitly.
    """
    return AgentState(
        dry_run=dry_run,
        start_time=time.time(),
        errors=[],
        memory_context="",
        memory_saved=False,
        raw_jobs=[],
        total_scraped=0,
        new_jobs=[],
        total_new=0,
        scored_jobs=[],
        jobs_to_apply=[],
        application_results=[],
        total_applied=0,
        total_failed=0,
        emails_sent=[],
        total_emailed=0,
        drip_emails_sent=0,
        report_html="",
    )