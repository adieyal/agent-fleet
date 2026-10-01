// The building (L0): every registered project on its own floor of a building seen in cross-section, with the lobby on
// the ground floor. A view beside the deck, chosen with the header's deck | building switch and remembered.
//
// Floors come from the server (fleet/building.py): capacity, and which floor each project holds. A floor never moves
// or resizes; only what is on it changes. Priority floors are open (no front wall): a furnished office, lit warm while
// any of its project's runs is working. Background floors show cool glass, quieter than any open floor, with a warm glow
// behind the panes while active. Free floors have a small "To let" card in one window and a dotted outline. The spine
// carries a name plate (at most three words) and a progress ring per floor, shown
// as unknown until projects have plans. No androids and no speech here: activity is light. The lobby holds the host
// colour key and the visitors: labels with work that no project claims, one row per label with its hosts. Moving one in
// first offers linking it to a project it may belong to (no floor taken), then a new project. A floor's ⇄ merges its
// project with another registered for the same work by mistake.
//
// Attention rolls up to floors: a floor with open or acknowledged items gets one marked lantern on a bracket from the
// spine beside its name plate, the diamond
// with the attention glyph and a count when it stands for more than one item. It swings once when an open item arrives,
// then hangs still. Items that belong to no floor light a lantern by the lobby. Each floor's plate carries its focus
// switch (open · windows), which sets the project's focus with one click and moves nothing.
//
// Clicking a floor enters it. Until floors have their own view, entering shows the deck with only that project's work
// (a stand-in for L1), with the lift panel on the screen's edge: a button per floor, L for the whole building and S for
// the storehouse. Esc steps out a level.
//
// Shuttering (ADR 0005) is a handle on the floor, apart from the focus switch: one pull, no confirmation, and a few
// seconds to undo. The project goes to the storehouse, an annex beside the lobby, as a crate; its floor says To let,
// and its attention hangs at the front desk and the storehouse door, never on the floor it left. A crate opens the
// project read-only on the deck, or moves back in, to its old floor if that is free. A full building offers only
// clearing a floor or cancelling; capacity is a setting (`fleet building capacity`), never offered here.
//
// The building is a light daylight diorama (docs/design/art-direction.md, l0.png) of pieces rendered in Blender
// (art/scripts/building_pieces.py): plinth and ground shadow, lobby, a floor piece per floor by its state, the spine and
// lift in one-storey bands, the roof and the storehouse. manifest.json says where each piece's level point lands and where
// every live control sits, so nothing here is measured by eye. It has its own canvas, drawn on demand (it is still unless
// the state changes or a lantern swings), so the deck's frame loop and input stay as they were.

import { animationNow, isStepping } from './clock.js';
import { QS, REDUCED, vh, vw } from './env.js';
import { esc, store } from './util.js';
import { THEMES, hostLook, projectLook, themeFor } from './looks.js';
import { working } from './activity.js';
import { enterProject } from './state.js';
import { openAttentionReader } from './reader.js';
import { enterFloor } from './bench.js';

// ------------------------------------------------------------------ views: the deck, the building (L0), a floor (L1)
// Inside, `current` is the floor entered, or 'S' with `crate` the project whose crate is open (read-only).
const VIEW_KEY = 'fleet.view';
export let buildingShown = false;
let current = null, crate = null;
const toggle = document.getElementById('viewToggle');
const lift = document.getElementById('lift');
// ('world' is a project floor in the sprite world, world/floor-view.js; opened from inside a floor, it shows that one)
function showView(view, where = null) {
  const leaving = current !== null;
  const project = view === 'world' && typeof current === 'number' ? floors.find(f => f.floor === current)?.projectId ?? null : null;
  current = view === 'floor' ? where : view === 'crate' ? 'S' : null;
  crate = view === 'crate' ? where : null;
  buildingShown = view === 'building';
  document.body.dataset.view = view === 'crate' ? 'floor' : view;
  if (crate) document.body.dataset.readonly = ''; else delete document.body.dataset.readonly;
  for (const b of toggle.querySelectorAll('button')) {
    b.setAttribute('aria-pressed', String(b.dataset.view === (current !== null ? 'building' : view)));
  }
  if (crate) enterProject(crate);
  else if (current !== null) enterProject(floors.find(f => f.floor === current).projectId);
  else if (leaving) enterProject(null);
  enterFloor(view === 'floor' ? floors.find(f => f.floor === current).projectId : null);
  if (!buildingShown) closeDialogs();
  renderLift();
  draw();
  document.dispatchEvent(new CustomEvent('fleet:view', { detail: { view, project } }));
}
toggle.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-view]');
  if (!b) return;
  store('localStorage', VIEW_KEY, b.dataset.view);
  showView(b.dataset.view);
});
function enter(floor) {
  const f = floors.find(x => x.floor === floor);
  if (f && f.projectId) showView('floor', floor);
}
// Esc steps out one level: a dialog over the building closes; inside, once nothing is open over the deck (a reader,
// the library, a panel or a list closes first), a floor goes back to the building and an open crate to the storehouse.
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape') return;
  if (buildingShown) { if (vacancy || storehouseOpen || moving || merging) { closeDialogs(); renderUi(); } return; }
  if (current === null) return;
  const open = !document.getElementById('reader').hidden || !document.getElementById('libraryPane').hidden
    || document.getElementById('panel').classList.contains('open') || !document.getElementById('attnPanel').hidden
    || !document.getElementById('workarea').hidden;
  if (open) return;
  if (current === 'S') openStorehouse(); else showView('building');
}, { capture: true });

// ------------------------------------------------------------------ the pieces and their manifest
// Each piece has tiers at 1x, 2x and 4x of l0's size (the plinths 1x and 2x); a tier gives its file, size, the pixel its
// level's point lands on (anchor_px) and slots: where live controls sit, in pixels from that point. Geometry here is
// always read from tier 1 and scaled; only the image drawn changes with the tier.
const PIECES = '/assets/world/building/';
const canvas = document.getElementById('buildingCanvas');
const ui = document.getElementById('buildingUi');
const ctx = canvas.getContext('2d');
let manifest = null;
fetch(PIECES + 'manifest.json').then(res => {
  if (!res.ok) throw new Error(`building pieces: HTTP ${res.status}`);
  return res.json();
}).then(m => {
  manifest = m;
  document.getElementById('building').style.background = m.backdrop;   // the diorama's sky; the header stays dark
  if (doc) applyBuilding(doc);
});
const one = name => manifest.pieces[name].tiers['1'];
// the smallest tier at least `want` times tier 1, else the largest there is
function tierOf(name, want) {
  const tiers = manifest.pieces[name].tiers, mults = Object.keys(tiers).map(Number).sort((a, b) => a - b);
  return tiers[mults.find(m => m >= want) ?? mults[mults.length - 1]];
}
const images = new Map();   // file → image, loading or loaded
function image(file) {
  let im = images.get(file);
  if (!im) {
    im = new Image();
    im.decoding = 'async';
    im.onload = () => draw();
    im.src = PIECES + file;
    images.set(file, im);
  }
  return im.complete && im.naturalWidth ? im : null;
}

