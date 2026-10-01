// Deck entry point: the frame loop and boot.

import './keyboard-help.js';

import { BK, DEBUG, DEMO, POLL_MS, QS, RD, REDUCED, RW, WARP, canvas, vh, vw } from './env.js';
import { esc } from './util.js';
import { advanceClock, animationNow, isStepping } from './clock.js';
import { hostLook } from './looks.js';
import { isActive } from './activity.js';
import { M, _p, _w, applyCamera, cam, camera, centreFor, loadAssets, renderer, scene, toScreen } from './scene.js';
import { ents, fanned } from './model.js';
import { edgeStrips, layoutRooms, roomByName, rooms } from './rooms.js';
import { _la, _lb, dashedLine, docSlots, liftHovered, lineGeo, nSeg, setNSeg, stepDocFx } from './docs3d.js';
import { crowds, positionTags } from './agents.js';
import { stepMotion, stepParticles, updateEnt, updateRoom } from './motion.js';
import { applyState, departIdle, stream } from './state.js';
import { positionSwitches, stepFocus } from './focus.js';
import { positionLanterns, stepLanterns } from './attention.js';
import { lanternState } from './building.js';
import { fit, resize, setRenderScale } from './camera.js';
import { miniBot, panelScrollUntil, renderLive, select } from './panel.js';
import './library.js';
import './running.js';
import { lowerQuality, quality } from './quality.js';
import { reader } from './reader.js';
import { demoSource } from './demo.js';
import { buildingReady, buildingShown, stepBuilding } from './building.js';
import { sankeyPane, stepSankey } from './sankey.js';
import { glowOf, keyOf, pipelines, screenOf, stepScreens } from './pipelines.js';
import { enterFloor } from './bench.js';
import { textBudget } from './text-budget.js';
import { worldShown } from './world/floor-view.js';

// ------------------------------------------------------------------ frame loop
let lastT = 0;
let frameNo = 0;
const SHADOW_EVERY = 3;   // frames per shadow-map refresh at low quality (scene.js turns the automatic one off); every frame at high

