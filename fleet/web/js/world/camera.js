// Pan and zoom between two framings: `far` (the whole floor, l1) is the widest zoom, `near` (the bench, l2) the
// closest. The camera eases towards a goal; panning is clamped so the view centre stays over the floor, with
// the allowed range shrinking to the far framing's centre as you zoom out.

import { boxRect, fit } from './projection.js';

const RATE = 14;   // damping: the gap to the goal closes by e^-RATE per second

export class Camera {
  // bounds: a world box the view centre may cover; far, near: framings (see fit)
  constructor({ far, near, bounds, reduced = false }) {
    this.far = far; this.near = near; this.reduced = reduced;
    this.bounds = boxRect(bounds);
    this.W = 1; this.H = 1;
    this.cur = { u: 0, v: 0, ppm: 1 };
    this.goal = { u: 0, v: 0, ppm: 1 };
  }

  resize(W, H) {
    const first = this.W === 1 && this.H === 1;
    this.W = W; this.H = H;
    this.min = fit(this.far, W, H); this.max = fit(this.near, W, H);
    if (first) this.jump(this.min);
    else { this.goal = this.clamp(this.goal); this.cur = this.clamp(this.cur); }
  }

  get view() { return { ...this.cur, W: this.W, H: this.H }; }
  get moving() {
    const g = this.goal, c = this.cur;
    return Math.abs(g.u - c.u) * c.ppm > 0.05 || Math.abs(g.v - c.v) * c.ppm > 0.05 || Math.abs(Math.log(g.ppm / c.ppm)) > 1e-4;
  }
  // where between the far (0) and near (1) framing the zoom is, on a log scale
  zoomLevel(ppm = this.cur.ppm) {
    return Math.log(ppm / this.min.ppm) / Math.log(this.max.ppm / this.min.ppm);
  }

  clamp(s) {
    const ppm = Math.min(this.max.ppm, Math.max(this.min.ppm, s.ppm));
    const k = Math.max(0, Math.min(1, this.zoomLevel(ppm)));
    const b = this.bounds, c = this.min;
    // at the far framing the centre is pinned; zoomed in it may reach any point of the bounds
    const lo = (a, e) => a + (e - a) * k;
    const u0 = lo(c.u, b.x), u1 = lo(c.u, b.x + b.w), v0 = lo(c.v, b.y), v1 = lo(c.v, b.y + b.h);
    return { u: Math.min(u1, Math.max(u0, s.u)), v: Math.min(v1, Math.max(v0, s.v)), ppm };
  }

  jump(s) { this.goal = this.clamp(s); this.cur = { ...this.goal }; }
  setGoal(s) { this.goal = this.clamp(s); if (this.reduced) this.cur = { ...this.goal }; }
  frame(f) { this.setGoal(fit(f, this.W, this.H)); }

  panBy(dx, dy) {   // screen pixels; the content follows the pointer
    const g = this.goal;
    this.setGoal({ u: g.u - dx / g.ppm, v: g.v - dy / g.ppm, ppm: g.ppm });
  }
  zoomAt(factor, sx, sy) {   // keeps the world point under (sx, sy) where it is
    const g = this.goal, ppm = Math.min(this.max.ppm, Math.max(this.min.ppm, g.ppm * factor));
    const ox = sx - this.W / 2, oy = sy - this.H / 2;
    const u = g.u + ox / g.ppm - ox / ppm, v = g.v + oy / g.ppm - oy / ppm;
    this.setGoal({ u, v, ppm });
  }

  // advance towards the goal; true while still moving
  step(dt) {
    if (!this.moving) { this.cur = { ...this.goal }; return false; }
    const k = this.reduced ? 1 : 1 - Math.exp(-RATE * dt);
    const c = this.cur, g = this.goal;
    this.cur = { u: c.u + (g.u - c.u) * k, v: c.v + (g.v - c.v) * k, ppm: c.ppm * Math.exp(Math.log(g.ppm / c.ppm) * k) };
    if (!this.moving) this.cur = { ...g };
    return true;
  }

  // pointer and wheel input on an element: drag pans, wheel and pinch zoom about the pointer. onTap(sx, sy) fires on
  // a press that barely moved. Returns a function that removes the listeners.
  attach(el, { onTap, onChange }) {
    const pts = new Map();
    let moved = 0, pinch = null;
    const local = e => { const r = el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const down = e => {
      el.setPointerCapture?.(e.pointerId);
      pts.set(e.pointerId, local(e));
      if (pts.size === 1) moved = 0;
      pinch = pts.size === 2 ? pinchOf() : null;
    };
    const pinchOf = () => {
      const [a, b] = [...pts.values()];
      return { d: Math.hypot(a[0] - b[0], a[1] - b[1]), m: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
    };
    const move = e => {
      if (!pts.has(e.pointerId)) return;
      const [x, y] = local(e), [px, py] = pts.get(e.pointerId);
      pts.set(e.pointerId, [x, y]);
      if (pts.size === 2 && pinch) {
        const now = pinchOf();
        this.panBy(now.m[0] - pinch.m[0], now.m[1] - pinch.m[1]);
        if (pinch.d > 0) this.zoomAt(now.d / pinch.d, now.m[0], now.m[1]);
        pinch = now; moved += 99;
      } else if (pts.size === 1) {
        moved += Math.hypot(x - px, y - py);
        this.panBy(x - px, y - py);
      }
      onChange();
    };
    const up = e => {
      if (!pts.has(e.pointerId)) return;
      const [x, y] = pts.get(e.pointerId);
      pts.delete(e.pointerId);
      if (pts.size === 0 && moved < 4 && onTap) onTap(x, y);
      pinch = pts.size === 2 ? pinchOf() : null;
    };
    const wheel = e => {
      e.preventDefault();
      const [x, y] = local(e);
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      this.zoomAt(Math.exp(-dy * 0.0015), x, y);
      onChange();
    };
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', wheel, { passive: false });
    el.style.touchAction = 'none';
    return () => {
      el.removeEventListener('pointerdown', down); el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up);
      el.removeEventListener('wheel', wheel);
    };
  }
}
