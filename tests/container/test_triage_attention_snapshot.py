"""Triage status covers exactly the attention snapshot rendered by either deck."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from fleet.api import Host
from fleet.container import configured_container


@pytest.mark.parametrize('live', [False, True], ids=['recorded', 'live'])
def test_triage_covers_projected_attention_when_state_attention_is_rebound(tmp_path, fixture_data, live):
    container = configured_container()
    recorded = container.fixture_state(fixture=fixture_data)
    try:
        state = recorded
        if live:
            workspace = recorded.workspace
            state = recorded.container.live_state(
                [Host(host['name'], None) for host in fixture_data['hosts']], {},
                workspace.registry, workspace, workspace.capacity)
            for host in fixture_data['hosts']:
                def fill(entry, host=host):
                    entry.update({key: value for key, value in host.items() if key not in {'jobs', 'sessions'}})
                    entry['jobs'] = {job['id']: job for job in host['jobs']}
                    entry['sessions'] = {session['id']: session for session in host['sessions']}
                state.update(host['name'], fill)
            state.reads = replace(state.reads, attention=recorded.reads.attention)
        attention = state.reads.attention
        project = next(iter(recorded.registry.projects))
        manual = attention.raise_item(
            project=project, kind='alert', owner='user', source='manual', source_reference='snapshot-regression',
            headline='Resolved attention still belongs to the response', context_reference='context', actor='tester')
        attention.resolve(manual.id, details='Already resolved', actor='tester')
        # Matches the browser fixture's independent replacement of state.attention.
        state.attention = configured_container(path=tmp_path / 'other.db').attention()
        unrelated = state.attention.raise_item(
            project='unrelated-project', kind='alert', owner='user', source='manual', source_reference='unrelated',
            headline='Not in the projected snapshot', context_reference='context', actor='tester')
        status = Mock(wraps=state.triage_status)
        state.triage_status = status
        original_list = attention.list
        attention.list = Mock(wraps=original_list)
        try:
            document = state.document()
            # Scheduler status may make additional project-scoped reads.
            assert sum(not call.args and not call.kwargs for call in attention.list.call_args_list) == 1
        finally:
            attention.list = original_list
        projects = {item['project_id'] for item in document['attention'] if item['project_id']}
        assert projects
        assert set(document['triage']) == projects
        assert {call.args[0] for call in status.call_args_list} == projects
        assert status.call_count == len(projects)
        assert any(item['id'] == manual.id and item['state'] == 'resolved' for item in document['attention'])
        assert all(item['id'] != unrelated.id for item in document['attention'])
        assert any(item['owner']['type'] != 'attention' for item in document['attention'])
    finally:
        recorded.attention_directory.cleanup()
