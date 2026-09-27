// The ground layer (floor and wall planes, flat sprites) as a cached snapshot. Tiled planes are drawn through a
// skewed pattern, which a software canvas fills at about 20 ns a pixel: far too slow to repaint every frame. So
// the ground is painted once into a bitmap a little larger than the screen, then blitted, shifted while panning and
// scaled while zooming. When the view settles somewhere the snapshot doesn't serve (another zoom, or panned past its
// margin) a new one is painted in bands over several frames, and swapped in when complete.

import { canvas } from './paint.js';

const MARGIN = 0.25;   // extra snapshot on each side, as a share of the screen
const BAND = 96;       // device pixel rows painted per step

export class GroundCache {
  // paint(g, view): paints the ground for a view into g, whose transform maps CSS pixels
  constructor(paint) {
    this.paint = paint;
    this.snap = null; this.next = null;
    this.version = 0;   // bumped by every change to the ground; a snapshot is current only at the latest version
    this.content = 0;   // bumped only when what is on the ground changes, not when a sprite swaps its tier
  }
  // content: false when only a ground sprite's tier changed: the overview (at the widest zoom) stays, and an older
  // snapshot still shows the right things meanwhile
  invalidate({ content = true } = {}) {
    this.version++;
    if (content) { this.content++; this.baseStale = true; }
  }
  get building() { return !!this.next; }

  // does the snapshot serve this view exactly (current, same zoom, view inside it)?
  serves(view) {
    const s = this.snap;
    return !!s && s.version === this.version && s.ppm === view.ppm && covers(s, view);
  }

  // start a snapshot for a view if needed; `now` paints it whole at once (the first frame, or after a change)
  update(view, dpr, { now = false } = {}) {
    if (this.serves(view)) { this.next = null; return false; }
    const target = this.next;
    if (!target || target.version !== this.version || target.ppm !== view.ppm || !covers(target, view)) {
      const W = Math.ceil(view.W * (1 + 2 * MARGIN)), H = Math.ceil(view.H * (1 + 2 * MARGIN));
      this.next = { u: view.u, v: view.v, ppm: view.ppm, W, H, dpr, c: canvas(Math.ceil(W * dpr), Math.ceil(H * dpr)), row: 0, version: this.version, content: this.content };
    }
    return this.step(now ? Infinity : 6);
  }
  // paint bands for up to `ms`; true when a snapshot was completed and swapped in
  step(ms) {
    const n = this.next;
    if (!n) return false;
    const start = performance.now(), g = n.c.getContext('2d'), rows = n.c.height;
    const view = { u: n.u, v: n.v, ppm: n.ppm, W: n.W, H: n.H };
    while (n.row < rows && (n.row === 0 || performance.now() - start < ms)) {
      const y = n.row / n.dpr, h = Math.min(BAND, rows - n.row) / n.dpr;
      g.save();
      g.setTransform(n.dpr, 0, 0, n.dpr, 0, 0);
      g.beginPath(); g.rect(0, y, n.W, h); g.clip();
      this.paint(g, view);
      g.restore();
      n.row += BAND;
      g.getImageData(0, 0, 1, 1);   // rasterise now, so the time taken is the time spent
    }
    if (n.row < rows) return false;
    this.snap = n; this.next = null;
    return true;
  }

  // the whole-floor snapshot, from the widest view, painted at once; it fills in wherever the current snapshot
  // doesn't reach while a new one is being painted (zooming out, panning past the margin)
  base(view, dpr) {
    if (this.overview && this.overview.ppm === view.ppm && !this.baseStale) return;
    const o = { u: view.u, v: view.v, ppm: view.ppm, W: view.W, H: view.H, dpr, c: canvas(Math.ceil(view.W * dpr), Math.ceil(view.H * dpr)), row: 0 };
    const next = this.next, snap = this.snap;
    this.next = o; this.step(Infinity);
    this.overview = o; this.snap = snap; this.next = next; this.baseStale = false;
  }

  // blit the snapshots for a view (clipped by the caller): the overview beneath, the current one over it
  draw(g, view, { partial = false } = {}) {
    // an out-of-date snapshot is left out while the overview (repainted at once on every change) is current; but a
    // partial repaint must match the rest of the screen, which was painted from that snapshot
    const old = !partial && this.snap && this.snap.content !== this.content && this.overview && !this.baseStale;
    // the overview only where the snapshot, as scaled now, doesn't reach (a full-screen blit is costly in software)
    if (this.overview && (!this.snap || old || !this.fills(this.snap, view))) this.blit(g, this.overview, view);
    if (this.snap && !old) this.blit(g, this.snap, view);
  }
  place(s, view) {
    const k = view.ppm / s.ppm, dpr = s.dpr;
    let x = view.W / 2 + (s.u - view.u) * view.ppm - s.W / 2 * k, y = view.H / 2 + (s.v - view.v) * view.ppm - s.H / 2 * k;
    if (k === 1) { x = Math.round(x * dpr) / dpr; y = Math.round(y * dpr) / dpr; }   // whole pixels: a plain copy
    return { x, y, w: s.c.width / dpr * k, h: s.c.height / dpr * k };
  }
  fills(s, view) {
    const r = this.place(s, view);
    return r.x <= 0 && r.y <= 0 && r.x + r.w >= view.W && r.y + r.h >= view.H;
  }
  // only the part that lands on the screen: at a high pixel ratio the whole snapshot is ~12 megapixels
  blit(g, s, view) {
    const r = this.place(s, view);
    const x0 = Math.max(0, r.x), y0 = Math.max(0, r.y), x1 = Math.min(view.W, r.x + r.w), y1 = Math.min(view.H, r.y + r.h);
    if (x1 <= x0 || y1 <= y0) return;
    const sx = s.c.width / r.w, sy = s.c.height / r.h;   // source pixels per CSS pixel
    g.drawImage(s.c, (x0 - r.x) * sx, (y0 - r.y) * sy, (x1 - x0) * sx, (y1 - y0) * sy, x0, y0, x1 - x0, y1 - y0);
  }
}

const covers = (s, view) => Math.abs(view.u - s.u) * view.ppm <= (s.W - view.W) / 2 && Math.abs(view.v - s.v) * view.ppm <= (s.H - view.H) / 2;
