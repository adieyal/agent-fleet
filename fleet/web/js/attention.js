// Attention: the one loud signal, and the only diamond on the deck. The server derives attention items from what the
// hosts report (fleet/attention.py); a room with any open item gets a marked lantern hanging outside its front corner:
// a diamond with a glyph for the kind (✋ a blocker, ? a decision) and a count when it stands for more than one item.
// It swings once when an open item arrives, then glows steadily; it never flashes or loops, and with reduced motion it
// doesn't swing at all. A room whose items are all acknowledged keeps a dimmer steady lantern; snoozed items stay out
// of sight until the snooze ends. The lantern hangs outside the room's footprint, so a background room's is never
// dimmed. Clicking it opens a small list of the room's items with their actions; reading never changes an item.

import * as THREE from 'three';
import { animationNow } from './clock.js';
import { BOT_H, RD, REDUCED, RW, vh, vw } from './env.js';
import { age, clock, esc, stamp } from './util.js';
import { G, deckGroup, softDot, toScreen } from './scene.js';
import { workOf } from './model.js';
import { idChip, select } from './panel.js';
import { shortId } from './activity.js';
import { openAttentionReader } from './reader.js';
import { showToast, showView } from './building.js';

const GLYPH = { blocker: '✋', decision: '?', alert: '✱' };
const KIND = { blocker: 'Blocked', decision: 'Needs a decision', alert: 'Alert' };
const itemKind = item => item.kind === 'blocker' && !item.blocked && /^job status (failed|stalled|lost)$/.test(item.source) ? 'Failed' : KIND[item.kind];
const SNOOZE_S = 3600;

// ------------------------------------------------------------------ items per room, from the state document
const seen = new Set();   // open item ids already announced: each swings the lantern once
let items = [], projects = [];
let display = { rooms: {}, open_count: 0 };
export let openCount = 0;   // open items under the lanterns: the header's "need you"
// Every item not yet resolved, newest first: what the reader's Previous and Next step through.
export const openAttention = () => items.filter(i => i.state !== 'resolved').sort((a, b) => b.last_seen - a.last_seen);
export const attentionFor = key =>items.filter(i => i.owner?.key === key && i.state !== 'resolved');   // a job's or session's
export function applyAttention(rooms, doc) {
  if (doc) { items = doc.attention; display = doc.attention_display; projects = doc.projects || []; }
  const now = animationNow() / 1000;
  openCount = display.open_count;
  for (const r of rooms) {
    const marker = display.rooms[r.name];
    r.attention = marker ? { ...marker, listed: marker.listed.map(id => items.find(i => i.id === id)),
      shown: marker.shown.map(id => items.find(i => i.id === id)) } : null;
    const arrived = marker ? marker.open_ids.filter(id => !seen.has(id)) : [];
    if (arrived.length && !REDUCED) r.swingFrom = now;
    for (const id of arrived) seen.add(id);
  }
  renderLanterns(rooms);
  if (listRoom !== null) renderPanel();   // open or closed, the list never shows stale entries
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
    const label = `${r.label}: ${n > 1 ? `${n} things need you` : itemKind(a.shown[0]).toLowerCase()}${a.level === 'acknowledged' ? ' (acknowledged)' : ''}`;
    el.setAttribute('aria-label', label);
    el.title = label;
  }
  for (const [name, el] of lanterns) if (!want.has(name)) { el.remove(); lanterns.delete(name); }
}

