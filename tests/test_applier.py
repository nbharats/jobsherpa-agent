"""Tests for the Playwright applier: field planning (pure) and real browser runs.

The browser tests are skipped automatically if Chromium is not installed.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import config
from tools import applier
from tools.applier import (ApplyError, ApplyUncertain, ApplyUnsupported,
                           apply_to_job, form_values, plan_field)

PROFILE = {"personal_info": {"name": "Test User", "email": "test@example.com",
                             "phone": "+91-9999999999",
                             "linkedin": "https://linkedin.com/in/test",
                             "github": "", "portfolio": ""}}
VALUES = form_values(PROFILE, Path("resume.pdf"), "Cover text")
DEMO_FORM = Path(config.BASE_DIR) / "demo" / "apply_form.html"


def field(**kwargs):
    base = dict(tag="input", type="text", name="", id="", placeholder="",
                aria="", label="", required=False)
    return {**base, **kwargs}


def test_plan_field():
    assert plan_field(field(name="full_name"), VALUES) == ("fill", "Test User")
    assert plan_field(field(name="first_name"), VALUES) == ("fill", "Test")
    assert plan_field(field(name="last_name"), VALUES) == ("fill", "User")
    assert plan_field(field(type="email", name="x"), VALUES) == ("fill", "test@example.com")
    assert plan_field(field(label="Your e-mail address"), VALUES) == ("fill", "test@example.com")
    assert plan_field(field(type="tel", name="x"), VALUES) == ("fill", "+91-9999999999")
    assert plan_field(field(name="linkedin"), VALUES) == ("fill", "https://linkedin.com/in/test")
    assert plan_field(field(type="file", name="resume"), VALUES) == ("upload", "resume.pdf")
    assert plan_field(field(tag="textarea", name="cover_letter"), VALUES) == ("fill", "Cover text")
    assert plan_field(field(name="github"), VALUES) is None            # empty in the profile
    assert plan_field(field(name="company_name"), VALUES) is None      # not the candidate's name
    assert plan_field(field(name="username"), VALUES) is None
    assert plan_field(field(type="checkbox", name="terms"), VALUES) is None
    assert plan_field(field(tag="select", type="select-one", name="country"), VALUES) is None
    assert plan_field(field(name="favorite_color"), VALUES) is None


def test_preconditions():
    with tempfile.TemporaryDirectory() as tmp:
        resume = Path(tmp) / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4 test")
        try:
            apply_to_job({}, PROFILE, resume)
        except ApplyUnsupported:
            pass
        else:
            raise AssertionError("a job without an apply URL was accepted")
        try:
            apply_to_job({"apply_url": "file:///x.html"}, PROFILE, Path(tmp) / "missing.pdf")
        except ApplyError as exc:
            assert "resume" in str(exc) and not isinstance(exc, ApplyUnsupported)
        else:
            raise AssertionError("a missing resume was accepted")


def browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


def page_url(tmp: str, html: str) -> str:
    path = Path(tmp) / "page.html"
    path.write_text(html, encoding="utf-8")
    return path.as_uri()


def run_page(url: str, tmp: str) -> None:
    resume = Path(tmp) / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    with patch.object(applier, "ACTION_DELAY", (0, 0)), \
            patch.object(applier, "CONFIRM_TIMEOUT_MS", 1500):
        apply_to_job({"apply_url": url}, PROFILE, resume, cover_letter="Hello", headless=True)


def test_browser_applies_to_demo_form():
    with tempfile.TemporaryDirectory() as tmp:
        run_page(DEMO_FORM.resolve().as_uri(), tmp)            # no exception means success


def test_browser_stops_on_unknown_required_field():
    html = '<form><input name="favorite_color" required><button type="submit">Send</button></form>'
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run_page(page_url(tmp, html), tmp)
        except ApplyUnsupported as exc:
            assert "favorite_color" in str(exc)
        else:
            raise AssertionError("an unknown required field was not rejected")


def test_browser_reports_uncertain_outcome():
    html = ('<form onsubmit="return false"><input name="email" type="email" required>'
            '<button type="submit">Send</button></form>')
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run_page(page_url(tmp, html), tmp)
        except ApplyUncertain:
            pass
        else:
            raise AssertionError("a missing confirmation was not reported")


def test_browser_no_form():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run_page(page_url(tmp, "<p>Nothing here</p>"), tmp)
        except ApplyUnsupported as exc:
            assert "no application form" in str(exc)
        else:
            raise AssertionError("a page without a form was accepted")


PURE = [test_plan_field, test_preconditions]
BROWSER = [test_browser_applies_to_demo_form, test_browser_stops_on_unknown_required_field,
           test_browser_reports_uncertain_outcome, test_browser_no_form]


def run() -> None:
    for test in PURE:
        test()
        print(f"PASS  {test.__name__}")
    if not browser_available():
        print("SKIP  browser tests: run 'playwright install chromium' first")
        return
    for test in BROWSER:
        test()
        print(f"PASS  {test.__name__}")
    print("All applier checks passed.")


if __name__ == "__main__":
    run()