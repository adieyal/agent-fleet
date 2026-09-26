// The building (L0): every registered project on its own floor of a building seen in cross-section, with the lobby on
// the ground floor. A view beside the deck, chosen with the header's deck | building switch and remembered.
//
// Floors come from the server (fleet/building.py): capacity, and which floor each project holds. A floor never moves
// or resizes; only what is on it changes. Priority floors are open (no front wall, furniture on view, a pennant on the
// edge, warm light while runs are active); background floors show a windowed facade that glows warm while active;
// free floors say "To let". The spine carries a name plate (at most three words) and a progress ring per floor, shown
// as unknown until projects have plans. No androids and no speech here: activity is light. The lobby holds the host
// colour key and the visitors: labels with work that no project claims, each of which can move in.
//
// The building has its own canvas and renderer, drawn on demand (it is still unless the state changes), so the deck's
// frame loop and input stay as they were; it reuses the deck's furniture, lighting and palette.

import * as THREE from 'three';
import { DESK_TOP, HALF, PI, QS, vh, vw } from './env.js';
import { esc, hsl, mix, store } from './util.js';
import { hostLook, projectLook } from './looks.js';
import { working } from './activity.js';
import { G, KIT, Placer, canvasTex, softDot } from './scene.js';

// ------------------------------------------------------------------ the view switch
const VIEW_KEY = 'fleet.view';
export let buildingShown = false;
const toggle = document.getElementById('viewToggle');
function showView(view) {
  buildingShown = view === 'building';
  document.body.dataset.view = view;
  for (const b of toggle.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.view === view));
  draw();
}
toggle.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-view]');
  if (!b) return;
  store('localStorage', VIEW_KEY, b.dataset.view);
  showView(b.dataset.view);
});

// ------------------------------------------------------------------ dimensions (tiles, as on the deck)
const FW = 26, FD = 6, FH = 3, SLAB = 0.3;   // floor width, depth and storey height: shallow, so ten fit a laptop screen
const LH = 4.2;                              // the lobby is a little taller
const SPINE = 3.2;                           // the column on the left that carries the name plates
const MAX_Z = 40;                            // pixels per tile at most, so a small building doesn't balloon
const WARM = '#ffc47a';
const baseOf = floor => LH + (floor - 1) * FH;

// ------------------------------------------------------------------ renderer, camera, lights
const root = document.getElementById('building');
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
let doc = null, floors = [], lobby = null, buildKey = '';
export function applyBuilding(state) {
  doc = state;
  floors = floorsOf(state);
  lobby = lobbyOf(state);
  const key = JSON.stringify([floors.map(f => [f.floor, f.projectId, f.name, f.mode, f.active]), lobby.hosts]);
  if (key !== buildKey && KIT.desk) { buildKey = key; build(); }
  renderUi();
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
  const busy = new Set();
  for (const h of state.hosts || []) for (const item of [...(h.jobs || []), ...(h.sessions || [])]) {
    if (item.project_id && working(item)) busy.add(item.project_id);
  }
  const taken = new Set();
  return Array.from({ length: b.capacity }, (_, i) => {
    const floor = i + 1, projectId = byFloor.get(floor);
    if (!projectId) return { floor, projectId: null, mode: 'to-let', active: false };
    const project = projects.get(projectId);
    return { floor, projectId, name: project.name, look: projectLook(projectId, taken),
      mode: b.focus[projectId] === 'background' ? 'windowed' : 'open', active: busy.has(projectId) };
  });
}

