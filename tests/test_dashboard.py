"""Tests for the dashboard: pages, filters, escaping, logs, report and the JSON API."""
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import config
import main
from dashboard import data
from dashboard.app import create_app
import dashboard.app as dashboard_app
from database import db_manager as db

LOG_LINES = [
    "2026-11-24 19:00:52,591 | INFO     | __main__ | Run started",
    "2026-11-24 19:00:53,000 | WARNING  | agent.llm | Gemini returned 503",
    "2026-11-24 19:00:54,000 | ERROR    | __main__ | <b>boom</b>",
]


@contextmanager
def dashboard_env():
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        root = Path(tmp)
        (root / "logs").mkdir()
        (root / "reports").mkdir()
        stack.enter_context(patch.object(config, "DATABASE_PATH", root / "t.db"))
        stack.enter_context(patch.multiple(config, LOGS_DIR=root / "logs",
                                           REPORTS_DIR=root / "reports"))
        stack.enter_context(patch.object(data, "today_str", lambda: "2026-11-24"))
        yield create_app().test_client(), root


def page(client, url: str) -> str:
    return client.get(url).get_data(as_text=True)


def seed() -> list[str]:
    """Three jobs, two days of stats and two drip contacts. Returns the job hashes."""
    jobs = [
        ("https://remoteok.com/remote-jobs/1", "<script>alert(1)</script> Python Dev",
         "Acme & Co", "remoteok"),
        ("https://demo.jobsherpa.example/jobs/2", "Flask Dev", "Beta", "demo"),
        ("https://remotive.com/remote-jobs/3", "Failed Job", "Gamma", "remotive"),
    ]
    for url, title, company, portal in jobs:
        db.insert_job({"url": url, "title": title, "company": company,
                       "portal": portal, "description": "long text"})
    hashes = [db.make_job_hash(j[0]) for j in jobs]
    db.update_job_score(hashes[0], 9.0)
    db.update_job_status(hashes[0], "email_sent", hr_email="hr@acme.com")
    db.update_job_status(hashes[1], "applied")
    db.update_job_status(hashes[2], "failed")
    with db.get_connection() as conn:                      # an unsafe link must never render
        conn.execute("UPDATE applications SET job_url = ? WHERE job_hash = ?",
                     ("javascript:alert(1)", hashes[2]))
    db.add_daily_stats("2026-11-23", scraped=100, new=5, applied=1, emailed=1, failed=1)
    db.add_daily_stats("2026-11-24", scraped=50, new=3, applied=2, emailed=1, failed=0)
    db.add_drip_contact("asha@acme.com", "Asha", "Acme", "HR")
    db.add_drip_contact("pending@x.com")
    db.mark_drip_result("asha@acme.com", "sent")
    return hashes


def test_empty_database_pages():
    with dashboard_env() as (client, root):
        for path in ("/", "/jobs", "/emails", "/logs", "/report"):
            assert client.get(path).status_code == 200, path
        assert client.get("/report/latest").status_code == 404
        assert "No report yet" in page(client, "/report")
        overview = client.get("/api/overview").get_json()
        assert overview["jobs_tracked"] == 0 and overview["today"]["success_rate"] is None
        assert overview["all_time"]["applied"] == 0 and overview["trend"] == []


def test_overview_numbers():
    with dashboard_env() as (client, root):
        seed()
        overview = client.get("/api/overview").get_json()
        assert overview["today"] == {"scraped": 50, "new": 3, "applied": 2, "emailed": 1,
                                     "failed": 0, "success_rate": 100.0}
        assert overview["all_time"]["scraped"] == 150 and overview["all_time"]["applied"] == 3
        assert overview["all_time"]["success_rate"] == 83.3 and overview["all_time"]["days"] == 2
        assert overview["statuses"] == {"applied": 1, "email_sent": 1, "failed": 1}
        assert overview["jobs_tracked"] == 3
        assert [t["date"] for t in overview["trend"]] == ["2026-11-23", "2026-11-24"]
        html = page(client, "/")
        assert "83.3%" in html and "100.0%" in html


def test_jobs_page_filters_and_escaping():
    with dashboard_env() as (client, root):
        seed()
        html = page(client, "/jobs")
        assert "&lt;script&gt;alert(1)&lt;/script&gt; Python Dev" in html
        assert "<script>alert(1)</script>" not in html            # web text is escaped
        assert "Acme &amp; Co" in html
        assert 'href="javascript:' not in html and "Failed Job" in html
        assert "Flask Dev" in html

        failed = page(client, "/jobs?status=failed")
        assert "Failed Job" in failed and "Flask Dev" not in failed
        demo = page(client, "/jobs?portal=demo")
        assert "Flask Dev" in demo and "Failed Job" not in demo
        everything = page(client, "/jobs?status=banana")           # invalid filter is ignored
        assert "Failed Job" in everything and "Flask Dev" in everything


