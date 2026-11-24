"""All SQLite operations for JobSherpa live in this module."""
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import config

HASH_LENGTH = 16

JOB_STATUSES = {"new", "in_progress", "applied", "email_sent", "failed", "manual_required"}
DRIP_STATUSES = {"pending", "sent", "invalid", "failed"}
TRACKING_PARAMS = {"fbclid", 'gclid', "ref", "refid", "trackingid"}
EMAIL_KINDS = {"cold", "drip"}
EMAIL_LOG_STATUSES = {"sent", "failed"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_hash TEXT UNIQUE NOT NULL,
    job_url TEXT,
    job_title TEXT,
    company TEXT,
    location TEXT,
    portal TEXT,
    job_description TEXT,
    status TEXT DEFAULT 'new',
    applied_date TEXT,
    hr_email TEXT,
    email_sent INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP 
);

CREATE TABLE IF NOT EXISTS hr_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    company_domain TEXT,
    hr_name TEXT,
    hr_email TEXT,
    source TEXT,
    verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_domain, hr_email)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_scraped INTEGER DEFAULT 0,
    total_new INTEGER DEFAULT 0,
    total_applied INTEGER DEFAULT 0,
    total_emailed INTEGER DEFAULT 0,
    total_failed INTEGER DEFAULT 0,
    run_time_seconds REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS drip_outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    company TEXT,
    designation TEXT,
    sent_date TEXT,
    status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS email_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
    kind TEXT,
    recipient TEXT,
    delivered_to TEXT,
    company TEXT,
    subject TEXT,
    body TEXT,
    status TEXT,
    job_hash TEXT
);
"""

#---------------- helpers -------------------

@contextmanager
def get_connection():
    """Open a connection; commit on success, roll back on any error."""
    db_path = Path(config.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Create all tables if they do not already exist."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)

def _now():
    return datetime.now().isoformat(timespec='seconds')

def normalise_url(url):
    """Validate a job URL and strip noise that does not identify the job."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError('Job URL cannot be empty.')
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
        raise ValueError(f'Job URL must start with http:// or https://: {url!r}')
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip('/'),
        urlencode(query),
        "",
    ))

def make_job_hash(url):
    """Return the first 16 hex chars of the SHA-256 of the normalised URL."""
    return hashlib.sha256(normalise_url(url).encode("utf-8")).hexdigest()[:HASH_LENGTH]

def normalise_email(email):
    """Trim and lowercase an email; reject obviously invalid values."""
    cleaned = email.strip().lower() if isinstance(email, str) else ""
    if not cleaned or "@" not in cleaned:
        raise ValueError(f'Invalid email: {email!r}')
    return cleaned

#---------------- applications -------------------

def insert_job(job):
    """Insert a new job. Returns True if added, False if it already existed."""
    url = normalise_url(job.get("url"))
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO applications
               (job_hash, job_url, job_title, company, location, portal, job_description)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (make_job_hash(url), url, job.get("title"), job.get("company"),
             job.get("location"), job.get("portal"), job.get("description")),
        )
        return cur.rowcount == 1

def job_exists(job_hash):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM applications WHERE job_hash = ?",(job_hash,)
        ).fetchone()
        return row is not None

def get_job(job_hash):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE job_hash = ?", (job_hash,)
        ).fetchone()
        return dict(row) if row else None

def count_jobs_by_status() -> dict[str, int]:
    """Return {status: number of jobs} for every status in the table."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

def update_job_score(job_hash, score):
    """Returns True if a job was updated, False if the hash is unknown."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE applications SET relevance_score = ? WHERE job_hash = ?",
            (score, job_hash),
        )
        return cur.rowcount > 0

def update_job_status(job_hash, status, hr_email=None, notes=None):
    """Set a job's status, stamping the date or email flag where relevant.

    Returns True if a job was updated, False if the hash is unknown.
    """
    if status not in JOB_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if hr_email is not None:
        hr_email = normalise_email(hr_email)
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE applications
               SET status = ?,
                   applied_date = CASE WHEN ? IN ('applied', 'email_sent') THEN ? ELSE applied_date END,
                   email_sent = CASE WHEN ? = 'email_sent' THEN 1 ELSE email_sent END,
                   hr_email = COALESCE(?, hr_email),
                   notes = COALESCE(?, notes)
               WHERE job_hash = ?""",
            (status, status, _now(), status, hr_email, notes, job_hash),
        )
        return cur.rowcount > 0

def claim_job(job_hash):
    """Atomically move a job from 'new' to 'in_progress'.

    Call this BEFORE applying or emailing. Only the caller that wins the
    claim may act, so a job is never attempted twice.
    """
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE applications SET status = 'in_progress' "
            "WHERE job_hash = ? AND status = 'new'",
            (job_hash,),
        )
        return cur.rowcount == 1

def recover_interrupted_jobs():
    """Handle jobs left 'in_progress' by a crash.

    We cannot know whether the action went through, so these are never
    retried automatically; they are flagged for a manual check instead.
    Returns the number of jobs flagged.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE applications
               SET status = 'manual_required',
                   notes = COALESCE(notes || ' | ', '') || 'interrupted run: verify manually'
               WHERE status = 'in_progress'"""
        )
        return cur.rowcount
    
def get_jobs(status=None, limit=None, portal=None):
    """Return jobs as dicts, newest first, optionally filtered by status and portal."""
    query = "SELECT * FROM applications"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if portal:
        clauses.append("portal = ?")
        params.append(portal)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    
# ---------- hr_contacts ----------

def add_hr_contact(company, company_domain, hr_email, hr_name=None,
                   source="pattern", verified=False):
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO hr_contacts
               (company, company_domain, hr_name, hr_email, source, verified)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (company, company_domain.strip().lower(), hr_name,
             normalise_email(hr_email), source, int(verified)),
        )
        return cur.rowcount == 1


def get_hr_contacts(company_domain):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM hr_contacts WHERE company_domain = ? ORDER BY id",
            (company_domain.strip().lower(),),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- daily_stats ----------

def add_daily_stats(day, scraped=0, new=0, applied=0, emailed=0,
                    failed=0, run_time_seconds=0.0):
    """Add one run's numbers to the totals for a YYYY-MM-DD date.

    Several runs on the same day accumulate instead of overwriting.
    """
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO daily_stats
               (date, total_scraped, total_new, total_applied,
                total_emailed, total_failed, run_time_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   total_scraped = daily_stats.total_scraped + excluded.total_scraped,
                   total_new = daily_stats.total_new + excluded.total_new,
                   total_applied = daily_stats.total_applied + excluded.total_applied,
                   total_emailed = daily_stats.total_emailed + excluded.total_emailed,
                   total_failed = daily_stats.total_failed + excluded.total_failed,
                   run_time_seconds = daily_stats.run_time_seconds + excluded.run_time_seconds""",
            (day, scraped, new, applied, emailed, failed, run_time_seconds),
        )


