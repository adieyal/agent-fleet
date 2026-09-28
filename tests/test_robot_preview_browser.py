"""The robot sprite preview at /prototype/robot: served but not linked from the deck, draws the paper-doll sprite
atlases (fleet/web/assets/world/robot/sprites/) on a 2D canvas without WebGL, composites their layers, tints the
shell through the mask, keeps its anchors on the robot, and orders a walking robot around the bench."""

from collections.abc import Iterator
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page

from conftest import FIXTURE, serve_fixture

# In the page: compose a frame (robotPreview.composeCanvas) and summarise its pixels.
SUMMARY = """async o => {
  const c = await robotPreview.composeCanvas(o);
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let x0 = c.width, y0 = c.height, x1 = -1, y1 = -1, n = 0; const sum = [0, 0, 0];
  for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
    const i = (y * c.width + x) * 4;
    if (d[i + 3] > 128) { n++; sum[0] += d[i]; sum[1] += d[i + 1]; sum[2] += d[i + 2]; }
    if (d[i + 3] > 8) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  }
  const at = (o.probe || []).map(([x, y]) => d[(Math.round(y) * c.width + Math.round(x)) * 4 + 3]);
  return { size: [c.width, c.height], box: [x0, y0, x1, y1], opaque: n, mean: sum.map(v => v / Math.max(1, n)), at,
           pixels: o.keep ? Array.from(d) : null };
}"""


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with serve_fixture(FIXTURE) as u:
        yield u


@pytest.fixture
def preview(browser: Browser, url: str) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1200, "height": 760})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.goto(url + "/prototype/robot?t=3")
    page.wait_for_function("window.robotPreview && (window.robotPreview.ready || window.robotPreview.error)", timeout=60_000)
    assert page.evaluate("robotPreview.error") is None
    yield page
    page.close()
    assert errors == []


def compose(page: Page, **o) -> dict:
    return page.evaluate(SUMMARY, o)


def frame(page: Page, clip: str, d: str, i: int = 0) -> dict:
    return page.evaluate("([c, d, i]) => { const dd = robotPreview.manifest().clips[c].dirs[d]; return { ...dd.frames[i], foot: dd.foot }; }",
                         [clip, d, i])


def test_the_page_is_served_but_not_linked_from_the_deck(url: str) -> None:
    with urlopen(url + "/prototype/robot") as response:
        assert response.headers["Content-Type"].startswith("text/html")
    with urlopen(url + "/") as response:
        assert b"/prototype/" not in response.read()
    with pytest.raises(HTTPError):
        urlopen(url + "/prototype/../server.py")


def test_it_draws_on_one_2d_canvas_without_webgl(preview: Page) -> None:
    preview.wait_for_function("robotPreview.stats().frame && robotPreview.stats().frame.frames > 3")
    assert preview.locator("canvas").count() == 1
    assert not preview.evaluate("performance.getEntriesByType('resource').some(e => e.name.includes('/vendor/three/'))")
    stats = preview.evaluate("robotPreview.stats()")
    assert stats["bytes"]["sprites"] > 0 and stats["res"] in ("1x", "2x", "4x")


def test_layers_composite_into_one_robot(preview: Page) -> None:
    body = compose(preview, clip="Idle", dir="S", layers=["body"])
    full = compose(preview, clip="Idle", dir="S", face="codex", kit="halo")
    assert full["opaque"] > body["opaque"]  # the face and the halo add pixels
    assert full["box"][1] < body["box"][1]  # the halo rises above the helmet
    eyes = compose(preview, clip="Idle", dir="S", layers=["body", "face_eyes"], keep=True)["pixels"]
    band = compose(preview, clip="Idle", dir="S", layers=["body", "face_band"], keep=True)["pixels"]
    assert eyes != band
    # the face layers were rendered with the body as a holdout: from behind only the agent's back dot is left
    front = compose(preview, clip="Idle", dir="S", layers=["face_eyes"])["opaque"]
    back = compose(preview, clip="Idle", dir="N", layers=["face_eyes"])["opaque"]
    assert 0 < back < 0.5 * front


def test_the_shadow_lies_under_the_feet(preview: Page) -> None:
    f = frame(preview, "Idle", "S")
    shadow = compose(preview, clip="Idle", dir="S", layers=["shadow"], probe=[f["foot"]])
    assert shadow["at"][0] > 20
    x0, y0, x1, y1 = shadow["box"]
    assert x0 < f["foot"][0] < x1 and y0 < f["foot"][1] < y1


def test_the_host_colour_tints_the_shell_but_not_the_joints_or_visor(preview: Page) -> None:
    grey = preview.evaluate("robotPreview.manifest().grey")
    plain = compose(preview, clip="Idle", dir="S", layers=["body"], host=grey, keep=True)["pixels"]
    red = compose(preview, clip="Idle", dir="S", layers=["body"], host="#e03020", keep=True)["pixels"]
    blue = compose(preview, clip="Idle", dir="S", layers=["body"], host="#2040e0", keep=True)["pixels"]
    assert compose(preview, clip="Idle", dir="S", layers=["body"], host="#e03020")["mean"][0] > \
        compose(preview, clip="Idle", dir="S", layers=["body"], host="#2040e0")["mean"][0] + 20
    opaque = [i for i in range(0, len(plain), 4) if plain[i + 3] > 250]
    same = [i for i in opaque if red[i:i + 3] == blue[i:i + 3]]
    changed = [i for i in opaque if abs(red[i] - blue[i]) > 60]
    assert len(changed) > 0.3 * len(opaque)  # the shell
    assert len(same) > 0.1 * len(opaque)  # joints, hands and visor keep their colour
    # (the tint mask is stored blurred at 1/8: a joint's edge may move one level)
    assert all(max(abs(r - q) for r, q in zip(red[i:i + 3], plain[i:i + 3])) <= 2 for i in same[:500])


