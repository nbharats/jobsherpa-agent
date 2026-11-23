"""Tests for LLM scoring: prompt, parsing, backoff, fallback and the node."""
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from agent import llm
from agent.llm import LLMError, call_with_backoff
from agent.nodes import score_node
from agent.scoring import (DESCRIPTION_LIMIT, build_prompt, heuristic_score,
                           parse_scores)
from agent.state import make_initial_state
from agent.utils import CriticalError
from database import db_manager as db
from scrapers.common import make_job

PROFILE = {
    "personal_info": {"name": "Test User", "email": "secret@example.com",
                      "phone": "+91-9999999999", "location": "India"},
    "education": [{"degree": "B.Tech CSE", "graduation_year": 2025}],
    "skills": {"programming_languages": ["Python", "SQL"], "backend": ["Flask"]},
    "projects": [{"name": "Proj", "technologies": ["Flask"]}],
    "target_roles": ["Python Developer"],
    "preferred_job_type": ["Full-time"],
    "experience_level": ["Fresher"],
    "excluded_roles": ["Director"],
}
SETTINGS = dict(JOB_SCORE_THRESHOLD=7, TOP_N_JOBS=5, USER_PROFILE=PROFILE)


class FakeApiError(Exception):
    """Mimics a Gemini API error that carries an HTTP status code."""

    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


def make_jobs(n: int) -> list[dict]:
    return [make_job(url=f"https://example.com/jobs/{i}", title=f"Python Developer {i}",
                     company="Acme", portal="test", description="Flask and SQL work")
            for i in range(n)]


def descending(prompt: str, **kwargs) -> str:
    """Fake Gemini: scores 10, 9.5, 9, ... in the order the jobs appear."""
    ids = list(dict.fromkeys(re.findall(r'"id": "(j\d+)"', prompt)))
    return json.dumps({"scores": [{"id": i, "score": round(10 - 0.5 * n, 1), "reason": "fake"}
                                  for n, i in enumerate(ids)]})


def fixed(scores: dict):
    """Fake Gemini that returns the given {id: score} mapping."""
    def fake(prompt: str, **kwargs) -> str:
        return json.dumps({"scores": [{"id": k, "score": v, "reason": "fake"}
                                      for k, v in scores.items()]})
    return fake


def run_node(fake, jobs, dry_run=True, **overrides):
    state = make_initial_state(dry_run)
    state["new_jobs"] = jobs
    with patch.multiple(config, **{**SETTINGS, **overrides}), \
            patch.object(score_node, "generate_text", fake):
        return score_node.score_jobs(state)


@contextmanager
def temp_db(init: bool = True):
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(config, "DATABASE_PATH", Path(tmp) / "t.db"):
        if init:
            db.init_db()
        yield


def test_prompt_content():
    long_job = make_job(url="https://x.com/j", title="T", company="C", portal="p",
                        description="x" * 5000)
    prompt = build_prompt(make_jobs(2) + [long_job], PROFILE)
    assert '"id": "j0"' in prompt and '"id": "j2"' in prompt
    assert "x" * (DESCRIPTION_LIMIT + 1) not in prompt          # descriptions truncated
    assert "secret@example.com" not in prompt                    # no contact details
    assert "+91-9999999999" not in prompt and "Test User" not in prompt
    assert "untrusted" in prompt and "Return ONLY valid JSON" in prompt


def test_parse_scores():
    ids = {"j0", "j1", "j2"}
    fenced = '```json\n{"scores":[{"id":"j0","score":8.5,"reason":"fits"},{"id":"j1","score":"7"}]}\n```'
    assert parse_scores(fenced, ids) == {"j0": (8.5, "fits"), "j1": (7.0, "")}
    chatty = 'Here you go: {"scores":[{"id":"j0","score":9}]} hope this helps'
    assert parse_scores(chatty, ids) == {"j0": (9.0, "")}
    assert parse_scores('[{"id":"j0","score":6}]', ids) == {"j0": (6.0, "")}     # bare list
    clamped = '{"scores":[{"id":"j0","score":15},{"id":"j1","score":-3}]}'
    assert parse_scores(clamped, ids) == {"j0": (10.0, ""), "j1": (0.0, "")}
    messy = ('{"scores":[{"id":"zzz","score":5},{"id":"j0","score":"abc"},'
             '{"id":"j1","score":4},{"id":"j1","score":9},{"id":"j2","score":"NaN"}]}')
    assert parse_scores(messy, ids) == {"j1": (4.0, "")}        # unknown, bad, duplicate, NaN
    for bad in ("not json at all", '{"foo": 1}'):
        try:
            parse_scores(bad, ids)
        except ValueError:
            continue
        raise AssertionError(f"accepted unusable output: {bad!r}")


def test_heuristic_score():
    skills = ["Python", "Flask"]
    good = make_job(url="https://x.com/1", title="Python Flask Developer", company="A", portal="p")
    bad = make_job(url="https://x.com/2", title="Marketing Manager", company="A", portal="p")
    assert 0 <= heuristic_score(bad, skills) < heuristic_score(good, skills) <= 10


