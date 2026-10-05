// Colour, text and maths helpers, and guarded browser storage.

import { PI } from './env.js';

// ------------------------------------------------------------------ colour + misc utils
function hexToRgb(h) { h = h.replace('#', ''); if (h.length === 3) h = h.split('').map(c => c + c).join(''); const n = parseInt(h, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
function rgbToHex(r, g, b) { return '#' + [r, g, b].map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join(''); }
export function mix(a, b, t) { const A = hexToRgb(a), B = hexToRgb(b); return rgbToHex(A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t); }
export function rgba(hex, a) { const [r, g, b] = hexToRgb(hex); return `rgba(${r},${g},${b},${a})`; }
export function hsl(h, s, l) {
  s /= 100; l /= 100;
  const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return rgbToHex(f(0) * 255, f(8) * 255, f(4) * 255);
}
export function hash(s) { let h = 2166136261; for (const ch of String(s)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
export function seeded(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
export function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
export function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
export function clock(ts) { if (!ts) return ''; return new Date(ts * 1000).toTimeString().slice(0, 8); }
// A moment to the minute: "08:15" today, "29 Sep 08:15" before.
export function stamp(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), time = d.toTimeString().slice(0, 5);
  return d.toDateString() === new Date().toDateString() ? time
    : `${d.getDate()} ${d.toLocaleString('en', { month: 'short' })} ${time}`;
}
export function age(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return s + 's'; if (s < 3600) return Math.floor(s / 60) + 'm'; if (s < 86400) return Math.floor(s / 3600) + 'h'; return Math.floor(s / 86400) + 'd';
}
// A length of time to the minute: "40s", "12m", "1h 12m", "2d 3h".
export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  return d ? `${d}d ${h % 24}h` : h ? `${h}h ${m % 60}m` : `${m}m`;
}
export function trunc(s, n) { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
export function rr(c, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  c.beginPath(); c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
export function angleTo(from, to) { let d = (to - from) % (PI * 2); if (d > PI) d -= PI * 2; if (d < -PI) d += PI * 2; return d; }

export function store(area, key, value) {
  try { if (value === undefined) return window[area].getItem(key); window[area].setItem(key, value); } catch (err) { /* storage unavailable */ }
  return null;
}

export function offlineLabel(item, compact = false) {
  const since = item.stale_since ?? item.offline_since;
  if (since == null) return 'offline since unknown';
  const date = new Date(typeof since === 'number' ? since * 1000 : since);
  return compact ? `offline since ${age(date.getTime() / 1000)} ago` : `offline since ${date.toLocaleString()}`;
}

// Kept worker observations explain the stored classification; offline state is rendered separately.
export function storedStatus(run) {
  return run.status_label || (run.status === 'unknown outcome'
    ? ({ queued: 'queued (not started)', stalled: 'stalled (outcome unknown)' }[run.reason] || run.status) : run.status);
}
