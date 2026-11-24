"""HR email discovery: a waterfall of cache, API lookup and role-address patterns.

1. Cache: contacts already stored in hr_contacts for the company's domain.
2. API lookup (Apollo / Snov): a placeholder until we confirm their free limits.
3. Pattern generation: role addresses such as hr@ and careers@ on a domain
   that passes an MX check.

Limits: an MX record proves the domain accepts email, NOT that a given
mailbox exists, so pattern addresses can bounce. Domains are guessed from
company names, so a guess can point at an unrelated company with a similar
name. The email node therefore keeps volume low and dry-run is the default.
"""
import logging
import re
import unicodedata
import smtplib
from collections.abc import Callable, Iterable
from functools import lru_cache
from typing import Any
from email.message import EmailMessage

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

import config
from database import db_manager as db

logger = logging.getLogger(__name__)

MXLookup = Callable[[str], Iterable[str]]

LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "gmbh", "ag", "corp", "corporation", "co",
    "plc", "pvt", "private", "sa", "bv", "oy", "ab",
}
UNKNOWN_COMPANIES = {"", "unknown", "n/a", "confidential"}


def domain_candidates(company: str) -> list[str]:
    """Guess likely website domains from a company name, most likely first."""
    folded = unicodedata.normalize("NFKD", company or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", folded.lower())
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    slug = "".join(words)
    if len(slug) < 3 or slug in UNKNOWN_COMPANIES:
        return []
    candidates = [f"{slug}.com"]
    if len(words) > 1:
        candidates.append(f"{'-'.join(words)}.com")
    candidates.append(f"{slug}.io")
    return candidates


@lru_cache(maxsize=512)
def _dns_mx_hosts(domain: str) -> tuple[str, ...]:
    """Return the domain's MX hosts, or () if it has none or DNS fails."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
    except dns.exception.DNSException:
        return ()
    return tuple(str(record.exchange) for record in answers)


def has_mx(domain: str, lookup: MXLookup | None = None) -> bool:
    """True if the domain publishes at least one usable MX record.

    A 'null MX' (a single '.') means the domain explicitly refuses mail.
    """
    hosts = (lookup or _dns_mx_hosts)(domain)
    return any(str(host).strip().strip(".") for host in hosts)


def is_valid_email(address: str) -> bool:
    """Syntax check only; no DNS is used."""
    try:
        validate_email(address, check_deliverability=False)
    except EmailNotValidError:
        return False
    return True


def role_addresses(domain: str) -> list[str]:
    """Role addresses (hr@, careers@, ...) for a domain, syntax-checked."""
    addresses = [f"{part}@{domain}" for part in config.HR_EMAIL_LOCALPARTS]
    return [a for a in addresses if is_valid_email(a)]


def lookup_via_api(company: str, domain: str) -> list[dict[str, Any]]:
    """Waterfall step 1 (Apollo / Snov). Not implemented yet: returns no contacts."""
    if config.APOLLO_API_KEY or config.SNOV_API_KEY:
        logger.info("API email lookup is not implemented yet; using patterns instead")
    return []


def _result(email: str, domain: str, source: str, verified: bool) -> dict[str, Any]:
    return {"email": email, "domain": domain, "source": source, "verified": verified}


def find_hr_emails(
    company: str,
    domain: str | None = None,
    mx_lookup: MXLookup | None = None,
    save: bool = True,
) -> list[dict[str, Any]]:
    """Return candidate HR addresses for a company, best first (empty if none found).

    Pass `domain` when it is known. With save=False the cache is read but
    nothing is written, which is what dry-run uses.
    """
    candidates = [domain.strip().lower()] if domain else domain_candidates(company)
    for candidate in candidates:
        cached = db.get_hr_contacts(candidate)
        if cached:
            return [_result(c["hr_email"], candidate, c["source"], bool(c["verified"]))
                    for c in cached]
        if not has_mx(candidate, mx_lookup):
            logger.info("No MX record for %s; skipping", candidate)
            continue

        found = lookup_via_api(company, candidate)
        if not found:
            found = [_result(a, candidate, "pattern", False) for a in role_addresses(candidate)]
        if not found:
            continue
        if save:
            for contact in found:
                db.add_hr_contact(company, candidate, contact["email"],
                                  source=contact["source"], verified=contact["verified"])
        return found
    return []

def credentials_problem() -> str | None:
    """Return why sending is impossible (missing credentials), or None. No resume needed."""
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        return "Gmail credentials are missing in .env"
    return None


def send_html(recipient: str, subject: str, html: str, text: str) -> None:
    """Send an HTML email with a plain-text alternative and no attachment.

    Used for the daily report. It is never redirected: it goes to the
    recipient given, which is your own REPORT_EMAIL. Raises EmailSendError.
    """
    problem = credentials_problem()
    if problem:
        raise EmailSendError(problem)
    try:
        message = EmailMessage()
        message["From"] = config.GMAIL_ADDRESS
        message["To"] = recipient
        message["Subject"] = " ".join(subject.split())
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                              context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD.replace(" ", ""))
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        raise EmailSendError(f"Could not send the report to {recipient}: {exc}") from exc
    logger.info("Report emailed to %s", recipient)
