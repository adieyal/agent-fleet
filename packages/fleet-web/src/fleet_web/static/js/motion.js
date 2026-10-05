// Where each android goes and how it moves: targets, pacing, particles and the per-frame update.

import * as THREE from 'three';
import { animationNow } from './clock.js';
import { PI, RD, REDUCED, SPEED } from './env.js';
import { angleTo, clamp, mix } from './util.js';
import {
  ACTS, AGENT_COLOR, AISLES, APART_ACROSS, APART_ALONG, CROSSINGS, FACE_VIEWER, FLOOR, OVERFLOW, SPOTS, apart,
  blocked, stationOf,
} from './looks.js';
import { isActive } from './activity.js';
import { heldItem as held, mayChange, reactions, resting, wanted } from './behaviour.js';
import { G, ROBOT, _m4, _m4b, _q, _sc, _v, cam, drawScreen, scene, softDot } from './scene.js';
import { ents, selectedKey } from './model.js';
import { PRESS, placer, roomByName, rooms } from './rooms.js';
import { dropEnt, playClip } from './agents.js';
import { closePanel } from './panel.js';

export function assignTargets() {
  const now = animationNow() / 1000;
  for (const r of rooms) r.ents = [];
  for (const e of ents.values()) {
    // a finished job heads for the door once its completion moment is over, and gives up its spot
    if (e.leaving) { if (e.target !== EXIT) { e.target = EXIT; e.path = route(e.local, EXIT); } continue; }
    const r = roomByName.get(e.room); if (r) r.ents.push(e);
  }
  for (const r of rooms) {
    r.ents.sort((a, b) => a.key < b.key ? -1 : 1);
    for (const e of r.ents) {
      noteEvents(e, now);
      // a change of station waits out a minimum dwell once there, so bursts of events don't send androids back and forth
      const want = wanted(e.job, e.act);
      if (mayChange(want, e, now)) { e.act = want; e.actSince = now; e.anchor = null; e.stage = 0; e.dropped = false; }
    }
    allocate(r);
  }
}
const EXIT = { x: 5.5, y: RD + 0.7, prop: 'door' };   // just outside the door, where androids walk in

// React to what happened since the last poll: a test run followed by anything but an error passed (a nod), an error gets
// a head shake. Seated androids nod or shake just their head; standing ones play the full clip.
function noteEvents(e, now) {
  for (const yes of reactions(e, e.job)) react(e, yes, now);   // (the entity keeps evTs and testing)
}
function react(e, yes, now) {
  if (REDUCED) return;
  if (e.target && e.target.prop === 'terminal') {   // the screen shows the verdict too
    const s = roomByName.get(e.room)?.screens[e.target.propIdx];
    if (s && (e.act === 'test' || !yes)) s.verdict = { ok: yes, until: now + 3 };
  }
  if (e.bot.clip === 'Sitting' && !e.walking) { e.nod = { yes, until: now + 1.5 }; return; }
  const clip = yes ? 'Yes' : 'No';
  e.holdClip = clip; e.holdUntil = now + ROBOT.clips[clip].duration;
}

