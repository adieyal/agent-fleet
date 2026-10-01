// Viewport, camera fit/zoom/focus, and pointer input: drag to pan, pinch or wheel to zoom, tap to select.

import * as THREE from 'three';
import { BOT_H, RD, RW, WALL_H, canvas, dpr, setDpr, setVh, setVw, vh, vw } from './env.js';
import { clamp, esc, seeded } from './util.js';
import { RIGHT, ROBOT, UP, _p, cam, camera, centreFor, renderer } from './scene.js';
import { ents, setFanned } from './model.js';
import { layoutNames, layoutRooms, plates, roomByName, rooms } from './rooms.js';
import { DOC_KIND, docKey, docMeshes, docMeta, docSlots, hoverDoc, kindOf, setHoverDoc } from './docs3d.js';
import { closePanel, select } from './panel.js';
import { closeLibrary, libraryPane, openProjectLibrary } from './library.js';
import { closeReader, openReader, reader } from './reader.js';
import { hoverScreen, pipelineByKey, screenMeshes } from './pipelines.js';
import { openSankey, sankeyPane } from './sankey.js';

// ------------------------------------------------------------------ camera: fit, zoom, focus
export function deckBounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of rooms) { x0 = Math.min(x0, r.ox); y0 = Math.min(y0, r.oy); x1 = Math.max(x1, r.ox + RW); y1 = Math.max(y1, r.oy + RD); }
  return { x0: x0 - 1.8, y0: y0 - 1.8, x1: x1 + 1.8, y1: y1 + 1.8 };
}
// extent of the deck along the screen axes, in tiles
function screenExtent() {
  let minR = Infinity, maxR = -Infinity, minU = Infinity, maxU = -Infinity;
  for (const b of plates) for (const x of [b.x0, b.x1]) for (const z of [b.y0, b.y1]) for (const y of [-0.55, WALL_H + 1.4]) {
    _p.set(x, y, z);
    const a = _p.dot(RIGHT), u = _p.dot(UP);
    minR = Math.min(minR, a); maxR = Math.max(maxR, a); minU = Math.min(minU, u); maxU = Math.max(maxU, u);
  }
  return { minR, maxR, minU, maxU };
}
export function fit(silent) {
  if (!rooms.length || !vw) return;
  const b = screenExtent();
  const mobile = vw < 760;
  const legendOpen = !document.getElementById('legend').classList.contains('closed');
  const left = (!mobile && vw > 1100 && legendOpen) ? 270 : 8;
  const top = mobile ? 100 : 60, bottom = mobile ? 70 : 24;
  const right = document.body.dataset.view === 'floor' ? 64 : 8;   // clear of the lift panel inside the building
  const aw = vw - left - right, ah = vh - top - bottom;
  const W = b.maxR - b.minR, H = b.maxU - b.minU;
  let z, cy;
  if (mobile) {
    // fit the column's width and let it scroll (drag) vertically
    z = clamp(aw / W, 6, 60);
    cy = H * z < ah ? top + ah / 2 : top + H * z / 2;
  } else {
    z = clamp(Math.min(aw / W, ah / H), 6, 60);
    cy = top + ah / 2;
  }
  const cx = left + aw / 2;
  if (!mobile) z = Math.max(6, Math.min(z, clearOfLog(b, cx, cy)));
  cam.z = z;
  // the world point at the middle of the extent goes to (cx, cy)
  const mid = new THREE.Vector3().addScaledVector(RIGHT, (b.minR + b.maxR) / 2).addScaledVector(UP, (b.minU + b.maxU) / 2);
  cam.c.copy(centreFor(mid, cx, cy, z));
  cam.tween = null;
  if (!silent) cam.userMoved = false;
}
// The deck log sits over the bottom-left of the deck: the largest zoom that keeps every room's focus switch (hung
// below its front corner, see focus.js) out from under it. Each switch clears the log by passing it on the right or above.
const SWITCH = { halfW: 72, drop: 14, h: 24 }, LOG_GAP = 8;
// the largest zoom z with a·z ≥ need (0 when zooming out can't satisfy it)
const zoomUpTo = (a, need) => need <= 0 ? (a >= 0 ? Infinity : need / a) : 0;
function clearOfLog(b, cx, cy) {
  const log = document.getElementById('feed').getBoundingClientRect();
  const midR = (b.minR + b.maxR) / 2, midU = (b.minU + b.maxU) / 2;
  let most = Infinity;
  for (const r of rooms) {
    _p.set(r.ox + RW, 0, r.oy + RD);
    const across = _p.dot(RIGHT) - midR, down = midU - _p.dot(UP);   // tiles from the deck's middle, in screen directions
    const passRight = zoomUpTo(across, log.right + LOG_GAP + SWITCH.halfW - cx);
    const passAbove = zoomUpTo(-down, cy + SWITCH.drop + SWITCH.h + LOG_GAP - log.top);
    const clear = Math.max(passRight, passAbove);
    if (clear > 0) most = Math.min(most, clear);   // a switch no zoom-out can clear is left where it is
  }
  return most;
}
// the log grows as entries arrive, and the cards open and close: fit again unless the user has moved the view
const refit = new ResizeObserver(() => { if (!cam.userMoved) fit(true); });
for (const id of ['legend', 'feed']) refit.observe(document.getElementById(id));
function zoomAt(sx, sy, f) {
  const nz = clamp(cam.z * f, 5, 140);
  const P = new THREE.Vector3().copy(cam.c).addScaledVector(RIGHT, (sx - vw / 2) / cam.z).addScaledVector(UP, -(sy - vh / 2) / cam.z);
  cam.c.copy(centreFor(P, sx, sy, nz));
  cam.z = nz;
  cam.userMoved = true; cam.tween = null;
}
function panBy(dx, dy) {
  cam.c.addScaledVector(RIGHT, -dx / cam.z).addScaledVector(UP, dy / cam.z);
  cam.userMoved = true; cam.tween = null;
}
export function focusOn(e) {
  if (!e || !e.bot) return;
  const mobile = vw < 760;
  // centre the android in whatever is left visible beside the side panel / above the bottom sheet
  const cx = mobile ? vw / 2 : (vw - 420) / 2, cy = mobile ? 52 + (vh * 0.38 - 52) * 0.5 + 40 : vh / 2;
  const want = mobile ? 30 : 44;
  if (cam.z < want) zoomAt(cx, cy, want / cam.z);
  const P = e.bot.root.position.clone(); P.y += BOT_H * 0.7;
  cam.tween = centreFor(P, cx, cy, cam.z);
  cam.userMoved = true;
}

