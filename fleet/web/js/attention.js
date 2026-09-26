// Attention: the one loud signal. A room with work that needs you gets a marked lantern hanging outside its front
// corner: a diamond with a glyph for the kind of item, and a count when there is more than one. It swings once when an
// item arrives, then glows steadily; it never flashes or loops. It hangs outside the room, so a background room shows
// it just as loudly. Items are jobs that failed or stalled: a failed job whose agent reported itself blocked is a
// blocker, anything else an alert. An item stays until the job goes away or is dismissed in this browser.

import * as THREE from 'three';
import { BOT_H, RD, REDUCED, RW } from './env.js';
import { G, deckGroup, softDot, toScreen } from './scene.js';
import { ents } from './model.js';
import { isSession } from './activity.js';
import { select } from './panel.js';

const BLOCKED = /FLEET_STATUS:\s*\**\s*blocked\b/i;
export function attentionKind(job) {
  if (job.status === 'stalled') return 'alert';
  if (job.status !== 'failed') return null;
  const step = (job.steps || []).find(s => s.status === 'failed');
  return step && BLOCKED.test(step.result || '') ? 'blocker' : 'alert';
}
const GLYPH = { blocker: '✋', alert: '!', mixed: '◆' };
const KIND_TEXT = { blocker: 'blocked', alert: 'needs a look', mixed: 'needs you' };

// ------------------------------------------------------------------ items per room, from the androids on the deck
const seen = new Set();   // "host:job@updated_at": an item that fails again later swings the lantern again
export function applyAttention(rooms) {
  const now = performance.now() / 1000;
  for (const r of rooms) r.attention = null;
  const byRoom = new Map();
  for (const e of ents.values()) {
    const kind = !isSession(e) && attentionKind(e.job);
    if (!kind) continue;
    if (!byRoom.has(e.room)) byRoom.set(e.room, []);
    byRoom.get(e.room).push({ key: e.key, kind, id: e.key + '@' + e.job.updated_at });
  }
  for (const r of rooms) {
    const items = byRoom.get(r.name);
    if (!items) continue;
    const kinds = new Set(items.map(i => i.kind));
    r.attention = { items, kind: kinds.size === 1 ? items[0].kind : 'mixed' };
    if (items.some(i => !seen.has(i.id))) r.swingFrom = now;
    for (const i of items) seen.add(i.id);
  }
  renderLanterns(rooms);
}

// ------------------------------------------------------------------ the lantern: a post, an arm and a cord in the world,
// the marked diamond itself drawn over the cord's end so it stays legible at every zoom
const POST_H = BOT_H + 1.7, ARM = 0.6, CORD = 0.5, SWING_S = 2.6;
const SWING_AXIS = new THREE.Vector3(1, 0, 1).normalize();   // across the screen
const postMat = new THREE.MeshStandardMaterial({ color: '#5b6780', roughness: 0.5, metalness: 0.6 });
const glowMat = new THREE.SpriteMaterial({ map: softDot('rgba(255,79,176,.6)', 'rgba(255,79,176,0)'), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true });
function buildLantern(r) {
  const g = new THREE.Group();
  g.position.set(r.ox + RW + 0.45, 0, r.oy + RD + 0.45);
  const piece = (parent, sx, sy, sz, x, y, z) => {
    const m = new THREE.Mesh(G.box, postMat);
    m.scale.set(sx, sy, sz); m.position.set(x, y, z); m.castShadow = true;
    parent.add(m);
  };
  piece(g, 0.08, POST_H, 0.08, 0, POST_H / 2, 0);
  piece(g, ARM, 0.05, 0.05, ARM / 2, POST_H, 0);
  const pivot = new THREE.Group();
  pivot.position.set(ARM, POST_H, 0);
  piece(pivot, 0.02, CORD, 0.02, 0, -CORD / 2, 0);
  const lamp = new THREE.Object3D();
  lamp.position.y = -CORD - 0.3;
  const glow = new THREE.Sprite(glowMat);
  glow.scale.setScalar(1.6);
  lamp.add(glow);
  pivot.add(lamp);
  g.add(pivot);
  deckGroup.add(g);
  return { group: g, pivot, lamp };
}

const lanterns = new Map();   // room name → element
function renderLanterns(rooms) {
  const want = new Set();
  for (const r of rooms) {
    if (!r.attention) { if (r.lantern) r.lantern.group.visible = false; continue; }
    want.add(r.name);
    if (!r.lantern) r.lantern = buildLantern(r);
    r.lantern.group.visible = true;
    let el = lanterns.get(r.name);
    if (!el) {
      el = document.createElement('button');
      el.className = 'lantern';
      el.innerHTML = '<span class="lg"></span><b></b>';
      el.addEventListener('click', ev => { ev.stopPropagation(); select(el.room.attention.items[0].key); });
      document.getElementById('floorUi').appendChild(el);
      lanterns.set(r.name, el);
    }
    const { items, kind } = r.attention, n = items.length;
    el.room = r;
    el.dataset.room = r.name; el.dataset.kind = kind; el.dataset.count = String(n);
    el.firstChild.textContent = GLYPH[kind];
    el.lastChild.textContent = n > 1 ? String(n) : '';
    el.setAttribute('aria-label', `${r.label}: ${n} ${n > 1 ? 'things need' : 'thing needs'} you`);
    el.title = `${r.label}: ${n > 1 ? `${n} things need you` : KIND_TEXT[kind]} · click to open`;
  }
  for (const [name, el] of lanterns) if (!want.has(name)) { el.remove(); lanterns.delete(name); }
}

// ------------------------------------------------------------------ per frame: one swing on arrival, then still
const _q = new THREE.Quaternion(), _a = new THREE.Vector3(), _s = { x: 0, y: 0 };
export function stepLanterns(rooms, now) {
  for (const r of rooms) {
    const l = r.lantern;
    if (!l || !l.group.visible) continue;
    const age = r.swingFrom == null || REDUCED ? SWING_S : now - r.swingFrom;
    const angle = age < SWING_S ? 0.45 * Math.exp(-age * 1.7) * Math.sin(age * 5.5) : 0;
    l.pivot.quaternion.copy(_q.setFromAxisAngle(SWING_AXIS, angle));
  }
}
export function positionLanterns() {
  for (const el of lanterns.values()) {
    const l = el.room.lantern;
    l.lamp.getWorldPosition(_a);
    toScreen(_a, _s);
    const x = Math.round(_s.x), y = Math.round(_s.y);
    if (x !== el.qx || y !== el.qy) { el.qx = x; el.qy = y; el.style.transform = `translate(${x}px,${y}px) translate(-50%,-50%)`; }
  }
}
