// Deck entry point: the frame loop and boot.

import { BK, DEBUG, DEMO, POLL_MS, QS, RD, REDUCED, RW, WARP, canvas, vh, vw } from './env.js';
import { esc } from './util.js';
import { hostLook } from './looks.js';
import { isActive } from './activity.js';
import { M, _p, _w, applyCamera, cam, camera, centreFor, loadAssets, renderer, scene, toScreen } from './scene.js';
import { ents } from './model.js';
import { edgeStrips, layoutRooms, roomByName, rooms } from './rooms.js';
import { _la, _lb, dashedLine, docSlots, liftHovered, lineGeo, nSeg, setNSeg, stepDocFx } from './docs3d.js';
import { positionTags } from './agents.js';
import { stepMotion, stepParticles, updateEnt, updateRoom } from './motion.js';
import { applyState, stream } from './state.js';
import { positionSwitches, stepFocus } from './focus.js';
import { positionLanterns, stepLanterns } from './attention.js';
import { resize } from './camera.js';
import { miniBot, panelScrollUntil, renderLive } from './panel.js';
import './library.js';
import { reader } from './reader.js';
import { demoSource } from './demo.js';
import { buildingReady, buildingShown } from './building.js';
import { sankeyPane } from './sankey.js';
import { glowOf, keyOf, pipelines, screenOf, stepScreens } from './pipelines.js';

// ------------------------------------------------------------------ frame loop
let lastT = 0;
function frame(ts) {
  requestAnimationFrame(frame);
  if (document.hidden || !reader.hidden || !sankeyPane.hidden) { lastT = 0; return; }   // a sheet covers the deck; don't render under it
  if (buildingShown) { lastT = 0; return; }                      // the building has the screen and draws itself
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
  stepFocus(rooms, dt);
  for (const e of ents.values()) {
    const r = roomByName.get(e.room);
    if (!r) continue;
    updateEnt(e, r, dt, t, now);
    if (e.walking || !e.target || !isActive(e.job.status) || r.focus === 'background') continue;   // a background room's props rest
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
  renderer.render(scene, camera);
  positionTags();
  positionSwitches();
  positionLanterns();
  if (fpsEl) {
    fpsN++;
    if (t - fpsT > 1) { fpsEl.textContent = `${Math.round(fpsN / (t - fpsT))} fps · ${renderer.info.render.calls} calls`; fpsN = 0; fpsT = t; }
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
  // read-only probe for browser tests: rooms live only in WebGL, so they have no DOM to query
  const onScreen = mesh => {   // a unit plane's bounding box on screen
    const pts = [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]].map(([x, y]) => toScreen(mesh.localToWorld(_w.set(x, y, 0)), { x: 0, y: 0 }));
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
  };
  window.fleetDeck = Object.freeze({
    rooms: () => rooms.map(r => ({ name: r.name, label: r.label, x: r.ox, y: r.oy,
      screen: toScreen(_w.set(r.ox + RW / 2, 0, r.oy + RD / 2), { x: 0, y: 0 }), focus: r.focus, dim: r.dimK ?? null,
      attention: r.attention?.level ? { kind: r.attention.kind, state: r.attention.level, count: r.attention.shown.length } : null })),
    agents: () => [...ents.values()].map(e => ({ key: e.key, kind: e.kind, room: e.room, status: e.job.status })),
    pipelines: () => pipelines.map(p => {
      const s = screenOf(keyOf(p));
      return { key: keyOf(p), room: p.project, run: p.run?.run_id ?? null,
        screen: s ? toScreen(s.mesh.getWorldPosition(_w), { x: 0, y: 0 }) : null,
        rect: s ? onScreen(s.mesh) : null, sign: s ? onScreen(s.room.signMesh) : null, glow: glowOf(keyOf(p)) };
    }),
    lookAt: (key, zoom) => {   // bring a pipeline's screen to the middle of the view (a phone shows one room at a time)
      const s = screenOf(key);
      if (!s) return;
      if (zoom) cam.z = zoom;
      cam.c.copy(centreFor(s.mesh.getWorldPosition(_w), vw / 2, vh / 2, cam.z)); cam.userMoved = true; cam.tween = null;
      applyCamera(); camera.updateMatrixWorld();
    },
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
