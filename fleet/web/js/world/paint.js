// Bitmaps the renderer derives once and caches: host tint through a mask, glow discs, hit masks, and the affine
// fill that lays a tiled texture onto a floor or wall plane.

import { maskFromRGBA, maskSize } from './hit.js';

export function canvas(w, h) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(w, h);
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  return c;
}

export async function loadImage(url) {
  const img = new Image();
  img.src = url;
  try { await img.decode(); } catch { throw new Error('missing ' + url); }
  return img;
}

// a greyscale mask image as an alpha-only bitmap
export function alphaOf(maskImg) {
  const c = canvas(maskImg.width, maskImg.height), g = c.getContext('2d');
  g.drawImage(maskImg, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height);
  for (let i = 0; i < d.data.length; i += 4) { d.data[i + 3] = d.data[i]; d.data[i] = d.data[i + 1] = d.data[i + 2] = 0; }
  g.putImageData(d, 0, 0);
  return c;
}

// the host colour multiplied over the masked shell (lifted so a mid-grey shell lands on the colour), as the bake-off
export function tinted(img, alpha, hex) {
  const lift = [1, 3, 5].map(i => Math.min(255, parseInt(hex.slice(i, i + 2), 16) / 0.8));
  const shell = canvas(img.width, img.height), s = shell.getContext('2d');
  s.drawImage(img, 0, 0);
  s.globalCompositeOperation = 'multiply';
  s.fillStyle = `rgb(${lift.join(',')})`;
  s.fillRect(0, 0, img.width, img.height);
  s.globalCompositeOperation = 'destination-in';
  s.drawImage(alpha, 0, 0, img.width, img.height);
  const out = canvas(img.width, img.height), o = out.getContext('2d');
  o.drawImage(img, 0, 0);
  o.drawImage(shell, 0, 0);
  return out;
}

// the hit mask of frame 0 of a sheet
export function hitMask(img, fw, fh) {
  const [w, h] = maskSize(fw, fh);
  const c = canvas(w, h), g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0, fw, fh, 0, 0, w, h);
  return maskFromRGBA(g.getImageData(0, 0, w, h).data, w, h);
}

// a soft disc of light, cached per colour and size bucket; drawn additively and scaled to the exact size
const glows = new Map();
export function glowDisc(color, px) {
  const size = Math.min(512, Math.max(16, 2 ** Math.ceil(Math.log2(px))));
  const key = color + size;
  if (!glows.has(key)) {
    const c = canvas(size, size), g = c.getContext('2d'), r = size / 2;
    const grad = g.createRadialGradient(r, r, 0, r, r, r);
    const [R, G, B] = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16));
    grad.addColorStop(0, `rgba(${R},${G},${B},0.6)`);
    grad.addColorStop(0.5, `rgba(${R},${G},${B},0.22)`);
    grad.addColorStop(1, `rgba(${R},${G},${B},0)`);
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    glows.set(key, c);
  }
  return glows.get(key);
}

// fill the screen polygon `pts` with a tiled texture laid on a world plane: `o` is the screen position of a tile
// corner, `a` and `b` of the next corners along the plane's two axes (exact under an orthographic camera). Only the
// polygon's extent is filled: a pattern fill over a huge rectangle is slow on a software canvas.
export function affineFill(g, img, pts, o, a, b) {
  const m = [(a[0] - o[0]) / img.width, (a[1] - o[1]) / img.width, (b[0] - o[0]) / img.height, (b[1] - o[1]) / img.height];
  const det = m[0] * m[3] - m[2] * m[1];
  if (Math.abs(det) < 1e-12) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [sx, sy] of pts) {   // the polygon in texture pixels
    const dx = sx - o[0], dy = sy - o[1], px = (m[3] * dx - m[2] * dy) / det, py = (m[0] * dy - m[1] * dx) / det;
    x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py);
  }
  g.save();
  g.beginPath();
  pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
  g.closePath();
  g.clip();
  g.transform(m[0], m[1], m[2], m[3], o[0], o[1]);
  g.fillStyle = g.createPattern(img, 'repeat');
  g.fillRect(x0 - 1, y0 - 1, x1 - x0 + 2, y1 - y0 + 2);
  g.restore();
}