// Give every android in a room its own spot. Androids keep the spot they already hold while their activity is
// unchanged; the rest take the first free spot at their station, then the nearest free overflow spot.
// Delegates stand beside their partner, on free floor, facing them.
function allocate(r) {
  const taken = [];
  const free = (x, y, self) => taken.every(t => t.e === self || apart(t.x, t.y, x, y));
  const claim = (e, spot) => { taken.push({ x: spot.x, y: spot.y, e }); e.spotProp = spot.prop; setTarget(e, spot); };
  const propOf = e => stationOf(e.act, e.stage);
  const rest = [];
  // 1. keep what's already held
  for (const e of r.ents) {
    const p = propOf(e), t = e.target;
    if (p === 'partner') continue;
    if (t && e.spotProp === p && (p !== 'stay' || e.anchor) && free(t.x, t.y, e)) { claim(e, t); continue; }
    rest.push(e);
  }
  const byDistance = (list, near) => list.sort((a, b) => Math.hypot(a[0] - near.x, a[1] - near.y) - Math.hypot(b[0] - near.x, b[1] - near.y));
  const overflow = (e, near, p) => {
    // the listed free-floor spots first, then any clear floor tile; only a packed room doubles up
    const s = byDistance(OVERFLOW.slice(), near).find(o => free(o[0], o[1], e))
      || byDistance(FLOOR.slice(), near).find(o => free(o[0], o[1], e))
      || OVERFLOW[0];
    return { x: s[0], y: s[1], face: s[2] ?? 0, prop: p };
  };
  // 2. everyone else, station by station
  for (const e of rest) {
    const p = propOf(e);
    let spot;
    if (p === 'stay') {
      // a session waiting on its human: stop where it is if that's clear, keeping the seat it was in
      const here = { x: e.local.x, y: e.local.y };
      spot = !e.fresh && e.target && free(here.x, here.y, e) && (e.target.sit != null || !blocked(here.x, here.y))
        ? { ...here, face: e.facing, sit: e.target.sit, prop: 'stay' }
        : overflow(e, here, 'stay');
      if (e.act === 'await' && spot.sit == null) spot.face = FACE_VIEWER;   // waiting on the human: look out of the screen
      e.anchor = spot;
    } else {
      const s = SPOTS[p].find(c => free(c[0], c[1], e));
      spot = s ? { x: s[0], y: s[1], face: s[2], prop: p, propIdx: s[3] || 0, sit: s[4] } : overflow(e, { x: SPOTS[p][0][0], y: SPOTS[p][0][1] }, p);
    }
    claim(e, spot);
  }
  // 3. delegates, beside whoever is working
  for (const e of r.ents) {
    if (propOf(e) !== 'partner') continue;
    const partner = r.ents.find(o => o !== e && !['partner', 'dock'].includes(propOf(o)) && o.target) || null;
    const same = e.spotProp === 'partner' && e.partner === partner && e.target && free(e.target.x, e.target.y, e);
    e.partner = partner;
    if (same) { claim(e, e.target); continue; }
    const t = partner ? partner.target : { x: 7.95, y: 1.35 };
    let spot = null;
    // beside the partner as seen on screen: offsets along (1,-1) move across the view
    for (const [dx, dy] of [[1.1, -1.1], [-1.1, 1.1], [1.9, 0.3], [0.3, 1.9], [1.4, -0.9], [-0.9, 1.4]]) {
      const x = t.x + dx, y = t.y + dy;
      if (!blocked(x, y) && free(x, y, e)) { spot = { x, y, face: Math.atan2(t.x - x, t.y - y), prop: 'partner' }; break; }
    }
    claim(e, spot || overflow(e, t, 'partner'));
  }
}

function setTarget(e, spot) {
  if (e.target && Math.abs(e.target.x - spot.x) < 0.01 && Math.abs(e.target.y - spot.y) < 0.01) { e.target = spot; return; }
  e.target = spot;
  e.arrivedAt = null; e.slow = false; e.away = false; e.paceAt = null;
  if (REDUCED || e.placeNow) { e.placeNow = false; e.local = { x: spot.x, y: spot.y }; e.path = []; e.facing = spot.face ?? e.facing; e.fresh = false; return; }
  e.path = route(e.local, spot);
}

// thinking androids pace and idle ones wander: a slow stroll a little way from their spot, a pause, and back
const PACE_SPEED = 0.4;   // of walking speed
function pace(e, now) {
  const reach = (ACTS[e.act] || {}).pace;
  if (!reach || REDUCED || !e.target || e.arrivedAt == null) return;
  if (e.paceAt == null) { e.paceAt = now + 2 + Math.random() * 2; return; }
  if (now < e.paceAt) return;
  let to = null;
  if (e.away) to = { x: e.target.x, y: e.target.y };
  else for (let k = 0; k < 8 && !to; k++) {
    const a = Math.random() * PI * 2, x = e.target.x + Math.sin(a) * reach, y = e.target.y + Math.cos(a) * reach;
    if (!blocked(x, y) && !blocked((x + e.target.x) / 2, (y + e.target.y) / 2)) to = { x, y };
  }
  if (!to) { e.paceAt = now + 3; return; }
  e.away = !e.away; e.slow = true; e.path = [to];
  e.paceAt = now + reach / (SPEED * PACE_SPEED) + 2.5 + Math.random() * 3;
}

