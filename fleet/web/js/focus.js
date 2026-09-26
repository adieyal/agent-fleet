// Focus: the user's choice of priority or background for a registered project, shown by how open its room is.
// A priority room stays open, with a pennant at its front corner. A background room is closed by a windowed front and
// a roof, with warm light in the windows while anyone works inside. Only the room's switch changes focus; activity
// never does, and nothing moves or resizes. Rooms whose work isn't one registered project have no focus: they stay
// as they always were, and their switch says why it can't be used.

import * as THREE from 'three';
import { BOT_H, RD, REDUCED, RW, WALL_H } from './env.js';
import { mix } from './util.js';
import { G, deckGroup, disposables, toScreen } from './scene.js';
import { isActive } from './activity.js';

export const SHELL_H = BOT_H + 0.3;    // a closed room's roof clears a standing android
const SLIDE_S = 0.5;                   // the front slides in or out in about half a second
const DROP = 1.6;                      // how far above the room the front starts its slide
const LIT = '#ffcf7a', UNLIT = '#34435e';
const floorUi = document.getElementById('floorUi');

// ------------------------------------------------------------------ the windowed front, roof and pennant (built with the room)
export function buildFront(r) {
  const { ox, oy, look } = r;
  const wallMat = new THREE.MeshStandardMaterial({ color: mix(look.wall, '#0b111d', 0.25), roughness: 0.85 });
  const roofMat = new THREE.MeshStandardMaterial({ color: mix(look.rim, '#0b111d', 0.4), roughness: 0.7 });
  const windowMat = new THREE.MeshBasicMaterial({ color: UNLIT, toneMapped: false });
  disposables.push(wallMat, roofMat, windowMat);
  const shell = new THREE.Group();
  const box = (mat, x, y, z, sx, sy, sz) => {
    const m = new THREE.Mesh(G.box, mat);
    m.position.set(ox + x, y, oy + z); m.scale.set(sx, sy, sz); m.castShadow = true; m.receiveShadow = true;
    shell.add(m);
    return m;
  };
  box(wallMat, RW / 2, SHELL_H / 2, RD, RW + 0.12, SHELL_H, 0.12);          // front
  box(wallMat, RW, SHELL_H / 2, RD / 2, 0.12, SHELL_H, RD);                 // right-hand side
  box(roofMat, RW / 2, SHELL_H + 0.05, RD / 2, RW + 0.24, 0.1, RD + 0.24);  // roof
  for (let i = 0; i < 4; i++) box(windowMat, 1.5 + i * 3, SHELL_H * 0.55, RD + 0.07, 1.7, 0.8, 0.02).castShadow = false;
  for (let i = 0; i < 3; i++) box(windowMat, RW + 0.07, SHELL_H * 0.55, 1.7 + i * 3.3, 0.02, 0.8, 1.7).castShadow = false;
  shell.visible = false;
  deckGroup.add(shell);

  // pennant on a short pole at the front-left corner: the backup cue for priority
  const pennant = new THREE.Group();
  const poleMat = new THREE.MeshStandardMaterial({ color: '#8b98ad', roughness: 0.5, metalness: 0.5 });
  const flagGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, -0.36, 0), new THREE.Vector3(0.62, -0.18, 0)]);
  flagGeo.setIndex([0, 1, 2]);
  const flagMat = new THREE.MeshBasicMaterial({ color: look.accent, side: THREE.DoubleSide, toneMapped: false });
  disposables.push(poleMat, flagGeo, flagMat);
  const pole = new THREE.Mesh(G.box, poleMat);
  pole.scale.set(0.05, 1.6, 0.05); pole.position.y = 0.8;
  const flag = new THREE.Mesh(flagGeo, flagMat);
  flag.position.set(0, 1.58, 0); flag.rotation.y = Math.PI / 4;
  pennant.add(pole, flag);
  pennant.position.set(ox + 0.25, 0, oy + RD + 0.25);
  pennant.visible = false;
  deckGroup.add(pennant);

  r.front = { shell, pennant, mats: [wallMat, roofMat, windowMat], windowMat, lit: null };
}

// ------------------------------------------------------------------ focus from the state document
// Changes made here wait for the server to confirm them, so a state document sent before the change landed
// doesn't flick the switch back.
const pending = new Map();   // project id → focus asked for
let current = [];            // rooms as of the last state document

