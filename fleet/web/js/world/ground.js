// The ground layer (floor and wall planes, flat sprites) as a cached snapshot. Tiled planes are drawn through a
// skewed pattern, which a software canvas fills at about 20 ns a pixel: far too slow to repaint every frame. So
// the ground is painted once into a bitmap a little larger than the screen, then blitted, shifted while panning and
// scaled while zooming. When the view settles somewhere the snapshot doesn't serve (another zoom, or panned past its
// margin) a new one is painted and crossfaded in.
//
// Painting a snapshot is most of a second on a software canvas, and done in a frame it stalled the page (zoom-out
// profile, 2026-09), so none is painted on the main thread: the painter draws into a recorder (about a millisecond)
// and a worker paints the recording (raster.js). One snapshot is in the worker at a time; a view that changes
// meanwhile is painted next, the ones in between skipped.

import { FADE } from './tiers.js';

const MARGIN = 0.25;   // extra snapshot on each side, as a share of the screen

export class GroundCache {
  // paint(g, view): paints the ground for a view into g, whose transform maps CSS pixels; painter: a Painter;
  // ready(): a snapshot came in
  constructor(paint, painter, { reduced = false, ready = () => {} } = {}) {
    this.paint = paint; this.painter = painter; this.reduced = reduced; this.ready = ready;
    this.snap = null; this.prev = null; this.since = -Infinity; this.overview = null;
    this.want = null; this.wantBase = null; this.busy = null; this.swapped = false;
    this.version = 0;   // bumped by every change to the ground; a snapshot is current only at the latest version
    this.content = 0;   // bumped only when what is on the ground changes, not when a sprite swaps its tier
    this.failed = -1;   // the version a snapshot failed at (an image missing): not tried again until the ground changes
    this.stats = { painted: 0, skipped: 0, errors: [] };
  }
  // content: false when only a ground sprite's tier changed: the overview (at the widest zoom) stays, and an older
  // snapshot still shows the right things meanwhile
  invalidate({ content = true } = {}) {
    this.version++;
    if (content) this.content++;
  }
  get building() { return !!(this.busy || this.want || this.wantBase); }

  // does the snapshot serve this view exactly (current, same zoom, view inside it)?
  serves(view) {
    const s = this.snap;
    return !!s && s.version === this.version && s.ppm === view.ppm && covers(s, view);
  }

  // ask for a snapshot for a view if needed; true when one came in since the last call
  update(view, dpr) {
    const swapped = this.swapped;
    this.swapped = false;
    if (this.serves(view)) { this.want = null; return swapped; }
    const w = this.want;
    if (!w || w.version !== this.version || w.ppm !== view.ppm || w.dpr !== dpr || !covers(w, view)) {
      const W = Math.ceil(view.W * (1 + 2 * MARGIN)), H = Math.ceil(view.H * (1 + 2 * MARGIN));
      this.want = { kind: 'snap', u: view.u, v: view.v, ppm: view.ppm, W, H, dpr, version: this.version, content: this.content };
      this.next();
    }
    return swapped;
  }
  // the whole-floor snapshot, from the widest view; it fills in wherever the current snapshot doesn't reach (zooming
  // out, panning past the margin, a snapshot out of date)
  base(view, dpr) {
    const same = s => s && s.ppm === view.ppm && s.W === view.W && s.H === view.H && s.dpr === dpr && s.content === this.content;
    if (same(this.overview)) { this.wantBase = null; return; }
    if (same(this.wantBase)) return;
    this.wantBase = { kind: 'base', u: view.u, v: view.v, ppm: view.ppm, W: view.W, H: view.H, dpr, version: this.version, content: this.content };
    this.next();
  }

  // send the worker the next recording, the overview first, unless it is busy
  async next() {
    const job = this.busy ? null : this.wantBase || this.want;
    if (!job || job.version === this.failed) return;
    this.busy = job;
    if (job.kind === 'base') this.wantBase = null; else this.want = null;
    let bitmap;
    try {
      bitmap = await this.painter.paint(Math.ceil(job.W * job.dpr), Math.ceil(job.H * job.dpr), g => {
        g.setTransform(job.dpr, 0, 0, job.dpr, 0, 0);
        this.paint(g, { u: job.u, v: job.v, ppm: job.ppm, W: job.W, H: job.H });
      });
    } catch (e) {
      this.busy = null; this.failed = job.version;
      this.stats.errors.push(String(e.message || e));
      return;
    }
    this.done(job, bitmap);
  }
  done(job, bitmap) {
    this.busy = null;
    const s = { ...job, c: bitmap };
    if (job.kind === 'base') {
      if (job.content === this.content) { close(this.overview); this.overview = s; this.swapped = true; } else { bitmap.close(); this.stats.skipped++; }
    } else if (job.version === this.version) {
      // (a snapshot at the same zoom is only shifted: nothing to fade from)
      const fade = this.snap && !this.reduced && this.snap.ppm !== s.ppm;
      close(this.prev);
      if (fade) { this.prev = this.snap; this.since = performance.now() / 1000; } else { close(this.snap); this.prev = null; }
      this.snap = s; this.swapped = true;
    } else { bitmap.close(); this.stats.skipped++; }
    this.stats.painted++;
    this.next();
    this.ready();
  }

  // blit the snapshots for a view (clipped by the caller): the overview beneath, the snapshot being replaced, and the
  // current one over them (fading in)
  draw(g, view, { partial = false, now = performance.now() / 1000 } = {}) {
    // an out-of-date snapshot is left out while the overview is current; but a partial repaint must match the rest
    // of the screen, which was painted from that snapshot
    const current = this.overview && this.overview.content === this.content;
    const old = !partial && this.snap && this.snap.content !== this.content && current;
    const top = this.snap && !old ? this.snap : null;
    const fade = this.prev && top ? Math.min(1, (now - this.since) / FADE) : 1;
    if (fade >= 1 && this.prev) { close(this.prev); this.prev = null; }
    const under = this.prev && this.prev.content === this.snap.content ? this.prev : null;
    // the overview only where the snapshots, as scaled now, don't reach (a full-screen blit is costly in software)
    if (this.overview && (!top || !this.fills(top, view) || (fade < 1 && !(under && this.fills(under, view))))) this.blit(g, this.overview, view);
    if (under && fade < 1) this.blit(g, under, view);
    if (top) {
      const a = g.globalAlpha;
      g.globalAlpha = a * fade;
      this.blit(g, top, view);
      g.globalAlpha = a;
    }
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

const close = s => s && s.c.close();   // (a snapshot's bitmap, once nothing draws it)
const covers = (s, view) => Math.abs(view.u - s.u) * view.ppm <= (s.W - view.W) / 2 && Math.abs(view.v - s.v) * view.ppm <= (s.H - view.H) / 2;
