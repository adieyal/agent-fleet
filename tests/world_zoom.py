"""Measuring the world view's zoom (fleet/web/js/world/): a probe installed on the running engine records, for every
drawn frame, what each sprite was drawn from and where its content landed, so a zoom can be judged for blur (a
bitmap drawn larger than its own pixels), for jumps (content moving against its world anchor from one draw to the
next, above all when a tier swaps) and for time to sharp. Shared by tests/test_world_zoom_browser.py and
scripts/world_zoom_bench.py."""

import math
from typing import Any

from playwright.sync_api import Page

# A draw's position is its alpha centroid and alpha quantile edges (0.5% and 99.5% of the mass along each axis):
# sub-pixel measures of where the content sits, unlike a thresholded box. Each is kept relative to the item's world
# anchor, in metres, so drawing the same content at another zoom or pan gives the same numbers.
PROBE = """(async () => {
  const w = fleetWorld.engine, { toScreen } = await import('/js/world/projection.js');
  if (w.zoomProbe) return;
  const log = w.zoomProbe = { frames: [], on: true };
  const P = CanvasRenderingContext2D.prototype, orig = P.drawImage;
  const mass = new WeakMap(), scratch = document.createElement('canvas');
  const sg = scratch.getContext('2d', { willReadFrequently: true });
  function massOf(img) {
    let m = mass.get(img);
    if (m !== undefined) return m;
    // (the probe's own time counts as loading, which the engine keeps out of its frame budget)
    const t0 = performance.now();
    try { m = measure(img); } finally { w.composing += performance.now() - t0; }
    mass.set(img, m);
    return m;
  }
  function measure(img) {
    let m;
    const W = img.width, H = img.height;
    scratch.width = W; scratch.height = H;
    orig.call(sg, img, 0, 0);
    const d = sg.getImageData(0, 0, W, H).data, col = new Float64Array(W), row = new Float64Array(H);
    let t = 0, sx = 0, sy = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const a = d[(y * W + x) * 4 + 3];
      if (!a) continue;
      col[x] += a; row[y] += a; t += a; sx += (x + 0.5) * a; sy += (y + 0.5) * a;
    }
    const q = (arr, p) => { let c = 0; for (let i = 0; i < arr.length; i++) { const n = c + arr[i]; if (n >= p * t) return i + (p * t - c) / arr[i]; c = n; } return arr.length; };
    m = t ? { cx: sx / t, cy: sy / t, x0: q(col, 0.005), x1: q(col, 0.995), y0: q(row, 0.005), y1: q(row, 0.995) } : null;
    return m;
  }
  let cur = null, rec = null, low = 1;
  // (only draws onto the frame's own context: not the tier copies and compositions made inside drawTier)
  const hook = orig => function (img, ...a) {
    if (cur && rec && log.on && this === cur.g) {
      let sw = img.width, sh = img.height, dx, dy, dw, dh;
      if (a.length === 4) [dx, dy, dw, dh] = a;
      else if (a.length === 8) [, , sw, sh, dx, dy, dw, dh] = a;
      else { [dx, dy] = a; dw = sw; dh = sh; }
      const tr = this.getTransform(), kx = dw / sw, ky = dh / sh, m = massOf(img);
      if (m) {
        const { it, tier, view } = cur, s = w.sprites.get(it.sprite);
        const [ax, ay] = toScreen(view, it.at);
        const rel = v => v.map((p, i) => (i % 2 ? dy + p * ky - ay : dx + p * kx - ax) / view.ppm);
        rec.draws.push({ id: it.id, sprite: it.sprite, tier, frame: it.frame || 0, shown: s.shown, prev: s.prev,
          up: kx * tr.a * low, pos: rel([m.cx, m.cy, m.x0, m.y0, m.x1, m.y1]) });
      }
    }
    return orig.call(this, img, ...a);
  };
  P.drawImage = hook(orig);
  // (motion drawn at pixel ratio 1 goes to an OffscreenCanvas)
  if (typeof OffscreenCanvasRenderingContext2D !== 'undefined') {
    const OP = OffscreenCanvasRenderingContext2D.prototype;
    OP.drawImage = hook(OP.drawImage);
  }
  const drawTier = w.drawTier;
  w.drawTier = function (g, it, s, i, view, alpha) {
    cur = { g, it, tier: i, view };
    try { return drawTier.call(this, g, it, s, i, view, alpha); } finally { cur = null; }
  };
  const paintLow = w.paintLow;
  w.paintLow = function (...a) { low = this.dpr; try { return paintLow.apply(this, a); } finally { low = 1; } };
  const blit = w.ground.blit;
  w.ground.blit = function (g, s, view) {
    const r = this.place(s, view);
    if (rec && log.on) rec.groundUp = Math.max(rec.groundUp, r.w / s.c.width * g.getTransform().a * low);
    return blit.call(this, g, s, view);
  };
  const frame = w.frame;
  w.frame = function () {
    rec = { t: performance.now(), draws: [], groundUp: 0 };
    frame.call(this);
    const v = this.camera.view;
    rec.work = performance.now() - rec.t; rec.ppm = v.ppm; rec.moving = this.camera.moving;
    rec.pending = [...this.sprites.values()].some(s => s.used && !s.loaded.has(s.want) && !s.failed.has(s.want));
    rec.fading = [...this.sprites.values()].some(s => s.used && s.fading);
    if (log.on) log.frames.push(rec);
    rec = null;
  };
})()"""

