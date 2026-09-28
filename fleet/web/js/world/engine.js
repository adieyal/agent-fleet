// The sprite world's renderer: a 2D canvas, no WebGL. Ground (floor and wall planes, flat sprites) comes from a
// cached snapshot (ground.js); standing sprites are drawn over it in footprint order; glow is added on top. Nothing
// is drawn unless something changed: the camera, a sprite's frame, a tier finishing loading, or an item set by the
// caller, and then only the changed rectangle when the camera is still. A still scene does no work at all.
// See docs/design/sprite-world.md.

import { Camera } from './camera.js';
import { GroundCache } from './ground.js';
import { pickAt } from './hit.js';
import { alphaOf, affineFill, canvas, glowDisc, hitMask, loadImage, tinted } from './paint.js';
import { EDGE_SLOPE, PITCH, YAW, depth, fromScreen, plane, toScreen } from './projection.js';
import { sortEntries } from './sort.js';
import { drawableTier, fadeAlpha, pickTier } from './tiers.js';

const THROTTLE_MAX = 8;

export class World {
  // camera: { far, near, bounds } (see Camera); budgetMs: work per frame above which ambient animation slows down;
  // motionDpr: 'auto' draws camera motion at pixel ratio 1 once it proves too slow at full ratio, 'full' never does;
  // grade: { color, alpha, mode } a tone laid over the ground (the floor's warm ambient)
  constructor(el, { camera, budgetMs = 8, reduced = matchMedia('(prefers-reduced-motion: reduce)').matches, dpr = devicePixelRatio || 1,
    background = '#dcebf8', motionDpr = 'auto', grade = null }) {
    this.el = el; this.g = el.getContext('2d');
    this.dpr = dpr; this.background = background; this.reduced = reduced;
    this.motionDpr = motionDpr; this.lowMotion = motionDpr === 'low'; this.slowMotion = 0; this.wasLow = false; this.grade = grade;
    this.camera = new Camera({ ...camera, reduced });
    this.sprites = new Map(); this.textures = new Map();
    this.items = new Map(); this.planes = []; this.glows = new Map();
    this.order = null;
    this.budget = budgetMs; this.throttle = 1; this.work = 0; this.throttleAt = 0; this.composing = 0;
    this.stats = { frames: 0, full: 0, partial: 0, fades: 0, requested: [], loaded: [], missing: [] };
    this.dirty = { all: true, ground: true, rects: [] };
    this.scaled = new Map(); this.scaledPpm = 0; this.steady = false; this.continuous = false; this.lastFrameAt = 0;
    this.lastView = null; this.lastTime = null; this.raf = 0; this.timer = 0; this.onTap = null;
    this.t0 = performance.now() / 1000;
    this.ground = new GroundCache((g, view) => this.paintGround(g, view));
    this.resize();
    this.detach = this.camera.attach(el, {
      onChange: () => this.request(),
      onTap: (x, y) => this.onTap && this.onTap(this.pick(x, y), x, y),
    });
  }

  resize() {
    const r = this.el.getBoundingClientRect(), W = Math.max(1, Math.round(r.width)), H = Math.max(1, Math.round(r.height));
    this.W = W; this.H = H;
    this.el.width = Math.round(W * this.dpr); this.el.height = Math.round(H * this.dpr);
    this.scaledPpm = 0;
    this.camera.resize(W, H);
    this.dirty.all = this.dirty.ground = true;
    this.request();
  }

  // --- content --------------------------------------------------------------------------------------------------