// walk along the aisles so androids go around desks and sofas rather than through them
function route(a, b) {
  const aisle = y => y < 4.1 ? AISLES[0] : y < 7.1 ? AISLES[1] : AISLES[2];   // the bench desks start at 4.2
  const ya = aisle(a.y), yb = aisle(b.y);
  const pts = [{ x: a.x, y: ya }];
  if (ya !== yb) {
    const cx = CROSSINGS.reduce((best, c) => Math.abs(a.x - c) + Math.abs(b.x - c) < Math.abs(a.x - best) + Math.abs(b.x - best) ? c : best);
    pts.push({ x: cx, y: ya }, { x: cx, y: yb });
  }
  pts.push({ x: b.x, y: yb }, { x: b.x, y: b.y });
  const out = []; let prev = a;
  for (const p of pts) { if (Math.hypot(p.x - prev.x, p.y - prev.y) > 0.02) { out.push(p); prev = p; } }
  return out;
}

export function stepMotion(dt, now) {
  for (const e of ents.values()) {
    if (!e.path.length) {
      if (e.leaving) { leave(e); continue; }
      if (e.target) e.facing = e.target.face ?? e.facing;
      e.walking = false;
      if (e.target) { e.fresh = false; if (e.arrivedAt == null) e.arrivedAt = now; }
      nextStage(e, now);
      pace(e, now);
      continue;
    }
    if (e.leaving && now > e.leaveBy) { leave(e); continue; }   // a slow frame rate never keeps it on the deck
    if (now < e.holdUntil) continue;
    // get up before walking off
    if (!REDUCED && e.bot.clip === 'Sitting') {
      e.holdClip = 'Standing'; e.holdUntil = now + ROBOT.clips.Standing.duration * 0.8;
      continue;
    }
    let remaining = SPEED * dt * (e.slow ? PACE_SPEED : 1);
    while (remaining > 0 && e.path.length) {
      const p = e.path[0], dx = p.x - e.local.x, dy = p.y - e.local.y, d = Math.hypot(dx, dy);
      if (d > 0.001) e.facing = Math.atan2(dx, dy);
      if (d <= remaining) { e.local.x = p.x; e.local.y = p.y; e.path.shift(); remaining -= d; }
      else { e.local.x += dx / d * remaining; e.local.y += dy / d * remaining; remaining = 0; }
    }
    e.walking = e.path.length > 0;
  }
  // walkers step around other androids: when two would overlap on screen, the walker is nudged sideways across the
  // view (along (1,-1)), easing back once clear
  const k = Math.min(1, dt * 4);
  for (const e of ents.values()) {
    let nx = 0, ny = 0;
    if (e.walking) for (const o of ents.values()) {
      if (o === e || o.room !== e.room) continue;
      const ax = e.local.x + e.nudge.x, ay = e.local.y + e.nudge.y, bx = o.local.x + o.nudge.x, by = o.local.y + o.nudge.y;
      const u = ((ax - ay) - (bx - by)) / Math.SQRT2, v = ((ax + ay) - (bx + by)) / Math.SQRT2;
      const q = (u / APART_ACROSS) ** 2 + (v / APART_ALONG) ** 2;
      if (q < 1) { const push = (1 - q) * 0.9 * (u >= 0 ? 1 : -1) / Math.SQRT2; nx += push; ny -= push; }
    }
    e.nudge.x += (clamp(nx, -0.8, 0.8) - e.nudge.x) * k;
    e.nudge.y += (clamp(ny, -0.8, 0.8) - e.nudge.y) * k;
  }
}

function leave(e) {
  dropEnt(e);
  ents.delete(e.key);
  if (selectedKey === e.key) closePanel();
}

// activities with more than one station move on once the android has spent a moment at the first (a book off the shelf)
function nextStage(e, now) {
  const s = (ACTS[e.act] || {}).station;
  if (!Array.isArray(s) || e.stage >= s.length - 1 || e.arrivedAt == null || now - e.arrivedAt < 1.8 || !isActive(e.job.status)) return;
  e.stage++;
  const r = roomByName.get(e.room);
  if (r) allocate(r);
}

