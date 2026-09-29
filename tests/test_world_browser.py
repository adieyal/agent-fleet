"""The sprite engine in the browser, on /prototype/world (an l2-like scene from the floor kit): pan and zoom stay between the
whole-room and the l2 framing, sprite tiers load only when the zoom needs them and crossfade in, a still scene
stops drawing, ambient animation slows when frames run over budget, and clicks find what was drawn there."""

from collections.abc import Iterator
from typing import Any
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page


def open_world(browser: Browser, base_url: str, query: str = "", scale: float = 1) -> tuple[Page, list[str]]:
    page = browser.new_page(viewport={"width": 1000, "height": 600}, device_scale_factor=scale)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/prototype/world?shot&{query}")
    page.wait_for_function("window.world && (window.world.ready || window.world.error)", timeout=60_000)
    assert page.evaluate("window.world.error") is None
    return page, errors


@pytest.fixture
def world(browser: Browser, base_url: str) -> Iterator[Page]:
    page, errors = open_world(browser, base_url)
    yield page
    page.close()
    assert errors == []


def camera(page: Page) -> dict[str, Any]:
    return page.evaluate("""(() => { const c = world.engine.camera;
      return { cur: c.cur, goal: c.goal, min: c.min.ppm, max: c.max.ppm, far: { u: c.min.u, v: c.min.v } }; })()""")


def settle(page: Page) -> None:
    # (and no ground snapshot still painting in its worker: its arrival repaints the whole view)
    page.wait_for_function("!world.engine.camera.moving && !world.engine.raf && !world.engine.ground.building"
                           " && !world.engine.ground.prev", timeout=10_000)


def wheel(page: Page, dy: float, times: int, at: tuple[int, int] = (500, 300)) -> None:
    page.mouse.move(*at)
    for _ in range(times):
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(20)
    settle(page)


def drag(page: Page, dx: float, dy: float, start: tuple[int, int] = (500, 300)) -> None:
    page.mouse.move(*start)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(start[0] + dx * k / 10, start[1] + dy * k / 10)
    page.mouse.up()
    settle(page)


def test_the_page_is_served_but_not_linked_and_needs_no_webgl(world: Page, base_url: str) -> None:
    with urlopen(base_url + "/") as response:
        assert b"/prototype/world" not in response.read()
    assert not world.evaluate("performance.getEntriesByType('resource').some(e => e.name.includes('/vendor/'))")
    assert world.evaluate("world.engine.stats.missing") == []


def test_it_opens_on_the_whole_room_and_zooms_no_further_out(world: Page) -> None:
    c = camera(world)
    assert c["cur"]["ppm"] == pytest.approx(c["min"])
    wheel(world, 400, 10)
    c = camera(world)
    assert c["cur"]["ppm"] == pytest.approx(c["min"])
    assert c["max"] / c["min"] > 1.5


def test_zooming_in_stops_at_the_l2_framing(world: Page) -> None:
    wheel(world, -400, 25)
    c = camera(world)
    assert c["cur"]["ppm"] == pytest.approx(c["max"])
    assert c["max"] == pytest.approx(600 / 5.486)  # l2 shows 5.486 m top to bottom


def test_zoom_eases_rather_than_jumping(world: Page) -> None:
    world.mouse.move(500, 300)
    world.mouse.wheel(0, -600)
    world.wait_for_timeout(40)
    c = camera(world)
    assert c["cur"]["ppm"] < c["goal"]["ppm"]
    settle(world)
    c = camera(world)
    assert c["cur"]["ppm"] == pytest.approx(c["goal"]["ppm"])


def test_the_whole_room_view_cannot_be_dragged_away(world: Page) -> None:
    before = camera(world)["cur"]
    drag(world, 300, -200)
    after = camera(world)["cur"]
    assert (after["u"], after["v"]) == pytest.approx((before["u"], before["v"]))


