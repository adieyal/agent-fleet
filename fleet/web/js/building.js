// The building (L0): every registered project on its own floor of a building seen in cross-section, with the lobby on
// the ground floor. A view beside the deck, chosen with the header's deck | building switch and remembered.
//
// Floors come from the server (fleet/building.py): capacity, and which floor each project holds. A floor never moves
// or resizes; only what is on it changes. Priority floors are open (no front wall, a pennant on the edge): one furnished
// room per project room on the deck, lit warm only where runs are working. Background floors show cool glass, quieter
// than any open floor, with a soft warm glow behind a few panes while active. Free floors have a small "To let" card in
// one window and a dotted outline. The spine carries a name plate (at most three words) and a progress ring per floor, shown
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
// The building has its own canvas and renderer, drawn on demand (it is still unless the state changes or a lantern
// swings), so the deck's frame loop and input stay as they were; it reuses the deck's furniture, lighting and palette.

import * as THREE from 'three';
import { animationNow, isStepping } from './clock.js';
import { DESK_TOP, HALF, PI, QS, REDUCED, vh, vw } from './env.js';
import { esc, mix, store } from './util.js';
import { THEMES, hostLook, projectLook, themeFor } from './looks.js';
import { working } from './activity.js';
import { G, KIT, Placer, canvasTex, softDot } from './scene.js';
import { enterProject } from './state.js';
import { openAttentionReader } from './reader.js';

// ------------------------------------------------------------------ views: the deck, the building (L0), a floor (L1)
// Inside, `current` is the floor entered, or 'S' with `crate` the project whose crate is open (read-only).
const VIEW_KEY = 'fleet.view';
export let buildingShown = false;
let current = null, crate = null;
const toggle = document.getElementById('viewToggle');
const lift = document.getElementById('lift');
function showView(view, where = null) {
  const leaving = current !== null;
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
  if (!buildingShown) closeDialogs();
  renderLift();
  draw();
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

// ------------------------------------------------------------------ dimensions (tiles, as on the deck)
const FW = 26, FD = 6, FH = 3, SLAB = 0.3;   // floor width, depth and storey height: shallow, so ten fit a laptop screen
const LH = 4.2;                              // the lobby is a little taller
const SPINE = 3.2;                           // the column on the left that carries the name plates
const MAX_Z = 40;                            // pixels per tile at most, so a small building doesn't balloon
const WARM = '#ffc47a';
const FLOOR_LIFT = 0.01;                     // a floor covering's centre above the slab: its top clears the slab's, no flicker
const baseOf = floor => LH + (floor - 1) * FH;

// ------------------------------------------------------------------ renderer, camera, lights
const canvas = document.getElementById('buildingCanvas');
const ui = document.getElementById('buildingUi');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.setClearColor(0x000000, 0);

const scene = new THREE.Scene();
const group = new THREE.Group();
scene.add(group);
scene.add(new THREE.HemisphereLight(0xb4c8ff, 0x2a2238, 1.35));
const sun = new THREE.DirectionalLight(0xfff0dc, 2.2);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.02;
scene.add(sun, sun.target);
const rim = new THREE.DirectionalLight(0x7fa2ff, 0.6);
rim.position.set(-20, 10, -10);
scene.add(rim);

// Close to a straight-on cross-section: a little from above and to the right, so floors read as shallow trays.
const VIEW = new THREE.Vector3(0.2, 0.26, 1).normalize();
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 400);
let zoom = 20;

