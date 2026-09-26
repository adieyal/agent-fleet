// The sprite world's renderer: a 2D canvas, no WebGL. Ground (floor and wall planes, flat sprites) comes from a
// cached snapshot (ground.js); standing sprites are drawn over it in footprint order; glow is added on top. Nothing
// is drawn unless something changed: the camera, a sprite's frame, a tier finishing loading, or an item set by the
// caller, and then only the changed rectangle when the camera is still. A still scene does no work at all.
// See docs/design/sprite-world.md.

import { Camera } from './camera.js';
import { GroundCache } from './ground.js';
import { pickAt } from './hit.js';
import { alphaOf, affineFill, canvas, glowDisc, hitMask, loadImage, tinted } from './paint.js';
import { PITCH, YAW, depth, fromScreen, plane, toScreen } from './projection.js';
import { sortEntries } from './sort.js';
import { drawableTier, fadeAlpha, pickTier } from './tiers.js';

const THROTTLE_MAX = 8;

export class World {
  // camera: { far, near, bounds } (see Camera); budgetMs: work per frame above which ambient animation slows down
  constructor(el, { camera, budgetMs = 8, reduced = matchMedia('(prefers-reduced-motion: reduce)').matches, dpr = devicePixelRatio || 1, background = '#dcebf8' }) {
    this.el = el; this.g = el.getContext('2d');
    this.dpr = dpr; this.background = background; this.reduced = reduced;
    this.camera = new Camera({ ...camera, reduced });
    this.sprites = new Map(); this.textures = new Map();
    this.items = new Map(); this.planes = []; this.glows = new Map();
    this.order = null;
    this.budget = budgetMs; this.throttle = 1; this.work = 0; this.throttleAt = 0;
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
      const tiers = s.tiers.map(t => ({ ...t, url: new URL(t.file, base).href, maskUrl: t.mask && new URL(t.mask, base).href, frames: t.frames || 1 }))
        .sort((a, b) => a.ppm - b.ppm);
      this.sprites.set(prefix + id, { ...s, id: prefix + id, tiers, img: [], alpha: [], tints: new Map(), loading: new Set(), loaded: new Set(), failed: new Set(),
        want: -1, shown: -1, prev: -1, since: 0, mask: null, used: false });
    }
    await Promise.all(Object.entries(m.textures || {}).map(async ([id, t]) => {
      this.textures.set(prefix + id, { ...t, img: await loadImage(new URL(t.file, base).href) });
    }));
    return m;
  }

  // a flat quad on the ground layer: quad is four world points; texture (id) tiles from `origin` along unit axes
  // `u` and `v`, or `color` fills it
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
    this.dirtyItem(it);
    Object.assign(it, patch);
    this.changed(id, 'at' in patch || 'sprite' in patch || 'attach' in patch || 'visible' in patch);
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
      if (s.shown >= 0 && fadeAlpha(s.since, now) < 1) { fading = true; this.dirtySprite(s); }
      else s.prev = -1;
    }
    return fading;
  }
  dirtySprite(s) {
    for (const it of this.items.values()) if (it.sprite === s.id) { if (it.layer === 'ground') this.dirty.ground = true; else this.dirtyItem(it); }
  }
  async fetchTier(s, i) {
    if (s.loading.has(i)) return;
    s.loading.add(i);
    const t = s.tiers[i];
    this.stats.requested.push(t.file);
    try {
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
  whenLoaded() {
    return new Promise(ok => {
      const check = () => {
        const need = this.camera.cur.ppm * this.dpr;
        const pending = [...this.sprites.values()].some(s => {
          const i = pickTier(s.tiers, need, s.want);
          return s.used && !s.loaded.has(i) && !s.failed.has(i);
        });
        if (!pending && this.stats.frames > 0 && !this.raf) ok(); else setTimeout(check, 30);
      };
      check();
    });
  }

  // --- animation ----------------------------------------------------------------------------------------------------

  frameOf(it, s, now) {
    const t = s.tiers[Math.max(0, s.shown)];
    if (t.frames > 1 && !t.fps) return Math.min(t.frames - 1, it.cell || 0);   // a sheet of states (the lift's doors)
    if (t.frames < 2 || !t.fps || this.reduced || it.still) return 0;
    const k = it.ambient ? this.throttle : 1, step = Math.floor((now - this.t0) * t.fps / k) * k;
    return step % t.frames;
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
    this.ground.base({ ...this.camera.min, W: view.W, H: view.H }, this.dpr);
    const zooming = this.camera.goal.ppm !== view.ppm || (this.lastView && this.lastView.ppm !== view.ppm);
    const groundChanged = !zooming && this.ground.update(view, this.dpr, { now: !this.ground.snap });
    let drew = false;
    if (viewChanged || this.dirty.all || groundChanged) {
      this.paint(view, now, null);
      this.stats.full++; drew = true;
    } else if (this.dirty.rects.length) {
      for (const r of merged(this.dirty.rects)) this.paint(view, now, r);   // robots far apart repaint apart
      this.stats.partial++; drew = true;
    }
    if (viewChanged && this.onView) this.onView(view);   // (a DOM overlay follows the view)
    this.lastView = view;
    this.dirty = { all: false, ground: false, rects: [] };
    // a canvas may rasterise after this returns, so the gap between back-to-back frames counts as well as the work
    const work = performance.now() - start, gap = this.continuous && this.lastFrameAt ? start - this.lastFrameAt - 1000 / 60 : 0;
    if (drew) {
      this.stats.frames++;
      this.pace(Math.max(work, gap), now);
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
    else if (this.work < this.budget / 3 && this.throttle > 1 && now - this.throttleAt > 2) { this.throttle /= 2; this.throttleAt = now; }
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
        g.fillStyle = p.color; g.fill();
      }
    }
    const flat = [...this.items.values()].filter(it => it.layer === 'ground' && it.visible).sort((a, b) => depth(a.at) - depth(b.at));
    for (const it of flat) this.drawItem(g, it, view, Infinity);   // (no crossfades in a snapshot)
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
    this.ground.draw(g, view);
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
    if (s.prev >= 0 && fade < 1) this.drawTier(g, it, s, s.prev, view, 1);
    this.drawTier(g, it, s, s.shown, view, fade);
  }
  drawTier(g, it, s, i, view, alpha) {
    if (alpha <= 0) return;
    const t = s.tiers[i], k = view.ppm / t.ppm, [x, y] = toScreen(view, it.at);
    let dx = x - t.anchor_px[0] * k, dy = y - t.anchor_px[1] * k;
    const w = t.fw * k, h = t.fh * k, frame = it.frame || 0;
    // at a steady zoom, blit a copy pre-scaled to it at whole device pixels; while zooming, scale on the fly
    const pre = this.steady ? this.prescaled(s, i, it.tint, frame, k) : null;
    if (pre) { dx = Math.round(dx * this.dpr) / this.dpr; dy = Math.round(dy * this.dpr) / this.dpr; }
    g.save();
    g.globalAlpha = alpha * (it.intensity ?? 1);
    if (it.cut && t.cut) {   // keep one side of the desk-top line through (cut.x, cut.y) in tier pixels
      const c = t.cut, line = sx => dy + k * (c.y + c.slope * ((sx - dx) / k - c.x));
      const edge = it.cut === 'over' ? dy - 1 : dy + h + 1;
      g.beginPath(); g.moveTo(dx, line(dx)); g.lineTo(dx + w, line(dx + w)); g.lineTo(dx + w, edge); g.lineTo(dx, edge); g.closePath();
      g.clip();
    }
    if (pre) g.drawImage(pre, dx, dy, pre.width / this.dpr, pre.height / this.dpr);
    else g.drawImage(this.bitmap(s, i, it.tint), frame * t.fw, 0, t.fw, t.fh, dx, dy, w, h);
    g.restore();
  }
  // one frame of a tier, tinted and scaled to the screen; cached until the zoom changes
  prescaled(s, i, tint, frame, k) {
    const key = `${s.id}|${i}|${tint || ''}|${frame}`;
    let c = this.scaled.get(key);
    if (!c) {
      const t = s.tiers[i], d = this.dpr;
      c = canvas(Math.max(1, Math.round(t.fw * k * d)), Math.max(1, Math.round(t.fh * k * d)));
      const g = c.getContext('2d');
      g.imageSmoothingQuality = 'high';
      g.drawImage(this.bitmap(s, i, tint), frame * t.fw, 0, t.fw, t.fh, 0, 0, c.width, c.height);
      this.scaled.set(key, c);
    }
    return c;
  }

  // --- picking ------------------------------------------------------------------------------------------------------

  // the item under a screen point (CSS px), as { id, place }, or the floor point under it as { floor: [x, y] }
  pick(x, y) {
    const view = this.camera.view;
    const entries = this.sorted().filter(it => it.hit !== false && this.sprites.get(it.sprite).hit !== 'none').map(it => {
      const s = this.sprites.get(it.sprite), r = this.screenRect(this.planeRect(it), view), t = s.tiers[Math.max(0, s.shown)];
      let keep = null;
      if (it.cut && t.cut && t.fw) {
        const k = r.w / t.fw, line = sx => r.y + k * (t.cut.y + t.cut.slope * ((sx - r.x) / k - t.cut.x));
        keep = it.cut === 'over' ? (px, py) => py < line(px) : (px, py) => py >= line(px);
      }
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
