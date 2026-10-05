"""The bench HTTP contract uses controller work, never the recorded host jobs."""

import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from fleet.composition import open_execution, open_store, open_work


def test_bench_endpoint(base_url):
    with urlopen(base_url + '/api/bench?project=p-5e1f0a01', timeout=5) as response:
        floor = json.load(response)
    room, = floor['rooms']
    assert room['title'] == 'Supplier intelligence'
    bench, = room['benches']
    with urlopen(base_url + '/api/bench?project=p-5e1f0a01&slice=' + bench['id'], timeout=5) as response:
        result = json.load(response)
    assert [task['title'] for task in result['tasks']] == ['Review evidence']
    assert result['agents'] == []
    assert result['summary'] is None
    assert result['progress'] == {'basis': 'unknown', 'complete': None, 'total': None}
    with pytest.raises(HTTPError) as missing:
        urlopen(base_url + '/api/bench?project=p-5e1f0a01&slice=missing', timeout=5)
    assert missing.value.code == 404
    with pytest.raises(HTTPError) as invalid:
        urlopen(base_url + '/api/bench', timeout=5)
    assert invalid.value.code == 400


def test_state_work_links_follow_store_changes(base_url, deck_state, monkeypatch):
    store = open_store()
    monkeypatch.setattr(deck_state, 'store', store)
    work = open_work(store)
    epic = work.add(project='links', title='Links epic', goal='Deliver', kind='epic', actor='user')
    milestone = work.add(project='links', title='Links slice', goal='Deliver', kind='milestone',
                         parent=epic.id, actor='user')

    def linked():
        with urlopen(base_url + '/api/state', timeout=5) as response:
            doc = json.load(response)
        job, = [job for host in doc['hosts'] if host['name'] == 'home' for job in host['jobs'] if job['id'] == 'a1c3e9']
        run = open_execution(store).find_run('home', 'a1c3e9')
        assert job['audit_run_id'] == (run.id if run else None)
        return job['work'] and [node['title'] for node in job['work']['chain']]

    assert linked() is None
    open_execution(store).link('home', 'a1c3e9', milestone.id, actor='user')
    assert linked() == ['Links epic', 'Links slice']
    work.set(milestone.id, title='Renamed slice', actor='user')
    assert linked() == ['Links epic', 'Renamed slice']