// Adaptive resolution: over each window of frames, many late ones step the deck's rendering resolution down (on auto
// quality, the first such window drops to low quality instead), and two windows in a row with almost none step it back
// up. A GPU that keeps up never leaves full resolution.
const RENDER_SCALES = [1, 0.85, 0.7], PACE_WINDOW = 120;
let scaleStep = 0, paced = 0, late = 0, goodWindows = 0;
function pace(interval) {
  paced++;
  if (interval > 1.5 / 60) late++;
  if (paced < PACE_WINDOW) return;
  const share = late / paced;
  paced = late = 0;
  if (share > 0.2 && lowerQuality()) { goodWindows = 0; return; }
  if (share > 0.2 && scaleStep < RENDER_SCALES.length - 1) { scaleStep++; goodWindows = 0; }
  else if (share < 0.03 && scaleStep > 0 && ++goodWindows >= 2) { scaleStep--; goodWindows = 0; }
  else return;
  setRenderScale(RENDER_SCALES[scaleStep]);
}
let needsFrame = true;
for (const event of ['pointermove', 'pointerup', 'click', 'keydown', 'wheel', 'resize']) {
  window.addEventListener(event, () => { needsFrame = true; });
}
let demoTick = null, demoElapsed = 0;
function advanceTime(seconds, draw = true) {
  if (!Number.isFinite(seconds) || seconds < 0) throw new Error('Expected non-negative seconds');
  advanceClock(0);
  for (let remaining = seconds; remaining > 0;) {
    const dt = Math.min(0.1, remaining);
    advanceClock(dt);
    demoElapsed += dt;
    if (demoTick && demoElapsed >= POLL_MS / 1000) {
      demoElapsed -= POLL_MS / 1000;
      applyState(demoTick());
    }
    const now = animationNow() / 1000;
    updateFrame(dt * WARP, now, now, false);
    remaining -= dt;
  }
  departIdle();
  const now = animationNow() / 1000;
  updateFrame(0, now, now, draw);
  stepSankey();
  if (draw) stepBuilding();
}
function frame(ts) {
  requestAnimationFrame(frame);
  if (document.hidden || !reader.hidden || !sankeyPane.hidden   // a sheet covers the deck; don't render under it
    || buildingShown                                             // the building has the screen and draws itself
    || worldShown                                                // so has the sprite-world floor
    || ts < panelScrollUntil) {                                  // hold the deck still while the panel scrolls, so the scroll gets the frame
    lastT = 0;
    if (fpsEl && fpsT) { fpsEl.textContent = 'deck paused'; fpsN = 0; fpsT = 0; }   // not a stale reading from the last deck frame
    return;
  }
  if (isStepping()) {
    const now = animationNow() / 1000;
    renderer.shadowMap.needsUpdate = true;   // stepped frames stay exact
    if (needsFrame) updateFrame(0, now, now, true);
    needsFrame = false;
    return;
  }
  const t = ts / 1000, now = animationNow() / 1000;
  if (lastT) pace(t - lastT);
  const dt = Math.min(0.1, lastT ? t - lastT : 0.016) * WARP;
  lastT = t;
  if (quality === 'high' || ++frameNo % SHADOW_EVERY === 0) renderer.shadowMap.needsUpdate = true;
  updateFrame(dt, t, now, true);
}
function updateFrame(dt, t, now, draw) {
  stepMotion(dt, now);
  if (cam.tween) {
    const k = REDUCED ? 1 : Math.min(1, dt * 7);
    cam.c.lerp(cam.tween, k);
    if (cam.c.distanceTo(cam.tween) < 0.01) cam.tween = null;
  }
  applyCamera();
  setNSeg(0);
  for (const r of rooms) { r.busyTerm = 0; r.testTerm = 0; r.busyWeb = false; r.busyPlan = false; r.busyCab = 0; r.busyRack = false; r.busyKitchen = false; r.busyPress = null; }
  stepFocus(rooms, dt);
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
  stepLanterns(rooms, now);
  stepDocFx(t);
  stepScreens(t);
  liftHovered();
  lineGeo.setDrawRange(0, nSeg * 2);
  lineGeo.attributes.position.needsUpdate = true;
  lineGeo.attributes.color.needsUpdate = true;
  if (!REDUCED) for (const tex of edgeStrips) tex.offset.x = -t * 0.35;
  M.beacon.color.set(REDUCED || Math.sin(t * 2.4) > 0.6 ? 0xf87171 : 0x5a1d1d);
  stepParticles(dt);
  if (draw) renderer.render(scene, camera);
  else scene.updateMatrixWorld();
  positionTags();
  positionSwitches();
  positionLanterns();
  if (fpsEl) {
    if (!fpsT) fpsT = t;
    fpsN++;
    if (t - fpsT > 1) { fpsEl.textContent = `${Math.round(fpsN / (t - fpsT))} fps · ${renderer.info.render.calls} calls · ${RENDER_SCALES[scaleStep]}x`; fpsN = 0; fpsT = t; }
  }
}
const fpsEl = DEBUG ? document.body.appendChild(Object.assign(document.createElement('div'), { style: 'position:fixed;right:70px;bottom:14px;z-index:99;font:12px monospace;color:#9fe' })) : null;
let fpsN = 0, fpsT = 0;

