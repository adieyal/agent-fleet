// Deck entry point: the frame loop and boot.

import * as THREE from 'three';
import {
  BK, BOT_H, DEBUG, DEMO, PI, POLL_MS, QS, RD, REDUCED, RW, WALL_H, WARP, canvas, dpr, setDpr, setVh, setVw, vh,
  vw,
} from './env.js';
import { age, clamp, clock, esc, hash, mix, seeded, store, trunc } from './util.js';
import { AGENT_COLOR, TOOL_ICON, hostLook } from './looks.js';
import { DOC_FILE, activityOf, isActive, isSession, shortId } from './activity.js';
import {
  M, RIGHT, ROBOT, UP, _p, _w, applyCamera, cam, camera, centreFor, loadAssets, renderer, scene, toScreen,
} from './scene.js';
import {
  ents, everLoaded, feed, feedSeeded, hosts, live, seenEvents, selectedKey, setFeedSeeded, setSelectedKey,
} from './model.js';
import { edgeStrips, layoutNames, layoutRooms, plates, roomByName, rooms } from './rooms.js';
import {
  DOC_KIND, _la, _lb, dashedLine, docKey, docMeshes, docMeta, docSlots, docsOf, fmtSize, hoverDoc, kindOf,
  liftHovered, lineGeo, nSeg, setHoverDoc, setNSeg, stepDocFx,
} from './docs3d.js';
import { action, buildRobot, positionTags } from './agents.js';
import { stepMotion, stepParticles, updateEnt, updateRoom } from './motion.js';
import {
  applyState, dismiss, hiddenCount, restoreDismissed, retiredCount, showFinished, stream, toggleFinished,
} from './state.js';

// ------------------------------------------------------------------ frame loop
let lastT = 0;
function frame(ts) {
  requestAnimationFrame(frame);
  if (document.hidden || !reader.hidden) { lastT = 0; return; }   // the reader covers the deck; don't render under it
  if (ts < panelScrollUntil) { lastT = 0; return; }             // hold the deck still while the panel scrolls, so the scroll gets the frame
  const t = ts / 1000, now = performance.now() / 1000;
  const dt = Math.min(0.1, lastT ? t - lastT : 0.016) * WARP;
  lastT = t;
  stepMotion(dt, now);
  if (cam.tween) {
    const k = REDUCED ? 1 : Math.min(1, dt * 7);
    cam.c.lerp(cam.tween, k);
    if (cam.c.distanceTo(cam.tween) < 0.01) cam.tween = null;
  }
  applyCamera();
  setNSeg(0);
  for (const r of rooms) { r.busyTerm = 0; r.testTerm = 0; r.busyWeb = false; r.busyPlan = false; r.busyCab = 0; r.busyRack = false; r.busyKitchen = false; r.busyPress = null; }
  for (const e of ents.values()) {
    const r = roomByName.get(e.room);
    if (!r) continue;
    updateEnt(e, r, dt, t, now);
    if (e.walking || !e.target || !isActive(e.job.status)) continue;
    const p = e.target.prop;
    if (p === 'terminal') { r.busyTerm |= 1 << e.target.propIdx; if (e.act === 'test') r.testTerm |= 1 << e.target.propIdx; }
    else if (p === 'cabinet') r.busyCab |= 1 << e.target.propIdx;
    else if (p === 'rack') r.busyRack = true;
    else if (p === 'kitchen') r.busyKitchen = true;
    else if (p === 'press') r.busyPress = e.look.color;
    else if (p === 'comms') r.busyWeb = true;
    else if (p === 'whiteboard') r.busyPlan = true;
    else if (p === 'partner' && e.partner && ents.has(e.partner.key)) {   // delegation: a marching arc to the helper
      e.bot.head.getWorldPosition(_la); e.partner.bot.head.getWorldPosition(_lb);
      _la.y += 0.35 * BK; _lb.y += 0.35 * BK;
      _w.copy(_la); _p.copy(_lb);
      dashedLine(_w, _p, e.look.color, t, 0.7);
    }
  }
  for (const r of rooms) updateRoom(r, t, dt, now);
  stepDocFx(t);
  liftHovered();
  lineGeo.setDrawRange(0, nSeg * 2);
  lineGeo.attributes.position.needsUpdate = true;
  lineGeo.attributes.color.needsUpdate = true;
  if (!REDUCED) for (const tex of edgeStrips) tex.offset.x = -t * 0.35;
  M.beacon.color.set(REDUCED || Math.sin(t * 2.4) > 0.6 ? 0xf87171 : 0x5a1d1d);
  M.failGlow.opacity = REDUCED ? 0.6 : 0.45 + Math.sin(t * 8) * 0.3;
  stepParticles(dt);
  renderer.render(scene, camera);
  positionTags();
  if (fpsEl) {
    fpsN++;
    if (t - fpsT > 1) { fpsEl.textContent = `${Math.round(fpsN / (t - fpsT))} fps · ${renderer.info.render.calls} calls`; fpsN = 0; fpsT = t; }
  }
}
const fpsEl = DEBUG ? document.body.appendChild(Object.assign(document.createElement('div'), { style: 'position:fixed;right:70px;bottom:14px;z-index:99;font:12px monospace;color:#9fe' })) : null;
let fpsN = 0, fpsT = 0;

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
  const top = mobile ? 100 : 60, bottom = mobile ? 70 : 24, right = 8;
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
  cam.z = z;
  // the world point at the middle of the extent goes to (cx, cy)
  const mid = new THREE.Vector3().addScaledVector(RIGHT, (b.minR + b.maxR) / 2).addScaledVector(UP, (b.minU + b.maxU) / 2);
  cam.c.copy(centreFor(mid, cx, cy, z));
  cam.tween = null;
  if (!silent) cam.userMoved = false;
}
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
function focusOn(e) {
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

function resize() {
  setDpr(Math.min(window.devicePixelRatio || 1, 2));
  setVw(window.innerWidth); setVh(window.innerHeight);
  renderer.setPixelRatio(dpr);
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
  if (hit && hit.doc && ev.pointerType !== 'touch') showDocTip(hit, ev.clientX, ev.clientY); else hideDocTip();
});
function endPointer(ev) {
  pointers.delete(ev.pointerId);
  if (pointers.size < 2) pinch = null;
  if (drag && !drag.moved && ev.type === 'pointerup') {
    const hit = pick(ev.clientX, ev.clientY);
    if (hit && hit.doc) openReader(hit.e, hit.doc);
    else if (hit) select(hit.e.key);
    else if (selectedKey) closePanel();
  }
  if (pointers.size === 0) { drag = null; canvas.classList.remove('dragging'); }
}
canvas.addEventListener('pointerup', endPointer);
canvas.addEventListener('pointercancel', endPointer);
canvas.addEventListener('pointerleave', hideDocTip);