def test_emails_page_and_action_date():
    with dashboard_env() as (client, root):
        hashes = seed()
        assert db.get_job(hashes[0])["applied_date"]               # email_sent stamps a date
        html = page(client, "/emails")
        assert "hr@acme.com" in html and "Acme &amp; Co" in html
        assert "asha@acme.com" in html and "pending@x.com" not in html


def test_logs_page_levels_and_escaping():
    with dashboard_env() as (client, root):
        (root / "logs" / "jobsherpa.log").write_text("\n".join(LOG_LINES), encoding="utf-8")
        everything = page(client, "/logs")
        assert "Run started" in everything and "Gemini returned 503" in everything
        warnings = page(client, "/logs?level=WARNING")
        assert "Gemini returned 503" in warnings and "Run started" not in warnings
        errors = page(client, "/logs?level=ERROR")
        assert "&lt;b&gt;boom&lt;/b&gt;" in errors and "Run started" not in errors
        assert "Run started" in page(client, "/logs?level=bogus")  # unknown level means all


def test_log_tail_handles_big_files():
    with dashboard_env() as (client, root):
        line = "2026-11-24 19:00:00,000 | INFO     | x | " + "y" * 90
        path = root / "logs" / "jobsherpa.log"
        path.write_text("\n".join(f"{line} {i}" for i in range(8000)), encoding="utf-8")
        assert path.stat().st_size > data.TAIL_BYTES
        tail = data.read_log_tail(3)
        assert len(tail) == 3 and tail[-1].endswith(" 7999")
        assert data.read_log_tail(5, "WARNING") == []
    with dashboard_env():
        assert data.read_log_tail() == []                           # no log file yet


def test_report_pages():
    with dashboard_env() as (client, root):
        (root / "reports" / "latest.html").write_text(
            "<!DOCTYPE html><html><body><p>Hello report</p></body></html>", encoding="utf-8")
        assert "<iframe" in page(client, "/report") and "/report/latest" in page(client, "/report")
        response = client.get("/report/latest")
        body = response.get_data(as_text=True)
        assert "Hello report" in body and '<base target="_blank">' in body
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_api_jobs():
    with dashboard_env() as (client, root):
        seed()
        applied = client.get("/api/jobs?status=applied").get_json()
        assert len(applied) == 1 and applied[0]["title"] == "Flask Dev"
        assert {"title", "company", "portal", "status", "score", "date"} <= set(applied[0])
        assert "job_description" not in applied[0]
        assert len(client.get("/api/jobs").get_json()) == 3


def test_success_rate():
    assert data.success_rate(2, 1, 0) == 100.0
    assert data.success_rate(3, 2, 1) == 83.3
    assert data.success_rate(0, 0, 4) == 0.0
    assert data.success_rate(0, 0, 0) is None


def test_main_dashboard_flag():
    with patch("dashboard.app.run_dashboard") as run, \
            patch.object(main, "setup_logging", lambda: None):
        assert main.main(["--dashboard"]) == 0
        assert run.call_count == 1

def test_api_emails_logs_report_meta():
    with dashboard_env() as (client, root):
        seed()
        (root / "logs" / "jobsherpa.log").write_text("\n".join(LOG_LINES), encoding="utf-8")
        emails = client.get("/api/emails").get_json()
        assert [r["hr_email"] for r in emails["cold"]] == ["hr@acme.com"]
        assert [r["email"] for r in emails["drip"]] == ["asha@acme.com"]
        logs = client.get("/api/logs?level=ERROR").get_json()
        assert logs["level"] == "ERROR" and len(logs["lines"]) == 1 and "boom" in logs["lines"][0]
        assert client.get("/api/logs?level=bogus").get_json()["level"] == "ALL"
        assert client.get("/api/report").get_json() == {"available": False}
        (root / "reports" / "latest.html").write_text("<html></html>", encoding="utf-8")
        assert client.get("/api/report").get_json() == {"available": True}
        meta = client.get("/api/meta").get_json()
        assert "applied" in meta["statuses"]
        assert meta["portals"] == ["demo", "remoteok", "remotive"]


def test_react_route():
    with dashboard_env() as (client, root):
        with patch.object(dashboard_app, "REACT_DIR", root / "missing"):
            response = client.get("/app")
            assert response.status_code == 404
            assert "npm run build" in response.get_data(as_text=True)
        build = root / "react"
        build.mkdir()
        (build / "index.html").write_text("<html>react shell</html>", encoding="utf-8")
        with patch.object(dashboard_app, "REACT_DIR", build):
            assert "react shell" in page(client, "/app")


TESTS = [
    test_empty_database_pages,
    test_overview_numbers,
    test_jobs_page_filters_and_escaping,
    test_emails_page_and_action_date,
    test_logs_page_levels_and_escaping,
    test_log_tail_handles_big_files,
    test_report_pages,
    test_api_jobs,
    test_success_rate,
    test_main_dashboard_flag,
    test_api_emails_logs_report_meta,
    test_react_route,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All dashboard checks passed.")


if __name__ == "__main__":
    run()