export function applyFocus(doc, rooms) {
  current = rooms;
  const byLabel = new Map();   // room name → project ids of its jobs and sessions (null: unlinked)
  const active = new Set();
  for (const h of doc.hosts || []) for (const item of [...(h.jobs || []), ...(h.sessions || [])]) {
    if (!item.project) continue;
    if (!byLabel.has(item.project)) byLabel.set(item.project, new Set());
    byLabel.get(item.project).add(item.project_id || null);
    if (isActive(item.status)) active.add(item.project);
  }
  const byId = new Map((doc.projects || []).map(p => [p.id, p]));
  for (const [id, focus] of pending) if (byId.get(id)?.focus === focus) pending.delete(id);
  const seen = new Set();
  for (const r of rooms) {
    const ids = byLabel.get(r.name) || new Set(), [only] = ids;
    r.project = ids.size === 1 && only ? byId.get(only) || null : null;
    r.mixed = ids.size > 1;
    r.focus = r.project ? pending.get(r.project.id) || r.project.focus : null;
    r.active = active.has(r.name);
    if (r.shellK == null) r.shellK = r.focus === 'background' ? 1 : 0;   // a new room starts in place; only a switch slides
    renderSwitch(r);
    seen.add(r.name);
  }
  for (const [name, el] of switches) if (!seen.has(name)) { el.remove(); switches.delete(name); }
}

// a relayout (a resize into or out of the phone column) rebuilds rooms between state documents: keep their focus
export function carryFocus(before, rooms) {
  const old = new Map(before.map(r => [r.name, r]));
  for (const r of rooms) {
    const o = old.get(r.name);
    if (!o || o.shellK == null) continue;
    Object.assign(r, { project: o.project, mixed: o.mixed, focus: o.focus, active: o.active, shellK: o.shellK });
    renderSwitch(r);
  }
  current = rooms;
}

// ------------------------------------------------------------------ the switch: Open · Windows, on the room's front corner
const switches = new Map();   // room name → element
function renderSwitch(r) {
  let el = switches.get(r.name);
  if (!el) {
    el = document.createElement('div');
    el.className = 'focus-switch';
    el.setAttribute('role', 'group');
    el.innerHTML = '<button data-set="priority">Open</button><button data-set="background">Windows</button>';
    el.addEventListener('click', ev => {
      const b = ev.target.closest('button');
      if (b && !b.disabled) setFocus(el.room, b.dataset.set);
    });
    floorUi.appendChild(el);
    switches.set(r.name, el);
  }
  el.room = r;
  el.dataset.room = r.name;
  el.dataset.focus = r.focus || 'none';
  for (const b of el.children) { b.disabled = !r.project; b.setAttribute('aria-pressed', String(b.dataset.set === r.focus)); }
  const name = r.project ? r.project.name : r.label;
  el.setAttribute('aria-label', `Focus for ${name}`);
  el.title = el.dataset.error ? `Couldn’t change focus: ${el.dataset.error}`
    : r.project ? `${name} is in ${r.focus === 'priority' ? 'priority: open' : 'the background: windows'}`
    : r.mixed ? 'Work from more than one project shares this room, so it has no single focus'
    : 'Not a registered project, so it has no focus. Register it with: fleet project add <name> --link host:label';
}

async function setFocus(r, focus) {
  if (!r.project || r.focus === focus) return;
  const id = r.project.id, el = switches.get(r.name);
  pending.set(id, focus);
  delete el.dataset.error;
  for (const room of current) if (room.project?.id === id) { room.focus = focus; renderSwitch(room); }
  try {
    const res = await fetch('/api/focus', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project: id, focus }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  } catch (err) {
    pending.delete(id);
    el.dataset.error = err.message;
    for (const room of current) if (room.project?.id === id) { room.focus = room.project.focus; renderSwitch(room); }
  }
}

// ------------------------------------------------------------------ per frame: slide fronts, light windows, place switches
const ease = k => k * k * (3 - 2 * k);
const _a = new THREE.Vector3(), _s = { x: 0, y: 0 };
export function stepFocus(rooms, dt) {
  for (const r of rooms) {
    const f = r.front;
    if (!f) continue;
    const want = r.focus === 'background' ? 1 : 0;
    r.shellK = want > r.shellK ? Math.min(want, r.shellK + dt / SLIDE_S) : Math.max(want, r.shellK - dt / SLIDE_S);
    r.closed = r.shellK >= 1;
    const k = ease(r.shellK);
    f.shell.visible = k > 0;
    f.shell.position.y = REDUCED ? 0 : (1 - k) * DROP;   // reduced motion: a fade, no slide
    for (const m of f.mats) { m.transparent = k < 1; m.opacity = k; m.depthWrite = k === 1; }
    r.signMesh.position.y = r.signY + k * (SHELL_H - WALL_H);   // the name stays readable above the roof
    const lit = r.active;
    if (lit !== f.lit) { f.lit = lit; f.windowMat.color.set(lit ? LIT : UNLIT); }
    f.pennant.visible = r.focus === 'priority';
  }
}
export function positionSwitches() {
  for (const el of switches.values()) {
    const r = el.room;
    toScreen(_a.set(r.ox + RW, 0, r.oy + RD), _s);
    const x = Math.round(_s.x), y = Math.round(_s.y + 14);
    if (x !== el.qx || y !== el.qy) { el.qx = x; el.qy = y; el.style.transform = `translate(${x}px,${y}px) translate(-50%,0)`; }
  }
}
