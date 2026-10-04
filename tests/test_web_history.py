"""The deck reads a subject's audit trail over HTTP, in the shape `fleet history --json` prints."""

import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from fleet.container import configured_container
from fleet.modules.records import TRIAGE_PATH
from fleet import cli


@pytest.fixture
def item(deck_state, monkeypatch, project_id, override_web_store):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    store = configured_container().store()
    override_web_store(deck_state, store)
    work = configured_container(store).work()
    item = work.add(project=project_id, title="Audit", goal="Keep history", actor="user")
    work.set(item.id, actor="claude", next_step="Serve it")
    return item


def get(base_url, **query):
    with urlopen(f"{base_url}/api/history?{urlencode(query)}", timeout=5) as response:
        return json.load(response)


def failure(base_url, **query):
    with pytest.raises(HTTPError) as error:
        get(base_url, **query)
    return error.value.code, json.load(error.value)["error"]


def test_history_returns_the_entries_the_cli_prints(base_url, item, capsys):
    result = get(base_url, subject=item.id[:8])
    capsys.readouterr()
    cli.main(["history", "--subject", item.id, "--json"])
    assert result == json.loads(capsys.readouterr().out)
    assert (result["kind"], result["id"]) == ("work item", item.id)
    newest = result["entries"][0]
    assert (newest["actor"], newest["changes"], newest["source_run"]) == (
        "claude", [{"field": "next_step", "before": None, "after": "Serve it"}], None)
    assert len(result["entries"]) == 2


def test_since_limits_the_entries(base_url, item):
    assert get(base_url, subject=item.id, since="2999-01-01")["entries"] == []
    assert len(get(base_url, subject=item.id, since="1h")["entries"]) == 2


def test_bad_requests_say_what_is_wrong(base_url, item):
    assert failure(base_url) == (400, "subject is required")
    assert failure(base_url, subject=item.id, since="soon") == (400, "not an ISO date or time: soon")
    assert failure(base_url, subject="zzzz") == (404, "no history for 'zzzz'")


def test_attention_history_includes_ownership_and_reasons(base_url, deck_state, monkeypatch, project_id, capsys, override_web_store):
    store = configured_container().store()
    override_web_store(deck_state, store)
    attention = configured_container(store).initialized_attention()
    # delegation needs the project's confirmed triage policy
    policy = dict(goal='Triage', constraints=[], escalation_conditions=[], criteria_it_may_judge=[],
                  decision_authority=['retry', 'escalate', 'record_decision'], host='carbon', runtime='codex',
                  cwd='/tmp', permission='acceptEdits', routing={}, permissions={'allow': ['Read'], 'escalate': []},
                  limits={'retries_per_step': 2, 'runs_per_day': 12, 'unclaimed_minutes': 30})
    configured_container(store).services().records.write_mandate(project_id, TRIAGE_PATH, json.dumps(policy), key='policy', actor='user')
    item = attention.raise_item(project=project_id, kind='alert', owner='user', source='manual',
        source_reference='ownership-history', headline='Review failure', context_reference='report', actor='reporter')
    other = attention.raise_item(project=project_id, kind='alert', owner='user', source='manual',
        source_reference='other-history', headline='Unrelated', context_reference='other', actor='reporter')
    attention.delegate(item.id, actor='adi', note='Triage under the charter')
    attention.take(item.id, actor='supervisor', reason='User must choose the recovery')
    attention.delegate(other.id, actor='other-actor', note='Unrelated handover')
    attention.acknowledge(item.id, actor='adi')
    before = store.latest_sequence()
    result = get(base_url, subject='attention:' + item.id)
    assert [e['actor'] for e in result['entries']] == ['adi', 'supervisor', 'adi', 'reporter']
    owners = [e for e in result['entries'] if e['subject'].endswith(':owner')]
    assert [e['subject'] for e in owners] == ['attention:' + item.id + ':owner'] * 2
    assert all(e['kind'] == 'attention ownership' and e['id'] == item.id for e in owners)
    assert owners[0]['changes'] == [
        {'field': 'owner', 'before': 'agent', 'after': 'user'},
        {'field': 'reason', 'before': None, 'after': 'User must choose the recovery'}]
    assert owners[1]['changes'] == [
        {'field': 'owner', 'before': 'user', 'after': 'agent'},
        {'field': 'reason', 'before': None, 'after': 'Triage under the charter'}]
    assert get(base_url, subject='attention:' + item.id[:8]) == result
    assert get(base_url, subject=item.id) == result
    assert get(base_url, subject=item.id, since='2999-01-01')['entries'] == []
    cli.main(['history', '--subject', item.id[:8], '--json'])
    assert json.loads(capsys.readouterr().out) == result
    assert store.latest_sequence() == before
