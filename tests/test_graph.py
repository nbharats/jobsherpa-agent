"""Tests for routing, error handling, dry-run safety and daily limits."""

from contextlib import contextmanager
from unittest.mock import patch

import config 
from agent.graph import build_graph, route_after_filter
from agent.nodes import stubs
from agent.state import make_initial_state
from agent.utils import CriticalError, safe_node

FULL = ["load_memory", 'scrape', 'filter', 'score', 'apply', 'email', 'hr_drip', 'save_memory', 'report']
SKIPPED = ['load_memory', 'scrape', 'filter', 'hr_drip', 'save_memory', 'report']

def run_pipline(app,dry_run: bool = True):
    """Run the graph once; return (node order, final merged state)."""
    state = make_initial_state(dry_run)
    order = []
    for chunk in app.stream(state, stream_mode = 'updates'):
        for node, update in chunk.items():
            order.append(node)
            state.update(update)
    return order, state

@contextmanager
def external_mocks():
    """Replace the three external actions with mocks and yield them."""
    with (patch.object(stubs, 'submit_application') as submit,
    patch.object(stubs, "send_cold_email") as email,
    patch.object(stubs, 'send_drip_email') as drip):
        yield submit, email, drip

@contextmanager
def busy_day():
    """8 jobs: 7 pass the threshold, shortlist 5, apply limit 2, email limit 1."""
    with (patch.object(stubs, "MOCK_JOB_COUNT", 8),
        patch.object(config, 'JOB_SCORE_THRESHOLD', 7),
          patch.object(config, 'TOP_N_JOBS', 5),
            patch.object(config, 'MAX_APPLY_PER_DAY', 2),
              patch.object(config, 'MAX_COLD_EMAILS_PER_DAY', 1)):
        yield

def test_normal_run():
    order, final = run_pipline(build_graph())
    assert order == FULL 
    assert final['total_new'] == stubs.MOCK_JOB_COUNT
    assert final['memory_saved'] and final['report_html']
    assert final['errors'] == []

def test_routing_uses_job_list_not_counter():
    assert route_after_filter({'new_jobs': [], 'total_new': 5}) == 'hr_drip'
    assert route_after_filter({'new_jobs': [{'url': 'x'}], 'total_new': 0})

def test_no_new_jobs_route():
    with patch.object(stubs, 'MOCK_JOB_COUNT', 0), external_mocks() as (submit, email, drip):
        order, final = run_pipline(build_graph(), dry_run=False)
    assert order == SKIPPED
    assert submit.call_count == 0 and email.call_count == 0
    assert drip.call_count == stubs.MOCK_DRIP_CONTACTS

def test_scraper_failure_is_recoverable():
    @safe_node
    def exploding_scrape(state):
        raise RuntimeError("portal down")

    with patch.object(stubs, "scrape_jobs", exploding_scrape):
        order, final = run_pipline(build_graph())
    assert len(final["errors"]) == 1 and "portal down" in final["errors"][0]
    assert order == SKIPPED
    assert final['report_html']

def test_critical_error_stops_the_run():
    @safe_node
    def broken_memory(state):
        raise CriticalError('database unavailable')

    with patch.object(stubs, 'load_memory', broken_memory):
        try:
            run_pipline(build_graph())
        except CriticalError:
            pass
        else:
            raise AssertionError('critical error did not stop the run')

def test_dry_run_prevents_external_actions():
    assert make_initial_state()['dry_run'] is True
    with busy_day(), external_mocks() as (submit, email, drip):
        order, final = run_pipline(build_graph(), dry_run=True)
    assert submit.call_count == 0 and email.call_count == 0 and drip.call_count == 0
    assert final['total_applied'] == 0
    assert final["total_emailed"] == 0
    assert final["drip_emails_sent"] == 0
    statuses = [r["apply_status"] for r in final["application_results"]]
    assert statuses.count('dry_run') == 2

def test_limits_are_respected():
    with busy_day(), external_mocks() as (submit, email, drip):
        order, final = run_pipline(build_graph(), dry_run=False)
    assert len(final['scored_jobs']) == 8
    assert len(final['jobs_to_apply']) == 5
    assert submit.call_count == 2
    assert final['total_applied'] == 2
    statuses = [r['apply_status'] for r in final['application_results']]
    assert statuses.count('limit_reached') == 1
    assert email.call_count == 1
    assert drip.call_count == stubs.MOCK_DRIP_CONTACTS

TESTS = [
    test_normal_run,
    test_routing_uses_job_list_not_counter,
    test_no_new_jobs_route,
    test_scraper_failure_is_recoverable,
    test_critical_error_stops_the_run,
    test_dry_run_prevents_external_actions,
    test_limits_are_respected,
]


def run() -> None:
    for test in TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print("All graph checks passed.")


if __name__ == "__main__":
    run()