// ------------------------------------------------------------------ the floors, from the state document
let doc = null, floors = [], lobby = null, crates = [], buildKey = '';
export function applyBuilding(state) {
  doc = state;
  for (const [id, focus] of pending) if (state.building?.focus[id] === focus) pending.delete(id);
  floors = floorsOf(state);
  lobby = lobbyOf(state);
  crates = cratesOf(state);
  applyLanterns(state);
  const key = JSON.stringify([floors.map(f => [f.floor, f.projectId, f.name, f.mode, f.active, f.rooms?.map(r => [r.label, r.active])]),
    lobby.hosts, [...lanterns.keys()].filter(place => typeof place !== 'number'), crates.length]);
  if (key !== buildKey && KIT.desk) { buildKey = key; build(); }
  renderUi();
  const gone = crate ? !crates.some(c => c.id === crate) : typeof current === 'number' && !floors.find(f => f.floor === current)?.projectId;
  if (gone) showView('building');   // the floor was shuttered, or the crate moved back in
  else renderLift();
  draw();
}
export function buildingReady() {   // the deck's models have loaded
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

// ------------------------------------------------------------------ building the scene
const disposables = [];
const mat = {
  slab: new THREE.MeshStandardMaterial({ color: 0x5b6780, roughness: 0.8, metalness: 0.2 }),
  spine: new THREE.MeshStandardMaterial({ color: 0x46536d, roughness: 0.7, metalness: 0.3 }),
  ground: new THREE.MeshStandardMaterial({ color: 0x0f1726, roughness: 0.9, metalness: 0.2 }),
};
const warmGlow = softDot('rgba(255,196,122,.55)', 'rgba(255,196,122,0)');
const built = new Map();   // floor → what was put there, for the probe

function box(w, h, d, x, y, z, material, shadow = true) {
  const m = new THREE.Mesh(G.box, material);
  m.scale.set(w, h, d); m.position.set(x, y, z);
  m.castShadow = shadow; m.receiveShadow = true;
  group.add(m);
  return m;
}
function tinted(color) {
  const m = new THREE.MeshStandardMaterial({ color, roughness: 0.85 });
  disposables.push(m);
  return m;
}

function build() {
  for (const d of disposables.splice(0)) d.dispose();
  group.clear();
  built.clear();
  const placer = new Placer();
  const top = baseOf(floors.length + 1);
  // ground plate, spine and roof
  box(AX1 + SPINE + 4, 0.5, FD + 5, (AX1 - SPINE) / 2, -0.25, FD / 2 + 1, mat.ground, false);
  box(SPINE, top + 0.6, FD, -SPINE / 2, (top + 0.6) / 2, FD / 2, mat.spine);
  box(FW + SPINE + 0.4, 0.5, FD + 0.4, (FW - SPINE) / 2, top + 0.25, FD / 2, mat.slab);
  box(0.3, top, FD, FW + 0.15, top / 2, FD / 2, mat.spine);   // the right-hand end wall
  buildLobby(placer);
  buildStorehouse();
  for (const f of floors) buildFloor(f, placer);
  placer.build(group, disposables);
  lanternObjs.clear();
  for (const place of lanterns.keys()) if (typeof place !== 'number') lanternObjs.set(place, buildLantern(place));
  hits.length = 0;
  const hit = (w, h, d, x, y, z, what) => {   // what a click lands on
    const m = box(w, h, d, x, y, z, HIT, false);
    m.receiveShadow = false;
    Object.assign(m.userData, what);
    hits.push(m);
  };
  for (const f of floors) if (f.projectId) hit(FW + SPINE, FH, FD, (FW - SPINE) / 2, baseOf(f.floor) + FH / 2, FD / 2, { floor: f.floor });
  hit(AW, AH, FD, (AX0 + AX1) / 2, AH / 2, FD / 2, { store: true });
  const mid = new THREE.Vector3((AX1 - SPINE) / 2, top / 2, FD / 2);
  sun.target.position.copy(mid);
  sun.position.copy(mid).add(new THREE.Vector3(-10, 12, 36));   // low and from the front, so it reaches into every floor
  const sc = sun.shadow.camera, rad = Math.max(AX1 + SPINE, top) * 0.8;
  sc.left = -rad; sc.right = rad; sc.top = rad; sc.bottom = -rad; sc.near = 1; sc.far = 120;
  sc.updateProjectionMatrix();
  fit();
}

function buildFloor(f, place) {
  const y = baseOf(f.floor), h = FH - SLAB;
  const look = f.look;
  box(FW, SLAB, FD, FW / 2, y + SLAB / 2, FD / 2, mat.slab);
  box(FW, h, 0.2, FW / 2, y + SLAB + h / 2, 0.1, tinted(look ? look.wall : '#2a3446'));   // back wall
  const record = { mode: f.mode, active: f.active, front: f.mode === 'open' ? 'none' : f.mode, furniture: 0, pennant: false,
    glow: false, rooms: [] };
  built.set(f.floor, record);
  if (f.mode === 'open') {
    furnishRooms(place, y + SLAB, f, record);
    // the pennant: flown from a short pole on the floor's outer edge, in the project's colour
    box(1.3, 0.07, 0.07, FW + 0.95, y + FH - 0.35, FD - 0.3, mat.spine);
    const flag = new THREE.Mesh(PENNANT, new THREE.MeshBasicMaterial({ color: look.accent, side: THREE.DoubleSide }));
    disposables.push(flag.material);
    flag.position.set(FW + 1.5, y + FH - 0.35, FD - 0.3);
    group.add(flag);
    record.pennant = true;
  } else {
    facade(f, y);
    record.glow = f.mode === 'windowed' && f.active;
  }
}
// Floor lanterns are drawn over the scene beside their name plates (placeUi). The lobby's hangs over the front desk
// and the storehouse's from an arm over its door; each diamond is drawn over its cord's end, with a modest glow.
const CORD = 0.4, FRONT_DESK_X = 6.8;
const lanternObjs = new Map();   // 'lobby' | 'store' → { pivot, lamp, glow }
const lanternGlow = softDot('rgba(255,79,176,.45)', 'rgba(255,79,176,0)');
function buildLantern(place) {
  const pivot = new THREE.Group();
  let cordLength = CORD;
  if (place === 'lobby') {
    pivot.position.set(FRONT_DESK_X, LH - 0.05, FD - 1.6);
    cordLength = 0.9;
  } else {
    const y = AH - 0.1, x = AX0 + 1.7;
    box(0.06, 0.06, 1.1, x, y, FD + 0.55, mat.spine);
    pivot.position.set(x, y, FD + 1.1);
  }
  const cord = new THREE.Mesh(G.box, mat.spine);
  cord.scale.set(0.03, cordLength, 0.03); cord.position.y = -cordLength / 2;
  const lamp = new THREE.Object3D();
  lamp.position.y = -cordLength - 0.55;
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: lanternGlow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
  disposables.push(glow.material);
  glow.scale.setScalar(1.1);
  lamp.add(glow);
  pivot.add(cord, lamp);
  group.add(pivot);
  return { pivot, lamp, glow };
}
const hits = [];
const HIT = new THREE.MeshBasicMaterial({ visible: false });

// The storehouse: a low annex beside the lobby, open at the front, with a labelled crate per shuttered project stacked
// in rows (twenty fit; more are counted on its sign). Still: in-flight runs are listed inside, not animated here.
const AX0 = FW + 0.3, AW = 11, AX1 = AX0 + AW, AH = LH - 0.4;
const CRATE = 1.5, CRATE_SLOTS = [];
for (const layer of [0, 1]) for (const z of [FD - 1.3, 2.3]) for (let i = 0; i < 5; i++) {
  CRATE_SLOTS.push([AX0 + 1.4 + i * 2.05, SLAB + CRATE / 2 + layer * CRATE, z]);
}
const crateMat = new THREE.MeshStandardMaterial({ color: 0x9c7a4f, roughness: 0.9 });
const labelMat = new THREE.MeshStandardMaterial({ color: 0xe8dcc2, roughness: 0.8 });
function buildStorehouse() {
  box(AW, SLAB, FD, (AX0 + AX1) / 2, SLAB / 2, FD / 2, mat.slab);
  box(AW, AH - SLAB, 0.2, (AX0 + AX1) / 2, (AH + SLAB) / 2, 0.1, tinted('#3d3a44'));   // back wall
  box(0.3, AH, FD, AX1 - 0.15, AH / 2, FD / 2, mat.spine);                              // far wall
  box(AW + 0.3, 0.35, FD + 0.3, (AX0 + AX1) / 2 + 0.15, AH + 0.17, FD / 2, mat.slab);   // roof
  crates.slice(0, CRATE_SLOTS.length).forEach((c, i) => {
    const [x, y, z] = CRATE_SLOTS[i];
    box(CRATE, CRATE, CRATE, x, y, z, crateMat);
    box(CRATE * 0.6, CRATE * 0.28, 0.03, x, y + 0.1, z + CRATE / 2 + 0.02, labelMat, false);   // the label
  });
}
const LAMP_ON = new THREE.MeshBasicMaterial({ color: WARM, toneMapped: false });
const LAMP_OFF = new THREE.MeshStandardMaterial({ color: 0x3a4458, roughness: 0.6 });
const PENNANT = (() => {
  const s = new THREE.Shape();
  s.moveTo(-0.5, 0); s.lineTo(0.5, 0); s.lineTo(0, -1.6); s.lineTo(-0.5, 0);   // hangs down from the pole
  return new THREE.ShapeGeometry(s);
})();

// An open floor: one room per project room on the deck, side by side behind low partitions, each furnished as that room
// is on the deck (its floor, wall colour and theme furniture), with warm light only in rooms where runs are working.
// A project with no work yet has an empty floor.
function furnishRooms(place, y, f, record) {
  const x0 = 0.3, width = FW - 0.6, n = f.rooms.length;
  if (!n) {
    place.add('floor:checker', FW / 2, FD / 2, 0, y + FLOOR_LIFT, new THREE.Vector3(FW, 0.04, FD), f.look.floor);
    return;
  }
  const w = width / n, h = FH - SLAB;
  f.rooms.forEach((room, i) => {
    const a = x0 + i * w, b = a + w, cx = (a + b) / 2, T = THEMES[room.theme], look = room.look;
    let furniture = 0;
    const at = (model, x, z, rot = 0, lift = 0) => {
      if (!model || model === 'rack' || !KIT[model]) return;
      place.add(model, x, z, rot, y + lift);
      furniture++;
    };
    place.add('floor:' + T.floor, cx, FD / 2, 0, y + FLOOR_LIFT, new THREE.Vector3(w, 0.04, FD), look.floor);
    box(w, h, 0.06, cx, y + h / 2, 0.23, tinted(look.wall), false);                                   // its back wall
    box(w - 0.4, 0.05, 0.05, cx, y + h - 0.25, 0.28, tinted(look.accent), false);                     // accent strip
    if (i > 0) box(0.12, h * 0.55, FD - 1.2, a, y + h * 0.275, (FD - 1.2) / 2 + 0.2, tinted(mix(look.wall, '#0b111d', 0.3)));
    // desks along the back wall, as many as fit; then the theme's back piece, lamp, sofa and plant where there's room
    const desks = Math.max(1, Math.min(4, Math.floor((w - 3.2) / 2.4)));
    for (let k = 0; k < desks; k++) {
      const x = a + 1.9 + k * 2.4;
      at('desk', x, 1.6); at('computerScreen', x, 1.4, 0, DESK_TOP); at('chairDesk', x, 2.65);
    }
    at(T.roles.back, a + 0.8, 0.5);
    if (w > 7) at(T.roles.sofa, b - 1.8, 1.0);
    if (w > 9) at(T.roles.lamp, b - 3.4, 0.6);
    at(T.roles.plant, b - 0.7, FD - 0.9);
    // light: warm where runs are working, off elsewhere
    box(w - 1.2, 0.06, 0.3, cx, y + h - 0.05, FD / 2, room.active ? LAMP_ON : LAMP_OFF, false);
    if (room.active) {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: warmGlow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.95 }));
      disposables.push(s.material);
      s.scale.set(Math.min(w * 0.9, 14), 3.6, 1); s.position.set(cx, y + 1.2, FD / 2 + 0.6);
      group.add(s);
      record.glow = true;
    }
    record.furniture += furniture;
    record.rooms.push({ label: room.label, theme: room.theme, active: room.active, furniture });
  });
}

