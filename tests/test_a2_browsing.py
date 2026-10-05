import json

import pytest
from fleet.container import configured_container
from fleet_cli import cli
from fleet.modules.execution import JobObservation
from tests.test_run_history import history, api, filters, get


@pytest.mark.parametrize('state,label', [('queued', 'queued (not started)'), ('stalled', 'stalled (outcome unknown)')])
def test_worker_state_survives_history_cli_and_offline(history, capsys, state, label):
    execution, run, now = history[3], history[6], history[-1]
    execution.observe(run.host, JobObservation(run.remote_job_id, state, run.runtime, run.start, None, now))
    stored = execution.get_run(run.id)
    assert stored.status == 'unknown outcome' and stored.reason == state
    cli.main(['run', 'show', run.id])
    assert label in capsys.readouterr().out
    cli.main(['history', 'runs', '--work-item', history[4].id[:8]])
    assert label in capsys.readouterr().out
    execution.record_host(run.host, reachable=False, error='connection refused')
    row = filters(history, work_item=history[4].id)['runs'][0]
    assert row['reason'] == state and row['offline_since']
    execution.observe(run.host, JobObservation(run.remote_job_id, 'running', run.runtime, now, None, now))
    assert execution.get_run(run.id).reason is None


def test_http_history_accepts_full_and_short_work_ids(api, history, capsys):
    identity = history[4].id
    full = get(api, '/api/history/runs', work_item=identity, descendants='true')
    short = get(api, '/api/history/runs', work_item=identity[:8], descendants='true')
    assert short[0] == 200 and short[1] == full[1]
    cli.main(['history', 'runs', '--work-item', identity[:8], '--descendants', '--json'])
    assert [r['id'] for r in json.loads(capsys.readouterr().out)['runs']] == [r['id'] for r in short[1]['runs']]


def test_http_history_ambiguous_prefix_lists_matches_and_exact_wins(api, history):
    from dataclasses import replace
    work = history[2]
    root, child = history[4:6]
    # Deterministic IDs let this test exercise ambiguity instead of depending on random UUID collisions.
    root_id, child_id = 'audit-prefix-one', 'audit-prefix-two'
    with work.repository.transaction() as repository:
        repository.save('item', replace(root, id=root_id), 'test')
        repository.save('item', replace(child, id=child_id, parent=None), 'test')
    status, value = get(api, '/api/history/runs', work_item='audit-prefix-')
    assert status == 400
    assert 'ambiguous work item' in value['error']
    assert root_id in value['error'] and child_id in value['error']
    assert get(api, '/api/history/runs', work_item=root_id)[0] == 200
    assert get(api, '/api/history/runs', work_item='missing-prefix')[0] == 400