// Visitors: host:label pairs with jobs or sessions that no project claims. Registered projects without a floor, and
// work fleetd couldn't place in a project, are listed too: nothing with work drops out of view.
function lobbyOf(state) {
  const visitors = new Map();
  const projects = new Map((state.projects || []).map(p => [p.id, p]));
  for (const h of state.hosts || []) for (const item of [...(h.jobs || []), ...(h.sessions || [])]) {
    if (item.project_id) continue;
    const key = h.name + ':' + (item.project ?? '');
    if (!visitors.has(key)) visitors.set(key, { host: h.name, label: item.project ?? null, count: 0, active: false });
    const v = visitors.get(key);
    v.count++;
    v.active ||= working(item);
  }
  const b = state.building || { capacity: 0, floors: {}, no_floor: [] };
  return {
    hosts: (state.hosts || []).map(h => ({ name: h.name, ok: !!h.ok, color: hostLook(h.name).color })),
    visitors: [...visitors.values()].sort((a, c) => (a.label ?? '').localeCompare(c.label ?? '') || a.host.localeCompare(c.host)),
    noFloor: b.no_floor.map(id => ({ id, name: projects.get(id)?.name ?? id })),
    full: Object.keys(b.floors).length >= b.capacity,
    labels: state.project_labels || {},
  };
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
  box(FW + SPINE + 8, 0.5, FD + 5, (FW - SPINE) / 2, -0.25, FD / 2 + 1, mat.ground, false);
  box(SPINE, top + 0.6, FD, -SPINE / 2, (top + 0.6) / 2, FD / 2, mat.spine);
  box(FW + SPINE + 0.4, 0.5, FD + 0.4, (FW - SPINE) / 2, top + 0.25, FD / 2, mat.slab);
  box(0.3, top, FD, FW + 0.15, top / 2, FD / 2, mat.spine);   // the right-hand end wall
  buildLobby(placer);
  for (const f of floors) buildFloor(f, placer);
  placer.build(group, disposables);
  const mid = new THREE.Vector3((FW - SPINE) / 2, top / 2, FD / 2);
  sun.target.position.copy(mid);
  sun.position.copy(mid).add(new THREE.Vector3(-14, 30, 26));
  const sc = sun.shadow.camera, rad = Math.max(FW, top) * 0.8;
  sc.left = -rad; sc.right = rad; sc.top = rad; sc.bottom = -rad; sc.near = 1; sc.far = 120;
  sc.updateProjectionMatrix();
  fit();
}

function buildFloor(f, place) {
  const y = baseOf(f.floor), h = FH - SLAB;
  const look = f.look;
  box(FW, SLAB, FD, FW / 2, y + SLAB / 2, FD / 2, mat.slab);
  box(FW, h, 0.2, FW / 2, y + SLAB + h / 2, 0.1, tinted(look ? look.wall : '#2a3446'));   // back wall
  const record = { mode: f.mode, active: f.active, front: f.mode === 'open' ? 'none' : f.mode, furniture: 0, pennant: false, glow: false };
  built.set(f.floor, record);
  if (f.mode === 'open') {
    furnish(place, y + SLAB, look, record);
    // the pennant: flown from a short pole on the floor's outer edge, in the project's colour
    box(1.3, 0.07, 0.07, FW + 0.95, y + FH - 0.35, FD - 0.3, mat.spine);
    const flag = new THREE.Mesh(PENNANT, new THREE.MeshBasicMaterial({ color: look.accent, side: THREE.DoubleSide }));
    disposables.push(flag.material);
    flag.position.set(FW + 1.5, y + FH - 0.35, FD - 0.3);
    group.add(flag);
    record.pennant = true;
    // ceiling light: warm while runs are active, off otherwise
    box(FW - 3, 0.06, 0.3, FW / 2, y + FH - 0.05, FD / 2, f.active ? LAMP_ON : LAMP_OFF, false);
    if (f.active) {
      for (const x of [FW * 0.3, FW * 0.62]) {
        const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: warmGlow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.8 }));
        disposables.push(s.material);
        s.scale.set(9, 3.4, 1); s.position.set(x, y + SLAB + 1.2, FD / 2 + 0.6);
        group.add(s);
      }
      record.glow = true;
    }
  } else {
    facade(f, y);
    record.glow = f.mode === 'windowed' && f.active;
  }
}
const LAMP_ON = new THREE.MeshBasicMaterial({ color: WARM, toneMapped: false });
const LAMP_OFF = new THREE.MeshStandardMaterial({ color: 0x3a4458, roughness: 0.6 });
const PENNANT = (() => {
  const s = new THREE.Shape();
  s.moveTo(-0.5, 0); s.lineTo(0.5, 0); s.lineTo(0, -1.6); s.lineTo(-0.5, 0);   // hangs down from the pole
  return new THREE.ShapeGeometry(s);
})();

// An open floor's furniture: the deck's desks, screens and chairs in a row, a bookcase, a sofa and plants.
function furnish(place, y, look, record) {
  const at = (model, x, z, rot = 0, lift = 0) => { place.add(model, x, z, rot, y + lift); record.furniture++; };
  place.add('floor:checker', FW / 2, FD / 2, 0, y - 0.02, new THREE.Vector3(FW, 0.04, FD), look.floor);
  at('bookcaseOpen', 1.3, 0.45);
  at('bookcaseOpen', 2.3, 0.45);
  for (const x of [5.5, 8.2, 10.9, 13.6, 16.3]) {
    at('desk', x, 1.7);
    at('computerScreen', x, 1.5, 0, DESK_TOP);
    at('chairDesk', x, 2.75);
  }
  at('loungeSofa', 21.2, 1.0);
  at('pottedPlant', 19.2, 0.7);
  at('pottedPlant', FW - 1.0, FD - 0.9);
}