const stars = document.getElementById('stars');
function drawStars() {
  stars.width = Math.round(vw * dpr); stars.height = Math.round(vh * dpr);
  const g = stars.getContext('2d'), rand = seeded(7);
  g.scale(dpr, dpr);
  for (let i = 0; i < 170; i++) {
    g.fillStyle = `rgba(200,220,255,${0.25 + rand() * 0.4})`;
    const s = rand() * 1.2 + 0.2;
    g.fillRect(rand() * vw, rand() * vh, s, s);
  }
}

// The deck's rendering resolution as a share of the screen's; the frame loop lowers it while frames run slow.
let renderScale = 1;
export function setRenderScale(scale) {
  if (scale === renderScale) return;
  renderScale = scale;
  renderer.setPixelRatio(dpr * renderScale);
  renderer.setSize(vw, vh, false);
}
export function resize() {
  setDpr(Math.min(window.devicePixelRatio || 1, 2));
  setVw(window.innerWidth); setVh(window.innerHeight);
  renderer.setPixelRatio(dpr * renderScale);
  renderer.setSize(vw, vh, false);
  drawStars();
  if (!ROBOT) return;
  layoutRooms(layoutNames);
  if (!cam.userMoved) fit(true);
}
window.addEventListener('resize', resize);

// ------------------------------------------------------------------ pointer: drag to pan, pinch/wheel to zoom, tap to select
const pointers = new Map();
let drag = null, pinch = null;
canvas.addEventListener('pointerdown', ev => {
  try { canvas.setPointerCapture(ev.pointerId); } catch (err) { /* synthetic pointer (test hook) */ }
  pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  if (pointers.size === 1) drag = { x: ev.clientX, y: ev.clientY, moved: false };
  else if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }; drag = null; }
});
canvas.addEventListener('pointermove', ev => {
  if (pointers.has(ev.pointerId)) pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  if (pinch && pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    panBy(mx - pinch.mx, my - pinch.my);
    if (pinch.d > 0) zoomAt(mx, my, d / pinch.d);
    pinch = { d, mx, my };
    return;
  }
  if (drag) {
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; canvas.classList.add('dragging'); }
    if (drag.moved) { panBy(dx, dy); drag.x = ev.clientX; drag.y = ev.clientY; hideDocTip(); }
    return;
  }
  const hit = pick(ev.clientX, ev.clientY);
  canvas.classList.toggle('hot', !!hit);
  hoverScreen(hit?.pipeline ? hit.key : null);
  if (hit && (hit.doc || hit.pipeline || hit.shelf) && ev.pointerType !== 'touch') showDocTip(hit, ev.clientX, ev.clientY); else hideDocTip();
});
function endPointer(ev) {
  pointers.delete(ev.pointerId);
  if (pointers.size < 2) pinch = null;
  if (drag && !drag.moved && ev.type === 'pointerup') {
    const hit = pick(ev.clientX, ev.clientY);
    if (hit && hit.shelf) { hideDocTip(); openProjectLibrary(hit.shelf.libraryKey, hit.shelf.label); }
    else if (hit && hit.pipeline) { hideDocTip(); openSankey(hit.pipeline); }
    else if (hit && hit.doc) openReader(hit.e, hit.doc);
    else if (hit && hit.e.crowd) setFanned(hit.e.crowd.key);
    else if (hit) select(hit.e.key);
    else closePanel();
  }
  if (pointers.size === 0) { drag = null; canvas.classList.remove('dragging'); }
}
canvas.addEventListener('pointerup', endPointer);
canvas.addEventListener('pointercancel', endPointer);
canvas.addEventListener('pointerleave', hideDocTip);