# Frame times only: no drawing hooks, so the probe costs nothing measurable.
TIMER = """(() => {
  const w = fleetWorld.engine;
  const log = w.zoomTimer = { frames: [] }, frame = w.frame;
  w.frame = function () {
    const t = performance.now();
    frame.call(this);
    const v = this.camera.view, sprites = [...this.sprites.values()].filter(s => s.used);
    // sharp: at rest, drawn from copies scaled to this zoom, over a ground snapshot made for it, from the tiers wanted
    const sharp = !this.camera.moving && this.steady && !this.dirty.all && this.ground.serves(v)
      && !sprites.some(s => s.fading || (!s.loaded.has(s.want) && !s.failed.has(s.want)) || s.shown !== s.want);
    log.frames.push({ t, end: performance.now(), work: performance.now() - t, moving: this.camera.moving, lowMotion: this.lowMotion, sharp });
  };
})()"""


def zoom(page: Page, direction: int, *, steps: int = 40, delta: float = 60, gap_ms: int = 25) -> None:
    """Wheel from the far floor towards the first bench (direction 1) or back out (-1), as a user would."""
    at = page.evaluate("""import('/js/world/projection.js').then(({ toScreen }) => {
      const w = fleetWorld.engine, b = fleetWorld.layout.benches.find(b => b.jobs.length) || fleetWorld.layout.benches[0];
      const [x, y] = toScreen(w.camera.view, b.at);
      return [Math.round(x), Math.round(y)]; })""")
    page.mouse.move(*at)
    for _ in range(steps):
        page.mouse.wheel(0, -delta * direction)
        page.wait_for_timeout(gap_ms)


def rest(page: Page, timeout: int = 20_000) -> None:
    """Until the camera has stopped, tiers are loaded and nothing is fading or being rebuilt."""
    page.wait_for_function("""(() => { const w = fleetWorld.engine;
      return !w.camera.moving && !w.raf && !w.ground.building
        && ![...w.sprites.values()].some(s => s.used && ((!s.loaded.has(s.want) && !s.failed.has(s.want)) || s.fading)); })()""",
                           timeout=timeout)
    page.wait_for_timeout(200)


