"""The floor: routes (fleet/web/js/world/nav.js) and the layout (layout.js) as pure functions, run in the browser,
and the Restoke floor at /prototype/floor: robots walk from the lift to their desks and sit, lamps glow only at
active runs, tiles follow steps, and a click on a bench zooms onto it."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page

KIT = json.loads((Path(__file__).parent.parent / "fleet" / "web" / "assets" / "world" / "kit" / "manifest.json").read_text())


@pytest.fixture(scope="module")
def page(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page()
    page.goto(base_url + "/api/state")
    yield page
    page.close()


@pytest.fixture(scope="module")
def state(base_url: str) -> dict[str, Any]:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return json.load(response)


def nav(page: Page, body: str, arg: Any = None) -> Any:
    return page.evaluate(f"([arg]) => import('/js/world/nav.js').then(m => ({body})(m, arg))", [arg])


def test_a_route_goes_around_furniture_and_stays_clear(page: Page) -> None:
    out = nav(page, """m => {
      const g = m.navGrid({ x0: 0, y0: 0, x1: 10, y1: 6 }, [[4, 0, 5, 4.5]]);   // a wall-like block, open at the top
      const pts = m.route(g, [1, 1], [9, 1]);
      return { pts, top: Math.max(...pts.map(p => p[1])), straight: m.route(g, [1, 5.5], [9, 5.5]).length };
    }""")
    assert out["pts"][0] == [1, 1] and out["pts"][-1] == [9, 1]
    assert out["top"] > 4.5 + 0.25  # over the block, clear of it by a robot's radius
    assert out["straight"] == 2     # an open line is one segment


def test_no_route_when_walled_in(page: Page) -> None:
    assert nav(page, "m => m.route(m.navGrid({ x0: 0, y0: 0, x1: 10, y1: 6 }, [[4, -1, 5, 7]]), [1, 1], [9, 1])") is None


def test_walking_along_a_route(page: Page) -> None:
    out = nav(page, "m => { const pts = [[0, 0], [3, 0], [3, 4]]; return [m.length(pts), m.along(pts, 1), m.along(pts, 5), m.along(pts, 9)]; }")
    assert out[0] == 7
    assert out[1]["at"] == [1, 0] and out[1]["heading"] == 0 and not out[1]["done"]
    assert out[2]["at"] == [3, 2] and out[2]["heading"] == pytest.approx(1.5708, abs=1e-3)
    assert out[3]["done"]


def layout(page: Page, state: dict[str, Any]) -> dict[str, Any]:
    return page.evaluate("""([state, kit]) => Promise.all([import('/js/world/layout.js'), import('/js/workarea-model.js')])
      .then(([L, W]) => { const room = W.workareaOf(state, 'restoke', state.time); return L.floorLayout(room, kit, h => '#' + h.length + '0a0a0'); })""",
                         [state, KIT["sprites"]])


def test_the_restoke_floor_seats_its_running_jobs_at_the_workarea_bench(page: Page, state: dict[str, Any]) -> None:
    out = layout(page, state)
    running = sorted(f"{h['name']}:{j['id']}" for h in state["hosts"] for j in h["jobs"]
                     if j.get("project") == "restoke" and j["status"] == "running")
    assert len(running) == 3
    assert sorted(r["key"] for r in out["runs"]) == running
    assert {r["bench"] for r in out["runs"]} == {"bench-0"}
    assert sorted(r["desk"] for r in out["runs"]) == [0, 1, 2]
    after = next(b for b in out["benches"] if b["key"] == "bench-1")
    # the done job ran in the last hour; the failed one is older, so only its blocker shows (on the lantern)
    assert after["jobs"] == ["worker:d4f7a2"]
    assert len(out["benches"]) == 6  # the workarea and l1's five on the open floor


def test_lamps_glow_only_at_active_runs(page: Page, state: dict[str, Any]) -> None:
    items = {i["id"]: i for i in layout(page, state)["items"]}
    # every desk's lamp throws a pool; only desks with a run at work are bright
    bright = sorted(k for k, i in items.items() if k.startswith("pool-") and i["intensity"] == 1)
    assert bright == ["pool-0-0", "pool-0-1", "pool-0-2"]
    assert all(0 < i["intensity"] < 0.5 for k, i in items.items() if k.startswith("pool-") and k not in bright)
    assert all(items[f"wash-{dx}"]["intensity"] == 1 for dx in (-1.4, 0, 1.4))  # the workarea is live
    assert all(items[f"pool-1-{d}"]["intensity"] < 0.5 for d in range(3))  # a done job's desk is dim


def test_tiles_follow_step_states(page: Page, state: dict[str, Any]) -> None:
    out = layout(page, state)
    items = {i["id"]: i for i in out["items"]}
    rows = KIT["sprites"]["plan-wall"]["slots"]["tiles"]["rows"]
    jobs = {f"{h['name']}:{j['id']}": j for h in state["hosts"] for j in h["jobs"]}
    work = next(b for b in out["benches"] if b["key"] == "bench-0")
    for d, key in enumerate(work["jobs"]):
        steps = sorted(jobs[key]["steps"], key=lambda s: s["index"])
        want = [{"done": "tile-done", "running": "tile-running", "failed": "tile-failed"}.get(s["status"], "tile-blank") for s in steps]
        assert [items[f"tile-{c}-{rows - 1 - d}"]["sprite"] for c in range(len(steps))] == want
    assert all(items[f"tile-{c}-0"]["sprite"] == "tile-blank" for c in range(10))  # rows beyond the desks stay blank


def test_the_question_desk_has_the_lantern_and_a_waiting_session_a_crate(page: Page, state: dict[str, Any]) -> None:
    items = {i["id"]: i for i in layout(page, state)["items"]}
    assert items["lantern"]["lantern"] == {"count": 2, "level": "open", "kind": "blocker"}
    assert "crate" not in items  # the page sets room.waiting; the layout alone has none


@pytest.fixture(scope="module")
def floor(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1200, "height": 700})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/prototype/floor?shot")
    page.wait_for_function("window.floor && (window.floor.ready || window.floor.error)", timeout=60_000)
    yield page
    page.close()
    assert errors == []


def test_robots_walk_from_the_lift_and_sit(floor: Page) -> None:
    assert floor.evaluate("floor.error") is None
    assert floor.evaluate("floor.engine.stats.missing") == []
    assert floor.evaluate("floor.walkers.every(w => w.pts && w.L > 3)")
    floor.wait_for_function("floor.walkers.some(w => w.state === 'walking')", timeout=10_000)
    lift_open = floor.evaluate("floor.engine.items.get('lift').cell")
    assert lift_open > 0
    floor.wait_for_function("floor.seated.length === floor.walkers.length", timeout=60_000)
    assert floor.evaluate("[...floor.engine.items.keys()].filter(k => k.startsWith('walker-')).length") == 0
    assert floor.evaluate("floor.layout.runs.every(r => !floor.engine.items.get(r.chair).visible)")
    floor.wait_for_function("floor.engine.items.get('lift').cell === 0")


def test_a_click_on_a_bench_zooms_onto_it(floor: Page) -> None:
    point = floor.evaluate("""import('/js/world/projection.js').then(p => {
      const e = floor.engine, b = floor.layout.benches.find(b => b.key === 'bench-1');
      return p.toScreen(e.camera.view, [b.at[0] + 1.8, b.at[1] - 0.2, 0.74]); })""")
    floor.mouse.click(*point)
    floor.wait_for_function("!floor.engine.camera.moving", timeout=10_000)
    cam = floor.evaluate("({ cur: floor.engine.camera.cur, max: floor.engine.camera.max.ppm })")
    assert cam["cur"]["ppm"] == pytest.approx(cam["max"])
    target = floor.evaluate("""import('/js/world/projection.js').then(p =>
      p.plane(floor.layout.benches.find(b => b.key === 'bench-1').frame.target))""")
    assert (cam["cur"]["u"], cam["cur"]["v"]) == pytest.approx(tuple(target), abs=0.05)
    floor.keyboard.press("Escape")
    floor.wait_for_function("!floor.engine.camera.moving", timeout=10_000)
    assert floor.evaluate("floor.engine.camera.zoomLevel()") == pytest.approx(0, abs=1e-6)


def test_working_robots_show_their_action_only_zoomed_in(floor: Page) -> None:
    floor.wait_for_function("floor.seated.length === floor.walkers.length", timeout=60_000)
    floor.evaluate("floor.engine.camera.jump(floor.engine.camera.min); floor.engine.request()")
    floor.wait_for_timeout(200)
    assert floor.evaluate("floor.bubbles.every(b => b.el.hidden)")
    floor.evaluate("floor.zoomTo('bench-0')")
    floor.wait_for_function("!floor.engine.camera.moving", timeout=10_000)
    floor.wait_for_timeout(200)
    shown = floor.evaluate("floor.bubbles.filter(b => !b.el.hidden).map(b => [b.el.querySelector('.glyph').dataset.action, b.el.style.color])")
    assert len(shown) == 3
    assert all(action for action, _ in shown)
    # the glyph takes the robot's host colour, never the attention magenta
    assert all("166, 14, 155" not in colour and "#a60e9b" not in colour for _, colour in shown)


def test_the_lift_panel_numbers_its_floors_and_marks_this_one(floor: Page) -> None:
    buttons = floor.evaluate("[...document.querySelectorAll('.floor-button')].map(b => [b.textContent, b.className])")
    assert [b[0] for b in buttons] == [str(i) for i in range(1, 7)]
    assert sum("here" in b[1] for b in buttons) == 1


def test_ambient_throttle_recovers_once_frames_are_cheap_again(browser: Browser, base_url: str) -> None:
    page = browser.new_page(viewport={"width": 1000, "height": 600})
    try:
        page.goto(f"{base_url}/prototype/floor?shot&seated")
        page.wait_for_function("window.floor && window.floor.ready", timeout=60_000)
        page.evaluate("floor.engine.budget = 0.001")
        page.wait_for_function("floor.engine.throttle >= 4", timeout=15_000)
        page.evaluate("floor.engine.budget = 1000")
        page.wait_for_function("floor.engine.throttle === 1", timeout=15_000)
    finally:
        page.close()


def test_motion_draws_at_ratio_one_and_rests_sharp(browser: Browser, base_url: str) -> None:
    page = browser.new_page(viewport={"width": 1000, "height": 600}, device_scale_factor=2)
    try:
        page.goto(f"{base_url}/prototype/floor?shot&seated&motion=low")
        page.wait_for_function("window.floor && window.floor.ready", timeout=60_000)
        page.evaluate("""(() => { const e = floor.engine, f = e.paintLow.bind(e); window.lows = 0;
          e.paintLow = (...a) => { window.lows++; return f(...a); }; floor.zoomTo('bench-0'); })()""")
        page.wait_for_function("!floor.engine.camera.moving && !floor.engine.raf", timeout=20_000)
        assert page.evaluate("window.lows") > 0
        assert page.evaluate("floor.engine.wasLow") is False  # the frame at rest was drawn at full ratio
    finally:
        page.close()
