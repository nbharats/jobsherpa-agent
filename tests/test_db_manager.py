"""Smoke tests for db_manager using a temporary database."""
import tempfile
from pathlib import Path

import config
from database import db_manager as db


def expect_error(func, *args, **kwargs):
    """Fail the test unless func raises ValueError."""
    try:
        func(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"{func.__name__} accepted invalid input")


def run():
    with tempfile.TemporaryDirectory() as tmp:
        config.DATABASE_PATH = Path(tmp) / "test.db"
        db.init_db()

        # URL validation and hashing
        expect_error(db.make_job_hash, "")
        expect_error(db.make_job_hash, "example.com/jobs/1")
        plain = db.make_job_hash("https://example.com/jobs/1")
        messy = db.make_job_hash("HTTPS://Example.com/jobs/1/?utm_source=x#top")
        assert plain == messy and len(plain) == 16
        assert (db.make_job_hash("https://x.com/v?currentJobId=1")
                != db.make_job_hash("https://x.com/v?currentJobId=2"))

        # Inserting jobs
        job = {"url": "https://example.com/jobs/1", "title": "Python Developer",
               "company": "Acme", "location": "Remote", "portal": "remoteok",
               "description": "Build APIs"}
        assert db.insert_job(job) is True
        assert db.insert_job(job) is False            # duplicate rejected
        expect_error(db.insert_job, {"title": "No URL"})
        assert db.job_exists(plain)

        # Updates report whether anything changed
        assert db.update_job_score(plain, 8.5) is True
        assert db.update_job_score("missing", 1) is False
        assert db.update_job_status("missing", "applied") is False
        expect_error(db.update_job_status, plain, "banana")

        # Claim workflow and crash recovery
        assert db.claim_job(plain) is True
        assert db.claim_job(plain) is False           # second claim loses
        assert db.recover_interrupted_jobs() == 1
        assert db.get_job(plain)["status"] == "manual_required"
        assert db.claim_job(plain) is False           # no longer 'new'

        # Normal completion path
        second = {"url": "https://example.com/jobs/2", "title": "Flask Dev"}
        db.insert_job(second)
        h2 = db.make_job_hash(second["url"])
        assert db.claim_job(h2) is True
        assert db.update_job_status(h2, "applied", notes="ok") is True
        saved = db.get_job(h2)
        assert saved["status"] == "applied" and saved["applied_date"]
        assert db.get_job(plain)["relevance_score"] == 8.5

        # HR contacts: email and domain are normalised
        assert db.add_hr_contact("Acme", "acme.com", "hr@acme.com") is True
        assert db.add_hr_contact("Acme", "ACME.com", " HR@Acme.com ") is False
        expect_error(db.add_hr_contact, "Acme", "acme.com", "not-an-email")
        assert len(db.get_hr_contacts("acme.com")) == 1

        # Daily stats accumulate across runs
        db.add_daily_stats("2026-11-20", scraped=10, new=4)
        db.add_daily_stats("2026-11-20", scraped=12, new=5, applied=2)
        stats = db.get_recent_stats()
        assert len(stats) == 1
        assert stats[0]["total_scraped"] == 22 and stats[0]["total_new"] == 9
        assert stats[0]["total_applied"] == 2

        # Drip campaign
        assert db.add_drip_contact("a@x.com") is True
        assert db.add_drip_contact("A@X.com") is False   # case-insensitive dup
        db.add_drip_contact("b@x.com")
        assert len(db.get_next_drip_batch(5)) == 2
        assert db.mark_drip_result("a@x.com", "sent") is True
        assert db.mark_drip_result("nobody@x.com", "sent") is False
        assert [r["email"] for r in db.get_next_drip_batch(5)] == ["b@x.com"]
        expect_error(db.mark_drip_result, "b@x.com", "banana")

    print("All database checks passed.")


if __name__ == "__main__":
    run()