// ------------------------------------------------------------------ the floors, from the state document
let doc = null, floors = [], lobby = null, crates = [];
export function applyBuilding(state) {
  doc = state;
  for (const [id, focus] of pending) if (state.building?.focus[id] === focus) pending.delete(id);
  floors = floorsOf(state);
  lobby = lobbyOf(state);
  crates = cratesOf(state);
  applyLanterns(state);
  renderUi();
  const gone = crate ? !crates.some(c => c.id === crate) : typeof current === 'number' && !floors.find(f => f.floor === current)?.projectId;
  if (gone) showView('building');   // the floor was shuttered, or the crate moved back in
  else renderLift();
  draw();
}
export function buildingReady() {   // the deck's assets have loaded (the building's own come with its manifest)
  if (doc) applyBuilding(doc);
}

function floorsOf(state) {
  const b = state.building;
  if (!b) return [];
  const projects = new Map((state.projects || []).map(p => [p.id, p]));
  const byFloor = new Map(Object.entries(b.floors).map(([id, floor]) => [floor, id]));
  // a floor's rooms are its project's rooms on the deck: one per label with work, looking as that room does there
  const looks = roomLooks(state), roomsOf = new Map();
  for (const h of state.hosts || []) for (const item of roomItems(state, h)) {
    if (!item.project_id || !item.project) continue;
    if (!roomsOf.has(item.project_id)) roomsOf.set(item.project_id, new Map());
    const rooms = roomsOf.get(item.project_id);
    if (!rooms.has(item.project)) rooms.set(item.project, { label: item.project, ...looks.get(item.project), active: false });
    rooms.get(item.project).active ||= working(item);
  }
  const taken = new Set();
  return Array.from({ length: b.capacity }, (_, i) => {
    const floor = i + 1, projectId = byFloor.get(floor);
    if (!projectId) return { floor, projectId: null, mode: 'to-let', active: false };
    const project = projects.get(projectId);
    const focus = pending.get(projectId) || b.focus[projectId];
    const rooms = [...(roomsOf.get(projectId) || new Map()).values()].sort((a, c) => a.label.localeCompare(c.label));
    return { floor, projectId, name: project.name, look: projectLook(projectId, taken), focus, rooms,
      mode: focus === 'background' ? 'windowed' : 'open', active: rooms.some(r => r.active) };
  });
}
// What gives a project a room: its jobs and sessions, and a pipeline declared to live there, with work or without.
function roomItems(state, h) {
  const declared = (state.pipelines || []).filter(p => p.host === h.name && p.project)
    .map(p => ({ project: p.project, project_id: p.project_id, status: p.run?.status === 'running' ? 'running' : 'idle' }));
  return [...(h.jobs || []), ...(h.sessions || []), ...declared];
}
// The deck gives each room a colour and a theme in name order (rooms.js layoutRooms); the same here, so a room looks
// the same on its floor as on the deck.
function roomLooks(state) {
  const names = new Set();
  for (const h of state.hosts || []) for (const item of roomItems(state, h)) if (item.project) names.add(item.project);
  const hues = new Set(), themes = new Set(), out = new Map();
  for (const name of [...names].sort()) {
    const look = projectLook(name, hues), theme = themeFor(name, themes);
    Object.assign(look, THEMES[theme].pal(look.hue));
    out.set(name, { look, theme });
  }
  return out;
}

// ------------------------------------------------------------------ focus: the switch on each plate
// A switch flipped here holds until the server's document agrees, so an older document doesn't flick it back.
const pending = new Map();   // project ID → focus asked for
const focusErrors = new Map();   // project ID → why the last change failed
async function setFocus(projectId, focus) {
  const f = floors.find(x => x.projectId === projectId);
  if (!f || f.focus === focus) return;
  pending.set(projectId, focus);
  focusErrors.delete(projectId);
  applyBuilding(doc);
  try {
    const res = await fetch('/api/focus', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ focus, projects: [projectId], labels: [] }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  } catch (err) {
    pending.delete(projectId);
    focusErrors.set(projectId, err.message);
    applyBuilding(doc);
  }
}

// ------------------------------------------------------------------ attention: one lantern per floor, and one by the lobby
// Only open and acknowledged items show; an acknowledged-only lantern is dimmer. Items on no floor (visitors' work,
// projects without a floor) hang theirs by the lobby, so none is ever out of sight.
const SWING_S = 2.6;
const seen = new Set();                 // open item IDs already announced: each swings its lantern once
const lanterns = new Map();             // floor number or 'lobby' → { level, count, kind, swingFrom }
const swings = new Map();               // floor number or 'lobby' → how many times its lantern has swung
function applyLanterns(state) {
  const byPlace = new Map(state.attention_display.places.map(marker => [marker.place, marker]));
  const now = animationNow() / 1000;
  for (const place of lanterns.keys()) if (!byPlace.has(place)) lanterns.delete(place);
  const announced = new Set();
  for (const [place, marker] of byPlace) {
    const l = lanterns.get(place) || { swingFrom: null };
    Object.assign(l, marker);
    const arrived = marker.open_ids.filter(id => !seen.has(id));
    for (const id of arrived) announced.add(id);
    if (arrived.length && !REDUCED) { l.swingFrom = now; swings.set(place, (swings.get(place) || 0) + 1); }
    lanterns.set(place, l);
  }
  for (const id of announced) seen.add(id);
}

