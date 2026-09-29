"""Screenshot the building view (L0) over a seeded fleet, measure its first paint and a state change's redraw, and set
it beside l0.png.

    uv run python art/scripts/shoot_building.py [OUT_DIR]

The fleet is the test fixture (tests/fixtures/restoke.json) with more projects moved in, so every kind of floor shows:
Restoke on floor 1 (open, working), Invoice analysis on 2 (behind glass, working), Research notes on 3 (open, idle),
Supplier catalogue on 4 (behind glass, idle), Old docs packed away in the storehouse (a crate), 5 and 6 to let, and
one lantern (Restoke's; Invoice analysis's item is snoozed). Writes building-1600x950.png, building-390.png,
building-vs-l0.png and perf.json to OUT_DIR (default art/build/building-shots).
"""
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tests'))
from conftest import FIXTURE, serve_fixture  # noqa: E402

CONCEPT = REPO / 'docs' / 'images' / 'concept' / 'l0.png'
INVOICES, RESTOKE = 'p-1c0ce5a2', 'p-5e1f0a01'
BUILT = "fleetBuilding.floors().length > 0 && fleetBuilding.floors().every(f => f.built)"


def seeded(path: Path) -> Path:
    fixture = json.loads(FIXTURE.read_text())
    fixture['projects'].update({
        'p-0000aa01': {'name': 'Research notes', 'links': [], 'repositories': []},
        'p-0000aa02': {'name': 'Supplier catalogue', 'links': [], 'repositories': []},
        'p-0000aa03': {'name': 'Old docs', 'links': [], 'repositories': []},
    })
    fixture['floors'].update({'p-0000aa01': 3, 'p-0000aa02': 4})
    fixture['focus']['projects']['p-0000aa02'] = 'background'
    fixture['shuttered'] = {'p-0000aa03': {'at': fixture['time'] - 3600, 'floor': 5}}
    path.write_text(json.dumps(fixture))
    return path


def post(url: str, path: str, body: dict) -> None:
    request = Request(url + path, data=json.dumps(body).encode(), method='POST', headers={'Content-Type': 'application/json'})
    urlopen(request, timeout=5).close()


def headless_env() -> dict[str, str]:
    """Without a display: a dead forwarded X display would stop Chromium's GPU paths (the deck behind is WebGL)."""
    return {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}


def first_paint(browser, url: str, viewport: dict, scale: float, shot: Path | None) -> dict:
    """Open the building straight from the address bar in a fresh context (nothing cached), time it until every floor
    is drawn, reload (cached) and time it again."""
    context = browser.new_context(viewport=viewport, device_scale_factor=scale, reduced_motion='reduce')
    page = context.new_page()
    errors, fetched = [], {}
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: m.type == 'error' and errors.append(m.text))
    page.on('response', lambda r: '/assets/world/building/' in r.url and fetched.__setitem__(r.url, len(r.body())))
    page.goto(url + '/?view=building')
    page.wait_for_function(BUILT, timeout=20000)
    cold = page.evaluate('performance.now()')
    cold_bytes = sum(fetched.values())
    page.reload()
    page.wait_for_function(BUILT, timeout=20000)
    warm = page.evaluate('performance.now()')
    page.wait_for_timeout(400)
    if shot:
        page.screenshot(path=str(shot))
    out = {'cold_ms': round(cold), 'warm_ms': round(warm), 'building_bytes': cold_bytes,
           'zoom': page.evaluate('fleetBuilding.zoom()'), 'pieces': len(page.evaluate('fleetBuilding.pieces()')), 'errors': errors}
    return out, page, context


def redraws(page) -> dict:
    """A state change: floor 1's project flips to the background and back, applied as a pushed document would be;
    timed from applying it until the new floor piece is on screen, and the script and layout time it costs."""
    cdp = page.context.new_cdp_session(page)
    cdp.send('Performance.enable')
    metric = lambda: {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
    before = metric()
    times = page.evaluate("""async () => {
      const doc = await (await fetch('/api/state')).json();
      const id = fleetBuilding.floors()[0].project, out = [];
      for (let i = 0; i < 12; i++) {
        const focus = i % 2 ? 'priority' : 'background';
        doc.building.focus[id] = focus;
        const want = focus === 'background' ? 'floor-glass' : 'floor-open';
        const t0 = performance.now();
        fleetDeck.apply(structuredClone(doc));
        await new Promise(done => { const check = () => fleetBuilding.floors()[0].built.piece.startsWith(want)
          ? done() : requestAnimationFrame(check); check(); });
        out.push(performance.now() - t0);
      }
      return out;
    }""")
    after = metric()
    n = len(times)
    return {'first_ms': round(times[0], 1), 'median_ms': round(statistics.median(times[1:]), 1), 'max_ms': round(max(times[1:]), 1),
            'script_ms_per_redraw': round((after['ScriptDuration'] - before['ScriptDuration']) * 1000 / n, 1),
            'layout_ms_per_redraw': round((after['LayoutDuration'] - before['LayoutDuration']) * 1000 / n, 1)}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / 'art' / 'build' / 'building-shots'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, serve_fixture(seeded(Path(tmp) / 'seeded.json')) as url:
        with urlopen(url + '/api/state', timeout=5) as response:
            state = json.load(response)
        for item in state['attention']:
            if item['project_id'] == INVOICES:
                post(url, '/api/attention/snooze', {'id': item['id'], 'seconds': 86400})
        with sync_playwright() as p:
            browser = p.chromium.launch(env=headless_env())
            desk, page, context = first_paint(browser, url, {'width': 1600, 'height': 950}, 1, out / 'building-1600x950.png')
            places = [lantern['place'] for lantern in page.evaluate('fleetBuilding.lanterns()')]
            crates = page.evaluate('fleetBuilding.cratesDrawn()')
            modes = [(f['floor'], f['built']['piece']) for f in page.evaluate('fleetBuilding.floors()')]
            desk['redraw'] = redraws(page)
            context.close()
            phone, page, context = first_paint(browser, url, {'width': 390, 'height': 844}, 3, out / 'building-390.png')
            context.close()
            hidpi, page, context = first_paint(browser, url, {'width': 1600, 'height': 950}, 2, None)
            context.close()
            browser.close()
    l0 = Image.open(CONCEPT).convert('RGB')
    shot = Image.open(out / 'building-1600x950.png').convert('RGB')
    shot = shot.resize((round(shot.width * l0.height / shot.height), l0.height), Image.LANCZOS)
    pair = Image.new('RGB', (l0.width + shot.width, l0.height), 'white')
    pair.paste(l0, (0, 0))
    pair.paste(shot, (l0.width, 0))
    pair.save(out / 'building-vs-l0.png', optimize=True)
    perf = {'seeded': {'floors': modes, 'lanterns': places, 'crates_drawn': crates},
            'desktop_1600x950_dpr1': desk, 'desktop_1600x950_dpr2': hidpi, 'phone_390x844_dpr3': phone}
    (out / 'perf.json').write_text(json.dumps(perf, indent=1))
    print(json.dumps(perf, indent=1))


if __name__ == '__main__':
    main()