const docTip = document.getElementById('docTip');
function showDocTip(hit, px, py) {
  const { e, doc } = hit, key = hit.shelf ? 'shelf:' + hit.shelf.name : hit.pipeline ? 'pipeline:' + hit.key : docKey(e, doc);
  if (hit.shelf) {
    if (hoverDoc?.key !== key) {
      setHoverDoc({ key });
      docTip.style.setProperty('--hc', hit.shelf.look.accent);
      docTip.innerHTML = `<div class="th"><span class="kb">Library</span><b>${esc(hit.shelf.label)} library</b></div><div class="tc">click to open</div>`;
      docTip.hidden = false;
    }
  } else if (hit.pipeline && hoverDoc?.key !== key) {
    setHoverDoc({ key });
    const p = hit.pipeline;
    docTip.style.setProperty('--hc', '#38bdf8');
    docTip.innerHTML = `<div class="th"><span class="kb">Pipeline</span><b>${esc(p.pipeline)}</b></div>
      <div class="tm">${esc(p.run ? (p.run.label || p.run.run_id) : 'no runs yet')}</div><div class="tc">${esc(p.host)} · click to open</div>`;
    docTip.hidden = false;
  } else if (!hit.pipeline && (!hoverDoc || hoverDoc.key !== key)) {
    setHoverDoc({ key });
    const K = DOC_KIND[kindOf(doc)];
    docTip.style.setProperty('--hc', e.look.color);
    docTip.innerHTML = `<div class="th"><span class="kb">${K.label}</span><b>${esc(doc.name)}</b></div>
      <div class="tm">${esc(docMeta(doc))}</div><div class="tc">${esc(e.host)}:${esc(e.job.id)} · click to read</div>`;
    docTip.hidden = false;
  }
  const w = docTip.offsetWidth, h = docTip.offsetHeight;
  const x = px + 16 + w > vw - 8 ? px - 16 - w : px + 16, y = py + 20 + h < vh - 8 ? py + 20 : Math.max(60, py - h - 60);
  docTip.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
}
export function hideDocTip() { setHoverDoc(null); docTip.hidden = true; }
canvas.addEventListener('wheel', ev => { ev.preventDefault(); zoomAt(ev.clientX, ev.clientY, Math.exp(-ev.deltaY * 0.0015)); }, { passive: false });