function clipFor(e, now) {
  if (now < e.holdUntil && e.holdClip) return e.holdClip;
  if (e.walking) return 'Walking';
  if (!e.target) return 'Idle';
  const st = e.job.status;
  if (st === 'idle') return 'Sitting';   // an idle session sits and rests
  if (isActive(st) && e.act === 'delegate') return 'Wave';
  return e.target.sit != null ? 'Sitting' : 'Idle';
}

// ------------------------------------------------------------------ particles: motes over finished androids, steam from the kitchen
const PN = 320;
const part = {
  pos: new Float32Array(PN * 3), col: new Float32Array(PN * 4), vel: new Float32Array(PN * 3),
  age: new Float32Array(PN).fill(1), life: new Float32Array(PN).fill(1), kind: new Uint8Array(PN), next: 0,
};
const partGeo = new THREE.BufferGeometry();
partGeo.setAttribute('position', new THREE.BufferAttribute(part.pos, 3).setUsage(THREE.DynamicDrawUsage));
partGeo.setAttribute('color', new THREE.BufferAttribute(part.col, 4).setUsage(THREE.DynamicDrawUsage));
const partMat = new THREE.PointsMaterial({ size: 10, map: softDot('rgba(255,255,255,1)', 'rgba(255,255,255,0)'), vertexColors: true, transparent: true, depthWrite: false });
const points = new THREE.Points(partGeo, partMat);
points.frustumCulled = false;
scene.add(points);
function spawn(x, y, z, kind) {
  if (REDUCED) return;
  const i = part.next; part.next = (part.next + 1) % PN;
  part.pos[i * 3] = x; part.pos[i * 3 + 1] = y; part.pos[i * 3 + 2] = z;
  const steam = kind === 2;   // 1 motes, 2 steam
  part.vel[i * 3] = (Math.random() - 0.5) * 0.1;
  part.vel[i * 3 + 1] = steam ? 0.3 + Math.random() * 0.15 : 0.35 + Math.random() * 0.2;
  part.vel[i * 3 + 2] = (Math.random() - 0.5) * 0.1;
  part.age[i] = 0; part.life[i] = steam ? 1.4 + Math.random() * 0.5 : 1.3; part.kind[i] = kind;
}
export function stepParticles(dt) {
  for (let i = 0; i < PN; i++) {
    const c = i * 4;
    if (part.age[i] >= part.life[i]) { part.col[c + 3] = 0; continue; }
    part.age[i] += dt;
    const k = 1 - Math.min(1, part.age[i] / part.life[i]);
    part.pos[i * 3] += part.vel[i * 3] * dt; part.pos[i * 3 + 1] += part.vel[i * 3 + 1] * dt; part.pos[i * 3 + 2] += part.vel[i * 3 + 2] * dt;
    if (part.kind[i] === 2) { part.col[c] = 0.9; part.col[c + 1] = 0.93; part.col[c + 2] = 0.97; part.col[c + 3] = 0.3 * k; }
    else { part.col[c] = 0.29; part.col[c + 1] = 0.87; part.col[c + 2] = 0.5; part.col[c + 3] = 0.9 * k; }
  }
  partGeo.attributes.position.needsUpdate = true;
  partGeo.attributes.color.needsUpdate = true;
  partMat.size = 0.45 * cam.z;
}

// ------------------------------------------------------------------ per-frame update of androids and props

