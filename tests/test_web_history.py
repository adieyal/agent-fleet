"""The deck reads a subject's audit trail over HTTP, in the shape `fleet history --json` prints."""

import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from fleet import cli, composition


@pytest.fixture
def item(deck_state, monkeypatch, project_id):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    store = composition.open_store()
    monkeypatch.setattr(deck_state, "store", store)
    work = composition.open_work(store)
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