const raycaster = new THREE.Raycaster();
const _ndc = new THREE.Vector2();
const proxies = [];
// the nearest android (an invisible capsule around each), document sheet or pipeline screen under the pointer
function pick(px, py) {
  proxies.length = 0;
  for (const e of ents.values()) if (roomByName.has(e.room) && e.bot.root.visible) proxies.push(e.proxy);
  for (const kind in docMeshes) proxies.push(docMeshes[kind]);
  proxies.push(...screenMeshes);
  for (const r of rooms) if (r.libraryKey) proxies.push(...r.shelves);
  _ndc.set(px / vw * 2 - 1, -(py / vh) * 2 + 1);
  raycaster.setFromCamera(_ndc, camera);
  const hit = raycaster.intersectObjects(proxies, false)[0];
  if (!hit) return null;
  if (hit.object.userData.shelf) return { shelf: hit.object.userData.shelf };
  if (hit.object.userData.pipeline) {
    const pipeline = pipelineByKey(hit.object.userData.pipeline);
    return pipeline ? { pipeline, key: hit.object.userData.pipeline } : null;
  }
  if (hit.object.isInstancedMesh) {
    const slot = docSlots[hit.object.userData.kind][hit.instanceId];
    return slot ? { e: slot.e, doc: slot.doc } : null;
  }
  return { e: hit.object.userData.ent };
}

document.getElementById('zoom').addEventListener('click', ev => {
  const z = ev.target.closest('button')?.dataset.z;
  if (z === 'in') zoomAt(vw / 2, vh / 2, 1.25);
  else if (z === 'out') zoomAt(vw / 2, vh / 2, 0.8);
  else if (z === 'fit') fit(false);
});
document.addEventListener('keydown', ev => {
  if (ev.target.closest && ev.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return;
  if (!reader.hidden) { if (ev.key === 'Escape') closeReader(); return; }
  if (!sankeyPane.hidden) return;   // the Sankey handles its own keys
  if (ev.key === 'Escape') { if (!libraryPane.hidden) closeLibrary(); else closePanel(); }
  else if (!['deck', 'floor'].includes(document.body.dataset.view) || !libraryPane.hidden
           || !document.getElementById('workarea').hidden) return;
  else if (ev.key === '+' || ev.key === '=') zoomAt(vw / 2, vh / 2, 1.2);
  else if (ev.key === '-' || ev.key === '_') zoomAt(vw / 2, vh / 2, 1 / 1.2);
  else if (ev.key === 'f' || ev.key === 'F') fit(false);
});
for (const b of document.querySelectorAll('[data-toggle]')) {
  b.addEventListener('click', () => {
    const card = document.getElementById(b.dataset.toggle);
    card.classList.toggle('closed');
    try { localStorage.setItem('fleet.deck.' + b.dataset.toggle, card.classList.contains('closed') ? 'closed' : 'open'); } catch (err) { /* storage unavailable */ }  });
}
for (const id of ['legend', 'feed']) {
  let saved = null;
  try { saved = localStorage.getItem('fleet.deck.' + id); } catch (err) { /* storage unavailable */ }
  if (saved === 'closed' || (saved === null && window.innerWidth < 760)) document.getElementById(id).classList.add('closed');
}
