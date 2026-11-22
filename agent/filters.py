"""Rule-based job filtering used by the filter node.

Rules run cheapest first, and every list is configurable in .env:
1. BLACKLIST_KEYWORDS: reject if the title contains any (whole words only).
2. SENIOR_KEYWORDS: reject senior-level titles.
3. MAX_YEARS_REQUIRED: reject if the description asks for more years.
4. WHITELIST_KEYWORDS: require a target keyword in the title or tags.
   An empty whitelist disables this rule.
"""
import re
from typing import Any

import config

Job = dict[str, Any]

_YEARS_PATTERNS = (
    re.compile(
        r"(\d{1,2})\s*(?:[-–]\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b[^.\n]{0,40}?experience",
        re.IGNORECASE,
    ),
    re.compile(
        r"experience[^.\n]{0,30}?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",
        re.IGNORECASE,
    ),
)


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a whole-word, case-insensitive pattern for a keyword.

    The lookarounds stop 'java' matching inside 'javascript' and 'ml'
    inside 'html', while keeping 'c++' and 'c#' usable. Spaces in a
    phrase also match hyphens, so 'full stack' finds 'Full-Stack'.
    """
    body = r"[\s\-]+".join(re.escape(part) for part in keyword.split())
    return re.compile(rf"(?<![a-z0-9+#]){body}(?![a-z0-9+#])", re.IGNORECASE)


def find_keyword(text: str, keywords: list[str]) -> str | None:
    """Return the first keyword found in text as a whole word, else None."""
    for keyword in keywords:
        if keyword.strip() and keyword_pattern(keyword).search(text):
            return keyword
    return None


def required_years(text: str) -> int | None:
    """Return the highest minimum years of experience the text asks for.

    For a range like '3-5 years' the lower bound counts. Returns None if
    the text states no requirement.
    """
    found = [
        int(match.group(1))
        for pattern in _YEARS_PATTERNS
        for match in pattern.finditer(text or "")
    ]
    return max(found) if found else None


def evaluate_job(job: Job) -> tuple[bool, str]:
    """Return (keep, reason). The reason is empty when the job is kept."""
    title = job.get("title") or ""
    description = job.get("description") or ""
    tags = " ".join(job.get("tags") or [])

    hit = find_keyword(title, config.BLACKLIST_KEYWORDS)
    if hit:
        return False, f"blacklist: {hit}"

    hit = find_keyword(title, config.SENIOR_KEYWORDS)
    if hit:
        return False, f"senior: {hit}"

    years = required_years(description)
    if years is not None and years > config.MAX_YEARS_REQUIRED:
        return False, f"experience: {years}+ years"

    if config.WHITELIST_KEYWORDS and not find_keyword(
        f"{title} {tags}", config.WHITELIST_KEYWORDS
    ):
        return False, "whitelist: no target keyword"

    return True, ""