def jumps(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """The largest movement of drawn content against its anchor, in screen px: within a frame between an item's old
    and new tier (a swap), and between consecutive draws of the same item, animation frame and tier choice."""
    swap = {"px": 0.0}
    step = {"px": 0.0}
    last: dict[tuple[str, int], tuple[list[float], int]] = {}
    for f in frames:
        seen: dict[tuple[str, int], dict[str, Any]] = {}
        for d in f["draws"]:
            key = (d["id"], d["frame"])
            other = seen.get(key)
            if other and other["tier"] != d["tier"]:
                px = max(abs(a - b) for a, b in zip(d["pos"], other["pos"])) * f["ppm"]
                if px > swap["px"]:
                    swap = {"px": px, "id": d["id"], "tiers": [other["tier"], d["tier"]], "ppm": f["ppm"]}
            seen[key] = d
        for key, d in seen.items():
            if key in last:
                pos, tier = last[key]
                px = max(abs(a - b) for a, b in zip(d["pos"], pos)) * f["ppm"]
                if px > step["px"]:
                    step = {"px": px, "id": d["id"], "tiers": [tier, d["tier"]], "ppm": f["ppm"]}
            last[key] = (d["pos"], d["tier"])
        # (an item not drawn this frame keeps its last draw)
    return {"swap": swap, "step": step}


def blur(frames: list[dict[str, Any]]) -> dict[str, float]:
    """The largest upscale of any sprite and of the ground, and the share of frames with any sprite upscaled."""
    ups = [max([d["up"] for d in f["draws"]] or [0]) for f in frames]
    return {"sprite_up": max(ups or [0]), "ground_up": max([f["groundUp"] for f in frames] or [0]),
            "blurry_frames": sum(u > 1.02 for u in ups) / max(1, len(ups))}


def time_to_sharp(frames: list[dict[str, Any]]) -> float | None:
    """Seconds from the camera stopping to the end of the first sharp frame (TIMER); None when none is."""
    stop = next((i for i, f in enumerate(frames) if not f["moving"] and all(not g["moving"] for g in frames[i:])), None)
    if stop is None:
        return None
    sharp = next((f for f in frames[stop:] if f["sharp"]), None)
    return sharp and (sharp["end"] - frames[stop]["t"]) / 1000


def percentile(values: list[float], p: float) -> float:
    if not values:
        return math.nan
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))]


def trace(frames: list[dict[str, Any]], item: str) -> list[dict[str, Any]]:
    """An item's draws frame by frame, for looking into a jump."""
    return [{"t": round(f["t"]), "ppm": round(f["ppm"], 2), "moving": f["moving"],
             "draws": [(d["tier"], round(d["up"], 3), [round(p * f["ppm"], 2) for p in d["pos"]]) for d in f["draws"] if d["id"] == item]}
            for f in frames]


# Draws each frame of a sprite from two neighbouring tiers through the engine's own drawTier, at the zoom where the
# coarser is drawn 1:1 (where the renderer swaps them), and measures both as PROBE does: the largest difference of
# centroid or quantile edges, in screen px. arg: { ids: [sprite ids], frames: max frames per sprite (0: all) }.
AGREEMENT = """async ({ ids, frames: most }) => {
  const w = fleetWorld.engine, { plane } = await import('/js/world/projection.js');
  const c = document.createElement('canvas'), g = c.getContext('2d', { willReadFrequently: true });
  const measure = () => {
    const d = g.getImageData(0, 0, c.width, c.height).data, W = c.width, H = c.height;
    const col = new Float64Array(W), row = new Float64Array(H);
    let t = 0, sx = 0, sy = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const a = d[(y * W + x) * 4 + 3];
      if (!a) continue;
      col[x] += a; row[y] += a; t += a; sx += (x + 0.5) * a; sy += (y + 0.5) * a;
    }
    const q = (arr, p) => { let s = 0; for (let i = 0; i < arr.length; i++) { const n = s + arr[i]; if (n >= p * t) return i + (p * t - s) / arr[i]; s = n; } return arr.length; };
    return t ? [sx / t, sy / t, q(col, 0.005), q(row, 0.005), q(col, 0.995), q(row, 0.995)] : null;
  };
  // (every tier of every sprite at once: past the engine's budget for decoded tiers, which would release them again)
  const keepBudget = w.tierBytes;
  w.tierBytes = Infinity;
  for (const id of ids) {
    const s = w.sprites.get(id);
    for (let j = 0; j < s.tiers.length; j++) {
      w.fetchTier(s, j);
      while (!s.loaded.has(j)) {
        if (s.failed.has(j)) throw new Error(id + ' tier ' + j + ' failed');
        await new Promise(ok => setTimeout(ok, 20));
      }
    }
  }
  // (from here on synchronous: no engine frame runs while its state is borrowed)
  const out = [];
  const keep = { steady: w.steady, scaledPpm: w.scaledPpm, dpr: w.dpr };
  // (drawn from the tiers themselves, unsnapped, smoothed as the copies drawn at rest are)
  const keepSmoothing = w.smoothing;
  w.steady = false; w.scaledPpm = Infinity; w.dpr = 1; w.smoothing = 'high';
  try {
    for (const id of ids) {
      const s = w.sprites.get(id);
      for (let i = 0; i + 1 < s.tiers.length; i++) {
        const t = s.tiers[i], ppm = t.ppm, n = most ? Math.min(most, t.frames) : t.frames;
        c.width = Math.ceil(t.size[0] + 8); c.height = Math.ceil(t.size[1] + 8);
        const at = [0, 0, 0], [u, v] = plane(at);
        const view = { u: u - (c.width / 2 - 4 - t.anchor_px[0]) / ppm, v: v - (c.height / 2 - 4 - t.anchor_px[1]) / ppm, ppm, W: c.width, H: c.height };
        let worst = { px: 0 }, centre = 0;   // (the largest difference of all six measures; of the centroid alone)
        for (let f = 0; f < n; f++) {
          const m = [i, i + 1].map(j => {
            g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, c.width, c.height);
            w.drawTier(g, { id: 'check', sprite: id, at, frame: f, cell: f }, s, j, view, 1);
            return measure();
          });
          if (!m[0] || !m[1]) continue;
          const px = Math.max(...m[0].map((a, k) => Math.abs(a - m[1][k])));
          centre = Math.max(centre, Math.abs(m[0][0] - m[1][0]), Math.abs(m[0][1] - m[1][1]));
          if (px > worst.px) worst = { px, frame: f, which: ['cx', 'cy', 'x0', 'y0', 'x1', 'y1'][m[0].findIndex((a, k) => Math.abs(a - m[1][k]) === px)] };
        }
        out.push({ id, tiers: [i, i + 1], ppm, ...worst, centroid: centre });
      }
    }
  } finally { Object.assign(w, keep); w.smoothing = keepSmoothing; w.tierBytes = keepBudget; }
  return out;
}"""


