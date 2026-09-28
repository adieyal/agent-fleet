import json
from urllib.request import urlopen

from fleet.composition import open_work, open_workspace
from test_web_attention import Deck


def test_state_work_and_bench_use_workspace_id():
    identity = open_workspace().move_in(['home'], 'supplier', name='Supplier').project_id
    work = open_work()
    epic = work.add(project=identity, title='Migration', goal='Ship', kind='epic', actor='user')
    work.add(project=identity, title='Slice', goal='Ship', kind='milestone', parent=epic.id, actor='user')
    deck = Deck()
    try:
        with urlopen(deck.url + '/api/state', timeout=5) as response:
            state = json.load(response)
        assert state['work'][identity]['work_items'][0]['id'] == epic.id
        document = {'attention': state['attention'], 'projects': state['projects']}
        projected = deck.state.with_building(document, open_workspace().registry())
        assert 'work' not in document
        assert projected['work'] == state['work']
        with urlopen(deck.url + '/api/bench?project=' + identity, timeout=5) as response:
            bench = json.load(response)
        assert bench['project'] == identity
        assert bench['rooms'][0]['id'] == epic.id
    finally:
        deck.close()