def test_zoomed_in_a_drag_pans_but_stops_at_the_room_edge(world: Page) -> None:
    wheel(world, -400, 25)
    start = camera(world)["cur"]
    drag(world, 150, 0)
    moved = camera(world)["cur"]
    assert moved["u"] == pytest.approx(start["u"] - 150 / start["ppm"], abs=0.05)  # the room follows the pointer
    for _ in range(6):
        drag(world, 400, 300)
    edge = camera(world)["cur"]
    for _ in range(3):
        drag(world, 400, 300)
    assert camera(world)["cur"] == pytest.approx(edge)
    bounds = world.evaluate("world.engine.camera.bounds")
    assert bounds["x"] <= edge["u"] <= bounds["x"] + bounds["w"]
    assert bounds["y"] <= edge["v"] <= bounds["y"] + bounds["h"]


def test_the_smallest_sharp_tier_is_drawn_the_next_loads_ahead_and_they_fade(browser: Browser, base_url: str) -> None:
    page, errors = open_world(browser, base_url, scale=2)
    try:
        # the room at 2x device pixels needs under 171.5 px/m: the plant's 1x tier (its tiers are 0.5x, 1x and 2x),
        # and at rest its 2x loads ahead
        plant = "world.engine.sprites.get('kit/plant-bush')"
        assert page.evaluate("world.engine.stats.requested").count("plant-bush@172.webp") == 1
        assert page.evaluate(plant + ".shown") == 1
        page.wait_for_function("world.engine.stats.requested.includes('plant-bush@343.webp')")
        shown = []
        page.expose_function("noteShown", lambda s: shown.append(s))
        page.evaluate("""(() => { const w = world.engine, f = w.frame.bind(w);
          w.frame = () => { f(); noteShown(w.sprites.get('kit/plant-bush').shown); }; })()""")
        wheel(page, -400, 25)
        page.wait_for_function("world.engine.stats.loaded.includes('plant-bush@343.webp')")
        settle(page)
        requested = page.evaluate("world.engine.stats.requested")
        assert requested.count("plant-bush@343.webp") == 1
        assert page.evaluate("world.engine.stats.fades") >= 1
        assert -1 not in shown  # never a frame without the plant
        # l2 at 2x device pixels is 219 px/m: 2x (343) is drawn
        assert page.evaluate(plant + ".shown") == 2
    finally:
        page.close()
    assert errors == []


def test_ground_added_after_the_first_frame_shows_up(world: Page) -> None:
    # regression: a change started a new snapshot, but the old one then counted as current and the new was dropped
    settle(world)
    colour = world.evaluate("""(async () => {
      const e = world.engine, m = await import('/js/world/projection.js');
      e.addPlane({ quad: [[1, -3, 0], [3, -3, 0], [3, -1, 0], [1, -1, 0]], color: '#ff00ff' });
      await new Promise(r => { const t = () => (e.ground.building || e.raf ? setTimeout(t, 30) : r()); setTimeout(t, 30); });
      const [x, y] = m.toScreen(e.camera.view, [2, -2, 0]);
      return [...e.g.getImageData(x * e.dpr, y * e.dpr, 1, 1).data].slice(0, 3);
    })()""")
    assert colour == [255, 0, 255]


def test_a_still_scene_draws_nothing(browser: Browser, base_url: str) -> None:
    page, errors = open_world(browser, base_url, "anim=0")
    try:
        settle(page)
        page.wait_for_timeout(300)
        frames = page.evaluate("world.engine.stats.frames")
        page.wait_for_timeout(1000)
        assert page.evaluate("world.engine.stats.frames") == frames
        assert page.evaluate("world.engine.raf || world.engine.timer") == 0
        drag(page, 0, 0)  # a tap: still nothing to draw
        wheel(page, -300, 3)
        assert page.evaluate("world.engine.stats.frames") > frames  # and a zoom wakes it
    finally:
        page.close()
    assert errors == []


def test_animation_repaints_only_the_robots_at_their_frame_rate(world: Page) -> None:
    settle(world)
    before = world.evaluate("({...world.engine.stats})")
    world.wait_for_timeout(1000)
    after = world.evaluate("({...world.engine.stats})")
    assert after["full"] == before["full"]
    assert 3 <= after["partial"] - before["partial"] <= 14  # loops at 6.7, 4 and 1.9 fps