// A windowed facade: cool neutral glass, quieter than any open floor. While runs are active a soft warm glow shows
// behind a few panes, never the whole front: a busy background floor must not look important. A free floor's front
// has a small "To let" card in one window and a dotted outline for the capacity it stands for.
const WARM_PANES = [2, 6, 10];
function facade(f, y) {
  const h = FH - SLAB, { c, g, tex } = canvasTex(1024, Math.round(1024 * h / FW));
  disposables.push(tex);
  const W = c.width, H = c.height, cols = 13, pw = W / cols;
  g.fillStyle = '#343c4a'; g.fillRect(0, 0, W, H);
  for (let k = 0; k < cols; k++) {
    const x = k * pw + 4, w = pw - 8;
    const glass = g.createLinearGradient(0, 6, 0, H - 6);
    glass.addColorStop(0, '#2c333e'); glass.addColorStop(1, '#1b2029');
    g.fillStyle = glass; g.fillRect(x, 6, w, H - 12);
    if (f.mode === 'windowed' && f.active && WARM_PANES.includes(k)) {
      const glow = g.createRadialGradient(x + w / 2, H * 0.62, 2, x + w / 2, H * 0.62, w * 0.75);
      glow.addColorStop(0, 'rgba(255,196,130,.42)'); glow.addColorStop(1, 'rgba(255,196,130,0)');
      g.fillStyle = glow; g.fillRect(x, 6, w, H - 12);
    }
    g.fillStyle = 'rgba(255,255,255,.06)'; g.fillRect(x + w * 0.15, 6, w * 0.12, H - 12);   // a reflection
    g.fillStyle = '#343c4a'; g.fillRect(x, H * 0.36, w, 3);                               // transom
  }
  if (f.mode === 'to-let') {
    g.setLineDash([8, 8]); g.lineWidth = 3; g.strokeStyle = 'rgba(163,178,203,.55)';
    g.strokeRect(3, 3, W - 6, H - 6);
    g.setLineDash([]);
    const k = 1, x = k * pw + 4, w = pw - 8, sw = w * 0.84, sh = H * 0.34, sx = x + (w - sw) / 2, sy = H * 0.46;
    g.fillStyle = '#d9dee7'; g.fillRect(sx, sy, sw, sh);
    g.fillStyle = '#2a3446'; g.font = `700 ${Math.round(sh * 0.42)}px Space Grotesk, system-ui, sans-serif`;
    g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText('TO LET', sx + sw / 2, sy + sh / 2 + 1, sw - 6);
  }
  const m = new THREE.Mesh(G.plane, new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  disposables.push(m.material);
  m.scale.set(FW, h, 1); m.position.set(FW / 2, y + SLAB + h / 2, FD);
  group.add(m);
  box(FW, 0.12, 0.3, FW / 2, y + SLAB + 0.06, FD - 0.1, mat.slab);   // sill
}

// The lobby: a reception desk, somewhere to sit, and plants. The host key and visitors are drawn over it.
function buildLobby(place) {
  const h = LH - SLAB;
  box(FW, SLAB, FD, FW / 2, SLAB / 2, FD / 2, mat.slab);
  box(FW, h, 0.2, FW / 2, SLAB + h / 2, 0.1, tinted('#34405a'));
  const at = (model, x, z, rot = 0, lift = 0) => place.add(model, x, z, rot, SLAB + lift);
  place.add('floor:tile', FW / 2, FD / 2, 0, SLAB + FLOOR_LIFT, new THREE.Vector3(FW, 0.04, FD), '#56627a');
  at('desk', 6, 2.4); at('desk', 7.6, 2.4);
  at('computerScreen', 6.8, 2.2, 0, DESK_TOP);
  at('chairDesk', 6.8, 1.2, PI);
  at('loungeSofa', 11.5, 1.1); at('loungeChair', 13.4, 2.0, -HALF * 0.5);
  at('pottedPlant', 1.0, 0.8); at('pottedPlant', 9.4, 0.8); at('pottedPlant', FW - 1, 0.8);
  box(FW, 0.06, 0.3, FW / 2, LH - 0.05, FD / 2, LAMP_ON, false);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: warmGlow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.6 }));
  disposables.push(s.material);
  s.scale.set(16, 4.5, 1); s.position.set(8, SLAB + 1.6, FD / 2 + 0.6);
  group.add(s);
}

