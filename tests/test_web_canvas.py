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


def test_chat_task_previews_adoption_and_delete_confirmation(deck, page, request):
    from pathlib import Path
    from playwright.sync_api import expect

    assert post(deck, "/api/canvas/init", {"space": deck.project})[0] == 200
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{deck.url}/canvas/{deck.project}")

    def send(text):
        page.locator("#cmd").fill(text)
        page.locator("#cmd").press("Enter")
        expect(page.locator(".cv-prop").last).to_be_visible()

    def model():
        return json.loads(get(deck, f"/api/canvas?space={deck.project}")[1])

    send("create task Browser task")
    expect(page.locator(".cv-prop").last.locator("pre")).to_contain_text('"op": "item.create"')
    assert model()["items"] == []
    shots = request.config.getoption("--shots")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / "chat-create-preview.png"), full_page=True)
    page.locator(".cv-prop").last.get_by_role("button", name="Discard", exact=True).click()
    expect(page.locator(".cv-prop").last).to_contain_text("Discarded")
    assert model()["items"] == []
    send("create task Browser task")
    page.locator(".cv-prop").last.get_by_role("button", name="Adopt", exact=True).click()
    expect(page.locator(".cv-prop").last).to_contain_text("Adopted")
    identity = model()["items"][0]["id"]
    send(f"rename task {identity} to Renamed task")
    assert model()["items"][0]["title"] == "Browser task"
    page.locator(".cv-prop").last.get_by_role("button", name="Adopt", exact=True).click()
    expect(page.locator(".cv-prop").last).to_contain_text("Adopted")
    assert model()["items"][0]["title"] == "Renamed task"
    send(f"move task {identity} to Plan")
    assert model()["items"][0]["stage"] is None
    page.locator(".cv-prop").last.get_by_role("button", name="Adopt", exact=True).click()
    expect(page.locator(".cv-prop").last).to_contain_text("Adopted")
    assert model()["items"][0]["stage"] == "plan"
    send(f"delete task {identity}")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator(".cv-prop").last.get_by_role("button", name="Adopt", exact=True).click()
    assert len(model()["items"]) == 1
    if shots:
        page.screenshot(path=str(Path(shots) / "chat-delete-preview.png"), full_page=True)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(".cv-prop").last.get_by_role("button", name="Adopt", exact=True).click()
    expect(page.locator(".cv-prop").last).to_contain_text("Adopted")
    assert model()["items"] == []


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


@pytest.fixture
def epic_tasks(deck):
    """Two epics and a loose task, isolated from the user's Fleet store."""
    post(deck, "/api/canvas/init", {"space": deck.project})

    def operation(name, **args):
        status, body = post(deck, "/api/canvas/op", {"space": deck.project, "op": name, "args": args})
        assert status == 200, body
        return body["result"]

    epic = operation("epic.create", title="Release", criteria=["Ship docs"])["epic"]
    other = operation("epic.create", title="Other epic")["epic"]
    child = operation("item.create", title="Write release docs", epic=epic)["item"]
    sibling = operation("item.create", title="Check release docs", epic=epic)["item"]
    outside = operation("item.create", title="Unrelated task", epic=other)["item"]
    loose = operation("item.create", title="Loose task")["item"]
    return SimpleNamespace(epic=epic, other=other, child=child, sibling=sibling, outside=outside, loose=loose)


def test_collapsed_epic_render_hides_only_its_tasks(deck, epic_tasks):
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise the canvas renderer")
    model = json.loads(get(deck, f"/api/canvas?space={deck.project}")[1])
    model["deps"] = [{"from": epic_tasks.child, "to": epic_tasks.outside},
                     {"from": epic_tasks.outside, "to": epic_tasks.loose}]
    module = Path(__file__).resolve().parents[1] / "packages/fleet-web/src/fleet_web/static/js/canvas/render.js"
    script = """
import { readFileSync } from 'node:fs';
const { render } = await import(process.argv[1]);
const { model, epic } = JSON.parse(readFileSync(0, 'utf8'));
const ui = { mode: 'canvas', pan: {x: 0, y: 0}, zoom: 0.56, pending: {}, live: {ok: true} };
const expanded = render(model, ui);
const collapsed = render(model, {...ui, collapsedEpics: [epic]});
console.log(JSON.stringify({expanded, collapsed}));
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module.as_uri()],
                            input=json.dumps({"model": model, "epic": epic_tasks.epic}),
                            text=True, capture_output=True, check=True)
    pages = json.loads(result.stdout)
    for identity in (epic_tasks.child, epic_tasks.sibling):
        assert f'data-key="t-{identity}"' in pages["expanded"]
        assert f'data-key="t-{identity}"' not in pages["collapsed"]
    for identity in (epic_tasks.outside, epic_tasks.loose):
        assert f'data-key="t-{identity}"' in pages["collapsed"]
    assert f'data-key="e-{epic_tasks.epic}"' in pages["collapsed"]
    assert f'data-key="a-{epic_tasks.child}-{epic_tasks.outside}"' not in pages["collapsed"]
    assert f'data-key="a-{epic_tasks.outside}-{epic_tasks.loose}"' in pages["collapsed"]
    assert 'aria-expanded="false"' in pages["collapsed"]
    assert "2 tasks hidden" in pages["collapsed"]
    assert pages["expanded"].count('class="cv-kid"') == 3
    assert pages["collapsed"].count('class="cv-kid"') == 1
    assert "Write release docs" not in pages["collapsed"]
    assert "Check release docs" not in pages["collapsed"]


def test_epic_task_toggle_persists_and_expands_on_enter(deck, epic_tasks, page, request):
    from pathlib import Path
    from playwright.sync_api import expect

    page.goto(f"{deck.url}/canvas/{deck.project}")
    page.set_viewport_size({"width": 1440, "height": 1000})
    expect(page.locator(f'[data-key="e-{epic_tasks.epic}"]')).to_have_count(1)
    # Position the epic in the viewport without changing shared layout.
    page.evaluate("""(project) => localStorage.setItem('fleet-canvas:' + project,
        JSON.stringify({pan: {x: 0, y: 120 - parseFloat(document.querySelector('.cv-epic').style.top) * 0.56},
                        zoom: 0.56, mode: 'canvas'}))""", deck.project)
    page.reload()
    epic = page.locator(f'[data-key="e-{epic_tasks.epic}"]')
    child = page.locator(f'[data-key="t-{epic_tasks.child}"]')
    expect(child).to_have_count(1)
    epic.get_by_role("button", name="Hide tasks (2)").click()
    expect(child).to_have_count(0)
    expect(epic.get_by_role("button", name="Show tasks (2)")).to_have_attribute("aria-expanded", "false")
    expect(page.locator(f'[data-key="t-{epic_tasks.outside}"]')).to_have_count(1)
    page.reload()
    expect(child).to_have_count(0)
    shots = request.config.getoption("--shots")
    if shots:
        directory = Path(shots)
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / "epic-collapsed.png"), full_page=True)
    epic.get_by_role("button", name="Show tasks (2)").click()
    expect(child).to_have_count(1)
    epic.get_by_role("button", name="Hide tasks (2)").click()
    # Enter epic is also available in its inspector when its child list is hidden.
    epic.locator('[data-drag^="epic:"]').click()
    page.locator('.cv-drawer').get_by_role("button", name="Enter epic", exact=True).click()
    expect(child).to_have_count(1)
    if shots:
        page.screenshot(path=str(directory / "epic-expanded.png"), full_page=True)
    page.reload()
    expect(child).to_have_count(1)
