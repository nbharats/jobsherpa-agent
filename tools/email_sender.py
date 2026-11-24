"""Gmail SMTP sender: builds a MIME multipart message and sends it over SSL (port 465).

EMAIL_REDIRECT_TO is a safety switch: when set, every email goes to that
address instead of the real recipient, with the intended recipient shown in
the subject and body.
"""
import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when an email cannot be built or delivered."""


def credentials_problem() -> str | None:
    """Return why sending is impossible (missing credentials), or None."""
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        return "Gmail credentials are missing in .env"
    return None


def sender_problem() -> str | None:
    """Return why real sending is impossible right now, or None if it is ready."""
    problem = credentials_problem()
    if problem:
        return problem
    if not Path(config.RESUME_PATH).is_file():
        return f"Resume PDF not found at {config.RESUME_PATH}"
    return None


def build_message(sender: str, recipient: str, subject: str, body: str,
                  attachment: Path | None = None) -> EmailMessage:
    """Build a plain-text email, with the attachment as a MIME multipart part."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = " ".join(subject.split())       # newlines are not allowed in headers
    message.set_content(body)
    if attachment is not None:
        mime_type = mimetypes.guess_type(attachment.name)[0] or "application/octet-stream"
        maintype, subtype = mime_type.split("/", 1)
        message.add_attachment(attachment.read_bytes(), maintype=maintype,
                               subtype=subtype, filename=attachment.name)
    return message


def apply_redirect(recipient: str, subject: str, body: str) -> tuple[str, str, str]:
    """Redirect the email to EMAIL_REDIRECT_TO if it is set."""
    target = config.EMAIL_REDIRECT_TO.strip()
    if not target:
        return recipient, subject, body
    return (target,
            f"[TEST for {recipient}] {subject}",
            f"[TEST MODE: this email would have gone to {recipient}]\n\n{body}")


def _deliver(message: EmailMessage) -> None:
    """Log in to Gmail over SSL and send one message."""
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                          context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD.replace(" ", ""))
        smtp.send_message(message)


def send_email(recipient: str, subject: str, body: str) -> str:
    """Send one email with the resume attached; return the address it went to.

    Raises EmailSendError if anything goes wrong.
    """
    problem = sender_problem()
    if problem:
        raise EmailSendError(problem)
    to, subject, body = apply_redirect(recipient, subject, body)
    try:
        message = build_message(config.GMAIL_ADDRESS, to, subject, body, Path(config.RESUME_PATH))
        _deliver(message)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        raise EmailSendError(f"Could not send to {to}: {exc}") from exc
    logger.info("Email sent to %s", to)
    return to


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
        _deliver(message)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        raise EmailSendError(f"Could not send the report to {recipient}: {exc}") from exc
    logger.info("Report emailed to %s", recipient)