// A windowed facade: panes across the front, warm while runs are active; a free floor's show "To let".
function facade(f, y) {
  const h = FH - SLAB, { c, g, tex } = canvasTex(1024, Math.round(1024 * h / FW));
  disposables.push(tex);
  const W = c.width, H = c.height, cols = 13, pw = W / cols;
  g.fillStyle = '#2a3446'; g.fillRect(0, 0, W, H);
  for (let k = 0; k < cols; k++) {
    const x = k * pw + 4, w = pw - 8;
    const glass = g.createLinearGradient(0, 6, 0, H - 6);
    // a background floor's glow stays low: busy must never look more important than an open floor
    if (f.mode === 'windowed' && f.active) { glass.addColorStop(0, '#9a6d3e'); glass.addColorStop(1, '#4f3522'); }
    else { glass.addColorStop(0, '#1d2a42'); glass.addColorStop(1, '#101828'); }
    g.fillStyle = glass; g.fillRect(x, 6, w, H - 12);
    g.fillStyle = 'rgba(255,255,255,.07)'; g.fillRect(x + w * 0.15, 6, w * 0.12, H - 12);   // a reflection
    g.fillStyle = '#2a3446'; g.fillRect(x, H * 0.36, w, 3);                               // transom
  }
  if (f.mode === 'to-let') {
    g.setLineDash([10, 8]); g.lineWidth = 3; g.strokeStyle = '#7384a0';
    g.strokeRect(3, 3, W - 6, H - 6);
    const sw = W * 0.2, sh = H * 0.62, sx = W / 2 - sw / 2, sy = (H - sh) / 2;
    g.setLineDash([]);
    g.fillStyle = '#e6edf8'; g.fillRect(sx, sy, sw, sh);
    g.fillStyle = '#05080f'; g.font = `700 ${Math.round(sh * 0.5)}px Space Grotesk, system-ui, sans-serif`;
    g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText('TO LET', W / 2, H / 2 + 2);
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
  place.add('floor:tile', FW / 2, FD / 2, 0, SLAB - 0.02, new THREE.Vector3(FW, 0.04, FD), '#56627a');
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
const TOP_UI = 52, MARGIN = 14, NARROW_LOBBY_H = 150;
function fit() {
  if (!vw || !floors.length) return;
  const top = baseOf(floors.length + 1) + 0.6;
  const centre = new THREE.Vector3((FW - SPINE) / 2, top / 2, FD / 2);
  camera.position.copy(centre).addScaledVector(VIEW, 100);
  camera.up.set(0, 1, 0);
  camera.lookAt(centre);
  camera.updateMatrixWorld();
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  const v = new THREE.Vector3();
  for (const x of [-SPINE, FW + 2.2]) for (const y of [-0.5, top]) for (const z of [0, FD + 0.5]) {
    v.set(x, y, z).applyMatrix4(camera.matrixWorldInverse);
    x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x); y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y);
  }
  // on a phone, room on the left for the name plates and below for the lobby's list, which can't fit over the lobby
  const narrow = vw < 760;
  const l = MARGIN + (narrow ? 64 : 0), r = vw - MARGIN, t = TOP_UI + MARGIN, b = vh - MARGIN - (narrow ? NARROW_LOBBY_H : 0);
  zoom = Math.min(MAX_Z, (r - l) / (x1 - x0), (b - t) / (y1 - y0));
  const ax = (l + r) / 2, ay = (t + b) / 2, mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
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
    renderer.render(scene, camera);
    placeUi();
  });
}