# Long tasks and long animation frames (PerformanceObserver), and every animation frame's interval from a rAF loop of
# its own, recorded from install until read back with LONG_TASKS_READ.
LONG_TASKS = """(() => {
  const log = window.zoomLongTasks = { tasks: [], frames: [], on: true };
  for (const type of ['longtask', 'long-animation-frame']) {
    try {
      new PerformanceObserver(list => { for (const e of list.getEntries()) if (log.on) log.tasks.push({ type, start: e.startTime, ms: e.duration }); })
        .observe({ type });
    } catch { /* (an entry type this browser doesn't have) */ }
  }
  let last = null;
  const tick = t => { if (last !== null && log.on) log.frames.push(t - last); last = t; if (log.on) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
})()"""
LONG_TASKS_READ = "(() => { const log = window.zoomLongTasks; log.on = false; return { tasks: log.tasks, frames: log.frames }; })()"


def zoom_bursts(page: Page, direction: int, *, bursts: int = 5, ticks: int = 8, delta: float = 60, pause_ms: int = 400) -> None:
    """Wheel out (-1) or in (1) in bursts with pauses between, as a hand on a wheel does: each pause lets the zoom
    settle and the ground be rebuilt for it, while the next burst starts."""
    page.mouse.move(720, 450)
    for _ in range(bursts):
        for _ in range(ticks):
            page.mouse.wheel(0, -delta * direction)
            page.wait_for_timeout(16)
        page.wait_for_timeout(pause_ms)


def long_task_summary(record: dict[str, Any]) -> dict[str, Any]:
    """The longest task (either entry type), how many ran over 50 ms, and the p50/p95/max animation frame interval."""
    tasks = [t["ms"] for t in record["tasks"] if t["type"] == "longtask"]
    frames = [t["ms"] for t in record["tasks"] if t["type"] == "long-animation-frame"]
    iv = record["frames"]
    return {"longest_task_ms": round(max(tasks or [0]), 1), "tasks_over_50ms": len(tasks),
            "longest_loaf_ms": round(max(frames or [0]), 1), "loafs_over_50ms": len(frames),
            "frame_p50_ms": round(percentile(iv, 0.5), 1), "frame_p95_ms": round(percentile(iv, 0.95), 1),
            "frame_max_ms": round(max(iv or [0]), 1), "frames": len(iv)}