// Visitors: labels with jobs, sessions or declared pipelines that no project claims, one per label listing the hosts it is unclaimed on.
// Registered projects without a floor, and work fleetd couldn't place in a project, are listed too: nothing with work
// drops out of view.
function lobbyOf(state) {
  const visitors = new Map();
  const projects = new Map((state.projects || []).map(p => [p.id, p]));
  for (const h of state.hosts || []) for (const item of roomItems(state, h)) {
    if (item.project_id) continue;
    const key = item.project ?? '';
    if (!visitors.has(key)) visitors.set(key, { label: item.project ?? null, hosts: [], count: 0, active: false });
    const v = visitors.get(key);
    if (!v.hosts.includes(h.name)) v.hosts.push(h.name);
    v.count++;
    v.active ||= working(item);
  }
  for (const v of visitors.values()) v.hosts.sort();
  const b = state.building || { capacity: 0, floors: {}, no_floor: [] };
  return {
    hosts: (state.hosts || []).map(h => ({ name: h.name, ok: !!h.ok, color: hostLook(h.name).color })),
    visitors: [...visitors.values()].sort((a, c) => (a.label ?? '').localeCompare(c.label ?? '')),
    noFloor: b.no_floor.map(id => ({ id, name: projects.get(id)?.name ?? id })),
    full: Object.keys(b.floors).length >= b.capacity,
    labels: state.project_labels || {},
  };
}

// The storehouse: a crate per shuttered project, oldest first, with its runs still in flight (they finish; nothing new
// is dispatched to a shuttered project).
function cratesOf(state) {
  const shuttered = state.building?.shuttered || {};
  const projects = new Map((state.projects || []).map(p => [p.id, p]));
  const runs = new Map();
  for (const h of state.hosts || []) for (const item of [...(h.jobs || []), ...(h.sessions || [])]) {
    if (!(item.project_id in shuttered)) continue;
    const r = runs.get(item.project_id) || { count: 0, active: 0 };
    r.count++;
    if (working(item)) r.active++;
    runs.set(item.project_id, r);
  }
  return Object.entries(shuttered).filter(([id]) => projects.has(id)).sort(([, a], [, b]) => a.at - b.at)
    .map(([id, record]) => ({ id, name: projects.get(id).name, floor: record.floor, at: record.at,
      runs: runs.get(id)?.count || 0, active: runs.get(id)?.active || 0 }));
}

// ------------------------------------------------------------------ the stack: which piece goes on which level
// Level 0 is the lobby, 1..n the floors, n + 1 the roof (and the spine's and lift's tops). Drawn back to front: the
// plinth (the ground and the building's shadow for this capacity), the storehouse, the spine, the lobby, the floors
// bottom up (each slab covers the back of the floor below), the roof, then the lift in front of the floors' right ends.
const built = new Map();   // floor → the piece drawn there, for the probe
function floorPiece(f) {
  const kind = f.mode === 'open' ? 'open' : f.mode === 'windowed' ? 'glass' : 'free';
  const top = f.floor === floors.length ? '-top' : '';   // the roof is cut away over the top floor: no ceiling's shade
  return `floor-${kind}${top}${kind === 'free' ? '' : f.active ? '-lit' : '-unlit'}`;
}
// the storehouse holds a crate per shuttered project, up to the most its pieces show; the sign counts them all
const cratesShown = () => Math.min(crates.length, Object.keys(manifest.pieces).filter(k => k.startsWith('annex-')).length - 1);
function stack() {
  const n = floors.length, roof = n + 1;
  const out = [[`plinth-${n}`, 0], [`annex-${cratesShown()}`, 0], ['spine-lobby', 0]];
  for (let k = 1; k <= n; k++) out.push(['spine', k]);
  out.push(['spine-cap', roof], ['lobby', 0]);
  for (const f of floors) out.push([floorPiece(f), f.floor]);
  out.push(['roof', roof], ['lift-lobby', 0]);
  for (let k = 1; k <= n; k++) out.push(['lift', k]);
  out.push(['lift-cap', roof]);
  return out;
}




// ------------------------------------------------------------------ framing: the whole building on one screen
// view.z is screen pixels per tier-1 pixel; (view.ox, view.oy) is where the lobby's level point lands. The frame holds
// every piece but the plinth, whose ground and shadow run off the edges into the backdrop, plus a strip of plinth in
// front; on the left it leaves room for the lobby's list and the lanterns. Ten floors fit a desktop screen.
const TOP_UI = 52, MARGIN = 14, LOBBY_ROOM = 300, NARROW_LOBBY_H = 150, PLINTH_STRIP = 40, MAX_Z = 1.25;
let view = null;
const levelOffset = i => i === 0 ? 0 : manifest.tiers['1'].lobby_step_px + (i - 1) * manifest.tiers['1'].step_px;
const levelY = i => view.oy - levelOffset(i) * view.z;
function fit() {
  if (!vw || !manifest || !floors.length) { view = null; return; }
  // on a phone the storeys get the width: the storehouse runs off the right edge (its sign stays on screen)
  const narrow = vw < 760;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [name, level] of stack()) {
    if (name.startsWith('plinth-') || (narrow && name.startsWith('annex-'))) continue;
    const t = one(name), x = -t.anchor_px[0], y = -levelOffset(level) - t.anchor_px[1];
    x0 = Math.min(x0, x); x1 = Math.max(x1, x + t.size[0]); y0 = Math.min(y0, y); y1 = Math.max(y1, y + t.size[1]);
  }
  y1 += PLINTH_STRIP;
  // (and the lobby's list goes below it)
  const l = MARGIN + (narrow ? 0 : LOBBY_ROOM), r = vw - MARGIN, t = TOP_UI + MARGIN, b = vh - MARGIN - (narrow ? NARROW_LOBBY_H : 0);
  const z = Math.min(MAX_Z, (r - l) / (x1 - x0), (b - t) / (y1 - y0));
  view = { z, ox: (l + r) / 2 - (x0 + x1) / 2 * z, oy: (t + b) / 2 - (y0 + y1) / 2 * z };
}
// A floor's front on screen, from the manifest's projection (one metre along x lands at x_px, in px right and down per
// px/m): its front edge runs from the level point to the right end, descending, and the storey rises one step above it.
function front() {
  const { x_px: [xr, xd], ppm_1x: ppm } = manifest.camera, w = manifest.floor.w;
  return { dx: w * xr * ppm, dy: w * xd * ppm };
}
function storeyRect(level) {
  const { dx, dy } = front(), step = level === 0 ? manifest.tiers['1'].lobby_step_px : manifest.tiers['1'].step_px, y = levelY(level);
  return { left: view.ox, right: view.ox + dx * view.z, top: y - step * view.z, bottom: y + dy * view.z };
}
const pieceRect = (name, level) => {
  const t = one(name);
  return { left: view.ox - t.anchor_px[0] * view.z, top: levelY(level) - t.anchor_px[1] * view.z, width: t.size[0] * view.z, height: t.size[1] * view.z };
};
const slotAt = (name, level, key) => {
  const s = one(name).slots[key];
  return { x: view.ox + s[0] * view.z, y: levelY(level) + s[1] * view.z };
};
const boxOf = (name, key) => one(name).boxes[key].map(v => v * view.z);

