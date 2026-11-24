"""Score node: batched Gemini scoring with a keyword fallback."""
import logging
import sqlite3
from typing import Any

import config
from agent.llm import LLMError, generate_text
from agent.scoring import (SCORE_BATCH_SIZE, build_prompt, heuristic_score,
                           parse_scores, profile_skills)
from agent.state import AgentState
from agent.utils import CriticalError, safe_node
from database import db_manager as db

logger = logging.getLogger(__name__)


def _score_batch(batch: list[dict[str, Any]], profile: dict[str, Any],
                 skills: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    """Score one batch. Returns (scored jobs, a warning if the fallback was used)."""
    valid_ids = {f"j{i}" for i in range(len(batch))}
    llm_scores: dict[str, tuple[float, str]] = {}
    warning = None

    try:
        text = generate_text(build_prompt(batch, profile), json_output=True)
        llm_scores = parse_scores(text, valid_ids)
    except (LLMError, ValueError) as exc:
        warning = f"score: Gemini unavailable or invalid ({exc}); used keyword fallback"
        logger.warning(warning)

    scored = []
    for i, job in enumerate(batch):
        if f"j{i}" in llm_scores:
            score, reason = llm_scores[f"j{i}"]
            source = "gemini"
        else:
            score, reason, source = heuristic_score(job, skills), "keyword fallback", "fallback"
        scored.append({**job, "relevance_score": score,
                       "score_reason": reason, "score_source": source})

    if warning is None and any(j["score_source"] == "fallback" for j in scored):
        warning = "score: Gemini skipped some jobs; used keyword fallback for them"
    return scored, warning


def _save_scores(scored: list[dict[str, Any]]) -> None:
    """Store scores in SQLite. A database failure is critical, as in the filter."""
    try:
        for job in scored:
            if job["score_source"] != "gemini":
                continue                      # 
            db.update_job_score(db.make_job_hash(job["url"]), job["relevance_score"])
    except sqlite3.Error as exc:
        raise CriticalError(f"Database error while saving scores: {exc}") from exc


@safe_node
def score_jobs(state: AgentState) -> dict[str, Any]:
    """Score every new job, then shortlist the best TOP_N_JOBS above the threshold.

    The daily application limit is NOT applied here; apply_jobs enforces it.
    Gemini is also called in dry-run mode, but scores are only written to
    SQLite on real runs.
    """
    profile = config.USER_PROFILE
    skills = profile_skills(profile)
    jobs = state["new_jobs"]

    scored: list[dict[str, Any]] = []
    warnings: list[str] = []
    for start in range(0, len(jobs), SCORE_BATCH_SIZE):
        batch_scored, warning = _score_batch(jobs[start:start + SCORE_BATCH_SIZE], profile, skills)
        scored.extend(batch_scored)
        if warning:
            warnings.append(warning)

    scored.sort(key=lambda j: j["relevance_score"], reverse=True)
    if not state.get("dry_run", True):
        _save_scores(scored)

    eligible = [j for j in scored
                if j["score_source"] == "gemini" or config.ALLOW_FALLBACK_SHORTLIST]
    shortlist = [j for j in eligible
                 if j["relevance_score"] >= config.JOB_SCORE_THRESHOLD][:config.TOP_N_JOBS]
    logger.info("Score: %d scored, %d shortlisted (threshold %s, top %s)",
                len(scored), len(shortlist), config.JOB_SCORE_THRESHOLD, config.TOP_N_JOBS)
    return {
        "scored_jobs": scored,
        "jobs_to_apply": shortlist,
        "errors": [*state["errors"], *warnings],
    }