// ------------------------------------------------------------------ camera fit: the whole building on one screen
const TOP_UI = 52, MARGIN = 14, NARROW_LOBBY_H = 150, PLATE_ROOM = 310, NARROW_PLATE_ROOM = 20;   // plates and their lanterns
function fit() {
  if (!vw || !floors.length) return;
  const top = baseOf(floors.length + 1) + 0.6;
  const centre = new THREE.Vector3((FW - SPINE) / 2, top / 2, FD / 2);
  camera.position.copy(centre).addScaledVector(VIEW, 100);
  camera.up.set(0, 1, 0);
  camera.lookAt(centre);
  camera.updateMatrixWorld();
  // On a phone the storeys get the width: the name plates sit just inside each floor, the lantern on the spine beside
  // them, and the storehouse annex runs off the right edge (its sign stays on screen); room below for the lobby's list.
  const narrow = vw < 760;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  const v = new THREE.Vector3();
  for (const x of [-SPINE, narrow ? FW + 2.2 : AX1 + 0.4]) for (const y of [-0.5, top]) for (const z of [0, FD + 1.2]) {
    v.set(x, y, z).applyMatrix4(camera.matrixWorldInverse);
    x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x); y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y);
  }
  const l = MARGIN + (narrow ? NARROW_PLATE_ROOM : PLATE_ROOM), r = vw - MARGIN, t = TOP_UI + MARGIN, b = vh - MARGIN - (narrow ? NARROW_LOBBY_H : 0);
  zoom = Math.min(MAX_Z, (r - l) / (x1 - x0), (b - t) / (y1 - y0));
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2, ax = (l + r) / 2;
  const ay = narrow ? b - (my - y0) * zoom : (t + b) / 2;   // on a phone the building stands on the lobby's list
  camera.left = mx - ax / zoom; camera.right = mx + (vw - ax) / zoom;
  camera.top = my + ay / zoom; camera.bottom = my - (vh - ay) / zoom;
  camera.updateProjectionMatrix();
}
let sized = '';
function resize() {
  sized = `${vw}x${vh}`;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(vw, vh, false);
  fit();
}
window.addEventListener('resize', draw);   // (after the deck's own handler has measured the window)

