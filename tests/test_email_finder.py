"""Offline tests for the email finder (fake DNS, temporary database)."""
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from database import db_manager as db
from tools.email_finder import (domain_candidates, find_hr_emails, has_mx,
                                is_valid_email, role_addresses)

PARTS = ["hr", "careers", "jobs"]


@contextmanager
def temp_db():
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"), \
            patch.object(config, "HR_EMAIL_LOCALPARTS", PARTS):
        db.init_db()
        yield


def mx_for(*domains: str):
    """Fake DNS: only the given domains have MX records."""
    return lambda domain: ["mx." + domain + "."] if domain in domains else []


def test_domain_candidates():
    assert domain_candidates("Acme Inc.") == ["acme.com", "acme.io"]
    assert domain_candidates("Dawn Technology") == [
        "dawntechnology.com", "dawn-technology.com", "dawntechnology.io"]
    assert domain_candidates("Café Corp") == ["cafe.com", "cafe.io"]   # accents and suffix
    for useless in ("Unknown", "", "AB", "Co", "   "):
        assert domain_candidates(useless) == []


def test_has_mx():
    assert has_mx("acme.com", lambda d: ["mx.acme.com."]) is True
    assert has_mx("acme.com", lambda d: []) is False
    assert has_mx("acme.com", lambda d: ["."]) is False                # null MX refuses mail


def test_email_syntax():
    assert is_valid_email("hr@acme.com") is True
    assert is_valid_email("a b@acme.com") is False
    assert is_valid_email("hr@@acme.com") is False
    with patch.object(config, "HR_EMAIL_LOCALPARTS", ["hr", "bad part"]):
        assert role_addresses("acme.com") == ["hr@acme.com"]           # invalid one dropped


def test_find_and_cache():
    with temp_db():
        found = find_hr_emails("Acme Inc", mx_lookup=mx_for("acme.com"))
        assert [c["email"] for c in found] == [
            "hr@acme.com", "careers@acme.com", "jobs@acme.com"]
        assert all(c["source"] == "pattern" and c["verified"] is False for c in found)
        assert len(db.get_hr_contacts("acme.com")) == 3

        def forbidden(domain):
            raise AssertionError("DNS was used although the contacts are cached")

        again = find_hr_emails("Acme Inc", mx_lookup=forbidden)
        assert [c["email"] for c in again] == [c["email"] for c in found]


def test_tries_next_domain_candidate():
    with temp_db():
        found = find_hr_emails("Beta Labs", mx_lookup=mx_for("beta-labs.com"))
        assert found[0]["email"] == "hr@beta-labs.com"
        assert found[0]["domain"] == "beta-labs.com"


def test_no_mx_means_no_contacts():
    with temp_db():
        assert find_hr_emails("Nowhere Ltd", mx_lookup=mx_for()) == []
        assert db.get_hr_contacts("nowhere.com") == []


def test_explicit_domain_and_dry_run_saving():
    with temp_db():
        found = find_hr_emails("Whatever", domain=" ACME.com ", mx_lookup=mx_for("acme.com"),
                               save=False)
        assert found and found[0]["email"] == "hr@acme.com"
        assert db.get_hr_contacts("acme.com") == []                    # save=False wrote nothing


TESTS = [
    test_domain_candidates,
    test_has_mx,
    test_email_syntax,
    test_find_and_cache,
    test_tries_next_domain_candidate,
    test_no_mx_means_no_contacts,
    test_explicit_domain_and_dry_run_saving,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All email finder checks passed.")


if __name__ == "__main__":
    run()