window.addEventListener('resize', draw);   // (after the deck's own handler has measured the window)
let drawPending = false;
function draw() {
  if (!buildingShown || drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => {
    drawPending = false;
    paint();
    placeUi();
    const swinging = stepLanterns(animationNow() / 1000);
    if (swinging && !isStepping()) draw();
  });
}
export { draw as stepBuilding };
// Every piece at the tier its on-screen size needs (zoom times the device's pixel ratio). A floor counts as built for
// the probe once its piece is on screen; pieces still loading are drawn when they arrive.
function paint() {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(vw * dpr), h = Math.round(vh * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w, h);
  fit();
  if (!view) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  const drawn = new Set();
  for (const [name, level] of stack()) {
    const im = image(tierOf(name, view.z * dpr).file);
    if (!im) continue;
    const r = pieceRect(name, level);
    ctx.drawImage(im, r.left, r.top, r.width, r.height);
    drawn.add(`${name}@${level}`);
  }
  for (const f of floors) {
    const piece = floorPiece(f);
    if (drawn.has(`${piece}@${f.floor}`)) built.set(f.floor, { piece, mode: f.mode, front: f.mode === 'open' ? 'none' : f.mode, glow: piece.endsWith('-lit') });
  }
}
// one swing on arrival, then still; true while any lantern is still swinging
function stepLanterns(now) {
  let swinging = false;
  for (const [place, l] of lanterns) {
    const age = l.swingFrom == null ? SWING_S : now - l.swingFrom;
    const angle = age < SWING_S ? 0.45 * Math.exp(-age * 1.7) * Math.sin(age * 5.5) : 0;
    const bob = ui.querySelector(`.lantern-hang[data-floor="${place}"] .bob`);
    if (bob) bob.style.transform = `rotate(${angle}rad)`;
    swinging ||= age < SWING_S;
  }
  return swinging;
}

