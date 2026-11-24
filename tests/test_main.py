"""Tests for the CLI, the run wrapper, the scheduler setup and logging."""
import logging
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
import logging_setup
import main
from agent.utils import CriticalError

FINISHED = {"total_scraped": 5, "total_new": 2, "total_applied": 1, "total_emailed": 1,
            "drip_emails_sent": 0, "errors": ["boom"]}


def fake_app(result=None, error=None, seen=None):
    def invoke(state):
        if seen is not None:
            seen.append(state["dry_run"])
        if error:
            raise error
        return result
    return SimpleNamespace(invoke=invoke)


def test_run_once_exit_codes():
    seen = []
    with patch.object(main, "build_graph", lambda: fake_app(result=FINISHED, seen=seen)):
        assert main.run_once(True) == 0 and main.run_once(False) == 0
    assert seen == [True, False]                       # dry_run reaches the state
    for error in (CriticalError("db down"), RuntimeError("bug")):
        with patch.object(main, "build_graph", lambda e=error: fake_app(error=e)):
            assert main.run_once(True) == 1


def test_parse_schedule_time():
    assert main.parse_schedule_time("09:00") == (9, 0)
    assert main.parse_schedule_time("9:30") == (9, 30)
    assert main.parse_schedule_time(" 23:59 ") == (23, 59)
    for bad in ("24:00", "09:60", "9", "abc", ""):
        try:
            main.parse_schedule_time(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_resolve_dry_run():
    with patch.object(config, "DRY_RUN", True):
        assert main.resolve_dry_run(False, False) is True
        assert main.resolve_dry_run(False, True) is False
    with patch.object(config, "DRY_RUN", False):
        assert main.resolve_dry_run(False, False) is False
        assert main.resolve_dry_run(True, False) is True


def test_scheduler_setup():
    with patch.multiple(config, SCHEDULE_TIME="09:05", TIMEZONE="Asia/Kolkata"):
        scheduler = main.build_scheduler(True)
        (job,) = scheduler.get_jobs()
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "9" and fields["minute"] == "5"
        assert job.args == (True,) and job.max_instances == 1 and job.coalesce is True
        assert str(scheduler.timezone) == "Asia/Kolkata"
    for bad in (dict(TIMEZONE="Not/AZone"), dict(SCHEDULE_TIME="25:00")):
        with patch.multiple(config, **{"SCHEDULE_TIME": "09:00", "TIMEZONE": "Asia/Kolkata", **bad}):
            try:
                main.build_scheduler(True)
            except ValueError:
                continue
            raise AssertionError(f"accepted {bad}")


def run_cli(argv, **settings):
    calls = {"run": [], "scheduler": []}

    class FakeScheduler:
        def start(self):
            calls["scheduler"].append("started")

    def fake_run(dry_run):
        calls["run"].append(dry_run)
        return 0

    def fake_build(dry_run):
        calls["scheduler"].append(dry_run)
        return FakeScheduler()

    with patch.multiple(config, **settings), \
            patch.object(main, "setup_logging", lambda: None), \
            patch.object(main, "run_once", fake_run), \
            patch.object(main, "build_scheduler", fake_build):
        return main.main(argv), calls


def test_cli_modes():
    code, calls = run_cli(["--run-now", "--dry-run"], DRY_RUN=False, EMAIL_REDIRECT_TO="")
    assert code == 0 and calls["run"] == [True]

    code, calls = run_cli(["--run-now", "--live"], DRY_RUN=True, EMAIL_REDIRECT_TO="")
    assert code == 2 and calls["run"] == []            # refused: would email real companies
    code, calls = run_cli(["--run-now"], DRY_RUN=False, EMAIL_REDIRECT_TO="")
    assert code == 2 and calls["run"] == []            # .env-driven live runs are protected too

    code, calls = run_cli(["--run-now", "--live", "--yes"], DRY_RUN=True, EMAIL_REDIRECT_TO="")
    assert code == 0 and calls["run"] == [False]
    code, calls = run_cli(["--run-now", "--live"], DRY_RUN=True, EMAIL_REDIRECT_TO="me@gmail.com")
    assert code == 0 and calls["run"] == [False]

    code, calls = run_cli([], DRY_RUN=True, EMAIL_REDIRECT_TO="")
    assert code == 0 and calls["run"] == [] and calls["scheduler"] == [True, "started"]

    try:
        main.main(["--dry-run", "--live"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--dry-run and --live were accepted together")

    with patch.object(main, "setup_logging", lambda: None), \
            patch.object(main, "build_scheduler", side_effect=ValueError("bad time")):
        assert main.main([]) == 2


def test_logging_setup():
    root = logging.getLogger()
    previous_level = root.level
    with tempfile.TemporaryDirectory() as tmp:
        try:
            logging_setup.setup_logging(Path(tmp), "INFO")
            logging_setup.setup_logging(Path(tmp), "INFO")      # second call adds nothing
            ours = [h for h in root.handlers if (h.get_name() or "").startswith("jobsherpa-")]
            assert len(ours) == 2
            logging.getLogger("jobsherpa.test").warning("hello log")
            for handler in ours:
                handler.flush()
            text = (Path(tmp) / "jobsherpa.log").read_text(encoding="utf-8")
            assert "WARNING" in text and "hello log" in text
            assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text)
        finally:
            logging_setup.shutdown_logging()
            root.setLevel(previous_level)


TESTS = [
    test_run_once_exit_codes,
    test_parse_schedule_time,
    test_resolve_dry_run,
    test_scheduler_setup,
    test_cli_modes,
    test_logging_setup,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All main checks passed.")


if __name__ == "__main__":
    run()