// ------------------------------------------------------------------ boot
resize();
loadAssets().then(() => {
  resize();
  layoutRooms([]);
  buildingReady();
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
  // probe for browser tests: rooms live only in WebGL, so they have no DOM to query
  const onScreen = mesh => {   // a unit plane's bounding box on screen
    const pts = [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]].map(([x, y]) => toScreen(mesh.localToWorld(_w.set(x, y, 0)), { x: 0, y: 0 }));
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
  };
  const quadOf = mesh => [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]]   // a unit plane's (or box face's) corners on screen
    .map(([x, y]) => toScreen(mesh.localToWorld(_w.set(x, y, 0)), { x: 0, y: 0 }));
  const wallQuad = (r, t) => [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => toScreen(t.wall === 'back'
    ? _w.set(r.ox + t.along + a * t.w / 2, t.up + b * t.h / 2, r.oy + t.out)
    : _w.set(r.ox + t.out, t.up + b * t.h / 2, r.oy + t.along - a * t.w / 2), { x: 0, y: 0 }));
  window.fleetDeck = Object.freeze({
    enterFloor, textBudget,
    select,        // open an agent's panel by key, as clicking it would
    advanceTime,   // seconds; switches to a manual animation clock until reload
    lanterns: lanternState,
    rooms: () => rooms.map(r => ({ name: r.name, label: r.label, x: r.ox, y: r.oy,
      screen: toScreen(_w.set(r.ox + RW / 2, 0, r.oy + RD / 2), { x: 0, y: 0 }), focus: r.focus, dim: r.dimK ?? null, lit: r.lit,
      attention: r.attention?.level ? { kind: r.attention.kind, state: r.attention.level, count: r.attention.shown.length } : null,
      library: r.libraryKey ?? null,
      shelves: r.shelves.map(m => toScreen(m.localToWorld(_w.set(0, 0.2, 0)), { x: 0, y: 0 })) })),   // (high on each bookcase, above anyone at it)
    agents: () => [...ents.values()].map(e => ({ key: e.key, kind: e.kind, room: e.room, status: e.job.status, leaving: !!e.leaving, clip: e.bot.clip,
      station: e.spotProp ?? null, gathered: !!e.crowd })),
    crowds: () => [...crowds.values()].map(c => ({ room: c.room, station: c.station, count: c.members.length, fanned: fanned === c.key })),
    apply: doc => applyState(doc),   // feed a state document as the stream would
    pipelines: () => pipelines.map(p => {
      const s = screenOf(keyOf(p));
      return { key: keyOf(p), room: p.project, run: p.run?.run_id ?? null,
        screen: s ? toScreen(s.mesh.getWorldPosition(_w), { x: 0, y: 0 }) : null,
        rect: s ? onScreen(s.mesh) : null, sign: s ? onScreen(s.room.signMesh) : null, glow: glowOf(keyOf(p)),
        quad: s ? quadOf(s.frame) : null, wall: s ? s.room.onWalls.map(t => ({ kind: t.kind, wall: t.wall, quad: wallQuad(s.room, t) })) : [] };
    }),
    lookAt: (key, zoom) => {   // bring a pipeline's screen to the middle of the view (a phone shows one room at a time)
      const s = screenOf(key);
      if (!s) return;
      if (zoom) cam.z = zoom;
      cam.c.copy(centreFor(s.mesh.getWorldPosition(_w), vw / 2, vh / 2, cam.z)); cam.userMoved = true; cam.tween = null;
      applyCamera(); camera.updateMatrixWorld();
      needsFrame = true;
    },
    lookAtRoom: (name, zoom) => {   // bring a room's middle to the middle of the view; no zoom fits the whole deck again
      const r = rooms.find(r => r.name === name);
      if (!zoom) fit();
      else if (r) { cam.z = zoom; cam.c.copy(centreFor(_w.set(r.ox + RW / 2, 0, r.oy + RD / 2), vw / 2, vh / 2, zoom)); cam.userMoved = true; cam.tween = null; }
      applyCamera(); camera.updateMatrixWorld();
      needsFrame = true;
    },
  });
  if (DEMO) {
    const tick = demoSource();
    demoTick = tick;
    applyState(tick());
    setInterval(() => { if (!isStepping()) applyState(tick()); }, POLL_MS);
  } else {
    stream();
  }
  setInterval(renderLive, 1000);
  setInterval(() => { if (!isStepping()) departIdle(); }, 1000);
  requestAnimationFrame(frame);
}, err => {
  const hint = document.getElementById('hint');
  hint.hidden = false;
  hint.innerHTML = `<h2>The deck couldn’t load its 3D assets</h2><p><code>${esc(err.message || String(err))}</code></p><p>They are served from <code>/vendor/</code> and <code>/assets/</code> by <code>fleet web</code>.</p>`;
  console.error(err);
});