// ------------------------------------------------------------------ name plates, progress rings and the lobby, over the scene
const WORDS = 3;
const plateName = name => { const w = name.trim().split(/\s+/); return w.length > WORDS ? w.slice(0, WORDS).join(' ') + '…' : w.join(' '); };
const RING = `<svg class="ring" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/></svg><i aria-hidden="true">?</i>`;
const SWITCH = { priority: 'open', background: 'windows' };   // the switch's two positions, as the floor shows them
function renderUi() {
  const frontDeskOpen = ui.querySelector('.front-desk')?.open;
  const plates = floors.map(f => {
    if (!f.projectId) return `<div class="plate" data-floor="${f.floor}" data-mode="to-let"><span class="fn">${f.floor}</span><b>To let</b></div>`;
    const error = focusErrors.get(f.projectId);
    return `<div class="plate" data-floor="${f.floor}" data-mode="${f.mode}" data-project="${esc(f.projectId)}" style="--accent:${f.look.accent}">
      <button class="enter" data-enter="${f.floor}" title="Enter ${esc(f.name)}"><span class="fn">${f.floor}</span><b>${esc(plateName(f.name))}</b></button>
      <span class="progress" data-progress="unknown" title="Progress unknown: no plan yet" aria-label="progress unknown">${RING}</span>
      <span class="fswitch" role="group" aria-label="Focus for ${esc(f.name)}" data-focus="${f.focus}"${error ? ` data-error title="Couldn’t change focus: ${esc(error)}"` : ''}>${
        Object.entries(SWITCH).map(([focus, label]) => `<button data-focus="${focus}" aria-pressed="${f.focus === focus}" title="${focus}">${label}</button>`).join('')}</span>
      <button class="merge-handle" data-merge="${esc(f.projectId)}" aria-label="Merge ${esc(f.name)} with another project"
        title="Merge with a project registered for the same work by mistake">⇄</button>
    </div>`;
  }).join('');
  const lamps = [...lanterns].map(([place, l]) => {
    const where = place === 'lobby' ? 'the front desk' : place === 'store' ? 'the storehouse' : floors.find(f => f.floor === place)?.name;
    const action = place === 'lobby' ? 'Show the whole deck' : place === 'store' ? 'Open the storehouse' : `Enter ${where}’s floor`;
    const label = `${action} · ${where}: ${l.count > 1 ? `${l.count} things need you` : 'something needs you'}${l.level === 'acknowledged' ? ' (acknowledged)' : ''}`;
    const lantern = `<button class="floor-lantern${l.level === 'acknowledged' ? ' ack' : ''}" data-place="${place}" data-state="${l.level}"
      data-count="${l.count}" data-kind="${l.kind}" aria-label="${esc(label)}" title="${esc(label)}"><span class="lg">${esc(l.glyph)}</span><b>${l.count > 1 ? l.count : ''}</b></button>`;
    // a floor's hangs from a bracket on the spine beside its name plate, the lobby's over the front desk and the
    // storehouse's at its door, on cords; the one swing picks up where it was on a redraw
    return `<div class="lantern-hang${typeof place === 'number' ? '' : ' cord'}" data-floor="${place}"><span class="bob">${lantern}</span></div>`;
  }).join('');
  const L = lobby;
  const visitor = v => `<li class="visitor" data-label="${esc(v.label ?? '')}" data-hosts="${esc(v.hosts.join(' '))}" title="${esc(`${v.label ?? 'no label'} on ${v.hosts.join(', ')} · ${v.count}`)}">
      <span>${esc(v.label === null ? 'no label' : L.labels[v.label] || v.label)}</span>${v.active ? '<em class="busy" aria-label="active"></em>' : ''}
      <small class="vhosts">${v.hosts.map(h => `<i data-host="${esc(h)}" style="--c:${hostLook(h).color}">${esc(h)}</i>`).join('')}</small>
      ${v.label === null ? '' : `<button data-move-in>Move in</button>`}
      <small class="err"></small></li>`;
  // the shutter handle: a pull-down bar at the floor's top corner, apart from the focus switch on the plate
  const handles = floors.filter(f => f.projectId).map(f => `<button class="shutter-handle" data-shutter="${esc(f.projectId)}" data-floor="${f.floor}"
      aria-label="Shutter ${esc(f.name)}" title="Pull down the shutter: pack ${esc(f.name)} away in the storehouse"></button>`).join('');
  const sign = `<button class="annex-sign" data-storehouse aria-label="Storehouse: ${crates.length} crate${crates.length === 1 ? '' : 's'}">Storehouse<b>${crates.length}</b></button>`;
  // the host colour key on the lobby's back wall: a dot per host (named in the lobby's list)
  const dots = `<div class="hostdots" aria-hidden="true">${L.hosts.map(h => `<i style="background:${h.ok ? h.color : 'var(--dim)'}"></i>`).join('')}</div>`;
  const html = `${plates}${handles}${lamps}${sign}${dots}${storehouseOpen ? storehouseHtml() : ''}${vacancy ? vacancyHtml() : ''}${
    moving ? moveInHtml(moving) : ''}${merging ? mergeHtml(merging) : ''}
    <div class="lobby" role="region" aria-label="Lobby">
      <div class="hostkey" aria-label="Hosts">${L.hosts.map(h => `<span class="host${h.ok ? '' : ' off'}" data-host="${esc(h.name)}" title="${esc(h.name)}${h.ok ? '' : ': offline'}"><i style="background:${h.ok ? h.color : 'var(--dim)'}"></i>${esc(h.name)}</span>`).join('')}</div>
      ${L.full ? '<div class="novacancy">No vacancies</div>' : ''}
      <div class="visitors"><h3>Visitors</h3>${L.visitors.length ? `<ul>${L.visitors.map(visitor).join('')}</ul>` : '<p class="none">None</p>'}</div>
      <details class="front-desk"${frontDeskOpen ? ' open' : ''}><summary>Front desk <b>${doc.attention_display.front_desk.length}</b></summary><ul>${doc.attention_display.front_desk.map(id => {
        const item = doc.attention.find(item => item.id === id);
        return `<li><button data-attention-context="${esc(id)}">✱ ${esc(item.summary)}</button></li>`;
      }).join('')}</ul></details>
      ${L.noFloor.length ? `<div class="nofloor"><h3>No floor</h3><ul>${L.noFloor.map(p => `<li data-project="${esc(p.id)}" title="${esc(p.name)}">${esc(plateName(p.name))}</li>`).join('')}</ul></div>` : ''}
    </div>`;
  // (an unchanged document leaves the controls alone: no flicker, and hover and keyboard focus stay where they were)
  if (html !== uiHtml) { ui.innerHTML = html; uiHtml = html; }
  placeUi();
}
let uiHtml = '';
async function post(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const reply = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(reply.error || `HTTP ${res.status}`);
  return reply;   // the change itself arrives with the next pushed document
}
ui.addEventListener('click', async ev => {
  const t = ev.target;
  const context = t.closest('[data-attention-context]');
  if (context) { openAttentionReader(doc.attention.find(item => item.id === context.dataset.attentionContext)); return; }
  const focus = t.closest('.fswitch button');
  if (focus) { setFocus(focus.closest('.plate').dataset.project, focus.dataset.focus); return; }
  const entry = t.closest('[data-enter]');
  if (entry) { enter(Number(entry.dataset.enter)); return; }
  const lamp = t.closest('.floor-lantern');
  if (lamp) {
    const place = lamp.dataset.place;
    if (place === 'lobby') showView('deck'); else if (place === 'store') openStorehouse(); else enter(Number(place));
    return;
  }
  const handle = t.closest('[data-shutter]');
  if (handle) { shutter(handle.dataset.shutter); return; }
  if (t.closest('[data-storehouse]')) { openStorehouse(); return; }
  if (t.closest('[data-close-dialog]') || t.closest('[data-cancel]')) { closeDialogs(); renderUi(); return; }
  const open = t.closest('[data-open-crate]');
  if (open) { showView('crate', open.closest('.crate').dataset.project); return; }
  const restore = t.closest('[data-restore]');
  if (restore) {
    const project = restore.closest('.crate').dataset.project;
    if (lobby.full) { vacancy = { kind: 'restore', project }; renderUi(); return; }
    await act(restore, () => post('/api/restore', { project }));
    return;
  }
  const clear = t.closest('[data-clear]');
  if (clear) {
    const v = vacancy, shutterId = clear.dataset.clear;
    await act(clear, () => v.kind === 'restore' ? post('/api/restore', { project: v.project, shutter: shutterId })
      : post('/api/move-in', { hosts: v.hosts, label: v.label, shutter: shutterId }), () => { vacancy = null; storehouseOpen = false; renderUi(); });
    return;
  }
  const link = t.closest('[data-link]');
  if (link) {
    const m = moving;
    await act(link, () => post('/api/link', { project: link.dataset.link, hosts: [...m.chosen], label: m.label }), () => { moving = null; renderUi(); });
    return;
  }
  const fresh = t.closest('[data-new-project]');
  if (fresh) { await newProject(fresh, moving.label, [...moving.chosen]); return; }
  const mergeHandle = t.closest('[data-merge]');
  if (mergeHandle) { closeDialogs(); merging = { project: mergeHandle.dataset.merge, other: null }; renderUi(); return; }
  const mergeWith = t.closest('[data-merge-with]');
  if (mergeWith) { merging.other = mergeWith.dataset.mergeWith; renderUi(); return; }
  const keep = t.closest('[data-merge-keep]');
  if (keep) { await merge(keep, keep.dataset.mergeKeep, [merging.project, merging.other].find(id => id !== keep.dataset.mergeKeep)); return; }
  const b = t.closest('[data-move-in]');
  if (!b) return;
  const row = b.closest('.visitor');
  await moveIn(b, row.dataset.label, row.dataset.hosts.split(' '));
});
ui.addEventListener('change', ev => {   // the hosts a move-in covers, kept across redraws
  const pick = ev.target.closest('[data-host-pick]');
  if (!pick || !moving) return;
  if (pick.checked) moving.chosen.add(pick.dataset.hostPick); else moving.chosen.delete(pick.dataset.hostPick);
  renderUi();
});
// run a change from a button: disabled while it runs, and a short note beside it if it fails
async function act(button, change, done = () => {}) {
  button.disabled = true;
  try {
    await change();
    done();
  } catch (err) {
    button.disabled = false;
    const note = button.closest('li')?.querySelector('.err') || button.closest('[role=dialog]')?.querySelector(':scope > .err');
    if (note) { note.textContent = 'Failed'; note.title = err.message; }
  }
}

