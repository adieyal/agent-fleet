"""A third client can project live reads without a container or scheduler."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fleet.projections.live import LiveProjection, building_document
from fleet.projections.ports import LiveReaders
from fleet.modules.workspace import Focus


def test_attention_uses_precomputed_status_without_service_lookup():
    attention = Mock()
    attention.list.return_value = [SimpleNamespace(
        id='item', project='p', stream_context=None, state='open', owner='agent', owner_reason='triage',
        source='manual', headline='Action needed', kind='alert', context_reference=None, refusals=[],
        questions=[], last_seen=SimpleNamespace(timestamp=lambda: 1), resolution_details=None,
        acknowledged_at=None, resolved_at=None, snooze_until=None)]
    workspace = Mock()
    workspace.focus_snapshot.return_value = Focus()
    state = LiveProjection()
    state.reads = SimpleNamespace(attention=attention, workspace=workspace)
    triage = {'p': {'policy_error': 'invalid mandate', 'live_run': None}}
    result = state.with_attention({'hosts': []}, triage)
    assert result['triage'] is triage
    assert result['attention'][0]['delegable'] is False
    attention.require_delegable.assert_not_called()
    triage['p']['policy_error'] = None
    result = state.with_attention({'hosts': []}, triage)
    assert result['attention'][0]['delegable'] is True
    attention.require_delegable.assert_called_once_with('item')
    with pytest.raises(KeyError, match='p'):
        state.with_attention({'hosts': []}, {})


def test_work_and_building_use_only_injected_readers():
    execution = Mock()
    execution.runs.return_value = [SimpleNamespace(host='worker', remote_job_id='job', id='run')]
    execution.deliveries.return_value = []
    revision = [1]
    links = Mock(return_value={('worker', 'job'): {'id': 'work'}})
    building = Mock(return_value={'floors': {}, 'shuttered': []})
    state = LiveProjection()
    state.work_links = None
    state.workspace, state.capacity = object(), 4
    state.reads = LiveReaders(attention=Mock(), execution=execution, workspace=state.workspace,
                             revision=lambda: revision[0], run_work=links, building=building,
                             history_runs=Mock(), run_detail=Mock(), overview=Mock())
    document = {'hosts': [{'name': 'worker', 'jobs': [{'id': 'job'}], 'sessions': []}]}
    result = state.with_work(document)
    assert result['hosts'][0]['jobs'][0]['work'] == {'id': 'work'}
    assert result['hosts'][0]['jobs'][0]['audit_run_id'] == 'run'
    state.with_work(document)
    links.assert_called_once_with()
    revision[0] += 1
    state.with_work(document)
    assert links.call_count == 2
    result = building_document(state, {'attention': [], 'projects': []}, 'registry')
    building.assert_called_once_with(workspace=state.workspace, registry='registry', capacity=4)
    assert result['building'] == {'floors': {}, 'shuttered': []}
