// Attention: the one loud signal, and the only diamond on the deck. The server derives attention items from what the
// hosts report (fleet/attention.py); a room with any open item gets a marked lantern hanging outside its front corner:
// a diamond with a glyph for the kind (✋ a blocker, ? a decision) and a count when it stands for more than one item.
// It swings once when an open item arrives, then glows steadily; it never flashes or loops, and with reduced motion it
// doesn't swing at all. A room whose items are all acknowledged keeps a dimmer steady lantern; snoozed items stay out
// of sight until the snooze ends. The lantern hangs outside the room's footprint, so a background room's is never
// dimmed. Clicking it opens a small list of the room's items with their actions; reading never changes an item.

import * as THREE from 'three';
import { BOT_H, RD, REDUCED, RW, vh, vw } from './env.js';
import { clock, esc } from './util.js';
import { G, deckGroup, softDot, toScreen } from './scene.js';
import { ents } from './model.js';
import { select } from './panel.js';
import { shortId } from './activity.js';

const GLYPH = { blocker: '✋', decision: '?' };
const KIND = { blocker: 'Blocked', decision: 'Needs a decision' };
const SNOOZE_S = 3600;

// ------------------------------------------------------------------ items per room, from the state document
const seen = new Set();   // open item ids already announced: each swings the lantern once
let items = [];
export let openCount = 0;   // open items under the lanterns: the header's "need you"
export function applyAttention(rooms, doc) {
  if (doc) items = doc.attention || [];
  const now = performance.now() / 1000;
  openCount = 0;
  for (const r of rooms) {
    const mine = items.filter(i => i.project === r.name && i.state !== 'resolved');
    const shown = mine.filter(i => i.state === 'open' || i.state === 'acknowledged');
    r.attention = mine.length ? {
      listed: mine, shown,
      level: shown.some(i => i.state === 'open') ? 'open' : shown.length ? 'acknowledged' : null,
      kind: shown.some(i => i.kind === 'blocker') ? 'blocker' : 'decision',   // a blocker outranks a decision
    } : null;
    openCount += shown.filter(i => i.state === 'open').length;
    const arrived = shown.filter(i => i.state === 'open' && !seen.has(i.id));
    if (arrived.length && !REDUCED) r.swingFrom = now;
    for (const i of arrived) seen.add(i.id);
  }
  renderLanterns(rooms);
  if (openRoom) renderPanel();
}

// ------------------------------------------------------------------ the lantern: a post, an arm and a cord in the world,
// the marked diamond itself drawn over the cord's end so it stays legible at every zoom
const POST_H = BOT_H + 1.7, ARM = 0.6, CORD = 0.5, SWING_S = 2.6;
const SWING_AXIS = new THREE.Vector3(1, 0, 1).normalize();   // across the screen
const postMat = new THREE.MeshStandardMaterial({ color: '#5b6780', roughness: 0.5, metalness: 0.6 });
const glowMap = softDot('rgba(255,79,176,.6)', 'rgba(255,79,176,0)');
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
  const glowMat = new THREE.SpriteMaterial({ map: glowMap, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true });
  const glow = new THREE.Sprite(glowMat);
  glow.scale.setScalar(1.6);
  lamp.add(glow);
  pivot.add(lamp);
  g.add(pivot);
  deckGroup.add(g);
  return { group: g, pivot, lamp, glowMat };
}

const lanterns = new Map();   // room name → element
function renderLanterns(rooms) {
  const want = new Set();
  for (const r of rooms) {
    const a = r.attention;
    if (!a || !a.level) { if (r.lantern) r.lantern.group.visible = false; continue; }
    want.add(r.name);
    if (!r.lantern) r.lantern = buildLantern(r);
    r.lantern.group.visible = true;
    r.lantern.glowMat.opacity = a.level === 'open' ? 1 : 0.3;
    let el = lanterns.get(r.name);
    if (!el) {
      el = document.createElement('button');
      el.className = 'lantern';
      el.innerHTML = '<span class="lg"></span><b></b>';
      el.addEventListener('click', ev => { ev.stopPropagation(); openPanel(el.room.name); });
      document.getElementById('floorUi').appendChild(el);
      lanterns.set(r.name, el);
    }
    const n = a.shown.length;
    el.room = r;
    Object.assign(el.dataset, { room: r.name, kind: a.kind, state: a.level, count: String(n) });
    el.classList.toggle('ack', a.level === 'acknowledged');
    el.firstChild.textContent = GLYPH[a.kind];
    el.lastChild.textContent = n > 1 ? String(n) : '';
    const label = `${r.label}: ${n > 1 ? `${n} things need you` : KIND[a.kind].toLowerCase()}${a.level === 'acknowledged' ? ' (acknowledged)' : ''}`;
    el.setAttribute('aria-label', label);
    el.title = label;
  }
  for (const [name, el] of lanterns) if (!want.has(name)) { el.remove(); lanterns.delete(name); }
}