// ------------------------------------------------------------------ shuttering, with a few seconds to undo
const toast = document.getElementById('toast');
const UNDO_S = 6;
let toastTimer = null;
async function shutter(projectId) {
  const f = floors.find(x => x.projectId === projectId);
  try {
    await post('/api/shutter', { project: projectId });
  } catch (err) {
    showToast(`Couldn’t shutter ${f?.name ?? projectId}: ${err.message}`);
    return;
  }
  showToast(`${f.name} is packed away in the storehouse. Floor ${f.floor} is free.`, async () => {
    try { await post('/api/restore', { project: projectId }); hideToast(); }
    catch (err) { showToast(`Couldn’t undo: ${err.message}`); }
  });
}
export function showToast(text, undo = null) {
  clearTimeout(toastTimer);
  toast.innerHTML = `<span>${esc(text)}</span>${undo ? '<button data-undo>Undo</button>' : ''}<i style="animation-duration:${UNDO_S}s"></i>`;
  toast.hidden = false;
  toast.onclick = ev => { if (undo && ev.target.closest('[data-undo]')) { ev.target.disabled = true; undo(); } };
  toastTimer = setTimeout(hideToast, UNDO_S * 1000);
}
function hideToast() { clearTimeout(toastTimer); toast.hidden = true; toast.onclick = null; }

// ------------------------------------------------------------------ the storehouse, and the "No vacancies" prompt
let storehouseOpen = false, vacancy = null;   // vacancy: what is waiting for a floor, { kind: 'move-in' | 'restore', … }
function openStorehouse() {
  if (!buildingShown) showView('building');
  vacancy = null;
  storehouseOpen = true;
  renderUi();
}
function closeDialogs() { storehouseOpen = false; vacancy = null; moving = null; merging = null; }
function storehouseHtml() {
  const needs = new Set(doc.attention_display.front_desk.map(id => doc.attention.find(item => item.id === id).project_id));
  const crateHtml = c => `<li class="crate" data-project="${esc(c.id)}" title="${esc(c.name)}">
      <b>${esc(plateName(c.name))}</b>${needs.has(c.id) ? '<i class="lift-lantern" data-state="open" aria-label="needs you"></i>' : ''}
      ${c.floor ? `<span class="was" title="Left floor ${c.floor}">${c.floor}</span>` : ''}
      ${c.runs ? `<span class="runs${c.active ? ' busy' : ''}" title="Runs still finishing: ${c.active} working">${c.runs} run${c.runs === 1 ? '' : 's'}</span>` : ''}
      <span class="acts"><button data-open-crate>Open</button><button data-restore>Restore</button></span><small class="err"></small></li>`;
  return `<div class="storehouse" role="dialog" aria-label="Storehouse">
      <div class="sh-head"><h3>Storehouse</h3><button data-close-dialog aria-label="Close">✕</button></div>
      ${crates.length ? `<ul class="crates">${crates.map(crateHtml).join('')}</ul>` : '<p class="none">Empty</p>'}
    </div>`;
}
// When the building is full, the only ways on are clearing a floor or cancelling: capacity is a setting, never offered here.
function vacancyHtml() {
  const occupied = [...floors].reverse().filter(f => f.projectId);
  return `<div class="vacancy" role="dialog" aria-label="No vacancies">
      <h3>The building’s full.</h3><p>Which floor should we clear?</p>
      <ul>${occupied.map(f => `<li><button data-clear="${esc(f.projectId)}" title="Shutter ${esc(f.name)}"><span class="fn">${f.floor}</span>${esc(plateName(f.name))}</button></li>`).join('')}</ul>
      <button data-cancel>Cancel</button><small class="err"></small>
    </div>`;
}

// ------------------------------------------------------------------ moving in: link to a project it belongs to, or a new one
// A label may belong to a project already: linked on another host, the same repository, or the same name. Those come
// first as "Link to …", which takes no floor; a new project comes second. Names alone never link anything by
// themselves (ADR 0001): the user picks. A label on several hosts also asks, so joining them is a choice too.
let moving = null;    // { label, hosts, chosen: Set of hosts, candidates, errors }
let merging = null;   // { project, other: the project picked to merge with, or null }
const WHY = { linked: 'linked on another host', repository: 'same repository', name: 'same name' };
async function moveIn(button, label, hosts) {
  let options;
  await act(button, async () => {
    const query = new URLSearchParams([['label', label], ...hosts.map(h => ['host', h])]);
    const res = await fetch(`/api/move-in?${query}`);
    options = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(options.error || `HTTP ${res.status}`);
  });
  if (!options?.candidates) return;
  button.disabled = false;
  if (!options.candidates.length && hosts.length === 1 && !options.errors.length) { await newProject(button, label, hosts); return; }
  closeDialogs();
  moving = { label, hosts, chosen: new Set(hosts), candidates: options.candidates, errors: options.errors };
  renderUi();
}
async function newProject(button, label, hosts) {
  if (lobby.full) { closeDialogs(); vacancy = { kind: 'move-in', label, hosts }; renderUi(); return; }
  await act(button, () => post('/api/move-in', { hosts, label }), () => { moving = null; renderUi(); });
}
function whereIs(c) {
  return c.floor ? `floor ${c.floor}` : c.shuttered ? 'in the storehouse' : 'no floor';
}
function moveInHtml(m) {
  const none = !m.chosen.size ? ' disabled' : '';
  return `<div class="movein" role="dialog" aria-label="Move ${esc(m.label)} in">
      <h3>Move ${esc(lobby.labels[m.label] || m.label)} in</h3>
      ${m.hosts.length > 1 ? `<p>From these hosts:</p><ul class="mhosts">${m.hosts.map(h => `<li><label><input type="checkbox" data-host-pick="${esc(h)}"${
        m.chosen.has(h) ? ' checked' : ''}><i style="background:${hostLook(h).color}"></i>${esc(h)}</label></li>`).join('')}</ul>` : ''}
      ${m.candidates.length ? `<p>It may belong to a project you have:</p><ul class="links">${m.candidates.map(c => `<li><button data-link="${esc(c.project_id)}"${none}
        title="${esc(c.reasons.map(r => WHY[r]).join(', '))}">Link to ${esc(c.name)} <span class="fn">(${whereIs(c)})</span><small class="why">${
        esc(c.reasons.map(r => WHY[r]).join(' · '))}</small></button></li>`).join('')}</ul>` : ''}
      ${m.errors.map(e => `<p class="note" title="${esc(e)}">Couldn’t check repositories on ${esc(e.split(':')[0])}.</p>`).join('')}
      <div class="acts"><button data-new-project${none}>New project</button><button data-cancel>Cancel</button></div><small class="err"></small>
    </div>`;
}

