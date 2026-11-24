"""Playwright applier: fills and submits a simple HTML application form.

Safety rules:
- Nothing is submitted unless every required field can be filled from the
  profile. Unknown required fields, required checkboxes (consent, terms) and
  drop-downs stop the attempt, because a person has to decide those.
- If no confirmation message appears after submitting, the outcome is
  'uncertain' and the job is never retried automatically.
"""
import logging
import random
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ACTION_DELAY = (0.3, 0.9)        # seconds between actions, to look human
NAV_TIMEOUT_MS = 30_000
CONFIRM_TIMEOUT_MS = 10_000
FIELD_SELECTOR = ("form input:not([type=hidden]):not([type=submit]):not([type=button]), "
                  "form textarea, form select")
SUBMIT_SELECTOR = "form button[type=submit], form input[type=submit]"
FIELDS_JS = """els => els.map(e => ({
    tag: e.tagName.toLowerCase(), type: (e.type || '').toLowerCase(),
    name: e.name || '', id: e.id || '', placeholder: e.placeholder || '',
    aria: e.getAttribute('aria-label') || '',
    label: Array.from(e.labels || []).map(l => l.textContent).join(' '),
    required: !!e.required
}))"""
SUCCESS = re.compile(r"thank you|application (was )?(received|submitted|sent)", re.IGNORECASE)
NOT_A_PERSON = ("company", "employer", "organi", "user", "school", "college",
                "university", "referr")


class ApplyError(Exception):
    """The application could not be completed."""


class ApplyUnsupported(ApplyError):
    """Nothing was submitted: the page needs a person (unknown field, no form...)."""


class ApplyUncertain(ApplyError):
    """The form was submitted but no confirmation was seen: verify manually."""


def form_values(profile: dict[str, Any], resume: Path, cover_letter: str) -> dict[str, str]:
    """The values that may be typed into a form, taken from the profile."""
    info = profile.get("personal_info", {})
    full = (info.get("name") or "").strip()
    parts = full.split()
    return {
        "full_name": full,
        "first_name": parts[0] if parts else "",
        "last_name": parts[-1] if len(parts) > 1 else "",
        "email": info.get("email") or "",
        "phone": info.get("phone") or "",
        "linkedin": info.get("linkedin") or "",
        "github": info.get("github") or "",
        "portfolio": info.get("portfolio") or "",
        "cover_letter": cover_letter,
        "resume": str(resume),
    }


def plan_field(attrs: dict[str, Any], values: dict[str, str]) -> tuple[str, str] | None:
    """Decide what to do with one form field: ('fill', text), ('upload', path) or None."""
    kind = attrs.get("type", "")
    text = " ".join(str(attrs.get(k, "")) for k in ("name", "id", "placeholder", "aria", "label")).lower()

    def pick(key: str) -> tuple[str, str] | None:
        return ("fill", values[key]) if values.get(key) else None

    if attrs.get("tag") == "select" or kind in {"checkbox", "radio", "password"}:
        return None
    if kind == "file":
        return ("upload", values["resume"]) if values.get("resume") else None
    if kind == "email" or "email" in text or "e-mail" in text:
        return pick("email")
    if kind == "tel" or any(w in text for w in ("phone", "mobile", "contact number")):
        return pick("phone")
    if "linkedin" in text:
        return pick("linkedin")
    if "github" in text:
        return pick("github")
    if "portfolio" in text or "website" in text:
        return pick("portfolio")
    if any(w in text for w in NOT_A_PERSON):
        return None
    if any(w in text for w in ("first name", "first_name", "firstname", "given name")):
        return pick("first_name")
    if any(w in text for w in ("last name", "last_name", "lastname", "surname", "family name")):
        return pick("last_name")
    if "name" in text:
        return pick("full_name")
    if attrs.get("tag") == "textarea" or any(w in text for w in ("cover", "message", "introduce")):
        return pick("cover_letter")
    return None


def _pause(page: Any) -> None:
    page.wait_for_timeout(random.uniform(*ACTION_DELAY) * 1000)


def _fill_and_submit(page: Any, url: str, values: dict[str, str]) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    _pause(page)
    fields = page.eval_on_selector_all(FIELD_SELECTOR, FIELDS_JS)
    if not fields:
        raise ApplyUnsupported("no application form found on the page")

    locators = page.locator(FIELD_SELECTOR)
    for index, attrs in enumerate(fields):
        plan = plan_field(attrs, values)
        label = attrs.get("name") or attrs.get("id") or attrs.get("type") or "field"
        if plan is None:
            if attrs.get("required"):
                raise ApplyUnsupported(f"required field '{label}' needs a person")
            continue
        action, value = plan
        field = locators.nth(index)
        if action == "upload":
            field.set_input_files(value)
        else:
            field.fill(value)
        _pause(page)

    submit = page.locator(SUBMIT_SELECTOR).first
    if submit.count() == 0:
        raise ApplyUnsupported("no submit button found")
    submit.click()
    try:
        page.get_by_text(SUCCESS).first.wait_for(state="visible", timeout=CONFIRM_TIMEOUT_MS)
    except PlaywrightTimeout as exc:
        raise ApplyUncertain("form submitted but no confirmation appeared") from exc


def apply_to_job(job: dict[str, Any], profile: dict[str, Any], resume_path: Path,
                 cover_letter: str = "", headless: bool = True) -> None:
    """Fill in and submit the job's application form. Raises ApplyError on any problem."""
    url = job.get("apply_url") or ""
    if not url:
        raise ApplyUnsupported("the job has no application URL")
    if not Path(resume_path).is_file():
        raise ApplyError(f"resume not found: {resume_path}")
    values = form_values(profile, Path(resume_path), cover_letter)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ApplyError("Playwright is not installed (pip install playwright; "
                         "playwright install chromium)") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            try:
                _fill_and_submit(browser.new_page(), url, values)
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ApplyError(f"browser error: {exc}") from exc