// ------------------------------------------------------------------ the list: what needs you in this room, and what to do
const panel = document.getElementById('attnPanel');
const reader = document.getElementById('reader');
const ALL_ROOMS = Symbol('all rooms');
let panelStatus = '';
const openFolds = new Set();
export const allAttentionOpen = () => listRoom === ALL_ROOMS && !panel.hidden;
let listRoom = null, opener = null;   // the room whose list is rendered, whether or not it is open
export function openAllAttention(from = document.getElementById('needYou')) {
  if (allAttentionOpen() && from?.id === 'needYou') { closePanel(true); return; }
  openPanel(ALL_ROOMS, from);
}
function openPanel(name, from = lanterns.get(name)) {
  listRoom = name;
  opener = from;
  panelStatus = '';
  panel.dataset.scope = name === ALL_ROOMS ? 'all' : 'room';
  panel.setAttribute('aria-label', name === ALL_ROOMS ? 'All-rooms attention' : 'What needs you here');
  renderPanel();
  if (listRoom === null) return;   // nothing listed there any more
  panel.hidden = false;
  positionPanel();
  document.getElementById('needYou')?.setAttribute('aria-expanded', String(allAttentionOpen()));
  panel.querySelector('[data-close]')?.focus({ preventScroll: true });
}
function positionPanel() {
  if (panel.hidden) return;
  if (listRoom === ALL_ROOMS) {
    panel.style.transform = `translate(${Math.max(8, vw - panel.offsetWidth - 12)}px,60px)`;
    return;
  }
  if (!opener) return;
  const at = opener.getBoundingClientRect(), w = panel.offsetWidth, h = panel.offsetHeight;
  const x = Math.min(Math.max(8, at.right + 10), vw - w - 8), y = Math.min(Math.max(60, at.top - 20), vh - h - 8);
  panel.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
}
window.addEventListener('resize', positionPanel);
const ownerName = owner => `${owner.host}:${shortId(owner.id)}`;
// A person closing it gets focus back on the lantern that opened it; an emptied list just goes.
function closePanel(returnFocus = false) {
  if (panel.hidden) return;
  const back = opener?.id === 'needYou' ? document.getElementById('needYou')
    : opener?.dataset.place === 'lobby' ? document.querySelector('.floor-lantern[data-place="lobby"]') : opener;
  opener = null; panel.hidden = true;
  document.getElementById('needYou')?.setAttribute('aria-expanded', 'false');
  const target = back?.isConnected ? back : listRoom === ALL_ROOMS ? document.getElementById('needYou') : null;
  if (returnFocus) target?.focus({ preventScroll: true });
}
// P5: shared rows and separate groups leave room for a later ownership fold.
function ownerMeta(owner) {
  if (!owner) return 'Owner not reported';
  if (typeof owner === 'string') return `Owner: ${esc(owner)}`;
  return `Owner: ${esc(owner.name || owner.label || owner.type || 'reported owner')}${owner.host ? ` · ${esc(owner.host)}` : ''}${owner.id ? ` · ${idChip(owner.id)}` : ''}`;
}
function placeName(item) {
  const project = projects.find(p => p.id === item.project_id);
  return project?.name || item.project || (item.project_id ? `Project ${item.project_id} · room not reported` : 'Front desk · no registered room');
}
function readerConsequence(item) {
  if (item.refusals) return 'Review before allowing: grants change job permissions and start a continuation; Fleet cannot undo them. All Bash requires confirmation.';
  if (item.questions) return 'Read the question; answer in the session’s terminal. Fleet cannot type there.';
  if (item.blocked) return 'Send an answer to add a new job step and continue work; sending cannot be undone in Fleet.';
  if (item.kind === 'decision') return 'Answer to record a decision and resolve the request; this cannot be undone in Fleet.';
  return 'Open the job or context to inspect the problem; opening changes no stored state.';
}
function renderItem(i, global) {
  const owner = typeof i.owner === 'object' ? i.owner : null;
  const present = owner?.key && !!workOf(owner.key);
  const state = i.state === 'snoozed' ? `snoozed until ${esc(clock(i.snoozed_until).slice(0, 5))}` : i.state;
  const actions = (i.state === 'open' ? `<button data-act="acknowledge" title="Mark as seen: dims the lantern when all items are acknowledged; keeps the item open. Reopen restores attention; work is unchanged">Acknowledge</button><button data-act="snooze" title="Hide from the lantern for 1 hour, then return automatically; Reopen restores it sooner. Work is unchanged">Snooze 1h</button>`
    : i.state === 'acknowledged' ? `<button data-act="snooze" title="Hide for 1 hour; Reopen restores it sooner. Work is unchanged">Snooze 1h</button><button data-act="reopen" title="Return this item to open attention and light its lantern; acknowledge or snooze it again to undo">Reopen</button>`
    : `<button data-act="reopen" title="Return this item to open attention and light its lantern; acknowledge or snooze it again to undo">Reopen</button>`)
    + '<button data-act="resolve" title="Close this attention item and remove it from the lantern; does not answer, restart work or grant permissions. Undo is available for 6 seconds">Resolve</button>';
  const since = i.since ?? i.last_seen;
  const context = i.refusals ? 'Review refused commands' : i.questions ? 'Read the question' : i.blocked ? 'Answer' : i.kind === 'decision' ? 'Answer question' : null;
  return `<li class="attn-item" data-id="${esc(i.id)}" data-state="${esc(i.state)}" data-kind="${esc(i.kind)}">
    <span class="ak">${GLYPH[i.kind]}</span><div class="ab"><b>${esc(i.summary)}</b>
      ${global ? `<small class="attn-place">${esc(placeName(i))} · ${idChip(i.id)}</small><small class="attn-owner">${ownerMeta(i.owner)}</small>` : ''}
      <small>${itemKind(i)} · ${state} · ${global ? `<time class="attn-age" title="${since ? esc(new Date(since * 1000).toLocaleString()) : ''}">${since ? `${i.since == null ? 'last seen ' : ''}${age(since)} ago` : 'Age not reported'}</time>` : `<time title="${esc(new Date(i.last_seen * 1000).toLocaleString())}">${stamp(i.last_seen)}</time>`}${i.stale ? ' · host unreachable' : ''}</small>
      ${present ? `<button class="owner" data-owner="${esc(owner.key)}" title="${global ? 'Open on the whole deck; your saved view is unchanged' : esc(owner.key)}">${owner.type === 'job' ? 'Open job' : 'Open session'} ${esc(ownerName(owner))}</button>` : `<button class="owner" data-context="${esc(i.id)}" title="Read context; opening changes no stored state">Open context</button>`}
      ${context ? `<button class="owner" data-context="${esc(i.id)}" title="${esc(readerConsequence(i))}">${context}</button>` : ''}
      ${global ? `<p class="attn-consequence">${esc(readerConsequence(i))}</p>` : ''}
      <p class="attn-consequence">Resolve closes this item and removes it from the lantern; it does not answer, restart work or grant permissions. Undo is available for 6 seconds.</p><div class="aa">${actions}</div><em class="err" role="alert"></em>
    </div></li>`;
}
function renderGroup(rows, key, name, fold = false) {
  const content = `<ul>${rows.map(i => renderItem(i, true)).join('')}</ul>`;
  return fold ? `<details data-attention-group="${esc(key)}"${openFolds.has(key) ? ' open' : ''}><summary>${esc(name)} · ${rows.length}</summary>${content}</details>`
    : `<section data-attention-group="${esc(key)}"><h4>${esc(name)} · ${rows.length}</h4>${content}</section>`;
}
function renderPanel() {
  const global = listRoom === ALL_ROOMS;
  const listed = global ? items.filter(i => i.state === 'open') : (display.rooms[listRoom]?.listed || []).map(id => items.find(i => i.id === id)).filter(Boolean);
  if (!global && !listed.length) { closePanel(); listRoom = null; panel.replaceChildren(); return; }
  const room = global ? null : lanterns.get(listRoom)?.room;
  const others = global ? items.filter(i => i.state === 'acknowledged' || i.state === 'snoozed') : [];
  panel.innerHTML = `<div class="ah"><h3>${global ? 'All-rooms attention' : esc(room ? room.label : listRoom)}</h3><button data-close aria-label="Close">✕</button></div>
    ${global ? `<p class="attn-guide">Every room and the front desk. Acknowledge marks seen; Snooze hides for 1 hour. Reopen returns either to open attention; work stays unchanged.</p><p class="attn-status" role="status">${esc(panelStatus)}</p>${listed.length ? renderGroup(listed, 'open', 'Open') : '<p class="attn-empty">No open attention items across the fleet.</p>'}${others.length ? renderGroup(others, 'other', 'Acknowledged and snoozed', true) : ''}`
      : `<ul>${listed.map(i => renderItem(i, false)).join('')}</ul>`}`;
  for (const fold of panel.querySelectorAll('details[data-attention-group]')) fold.addEventListener('toggle', ev => {
    const key = ev.target.dataset.attentionGroup;
    if (ev.target.open) openFolds.add(key); else openFolds.delete(key);
  });
  positionPanel();
}
panel.addEventListener('click', async ev => {
  if (ev.target.closest('[data-close]')) { closePanel(true); return; }
  const context = ev.target.closest('[data-context]');
  if (context) { openAttentionReader(items.find(item => item.id === context.dataset.context)); return; }
  const owner = ev.target.closest('[data-owner]');
  if (owner) {
    if (listRoom === ALL_ROOMS) { closePanel(); showView('deck'); }
    select(owner.dataset.owner); return;
  }
  const b = ev.target.closest('[data-act]');
  if (!b) return;
  const row = b.closest('.attn-item'), body = { id: row.dataset.id };
  if (b.dataset.act === 'snooze') body.seconds = SNOOZE_S;
  for (const x of row.querySelectorAll('[data-act]')) x.disabled = true;
  try {
    const res = await fetch('/api/attention/' + b.dataset.act, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const result = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(result.error || `HTTP ${res.status}`);
    if (listRoom === ALL_ROOMS) {
      panelStatus = `${b.dataset.act === 'acknowledge' ? 'Acknowledged; now in the fold below' : b.dataset.act === 'snooze' ? 'Snoozed for 1 hour; now in the fold below' : b.dataset.act === 'reopen' ? 'Returned to open attention' : 'Resolved; Undo is available in the toast'}. Work is unchanged.`;
      const status = panel.querySelector('.attn-status');
      if (status) status.textContent = panelStatus;
    }
    if (b.dataset.act === 'resolve') {
      showToast('Attention resolved; work is unchanged. Undo within 6 seconds.', async () => {
        try {
          const response = await fetch('/api/attention/undo-resolve', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: body.id, undo: result.undo }) });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
          if (listRoom === ALL_ROOMS) {
            panelStatus = 'Attention restored to its previous state; work is unchanged.';
            const status = panel.querySelector('.attn-status');
            if (status) status.textContent = panelStatus;
          }
          showToast('Attention restored; work is unchanged.');
        } catch (err) { showToast(`Couldn’t undo Resolve: ${err.message}`); }
      });
    }
  } catch (err) {
    for (const x of row.querySelectorAll('[data-act]')) x.disabled = false;
    row.querySelector('.err').textContent = `Couldn’t ${b.dataset.act}: ${err.message}`;
  }
  // the new state arrives with the next pushed document
});
// Captured so this Escape closes only the list, not the job panel too; an open reader takes Escape first.
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || panel.hidden || !reader.hidden) return;
  ev.stopPropagation();
  closePanel(true);
}, true);
// A click away on the deck itself closes it; focus goes where the click went. The deck's other controls (the job
// panel it opens, the header chips, the reader) work beside the list and leave it open.
const scene = [document.getElementById('world'), document.getElementById('stars')];
document.addEventListener('pointerdown', ev => {
  if (!panel.hidden && scene.includes(ev.target)) closePanel();
});

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