function tone(e, t) {
  // finished androids rest with their face light low
  const key = resting(e.job.status, !e.walking && !!e.target) ? 'rest' : '';
  if (key === e.tone) return;
  e.tone = key;
  const lit = AGENT_COLOR[e.job.agent] || '#cbd5e1', low = mix(lit, '#1b2333', 0.35);
  e.bot.face.color.set(key ? low : lit);
  if (e.bot.eyes) { e.bot.eyes.color.set(key ? low : lit); e.bot.eyes.emissiveIntensity = key ? 0.2 : 1.4; }
}
// things androids carry: a parcel for the outbox, a book from the shelf, a printout to read (one small mesh, made on first use)
// [x, y, z, tilt] in the android's frame, standing and seated
const HELD = {
  box:   { mat: new THREE.MeshStandardMaterial({ color: '#b98b52', roughness: 0.9 }), size: [0.5, 0.38, 0.4], stand: [0, 1.05, 0.42, 0], sit: [0, 0.95, 0.45, 0] },
  book:  { mat: new THREE.MeshStandardMaterial({ color: '#b4463c', roughness: 0.7 }), size: [0.36, 0.08, 0.46], stand: [0, 1.3, 0.4, -1.0], sit: [0, 1.2, 0.5, -1.1] },
  sheet: { mat: new THREE.MeshBasicMaterial({ color: '#f4f6fa' }), size: [0.4, 0.012, 0.52], stand: [0, 1.3, 0.4, -1.1], sit: [0, 1.02, 0.45, -0.55] },
};
const heldItem = (e, now) => held(e.job.status, e, now);
function hold(e, item, seated) {
  if (!item) { if (e.held) e.held.visible = false; return; }
  if (!e.held) { e.held = new THREE.Mesh(G.box); e.bot.root.add(e.held); }
  const H = HELD[item], [x, y, z, tilt] = seated ? H.sit : H.stand;
  e.held.material = H.mat; e.held.visible = true;
  e.held.scale.set(...H.size); e.held.position.set(x, y, z); e.held.rotation.set(tilt, 0, 0);
}

export function updateEnt(e, r, dt, t, now) {
  const bot = e.bot, root = bot.root;
  playClip(e, clipFor(e, now));
  if (bot.clip === 'Walking') bot.actions.Walking.timeScale = e.slow ? 0.6 : 1.25;   // pacing is a slow amble
  bot.mixer.update(REDUCED ? 0 : dt);
  const st = e.job.status, arrived = !e.walking && !!e.target;
  // on top of the clip: typing forearms, a nod or head shake, eyes down on a printout
  const A = ACTS[e.act] || {}, seated = arrived && bot.clip === 'Sitting' && isActive(st);
  if (!REDUCED && seated && A.hands) bot.arms.forEach((arm, i) => { arm.rotation.x += Math.sin(t * 15 + i * 2.1) * 0.14; });
  if (e.nod && now < e.nod.until) {
    const k = Math.sin((e.nod.until - now) * 13) * 0.32;
    if (e.nod.yes) bot.head.rotation.x += k; else bot.head.rotation.y += k * 1.3;
  }
  const item = heldItem(e, now);
  if (item === 'sheet' && seated) bot.head.rotation.x += 0.3;
  hold(e, item, seated);
  // a parcel goes into the outbox a moment after the android gets there
  if (e.act === 'ship' && !e.dropped && arrived && e.target.prop === 'mail' && e.arrivedAt != null && now - e.arrivedAt > 0.6) { e.dropped = true; r.mailAt = now; }
  const lift = arrived && e.target.sit != null && bot.clip === 'Sitting' ? e.target.sit : 0;
  e.lift += (lift - e.lift) * Math.min(1, dt * 6);
  root.position.set(r.ox + e.local.x + e.nudge.x, e.lift, r.oy + e.local.y + e.nudge.y);
  let face = e.facing;
  if (e.produceUntil && t >= e.produceFrom && t < e.produceUntil) face = Math.atan2(PRESS.x - e.local.x, PRESS.fab - e.local.y);   // turned to the press
  root.rotation.y += REDUCED ? angleTo(root.rotation.y, face) : angleTo(root.rotation.y, face) * Math.min(1, dt * 10);
  tone(e, t);
  e.ring.visible = e.key === selectedKey;
  if (e.ring.visible) e.ring.material.opacity = REDUCED ? 0.8 : 0.55 + Math.sin(t * 4) * 0.3;
  if (e.halo) e.halo.material.opacity = st === 'working' && !REDUCED ? 0.4 + Math.sin(t * 3) * 0.25 : 0.3;
  e.glow.visible = st === 'done' && arrived;
  if (e.glow.visible && Math.random() < 0.03) spawn(root.position.x + (Math.random() - 0.5) * 0.8, 0.2, root.position.z + (Math.random() - 0.5) * 0.5, 1);
}

