"""Data helpers for the dashboard: overview numbers, log tails and the latest report."""
from pathlib import Path
from typing import Any

import config
from agent.nodes.memory_node import today_str
from database import db_manager as db

LOG_LEVELS = ("ALL", "INFO", "WARNING", "ERROR")
LEVEL_MARKERS = {
    "INFO": ("| INFO ",),
    "WARNING": ("| WARNING",),
    "ERROR": ("| ERROR", "| CRITICAL"),
}
TAIL_BYTES = 512 * 1024
BAR_PIXELS = 140


def success_rate(applied: int, emailed: int, failed: int) -> float | None:
    """Percent of attempted actions that succeeded, or None if nothing was attempted."""
    attempts = applied + emailed + failed
    return round(100 * (applied + emailed) / attempts, 1) if attempts else None


def _numbers(stats: dict[str, Any] | None) -> dict[str, int]:
    keys = ("total_scraped", "total_new", "total_applied", "total_emailed", "total_failed")
    return {key: int((stats or {}).get(key) or 0) for key in keys}


def build_overview(day: str | None = None) -> dict[str, Any]:
    """Everything the overview page and /api/overview need."""
    day = day or today_str()
    totals = db.get_totals()
    today = _numbers(db.get_stats_for(day))
    statuses = db.count_jobs_by_status()
    history = list(reversed(db.get_recent_stats(7)))          # oldest first
    peak = max((r["total_applied"] + r["total_emailed"] for r in history), default=0)

    def block(scraped: int, new: int, applied: int, emailed: int, failed: int) -> dict[str, Any]:
        return {"scraped": scraped, "new": new, "applied": applied, "emailed": emailed,
                "failed": failed, "success_rate": success_rate(applied, emailed, failed)}

    return {
        "day": day,
        "today": block(today["total_scraped"], today["total_new"], today["total_applied"],
                       today["total_emailed"], today["total_failed"]),
        "all_time": {**block(totals["scraped"], totals["new"], totals["applied"],
                             totals["emailed"], totals["failed"]), "days": totals["days"]},
        "statuses": dict(sorted(statuses.items())),
        "jobs_tracked": sum(statuses.values()),
        "trend": [
            {"date": r["date"], "scraped": r["total_scraped"], "applied": r["total_applied"],
             "emailed": r["total_emailed"], "failed": r["total_failed"],
             "bar": round(BAR_PIXELS * (r["total_applied"] + r["total_emailed"]) / peak) if peak else 0}
            for r in history
        ],
    }


def log_file() -> Path:
    return Path(config.LOGS_DIR) / "jobsherpa.log"


def read_log_tail(lines: int = 200, level: str = "ALL") -> list[str]:
    """Return the last `lines` log lines, optionally only one severity.

    Only the end of a large file is read. A missing file gives an empty list.
    """
    path = log_file()
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
            raw = handle.read()
    except OSError:
        return []
    rows = raw.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES:
        rows = rows[1:]                                      # the first line is probably cut
    markers = LEVEL_MARKERS.get(level.upper())
    if markers:
        rows = [row for row in rows if any(marker in row for marker in markers)]
    return rows[-lines:]


def latest_report() -> str | None:
    """The most recent saved daily report, or None if there is none."""
    try:
        return (Path(config.REPORTS_DIR) / "latest.html").read_text(encoding="utf-8")
    except OSError:
        return None


def with_new_tab_links(html: str) -> str:
    """Make links in the embedded report open in a new tab."""
    return html.replace("<html>", '<html><head><base target="_blank"></head>', 1)