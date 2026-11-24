"""Reads the HR contact list used by the drip campaign.

Expected header: name,email,company,designation (extra columns are ignored).
"""
import csv
import logging
from pathlib import Path

from tools.email_finder import is_valid_email

logger = logging.getLogger(__name__)


def read_hr_csv(path: Path) -> tuple[list[dict[str, str]], int]:
    """Return (valid contacts, number of rows skipped for a bad email)."""
    rows: list[dict[str, str]] = []
    skipped = 0
    if not Path(path).is_file():
        return rows, skipped
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = {(key or "").strip().lower(): (value or "").strip()
                   for key, value in raw.items()}
            email = row.get("email", "")
            if not is_valid_email(email):
                skipped += 1
                continue
            rows.append({"email": email.lower(), "name": row.get("name", ""),
                         "company": row.get("company", ""),
                         "designation": row.get("designation", "")})
    return rows, skipped