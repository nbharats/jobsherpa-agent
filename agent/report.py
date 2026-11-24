"""Builds the daily report as HTML (for email) plus a plain-text fallback.

Pure functions: no files, no network, no database. Every value that comes
from the web (job titles, companies, reasons) is HTML-escaped, and links are
only created for http and https URLs.
"""
import html
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

PORTAL_NAMES = {
    "remoteok": "Remote OK", "remotive": "Remotive", "arbeitnow": "Arbeitnow",
    "demo": "Demo portal (sample)", "demo-board": "Demo board (sample)",
}
PORTAL_HOMES = {
    "remoteok": "https://remoteok.com", "remotive": "https://remotive.com",
    "arbeitnow": "https://www.arbeitnow.com",
}
APPLY_LABELS = {
    "applied": "Applied", "dry_run": "Would apply (dry run)",
    "route_email": "Routed to email", "limit_reached": "Daily limit reached",
    "manual_required": "Needs manual action", "failed": "Failed",
    "already_handled": "Already handled",
}
EMAIL_LABELS = {
    "sent": "Sent", "dry_run": "Written, not sent", "failed": "Failed",
    "skipped": "Skipped (credentials)", "no_contact": "No HR contact found",
    "already_contacted": "HR already contacted", "already_handled": "Already handled",
}
MAX_JOB_ROWS = 10
BAR_PIXELS = 120

TH = "text-align:left;padding:6px 8px;border-bottom:2px solid #d4d4d8;font-size:13px;"
TD = "padding:6px 8px;border-bottom:1px solid #e4e4e7;font-size:13px;vertical-align:top;"


def esc(value: Any) -> str:
    """HTML-escape any value (None becomes an empty string)."""
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(url: Any) -> str:
    """Return the URL if it is a plain http(s) link, otherwise an empty string."""
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    return text if parts.scheme in ("http", "https") and parts.netloc else ""


def link(url: Any, label: Any) -> str:
    """An anchor for safe URLs; plain escaped text otherwise."""
    target = safe_url(url)
    if not target:
        return esc(label)
    return f'<a href="{esc(target)}" style="color:#4338ca;">{esc(label)}</a>'


def portal_name(portal: Any) -> str:
    return PORTAL_NAMES.get(portal or "", str(portal or "unknown"))


