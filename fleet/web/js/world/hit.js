// Hit testing: front to back over drawn entries, a rectangle test and then the sprite's own alpha, from a small
// 1-bit mask built once per sprite. Masks are in normalised sprite coordinates, so any tier's image can build one.

const SIDE = 128;   // longest side of a mask, in cells
const SOLID = 32;   // alpha at or above this counts as the sprite

// mask from RGBA pixels (as ImageData.data) of a w×h image already scaled to the mask's size
export function maskFromRGBA(data, w, h, threshold = SOLID) {
  const bits = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) bits[i] = data[i * 4 + 3] >= threshold ? 1 : 0;
  return { w, h, bits };
}
export function maskSize(fw, fh) {
  const k = Math.min(1, SIDE / Math.max(fw, fh));
  return [Math.max(1, Math.round(fw * k)), Math.max(1, Math.round(fh * k))];
}
// is the point (s, t), each 0..1 across the sprite, solid?
export function solidAt(mask, s, t) {
  if (s < 0 || t < 0 || s >= 1 || t >= 1) return false;
  return mask.bits[Math.floor(t * mask.h) * mask.w + Math.floor(s * mask.w)] === 1;
}

// entries in draw order: { item, rect: {x, y, w, h} in screen px, mask?, keep?: (x, y) => bool, hit: 'alpha'|'box' }.
// keep restricts an entry to part of its rectangle (a layer cut along the desk top). Returns the front-most item.
export function pickAt(entries, x, y) {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i], r = e.rect;
    if (!e.hit || x < r.x || y < r.y || x >= r.x + r.w || y >= r.y + r.h) continue;
    if (e.keep && !e.keep(x, y)) continue;
    if (e.hit === 'box') return e.item;
    if (e.mask && solidAt(e.mask, (x - r.x) / r.w, (y - r.y) / r.h)) return e.item;
  }
  return null;
}
