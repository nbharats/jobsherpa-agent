"""Flask dashboard (F8): overview, jobs, emails, logs and the latest daily report.

Local, read-only monitoring. It listens on 127.0.0.1 by default and never
displays secrets. Everything shown comes from SQLite, the log file and the
saved report. Jinja escapes all text, and job links are only rendered for
http(s) URLs. The same data is available as JSON under /api, which the React
interface (served at /app) uses.
"""
import logging
import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import config
from agent.report import portal_name, safe_url
from dashboard import data
from database import db_manager as db

logger = logging.getLogger(__name__)

ROW_LIMIT = 300
REACT_DIR = Path(__file__).resolve().parent / "static" / "react"


def _when(value: Any) -> str:
    return str(value or "")[:16].replace("T", " ")


def job_view(row: dict[str, Any]) -> dict[str, Any]:
    """The fields the pages and the JSON API show for one job (no description)."""
    return {
        "score": float(row.get("relevance_score") or 0),
        "title": row.get("job_title") or "Untitled",
        "link": safe_url(row.get("job_url")),
        "company": row.get("company") or "",
        "portal": portal_name(row.get("portal")),
        "portal_key": row.get("portal") or "",
        "status": row.get("status") or "",
        "date": _when(row.get("applied_date") or row.get("created_at")),
        "hr_email": row.get("hr_email") or "",
        "notes": row.get("notes") or "",
    }


def _job_filters() -> tuple[str, str]:
    status = request.args.get("status", "")
    portal = request.args.get("portal", "").strip()
    return (status if status in db.JOB_STATUSES else ""), portal


def _log_params() -> tuple[str, int]:
    level = request.args.get("level", "ALL").upper()
    if level not in data.LOG_LEVELS:
        level = "ALL"
    try:
        lines = min(1000, max(10, int(request.args.get("lines", 200))))
    except ValueError:
        lines = 200
    return level, lines


def _emails_payload() -> dict[str, list[dict[str, Any]]]:
    cold = [job_view(r) for r in db.list_emailed_jobs(ROW_LIMIT)]
    drip = [{"email": r["email"], "name": r.get("name") or "", "company": r.get("company") or "",
             "designation": r.get("designation") or "", "status": r["status"],
             "date": _when(r.get("sent_date"))} for r in db.list_drip_history(ROW_LIMIT)]
    return {"cold": cold, "drip": drip}


def create_app() -> Flask:
    """Build the dashboard app (creates the database tables if they are missing)."""
    app = Flask(__name__)
    db.init_db()

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    @app.errorhandler(sqlite3.Error)
    def database_error(exc: Exception):
        logger.exception("Dashboard database error")
        return render_template("error.html", page="", message="The database could not be read."), 500

    # ----- server-rendered pages -----

    @app.get("/")
    def overview():
        return render_template("overview.html", page="overview", o=data.build_overview())

    @app.get("/jobs")
    def jobs():
        status, portal = _job_filters()
        rows = db.get_jobs(status=status or None, portal=portal or None, limit=ROW_LIMIT)
        return render_template(
            "jobs.html", page="jobs", rows=[job_view(r) for r in rows],
            statuses=sorted(db.JOB_STATUSES), portals=db.list_portals(),
            status=status, portal=portal, limit=ROW_LIMIT)

    @app.get("/emails")
    def emails():
        return render_template("emails.html", page="emails", **_emails_payload())

    @app.get("/logs")
    def logs():
        level, lines = _log_params()
        return render_template("logs.html", page="logs", rows=data.read_log_tail(lines, level),
                               level=level, levels=data.LOG_LEVELS, lines=lines)

    @app.get("/report")
    def report():
        return render_template("report.html", page="report",
                               has_report=data.latest_report() is not None)

    @app.get("/report/latest")
    def report_latest():
        html = data.latest_report()
        if html is None:
            return Response("No report yet.", status=404, mimetype="text/plain")
        response = Response(data.with_new_tab_links(html), mimetype="text/html")
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
        return response

    # ----- JSON API -----

    @app.get("/api/overview")
    def api_overview():
        return jsonify(data.build_overview())

    @app.get("/api/jobs")
    def api_jobs():
        status, portal = _job_filters()
        rows = db.get_jobs(status=status or None, portal=portal or None, limit=ROW_LIMIT)
        return jsonify([job_view(r) for r in rows])

    @app.get("/api/meta")
    def api_meta():
        return jsonify({"statuses": sorted(db.JOB_STATUSES), "portals": db.list_portals()})

    @app.get("/api/emails")
    def api_emails():
        return jsonify(_emails_payload())

    @app.get("/api/logs")
    def api_logs():
        level, lines = _log_params()
        return jsonify({"level": level, "lines": data.read_log_tail(lines, level)})

    @app.get("/api/report")
    def api_report():
        return jsonify({"available": data.latest_report() is not None})

    # ----- React interface -----

    @app.get("/app")
    def react_app():
        if not (REACT_DIR / "index.html").is_file():
            return Response(
                "The React interface is not built yet. Run: cd dashboard/frontend; "
                "npm install; npm run build",
                status=404, mimetype="text/plain")
        return send_from_directory(REACT_DIR, "index.html", max_age=0)

    return app


def run_dashboard() -> None:
    """Start the dashboard server (blocking)."""
    app = create_app()
    logger.info("Dashboard running at http://%s:%s (React interface at /app, Ctrl+C to stop)",
                config.DASHBOARD_HOST, config.DASHBOARD_PORT)
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, debug=False)


if __name__ == "__main__":
    from logging_setup import setup_logging

    setup_logging()
    run_dashboard()