// ------------------------------------------------------------------ the list: what needs you in this room, and what to do
const panel = document.getElementById('attnPanel');
let openRoom = null;
function openPanel(name) {
  openRoom = name;
  renderPanel();
  panel.hidden = false;
  const at = lanterns.get(name).getBoundingClientRect(), w = panel.offsetWidth, h = panel.offsetHeight;
  const x = Math.min(Math.max(8, at.right + 10), vw - w - 8), y = Math.min(Math.max(60, at.top - 20), vh - h - 8);
  panel.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
}
const ownerName = owner => `${owner.host}:${shortId(owner.id)}`;
function closePanel() { openRoom = null; panel.hidden = true; }
function renderPanel() {
  const listed = (items.filter(i => i.project === openRoom && i.state !== 'resolved'));
  if (!listed.length) { closePanel(); return; }
  const room = lanterns.get(openRoom)?.room;
  panel.innerHTML = `<div class="ah"><h3>${esc(room ? room.label : openRoom)}</h3><button data-close aria-label="Close">✕</button></div>
    <ul>${listed.map(i => {
      const owner = i.owner, present = ents.has(owner.key);
      const state = i.state === 'snoozed' ? `snoozed until ${esc(clock(i.snoozed_until).slice(0, 5))}` : i.state;
      const actions = i.state === 'open' ? `<button data-act="acknowledge">Acknowledge</button><button data-act="snooze">Snooze 1h</button>`
        : i.state === 'acknowledged' ? `<button data-act="snooze">Snooze 1h</button><button data-act="reopen">Reopen</button>`
        : `<button data-act="reopen">Reopen</button>`;
      return `<li class="attn-item" data-id="${esc(i.id)}" data-state="${esc(i.state)}" data-kind="${esc(i.kind)}">
        <span class="ak">${GLYPH[i.kind]}</span>
        <div class="ab"><b>${esc(i.summary)}</b>
          <small>${KIND[i.kind]} · ${state}${i.stale ? ' · host unreachable' : ''}</small>
          ${present ? `<button class="owner" data-owner="${esc(owner.key)}" title="${esc(owner.key)}">${owner.type === 'job' ? 'Open job' : 'Open session'} ${esc(ownerName(owner))}</button>`
                    : `<span class="owner" title="${esc(owner.key)}">${esc(ownerName(owner))}</span>`}
          <div class="aa">${actions}</div><em class="err"></em></div>
      </li>`;
    }).join('')}</ul>`;
}
panel.addEventListener('click', async ev => {
  if (ev.target.closest('[data-close]')) { closePanel(); return; }
  const owner = ev.target.closest('[data-owner]');
  if (owner) { select(owner.dataset.owner); return; }
  const b = ev.target.closest('[data-act]');
  if (!b) return;
  const row = b.closest('.attn-item'), body = { id: row.dataset.id };
  if (b.dataset.act === 'snooze') body.seconds = SNOOZE_S;
  for (const x of row.querySelectorAll('[data-act]')) x.disabled = true;
  try {
    const res = await fetch('/api/attention/' + b.dataset.act, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  } catch (err) {
    for (const x of row.querySelectorAll('[data-act]')) x.disabled = false;
    row.querySelector('.err').textContent = `Couldn’t ${b.dataset.act}: ${err.message}`;
  }
  // the new state arrives with the next pushed document
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && openRoom) closePanel(); });

// ------------------------------------------------------------------ per frame: one swing on arrival, then still
const _q = new THREE.Quaternion(), _a = new THREE.Vector3(), _s = { x: 0, y: 0 };
export function stepLanterns(rooms, now) {
  for (const r of rooms) {
    const l = r.lantern;
    if (!l || !l.group.visible) continue;
    const age = r.swingFrom == null ? SWING_S : now - r.swingFrom;
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
