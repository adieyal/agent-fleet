// Page flags from the query string, deck dimensions and the canvases everything draws into.

export const QS = new URLSearchParams(location.search);
export const DEMO = QS.has('demo');
export const DEBUG = QS.has('debug');
export const WARP = DEBUG ? Number(QS.get('warp')) || 1 : 1;   // ?debug&warp=4 speeds up motion, for screenshots on slow software GL
export const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches || (DEBUG && QS.has('reduced'));
export const RW = 12, RD = 10, GAP = 4;    // room size (tiles) and corridor width
export const SPEED = 2.3;                  // walking speed, tiles per second
export const POLL_MS = 2000;
export const FS = 2;                       // furniture scale: tiles per Kenney unit
export const BOT_H = 2.2;                  // android height in tiles (a Kenney desk chair is ~1.2): the focal object of a room
export const BK = BOT_H / 1.7;             // accessories below were sized on a 1.7-tile android
export const WALL_H = 1.7;                 // back wall height
export const DESK_TOP = 0.77;              // desk surface height once scaled
export const SMALL_Z = 16, TINY_Z = 9;     // pixels per tile below which tags shrink / hide
export const PI = Math.PI, HALF = Math.PI / 2;

export const canvas = document.getElementById('world');
export const tagsEl = document.getElementById('tags');
export let dpr = 1, vw = 0, vh = 0;

if (DEBUG) {
  const box = document.createElement('pre');
  box.style.cssText = 'position:fixed;left:280px;top:60px;z-index:99;max-width:60vw;max-height:40vh;overflow:auto;margin:0;padding:8px;font:11px/1.4 monospace;color:#fecaca;background:rgba(40,0,0,.85);pointer-events:none;white-space:pre-wrap';
  box.hidden = true;
  document.body.appendChild(box);
  const report = msg => { box.hidden = false; box.textContent += msg + '\n'; };
  addEventListener('error', ev => report('error: ' + (ev.message || ev.error)));
  addEventListener('unhandledrejection', ev => report('rejection: ' + (ev.reason && (ev.reason.stack || ev.reason.message) || ev.reason)));
  const err = console.error.bind(console), warn = console.warn.bind(console);
  console.error = (...a) => { report('console.error: ' + a.join(' ')); err(...a); };
  console.warn = (...a) => { report('console.warn: ' + a.join(' ')); warn(...a); };
}

export function setDpr(value) { dpr = value; }
export function setVw(value) { vw = value; }
export function setVh(value) { vh = value; }
