"""The room's guidance over HTTP: read, edit with a version check, history, decisions, and promotion."""

import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from fleet import composition

FIXTURES = Path(__file__).parent / "fixtures" / "guidance"
CONSTITUTION = (FIXTURES / "invoice-training.constitution.md").read_text()
CHARTER = (FIXTURES / "epic-positional-transcriber.charter.md").read_text()


@pytest.fixture
def room(base_url, deck_state, monkeypatch, tmp_path, project_id):
    store = composition.open_store()
    monkeypatch.setattr(deck_state, "store", store)
    work = composition.open_work(store)
    epic = work.add(project=project_id, title="Transcriber", goal="Read", kind="epic", actor="user")
    task = work.add(project=project_id, title="Liquid Mix", goal="Map", parent=epic.id, actor="user")
    repo = tmp_path / "management"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True, timeout=10)
    return dict(base=base_url, project=project_id, epic=epic.id, task=task.id, repo=repo)


def get(room, path, **query):
    with urlopen(f"{room['base']}{path}?{urlencode(query)}", timeout=5) as response:
        return json.load(response)


def post(room, path, body, origin=None):
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    request = Request(room["base"] + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def refused(room, path, body, **query):
    with pytest.raises(HTTPError) as error:
        post(room, path, body) if body is not None else get(room, path, **query)
    return error.value.code, json.load(error.value)["error"]


def register(room):
    composition.open_records().register(room["project"], room["repo"], actor="user")


def test_a_project_without_a_repository_shows_nothing_recorded_and_its_first_save_creates_one(room):
    view = get(room, "/api/guidance", project=room["project"])
    assert (view["guidance"], view["history"]) == (None, [])
    saved = post(room, "/api/guidance", dict(project=room["project"], epic=None, markdown=CONSTITUTION, base=0))
    assert saved["guidance"]["version"]["number"] == 1


def test_edit_history_and_older_versions(room):
    register(room)
    empty = get(room, "/api/guidance", project=room["project"])
    assert (empty["guidance"], empty["name"]) == (None, "Constitution")
    first = post(room, "/api/guidance", dict(project=room["project"], epic=None, markdown=CONSTITUTION, base=0))
    assert first["guidance"]["version"]["number"] == 1 and first["guidance"]["version"]["actor"] == "web-user"
    assert first["name"] == "Constitution · version 1" and "<h2" in first["html"] and first["markdown"] == CONSTITUTION
    second = post(room, "/api/guidance", dict(project=room["project"], epic=None, markdown=CONSTITUTION + "\nMore.\n",
                                              base=1))
    assert [version["number"] for version in second["history"]] == [2, 1]
    older = get(room, "/api/guidance", project=room["project"], version=1)
    assert older["markdown"] == CONSTITUTION and older["name"] == "Constitution · version 1"
    code, message = refused(room, "/api/guidance", dict(project=room["project"], epic=None, markdown="Stale", base=1))
    assert code == 409 and "now version 2" in message
    code, message = refused(room, "/api/guidance", dict(project=room["project"], epic=None,
                                                        markdown=CONSTITUTION + "\nMore.\n", base=2))
    assert code == 400 and "unchanged" in message
    assert refused(room, "/api/guidance", None, project=room["project"], version=9)[0] == 404


def test_charter_shows_what_it_inherits(room):
    register(room)
    post(room, "/api/guidance", dict(project=room["project"], epic=None, markdown=CONSTITUTION, base=0))
    charter = post(room, "/api/guidance", dict(project=room["project"], epic=room["epic"], markdown=CHARTER, base=0))
    assert charter["name"] == "Charter: Transcriber · version 1"
    assert charter["guidance"]["inherits"]["number"] == 1 and charter["guidance"]["constitution"]["number"] == 1
    assert charter["guidance"]["path"] == f"charters/{room['epic']}.md"
    code, message = refused(room, "/api/guidance", dict(project=room["project"], epic=room["task"], markdown="x",
                                                        base=0))
    assert code == 400 and "charters belong to epics" in message


def test_decisions_and_promotion(room):
    register(room)
    decisions = composition.open_decisions()
    older = decisions.record_guided(room["task"], actor="codex", question="Store fees as freight?",
                                    answer="No, as charge lines", principle="Charter: decision 3")
    newer = decisions.record_guided(room["epic"], actor="claude", question="Rerun the flaky test?",
                                    answer="Once", principle="Constitution: test-only fixes")
    listed = get(room, "/api/decisions", epic=room["epic"])
    assert listed["charter"] is False
    assert [(d["id"], d["principle"], d["promoted"]) for d in listed["decisions"]] == [
        (newer.id, "Constitution: test-only fixes", None), (older.id, "Charter: decision 3", None)]
    code, message = refused(room, "/api/guidance/promote", dict(epic=room["epic"], decision=older.id))
    assert code == 404 and "no charter recorded" in message

    post(room, "/api/guidance", dict(project=room["project"], epic=room["epic"], markdown=CHARTER, base=0))
    promoted = post(room, "/api/guidance/promote", dict(epic=room["epic"], decision=older.id))
    assert promoted["guidance"]["version"]["number"] == 2 and promoted["guidance"]["version"]["actor"] == "web-user"
    day = older.time.date().isoformat()
    assert (f"8. {day}: Store fees as freight? — No, as charge lines (principle: Charter: decision 3; "
            f"decision {older.id[:8]} by codex)") in promoted["markdown"]
    section = promoted["markdown"].split("## Decisions in force", 1)[1].split("\n## ", 1)[0]
    assert section.rstrip().endswith(f"by codex)")
    assert [d["promoted"] for d in get(room, "/api/decisions", epic=room["epic"])["decisions"]] == [False, True]
    code, message = refused(room, "/api/guidance/promote", dict(epic=room["epic"], decision=older.id))
    assert code == 400 and "already in the charter" in message
    outside = decisions.record_guided(
        composition.open_work().add(project=room["project"], title="Other", goal="O", actor="user").id,
        actor="codex", question="Q", answer="A", principle="P")
    assert refused(room, "/api/guidance/promote", dict(epic=room["epic"], decision=outside.id))[0] == 404


def test_writes_need_json_from_this_origin(room):
    register(room)
    request = Request(room["base"] + "/api/guidance", data=b"project=p", method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=5)
    assert error.value.code == 415
    with pytest.raises(HTTPError) as error:
        post(room, "/api/guidance", dict(project=room["project"], epic=None, markdown="x", base=0),
             origin="http://elsewhere.example")
    assert error.value.code == 403
    assert refused(room, "/api/guidance", dict(project=room["project"], markdown="x"))[0] == 400
    assert refused(room, "/api/decisions", None)[0] == 400