// ------------------------------------------------------------------ name plates, progress rings and the lobby, over the scene
const WORDS = 3, LOBBY_MIN_W = 440;
const plateName = name => { const w = name.trim().split(/\s+/); return w.length > WORDS ? w.slice(0, WORDS).join(' ') + '…' : w.join(' '); };
const RING = `<svg class="ring" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/></svg><i aria-hidden="true">?</i>`;
function renderUi() {
  const plates = floors.map(f => `<div class="plate" data-floor="${f.floor}" data-mode="${f.mode}"${f.projectId ? ` data-project="${esc(f.projectId)}"` : ''}
      ${f.name ? `title="${esc(f.name)}"` : ''} style="${f.look ? `--accent:${f.look.accent}` : ''}">
      <span class="fn">${f.floor}</span><b>${f.projectId ? esc(plateName(f.name)) : 'To let'}</b>
      ${f.projectId ? `<span class="progress" data-progress="unknown" title="Progress unknown: no plan yet" aria-label="progress unknown">${RING}</span>` : ''}
    </div>`).join('');
  const L = lobby;
  const visitor = v => `<li class="visitor" data-host="${esc(v.host)}" data-label="${esc(v.label ?? '')}" title="${esc(`${v.host}:${v.label ?? 'no label'} · ${v.count}`)}">
      <i style="background:${hostLook(v.host).color}"></i><span>${esc(v.label === null ? 'no label' : L.labels[v.label] || v.label)}</span>${v.active ? '<em class="busy" aria-label="active"></em>' : ''}
      ${v.label === null ? '' : L.full ? '<button disabled title="Every floor is taken">No vacancies</button>' : '<button data-move-in>Move in</button>'}
      <small class="err"></small></li>`;
  ui.innerHTML = `${plates}
    <div class="lobby" role="region" aria-label="Lobby">
      <div class="hostkey" aria-label="Hosts">${L.hosts.map(h => `<span class="host${h.ok ? '' : ' off'}" data-host="${esc(h.name)}" title="${esc(h.name)}${h.ok ? '' : ': offline'}"><i style="background:${h.ok ? h.color : 'var(--dim)'}"></i>${esc(h.name)}</span>`).join('')}</div>
      ${L.full ? '<div class="novacancy">No vacancies</div>' : ''}
      <div class="visitors"><h3>Visitors</h3>${L.visitors.length ? `<ul>${L.visitors.map(visitor).join('')}</ul>` : '<p class="none">None</p>'}</div>
      ${L.noFloor.length ? `<div class="nofloor"><h3>No floor</h3><ul>${L.noFloor.map(p => `<li data-project="${esc(p.id)}" title="${esc(p.name)}">${esc(plateName(p.name))}</li>`).join('')}</ul></div>` : ''}
    </div>`;
  placeUi();
}
ui.addEventListener('click', async ev => {
  const b = ev.target.closest('[data-move-in]');
  if (!b) return;
  const row = b.closest('.visitor');
  b.disabled = true;
  try {
    const res = await fetch('/api/move-in', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host: row.dataset.host, label: row.dataset.label }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  } catch (err) {
    b.disabled = false;
    row.querySelector('.err').textContent = 'Failed';
    row.title = `Couldn’t move in: ${err.message}`;
  }
  // the new floor arrives with the next pushed document
});

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
function placeUi() {
  for (const el of ui.querySelectorAll('.plate')) {
    const r = floorRect(baseOf(Number(el.dataset.floor)), FH);
    const p = screenOf(-SPINE / 2, baseOf(Number(el.dataset.floor)) + FH / 2, FD);
    el.style.transform = `translate(${Math.round(p.x)}px,${Math.round(p.y)}px) translate(-50%,-50%)`;
    el.style.setProperty('--h', `${Math.round(r.bottom - r.top)}px`);
  }
  const lob = ui.querySelector('.lobby');
  if (lob) {
    const r = floorRect(0, LH), left = screenOf(12.2, 0, FD).x;
    if (vw < 760) {   // a phone: across the screen below the building
      Object.assign(lob.style, { left: `${MARGIN}px`, top: `${Math.round(vh - MARGIN - NARROW_LOBBY_H + 8)}px`,
        width: `${vw - 2 * MARGIN}px`, height: `${NARROW_LOBBY_H - 8}px` });
      return;
    }
    // over the lobby's right-hand side, out into the street when the building is drawn small
    const width = Math.min(Math.max(r.right - left - 10, LOBBY_MIN_W), vw - left - MARGIN);
    Object.assign(lob.style, { left: `${Math.round(left)}px`, top: `${Math.round(r.top + 6)}px`,
      width: `${Math.round(width)}px`, height: `${Math.round(r.bottom - r.top - 12)}px` });
  }
}

// read-only probe for browser tests: the building lives in WebGL, so the tests ask it what it drew and where
window.fleetBuilding = Object.freeze({
  floors: () => floors.map(f => ({ floor: f.floor, project: f.projectId, name: f.name ?? null, mode: f.mode, active: f.active,
    built: built.get(f.floor) ? { ...built.get(f.floor) } : null, screen: floorRect(baseOf(f.floor), FH) })),
  lobby: () => ({ screen: floorRect(0, LH), hosts: lobby?.hosts ?? [], visitors: lobby?.visitors ?? [] }),
  zoom: () => zoom,
});

showView((QS.get('view') || store('localStorage', VIEW_KEY)) === 'building' ? 'building' : 'deck');