// ------------------------------------------------------------------ merging a project registered twice by mistake
// From a floor's ⇄: pick the other project, then merge. The older keeps its ID and name and takes the other's links and
// repositories; the other's floor is freed. Projects registered before ages were recorded can't be told apart by age,
// so then the user says which stays.
async function merge(button, keep, other) {
  const [kept, gone] = [keep, other].map(id => doc.projects.find(p => p.id === id));
  const freed = floors.find(f => f.projectId === other)?.floor;
  await act(button, () => post('/api/merge', { keep, other }), () => {
    merging = null;
    renderUi();
    showToast(`${gone.name} is merged into ${kept.name}.${freed ? ` Floor ${freed} is free.` : ''}`);
  });
}
function mergeHtml(m) {
  const projects = new Map((doc?.projects || []).map(p => [p.id, p]));
  const a = projects.get(m.project), b = m.other && projects.get(m.other);
  if (!a) return '';
  const place = id => { const f = floors.find(x => x.projectId === id); return f ? `floor ${f.floor}` : id in (doc.building?.shuttered || {}) ? 'in the storehouse' : 'no floor'; };
  let body;
  if (!b) {
    const others = [...projects.values()].filter(p => p.id !== a.id).sort((x, y) => x.name.localeCompare(y.name));
    body = `<p>Which project is the same work?</p>${others.length ? `<ul>${others.map(p => `<li><button data-merge-with="${esc(p.id)}">${
      esc(p.name)} <span class="fn">(${place(p.id)})</span></button></li>`).join('')}</ul>` : '<p class="none">No other projects</p>'}`;
  } else if (a.created_at != null && b.created_at != null) {
    const [older, newer] = a.created_at <= b.created_at ? [a, b] : [b, a];
    body = `<p>${esc(older.name)} is older, so it stays; ${esc(newer.name)}’s hosts and repositories join it${
      floors.some(f => f.projectId === newer.id) ? ` and ${place(newer.id)} is freed` : ''}.</p>
      <div class="acts"><button data-merge-keep="${esc(older.id)}">Merge</button></div>`;
  } else {
    body = `<p>Which one should stay? The other’s hosts and repositories join it, and its floor is freed.</p>
      <ul>${[a, b].map(p => `<li><button data-merge-keep="${esc(p.id)}">Keep ${esc(p.name)} <span class="fn">(${place(p.id)})</span></button></li>`).join('')}</ul>`;
  }
  return `<div class="merge" role="dialog" aria-label="Merge ${esc(a.name)}">
      <h3>Merge ${esc(a.name)}</h3>${body}<div class="acts"><button data-cancel>Cancel</button></div><small class="err"></small>
    </div>`;
}

// ------------------------------------------------------------------ the live controls, on the manifest's slots
// Plates on the spine, each carrying its progress ring and focus switch (with ⇄) at their own slots; the shutter handle
// on the slab's edge; lanterns on brackets beside the plates, over the front desk and at the storehouse door; the host
// key's dots on the lobby wall; the storehouse's sign over the annex; and the lobby's list beside the building.
const LANTERN_DROP = 27;   // from a lantern's bracket down to its diamond's middle (deck.css .lantern-hang)
const PIVOT = 17;          // a hanging lantern's cord, from the left of its box (deck.css .lantern-hang .bob)
const PLATE_MAX = 190;     // a name plate's widest: three words (the lobby's list keeps clear of it, LOBBY_ROOM)
const px = v => `${Math.round(v)}px`;
const moveTo = (el, x, y, then = '') => { el.style.transform = `translate(${px(x)},${px(y)})${then}`; };
function placeUi() {
  if (!view) return;
  const z = view.z;
  ui.style.setProperty('--z', z.toFixed(3));
  for (const el of ui.querySelectorAll('.plate')) {
    const level = Number(el.dataset.floor), p = slotAt('spine', level, 'plate'), [w, h] = boxOf('spine', 'plate');
    const ring = slotAt('spine', level, 'ring'), [rd] = boxOf('spine', 'ring');
    const sw = slotAt('spine', level, 'focus_switch'), [fw, sh] = boxOf('spine', 'focus_switch');
    if (vw < 760) {   // a phone: no room beside the spine, so the plate sits just inside its floor, controls in a row
      Object.assign(el.style, { height: '', minWidth: '', maxWidth: px(vw - view.ox - MARGIN - 6) });
      for (const child of el.querySelectorAll('.progress,.fswitch,.merge-handle')) child.removeAttribute('style');
      moveTo(el, view.ox + 4, p.y, ' translate(0,-50%)');
      continue;
    }
    // right-aligned to its slot (short of the ring), the plate grows to the left past the spine's edge for a longer
    // name, as l0's do
    const right = Math.min(p.x + w / 2, ring.x - rd / 2 - 3), top = p.y - h / 2;
    Object.assign(el.style, { height: px(h), minWidth: px(w), maxWidth: px(PLATE_MAX) });
    const left = right - el.offsetWidth;
    moveTo(el, left, top);
    const at = (child, x, y, width, height) => child && Object.assign(child.style,
      { left: px(x - left), top: px(y - top), ...(width ? { width: px(width) } : {}), ...(height ? { height: px(height) } : {}) });
    at(el.querySelector('.progress'), ring.x - rd / 2, ring.y - rd / 2, rd, rd);
    const fs = el.querySelector('.fswitch');
    at(fs, sw.x - fw / 2, sw.y - sh / 2, 0, sh);
    if (fs) at(el.querySelector('.merge-handle'), sw.x - fw / 2 + fs.offsetWidth + 3, sw.y - sh / 2, 0, sh);
  }
  for (const el of ui.querySelectorAll('.shutter-handle')) {
    const f = floors.find(x => x.floor === Number(el.dataset.floor)), p = slotAt(floorPiece(f), f.floor, 'shutter_handle');
    moveTo(el, p.x, p.y, ' translate(-50%,-50%)');
  }
  const annex = `annex-${cratesShown()}`;
  for (const el of ui.querySelectorAll('.lantern-hang')) {
    const place = el.dataset.floor;
    if (place === 'lobby' || place === 'store') {   // on a cord from the ceiling over the desk, or the annex's lintel
      const p = place === 'lobby' ? slotAt('lobby', 0, 'desk_lantern') : slotAt(annex, 0, 'door_lantern');
      moveTo(el, p.x - PIVOT, p.y);
      continue;
    }
    // a floor's bracket meets its plate's left edge, the diamond level with the plate
    const plate = ui.querySelector(`.plate[data-floor="${place}"]`).getBoundingClientRect();
    moveTo(el, Math.max(2, plate.left - el.offsetWidth), (plate.top + plate.bottom) / 2 - LANTERN_DROP);
  }
  const dots = ui.querySelector('.hostdots');
  if (dots) { const p = slotAt('lobby', 0, 'host_key'); moveTo(dots, p.x, p.y, ' translate(-50%,-50%)'); }
  const sign = ui.querySelector('.annex-sign');
  if (sign) {   // over the annex's open front, kept on screen
    const door = slotAt(annex, 0, 'storehouse_door'), top = pieceRect(annex, 0).top, half = sign.offsetWidth / 2;
    moveTo(sign, Math.min(Math.max(door.x, MARGIN + half), vw - MARGIN - half), Math.max(top, TOP_UI + sign.offsetHeight), ' translate(-50%,-100%)');
  }
  const lob = ui.querySelector('.lobby');
  if (lob) {
    if (vw < 760) {   // a phone: across the screen below the building
      Object.assign(lob.style, { left: px(MARGIN), top: px(vh - MARGIN - NARROW_LOBBY_H + 8), width: px(vw - 2 * MARGIN), maxHeight: px(NARROW_LOBBY_H - 8) });
      return;
    }
    // beside the building at the lobby's level, left of the spine: it covers no floor
    const top = storeyRect(0).top, spine = pieceRect('spine-lobby', 0).left;
    Object.assign(lob.style, { left: px(MARGIN), top: px(top), width: px(spine - MARGIN - 12), maxHeight: px(vh - top - MARGIN) });
  }
}

