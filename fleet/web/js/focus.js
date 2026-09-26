// Focus: the user's choice of priority or background for each room's work, set with the room's two-position switch.
// Work is focused through its registered project when linked and through its label otherwise; the server resolves
// each job's and session's `focus`, with priority for anything never chosen. A room is in the background when all its
// work is. A background room is dimmed and desaturated, its androids drop their speech bubbles and move to near
// still however busy they are, and its props stop animating. Nothing moves or resizes when focus changes.

import * as THREE from 'three';
import { RD, RW } from './env.js';
import { toScreen } from './scene.js';
import { setDimmed } from './dim.js';

const FADE_S = 0.4;
export const CALM = 0.05;   // how fast androids in a background room move and animate, relative to normal
const floorUi = document.getElementById('floorUi');

// ------------------------------------------------------------------ focus from the state document
// A switch flipped here waits for the server to confirm it, so a document sent before the change landed doesn't
// flick the room back.
const pending = new Map();   // "project:<id>" | "label:<label>" → focus asked for
let current = [];            // rooms as of the last state document
const keyOf = item => item.project_id ? 'project:' + item.project_id : 'label:' + item.project;

export function applyFocus(doc, rooms) {
  current = rooms;
  const byRoom = new Map();   // room name → key → focus the server resolved
  for (const h of doc.hosts || []) for (const item of [...(h.jobs || []), ...(h.sessions || [])]) {
    if (!item.project) continue;
    if (!byRoom.has(item.project)) byRoom.set(item.project, new Map());
    byRoom.get(item.project).set(keyOf(item), item.focus);
  }
  for (const keys of byRoom.values()) for (const [key, focus] of keys) if (pending.get(key) === focus) pending.delete(key);
  for (const r of rooms) {
    r.focusKeys = byRoom.get(r.name) || new Map();
    resolveRoom(r);
    if (r.dimK == null) r.dimK = r.focus === 'background' ? 1 : 0;   // a room appears as it is; only a switch fades
    renderSwitch(r);
  }
  const names = new Set(rooms.map(r => r.name));
  for (const [name, el] of switches) if (!names.has(name)) { el.remove(); switches.delete(name); }
}
function resolveRoom(r) {
  const focuses = [...r.focusKeys].map(([key, focus]) => pending.get(key) || focus);
  r.focus = !focuses.length ? null : focuses.every(f => f === 'background') ? 'background' : 'priority';
}

// a relayout (a resize into or out of the phone column) rebuilds rooms between state documents: keep their focus
export function carryFocus(before, rooms) {
  const old = new Map(before.map(r => [r.name, r]));
  for (const r of rooms) {
    const o = old.get(r.name);
    if (!o || o.dimK == null) continue;
    Object.assign(r, { focusKeys: o.focusKeys, focus: o.focus, dimK: o.dimK });
    renderSwitch(r);
  }
  current = rooms;
}

// ------------------------------------------------------------------ the switch, on the room's front corner
const switches = new Map();   // room name → element
function renderSwitch(r) {
  let el = switches.get(r.name);
  if (!r.focus) { if (el) { el.remove(); switches.delete(r.name); } return; }   // no work, nothing to focus
  if (!el) {
    el = document.createElement('div');
    el.className = 'focus-switch';
    el.setAttribute('role', 'group');
    el.innerHTML = '<button data-set="priority">priority</button><button data-set="background">background</button>';
    el.addEventListener('click', ev => {
      const b = ev.target.closest('button');
      if (b) setFocus(el.room, b.dataset.set);
    });
    floorUi.appendChild(el);
    switches.set(r.name, el);
  }
  el.room = r;
  el.dataset.room = r.name;
  el.dataset.focus = r.focus;
  for (const b of el.children) b.setAttribute('aria-pressed', String(b.dataset.set === r.focus));
  el.setAttribute('aria-label', `Focus for ${r.label}`);
  el.title = el.dataset.error ? `Couldn’t change focus: ${el.dataset.error}` : `${r.label} is in ${r.focus}`;
}

async function setFocus(r, focus) {
  if (r.focus === focus) return;
  const keys = [...r.focusKeys.keys()], el = switches.get(r.name);
  for (const key of keys) pending.set(key, focus);
  delete el.dataset.error;
  const touched = () => current.filter(room => [...room.focusKeys.keys()].some(key => keys.includes(key)));
  for (const room of touched()) { resolveRoom(room); renderSwitch(room); }
  const body = { focus, projects: [], labels: [] };
  for (const key of keys) {
    const [kind, name] = [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)];
    body[kind === 'project' ? 'projects' : 'labels'].push(name);
  }
  try {
    const res = await fetch('/api/focus', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  } catch (err) {
    for (const key of keys) pending.delete(key);
    el.dataset.error = err.message;
    for (const room of touched()) { resolveRoom(room); renderSwitch(room); }
  }
}

// ------------------------------------------------------------------ per frame: fade the dimming, place the switches
export function stepFocus(rooms, dt) {
  const rects = [];
  for (const r of rooms) {
    if (r.dimK == null) continue;   // not yet placed by a state document
    const want = r.focus === 'background' ? 1 : 0;
    r.dimK = want > r.dimK ? Math.min(want, r.dimK + dt / FADE_S) : Math.max(want, r.dimK - dt / FADE_S);
    if (r.dimK > 0) rects.push([r.ox - 0.15, r.oy - 0.15, r.ox + RW + 0.15, r.oy + RD + 0.15, r.dimK]);
  }
  setDimmed(rects);
}
const _a = new THREE.Vector3(), _s = { x: 0, y: 0 };
export function positionSwitches() {
  for (const el of switches.values()) {
    const r = el.room;
    toScreen(_a.set(r.ox + RW, 0, r.oy + RD), _s);
    const x = Math.round(_s.x), y = Math.round(_s.y + 14);
    if (x !== el.qx || y !== el.qy) { el.qx = x; el.qy = y; el.style.transform = `translate(${x}px,${y}px) translate(-50%,0)`; }
  }
}