const WAVE_ON = new THREE.Color('#38bdf8'), WAVE = new THREE.Color();
// busy props: terminal screens scroll, the comms dish sends out waves, a marker light sketches on the whiteboard
export function updateRoom(r, t, dt, now) {
  for (let i = 0; i < r.screens.length; i++) {
    const s = r.screens[i], busy = !!(r.busyTerm & (1 << i)), test = !!(r.testTerm & (1 << i));
    const verdict = s.verdict && now < s.verdict.until ? s.verdict.ok : null;
    if (busy !== s.busy || test !== s.test || verdict !== s.shown || t >= s.next) {
      s.busy = busy; s.test = test; s.shown = verdict; s.next = t + (busy ? 0.12 : 0.5); drawScreen(s, t);
    }
  }
  // cabinet drawers slide out while searched
  for (let i = 0; i < r.drawers.length; i++) {
    const h = r.drawers[i], want = r.busyCab & (1 << i) ? 1 : 0;
    const open = h.open ?? 0, next = REDUCED ? want : open + (want - open) * Math.min(1, dt * 5);
    if (Math.abs(next - open) < 0.002 && h.open != null) continue;
    h.open = next;
    _m4.copy(h.base); _m4.elements[12] += next * 0.5;   // along +x, out of the cabinet
    placer.setMatrix(h, _m4);
  }
  // build rack: the progress meter fills bar by bar while something builds, the status light blinks amber
  if (r.busyRack || r.wasRack) {
    const lit = r.busyRack ? (REDUCED ? 3 : Math.floor(t * 2.2) % (r.rackBars.length + 2)) : 0;
    r.rackBars.forEach((h, k) => placer.setColor(h, k < lit ? (k >= 4 ? '#facc15' : '#4ade80') : '#1e293b'));
    placer.setColor(r.rackLed, r.busyRack && (REDUCED || Math.sin(t * 7) > 0) ? '#f59e0b' : '#334155');
  }
  r.wasRack = r.busyRack;
  // coffee machine steams while someone waits for it
  if (r.busyKitchen && Math.random() < dt * 9) spawn(r.steamAt.x + (Math.random() - 0.5) * 0.12, r.steamAt.y, r.steamAt.z + (Math.random() - 0.5) * 0.12, 2);
  // outbox slot flashes as a parcel goes in
  const mail = r.mailAt != null && now - r.mailAt < 0.9 ? 1 - (now - r.mailAt) / 0.9 : 0;
  if (mail || r.wasMail) placer.setColor(r.mailSlot, mix(mix(r.look.accent, '#000000', 0.5), '#fff7d6', mail));
  r.wasMail = mail > 0;
  // the press's slit glows in the writer's colour while a document is being written
  if (r.busyPress || r.wasPress) placer.setColor(r.slit, r.busyPress && (REDUCED || Math.sin(t * 5) > -0.4) ? mix(r.busyPress, '#04070d', 0.35) : '#04070d');
  r.wasPress = !!r.busyPress;
  const web = r.busyWeb;
  if (web !== r.wasWeb || web) {
    placer.setColor(r.tip, web && (REDUCED || Math.sin(t * 8) > -0.2) ? '#38bdf8' : '#64748b');
    for (let k = 0; k < r.pulses.length; k++) {
      const h = r.pulses[k], u = REDUCED ? 0.5 : (t * 0.9 + k * 0.5) % 1, s = web ? 0.15 + u * 0.55 : 0;
      _m4.copy(h.base); _m4b.makeScale(s || 1e-4, s || 1e-4, s || 1e-4); _m4.multiply(_m4b);
      placer.setMatrix(h, _m4);
      placer.setColor(h, WAVE.copy(WAVE_ON).multiplyScalar(web ? 1 - u : 0));
    }
  }
  if (r.busyPlan !== r.wasPlan || r.busyPlan) {
    const u = REDUCED ? 0.3 : t * 0.7;
    const s = r.busyPlan ? 0.045 : 1e-4;
    _m4.copy(r.pen.base); _m4b.compose(_v.set(Math.sin(u * 2.1) * 0.8, Math.sin(u * 3.3) * 0.3, 0.02), _q.identity(), _sc.set(s, s, s)); _m4.multiply(_m4b);
    placer.setMatrix(r.pen, _m4);
  }
  r.wasWeb = web; r.wasPlan = r.busyPlan;
}