@pytest.mark.parametrize("clip,d,i", [("Idle", "S", 0), ("Walking", "E", 3), ("Wave", "W", 6), ("Typing", "S", 2)])
def test_anchors_sit_on_the_robot(preview: Page, clip: str, d: str, i: int) -> None:
    f = frame(preview, clip, d, i)
    names = ["body_low", "body_high"] if "body_low" in f["layers"] else ["body"]
    head = f["anchors"]["head_top"]
    probe = [f["anchors"]["hand_l"], f["anchors"]["hand_r"], [head[0], head[1] + 3], [head[0], head[1] - 3]]
    got = compose(preview, clip=clip, dir=d, frame=i, layers=names, probe=probe)
    x0, y0, x1, y1 = got["box"]
    hand_l, hand_r, below_head, above_head = got["at"]
    assert below_head > 200 and above_head < below_head  # the helmet's top edge (a waving hand may rise above it)
    hx, hy, hw, hh = f["hit"]
    assert hx <= x0 + 1 and hy <= y0 + 1 and hx + hw >= x1 - 1 and hy + hh >= y1 - 1  # the hit box holds the body
    assert hand_l > 0 and hand_r > 0  # the hand anchors fall on the hands


@pytest.mark.parametrize("d", ["S", "E", "N", "W"])
def test_the_foot_anchor_is_under_the_robot(preview: Page, d: str) -> None:
    # the root, between the feet: the boots and hanging fists reach past it towards the camera
    f = frame(preview, "Idle", d)
    x0, y0, x1, y1 = compose(preview, clip="Idle", dir=d, layers=["body"])["box"]
    assert x0 < f["foot"][0] < x1 and y1 - 0.25 * (y1 - y0) < f["foot"][1] < y1


def test_the_walker_is_ordered_around_the_bench(preview: Page) -> None:
    def order(foot, seated=False):
        labels = preview.evaluate("([f, s]) => robotPreview.order(f, s)", [foot, seated])
        return labels

    front = order([6.5, 2.6])
    assert front.index("robot") > front.index("bench")
    behind = order([6.5, 5.5])
    assert behind.index("robot") < behind.index("bench")
    seated = order(None, True)
    assert seated.index("robot below") < seated.index("bench") < seated.index("robot above")


def test_the_path_walks_in_front_of_and_behind_the_bench_and_sits_down(preview: Page) -> None:
    total = preview.evaluate("robotPreview.pathSeconds()")
    states = preview.evaluate("n => Array.from({ length: n }, (_, k) => robotPreview.walkerAt(k * %f / n))" % total, 400)
    walking = [s for s in states if s["clip"] == "Walking"]
    assert {s["dir"] for s in walking} == {"S", "E", "N", "W"}
    assert min(s["foot"][1] for s in walking) < 3.95 and max(s["foot"][1] for s in walking) > 4.75  # either side
    clips = [s["clip"] for s in states]
    assert {"Sitting", "Typing", "StandUp"} <= set(clips)
    assert all(s.get("atDesk") for s in states if s["clip"] in ("Sitting", "Typing", "StandUp"))


def test_the_stalled_look_dims_the_body_and_face(preview: Page) -> None:
    normal = compose(preview, clip="Idle", dir="S", host="#2dd4bf", look="normal")["mean"]
    stalled = compose(preview, clip="Idle", dir="S", host="#2dd4bf", look="stalled")["mean"]
    assert sum(stalled) < sum(normal) - 30


def test_every_clip_is_drawn_in_every_facing_it_lists(preview: Page) -> None:
    clips = preview.evaluate("Object.fromEntries(Object.entries(robotPreview.manifest().clips).map(([k, c]) => [k, Object.keys(c.dirs)]))")
    assert {"Walking", "Idle", "Wave", "Yes", "No", "Death", "ThumbsUp", "StandUp", "Sitting", "SitIdle", "Typing",
            "Writing", "SitRead", "Holding", "SitThumbsUp", "Slump", "SitSlump", "SitNod", "SitShake", "BoxIdle",
            "BoxWalk", "BookWalk", "SheetWalk"} <= set(clips)
    for clip, dirs in clips.items():
        for d in dirs:
            assert compose(preview, clip=clip, dir=d)["opaque"] > 500, (clip, d)


def test_a_seated_robots_shadow_lies_under_its_chair(preview: Page) -> None:
    # the rebuilt robot's feet hang clear of the floor on its raised chair: its shadow is drawn under the chair, not on
    # the floor under the seat point, which this camera shows below the desk's near edge, detached from the robot
    out = preview.evaluate("""(() => { const P = robotPreview, seat = P.seat(1);
      return { seat, at: P.shadowPoint(seat), behind: P.manifest().seat_furniture.chair_behind_m,
               seated: P.layersOf('Typing', 'S'), standing: P.layersOf('Idle', 'S') }; })()""")
    assert out["at"] == pytest.approx([out["seat"][0], out["seat"][1] + out["behind"], 0])
    assert "shadow" not in out["seated"][0] and "shadow" in out["standing"][0]   # (drawn with the chair instead)
