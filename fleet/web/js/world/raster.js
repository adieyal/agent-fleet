// Drawing off the main thread. Code written against a 2D context draws into a Recorder instead; a Painter sends the
// recording to a worker (raster-worker.js), which replays it onto an OffscreenCanvas and sends back an ImageBitmap.
// The world's ground snapshots and its sprite copies scaled to a resting zoom are made this way: each is tens to
// hundreds of milliseconds of software rasterising, and done in a frame they stalled the page (zoom-out profile,
// 2026-09).
//
// An image loaded from a file (loadImage) goes to the worker as its URL, and the worker fetches and decodes it
// itself; any other image (a canvas: a tinted or composed sprite) goes as an ImageBitmap copied from it and
// transferred. The worker keeps the images the last few recordings used, up to HELD of them.

import { urlOf } from './paint.js';

const HELD = 400;   // images a worker keeps between recordings (a ground snapshot uses ~200)

export class Painter {
  constructor() {
    this.worker = new Worker(new URL('./raster-worker.js', import.meta.url), { type: 'module' });
    this.worker.onmessage = ({ data: { id, bitmap, error } }) => {
      const p = this.pending.get(id);
      this.pending.delete(id);
      if (error) p.fail(new Error(error)); else p.ok(bitmap);
    };
    this.pending = new Map(); this.ids = 0;
    this.held = new Map();   // image key -> true, least recently used first
    this.keys = new WeakMap(); this.keyed = 0;
  }
  // draw(g) into a w x h canvas (device pixels), as an ImageBitmap
  async paint(w, h, draw) {
    const rec = new Recorder(img => this.keyOf(img));
    draw(rec);
    const fresh = [...rec.used].filter(([key]) => typeof key === 'number' && !this.held.has(key));
    // (transferred, not cloned: a cloned bitmap's pixels are copied on the main thread)
    const sources = await Promise.all(fresh.map(async ([key, img]) => [key, await createImageBitmap(img)]));
    for (const key of rec.used.keys()) { this.held.delete(key); this.held.set(key, true); }
    const drop = [];
    for (const key of this.held.keys()) {
      if (this.held.size - drop.length <= HELD || rec.used.has(key)) break;
      drop.push(key);
    }
    for (const key of drop) this.held.delete(key);
    const id = ++this.ids;
    this.worker.postMessage({ id, w, h, calls: rec.calls, sources, drop }, sources.map(([, bitmap]) => bitmap));
    return new Promise((ok, fail) => this.pending.set(id, { ok, fail }));
  }
  keyOf(img) {
    const url = urlOf(img);
    if (url) return url;
    if (!this.keys.has(img)) this.keys.set(img, ++this.keyed);
    return this.keys.get(img);
  }
}

// Stands in for a 2D context and records the calls made on it: images become keys (see Painter), and patterns and
// gradients references to what the worker makes from them. Only what the world's drawing code uses.
const CALLS = ['save', 'restore', 'beginPath', 'moveTo', 'lineTo', 'closePath', 'rect', 'clip', 'fill', 'fillRect', 'transform', 'setTransform', 'drawImage'];
const PROPS = ['fillStyle', 'globalAlpha', 'globalCompositeOperation', 'imageSmoothingEnabled', 'imageSmoothingQuality'];

export class Recorder {
  constructor(keyOf) { this.keyOf = keyOf; this.calls = []; this.used = new Map(); this.refs = 0; this.props = {}; }
  arg(a) {
    if (!a || typeof a !== 'object') return a;
    if ('ref' in a) return { ref: a.ref };
    const key = this.keyOf(a);
    this.used.set(key, a);
    return { src: key };
  }
  createPattern(img, repeat) { return this.made('createPattern', this.arg(img), repeat); }
  createRadialGradient(...a) { return this.made('createRadialGradient', ...a); }
  createLinearGradient(...a) { return this.made('createLinearGradient', ...a); }
  made(name, ...a) {
    const ref = ++this.refs;
    this.calls.push([name, ref, ...a]);
    return { ref, addColorStop: (o, c) => this.calls.push(['addColorStop', ref, o, c]) };
  }
}
for (const name of CALLS) Recorder.prototype[name] = function (...a) { this.calls.push([name, ...a.map(x => this.arg(x))]); };
for (const p of PROPS) {
  Object.defineProperty(Recorder.prototype, p, {
    get() { return this.props[p]; },
    set(v) { this.props[p] = v; this.calls.push(['=', p, this.arg(v)]); },
  });
}
