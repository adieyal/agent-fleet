import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


from fleet.container import configured_container
from fleet.transport import Host
from fleet_web.server import FleetState, apply_message


@pytest.fixture
def recorded(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/restoke.json').read_text())
    host = next(host for host in fixture['hosts'] if any(job['status'] == 'failed' for job in host['jobs']))
    job = next(job for job in host['jobs'] if job['status'] == 'failed')
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    store = configured_container(clock=lambda : now[0]).store()
    worker = Host(host['name'], None)
    state = FleetState([worker], container=configured_container(store=store))
    return state, worker, copy.deepcopy(job), store, now


def report(state, worker, job):
    apply_message(state, worker, {'type': 'hello'})
    apply_message(state, worker, {'type': 'job', 'job': job})
    apply_message(state, worker, {'type': 'heartbeat'})


def test_recorded_failure_is_stored_once_before_any_read(recorded):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    attention = configured_container(store).initialized_attention()
    assert len(attention.list()) == 1
    first = attention.list()[0]
    assert first.kind == 'blocker'
    for _ in range(3):
        now[0] += timedelta(seconds=1)
        apply_message(state, worker, {'type': 'job', 'job': job})
    assert [item.id for item in attention.list()] == [first.id]
    assert attention.list()[0].last_seen == now[0]
    sequence = store.latest_sequence()
    assert state.document()['attention'] == state.document()['attention']
    assert store.latest_sequence() == sequence


def test_unchanged_heartbeat_does_not_write_or_notify(recorded):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    sequence = store.latest_sequence()
    version = state.version
    first, = configured_container(store).initialized_attention().list()
    now[0] += timedelta(seconds=5)
    apply_message(state, worker, {'type': 'heartbeat'})
    assert store.latest_sequence() == sequence
    assert state.version == version
    assert configured_container(store).initialized_attention().get(first.id) == first


@pytest.mark.parametrize('clearing', ['retry', 'deleted', 'removed by fleet rm'])
def test_reachable_clear_records_resolution(recorded, clearing):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    if clearing == 'retry':
        job['status'] = 'running'
        apply_message(state, worker, {'type': 'job', 'job': job})
    else:
        apply_message(state, worker, {'type': 'removed', 'id': job['id'], 'reason': clearing})
    item, = configured_container(store).initialized_attention().list()
    assert item.state == 'resolved'
    assert item.resolution_details == ('job retried or finished' if clearing == 'retry'
                                       else 'job deleted from its host')


@pytest.mark.parametrize('absence', ['aged', 'removed-by-old-fleetd', 'absent-on-reconnect'])
def test_a_job_that_leaves_the_stream_without_being_deleted_keeps_its_item_open(recorded, absence):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    first, = configured_container(store).initialized_attention().list()
    if absence == 'aged':
        apply_message(state, worker, {'type': 'removed', 'id': job['id'], 'reason': 'aged'})
    elif absence == 'removed-by-old-fleetd':
        apply_message(state, worker, {'type': 'removed', 'id': job['id']})
    else:
        apply_message(state, worker, {'type': 'hello'})
    apply_message(state, worker, {'type': 'heartbeat'})
    assert job['id'] not in state.by_host[worker.name]['jobs']
    assert configured_container(store).initialized_attention().get(first.id).state == 'open'


def test_a_lost_job_raises_a_blocker(recorded):
    state, worker, job, store, now = recorded
    job['status'] = 'lost'
    for step in job['steps']:
        if step['status'] == 'failed':
            step['status'] = 'running'
    report(state, worker, job)
    item, = configured_container(store).initialized_attention().list()
    assert (item.kind, item.state) == ('blocker', 'open')
    assert 'lost' in item.headline


def test_offline_restart_and_reconnect_preserve_open_items(recorded):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    first, = configured_container(store).initialized_attention().list()
    now[0] += timedelta(hours=1)
    apply_message(state, worker, {'type': 'error', 'error': 'offline'})
    restarted = FleetState([worker], container=configured_container(store=configured_container(path=store.path, clock=lambda : now[0]).store()))
    for server in (state, restarted):
        item, = server.document()['attention']
        assert item['id'] == first.id
        assert item['state'] == 'open' and item['stale']
        assert item['last_seen'] == first.last_seen.timestamp()
    apply_message(restarted, worker, {'type': 'hello'})
    assert configured_container(store).initialized_attention().get(first.id).state == 'open'
    apply_message(restarted, worker, {'type': 'job', 'job': job})
    apply_message(restarted, worker, {'type': 'heartbeat'})
    assert configured_container(store).initialized_attention().get(first.id).state == 'open'


def test_a_failed_job_is_the_users_item_about_that_job(recorded):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    [item] = configured_container(store).initialized_attention().list()
    assert (item.owner, item.subject) == ('user', f"job:{worker.name}:{job['id']}")


def test_an_item_handed_to_the_agent_stays_the_agents_when_its_job_is_seen_again(recorded):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    attention = configured_container(store).initialized_attention()
    [item] = attention.list()
    attention.mandate = lambda project: object()  # Isolate repeated-observation ownership behavior.
    attention.delegate(item.id, actor='user', note='retry it')
    now[0] += timedelta(seconds=1)
    apply_message(state, worker, {'type': 'job', 'job': job})
    seen = configured_container(store).initialized_attention().get(item.id)
    assert (seen.owner, seen.owner_reason, seen.owner_actor, seen.state) == ('agent', 'retry it', 'user', 'open')


def test_agent_owned_aged_out_job_stays_open_until_explicit_deletion(recorded):
    state, worker, job, store, now = recorded
    report(state, worker, job)
    attention = configured_container(store).initialized_attention()
    [item] = attention.list()
    attention.mandate = lambda project: object()  # Isolate repeated-observation ownership behavior.
    attention.delegate(item.id, actor='user', note='inspect failure')
    apply_message(state, worker, {'type': 'removed', 'id': job['id'], 'reason': 'aged_out'})
    apply_message(state, worker, {'type': 'heartbeat'})
    seen = attention.get(item.id)
    assert (seen.owner, seen.subject, seen.state) == ('agent', f"job:{worker.name}:{job['id']}", 'open')
    apply_message(state, worker, {'type': 'removed', 'id': job['id'], 'reason': 'deleted'})
    resolved = attention.get(item.id)
    assert (resolved.owner, resolved.state, resolved.resolution_details) == ('agent', 'resolved', 'job deleted from its host')
