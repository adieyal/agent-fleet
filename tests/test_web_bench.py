"""The bench HTTP contract uses controller work, never the recorded host jobs."""

import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


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