def format_duration(seconds: float) -> str:
    """5.4 -> '5.4 s', 75 -> '1 min 15 s', 3725 -> '1 h 2 min'."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """A table; row cells must already be escaped HTML."""
    head = "".join(f'<th style="{TH}">{esc(h)}</th>' for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td style="{TD}">{cell}</td>' for cell in row) + "</tr>"
        for row in rows)
    return (f'<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="border-collapse:collapse;"><tr>{head}</tr>{body}</table>')


def section(title: str, content: str) -> str:
    return f'<h2 style="font-size:16px;margin:22px 0 8px;">{esc(title)}</h2>{content}'


def _job_rows(jobs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for job in jobs[:MAX_JOB_ROWS]:
        sample = " (sample)" if job.get("sample") else ""
        rows.append([
            f"{float(job.get('relevance_score') or 0):.1f}",
            link(job.get("url"), job.get("title") or "Untitled") + sample,
            esc(job.get("company")),
            esc(portal_name(job.get("portal"))),
            "Gemini" if job.get("score_source") == "gemini" else "Keyword fallback",
            esc(job.get("score_reason")),
        ])
    return rows


def _apply_rows(results: list[dict[str, Any]]) -> list[list[str]]:
    return [[link(r.get("url"), r.get("title") or "Untitled"),
             esc(portal_name(r.get("portal"))),
             esc(APPLY_LABELS.get(r.get("apply_status", ""), r.get("apply_status", "")))]
            for r in results]


def _email_rows(records: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for rec in records:
        to = esc(rec.get("to") or "-")
        delivered = rec.get("delivered_to") or ""
        if delivered and delivered != rec.get("to"):
            to += f"<br><small>delivered to test address {esc(delivered)}</small>"
        rows.append([esc(rec.get("company")), to,
                     esc(EMAIL_LABELS.get(rec.get("status", ""), rec.get("status", ""))),
                     esc(rec.get("subject"))])
    return rows


def _trend(trend: list[dict[str, Any]]) -> str:
    days = list(reversed(trend[:7]))               # database returns newest first
    peak = max((int(d["total_applied"]) + int(d["total_emailed"]) for d in days), default=0)
    rows = []
    for day in days:
        activity = int(day["total_applied"]) + int(day["total_emailed"])
        width = round(BAR_PIXELS * activity / peak) if peak else 0
        bar = f'<div style="background:#6366f1;height:10px;width:{width}px;"></div>'
        rows.append([esc(day["date"]), str(int(day["total_scraped"])),
                     str(int(day["total_applied"])), str(int(day["total_emailed"])),
                     str(int(day["total_failed"])), bar])
    return table(["Date", "Scraped", "Applied", "Emailed", "Failed", "Applied + emailed"], rows)


def render_report(state: dict[str, Any], trend: list[dict[str, Any]], *,
                  now: datetime | None = None,
                  test_redirect: bool = False) -> tuple[str, str, str]:
    """Return (subject, html, plain text) for one finished run."""
    now = now or datetime.now()
    dry_run = bool(state.get("dry_run", True))
    date = now.strftime("%Y-%m-%d")
    duration = format_duration(now.timestamp() - state.get("start_time", now.timestamp()))
    errors = state.get("errors", [])
    applied = int(state.get("total_applied", 0))
    emailed = int(state.get("total_emailed", 0)) + int(state.get("drip_emails_sent", 0))

    metrics = [
        ("Jobs scraped", state.get("total_scraped", 0)),
        ("New after filtering", state.get("total_new", 0)),
        ("Shortlisted", len(state.get("jobs_to_apply", []))),
        ("Applications", state.get("total_applied", 0)),
        ("Application failures", state.get("total_failed", 0)),
        ("Cold emails", state.get("total_emailed", 0)),
        ("HR drip emails", state.get("drip_emails_sent", 0)),
        ("Warnings and errors", len(errors)),
        ("Run time", duration),
    ]
    badge_color, badge = ("#b45309", "DRY RUN") if dry_run else ("#15803d", "LIVE")
    notes = []
    if dry_run:
        notes.append("Dry run: no applications or emails were sent. The tables show what "
                     "would have happened.")
    if test_redirect:
        notes.append("Emails are redirected to a test address, not to the companies.")

    parts = [
        f'<h1 style="font-size:20px;margin:0 0 4px;">JobSherpa daily report</h1>'
        f'<p style="margin:0 0 12px;color:#52525b;">{esc(date)} &nbsp; '
        f'<span style="background:{badge_color};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:12px;">{badge}</span></p>',
        "".join(f'<p style="margin:0 0 8px;color:#52525b;">{esc(n)}</p>' for n in notes),
        section("Summary", table(["Metric", "Value"],
                                 [[esc(label), esc(value)] for label, value in metrics])),
    ]
    scored = state.get("scored_jobs", [])
    if scored:
        parts.append(section("Top scored jobs", table(
            ["Score", "Job", "Company", "Source", "Scored by", "Why"], _job_rows(scored))))
    if state.get("application_results"):
        parts.append(section("Applications", table(
            ["Job", "Source", "Result"], _apply_rows(state["application_results"]))))
    if state.get("emails_sent"):
        parts.append(section("Cold emails", table(
            ["Company", "To", "Status", "Subject"], _email_rows(state["emails_sent"]))))
    if errors:
        parts.append(section("Warnings and errors", "<ul>" + "".join(
            f"<li>{esc(e)}</li>" for e in errors) + "</ul>"))
    if trend:
        parts.append(section("Last 7 days", _trend(trend)))

    used = {j.get("portal") for j in [*state.get("raw_jobs", []), *scored]}
    credits = ", ".join(link(PORTAL_HOMES[p], PORTAL_NAMES[p]) for p in PORTAL_HOMES if p in used)
    if credits:
        parts.append(f'<p style="margin-top:24px;color:#71717a;font-size:12px;">'
                     f'Job listings come from {credits}. Each job title links to the original '
                     f'posting.</p>')

    page = ('<!DOCTYPE html><html><body style="margin:0;padding:16px;background:#f4f4f5;'
            'font-family:Arial,Helvetica,sans-serif;color:#18181b;">'
            '<div style="max-width:720px;margin:0 auto;background:#ffffff;padding:20px;'
            f'border-radius:8px;">{"".join(parts)}</div></body></html>')

    lines = [f"JobSherpa daily report - {date} ({badge})", "",
             *[f"{label}: {value}" for label, value in metrics]]
    if errors:
        lines += ["", "Warnings and errors:", *[f"- {e}" for e in errors]]
    subject = f"JobSherpa report {date}: {applied} applied, {emailed} emailed"
    if dry_run:
        subject += " [DRY RUN]"
    return subject, page, "\n".join(lines)