def test_ambient_animation_slows_when_frames_run_over_budget(browser: Browser, base_url: str) -> None:
    page, errors = open_world(browser, base_url, "budget=0.001")
    try:
        page.wait_for_function("world.engine.throttle === 8", timeout=15_000)   # (its ceiling: no more speed changes)
        settle(page)
        # count the typing robot's frame changes (other repaints, a late tier fading in, don't count)
        changes = page.evaluate("""() => new Promise(done => {
          const it = world.engine.items.get('robot-0:high'); let last = it.frame, n = 0;
          const t = setInterval(() => { if (it.frame !== last) { n++; last = it.frame; } }, 20);
          setTimeout(() => { clearInterval(t); done(n); }, 2000);
        })""")
        assert changes <= 3  # a 6.7 fps loop at an eighth of its speed; 13 unthrottled
    finally:
        page.close()
    assert errors == []


def screen(page: Page, point: list[float]) -> list[float]:
    return page.evaluate("p => import('/js/world/projection.js').then(m => m.toScreen(world.engine.camera.view, p))", point)


def test_clicks_find_the_robot_the_lantern_and_the_floor(world: Page) -> None:
    for point in ([3.1, 5.25, 2.25], [2.0, 1.0, 0]):   # the lantern and a spot of floor, in the whole-room view
        world.mouse.click(*screen(world, point))
    wheel(world, -400, 25)
    world.mouse.click(*screen(world, [4.6, 4.91, 0.95]))   # the typing robot's head, above the desk top
    lantern, floor, robot = world.evaluate("world.taps")
    assert (lantern["id"], lantern["place"]) == ("lantern", "attention")
    assert floor["floor"] == pytest.approx([2.0, 1.0], abs=0.05)
    assert (robot["id"], robot["place"]) == ("robot-0", "run:teal")


def test_a_seated_robots_legs_are_behind_the_desk(world: Page) -> None:
    # below the desk-top line the bench is in front: a click there finds the bench, not the robot's lower layer
    wheel(world, -400, 25)
    pedestal = screen(world, [4.16, 4.2, 0.45])   # the first desk's drawer pedestal
    assert world.evaluate("([x, y]) => world.engine.pick(x, y)", pedestal)["id"] == "bench"


def test_a_frame_of_a_sheet_is_drawn_without_its_neighbours_pixels(world: Page) -> None:
    # the bake-off pencil robot's frame 2 is opaque down its right edge; frame 3, scaled up, must not show that column
    # down its left edge (the thin line beside a seated robot, floor review 2). (The scene no longer places it: it is
    # defined here from the bake-off's manifest, as a sheet with that edge.)
    world.evaluate("""(async () => {
      const url = new URL('/art/bakeoff/world.json', location.href), s = (await fetch(url).then(r => r.json())).sprites['robot-pencil'];
      world.engine.define('robot-pencil', { ...s, tiers: s.tiers.map(t => ({ ...t, url: new URL(t.file, url).href })) });
      world.engine.use('robot-pencil'); })()""")
    world.wait_for_function("world.engine.sprites.get('robot-pencil').loaded.size > 0", timeout=20_000)
    out = world.evaluate("""(() => {
      const e = world.engine, s = e.sprites.get('robot-pencil'), i = [...s.loaded][0], t = s.tiers[i];
      const c = document.createElement('canvas'); c.width = t.fw * 4; c.height = t.fh * 4;
      const g = c.getContext('2d');
      g.drawImage(e.cellOf(s, i, null, 3), 0, 0, c.width, c.height);
      const col = g.getImageData(0, 0, 1, c.height).data;
      let max = 0; for (let k = 3; k < col.length; k += 4) max = Math.max(max, col[k]);
      const own = document.createElement('canvas'); own.width = 1; own.height = t.fh;
      own.getContext('2d').drawImage(s.img[i], 3 * t.fw, 0, 1, t.fh, 0, 0, 1, t.fh);
      const src = own.getContext('2d').getImageData(0, 0, 1, t.fh).data;
      let srcMax = 0; for (let k = 3; k < src.length; k += 4) srcMax = Math.max(srcMax, src[k]);
      return { drawn: max, own: srcMax };
    })()""")
    # the left column of the drawn frame is no stronger than the frame's own left column
    assert out["drawn"] <= out["own"] + 2, out