def test_backoff_schedule():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) <= 2:
            raise FakeApiError(429)
        return "ok"

    assert call_with_backoff(flaky, sleep=sleeps.append) == "ok"
    assert sleeps == [5, 10]

    sleeps.clear()

    def bad_request():
        raise FakeApiError(400)

    try:
        call_with_backoff(bad_request, sleep=sleeps.append)
    except FakeApiError:
        assert sleeps == []                     # 400 is not retried
    else:
        raise AssertionError("a non-retryable error was swallowed")

    def always_limited():
        raise FakeApiError(429)

    try:
        call_with_backoff(always_limited, sleep=sleeps.append)
    except FakeApiError:
        assert sleeps == [5, 10, 20, 40]        # gives up after four retries
    else:
        raise AssertionError("retries never gave up")


def test_model_fallback_and_missing_key():
    class FakeModels:
        def __init__(self):
            self.used = []

        def generate_content(self, *, model, contents, config):
            self.used.append(model)
            if model == "primary":
                raise FakeApiError(429)
            return type("Reply", (), {"text": "from backup"})()

    class FakeClient:
        models = FakeModels()

    settings = dict(GEMINI_MODEL="primary", GEMINI_FALLBACK_MODELS=["backup"],
                    GEMINI_MAX_RPM=0, GEMINI_API_KEY="x")
    with patch.multiple(config, **settings), \
            patch.object(llm, "_get_client", return_value=FakeClient()), \
            patch("agent.llm.time.sleep"):
        assert llm.generate_text("hi") == "from backup"
        assert FakeClient.models.used == ["primary"] * 5 + ["backup"]   # 1 try + 4 retries

        FakeClient.models.generate_content = lambda **kw: (_ for _ in ()).throw(FakeApiError(429))
        try:
            llm.generate_text("hi")
        except LLMError:
            pass
        else:
            raise AssertionError("exhausting every model did not raise LLMError")

    with patch.multiple(config, GEMINI_API_KEY=""), patch.object(llm, "_client", None):
        try:
            llm._get_client()
        except LLMError:
            return
        raise AssertionError("a missing API key was accepted")


def test_node_ranks_and_shortlists():
    update = run_node(fixed({"j0": 9, "j1": 3, "j2": 8}), make_jobs(3))
    assert [j["title"] for j in update["scored_jobs"]] == [
        "Python Developer 0", "Python Developer 2", "Python Developer 1"]
    assert all(j["score_source"] == "gemini" for j in update["scored_jobs"])
    assert len(update["jobs_to_apply"]) == 2                      # threshold 7
    assert update["errors"] == []
    top_one = run_node(fixed({"j0": 9, "j1": 3, "j2": 8}), make_jobs(3), TOP_N_JOBS=1)
    assert len(top_one["jobs_to_apply"]) == 1                     # TOP_N_JOBS caps it


def test_node_falls_back_when_llm_fails():
    def raising(prompt, **kwargs):
        raise LLMError("quota")

    for fake in (raising, lambda prompt, **kw: "total garbage"):
        update = run_node(fake, make_jobs(3))
        assert len(update["scored_jobs"]) == 3
        assert all(j["score_source"] == "fallback" for j in update["scored_jobs"])
        assert all(0 <= j["relevance_score"] <= 10 for j in update["scored_jobs"])
        assert len(update["errors"]) == 1 and "fallback" in update["errors"][0]

    partial = run_node(fixed({"j0": 9}), make_jobs(3))           # Gemini skipped two jobs
    sources = [j["score_source"] for j in partial["scored_jobs"]]
    assert sources == ["gemini", "fallback", "fallback"] and len(partial["errors"]) == 1


def test_node_batches_large_lists():
    calls = []

    def counting(prompt, **kwargs):
        calls.append(1)
        return descending(prompt)

    update = run_node(counting, make_jobs(30))
    assert len(calls) == 2                                        # 25 + 5 jobs
    assert len(update["scored_jobs"]) == 30


def test_scores_saved_only_on_real_runs():
    jobs = make_jobs(3)
    fake = fixed({"j0": 9, "j1": 3, "j2": 8})
    with temp_db():
        for job in jobs:
            db.insert_job(job)
        first, second = (db.make_job_hash(jobs[0]["url"]), db.make_job_hash(jobs[1]["url"]))

        run_node(fake, jobs, dry_run=True)
        assert db.get_job(first)["relevance_score"] == 0          # dry run wrote nothing

        run_node(fake, jobs, dry_run=False)
        assert db.get_job(first)["relevance_score"] == 9.0
        assert db.get_job(second)["relevance_score"] == 3.0


def test_database_failure_is_critical():
    with temp_db(init=False):
        try:
            run_node(fixed({"j0": 9}), make_jobs(1), dry_run=False)
        except CriticalError:
            return
        raise AssertionError("a database failure did not stop the run")


TESTS = [
    test_prompt_content,
    test_parse_scores,
    test_heuristic_score,
    test_backoff_schedule,
    test_model_fallback_and_missing_key,
    test_node_ranks_and_shortlists,
    test_node_falls_back_when_llm_fails,
    test_node_batches_large_lists,
    test_scores_saved_only_on_real_runs,
    test_database_failure_is_critical,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All scoring checks passed.")


if __name__ == "__main__":
    run()