// ------------------------------------------------------------------ the lift panel: the way around once inside
// One button per floor, top floor first, then L (the whole building) and S (the storehouse). The current floor, or S
// while a crate is open, is lit; a floor whose project has work running glows in the run colour; a place with
// attention shows the lantern's diamond on its button.
function renderLift() {
  if (current === null) { lift.innerHTML = ''; return; }
  const button = f => {
    const l = lanterns.get(f.floor);
    const label = f.projectId ? `${f.floor}: ${f.name}${f.active ? ' · work running' : ''}${l ? ` · ${l.count > 1 ? `${l.count} things need you` : 'something needs you'}` : ''}` : `${f.floor}: to let`;
    return `<button data-lift="${f.floor}"${f.floor === current ? ' aria-current="true"' : ''}${f.active ? ' data-active' : ''}${f.projectId ? '' : ' disabled'}
      aria-label="${esc(label)}" title="${esc(label)}">${f.floor}${l ? `<i class="lift-lantern" data-glyph="${l.glyph}" data-state="${l.level}" aria-hidden="true"></i>` : ''}</button>`;
  };
  const storeLamp = lanterns.get('store');
  lift.innerHTML = [...floors].reverse().map(button).join('')
    + '<button data-lift="L" aria-label="Lobby: the whole building" title="Lobby: the whole building">L</button>'
    + `<button data-lift="S"${current === 'S' ? ' aria-current="true"' : ''} aria-label="Storehouse" title="Storehouse">S${
      storeLamp ? `<i class="lift-lantern" data-glyph="${storeLamp.glyph}" data-state="${storeLamp.level}" aria-hidden="true"></i>` : ''}</button>`;
}
lift.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-lift]');
  if (!b || b.disabled) return;
  if (b.dataset.lift === 'L') showView('building');
  else if (b.dataset.lift === 'S') openStorehouse();
  else enter(Number(b.dataset.lift));
});

// ------------------------------------------------------------------ a click on a floor goes in; clicks never set focus
// A floor is hit anywhere on its front, between its slab's edge and the storey above (the edge descends to the right);
// the storehouse anywhere on its annex.
function hitAt(ev) {   // { floor } or { store }, or null
  if (!view) return null;
  const x = ev.clientX, y = ev.clientY;
  for (const f of floors) {
    if (!f.projectId) continue;
    const r = storeyRect(f.floor);
    if (x < r.left || x > r.right) continue;
    const edge = levelY(f.floor) + (x - r.left) / (r.right - r.left) * (r.bottom - levelY(f.floor));
    if (y <= edge && y >= edge - (levelY(f.floor) - r.top)) return { floor: f.floor };
  }
  const a = pieceRect(`annex-${cratesShown()}`, 0);
  return x >= a.left && x <= a.left + a.width && y >= a.top && y <= a.top + a.height ? { store: true } : null;
}
let downAt = null;
canvas.addEventListener('pointerdown', ev => { downAt = { x: ev.clientX, y: ev.clientY }; });
canvas.addEventListener('pointerup', ev => {
  if (downAt && Math.hypot(ev.clientX - downAt.x, ev.clientY - downAt.y) < 5) {
    const hit = hitAt(ev);
    if (hit?.store) openStorehouse(); else if (hit?.floor) enter(hit.floor);
  }
  downAt = null;
});
canvas.addEventListener('pointermove', ev => { canvas.style.cursor = hitAt(ev) ? 'pointer' : ''; });

// read-only probe for browser tests: the building is pictures on a canvas, so the tests ask it what it drew and where
export const lanternState = () => [...lanterns].map(([place, l]) => ({ place, level: l.level, count: l.count,
  kind: l.kind, glyph: l.glyph, swings: swings.get(place) || 0,
  swinging: l.swingFrom !== null && animationNow() / 1000 - l.swingFrom < SWING_S }));
window.fleetBuilding = Object.freeze({
  floors: () => floors.map(f => ({ floor: f.floor, project: f.projectId, name: f.name ?? null, mode: f.mode, active: f.active,
    rooms: (f.rooms || []).map(r => ({ label: r.label, theme: r.theme, active: r.active })),
    built: built.get(f.floor) ? { ...built.get(f.floor) } : null, screen: view ? storeyRect(f.floor) : null })),
  lobby: () => ({ screen: view ? storeyRect(0) : null, hosts: lobby?.hosts ?? [], visitors: lobby?.visitors ?? [] }),
  lanterns: lanternState,
  current: () => current,
  crates: () => crates.map(c => ({ ...c })),
  cratesDrawn: () => (manifest ? cratesShown() : 0),
  zoom: () => view?.z ?? null,
  pieces: () => (view ? stack().map(([name, level]) => ({ name, level })) : []),
});

{ const v = QS.get('view') || store('localStorage', VIEW_KEY); showView(v === 'building' || v === 'world' ? v : 'deck'); }