def get_recent_stats(days=7):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- drip_outreach ----------

def add_drip_contact(email, name=None, company=None, designation=None):
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO drip_outreach (email, name, company, designation)
               VALUES (?, ?, ?, ?)""",
            (normalise_email(email), name, company, designation),
        )
        return cur.rowcount == 1


def get_next_drip_batch(limit):
    """Return up to `limit` contacts still pending, in insertion order."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM drip_outreach WHERE status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_drip_result(email, status):
    """Returns True if a contact was updated, False if the email is unknown."""
    if status not in DRIP_STATUSES:
        raise ValueError(f"Invalid drip status: {status}")
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE drip_outreach
               SET status = ?,
                   sent_date = CASE WHEN ? = 'sent' THEN ? ELSE sent_date END
               WHERE email = ?""",
            (status, status, _now(), normalise_email(email)),
        )
        return cur.rowcount > 0

def get_drip_contact(email):
    """Return a drip contact as a dict, or None if the email is unknown."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM drip_outreach WHERE email = ?", (normalise_email(email),)
        ).fetchone()
        return dict(row) if row else None


def hr_email_already_contacted(hr_email):
    """True if this address already received a cold email or a drip email."""
    address = normalise_email(hr_email)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM applications WHERE hr_email = ? AND email_sent = 1 LIMIT 1",
            (address,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT 1 FROM drip_outreach WHERE email = ? AND status = 'sent' LIMIT 1",
                (address,),
            ).fetchone()
        return row is not None

# ---------- daily_stats totals and the email log ----------

def get_stats_totals():
    """Return all-time sums over daily_stats, plus the number of days recorded."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(total_scraped), 0) AS total_scraped,
                      COALESCE(SUM(total_new), 0) AS total_new,
                      COALESCE(SUM(total_applied), 0) AS total_applied,
                      COALESCE(SUM(total_emailed), 0) AS total_emailed,
                      COALESCE(SUM(total_failed), 0) AS total_failed,
                      COALESCE(SUM(run_time_seconds), 0) AS run_time_seconds,
                      COUNT(*) AS days
               FROM daily_stats"""
        ).fetchone()
        return dict(row)


def log_email(kind, recipient, delivered_to, company, subject, body, status, job_hash=None):
    """Record one real send attempt for the dashboard's Emails view."""
    if kind not in EMAIL_KINDS:
        raise ValueError(f"Invalid email kind: {kind}")
    if status not in EMAIL_LOG_STATUSES:
        raise ValueError(f"Invalid email status: {status}")
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO email_log
               (kind, recipient, delivered_to, company, subject, body, status, job_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, (recipient or "").strip(), (delivered_to or "").strip(), company,
             subject, body, status, job_hash),
        )
        return cur.lastrowid


def get_email_log(limit=200):
    """Return logged emails as dicts, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM email_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

# ---------- dashboard queries ----------

def list_portals():
    """Every portal name that has at least one job."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT portal FROM applications WHERE portal IS NOT NULL ORDER BY portal"
        ).fetchall()
        return [r["portal"] for r in rows]


def get_totals():
    """All-time sums from daily_stats, plus how many days are recorded."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(total_scraped), 0) AS scraped,
                      COALESCE(SUM(total_new), 0) AS new,
                      COALESCE(SUM(total_applied), 0) AS applied,
                      COALESCE(SUM(total_emailed), 0) AS emailed,
                      COALESCE(SUM(total_failed), 0) AS failed,
                      COUNT(*) AS days
               FROM daily_stats"""
        ).fetchone()
        return dict(row)


def get_stats_for(day):
    """The daily_stats row for a YYYY-MM-DD date, or None."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (day,)).fetchone()
        return dict(row) if row else None


def list_emailed_jobs(limit=200):
    """Jobs that received a cold email, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE email_sent = 1 ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_drip_history(limit=200):
    """HR drip contacts that are no longer pending, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM drip_outreach WHERE status != 'pending' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
