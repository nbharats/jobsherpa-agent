"""Tests for the sender, the email helpers and the HR CSV reader."""
import smtplib
import tempfile
from pathlib import Path
from unittest.mock import patch

import config
from agent.emailing import (build_email_prompt, drip_email, fallback_cold_email,
                            parse_emails, signature)
from tools import email_sender
from tools.email_sender import EmailSendError
from tools.hr_csv import read_hr_csv

PROFILE = {
    "personal_info": {"name": "Test User", "email": "secret@example.com",
                      "phone": "+91-9999999999", "linkedin": "",
                      "github": "https://github.com/test"},
    "education": [{"degree": "B.Tech Computer Science", "graduation_year": 2025}],
    "skills": {"programming_languages": ["Python", "SQL"], "backend": ["Flask"]},
    "experience": [{"organization": "Acme", "role": "Intern"}],
    "projects": [{"name": "Proj", "description": "A web app", "technologies": ["Flask"]}],
    "target_roles": ["Python Developer", "Full Stack Developer"],
    "preferred_job_type": ["Full-time"],
}
GOOD_BODY = ("Hello team, I would like to be considered for this role. I work with Python "
             "and Flask and have attached my resume for your review. Is it still open?")


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL and records what happens."""
    instances: list["FakeSMTP"] = []
    fail: Exception | None = None

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port
        self.login_args, self.sent = None, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent.append(message)


def sender_settings(resume: Path, redirect: str = "", password: str = "abcd efgh ijkl mnop"):
    return patch.multiple(config, GMAIL_ADDRESS="me@gmail.com", GMAIL_APP_PASSWORD=password,
                          SMTP_HOST="smtp.gmail.com", SMTP_PORT=465, RESUME_PATH=resume,
                          EMAIL_REDIRECT_TO=redirect)


def make_resume(tmp: str) -> Path:
    resume = Path(tmp) / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    return resume


def test_send_email_flow():
    with tempfile.TemporaryDirectory() as tmp, sender_settings(make_resume(tmp)), \
            patch.object(email_sender.smtplib, "SMTP_SSL", FakeSMTP):
        FakeSMTP.instances.clear()
        assert email_sender.send_email("hr@acme.com", "Hi\nthere", "Body text") == "hr@acme.com"
        smtp = FakeSMTP.instances[0]
        assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
        assert smtp.login_args == ("me@gmail.com", "abcdefghijklmnop")   # spaces removed
        message = smtp.sent[0]
        assert message["To"] == "hr@acme.com" and message["Subject"] == "Hi there"
        assert message.is_multipart()
        assert [p.get_filename() for p in message.iter_attachments()] == ["resume.pdf"]


def test_redirect_to_test_address():
    with tempfile.TemporaryDirectory() as tmp, \
            sender_settings(make_resume(tmp), redirect="me@gmail.com"), \
            patch.object(email_sender.smtplib, "SMTP_SSL", FakeSMTP):
        FakeSMTP.instances.clear()
        assert email_sender.send_email("hr@acme.com", "Hello", "Body") == "me@gmail.com"
        message = FakeSMTP.instances[0].sent[0]
        assert message["To"] == "me@gmail.com"
        assert message["Subject"] == "[TEST for hr@acme.com] Hello"
        assert "TEST MODE" in message.get_body(("plain",)).get_content()


def test_sender_problems():
    with tempfile.TemporaryDirectory() as tmp:
        with sender_settings(make_resume(tmp), password=""):
            assert "credentials" in email_sender.sender_problem()
            try:
                email_sender.send_email("hr@acme.com", "s", "b")
            except EmailSendError:
                pass
            else:
                raise AssertionError("missing credentials were accepted")
        with sender_settings(Path(tmp) / "missing.pdf"):
            assert "Resume" in email_sender.sender_problem()
        with sender_settings(make_resume(tmp)), \
                patch.object(email_sender.smtplib, "SMTP_SSL", FakeSMTP):
            assert email_sender.sender_problem() is None
            FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, b"bad credentials")
            try:
                email_sender.send_email("hr@acme.com", "s", "b")
            except EmailSendError as exc:
                assert "hr@acme.com" in str(exc)
            else:
                raise AssertionError("an SMTP failure was not reported")
            finally:
                FakeSMTP.fail = None


def test_prompt_has_no_contact_details():
    jobs = [{"title": "Python Developer", "company": "Acme", "description": "x" * 5000},
            {"title": "Flask Dev", "company": "Beta", "description": ""}]
    prompt = build_email_prompt(jobs, PROFILE)
    assert '"id": "j0"' in prompt and '"id": "j1"' in prompt
    assert "x" * 501 not in prompt                              # description truncated
    for private in ("secret@example.com", "+91-9999999999", "Test User"):
        assert private not in prompt
    assert "untrusted" in prompt and "Return ONLY valid JSON" in prompt
    assert '"emails"' in prompt


def test_parse_emails():
    ids = {"j0", "j1", "j2", "j3"}
    good = {"id": "j0", "subject": "Python role at Acme", "body": GOOD_BODY}
    fenced = "```json\n" + '{"emails": [' + str(good).replace("'", '"') + "]}" + "\n```"
    assert parse_emails(fenced, ids) == {"j0": ("Python role at Acme", GOOD_BODY)}
    messy = ('{"emails": ['
             '{"id": "zzz", "subject": "s", "body": "' + GOOD_BODY + '"},'
             '{"id": "j0", "subject": "", "body": "' + GOOD_BODY + '"},'
             '{"id": "j1", "subject": "ok", "body": "too short"},'
             '{"id": "j2", "subject": "ok", "body": "' + GOOD_BODY + ' [Your Name]"},'
             '{"id": "j3", "subject": "Fine subject", "body": "' + GOOD_BODY + '"}]}')
    assert list(parse_emails(messy, ids)) == ["j3"]            # unknown, empty, short, placeholder
    try:
        parse_emails("not json", ids)
    except ValueError:
        return
    raise AssertionError("unusable output was accepted")


def test_fallback_and_signature():
    subject, body = fallback_cold_email({"title": "Python Developer", "company": "Acme"}, PROFILE)
    assert "Python Developer" in subject and body.startswith("Hello Acme hiring team,")
    assert "Python" in body and "Proj" in body and "2025" in body and "[" not in body
    sign = signature(PROFILE)
    assert sign.startswith("Best regards,")
    for line in ("Test User", "+91-9999999999", "secret@example.com", "https://github.com/test"):
        assert line in sign
    assert "linkedin" not in sign.lower()                       # empty fields are left out


def test_drip_email():
    subject, body = drip_email({"name": "Asha", "company": "Acme"}, PROFILE)
    assert body.startswith("Hello Asha,") and "Acme" in subject and "Python Developer" in body
    _, anonymous = drip_email({}, PROFILE)
    assert anonymous.startswith("Hello,\n")


def test_read_hr_csv():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hr.csv"
        path.write_text("Name,Email,Company,Designation\n"
                        "Asha,ASHA@Acme.com,Acme,HR\n"
                        "Bad,not-an-email,X,Y\n"
                        "Dup,asha@acme.com,Acme,HR\n", encoding="utf-8-sig")
        rows, skipped = read_hr_csv(path)
        assert len(rows) == 2 and skipped == 1
        assert rows[0] == {"email": "asha@acme.com", "name": "Asha",
                           "company": "Acme", "designation": "HR"}
        assert read_hr_csv(Path(tmp) / "missing.csv") == ([], 0)


TESTS = [
    test_send_email_flow,
    test_redirect_to_test_address,
    test_sender_problems,
    test_prompt_has_no_contact_details,
    test_parse_emails,
    test_fallback_and_signature,
    test_drip_email,
    test_read_hr_csv,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All email tool checks passed.")


if __name__ == "__main__":
    run()