  // manifest: { camera: {pitch, yaw}, sprites: { id: { footprint, hit, tiers: [{ ppm, file, size, anchor_px, mask?,
  // frames?, fps?, cut? }] } }, textures: { id: { file, metres } } }. Files are relative to the manifest.
  // A prefix namespaces the manifest's sprite and texture ids, so two manifests can both have a 'bench'.
  async load(url, { prefix = '' } = {}) {
    const m = await fetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error('missing ' + url))));
    if (Math.abs(m.camera.pitch - PITCH) > 0.01 || Math.abs(m.camera.yaw - YAW) > 0.01) {
      throw new Error(`${url} was made for pitch ${m.camera.pitch}°, yaw ${m.camera.yaw}°; the world is ${PITCH}°, ${YAW}°`);
    }
    const base = new URL(url, location.href);
    for (const [id, s] of Object.entries(m.sprites || {})) {
      this.define(prefix + id, { ...s, tiers: s.tiers.map(t => ({ ...t, url: new URL(t.file, base).href, maskUrl: t.mask && new URL(t.mask, base).href })) });
    }
    await Promise.all(Object.entries(m.textures || {}).map(async ([id, t]) => {
      this.textures.set(prefix + id, { ...t, img: await loadImage(new URL(t.file, base).href) });
    }));
    return m;
  }

  // A sprite made at runtime, or from a manifest (load). Besides the manifest's fields, `compose` stands in for tier
  // files: compose.load(i) readies tier i, and compose.cell(i, frame) returns that frame's bitmap at the tier's size
  // (the robots, composed from their layers: robots.js). Such a sprite is coloured by its composer, not by items.
  define(id, s) {
    const tiers = s.tiers.map(t => ({ ...t, frames: t.frames || 1 })).sort((a, b) => a.ppm - b.ppm);
    const had = this.sprites.get(id);
    this.sprites.set(id, { ...s, id, tiers, img: [], alpha: [], tints: new Map(), cells: new Map(), loading: new Set(), loaded: new Set(), failed: new Set(),
      want: -1, shown: -1, prev: -1, since: 0, mask: null, used: !!(had && had.used) });
  }

  // a flat quad on the ground layer: quad is four world points; texture (id) tiles from `origin` along unit axes
  // `u` and `v`, or `color` fills it, or a `gradient` (radial: { at, radius, stops }) or `linear` ({ from, to, along,
  // stops }: world points, and the world direction its colour is constant along) does
  addPlane(p) { this.planes.push(p); this.dirty.ground = true; this.request(); }

  // item: { id, sprite, at: [x, y, z], layer?: 'standing' | 'ground' | 'light' (default: the sprite's layer, else
  // standing), tint?, ambient?, still?, cell? (the frame of a sheet without fps), intensity? (0..1, light items),
  // attach?: { to, order }, cut?: 'under' | 'over', hit?: false, place? }. A seated robot is two items cut along
  // the desk top, attached before and after its desk (see seat()).
  add(item) {
    const s = this.sprites.get(item.sprite);
    if (!s) throw new Error('no sprite ' + item.sprite);
    s.used = true;
    this.items.set(item.id, { layer: s.layer || 'standing', visible: true, ...item });   // the manifest's layer hint by default
    this.changed(item.id, true);
    return item.id;
  }
  seat(id, { on, ...rest }) {
    this.add({ ...rest, id: id + ':under', of: id, cut: 'under', attach: { to: on, order: -1 } });
    this.add({ ...rest, id: id + ':over', of: id, cut: 'over', attach: { to: on, order: 1 } });
  }
  set(id, patch) {
    const it = this.items.get(id);
    if (!it) throw new Error('no item ' + id);
    if ('sprite' in patch) {   // (a sprite first shown by a set loads like one first added)
      const s = this.sprites.get(patch.sprite);
      if (!s) throw new Error('no sprite ' + patch.sprite);
      s.used = true;
    }
    this.dirtyItem(it);
    Object.assign(it, patch);
    this.changed(id, 'at' in patch || 'sprite' in patch || 'attach' in patch || 'visible' in patch);
  }
  // load sprites that nothing shows yet but soon will (robots about to arrive), so they appear without a delay
  use(...ids) {
    for (const id of ids) {
      const s = this.sprites.get(id);
      if (!s) throw new Error('no sprite ' + id);
      s.used = true;
    }
    this.request();
  }
  remove(id) {
    const it = this.items.get(id);
    if (!it) return;
    this.dirtyItem(it);
    this.items.delete(id);
    this.order = null;
    if (it.layer === 'ground') this.dirty.ground = true;
    this.request();
  }
  // glow: { id, at, radius (m), color, intensity 0..1 }; drawn additively over everything
  glow(g) {
    const old = this.glows.get(g.id);
    if (old) this.dirty.rects.push(this.glowRect(old));
    const next = { intensity: 1, ...old, ...g };
    this.glows.set(g.id, next);
    this.dirty.rects.push(this.glowRect(next));
    this.request();
  }

  changed(id, moved) {
    const it = this.items.get(id);
    if (moved) this.order = null;
    if (it.layer === 'ground') this.dirty.ground = true;
    this.dirtyItem(it);
    this.request();
  }
  dirtyItem(it) { if (this.lastView) this.dirty.rects.push(this.screenRect(this.planeRect(it))); }

  // --- geometry ---------------------------------------------------------------------------------------------------

  // the item's sprite rectangle in plane metres, from its coarsest tier (every tier covers the same area)
  planeRect(it) {
    const t = this.sprites.get(it.sprite).tiers[0], [u, v] = plane(it.at);
    return { x: u - t.anchor_px[0] / t.ppm, y: v - t.anchor_px[1] / t.ppm, w: t.size[0] / t.ppm, h: t.size[1] / t.ppm };
  }
  footprint(it) {
    const f = this.sprites.get(it.sprite).footprint, a = it.at;
    return [a[0] + f[0], a[1] + f[1], a[2] + f[2], a[0] + f[3], a[1] + f[4], a[2] + f[5]];
  }
  screenRect(r, view = this.camera.view) {
    return { x: view.W / 2 + (r.x - view.u) * view.ppm, y: view.H / 2 + (r.y - view.v) * view.ppm, w: r.w * view.ppm, h: r.h * view.ppm };
  }
  glowRect(gl) {
    const r = gl.radius, [u, v] = plane(gl.at);
    return this.screenRect({ x: u - r, y: v - r, w: 2 * r, h: 2 * r });
  }
  sorted() {
    if (!this.order) {
      const standing = [...this.items.values()].filter(it => it.layer === 'standing' && it.visible);
      this.order = sortEntries(standing.map(it => ({ id: it.id, item: it, box: this.footprint(it), rect: this.planeRect(it), attach: it.attach })))
        .map(e => e.item);
    }
    return this.order;
  }
  lights() { return [...this.items.values()].filter(it => it.layer === 'light' && it.visible && (it.intensity ?? 1) > 0); }

  // --- level of detail ----------------------------------------------------------------------------------------------

  // choose each used sprite's tier for this zoom, start loading it, and advance crossfades; true while fading
  levels(need, now) {
    let fading = false;
    for (const s of this.sprites.values()) {
      if (!s.used) continue;
      s.want = pickTier(s.tiers, need, s.want);
      if (!s.loaded.has(s.want)) this.fetchTier(s, s.want);
      const show = drawableTier(s.tiers, s.want, s.loaded);
      if (show !== s.shown) {
        if (s.shown >= 0 && show >= 0) { s.prev = s.shown; s.since = now; this.stats.fades++; }
        else if (show >= 0) { s.prev = -1; s.since = this.stats.frames ? now : -Infinity; }   // first sight fades in, except the opening frame
        s.shown = show;
        this.dirtySprite(s);
      }
      if (s.shown >= 0 && fadeAlpha(s.since, now) < 1) { fading = true; s.fading = true; this.dirtySprite(s); }
      else {
        if (s.fading) this.dirtySprite(s);   // the fade's last frame: repaint it at full strength
        s.fading = false; s.prev = -1;
      }
    }
    return fading;
  }
  dirtySprite(s) {
    for (const it of this.items.values()) if (it.sprite === s.id) { if (it.layer === 'ground') this.dirty.groundTier = true; else this.dirtyItem(it); }
  }
  async fetchTier(s, i) {
    if (s.loading.has(i)) return;
    s.loading.add(i);
    const t = s.tiers[i];
    this.stats.requested.push(t.file || s.id);
    try {
      if (s.compose) {
        await s.compose.load(i);
        t.fw = t.size[0]; t.fh = t.size[1];
        if (!s.mask) s.mask = hitMask(s.compose.cell(i, 0), t.fw, t.fh);
        s.loaded.add(i);
        this.request();
        return;
      }
      const img = await loadImage(t.url);
      if (t.maskUrl) s.alpha[i] = alphaOf(await loadImage(t.maskUrl));
      s.img[i] = img;
      t.fw = img.width / t.frames; t.fh = img.height;
      if (!s.mask) s.mask = hitMask(img, t.fw, t.fh);
      s.loaded.add(i);
      this.stats.loaded.push(t.file);
    } catch (e) {
      s.failed.add(i);
      this.stats.missing.push(String(e.message || e));
    }
    this.request();
  }
  // the bitmap for tier i of an item: its sheet, tinted through the tier's mask in the item's host colour
  bitmap(s, i, tint) {
    if (!tint || !s.alpha[i]) return s.img[i];
    const key = i + tint;
    if (!s.tints.has(key)) s.tints.set(key, tinted(s.img[i], s.alpha[i], tint));
    return s.tints.get(key);
  }
  // one frame of that bitmap as its own bitmap: drawn scaled straight from a sheet, a frame picks up its neighbour's
  // edge pixels (a thin line beside a seated robot: floor review 2); copied out 1:1 first, it can't
  cellOf(s, i, tint, frame) {
    if (s.compose) {   // (composing a frame the first time is loading: its time is kept out of the budget)
      const t0 = performance.now(), c = s.compose.cell(i, frame);
      this.composing += performance.now() - t0;
      return c;
    }
    const t = s.tiers[i], sheet = this.bitmap(s, i, tint);
    if (t.frames === 1) return sheet;
    const key = `${i}|${tint || ''}|${frame}`;
    if (!s.cells.has(key)) {
      const c = canvas(t.fw, t.fh);
      c.getContext('2d').drawImage(sheet, frame * t.fw, 0, t.fw, t.fh, 0, 0, t.fw, t.fh);
      s.cells.set(key, c);
    }
    return s.cells.get(key);
  }
  whenLoaded() {
    return new Promise(ok => {
      const check = () => {
        const need = this.camera.cur.ppm * this.dpr;
        const pending = [...this.sprites.values()].some(s => {
          const i = pickTier(s.tiers, need, s.want);
          return s.used && !s.loaded.has(i) && !s.failed.has(i);
        });
        // (not waiting for the loop to go idle: a scene with walking robots never does)
        if (!pending && this.stats.frames > 0 && !this.ground.building) ok(); else setTimeout(check, 30);
      };
      check();
    });
  }

  // --- animation ----------------------------------------------------------------------------------------------------

  frameOf(it, s, now) {
    const t = s.tiers[Math.max(0, s.shown)];
    if (t.frames > 1 && !t.fps) return Math.min(t.frames - 1, it.cell || 0);   // a sheet of states (the lift's doors)
    if (t.frames < 2 || !t.fps || this.reduced || it.still) return 0;
    // throttled, an ambient loop plays slower: skipping frames instead would freeze a 4-frame loop at a throttle of 4
    // (every update landing on the same frame), and with no frames left to measure the throttle could never recover
    const k = it.ambient ? this.throttle : 1;
    return Math.floor((now - this.t0) * t.fps / k) % t.frames;
  }
  // seconds until some animated item shows its next frame; Infinity when nothing animates
  nextFrameIn(now) {
    let soonest = Infinity;
    if (this.reduced) return soonest;
    for (const it of this.items.values()) {
      const s = this.sprites.get(it.sprite), t = s.tiers[Math.max(0, s.shown)];
      if (!it.visible || it.still || t.frames < 2 || !t.fps) continue;
      const period = (it.ambient ? this.throttle : 1) / t.fps, x = (now - this.t0) / period;
      soonest = Math.min(soonest, (Math.floor(x) + 1 - x) * period);
    }
    return soonest;
  }

  // --- the frame loop -------------------------------------------------------------------------------------------------

  request() {
    if (this.raf) return;
    clearTimeout(this.timer); this.timer = 0;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.frame(); });
  }
  frame() {
    const start = performance.now(), now = start / 1000;
    this.composing = 0;
    const dt = this.lastTime === null ? 0 : Math.min(0.1, now - this.lastTime);
    this.lastTime = now;
    const moving = this.camera.step(dt);
    const view = this.camera.view;
    const fading = this.levels(view.ppm * this.dpr, now);
    const viewChanged = !this.lastView || ['u', 'v', 'ppm', 'W', 'H'].some(k => this.lastView[k] !== view[k]);
    this.steady = !!this.lastView && this.lastView.ppm === view.ppm && this.camera.goal.ppm === view.ppm;
    if (this.steady && this.scaledPpm !== view.ppm) { this.scaled.clear(); this.scaledPpm = view.ppm; }
    for (const it of this.items.values()) {   // animated sprites whose frame changed
      const s = this.sprites.get(it.sprite), f = s.shown >= 0 ? this.frameOf(it, s, now) : 0;
      if (f !== it.frame) { it.frame = f; if (!viewChanged) this.dirtyItem(it); }
    }
    // the ground snapshot follows the view once the zoom holds still; until then the old one is scaled
    if (this.dirty.ground) this.ground.invalidate();
    else if (this.dirty.groundTier) this.ground.invalidate({ content: false });
    this.ground.base({ ...this.camera.min, W: view.W, H: view.H }, this.dpr);
    const zooming = this.camera.goal.ppm !== view.ppm || (this.lastView && this.lastView.ppm !== view.ppm);
    const groundChanged = !zooming && this.ground.update(view, this.dpr, { now: !this.ground.snap });
    let drew = false, full = false;
    // while the camera moves on a machine that can't keep up at a high pixel ratio, frames are drawn at ratio 1 and
    // scaled up; the frame at rest is drawn sharp again
    const low = moving && this.dpr > 1 && this.lowMotion;
    if (viewChanged || this.dirty.all || groundChanged || (this.wasLow && !low)) {
      if (low) this.paintLow(view, now); else this.paint(view, now, null);
      this.stats.full++; drew = full = true;
    } else if (this.dirty.rects.length) {
      for (const r of merged(this.dirty.rects)) this.paint(view, now, r);   // robots far apart repaint apart
      this.stats.partial++; drew = true;
    }
    this.wasLow = low;
    if (viewChanged && this.onView) this.onView(view);   // (a DOM overlay follows the view)
    this.lastView = view;
    this.dirty = { all: false, ground: false, rects: [] };
    // a canvas may rasterise after this returns, so the gap between back-to-back frames counts as well as the work
    const work = performance.now() - start, gap = this.continuous && this.lastFrameAt ? start - this.lastFrameAt - 1000 / 60 : 0;
    const cost = Math.max(work, gap) - this.composing;
    if (drew) {
      this.stats.frames++;
      // ambient animation answers only to the cost of animation frames: loading, zooming and full repaints are
      // expensive for other reasons and would keep it throttled long after they end
      if (!moving && !full) this.pace(cost, now);
      // three slow frames while moving at a high pixel ratio: draw motion at ratio 1 from then on
      if (moving && full && !low && this.dpr > 1 && this.motionDpr === 'auto' && cost > this.budget * 1.5 && ++this.slowMotion >= 3) this.lowMotion = true;
    }
    this.lastWork = work; this.lastFrameAt = start;
    // when the zoom comes to rest, one more full frame from bitmaps pre-scaled to it: sharper, and what partial
    // repaints will match
    if (drew && !moving && !this.steady) this.dirty.all = true;
    // (a tier that finishes loading asks for a frame itself)
    this.continuous = moving || fading || this.dirty.all || this.ground.building;
    if (this.continuous) this.request();
    else {
      this.lastTime = null;   // resting: the next frame starts a fresh clock
      const wait = this.nextFrameIn(now);
      if (wait < Infinity) this.timer = setTimeout(() => this.request(), Math.max(0, wait * 1000 - 4));
    }
  }
  // ambient animation (idle loops) updates less often while frames run over budget, and recovers when they don't
  pace(ms, now) {
    this.work = this.work ? this.work * 0.8 + ms * 0.2 : ms;
    if (this.work > this.budget && this.throttle < THROTTLE_MAX && now - this.throttleAt > 0.5) { this.throttle *= 2; this.throttleAt = now; }
    else if (this.work < this.budget / 2 && this.throttle > 1 && now - this.throttleAt > 1) { this.throttle /= 2; this.throttleAt = now; }
  }
  // a full frame at pixel ratio 1 into a side canvas, scaled onto the screen
  paintLow(view, now) {
    if (!this.lo || this.lo.width !== view.W || this.lo.height !== view.H) this.lo = canvas(view.W, view.H);
    const g = this.g, dpr = this.dpr;
    this.g = this.lo.getContext('2d'); this.dpr = 1;
    try { this.paint(view, now, null); } finally { this.g = g; this.dpr = dpr; }
    g.save();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.drawImage(this.lo, 0, 0, this.el.width, this.el.height);
    g.restore();
  }

  // the ground for a view, into a context already scaled to CSS pixels (see GroundCache)
  paintGround(g, view) {
    g.fillStyle = this.background;
    g.fillRect(0, 0, view.W, view.H);
    for (const p of this.planes) {
      const pts = p.quad.map(q => toScreen(view, q));
      if (p.texture) {
        const t = this.textures.get(p.texture), o = p.origin || p.quad[0];
        const along = d => toScreen(view, o.map((c, i) => c + d[i] * t.metres));
        affineFill(g, t.img, pts, toScreen(view, o), along(p.u), along(p.v));
      } else {
        g.beginPath();
        pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
        g.closePath();
        if (p.gradient) {   // a radial gradient centred on a world point, radius in metres (light falling off)
          const [cx, cy] = toScreen(view, p.gradient.at), gr = g.createRadialGradient(cx, cy, 0, cx, cy, p.gradient.radius * view.ppm);
          for (const [o, c] of p.gradient.stops) gr.addColorStop(o, c);
          g.fillStyle = gr;
        } else if (p.linear) {   // a linear gradient between two world points (occlusion fading off a wall's foot)
          // canvas keeps a linear gradient constant across its screen direction; turn that to run along the world
          // direction `along` (the wall), so the shading stays parallel to the wall under the camera
          const [x0, y0] = toScreen(view, p.linear.from), [x1, y1] = toScreen(view, p.linear.to);
          const [ax, ay] = toScreen(view, p.linear.from.map((v, i) => v + p.linear.along[i])), al = Math.hypot(ax - x0, ay - y0);
          const ux = (ax - x0) / al, uy = (ay - y0) / al, t = (x1 - x0) * ux + (y1 - y0) * uy;
          const gr = g.createLinearGradient(x0, y0, x1 - t * ux, y1 - t * uy);
          for (const [o, c] of p.linear.stops) gr.addColorStop(o, c);
          g.fillStyle = gr;
        } else g.fillStyle = p.color;
        g.fill();
      }
    }
    // far to near; light on the ground (a wall washer's scallop, the lantern's halo) last, added over what it lights
    const lit = it => (this.sprites.get(it.sprite).blend === 'lighter' ? 1 : 0);
    const flat = [...this.items.values()].filter(it => it.layer === 'ground' && it.visible && (it.intensity ?? 1) > 0)
      .sort((a, b) => lit(a) - lit(b) || depth(a.at) - depth(b.at));
    for (const it of flat) {   // (no crossfades in a snapshot)
      g.save();
      if (lit(it)) g.globalCompositeOperation = 'lighter';
      this.drawItem(g, it, view, Infinity);
      g.restore();
    }
    // the grade (a warm ambient) tones the ground: the floor and walls that fill most of the screen. Laid on every
    // frame instead, a soft-light fill of the whole screen costs a software canvas more than the rest of the frame
    if (this.grade) {
      g.save();
      g.globalCompositeOperation = this.grade.mode || 'soft-light';
      g.globalAlpha = this.grade.alpha;
      g.fillStyle = this.grade.color;
      g.fillRect(0, 0, view.W, view.H);
      g.restore();
    }
  }

  // clip: a screen rectangle to repaint, or null for everything
  paint(view, now, clip) {
    const g = this.g;
    g.save();
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (clip) {
      const x = Math.max(0, Math.floor(clip.x) - 1), y = Math.max(0, Math.floor(clip.y) - 1);
      const w = Math.min(view.W, Math.ceil(clip.x + clip.w) + 1) - x, h = Math.min(view.H, Math.ceil(clip.y + clip.h) + 1) - y;
      if (w <= 0 || h <= 0) { g.restore(); return; }
      clip = { x, y, w, h };
      g.beginPath(); g.rect(x, y, w, h); g.clip();
    }
    g.fillStyle = this.background;
    g.fillRect(0, 0, view.W, view.H);
    this.ground.draw(g, view, { partial: !!clip });
    for (const it of this.sorted()) {
      if (clip && !meets(this.screenRect(this.planeRect(it), view), clip)) continue;
      this.drawItem(g, it, view, now);
    }
    g.globalCompositeOperation = 'lighter';
    for (const it of this.lights()) {   // glow sprites (layer 'light'), at their intensity
      if (clip && !meets(this.screenRect(this.planeRect(it), view), clip)) continue;
      this.drawItem(g, it, view, now);
    }
    for (const gl of this.glows.values()) {
      if (gl.intensity <= 0) continue;
      const r = this.glowRect(gl);
      if (clip && !meets(r, clip)) continue;
      g.globalAlpha = Math.min(1, gl.intensity);
      g.drawImage(glowDisc(gl.color, r.w * this.dpr), r.x, r.y, r.w, r.h);
    }
    g.restore();
  }

  drawItem(g, it, view, now) {
    const s = this.sprites.get(it.sprite);
    if (s.shown < 0) return;
    const fade = fadeAlpha(s.since, now);
    // the old tier stays whole beneath the new one, except for additive light, which would then count twice
    if (s.prev >= 0 && fade < 1) this.drawTier(g, it, s, s.prev, view, it.layer === 'light' ? 1 - fade : 1);
    this.drawTier(g, it, s, s.shown, view, fade);
  }
  drawTier(g, it, s, i, view, alpha) {
    if (alpha <= 0) return;
    const t = s.tiers[i], k = view.ppm / t.ppm, [x, y] = toScreen(view, it.at);
    let dx = x - t.anchor_px[0] * k, dy = y - t.anchor_px[1] * k;
    const w = t.fw * k, h = t.fh * k, frame = it.frame || 0;
    // at a steady zoom, blit a copy pre-scaled to it at whole device pixels; while zooming, scale on the fly
    // while zooming, the copy made for the last resting zoom, scaled: a few screen pixels rather than a large tier
    const pre = this.steady ? this.prescaled(s, i, it.tint, frame, k, true) : this.prescaled(s, i, it.tint, frame, k, false);
    const z = this.steady ? 1 : view.ppm / this.scaledPpm;
    const usePre = pre && z > 0.25 && z < 4;   // (softer mid-zoom; the frame at rest is sharp)
    if (usePre && this.steady) { dx = Math.round(dx * this.dpr) / this.dpr; dy = Math.round(dy * this.dpr) / this.dpr; }
    g.save();
    g.globalAlpha = alpha * (it.intensity ?? 1);
    const cut = it.cut && this.cutLines(it, t, view, dx, dy, k);
    if (cut) {   // keep one side of the desk-top line (the under part also stops at the desk's near edge)
      const x0 = dx - 1, x1 = dx + w + 1;
      const [top, bottom] = it.cut === 'over' ? [() => dy - 1, cut.split] : [cut.split, cut.floor || (() => dy + h + 1)];
      g.beginPath(); g.moveTo(x0, top(x0)); g.lineTo(x1, top(x1)); g.lineTo(x1, bottom(x1)); g.lineTo(x0, bottom(x0)); g.closePath();
      g.clip();
    }
    if (usePre) g.drawImage(pre, dx, dy, pre.width / this.dpr * z, pre.height / this.dpr * z);
    else g.drawImage(this.cellOf(s, i, it.tint, frame), dx, dy, w, h);
    g.restore();
  }
  // A seated item's cut lines on screen, as functions of x: `split`, the desk-top line between its under and over
  // parts, and `floor`, below which its under part is not drawn. An item gives them as world points on the desk
  // (cutAt, cutFloor), each a line along the desk's long edges; otherwise the sprite's own fixed line (tier px).
  cutLines(it, t, view, dx, dy, k) {
    if (it.cutAt) {
      const through = p => { const [x, y] = toScreen(view, p); return sx => y + EDGE_SLOPE * (sx - x); };
      return { split: through(it.cutAt), floor: it.cutFloor ? through(it.cutFloor) : null };
    }
    if (!t.cut) return null;
    const c = t.cut;
    return { split: sx => dy + k * (c.y + c.slope * ((sx - dx) / k - c.x)), floor: null };
  }
  // One frame of a sprite, tinted and scaled to the screen at the resting zoom (scaledPpm), from tier i; cached
  // until the zoom rests somewhere else. `build` makes it (or remakes it from a new tier); without, only a lookup.
  prescaled(s, i, tint, frame, k, build) {
    const key = `${s.id}|${tint || ''}|${frame}`;
    const had = this.scaled.get(key);
    if (!build) return had ? had.c : null;
    if (had && had.i === i) return had.c;
    const t = s.tiers[i], d = this.dpr;
    const c = canvas(Math.max(1, Math.round(t.fw * k * d)), Math.max(1, Math.round(t.fh * k * d)));
    const g = c.getContext('2d');
    g.imageSmoothingQuality = 'high';
    g.drawImage(this.cellOf(s, i, tint, frame), 0, 0, c.width, c.height);
    this.scaled.set(key, { c, i });
    return c;
  }

  // --- picking ------------------------------------------------------------------------------------------------------

  // the item under a screen point (CSS px), as { id, place }, or the floor point under it as { floor: [x, y] }
  pick(x, y) {
    const view = this.camera.view;
    const entries = this.sorted().filter(it => it.hit !== false && this.sprites.get(it.sprite).hit !== 'none').map(it => {
      const s = this.sprites.get(it.sprite), r = this.screenRect(this.planeRect(it), view), t = s.tiers[Math.max(0, s.shown)];
      let keep = null;
      const cut = it.cut && t.fw && this.cutLines(it, t, view, r.x, r.y, r.w / t.fw);
      if (cut) keep = it.cut === 'over' ? (px, py) => py < cut.split(px) : (px, py) => py >= cut.split(px) && (!cut.floor || py < cut.floor(px));
      return { item: it, rect: r, mask: s.mask, keep, hit: s.hit || 'alpha' };
    });
    const it = pickAt(entries, x, y);
    if (it) return { id: it.of || it.id, place: it.place ?? null };
    const [fx, fy] = fromScreen(view, x, y, 0);
    return { floor: [fx, fy] };
  }

  destroy() { cancelAnimationFrame(this.raf); clearTimeout(this.timer); this.detach(); }
}

const meets = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
// rectangles merged wherever they overlap (or nearly), so each patch of screen is repainted once; many small
// patches collapse into their union
export function merged(rs) {
  const out = rs.filter(r => r.w > 0 && r.h > 0).map(r => ({ ...r }));
  const near = (a, b) => a.x < b.x + b.w + 8 && b.x < a.x + a.w + 8 && a.y < b.y + b.h + 8 && b.y < a.y + a.h + 8;
  for (let joined = true; joined;) {
    joined = false;
    for (let i = 0; i < out.length && !joined; i++) for (let j = i + 1; j < out.length; j++) {
      if (!near(out[i], out[j])) continue;
      out[i] = union([out[i], out[j]]); out.splice(j, 1); joined = true; break;
    }
  }
  return out.length > 8 ? [union(out)] : out;
}
function union(rs) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of rs) { x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.x + r.w); y1 = Math.max(y1, r.y + r.h); }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
