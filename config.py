
import json
import os
from pathlib import Path

from dotenv import load_dotenv


# --------------------------------------------------
# Project paths
# --------------------------------------------------


BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"
PROFILE_FILE = BASE_DIR / "user_profile.json"

DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

load_dotenv(ENV_FILE)

DATABASE_PATH = Path(
    os.getenv("DATABASE_PATH", str(DATA_DIR / "jobsherpa.db"))
)

# Load environment variables from .env
load_dotenv(ENV_FILE)


# --------------------------------------------------
# Helper functions
# --------------------------------------------------

def get_bool(name: str, default: bool = False) -> bool:
    """Read a boolean value from an environment variable."""
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"true", "1", "yes", "on"}


def get_int(name: str, default: int) -> int:
    """Read an integer value from an environment variable."""
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must contain a valid integer."
        ) from exc


def get_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable as a list."""
    value = os.getenv(name, default)

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def load_user_profile() -> dict:
    """Load candidate information from user_profile.json."""
    if not PROFILE_FILE.exists():
        raise FileNotFoundError(
            f"Candidate profile not found: {PROFILE_FILE}"
        )

    try:
        with PROFILE_FILE.open("r", encoding="utf-8") as file:
            profile = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "user_profile.json contains invalid JSON."
        ) from exc

    if not isinstance(profile, dict):
        raise ValueError(
            "The candidate profile must be a JSON object."
        )

    return profile


# --------------------------------------------------
# Application settings
# --------------------------------------------------

APP_NAME = "JobSherpa"
APP_ENV = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

GEMINI_MAX_RPM = get_int("GEMINI_MAX_RPM", 12)
EMAIL_DELAY_MIN_SECONDS = get_int("EMAIL_DELAY_MIN_SECONDS", 15)
EMAIL_DELAY_MAX_SECONDS = get_int("EMAIL_DELAY_MAX_SECONDS", 30)
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "09:00")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
BLACKLIST_KEYWORDS = get_list("BLACKLIST_KEYWORDS", "java,php,wordpress,sales,bpo")
WHITELIST_KEYWORDS = get_list("WHITELIST_KEYWORDS", "python,react,flask,javascript,full stack,ai,ml")
MAX_YEARS_REQUIRED = get_int("MAX_YEARS_REQUIRED", 2)
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
SNOV_API_KEY = os.getenv("SNOV_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

DRY_RUN = get_bool("DRY_RUN", True)


# --------------------------------------------------
# Candidate profile
# --------------------------------------------------

USER_PROFILE = load_user_profile()

# --------------------------------------------------
# Job search settings
# --------------------------------------------------

JOB_LOCATION = os.getenv("JOB_LOCATION", "India")

JOB_KEYWORDS = get_list(
    "JOB_KEYWORDS",
    "Python Developer,Frontend Developer,React Developer,"
    "Full Stack Developer,Software Engineer,AI Engineer"
)

JOB_SCORE_THRESHOLD = get_int("JOB_SCORE_THRESHOLD", 7)
TOP_N_JOBS = get_int("TOP_N_JOBS", 10)

MAX_APPLY_PER_DAY = get_int("MAX_APPLY_PER_DAY", 5)
MAX_COLD_EMAILS_PER_DAY = get_int("MAX_COLD_EMAILS_PER_DAY", 3)

GEMINI_MAX_RPM = get_int("GEMINI_MAX_RPM", 12)
EMAIL_DELAY_MIN_SECONDS = get_int("EMAIL_DELAY_MIN_SECONDS", 15)
EMAIL_DELAY_MAX_SECONDS = get_int("EMAIL_DELAY_MAX_SECONDS", 30)
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "09:00")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
BLACKLIST_KEYWORDS = get_list("BLACKLIST_KEYWORDS", "java,php,wordpress,sales,bpo")
WHITELIST_KEYWORDS = get_list("WHITELIST_KEYWORDS", "python,react,flask,javascript,full stack,ai,ml")
MAX_YEARS_REQUIRED = get_int("MAX_YEARS_REQUIRED", 2)
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
SNOV_API_KEY = os.getenv("SNOV_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# --------------------------------------------------
# Gemini settings
# --------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# --------------------------------------------------
# Gmail settings
# --------------------------------------------------

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = get_int("SMTP_PORT", 465)

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = get_int("IMAP_PORT", 993)

REPORT_EMAIL = os.getenv("REPORT_EMAIL", "")


# --------------------------------------------------
# Runtime directories
# --------------------------------------------------

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------
# Basic configuration validation
# --------------------------------------------------

def validate_basic_config() -> None:
    """Check basic settings without requiring API credentials."""

    if JOB_SCORE_THRESHOLD < 0 or JOB_SCORE_THRESHOLD > 10:
        raise ValueError(
            "JOB_SCORE_THRESHOLD must be between 0 and 10."
        )

    if TOP_N_JOBS < 1:
        raise ValueError("TOP_N_JOBS must be at least 1.")

    if MAX_APPLY_PER_DAY < 0:
        raise ValueError("MAX_APPLY_PER_DAY cannot be negative.")

    if MAX_COLD_EMAILS_PER_DAY < 0:
        raise ValueError(
            "MAX_COLD_EMAILS_PER_DAY cannot be negative."
        )


validate_basic_config()
