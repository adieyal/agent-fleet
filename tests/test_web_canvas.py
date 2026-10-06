"""The canvas page and its HTTP contract: reads, one operation per write, refusals as 409 with their source."""
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet.container import configured_container
from fleet.services.live import FleetState
from fleet_web.server import make_handler


@pytest.fixture
def deck(monkeypatch):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    container = configured_container()
    project = container.initialized_workspace().edit_registry(lambda registry: registry.create("web-canvas")).id
    state = FleetState([], container=container)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", project=project, state=state)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get(deck, path):
    with urlopen(deck.url + path, timeout=5) as response:
        return response.status, response.read()


def post(deck, path, body, *, origin=True, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if origin:
        headers["Origin"] = deck.url
    request = Request(deck.url + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def test_the_canvas_page_and_an_uninitialised_space(deck):
    status, page = get(deck, f"/canvas/{deck.project}")
    assert status == 200 and b'src="/js/canvas/app.js"' in page
    status, body = get(deck, "/api/canvas/spaces")
    listing = json.loads(body)
    assert listing["spaces"] == [] and listing["projects"] == [{"id": deck.project, "name": "web-canvas"}]
    with pytest.raises(HTTPError) as missing:
        get(deck, f"/api/canvas?space={deck.project}")
    assert missing.value.code == 404


def test_operations_refusals_and_layout_over_http(deck):
    before = deck.state.version
    status, body = post(deck, "/api/canvas/init", {"space": deck.project})
    assert status == 200 and body["result"] == {"created": True}
    assert deck.state.version > before
    status, body = post(deck, "/api/canvas/op", {"space": deck.project, "op": "item.create", "args": {"title": "Card"},
                                                 "op_id": "web-1"})
    assert status == 200 and body["ok"]
    identity = body["result"]["item"]
    status, refusal = post(deck, "/api/canvas/op", {"space": deck.project, "op": "item.move",
                                                    "args": {"item": identity, "stage": "approve"}})
    assert status == 409 and refusal["code"] == "stage_skipped" and refusal["source"]["object"] == "workflow"
    status, _ = post(deck, "/api/canvas/layout", {"space": deck.project, "object": "doc:spec", "props": {"x": 1, "y": 2}})
    assert status == 200
    model = json.loads(get(deck, f"/api/canvas?space={deck.project}")[1])
    assert model["layout"] == {"doc:spec": {"x": 1, "y": 2}}
    assert model["items"][0]["title"] == "Card" and model["log"][-1]["tone"] == "refuse"
    events = json.loads(get(deck, f"/api/canvas/events?space={deck.project}&after=0")[1])["events"]
    assert events[0]["seq"] < events[-1]["seq"]


def test_writes_must_be_same_origin_json(deck):
    status, body = post(deck, "/api/canvas/op", {"space": deck.project, "op": "tick"}, content_type="text/plain")
    assert status == 415
    request = Request(deck.url + "/api/canvas/op", data=b"{}", method="POST",
                      headers={"Content-Type": "application/json", "Origin": "http://evil.example"})
    with pytest.raises(HTTPError) as refused:
        urlopen(request, timeout=5)
    assert refused.value.code == 403
    status, body = post(deck, "/api/canvas/op", {"space": deck.project})
    assert status == 400


def test_compile_preview_reads_code_without_saving(deck):
    status, body = post(deck, "/api/canvas/compile", {"text": 'zone "Parked"\n  on enter:\n    pause runs\n    hum'})
    assert status == 200 and [line["marker"] for line in body["lines"]] == ["", "", "✓", "~"]
    status, body = post(deck, "/api/canvas/compile", {"text": "nothing here"})
    assert status == 200 and "header" in body["error"]