let drawPending = false;
function draw() {
  if (!buildingShown || drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => {
    drawPending = false;
    if (sized !== `${vw}x${vh}`) resize();
    const swinging = stepLanterns(animationNow() / 1000);
    renderer.render(scene, camera);
    placeUi();
    if (swinging && !isStepping()) draw();
  });
}
// one swing on arrival, then still; true while any lantern is still swinging
export { draw as stepBuilding };
function stepLanterns(now) {
  let swinging = false;
  for (const [place, l] of lanterns) {
    const o = lanternObjs.get(place);
    const age = l.swingFrom == null ? SWING_S : now - l.swingFrom;
    const angle = age < SWING_S ? 0.45 * Math.exp(-age * 1.7) * Math.sin(age * 5.5) : 0;
    if (o) {
      o.pivot.rotation.z = angle;
      o.glow.material.opacity = l.level === 'open' ? 1 : 0.3;
    }
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
    const label = `${where}: ${l.count > 1 ? `${l.count} things need you` : 'something needs you'}${l.level === 'acknowledged' ? ' (acknowledged)' : ''}`;
    const lantern = `<button class="floor-lantern${l.level === 'acknowledged' ? ' ack' : ''}" data-place="${place}" data-state="${l.level}"
      data-count="${l.count}" data-kind="${l.kind}" aria-label="${esc(label)}" title="${esc(label)}"><span class="lg">${esc(l.glyph)}</span><b>${l.count > 1 ? l.count : ''}</b></button>`;
    if (typeof place !== 'number') return lantern;
    // a floor's hangs from a bracket on the spine beside its name plate; the one swing picks up where it was on a redraw
    return `<div class="lantern-hang" data-floor="${place}"><span class="bob">${lantern}</span></div>`;
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
  ui.innerHTML = `${plates}${handles}${lamps}${sign}${storehouseOpen ? storehouseHtml() : ''}${vacancy ? vacancyHtml() : ''}${
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
  placeUi();
}
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
function showToast(text, undo = null) {
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

const _v = new THREE.Vector3();
function screenOf(x, y, z) {
  _v.set(x, y, z).project(camera);
  return { x: (_v.x + 1) / 2 * vw, y: (1 - _v.y) / 2 * vh };
}
// a floor's front face on screen: the spine's front edge to the end wall, slab to ceiling
function floorRect(base, height) {
  const r = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
  for (const x of [-SPINE, FW + 0.3]) for (const y of [base, base + height]) {
    const p = screenOf(x, y, FD);
    r.left = Math.min(r.left, p.x); r.right = Math.max(r.right, p.x); r.top = Math.min(r.top, p.y); r.bottom = Math.max(r.bottom, p.y);
  }
  return r;
}
const _w = new THREE.Vector3();
function placeUi() {
  const narrow = vw < 760;
  for (const el of ui.querySelectorAll('.plate')) {   // right-aligned to the spine's front edge, reaching out to the left
    const r = floorRect(baseOf(Number(el.dataset.floor)), FH);
    const p = screenOf(narrow ? 0.2 : -0.15, baseOf(Number(el.dataset.floor)) + FH / 2, FD);
    el.style.transform = `translate(${Math.round(p.x)}px,${Math.round(p.y)}px) translate(${narrow ? '0' : '-100%'},-50%)`;
    el.style.setProperty('--h', `${Math.round(r.bottom - r.top)}px`);
  }
  for (const el of ui.querySelectorAll('.lantern-hang')) {   // its bracket meets the plate's left edge
    const plate = ui.querySelector(`.plate[data-floor="${el.dataset.floor}"]`).getBoundingClientRect();
    const x = Math.max(2, plate.left - el.offsetWidth), y = (plate.top + plate.bottom) / 2 - LANTERN_DROP;
    el.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
  }
  for (const el of ui.querySelectorAll('.floor-lantern')) {
    const o = lanternObjs.get(el.dataset.place);   // the lobby's and the storehouse's hang in the scene
    if (!o) continue;
    o.lamp.getWorldPosition(_w);
    const p = screenOf(_w.x, _w.y, _w.z);
    el.style.transform = `translate(${Math.round(p.x)}px,${Math.round(p.y)}px) translate(-50%,-50%)`;
  }
  for (const el of ui.querySelectorAll('.shutter-handle')) {
    const p = screenOf(FW - 1.1, baseOf(Number(el.dataset.floor)) + FH - 0.45, FD);
    el.style.transform = `translate(${Math.round(p.x)}px,${Math.round(p.y)}px) translate(-50%,-50%)`;
  }
  const sign = ui.querySelector('.annex-sign');
  if (sign) {
    const p = screenOf((AX0 + AX1) / 2, AH + 0.35, FD + 0.15), half = sign.offsetWidth / 2;
    const x = Math.min(Math.max(p.x, MARGIN + half), vw - MARGIN - half);   // kept on screen when the building is small
    sign.style.transform = `translate(${Math.round(x)}px,${Math.round(p.y)}px) translate(-50%,-100%)`;
  }
  const lob = ui.querySelector('.lobby');
  if (lob) {
    if (vw < 760) {   // a phone: across the screen below the building
      Object.assign(lob.style, { left: `${MARGIN}px`, top: `${Math.round(vh - MARGIN - NARROW_LOBBY_H + 8)}px`,
        width: `${vw - 2 * MARGIN}px`, maxHeight: `${NARROW_LOBBY_H - 8}px` });
      return;
    }
    // beside the building at the lobby's level, under the name plates: it covers no floor
    const r = floorRect(0, LH), spine = screenOf(-SPINE, 0, FD).x;
    Object.assign(lob.style, { left: `${MARGIN}px`, top: `${Math.round(r.top)}px`, width: `${Math.round(spine - MARGIN - 12)}px`,
      maxHeight: `${Math.round(vh - r.top - MARGIN)}px` });
  }
}
const LANTERN_DROP = 27;   // from a lantern's bracket down to its diamond's middle (deck.css .lantern-hang)

// ------------------------------------------------------------------ the lift panel: the way around once inside
// One button per floor, top floor first, then L (the whole building) and S (the storehouse). The current floor, or S
// while a crate is open, is lit; a place with attention shows the lantern's diamond on its button.
function renderLift() {
  if (current === null) { lift.innerHTML = ''; return; }
  const button = f => {
    const l = lanterns.get(f.floor);
    const label = f.projectId ? `${f.floor}: ${f.name}${l ? ` · ${l.count > 1 ? `${l.count} things need you` : 'something needs you'}` : ''}` : `${f.floor}: to let`;
    return `<button data-lift="${f.floor}"${f.floor === current ? ' aria-current="true"' : ''}${f.projectId ? '' : ' disabled'}
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
const raycaster = new THREE.Raycaster(), _ndc = new THREE.Vector2();
function hitAt(ev) {   // { floor } or { store }, or null
  _ndc.set(ev.clientX / vw * 2 - 1, -(ev.clientY / vh) * 2 + 1);
  raycaster.setFromCamera(_ndc, camera);
  return raycaster.intersectObjects(hits, false)[0]?.object.userData ?? null;
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

// read-only probe for browser tests: the building lives in WebGL, so the tests ask it what it drew and where
export const lanternState = () => [...lanterns].map(([place, l]) => ({ place, level: l.level, count: l.count,
  kind: l.kind, glyph: l.glyph, swings: swings.get(place) || 0,
  swinging: l.swingFrom !== null && animationNow() / 1000 - l.swingFrom < SWING_S }));
window.fleetBuilding = Object.freeze({
  floors: () => floors.map(f => ({ floor: f.floor, project: f.projectId, name: f.name ?? null, mode: f.mode, active: f.active,
    built: built.get(f.floor) ? { ...built.get(f.floor) } : null, screen: floorRect(baseOf(f.floor), FH) })),
  lobby: () => ({ screen: floorRect(0, LH), hosts: lobby?.hosts ?? [], visitors: lobby?.visitors ?? [] }),
  lanterns: lanternState,
  current: () => current,
  crates: () => crates.map(c => ({ ...c })),
  cratesDrawn: () => Math.min(crates.length, CRATE_SLOTS.length),
  zoom: () => zoom,
});

showView((QS.get('view') || store('localStorage', VIEW_KEY)) === 'building' ? 'building' : 'deck');