const docTip = document.getElementById('docTip');
function showDocTip(hit, px, py) {
  const { e, doc } = hit, key = docKey(e, doc);
  if (!hoverDoc || hoverDoc.key !== key) {
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
function hideDocTip() { setHoverDoc(null); docTip.hidden = true; }
canvas.addEventListener('wheel', ev => { ev.preventDefault(); zoomAt(ev.clientX, ev.clientY, Math.exp(-ev.deltaY * 0.0015)); }, { passive: false });

const raycaster = new THREE.Raycaster();
const _ndc = new THREE.Vector2();
const proxies = [];
// the nearest android (an invisible capsule around each) or document sheet under the pointer
function pick(px, py) {
  proxies.length = 0;
  for (const e of ents.values()) if (roomByName.has(e.room)) proxies.push(e.proxy);
  for (const kind in docMeshes) proxies.push(docMeshes[kind]);
  _ndc.set(px / vw * 2 - 1, -(py / vh) * 2 + 1);
  raycaster.setFromCamera(_ndc, camera);
  const hit = raycaster.intersectObjects(proxies, false)[0];
  if (!hit) return null;
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
  if (ev.target.closest && ev.target.closest('input,textarea')) return;
  if (!reader.hidden) { if (ev.key === 'Escape') closeReader(); return; }
  if (ev.key === 'Escape') { if (!libraryPane.hidden) closeLibrary(); else closePanel(); }
  else if (ev.key === '+' || ev.key === '=') zoomAt(vw / 2, vh / 2, 1.2);
  else if (ev.key === '-' || ev.key === '_') zoomAt(vw / 2, vh / 2, 1 / 1.2);
  else if (ev.key === 'f' || ev.key === 'F') fit(false);
});
for (const b of document.querySelectorAll('[data-toggle]')) {
  b.addEventListener('click', () => {
    const card = document.getElementById(b.dataset.toggle);
    card.classList.toggle('closed');
    try { localStorage.setItem('fleet.deck.' + b.dataset.toggle, card.classList.contains('closed') ? 'closed' : 'open'); } catch (err) { /* storage unavailable */ }
  });
}
for (const id of ['legend', 'feed']) {
  let saved = null;
  try { saved = localStorage.getItem('fleet.deck.' + id); } catch (err) { /* storage unavailable */ }
  if (saved === 'closed' || (saved === null && window.innerWidth < 760)) document.getElementById(id).classList.add('closed');
}

// ------------------------------------------------------------------ portraits for the manifest and the panel
// Rendered once per look into an offscreen target with the main renderer, then copied into small 2D canvases.
const portraits = new Map();
let portraitStage = null;
function renderPortrait(look, agent, pose, W, H) {
  if (!portraitStage) {
    const s = new THREE.Scene();
    s.add(new THREE.HemisphereLight(0xc8d8ff, 0x302838, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(2, 3, 4); s.add(key);
    const c = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 50);
    portraitStage = { scene: s, camera: c };
  }
  const { scene: s, camera: c } = portraitStage;
  const bot = buildRobot(look, agent);
  action(bot, 'Idle').play();
  bot.mixer.update(0.5);
  if (pose === 'off') { bot.main.color.set(mix(look.color, '#475163', 0.55)); bot.face.color.set('#3a4252'); if (bot.eyes) { bot.eyes.color.set('#3a4252'); bot.eyes.emissiveIntensity = 0; } }
  bot.root.rotation.y = 0.45 + (DEBUG && QS.get('bots') === 'back' ? PI : 0);
  s.add(bot.root);
  const hgt = BOT_H + 0.4, wid = hgt * W / H;
  c.left = -wid / 2; c.right = wid / 2; c.top = hgt / 2; c.bottom = -hgt / 2;
  c.position.set(0, hgt / 2 + 0.6, 6); c.lookAt(0, hgt / 2 - 0.05, 0); c.updateProjectionMatrix();
  const rt = new THREE.WebGLRenderTarget(W * 2, H * 2);
  rt.texture.colorSpace = THREE.SRGBColorSpace;
  renderer.setRenderTarget(rt);
  renderer.clear();
  renderer.render(s, c);
  const px = new Uint8Array(W * 2 * H * 2 * 4);
  renderer.readRenderTargetPixels(rt, 0, 0, W * 2, H * 2, px);
  renderer.setRenderTarget(null);
  s.remove(bot.root);
  rt.dispose(); bot.main.dispose(); bot.face.dispose(); bot.hostMat.dispose(); bot.eyes?.dispose();
  const out = document.createElement('canvas'); out.width = W * 2; out.height = H * 2;
  const img = out.getContext('2d').createImageData(W * 2, H * 2);
  const row = W * 2 * 4;
  for (let y = 0; y < H * 2; y++) img.data.set(px.subarray((H * 2 - 1 - y) * row, (H * 2 - y) * row), y * row);
  out.getContext('2d').putImageData(img, 0, 0);
  return out;
}
function miniBot(canvasEl, look, agent, pose) {
  const w = canvasEl.clientWidth || 34, h = canvasEl.clientHeight || 44;
  const r = Math.min(window.devicePixelRatio || 1, 2);
  canvasEl.width = w * r; canvasEl.height = h * r;
  if (!ROBOT) return;
  const key = [look.color, look.acc, agent, pose, w, h].join('|');
  let img = portraits.get(key);
  if (!img) { img = renderPortrait(look, agent, pose, w * r, h * r); portraits.set(key, img); }
  const g = canvasEl.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.drawImage(img, 0, 0, canvasEl.width, canvasEl.height);
}

// ------------------------------------------------------------------ side panel
const panel = document.getElementById('panel');
const PANEL_SCROLL_HOLD_MS = 180;
let panelScrollUntil = 0, panelRenderPending = false;
document.getElementById('panelBody').addEventListener('scroll', () => {
  panelScrollUntil = performance.now() + PANEL_SCROLL_HOLD_MS;
}, { passive: true });
export function select(key) {
  if (key !== selectedKey) document.getElementById('panelBody').scrollTop = 0;
  setSelectedKey(key);
  panel.classList.add('open');
  panel.setAttribute('aria-hidden', 'false');
  renderPanel();
  focusOn(ents.get(key));
}
export function closePanel() {
  setSelectedKey(null);
  panel.classList.remove('open');
  panel.setAttribute('aria-hidden', 'true');
}
export function renderPanel() {
  // mid-scroll, updates wait until the scroll settles rather than rewriting content under it
  const wait = panelScrollUntil - performance.now();
  if (wait > 0) {
    if (!panelRenderPending) { panelRenderPending = true; setTimeout(() => { panelRenderPending = false; renderPanel(); }, wait + 20); }
    return;
  }
  const e = ents.get(selectedKey);
  if (!e) return;
  if (isSession(e)) { renderSessionPanel(e); return; }
  const j = e.job;
  const ref = `${e.host}:${j.id}`;
  const headHtml = `<canvas style="width:46px;height:60px"></canvas>
    <div style="min-width:0;flex:1"><h2>${esc(j.description)}</h2>
      <div class="sub">
        <span class="chip"><i style="background:${e.look.color}"></i><b>${esc(e.host)}</b></span>
        <span class="chip"><i style="background:${AGENT_COLOR[j.agent] || '#ccc'}"></i>${esc(j.agent)}</span>
        <span class="chip st-${esc(j.status)}">${esc(j.status)}</span>
      </div></div>
    ${j.status !== 'running' ? DISMISS_BUTTON : ''}
    <button id="close" aria-label="Close">✕</button>`;
  const steps = j.steps || [];
  const stepIcon = { done: '✓', running: '▶', failed: '✗', cancelled: '⊘', pending: '○' };
  const events = (j.events || []).filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').slice(-18).reverse();
  const cmds = [`fleet attach ${ref}`, `fleet tail ${ref} -f`, `fleet show ${ref}`];
  // state updates arrive for every job, many times a second: replace only the sections that changed,
  // so the rest of the panel keeps its nodes and the scroll position stays put
  const sections = [`
    <h3>Job</h3>
    <dl class="meta">
      <dt>ref</dt><dd>${esc(ref)}</dd>
      <dt>project</dt><dd>${esc(j.project)}</dd>
      <dt>model</dt><dd>${esc(j.model || 'default')}</dd>
      <dt>cwd</dt><dd>${esc(j.cwd)}</dd>
      ${j.permission ? `<dt>perms</dt><dd>${esc(j.permission)}</dd>` : ''}
      <dt>updated</dt><dd>${esc(age(j.updated_at))} ago</dd>
    </dl>`, `
    <h3>Steps · ${steps.filter(s => s.status === 'done').length}/${steps.length}</h3>
    <ol class="steps">${steps.map(s => `<li class="${esc(s.status)}"><span class="si">${stepIcon[s.status] || '?'}</span>
      <span class="t">${s.index + 1}. ${esc(s.title)}</span>${s.result ? `<span class="r">${esc(trunc(s.result, 400))}</span>` : ''}</li>`).join('')}</ol>`,
    docsPanelHtml(e),
    (j.todos && j.todos.length) ? `<h3>Agent's own todo list</h3><ul class="todos">${j.todos.map(td => `<li class="${esc(td.status)}">${td.status === 'completed' ? '✓' : td.status === 'in_progress' ? '▸' : '·'} ${esc(td.text)}</li>`).join('')}</ul>` : '', `
    <h3>Recent activity</h3>
    ${events.length ? `<ul class="evs">${events.map(ev => `<li class="${ev.kind === 'error' ? 'err' : ''}"><time>${clock(ev.ts)}</time><span class="k">${esc(eventIcon(ev))}</span><span class="${isCodeEvent(ev) ? 'code' : ''}">${esc(trunc(ev.summary || ev.status || ev.kind, 220))}</span></li>`).join('')}</ul>` : '<p class="muted" style="font-size:12px">No events yet.</p>'}`, `
    <h3>Commands</h3>
    ${cmds.map(c => `<div class="cmd"><code>${esc(c)}</code><button data-copy="${esc(c)}">copy</button></div>`).join('')}`];
  patchPanel(headHtml, sections, e, j.status === 'done' ? 'off' : j.status === 'stalled' ? 'slump' : 'normal');
}
const DISMISS_BUTTON = '<button id="dismiss" title="Hide this agent from the deck until it has new activity">Dismiss</button>';
// State updates arrive for every job, many times a second: rewrite the head and each body section only when its
// markup changed, so the rest of the panel keeps its nodes and the scroll position stays put.
function patchPanel(headHtml, sections, e, pose) {
  const head = document.getElementById('panelHead');
  if (head.lastHtml !== headHtml) {
    head.lastHtml = headHtml;
    head.innerHTML = headHtml;
    miniBot(head.querySelector('canvas'), e.look, e.job.agent, pose);
    head.querySelector('#close').addEventListener('click', closePanel);
    head.querySelector('#dismiss')?.addEventListener('click', () => dismiss(selectedKey));
  }
  const body = document.getElementById('panelBody');
  if (body.childElementCount !== sections.length) body.replaceChildren(...sections.map(() => document.createElement('div')));
  sections.forEach((html, i) => {
    const part = body.children[i];
    if (part.lastHtml !== html) { part.lastHtml = html; part.innerHTML = html; }
  });
}
// An interactive session: what it is, where it runs, its todos and recent activity. No steps or fleet commands —
// the one useful command is resuming it in a terminal.
function renderSessionPanel(e) {
  const s = e.job;
  const headHtml = `<canvas style="width:46px;height:60px"></canvas>
    <div style="min-width:0;flex:1"><h2>${s.title ? esc(s.title) : '<span class="untitled">no title yet</span>'}</h2>
      <div class="sub">
        <span class="chip sess st-${esc(s.status)}"><i></i>live · ${esc(s.status === 'idle' ? 'waiting for you' : s.status)}</span>
        <span class="chip"><i style="background:${e.look.color}"></i><b>${esc(e.host)}</b></span>
        <span class="chip"><i style="background:${AGENT_COLOR[s.agent] || '#ccc'}"></i>${esc(s.agent)}</span>
      </div></div>
    ${s.status === 'idle' ? DISMISS_BUTTON : ''}
    <button id="close" aria-label="Close">✕</button>`;
  const events = (s.events || []).filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').slice(-18).reverse();
  const sections = [`
    <h3>Interactive session</h3>
    <dl class="meta">
      <dt>session</dt><dd>${esc(s.id)}</dd>
      <dt>project</dt><dd>${esc(s.project)}</dd>
      <dt>model</dt><dd>${esc(s.model || 'not reported yet')}</dd>
      <dt>cwd</dt><dd>${esc(s.cwd)}</dd>
      <dt>started</dt><dd>${s.started_at ? `${esc(clock(s.started_at))} · ${esc(age(s.started_at))} ago` : '<span class="muted">unknown</span>'}</dd>
      <dt>updated</dt><dd>${esc(clock(s.updated_at))} · ${esc(age(s.updated_at))} ago</dd>
    </dl>`,
    (s.todos && s.todos.length) ? `<h3>Agent's own todo list</h3><ul class="todos">${s.todos.map(td => `<li class="${esc(td.status)}">${td.status === 'completed' ? '✓' : td.status === 'in_progress' ? '▸' : '·'} ${esc(td.text)}</li>`).join('')}</ul>` : '', `
    <h3>Recent activity</h3>
    ${events.length ? `<ul class="evs">${events.map(ev => `<li class="${ev.kind === 'error' ? 'err' : ''}"><time>${clock(ev.ts)}</time><span class="k">${esc(eventIcon(ev))}</span><span class="${isCodeEvent(ev) ? 'code' : ''}">${esc(trunc(ev.summary || ev.kind, 220))}</span></li>`).join('')}</ul>` : '<p class="muted" style="font-size:12px">No activity in the transcript tail.</p>'}`,
    s.resume ? `<h3>Resume in a terminal</h3><div class="cmd"><code>${esc(s.resume)}</code><button data-copy="${esc(s.resume)}">copy</button></div>` : ''];
  patchPanel(headHtml, sections, e, 'normal');
}
function docsPanelHtml(e) {
  const docs = docsOf(e.job).reverse();
  if (!docs.length) return '';
  return `<h3>Documents · ${docs.length}</h3><ul class="docs" style="--hc:${e.look.color}">${docs.map(d => {
    const kind = kindOf(d);
    return `<li><button data-doc="${esc(d.id)}" title="Read ${esc(d.name)}"><span class="dk ${kind}" aria-hidden="true">${DOC_KIND[kind].glyph}</span>
      <span class="dn">${esc(d.name)}</span><span class="dm">${DOC_KIND[kind].label.toLowerCase()} · ${esc(docMeta(d))}</span><span class="go">Read →</span></button></li>`;
  }).join('')}</ul>`;
}
panel.addEventListener('click', ev => {
  const open = ev.target.closest('[data-doc]');
  if (open) {
    const e = ents.get(selectedKey), doc = e && (e.job.documents || []).find(d => d.id === open.dataset.doc);
    if (doc) openReader(e, doc);
    return;
  }
  const b = ev.target.closest('[data-copy]');
  if (!b) return;
  const text = b.dataset.copy;
  const done = () => { b.textContent = 'copied'; b.classList.add('ok'); setTimeout(() => { b.textContent = 'copy'; b.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  else fallbackCopy(text, done);
});
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (err) { /* nothing else to try */ }
  ta.remove();
}
function eventIcon(ev) {
  if (ev.kind === 'tool') return TOOL_ICON[ev.tool] || '•';
  return { text: '“', error: '!', step: '▸', job: '◆', result: '✓', log: '·' }[ev.kind] || '·';
}

// ------------------------------------------------------------------ local project library
const libraryPane = document.getElementById('libraryPane');
const libSearch = document.getElementById('libSearch');
let libraryDocs = [], libraryFocus = null;
function closeLibrary() {
  if (libraryPane.hidden) return;
  libraryPane.hidden = true;
  libraryFocus?.focus();
}
function renderLibrary() {
  const query = libSearch.value.trim().toLowerCase();
  const docs = libraryDocs.filter(d => `${d.project} ${d.title} ${d.id}`.toLowerCase().includes(query));
  const list = document.getElementById('libList');
  if (!docs.length) {
    list.innerHTML = `<p class="lib-empty">${libraryDocs.length ? 'No matching documents.' : 'No project libraries configured. Add one with <code>fleet library add PROJECT /path/to/repo</code>.'}</p>`;
    return;
  }
  const groups = new Map();
  for (const doc of docs) {
    if (!groups.has(doc.project)) groups.set(doc.project, []);
    groups.get(doc.project).push(doc);
  }
  list.innerHTML = [...groups].map(([project, items]) => `<section class="lib-group"><h3>${esc(project)}</h3>
    ${items.map(d => `<button class="lib-doc" data-project="${esc(d.project)}" data-id="${esc(d.id)}">
      <i aria-hidden="true">▤</i><span><b>${esc(d.title)}</b><small>${esc(d.id)} · ${esc(fmtSize(d.size))}</small></span></button>`).join('')}</section>`).join('');
}
async function showLibrary() {
  libraryFocus = document.activeElement;
  libraryPane.hidden = false;
  document.getElementById('libList').innerHTML = '<p class="lib-empty">Loading documents…</p>';
  libSearch.focus();
  try {
    const response = await fetch('/api/library');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    libraryDocs = (await response.json()).documents || [];
    renderLibrary();
  } catch (error) {
    document.getElementById('libList').innerHTML = `<p class="lib-empty">Couldn’t load the library: ${esc(error.message)}</p>`;
  }
}
document.getElementById('libraryOpen').addEventListener('click', showLibrary);
libraryPane.addEventListener('click', ev => {
  if (ev.target.closest('[data-lib-close]')) { closeLibrary(); return; }
  const button = ev.target.closest('.lib-doc');
  if (!button) return;
  const doc = libraryDocs.find(d => d.project === button.dataset.project && d.id === button.dataset.id);
  if (doc) openLibraryReader(doc);
});
libSearch.addEventListener('input', renderLibrary);

// ------------------------------------------------------------------ reader
const reader = document.getElementById('reader');
const rdSheet = reader.querySelector('.rd-sheet');
const rdBody = document.getElementById('rdBody');
const WIDE = matchMedia('(min-width: 1101px)');
const rdProgress = document.getElementById('rdProgress');
const rd = { key: null, source: 'job', host: null, job: null, doc: null, data: null, req: 0, raf: 0, lastFocus: null, tocLinks: [], tocCurrent: null };
const THEME_ICON = {
  dark: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/></svg><span class="lb">Paper</span>',
  paper: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z"/></svg><span class="lb">Dark</span>',
};

function setReaderTheme(theme) {
  rdSheet.dataset.theme = theme;
  const b = document.getElementById('rdTheme');
  b.innerHTML = THEME_ICON[theme];
  b.setAttribute('aria-label', theme === 'dark' ? 'Switch to the paper theme' : 'Switch to the dark theme');
}
setReaderTheme(store('localStorage','fleet.reader.theme') === 'paper' ? 'paper' : 'dark');

export function openReader(e, doc) {
  hideDocTip();
  rd.req++;
  rd.key = `${e.host}:${e.job.id}:${doc.id}`;
  rd.source = 'job';
  rd.host = e.host; rd.job = e.job; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderLoading();
  rdSheet.focus();
  loadDoc(rd.req);
}
function openLibraryReader(doc) {
  if (!reader.hidden) saveReaderScroll();
  rd.req++;
  rd.key = `library:${doc.project}:${doc.id}`;
  rd.source = 'library';
  rd.host = null; rd.job = { id: 'library', description: doc.project }; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderLoading();
  rdSheet.focus();
  loadDoc(rd.req);
}
function closeReader() {
  if (reader.hidden) return;
  saveReaderScroll();
  rd.req++; rd.key = null;
  reader.hidden = true;
  if (rd.lastFocus && rd.lastFocus.focus) rd.lastFocus.focus();
}
async function loadDoc(req) {
  try {
    const data = rd.source === 'library' ? await fetchLibraryDoc(rd.doc.project, rd.doc.id)
      : DEMO ? await demoDoc(rd.host, rd.job.id, rd.doc.id) : await fetchDoc(rd.host, rd.job.id, rd.doc.id);
    if (req !== rd.req) return;
    rd.data = data;
    renderReaderHead();
    renderReaderBody();
  } catch (err) {
    if (req === rd.req) renderReaderError(err.message || String(err));
  }
}
async function fetchDoc(host, job, id) {
  const res = await fetch('/api/doc?' + new URLSearchParams({ host, job, id }));
  let body = null;
  try { body = await res.json(); } catch (err) { /* not JSON */ }
  if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status} ${res.statusText}`);
  if (!body || typeof body.html !== 'string') throw new Error('The server returned an empty document.');
  return body;
}
async function fetchLibraryDoc(project, id) {
  const res = await fetch('/api/library/doc?' + new URLSearchParams({ project, id }));
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function renderReaderHead() {
  const doc = rd.doc, d = rd.data || {}, kind = kindOf(doc);
  const kindEl = document.getElementById('rdKind');
  kindEl.className = 'rd-kind ' + kind; kindEl.textContent = DOC_KIND[kind].label;
  document.getElementById('rdTitle').textContent = rd.source === 'library' ? (d.title || doc.title || d.name || doc.name) : (d.name || doc.name);
  const step = d.step ?? doc.step;
  document.getElementById('rdMeta').innerHTML = [
    rd.source === 'library' ? `<span>${esc(doc.project)} · ${esc(doc.id)}</span>`
      : `<span title="${esc(d.job_description || rd.job.description)}"><i class="hd" style="background:${hostLook(rd.host).color}"></i>${esc(rd.host)} · ${esc(rd.job.id)} · ${esc(d.agent || rd.job.agent)}</span>`,
    step != null ? `<span>step ${step + 1}</span>` : '',
    d.minutes ? `<span>${d.minutes} min read</span>` : '',
    (d.mtime || doc.mtime) ? `<span>updated ${age(d.mtime || doc.mtime)} ago</span>` : '',
  ].join('');
  document.getElementById('rdCopy').disabled = !rd.data;
  document.getElementById('rdDownload').disabled = !rd.data;
}
function renderReaderLoading() {
  document.getElementById('rdProgress').style.transform = 'scaleX(0)';
  rdBody.innerHTML = `<div class="rd-grid"><div class="rd-state rd-skel" aria-label="Loading document" role="status">
    <i class="h"></i>${[96, 88, 93, 60, 0, 91, 97, 85, 70].map(w => w ? `<i style="width:${w}%"></i>` : '<br>').join('')}</div></div>`;
  rdBody.scrollTop = 0;
}
function renderReaderError(message) {
  rdBody.innerHTML = `<div class="rd-grid"><div class="rd-state rd-error" role="alert"><b>Couldn’t open this document</b>
    <code>${esc(message)}</code><br><button class="rd-retry" id="rdRetry">Try again</button></div></div>`;
  document.getElementById('rdRetry').addEventListener('click', () => { renderReaderLoading(); loadDoc(++rd.req); });
}
function renderReaderBody() {
  const d = rd.data;
  const toc = (d.toc || []).filter(x => x.id && x.level <= 3);
  const showToc = toc.length >= 3, top = Math.min(...toc.map(x => x.level));
  rdBody.innerHTML = `<div class="rd-grid${showToc ? ' has-toc' : ''}">
    ${showToc ? `<details class="rd-toc"><summary>Contents<span>${toc.length}</span></summary><nav aria-label="Contents"><p class="lbl">Contents</p>
      ${toc.map(x => `<a href="#doc-${esc(x.id)}" class="l${x.level - top + 1}">${esc(x.text)}</a>`).join('')}</nav></details>` : ''}
    <article class="prose"></article></div>`;
  const prose = rdBody.querySelector('.prose');
  prose.innerHTML = d.html;   // rendered server-side with raw HTML escaped
  if (d.truncated) prose.insertAdjacentHTML('beforeend', '<p class="rd-note">This document was truncated for the reader. Download the Markdown for the full text.</p>');
  tidyProse(prose);
  syncTocMode();
  fitTables();
  if (document.fonts) document.fonts.ready.then(fitTables);
  rd.tocLinks = [...rdBody.querySelectorAll('.rd-toc a')]
    .map(a => ({ a, h: document.getElementById(a.getAttribute('href').slice(1)) }))
    .filter(x => x.h);
  rd.tocCurrent = null;
  rdBody.scrollTop = Number(store('sessionStorage','fleet.reader.scroll.' + rd.key)) || 0;
  onReaderScroll();
}
// heading ids are prefixed so a heading called "panel" or "legend" can't collide with the page's own ids
function tidyProse(prose) {
  for (const el of prose.querySelectorAll('[id]')) el.id = 'doc-' + el.id;
  for (const a of prose.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (href.startsWith('#')) a.setAttribute('href', '#doc-' + href.slice(1));
    else if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    else if (rd.source === 'library') {
      try {
        const url = new URL(href, location.origin + '/' + rd.doc.id);
        const id = decodeURIComponent(url.pathname.slice(1));
        if (url.origin === location.origin && libraryDocs.some(doc => doc.project === rd.doc.project && doc.id === id)) {
          a.dataset.libraryDoc = id;
          a.href = '#';
        }
      } catch (error) { /* leave an invalid link untouched */ }
    }
  }
  for (const table of prose.querySelectorAll('table')) {
    // single tokens (ids, codes) stay on one line; figures get tabular digits
    for (const cell of table.querySelectorAll('td')) {
      const text = cell.textContent.trim();
      if (text.length <= 24 && !/\s/.test(text)) cell.classList.add('nw');
      if (/^[−–+\-]?[£$€]?\d[\d.,]*\s*(%|¢|s|ms|B|KB|MB)?$/.test(text)) cell.classList.add('num');
    }
    const wrap = document.createElement('div'), scroller = document.createElement('div');
    wrap.className = 'rd-table'; scroller.className = 'rd-scroller'; scroller.tabIndex = 0;
    table.replaceWith(wrap); wrap.appendChild(scroller); scroller.appendChild(table);
    scroller.addEventListener('scroll', () => edgeFades(scroller), { passive: true });
  }
  for (const pre of prose.querySelectorAll('pre')) pre.tabIndex = 0;
}
function syncTocMode() {
  const toc = rdBody.querySelector('.rd-toc');
  if (toc) toc.open = WIDE.matches;
}
WIDE.addEventListener('change', syncTocMode);
// give tables that don't fit the 68ch measure a wider column (wide screens only; phones scroll them)
function fitTables() {
  const grid = rdBody.querySelector('.rd-grid');
  if (!grid) return;
  const scrollers = [...grid.querySelectorAll('.rd-scroller')];
  grid.classList.remove('wide');
  if (scrollers.some(s => s.scrollWidth > s.clientWidth + 1)) grid.classList.add('wide');
  scrollers.forEach(edgeFades);
}
// fade the edge a table can still scroll towards, so a clipped column reads as "more this way"
function edgeFades(s) {
  const wrap = s.parentElement, max = s.scrollWidth - s.clientWidth;
  wrap.classList.toggle('more-l', s.scrollLeft > 1);
  wrap.classList.toggle('more-r', s.scrollLeft < max - 1);
}
window.addEventListener('resize', () => { if (!reader.hidden) fitTables(); });

// reads first, then writes, so a scroll frame never forces a second layout
function onReaderScroll() {
  rd.raf = 0;
  const top = rdBody.scrollTop, max = rdBody.scrollHeight - rdBody.clientHeight;
  const current = currentTocLink(top, max);
  rdProgress.style.transform = `scaleX(${max > 0 ? clamp(top / max, 0, 1) : 1})`;
  if (current !== rd.tocCurrent) {
    if (rd.tocCurrent) rd.tocCurrent.removeAttribute('aria-current');
    if (current) current.setAttribute('aria-current', 'location');
    rd.tocCurrent = current;
  }
}
function currentTocLink(top, max) {
  const links = rd.tocLinks;
  if (!links.length) return null;
  if (max > 0 && top >= max - 2) return links[links.length - 1].a;
  const line = rdBody.getBoundingClientRect().top + 110;
  let current = links[0].a;
  for (const { a, h } of links) {
    if (h.getBoundingClientRect().top > line) break;
    current = a;
  }
  return current;
}
function saveReaderScroll() {
  if (rd.key && rd.data) store('sessionStorage','fleet.reader.scroll.' + rd.key, String(Math.round(rdBody.scrollTop)));
}
rdBody.addEventListener('scroll', () => { if (!rd.raf) rd.raf = requestAnimationFrame(onReaderScroll); }, { passive: true });
addEventListener('pagehide', saveReaderScroll);
rdBody.addEventListener('click', ev => {
  const linked = ev.target.closest('a[data-library-doc]');
  if (linked) {
    ev.preventDefault();
    const doc = libraryDocs.find(item => item.project === rd.doc.project && item.id === linked.dataset.libraryDoc);
    if (doc) openLibraryReader(doc);
    return;
  }
  const a = ev.target.closest('a[href^="#"]');
  if (!a) return;
  const target = document.getElementById(a.getAttribute('href').slice(1));
  if (!target) return;
  ev.preventDefault();
  target.scrollIntoView({ block: 'start', behavior: REDUCED ? 'auto' : 'smooth' });
  const toc = a.closest('.rd-toc');
  if (toc && !WIDE.matches) toc.open = false;
});
reader.addEventListener('click', ev => { if (ev.target.closest('[data-close]')) closeReader(); });
document.getElementById('rdTheme').addEventListener('click', () => {
  const theme = rdSheet.dataset.theme === 'dark' ? 'paper' : 'dark';
  setReaderTheme(theme);
  store('localStorage', 'fleet.reader.theme', theme);
});
document.getElementById('rdCopy').addEventListener('click', ev => {
  if (!rd.data) return;
  const b = ev.currentTarget, label = b.querySelector('.lb');
  const done = () => { label.textContent = 'Copied'; b.classList.add('ok'); setTimeout(() => { label.textContent = 'Copy'; b.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(rd.data.markdown).then(done, () => fallbackCopy(rd.data.markdown, done));
  else fallbackCopy(rd.data.markdown, done);
});
document.getElementById('rdDownload').addEventListener('click', () => {
  if (!rd.data) return;
  const prefix = rd.source === 'library' ? rd.doc.project : rd.job.id;
  const base = `${prefix}-${(rd.data.name || rd.doc.name).replace(/\.(md|markdown|mdx)$/i, '')}`.replace(/[^\w.-]+/g, '-').replace(/-+/g, '-');
  const url = URL.createObjectURL(new Blob([rd.data.markdown], { type: 'text/markdown;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: base + '.md' });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
// keep Tab inside the open reader
reader.addEventListener('keydown', ev => {
  if (ev.key !== 'Tab') return;
  const items = [...reader.querySelectorAll('button:not(:disabled),a[href],summary,[tabindex="0"]')].filter(el => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && (document.activeElement === first || document.activeElement === rdSheet)) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
});

// ------------------------------------------------------------------ legend, stats, feed
export function renderLegend() {
  const body = document.getElementById('legendBody');
  if (!hosts.length) { body.innerHTML = '<div class="crew"><small>No hosts reported yet.</small></div>'; return; }
  body.innerHTML = hosts.map(h => {
    const look = hostLook(h.name);
    const jobs = h.jobs || [];
    const running = jobs.filter(j => j.status === 'running').length, sessions = (h.sessions || []).length;
    const sub = h.ok
      ? `${look.label} ·${running ? running + ' working' : jobs.length ? jobs.length + ' job' + (jobs.length === 1 ? '' : 's') : 'idle'}${sessions ? ` · ${sessions} live` : ''}`
      : `offline — ${trunc((h.error || '').replace(/^[^:]+:\s*/, ''), 60)}`;
    return `<div class="crew${h.ok ? '' : ' off'}" title="${esc(h.ok ? h.name : h.error || 'unreachable')}">
      <canvas data-host="${esc(h.name)}"></canvas>
      <div class="who"><b style="color:${look.color}">${esc(h.name)}</b><small>${esc(sub)}</small></div><i class="st"></i></div>`;
  }).join('') + `<div class="agents"><span><i class="visor"></i>visor band · Claude</span><span><span class="eyes"><i></i><i></i></span>twin eyes · Codex</span></div>`;
  for (const cv of body.querySelectorAll('canvas[data-host]')) {
    const h = hosts.find(x => x.name === cv.dataset.host);
    miniBot(cv, hostLook(cv.dataset.host), 'claude', h && !h.ok ? 'off' : 'normal');
  }
}
export function renderStats() {
  const count = { running: 0, queued: 0, done: 0, failed: 0, stalled: 0 }, live = { working: 0, idle: 0 };
  for (const e of ents.values()) {
    const tally = isSession(e) ? live : count;
    if (tally[e.job.status] !== undefined) tally[e.job.status]++;
  }
  document.getElementById('stats').innerHTML = `
    ${live.working + live.idle ? `<span class="chip sess" title="interactive Claude Code / Codex sessions"><i></i><b>${live.working + live.idle}</b> live${live.idle ? `<span class="opt"> · ${live.idle} waiting</span>` : ''}</span>` : ''}
    <span class="chip"><i style="background:var(--run)"></i><b>${count.running}</b> working</span>
    <span class="chip opt"><i style="background:var(--warn)"></i><b>${count.queued}</b> queued</span>
    <span class="chip opt"><i style="background:var(--ok)"></i><b>${count.done}</b> done</span>
    <span class="chip"><i style="background:var(--bad)"></i><b>${count.failed + count.stalled}</b> need you</span>
    ${retiredCount ? `<button class="chip restore" id="toggleFinished" title="Show finished jobs that have left the deck"><b>${retiredCount}</b> finished · show</button>`
      : showFinished ? '<button class="chip restore" id="toggleFinished" title="Let finished jobs leave the deck again">hide finished</button>' : ''}
    ${hiddenCount ? `<button class="chip restore" id="restoreDismissed" title="Show dismissed agents again"><b>${hiddenCount}</b> hidden · show</button>` : ''}`;
}
document.getElementById('stats').addEventListener('click', ev => {
  if (ev.target.closest('#restoreDismissed')) restoreDismissed();
  else if (ev.target.closest('#toggleFinished')) toggleFinished();
});
export function renderLive() {
  const el = document.getElementById('live');
  if (DEMO) { el.className = 'live demo'; el.innerHTML = '<i></i><span>demo data</span>'; return; }
  if (live.ok) { el.className = 'live'; el.innerHTML = `<i></i><span>live · ${clock(Date.now() / 1000)}</span>`; }
  else { el.className = 'live bad'; el.innerHTML = `<i></i><span>${everLoaded ? 'server lost · retrying' : 'no server'}</span>`; }
}
export function collectEvents() {
  const fresh = [];
  for (const h of hosts) for (const j of h.jobs || []) for (const ev of j.events || []) {
    if (ev.kind === 'todos' || ev.kind === 'session' || !ev.summary) continue;
    const k = `${h.name}:${j.id}:${ev.ts}:${ev.kind}:${ev.summary}`;
    if (seenEvents.has(k)) continue;
    seenEvents.add(k);
    fresh.push({ host: h.name, id: j.id, label: j.id, ev, isNew: feedSeeded });
  }
  for (const h of hosts) for (const s of h.sessions || []) for (const ev of s.events || []) {
    if (ev.kind === 'todos' || !ev.summary || !s.project) continue;
    const k = `${h.name}:${s.id}:${ev.ts}:${ev.kind}:${ev.summary}`;
    if (seenEvents.has(k)) continue;
    seenEvents.add(k);
    fresh.push({ host: h.name, id: s.id, label: `${s.agent}·${shortId(s.id)}`, live: true, ev, isNew: feedSeeded });
  }
  fresh.sort((a, b) => a.ev.ts - b.ev.ts);
  for (const f of fresh) feed.unshift(f);
  if (!feedSeeded) { feed.sort((a, b) => b.ev.ts - a.ev.ts); setFeedSeeded(true); }
  feed.length = Math.min(feed.length, 60);
  if (seenEvents.size > 5000) { seenEvents.clear(); for (const f of feed) seenEvents.add(`${f.host}:${f.id}:${f.ev.ts}:${f.ev.kind}:${f.ev.summary}`); }
}
// Commands, paths and patterns read better in monospace; agent prose reads better in Inter.
const CODE_TOOLS = new Set(['bash', 'edit', 'read', 'search', 'web']);
function isCodeEvent(ev) { return ev.kind === 'tool' && CODE_TOOLS.has(ev.tool); }

export function renderFeed() {
  const ul = document.getElementById('feedList');
  if (!feed.length) { ul.innerHTML = '<li class="empty">Nothing has happened yet.</li>'; return; }
  ul.innerHTML = feed.map(f => `<li class="${f.ev.kind === 'error' ? 'err ' : ''}${f.isNew ? 'new' : ''}" data-key="${esc(f.host + ':' + f.id)}">
    <i style="background:${hostLook(f.host).color}"></i>${f.live ? '<span class="lv">LIVE</span>' : ''}<b>${esc(f.host)}:${esc(f.label)}</b><span class="k">${esc(eventIcon(f.ev))}</span>
    <span class="s${isCodeEvent(f.ev) ? ' code' : ''}">${esc(f.ev.summary)}</span><time>${clock(f.ev.ts)}</time></li>`).join('');
  for (const f of feed) f.isNew = false;
}
document.getElementById('feedList').addEventListener('click', ev => {
  const li = ev.target.closest('li[data-key]');
  if (li && ents.has(li.dataset.key)) select(li.dataset.key);
});
export function updateHint() {
  const hint = document.getElementById('hint');
  if (ents.size) { hint.hidden = true; return; }
  hint.hidden = false;
  hint.innerHTML = everLoaded
    ? `<h2>The deck is quiet</h2><p>No jobs on any host in the last day. Send one with</p><p><code>fleet send -H worker -p myrepo -d "…" -C ~/src/myrepo -s "…"</code></p><p><a href="?demo">See the demo crew</a></p>`
    : `<h2>Waiting for the fleet server</h2><p>This page is served by <code>fleet web</code>. It couldn&apos;t reach <code>/api/stream</code> yet.</p><p><a href="?demo">Open the demo instead</a></p>`;
}


// Demo only: a small Markdown renderer producing the same shapes as the server's markdown-it
// (heading ids, tables, task lists, footnotes). Everything is escaped before any tag is added.
function mdInline(s) {
  return s.split(/(`[^`]+`)/).map(part => part.length > 1 && part.startsWith('`') && part.endsWith('`')
    ? `<code>${esc(part.slice(1, -1))}</code>`
    : esc(part)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
      .replace(/\[\^(\w+)\]/g, '<sup class="footnote-ref"><a href="#fn-$1" id="fnref-$1">[$1]</a></sup>')
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>')).join('');
}
function mdList(lines) {
  const indent = l => l.match(/^\s*/)[0].length, bullet = /^\s*([-*]|\d+\.)\s+/;
  const base = indent(lines[0]), items = [];
  for (const l of lines) {
    if (indent(l) <= base && bullet.test(l)) items.push({ text: l.replace(bullet, ''), kids: [] });
    else if (items.length) items[items.length - 1].kids.push(l);
  }
  const tag = /^\s*\d+\./.test(lines[0]) ? 'ol' : 'ul';
  let tasks = false;
  const lis = items.map(it => {
    const m = it.text.match(/^\[([ xX])\]\s+(.*)$/);
    if (m) tasks = true;
    const box = m ? `<input class="task-list-item-checkbox" disabled type="checkbox"${m[1] === ' ' ? '' : ' checked'}>` : '';
    return `<li${m ? ' class="task-list-item"' : ''}>${box}${mdInline(m ? m[2] : it.text)}${it.kids.length ? mdList(it.kids) : ''}</li>`;
  }).join('');
  return `<${tag}${tasks ? ' class="contains-task-list"' : ''}>${lis}</${tag}>`;
}
function mdToHtml(md) {
  const lines = md.split('\n'), out = [], toc = [], notes = [], used = new Set();
  const slug = text => { const b = text.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-') || 'section'; let k = b, n = 1; while (used.has(k)) k = `${b}-${n++}`; used.add(k); return k; };
  const starts = l => /^(#{1,6}\s|```|>|\||\s*([-*]|\d+\.)\s|\[\^\w+\]:)/.test(l) || /^(-{3,}|\*{3,})\s*$/.test(l);
  const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  let i = 0, m;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if ((m = l.match(/^(#{1,6})\s+(.*)$/))) {
      const level = m[1].length, text = m[2].trim();
      if (level <= 3) { const id = slug(text); toc.push({ level, id, text }); out.push(`<h${level} id="${id}">${mdInline(text)}</h${level}>`); }
      else out.push(`<h${level}>${mdInline(text)}</h${level}>`);
      i++;
    } else if (l.startsWith('```')) {
      const lang = l.slice(3).trim(), buf = [];
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) buf.push(lines[i]);
      i++;
      out.push(`<pre><code${lang ? ` class="language-${esc(lang)}"` : ''}>${esc(buf.join('\n'))}\n</code></pre>`);
    } else if (/^(-{3,}|\*{3,})\s*$/.test(l)) { out.push('<hr>'); i++; }
    else if (l.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith('>')) buf.push(lines[i++].replace(/^>\s?/, ''));
      out.push(`<blockquote>${mdToHtml(buf.join('\n')).html}</blockquote>`);
    } else if (l.startsWith('|')) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith('|')) rows.push(lines[i++]);
      const align = cells(rows[1] || '').map(c => /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : '');
      const at = k => align[k] ? ` style="text-align:${align[k]}"` : '';
      out.push(`<table><thead><tr>${cells(rows[0]).map((c, k) => `<th${at(k)}>${mdInline(c)}</th>`).join('')}</tr></thead><tbody>${
        rows.slice(2).map(r => `<tr>${cells(r).map((c, k) => `<td${at(k)}>${mdInline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
    } else if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const buf = [];
      while (i < lines.length && (/^\s*([-*]|\d+\.)\s/.test(lines[i]) || /^\s{2,}\S/.test(lines[i]))) buf.push(lines[i++]);
      out.push(mdList(buf));
    } else if ((m = l.match(/^\[\^(\w+)\]:\s*(.*)$/))) { notes.push({ id: m[1], text: m[2] }); i++; }
    else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !(buf.length && starts(lines[i]))) buf.push(lines[i++].trim());
      out.push(`<p>${mdInline(buf.join(' '))}</p>`);
    }
  }
  if (notes.length) out.push(`<hr class="footnotes-sep"><section class="footnotes"><ol class="footnotes-list">${notes.map(n =>
    `<li id="fn-${esc(n.id)}" class="footnote-item"><p>${mdInline(n.text)} <a href="#fnref-${esc(n.id)}" class="footnote-backref">↩︎</a></p></li>`).join('')}</ol></section>`);
  return { html: out.join('\n'), toc };
}

const DEMO_DOCS = {
  shadowDiff: `# Shadow-parse vs Textract: line-item diff

Ran the shadow parser over **60 invoices** from today’s sample (12 suppliers) and diffed every line item against the Textract baseline. 51 invoices match exactly; 9 have at least one mismatch, 23 rows in total.

> The shadow parser is ahead on credit notes and multi-page invoices, but still trails Textract on handwritten quantity corrections. Nothing here blocks the rollout to the 12 pilot restaurants.

## Summary

| Measure | Shadow parser | Textract | Δ |
|---|---:|---:|---:|
| Invoices parsed | 60 | 60 | 0 |
| Exact line-item match | 51 | 47 | +4 |
| Rows with a mismatch | 23 | 31 | −8 |
| Median latency (s) | 2.8 | 6.1 | −3.3 |
| Cost per invoice (¢) | 0.9 | 1.5 | −0.6 |

## Method

1. Pulled the sample with \`fleet-sample --date 2026-09-25 --limit 60\` and hashed every page image so both parsers saw identical input.
2. Ran both parsers with the same images and no retries.
3. Normalised supplier codes and units before comparing:
   - \`KG\`, \`kg\` and \`Kilo\` all become \`kg\`
   - pack sizes such as \`6x1L\` split into quantity and unit
4. Compared line by line on *description, quantity, unit price and line total*, with a 0.5 % tolerance on totals.

\`\`\`python
def rows_match(a: LineItem, b: LineItem, tolerance: Decimal = Decimal("0.005")) -> bool:
    if normalise(a.description) != normalise(b.description):
        return False
    if a.quantity != b.quantity:
        return False
    return abs(a.line_total - b.line_total) <= tolerance * abs(b.line_total)
\`\`\`

## Mismatches by cause

### Handwritten corrections

Seven rows. A driver crossed out the printed quantity and wrote a new one beside it; Textract read the handwriting in four of them and we read none. This is the only category where the shadow parser is clearly behind.

| Invoice | Supplier | Line | Printed | Written | Shadow | Textract |
|---|---|---|---:|---:|---:|---:|
| INV-1001 | Supplier A | Chicken thigh 2 kg | 6 | 4 | 6 | 4 |
| INV-1001 | Supplier A | Rapeseed oil 20 L | 2 | 1 | 2 | 1 |
| INV-1002 | Supplier B | Double cream 2 L | 10 | 8 | 10 | 10 |
| INV-1003 | Supplier B | Free-range eggs ×180 | 3 | 2 | 3 | 2 |
| INV-1006 | Supplier C | Lemons (box of 80) | 2 | 1 | 2 | 1 |

### Credit notes

Textract flipped negative line totals to positive in five of six credit notes. The shadow parser keeps the sign because it reads the document type before any line items[^1]. This matters more than the row count suggests: a flipped credit note overstates spend twice, once for the credit and once for the original.

### Multi-page invoices

Textract dropped the carried-forward subtotal on page two of three invoices, so its totals were short by exactly the page-one amount:

\`\`\`text
INV-1004  page 1 subtotal   £412.60   carried forward: missing
INV-1004  page 2 total      £198.35   expected £610.95
INV-1005  page 1 subtotal   £1,084.20 carried forward: missing
\`\`\`

### Unit-price rounding

Four rows differ by a penny because the supplier prints unit prices to three decimal places. Both parsers are arguably right. The relative tolerance should absorb these, and does, except for line totals under £1, where half a percent is less than a penny.

## What I changed

- Added \`luc_tolerance_abs = Decimal("0.01")\` alongside the relative tolerance, and a test for a £0.84 line.
- Kept document-type detection ahead of line parsing, with a regression test for each of the six credit notes.
- Did **not** touch handwriting: that needs a model change, not a rule, and belongs in its own job.

## Checklist

- [x] Sample pulled and hashed
- [x] Both parsers run on identical images
- [x] Mismatches clustered by cause
- [ ] Taxonomy update drafted (next step)
- [ ] Findings reviewed by the invoices team

## Open questions

1. Should handwritten corrections be *flagged for review* rather than parsed? A flag is cheap and safe; parsing them wrong is expensive.
2. When printed and written quantities disagree and there is no delivery note, which one do we trust?
3. Is a penny of rounding worth surfacing to the restaurant at all, or should it be silently absorbed?

---

The full row-level list is in the outbox as \`mismatches.md\`.

[^1]: \`DocumentKind.CREDIT_NOTE\` is detected from the header block before any line items are read, so the sign is applied once, at the end.`,

  mismatches: `# Mismatches (23 rows)

Row-level list behind the step 3 report. Amounts in GBP.

| # | Invoice | Supplier | Cause | Shadow | Textract | Expected |
|---:|---|---|---|---:|---:|---:|
| 1 | INV-1001 | Supplier A | handwriting | 6 | 4 | 4 |
| 2 | INV-1001 | Supplier A | handwriting | 2 | 1 | 1 |
| 3 | INV-1002 | Supplier B | handwriting | 10 | 10 | 8 |
| 4 | CN-1001 | Supplier B | credit sign | −12.40 | 12.40 | −12.40 |
| 5 | CN-1002 | Supplier A | credit sign | −3.10 | 3.10 | −3.10 |
| 6 | INV-1004 | Supplier C | carried forward | 610.95 | 198.35 | 610.95 |
| 7 | INV-1006 | Supplier C | rounding | 0.84 | 0.85 | 0.84 |
| 8 | INV-1007 | Supplier C | rounding | 0.63 | 0.62 | 0.63 |

Rows 9–23 follow the same four causes; see the step report for the breakdown.`,

  diffMethod: `# How the Textract diff works

A short note so the next run can reuse the method.

- Both parsers get the **same page images**, hashed before the run.
- Units and supplier codes are normalised first; see \`normalise()\` in \`shadow/diff.py\`.
- Totals use a relative tolerance of 0.5 % *and* an absolute floor of one penny.

\`\`\`bash
python -m shadow.diff --sample samples/2026-09-25 --baseline textract --out outbox/
\`\`\``,

  article: `# PAR by weekday

Par levels tell you how much of each item to keep on hand. Most kitchens set one number and live with it, but Friday is not Tuesday. **PAR by weekday** lets you set a different par for each day, so you order for the service you are actually going to have.

## When to use it

- Your covers swing a lot across the week (for example, quiet Mondays and a packed Saturday).
- You receive deliveries on fixed days and want each order to cover the gap until the next one.
- You waste perishables early in the week and run short at the weekend.

## Setting it up

1. Open **Inventory → Par levels** and pick an item.
2. Switch *Same every day* to **By weekday**.
3. Enter a par for each day. Leave a day blank to fall back to the item’s default.

| Day | Covers (avg) | Par: salmon fillets |
|---|---:|---:|
| Monday | 62 | 8 |
| Friday | 148 | 20 |
| Saturday | 171 | 24 |

> Start with your busiest day and your quietest day. The days in between are usually obvious once those two are right.

## What changes in ordering

Suggested orders now use the par for the day the delivery **covers**, not the day you place it. If Tuesday’s delivery has to last until Friday, the suggestion uses the highest par across those days.`,

  deckLayout: `# Deck layout notes

How the rooms are laid out, so the next change doesn’t fight the walk paths.

## Room grid

| Prop | Tiles (x, y) | Who stands there |
|---|---|---|
| Terminals | 0.9–6.3, 0.25 | bash |
| Whiteboard | 6.9–9.5, 0 | plan, delegate fallback |
| Workbench | 4.8–7.6, 4.2 | edit |
| Document press | 11.1–11.9, 2.9–5.5 | nobody; documents land here |

## Walk paths

- Two aisles, at y = 2.6 and y = 7.3.
- Crossings at x = 3.6 and x = 8.7, so nobody walks through the workbench.

## Still to do

- [x] Trays tinted by host
- [x] Printed-sheet animation
- [ ] Tune stack height at 390 px`,

  rootCause: `# Root cause: flaky invoice upload e2e

The upload test failed about one run in twelve. The poller and the upload handler both called \`commit()\` on the same session, and whichever landed second raised \`StaleDataError\`.

## Fix

\`\`\`python
with upload_lock(invoice.id):
    session.refresh(invoice)
    invoice.status = Status.PARSED
    session.commit()
\`\`\`

## Evidence

| Runs | Before | After |
|---:|---:|---:|
| 50 | 4 failures | 0 failures |
| 200 | 17 failures | 0 failures |

- [x] Reproduced 20× before the fix
- [x] 250 green runs after`,
};

function stepReport(job, i) {
  const s = job.steps[i];
  return `# Step ${i + 1}: ${s.title}

${s.result || 'Done.'}

## What I did

- Read the code paths involved and the existing tests before changing anything.
- Made the change in small commits in \`${job.cwd}\`, re-running the affected tests after each one.
- Checked the result against the step’s goal rather than just the tests.

## Checks

| Check | Command | Result |
|---|---|---|
| Unit tests | \`pytest -q\` | 142 passed |
| Lint | \`ruff check .\` | clean |
| Types | \`mypy .\` | no issues |

## Left open

Nothing blocking for the next step.`;
}

let demoDoc = null;   // set by demoSource: (host, job, id) → document, as /api/doc would return it

function demoSource() {
  const rand = seeded(42);
  const pick = arr => arr[Math.floor(rand() * arr.length)];
  const now = () => Date.now() / 1000;
  const SUMMARIES = {
    bash: ['pytest tests/invoices/test_upload.py -q', 'npx vitest run src/v2/suppliers', 'git diff --stat', 'ruff check app/suppliers', 'make migrate', 'npm run lint -- --fix', 'git log --oneline -5',
      'git commit -m "Port supplier filters to the V2 route"', 'git push -u origin HEAD', 'npm install', 'npm run build', 'docker compose up -d db', 'sleep 30',
      'curl -s https://api.github.com/repos/example/demo-store/pulls', 'ssh node-b fleet ls', 'psql -c "select count(*) from invoices"', 'ls -la fleet/web', 'mypy app/invoices',
      'git add -A', 'python scripts/export_suppliers.py --dry-run', 'cat package.json', 'git checkout -b fix/upload-poller'],
    edit: ['frontend/src/v2/routes/suppliers.tsx', 'app/invoices/parser/luc.py', 'fleet/web/index.html', 'docs/guides/onboarding.md', 'app/suppliers/adapters.py', 'tests/test_fleetd_parsers.py'],
    read: ['app/suppliers/views.py', 'docs/adr/0002-module-refactor.md', 'frontend/src/v2/router.tsx', 'fleet/remote/fleetd.py', 'invoices/sample-0412.json'],
    search: ['SupplierRow', '**/*.spec.ts', 'luc_tolerance', 'docs/**/*.png'],
    web: ['https://tanstack.com/router/latest/docs/guide/data-loading', 'https://docs.python.org/3/library/decimal.html', 'https://playwright.dev/docs/screenshots'],
    think: ['Weighing whether the tolerance should be relative to the line total…', 'The flake only happens when the poller fires twice…', 'Two ways to split the loader; the second keeps parity…'],
    plan: ['{"todos": […]}'],
    delegate: ['Find every caller of get_active_restaurants', 'List routes still on the legacy table'],
  };
  // shaped like fleetd's events: the Claude tool name, its main argument, and for shell calls sometimes Claude's description
  const INTENTS = { 'make migrate': 'Run the database migrations', 'git diff --stat': 'Show what changed', 'ruff check app/suppliers': 'Lint the suppliers module',
    'git push -u origin HEAD': 'Push the branch', 'sleep 30': 'Wait for the server to come up' };
  const demoTool = kind => {
    const summary = pick(SUMMARIES[kind]);
    const name = { bash: 'Bash', edit: DOC_FILE.test(summary) ? 'Write' : 'Edit', read: 'Read', web: 'WebFetch', think: '', plan: 'TodoWrite', delegate: 'Agent' }[kind]
      ?? (summary.includes('*') ? 'Glob' : 'Grep');
    const ev = { kind: 'tool', tool: kind, name, summary };
    if (kind === 'bash' && INTENTS[summary] && rand() < 0.7) ev.intent = INTENTS[summary];
    return ev;
  };
  const WEIGHTS = [['bash', 8], ['edit', 4], ['read', 4], ['search', 2], ['web', 1], ['think', 3], ['plan', 1], ['delegate', 1.2]];
  // ?debug&pin=ship,read,…: running jobs only do these activities (job i does the i-th, round the list), for close-ups;
  // an entry like type+ship alternates between them
  const PINS = DEBUG && QS.get('pin') ? QS.get('pin').split(',') : null;
  const pinned = act => {
    for (let k = 0; k < 400; k++) { const ev = demoTool(pickTool()); if (activityOf(ev) === act) return ev; }
    return demoTool('bash');
  };
  const pickTool = () => { let x = rand() * WEIGHTS.reduce((s, w) => s + w[1], 0); for (const [k, w] of WEIGHTS) { if ((x -= w) <= 0) return k; } return 'bash'; };
  const RESULTS = ['Done — 14 files touched, tests green.', 'Found the race: poller and upload both call commit(). Patched with a lock.', 'Parity confirmed against master for all 6 filters.', 'Wrote findings to outbox/mismatches.md (23 rows).', 'All steps reproduced locally; nothing left open.'];
  const TODO_POOL = ['Read the failing test', 'Reproduce locally', 'Write the fix', 'Run the affected tests', 'Update the docs', 'Check parity with master', 'Summarise for the orchestrator'];
  const specs = [
    ['node-a', 'demo-store', 'Migrate the suppliers list to a V2 React route', 'claude', 'claude-opus-5-5', ['Map legacy supplier list behaviour', 'Build SuppliersRoute with a TanStack loader', 'Port filters and sort', 'Add parity tests', 'Run vitest + e2e', 'Write the PR description'], 2, 'running'],
    ['node-b', 'demo-store', 'Fix the flaky invoice upload e2e test', 'codex', 'gpt-5-codex', ['Reproduce the flake 20×', 'Find the race in the upload poller', 'Patch and rerun 50×', 'Summarise the root cause'], 1, 'running'],
    ['node-c', 'demo-store', 'Apply review feedback', 'claude', 'claude-opus-5-5', ['Collect review threads', 'Apply naming fixes', 'Re-run affected tests'], 0, 'running'],
    ['node-c', 'demo-parser', 'Shadow-parse 60 invoices and diff against Textract', 'claude', 'claude-opus-5-5', ['Pull today’s invoice sample', 'Run the shadow parser', 'Diff line items', 'Cluster mismatches', 'Write findings to outbox', 'Draft taxonomy update', 'Summarise'], 3, 'running'],
    ['node-b', 'demo-parser', 'Tune LUC tolerance for credit notes', 'codex', 'gpt-5-codex', ['Add a failing test for negative LUC', 'Adjust the tolerance', 'Run the parser suite'], 1, 'failed'],
    ['node-a', 'agent-fleet', 'Isometric deck view for fleet web', 'claude', 'claude-opus-5-5', ['Sketch the room layout', 'Draw androids per host', 'Walk-to-prop animation', 'Detail panel', 'Screenshot at 390px'], 2, 'running'],
    ['node-c', 'agent-fleet', 'Unit tests for the fleetd parsers', 'codex', 'gpt-5-codex', ['Claude stream fixtures', 'Codex exec fixtures', 'Runner lock tests'], 0, 'queued'],
    ['node-b', 'demo-docs', 'Refresh the onboarding guide screenshots', 'claude', 'claude-opus-5-5', ['List stale screenshots', 'Capture new ones', 'Update the markdown'], 1, 'stalled'],
    ['node-a', 'demo-docs', 'Support article: PAR by weekday', 'claude', 'claude-opus-5-5', ['Read the feature PR', 'Draft the article', 'Tighten the copy'], 3, 'done'],
  ];
  const t0 = now();
  function newTodos(job) { const start = Math.floor(rand() * 4); job.todos = TODO_POOL.slice(start, start + 3).map((text, i) => ({ text, status: i === 0 ? 'in_progress' : 'pending' })); }
  const jobs = specs.map(([host, project, description, agent, model, titles, cur, status], i) => {
    const id = hash(description).toString(16).padStart(8, '0').slice(0, 6);
    const job = {
      id, host, project, description, agent, model, status, cwd: `~/src/${project}`,
      permission: agent === 'claude' ? 'acceptEdits' : 'workspace-write',
      created_at: t0 - 3600 + i * 240, updated_at: t0 - (status === 'stalled' ? 1500 : 20),
      session_id: null, tmux: `tmux -L fleet attach -t fleet-${id}`, todos: [], events: [], activity: null, ticks: 0, documents: [],
      steps: titles.map((title, idx) => ({
        index: idx, title, result: null, started_at: null, finished_at: null,
        status: status === 'done' || idx < cur ? 'done' : idx > cur ? 'pending'
          : (status === 'running' || status === 'stalled') ? 'running' : status === 'failed' ? 'failed' : 'pending',
      })),
    };
    job.steps.forEach(s => { if (s.status === 'done') s.result = pick(RESULTS); });
    if (status === 'failed') job.steps[cur].result = 'test_negative_luc_credit_note still fails: expected -12.40, got -12.00 (tolerance applied before sign).';
    const at = (k, e) => job.events.push({ ts: t0 - 400 + k * 30 + i, step: Math.max(0, Math.min(cur, titles.length - 1)), ...e });
    at(0, { kind: 'job', status: 'queued', summary: `job created: ${description}` });
    if (status !== 'queued') for (let k = 1; k < 7; k++) at(k, demoTool(pickTool()));
    if (status === 'failed') at(8, { kind: 'error', summary: 'exit 1: pytest tests/test_luc.py' });
    if (status === 'done') at(9, { kind: 'job', status: 'done', summary: 'job done' });
    if (status === 'stalled') at(9, { kind: 'log', summary: 'runner exited unexpectedly (SIGKILL)' });
    if (status === 'running') newTodos(job);
    job.activity = [...job.events].reverse().find(e => e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') || null;
    return job;
  });

  // interactive CLI sessions, shaped like `fleetd sessions` output: a working Claude, an idle one, a working Codex
  const sessionSpecs = [
    ['node-a', 'agent-fleet', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000001', 'Show live CLI sessions on the deck', 'working', 1900],
    ['node-b', 'demo-store', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000002', 'Why does the stocktake import modal re-render twice?', 'idle', 2600],
    ['node-c', 'demo-parser', 'codex', 'gpt-6-sol', '00000000-0000-4000-8000-000000000003', 'review the unstaged changes are they correct and safe?', 'working', 900],
  ];
  const sessions = sessionSpecs.map(([host, project, agent, model, id, title, status, startedAgo], i) => {
    const cwd = `~/src/${project}`;
    const session = {
      id, host, agent, cwd, project, title, status, model, started_at: t0 - startedAgo,
      updated_at: t0 - (status === 'idle' ? 380 : 4), todos: [], events: [], activity: null,
      resume: `cd ${cwd} && ${agent === 'codex' ? 'codex resume' : 'claude --resume'} ${id}`,
    };
    for (let k = 0; k < 6; k++) session.events.push({ ...demoTool(pickTool()), ts: t0 - 700 + k * 50 + i });
    if (status === 'idle') session.events.push({ kind: 'text', summary: 'The modal re-renders because the loader returns a new object each time. Want me to memoise it or move the fetch up?', ts: session.updated_at });
    if (agent === 'claude' && status === 'working') session.todos = [
      { text: 'Discover sessions in fleetd', status: 'completed' }, { text: 'Carry them through the server', status: 'completed' },
      { text: 'Session androids on the deck', status: 'in_progress' }, { text: 'Screenshots', status: 'pending' }];
    session.activity = [...session.events].reverse().find(e => e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') || null;
    return session;
  });

  // documents: jobs carry metadata only, as from the server; the markdown stays here for demoDoc
  const docText = new Map();
  function addDoc(job, id, kind, name, step, markdown, mtime) {
    if (job.documents.some(d => d.id === id)) return;
    const path = kind === 'report' ? `~/.fleet/jobs/${job.id}/result-${step}.md` : kind === 'outbox' ? `~/.fleet/jobs/${job.id}/outbox/${name}` : `${job.cwd}/${name}`;
    job.documents.push({ id, kind, name, step, path, size: new TextEncoder().encode(markdown).length, mtime: mtime || now() });
    docText.set(`${job.host}:${job.id}:${id}`, markdown);
  }
  function addReport(job, i, mtime) { addDoc(job, `report-${i}`, 'report', `Step ${i + 1}: ${job.steps[i].title}`, i, stepReport(job, i), mtime); }
  const [suppliers, flaky, , shadow, luc, deck, , , article] = jobs;
  addReport(suppliers, 0, t0 - 2400); addReport(suppliers, 1, t0 - 1300);
  addDoc(suppliers, 'file-0', 'file', 'docs/suppliers-v2-parity.md', 1, DEMO_DOCS.deckLayout.replace('Deck layout notes', 'Suppliers V2 parity notes'), t0 - 1250);
  addReport(flaky, 0, t0 - 900);
  addReport(shadow, 0, t0 - 3000); addReport(shadow, 1, t0 - 2100);
  addDoc(shadow, 'file-0', 'file', 'notes/textract-diff-method.md', 2, DEMO_DOCS.diffMethod, t0 - 1500);
  addDoc(shadow, 'report-2', 'report', 'Step 3: Diff line items', 2, DEMO_DOCS.shadowDiff, t0 - 700);
  addDoc(shadow, 'outbox-mismatches.md', 'outbox', 'mismatches.md', null, DEMO_DOCS.mismatches, t0 - 650);
  addReport(luc, 0, t0 - 1800);
  addReport(deck, 0, t0 - 2600); addReport(deck, 1, t0 - 1400);
  addReport(article, 0, t0 - 5200);
  addDoc(article, 'file-0', 'file', 'drafts/par-by-weekday-v1.md', 1, DEMO_DOCS.article, t0 - 4800);
  addDoc(article, 'file-1', 'file', 'drafts/par-by-weekday-v2.md', 1, DEMO_DOCS.article, t0 - 4300);
  addReport(article, 1, t0 - 4000);
  addDoc(article, 'file-2', 'file', 'articles/par-by-weekday.md', 2, DEMO_DOCS.article, t0 - 3500);
  addReport(article, 2, t0 - 3300);
  addDoc(article, 'outbox-par-by-weekday.md', 'outbox', 'par-by-weekday.md', null, DEMO_DOCS.article, t0 - 3200);
  let tickCount = 0;
  demoDoc = async (host, jobId, id) => {
    await new Promise(resolve => setTimeout(resolve, 350));
    const job = jobs.find(j => j.host === host && j.id === jobId);
    const meta = job && job.documents.find(d => d.id === id);
    if (!meta) throw new Error(`no document ${id} on ${host}:${jobId}`);
    const markdown = docText.get(`${host}:${jobId}:${id}`), { html, toc } = mdToHtml(markdown);
    const words = markdown.split(/\s+/).filter(Boolean).length;
    return { ...meta, job: job.id, project: job.project, agent: job.agent, host, job_description: job.description,
      markdown, html, toc, words, minutes: Math.max(1, Math.round(words / 230)), truncated: false };
  };
  function push(job, e) {
    job.events.push({ ts: now(), step: job.steps.findIndex(s => s.status === 'running'), ...e });
    if (job.events.length > 15) job.events.splice(0, job.events.length - 15);
    job.updated_at = now();
    if (e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') job.activity = job.events[job.events.length - 1];
  }
  function tick() {
    tickCount++;
    if (tickCount === 3) { addDoc(deck, 'file-0', 'file', 'docs/deck-layout.md', 2, DEMO_DOCS.deckLayout); push(deck, { kind: 'tool', tool: 'edit', summary: 'docs/deck-layout.md' }); }
    if (tickCount === 7) addDoc(flaky, 'outbox-root-cause.md', 'outbox', 'root-cause.md', null, DEMO_DOCS.rootCause);
    for (const job of jobs) {
      job.ticks++;
      if (job.status === 'queued' && job.ticks > 7) {
        job.status = 'running'; job.steps[0].status = 'running'; job.steps[0].started_at = now(); newTodos(job);
        push(job, { kind: 'step', status: 'running', summary: job.steps[0].title });
        continue;
      }
      if (job.status === 'done' && job.ticks > 16) {   // re-queued: idles in the lounge for a while, then starts over
        job.steps.forEach(s => { s.status = 'pending'; s.result = null; });
        job.status = 'queued'; job.ticks = 0;
        push(job, { kind: 'job', status: 'queued', summary: `${job.steps.length} step(s) re-queued` });
        continue;
      }
      if (job.status !== 'running') continue;
      const i = jobs.indexOf(job);
      if (PINS) {
        const seq = PINS[i % PINS.length].split('+'), act = seq[Math.floor(tickCount / 5) % seq.length];   // type+ship: alternate every 10 s
        // a pinned test run alternates with its outcome: an error half the time, otherwise the next command
        if (act === 'test' && job.tested) push(job, rand() < 0.5 ? { kind: 'error', summary: 'exit 1: pytest -q (2 failed, 41 passed)' } : { kind: 'tool', tool: 'bash', name: 'Bash', summary: 'git add -A' });
        else push(job, pinned(act));
        job.tested = act === 'test' && !job.tested;
        continue;
      }
      // a test run is followed by its outcome: sometimes a failure, otherwise the agent simply carries on
      if (job.tested) {
        job.tested = false;
        if (rand() < 0.35) { push(job, { kind: 'error', summary: 'exit 1: 2 failed, 41 passed' }); continue; }
      }
      if (rand() < 0.55) {
        const tool = pickTool();
        const sameRoom = jobs.some(o => o !== job && o.project === job.project);
        const kind = tool === 'delegate' && !sameRoom ? 'read' : tool;
        if (kind === 'think' && rand() < 0.4) push(job, { kind: 'text', summary: pick(SUMMARIES.think) });
        else push(job, demoTool(kind));
        job.tested = activityOf(job.activity) === 'test';
      }
      if (rand() < 0.3 && job.todos.length) {
        const i = job.todos.findIndex(td => td.status !== 'completed');
        if (i >= 0) { job.todos[i].status = 'completed'; if (job.todos[i + 1]) job.todos[i + 1].status = 'in_progress'; }
      }
      if (rand() < 0.08) {
        const i = job.steps.findIndex(s => s.status === 'running');
        if (i < 0) continue;
        job.steps[i].status = 'done'; job.steps[i].finished_at = now(); job.steps[i].result = pick(RESULTS);
        addReport(job, i);
        push(job, { kind: 'step', status: 'done', summary: job.steps[i].result });
        if (job.steps[i + 1]) { job.steps[i + 1].status = 'running'; job.steps[i + 1].started_at = now(); newTodos(job); push(job, { kind: 'step', status: 'running', summary: job.steps[i + 1].title }); }
        else { job.status = 'done'; job.ticks = 0; job.todos = []; push(job, { kind: 'job', status: 'done', summary: 'job done' }); }
      }
    }
    for (const s of sessions) {
      if (s.status !== 'working' || rand() > 0.5) continue;
      const tool = pickTool();
      s.events.push(tool === 'think' && rand() < 0.4 ? { kind: 'text', summary: pick(SUMMARIES.think), ts: now() }
        : { ...demoTool(tool === 'delegate' ? 'read' : tool), ts: now() });
      if (s.events.length > 15) s.events.splice(0, s.events.length - 15);
      s.activity = s.events[s.events.length - 1];
      s.updated_at = now();
    }
    const doc = { time: now(), hosts: [
      ...['node-a', 'node-b', 'node-c'].map(name => ({ name, ok: true, error: null, jobs: jobs.filter(j => j.host === name),
        sessions: sessions.filter(s => s.host === name) })),
      { name: 'node-d', ok: false, error: 'node-d: ssh: connect to host 192.0.2.10 port 22: Connection timed out', jobs: [] },
    ] };
    return JSON.parse(JSON.stringify(doc));
  }
  return tick;
}

// ------------------------------------------------------------------ boot
resize();
loadAssets().then(() => {
  resize();
  layoutRooms([]);
  if (DEBUG && QS.get('cam')) {   // ?debug&cam=x,z,zoom: look at one spot, for close-up screenshots
    const [x, z, zoom] = QS.get('cam').split(',').map(Number);
    cam.z = zoom || 60; cam.c.set(x, 0.7, z); cam.userMoved = true;
  }
  if (DEBUG && QS.has('hoverdoc')) {   // ?debug&hoverdoc=S: after S seconds, hover a 3D document through the raycast path
    setTimeout(() => {
      const all = Object.values(docSlots).flat();
      const slot = all.find(s => /Diff line items/.test(s.doc.name)) || all[0];
      if (!slot) { console.error('hoverdoc: no documents on the deck'); return; }
      _w.setFromMatrixPosition(slot.m); _w.y += 0.01;
      const at = toScreen(_w, { x: 0, y: 0 });
      canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: at.x, clientY: at.y, pointerId: 99, pointerType: 'mouse', bubbles: true }));
    }, Number(QS.get('hoverdoc')) * 1000 || 5000);
  }
  if (DEBUG && QS.has('clickbot')) {   // ?debug&clickbot=S: click the first running android through the raycast path
    setTimeout(() => {
      const e = [...ents.values()].find(x => x.job.status === 'running') || ents.values().next().value;
      if (!e) { console.error('clickbot: no androids on the deck'); return; }
      e.bot.head.getWorldPosition(_w);
      const at = toScreen(_w, { x: 0, y: 0 });
      const opts = { clientX: at.x, clientY: at.y, pointerId: 98, pointerType: 'mouse', bubbles: true };
      canvas.dispatchEvent(new PointerEvent('pointerdown', opts)); canvas.dispatchEvent(new PointerEvent('pointerup', opts));
    }, Number(QS.get('clickbot')) * 1000 || 5000);
  }
  if (DEBUG && QS.has('bots')) {  // ?debug&bots: large portraits of every android variant
    const wrap = document.body.appendChild(Object.assign(document.createElement('div'), { style: 'position:fixed;left:280px;top:70px;z-index:98;display:flex;flex-wrap:wrap;gap:8px;background:#0b111d;padding:8px' }));
    for (const [host, agent, pose] of [['node-a', 'claude'], ['node-b', 'codex'], ['node-c', 'claude'], ['node-d', 'codex'], ['node-a', 'codex', 'off']]) {
      const cv = wrap.appendChild(Object.assign(document.createElement('canvas'), { style: 'width:200px;height:260px;background:#1a2336' }));
      miniBot(cv, hostLook(host), agent, pose || 'normal');
    }
  }
  // read-only probe for browser tests: rooms live only in WebGL, so they have no DOM to query
  window.fleetDeck = Object.freeze({
    rooms: () => rooms.map(r => ({ name: r.name, label: r.label })),
    agents: () => [...ents.values()].map(e => ({ key: e.key, kind: e.kind, room: e.room, status: e.job.status })),
  });
  if (DEMO) {
    const tick = demoSource();
    applyState(tick());
    setInterval(() => applyState(tick()), POLL_MS);
  } else {
    stream();
  }
  setInterval(renderLive, 1000);
  requestAnimationFrame(frame);
}, err => {
  const hint = document.getElementById('hint');
  hint.hidden = false;
  hint.innerHTML = `<h2>The deck couldn’t load its 3D assets</h2><p><code>${esc(err.message || String(err))}</code></p><p>They are served from <code>/vendor/</code> and <code>/assets/</code> by <code>fleet web</code>.</p>`;
  console.error(err);
});
