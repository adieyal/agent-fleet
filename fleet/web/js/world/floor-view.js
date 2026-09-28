// The sprite-world floor in the app: a project's floor (layout.js) as an alternative to the deck, chosen with the
// header's deck | world | building switch and remembered like the others. Fed by the same state documents as the deck
// (state.js announces each one), so it is live, and --fixture shows the fixture.
//
// - Each job the floor shows (workarea-model.js; finished ones retire as on the deck, behaviour.js) has a robot at its
//   own desk (desks.js): kept across updates and reloads, never moved by others coming or going. Robots wear their
//   host's colour and kit and a Codex or Claude face, and do what their job does (crew.js, the deck's behaviour).
// - Something that needs you about a job (an open or acknowledged attention item it owns) hangs a lantern over its
//   desk; the rest of the floor's items hang over the question desk.
// - Clicking a robot (or its lantern) opens the job's panel; clicking a bench zooms onto it, Esc zooms out. At close
//   zoom each robot shows a short name (its job's first words) and, at work, its action.
// - One floor at a time; the picker at the bottom left changes it. Opened from inside a floor of the building, it
//   shows that floor.

import { World } from './engine.js';
import { DESKS, floorLayout } from './layout.js';
import { along, length, navGrid, route } from './nav.js';
import { toScreen } from './projection.js';
import { Robots } from './robots.js';
import { Crew, crewSprites } from './crew.js';
import { assignDesks, heldDesks, keepDesks } from './desks.js';
import { RECENT_S, lanternOf, workareasOf } from '../workarea-model.js';
import { hostLook } from '../looks.js';
import { actionOf, glyphHtml } from '../glyphs.js';
import { activityFor, isActive } from '../activity.js';
import { retired } from '../behaviour.js';
import { select } from '../panel.js';
import { mix, store } from '../util.js';

const FLOOR_KEY = 'fleet.world.floor';
const ZOOM_RATE = 3.2;           // a click's zoom: slower than following the pointer
const CLOSE = 0.75;              // zoom level (0 whole floor .. 1 a bench) from which names and actions show
const DOORS = 0.5;               // seconds for the lift doors to open or close
const PRINTS = 0.4;              // a fresh trail's strength; it fades over the hour a walk stays recent
const NAME_WORDS = 3;
const GLYPH = { blocker: '✋', decision: '?', alert: '!' };
const SHOWN = new Set(['open', 'acknowledged']);
const GRADE = { color: '#ffc088', alpha: 0.26, mode: 'soft-light' };

const root = document.getElementById('floorWorld');
const canvas = document.getElementById('floorWorldCanvas');
const ui = document.getElementById('floorWorldUi');
const picker = document.getElementById('floorWorldProject');
const note = document.getElementById('floorWorldNote');

export let worldShown = false;
let doc = null, label = null, wanted = null;      // the state, the floor shown, a project asked for (from the building)
let world = null, robots = null, kit = null, seats = null, loading = null, error = null;
let crew = null, layout = null, placed = new Map(), desks = new Map(), memory = new Map(), overlay = [], ticking = false;
const probe = window.fleetWorld = { ready: false, error: null, taps: [] };

// ------------------------------------------------------------------ showing and hiding
document.addEventListener('fleet:view', ev => show(ev.detail.view === 'world', ev.detail.project));
document.addEventListener('fleet:state', ev => { doc = ev.detail; renderPicker(); if (worldShown && world) sync(); });
if (document.body.dataset.view === 'world') show(true, null);   // (the view was chosen before this module ran)
picker.addEventListener('change', () => { store('localStorage', FLOOR_KEY, picker.value); open(picker.value); });
addEventListener('resize', () => { if (worldShown && world) world.resize(); });
addEventListener('keydown', ev => {
  if (!worldShown || ev.key !== 'Escape' || !layout) return;
  if (document.getElementById('panel').classList.contains('open') || !document.getElementById('reader').hidden) return;
  world.camera.frame(layout.frames.far, ZOOM_RATE); world.request();
});

function show(on, project) {
  worldShown = on;
  if (!on) { stopTicking(); return; }
  wanted = project;
  if (doc) renderPicker();
  if (!world) { start(); return; }
  world.resize();
  if (label) sync();
}
async function start() {
  if (loading) return loading;
  loading = (async () => {
    try {
      // (the floor's framings are the same for every project: from an empty floor)
      const [k, r] = await Promise.all(['/assets/world/kit/manifest.json', '/assets/world/robot/sprites/sprites.json']
        .map(u => fetch(u).then(res => (res.ok ? res.json() : Promise.reject(new Error('missing ' + u))))));
      const bare = floorLayout(null, k.sprites, r.seat_furniture);
      world = probe.engine = new World(canvas, { camera: { far: bare.frames.far, near: bare.frames.near, bounds: bare.bounds }, grade: GRADE });
      kit = (await world.load('/assets/world/kit/manifest.json')).sprites;
      robots = probe.robots = await Robots.load(world);
      seats = robots.seatFurniture;
      world.onTap = tap;
      world.onView = place;
      if (doc) renderPicker();
      if (label) open(label, true);
    } catch (err) {
      error = probe.error = String(err.message || err);
      note.textContent = `The world couldn’t load: ${error}`;
      console.error(err);
    }
  })();
  return loading;
}

// ------------------------------------------------------------------ which floor
function labelsOf(d) {
  // label → the name shown: its own display label, else its project's name, else the label itself
  const names = new Map((d.projects || []).flatMap(p => (p.links || []).map(l => [l.label, p.name])));
  const out = new Map();
  const add = l => out.set(l, d.project_labels?.[l] || names.get(l) || l);
  for (const r of workareasOf(d, d.time)) add(r.room);
  for (const l of names.keys()) add(l);
  return new Map([...out].sort((a, b) => a[0].localeCompare(b[0])));
}
function labelFor(d, projectId) {
  const p = (d.projects || []).find(x => x.id === projectId);
  const rooms = workareasOf(d, d.time);
  return rooms.find(r => r.projectIds.includes(projectId))?.room ?? p?.links?.[0]?.label ?? null;
}
function renderPicker() {
  const labels = labelsOf(doc);
  const want = (wanted && labelFor(doc, wanted)) || null;
  wanted = null;
  let next = want || label;
  if (!next || !labels.has(next)) {
    const kept = store('localStorage', FLOOR_KEY);
    const rooms = workareasOf(doc, doc.time);
    const busiest = [...rooms].sort((a, b) => b.benches.filter(x => x.active).length - a.benches.filter(x => x.active).length)[0];
    next = kept && labels.has(kept) ? kept : busiest ? busiest.room : [...labels.keys()][0] ?? null;
  }
  const html = [...labels].map(([l, name]) => `<option value="${l}">${name.replace(/[<&]/g, c => (c === '<' ? '&lt;' : '&amp;'))}</option>`).join('');
  if (picker.innerHTML !== html) picker.innerHTML = html;
  picker.disabled = !labels.size;
  note.textContent = labels.size ? '' : 'No project has work yet';
  if (next !== label) open(next);
  else if (next) picker.value = next;
}
function open(next, force = false) {
  if (next === label && !force) return;
  label = next;
  if (label) picker.value = label;
  if (!world || !robots) return;
  reset();
  if (label && doc) sync(true);
}
// a fresh floor: nothing placed, no robots
function reset() {
  stopTicking();
  for (const id of [...world.items.keys()]) world.remove(id);
  world.planes.length = 0;
  placed = new Map(); crew = null; layout = null; desks = new Map(); memory = heldDesks(label || '');
  for (const o of overlay) o.el.remove();
  overlay = [];
  probe.ready = false;
}

// ------------------------------------------------------------------ the floor from the state
function jobsOf(d) {
  return new Map((d.hosts || []).flatMap(h => (h.jobs || []).map(j => [`${h.name}:${j.id}`, j])));
}
// what the floor shows now: its jobs that have a robot, each at its desk, and what needs you about them
function roomNow(jobs) {
  const room = workareasOf(doc, doc.time).find(r => r.room === label)
    ?? { room: label, projectIds: [], benches: [], desk: { items: [], lantern: null }, footprints: [] };
  const memo = key => crew && crew.members.find(m => m.run.key === key) || null;
  const benches = room.benches.filter(b => jobs.has(b.key) && !retired(jobs.get(b.key), memo(b.key)));
  const owned = new Set(benches.map(b => b.key));
  const items = (doc.attention || []).filter(i => i.project === label && SHOWN.has(i.state));
  for (const b of benches) b.lantern = lanternOf(items.filter(i => i.owner?.key === b.key));
  const rest = items.filter(i => !owned.has(i.owner?.key));
  // a live session idle in this project is waiting on its human: the floor shows a waiting crate
  const waiting = (doc.hosts || []).flatMap(h => h.sessions || []).filter(s => s.project === label && s.status === 'idle');
  return { ...room, benches, desk: { items: rest, lantern: lanternOf(rest) }, waiting };
}
function sync(first = false) {
  probe.syncs = (probe.syncs || 0) + 1;
  if (!label || !world || !robots) return;
  const now = performance.now() / 1000, jobs = jobsOf(doc), room = roomNow(jobs);
  const created = key => jobs.get(key)?.created_at ?? 0;
  const given = assignDesks(room.benches.map(b => ({ key: b.key, createdAt: created(b.key) })), memory, DESKS, desks);
  desks = given.desks; memory = given.memory;
  keepDesks(label, memory);
  layout = probe.layout = floorLayout(room, kit, seats, desks);
  if (first) for (const p of layout.planes) world.addPlane(p);
  const grid = gridOf(layout);
  const liftOut = [layout.lift.at[0], layout.lift.at[1] - 0.15];
  placeItems([...layout.items, ...trails(layout, grid, liftOut)]);
  const acts = run => activityFor({ ...(jobs.get(run.key) || {}), status: run.status }) || 'type';
  const looks = run => { const h = hostLook(run.host); return { host: h.color, kit: h.acc, agent: run.agent }; };
  if (!crew) {
    crew = probe.crew = new Crew({ world, robots, grid, lift: liftOut, store: layout.store, runs: [], looks, acts, reduced: world.reduced });
    crew.onChange = () => place(world.camera.view);
  }
  crew.grid = grid;
  crew.update(layout.runs, now, first);
  crew.noticeAll(jobs, now);
  world.use(...crewSprites(crew));
  renderOverlay(jobs, room);
  tick();
  if (first) world.whenLoaded().then(() => { if (layout) probe.ready = true; });
  probe.unseated = given.unseated;
  const over = given.unseated.length;   // (more jobs than desks: said, not hidden)
  note.textContent = over ? `${over} more without a desk` : '';
}
// walking: a grid from everything standing on the floor below head height
function gridOf(lay) {
  const blocks = [];
  for (const it of lay.items) {
    const s = kit[it.sprite];
    // (chairs are pushed aside, not walked around: the row behind a bench is how its seats are reached)
    if (!s || s.layer === 'light' || /^(footprints|slab|chair|floor-sheen|shadow)|-shadow$/.test(it.sprite)) continue;
    const f = s.footprint, z0 = it.at[2] + f[2];
    if (z0 > 1.8) continue;   // hanging (the lanterns)
    blocks.push([it.at[0] + f[0], it.at[1] + f[1], it.at[0] + f[3], it.at[1] + f[4]]);
  }
  return navGrid({ x0: 0, y0: 0, x1: lay.size.w, y1: lay.size.d }, blocks);
}
// one light trail of footprints per recent walk, from the lift to the desk, fading as the walk grows older
function trails(lay, grid, from) {
  const out = [];
  for (const t of lay.trails) {
    const fade = PRINTS * Math.max(0, 1 - (doc.time - t.at) / RECENT_S);
    const pts = fade > 0.02 && route(grid, from, t.seat);
    if (!pts) continue;
    const L = length(pts);
    for (let d = 0.9, k = 0; d < L - 0.9; d += 0.75, k++) {
      const { at, heading } = along(pts, d);
      const deg = ((Math.round(heading / (Math.PI / 4)) % 8) + 8) % 8 * 45;
      out.push({ id: `steps-${t.to}-${k}`, sprite: `footprints-${String(deg).padStart(3, '0')}`, at: [at[0], at[1], 0], intensity: +fade.toFixed(2) });
    }
  }
  return out;
}
// place the floor's items, changing only what changed since the last state
function placeItems(items) {
  const next = new Map(items.map(it => [it.id, it]));
  for (const id of placed.keys()) if (!next.has(id)) world.remove(id);
  for (const it of items) {
    const was = placed.get(it.id);
    if (was && JSON.stringify(was) === JSON.stringify(it)) continue;
    if (was) world.remove(it.id);
    world.add({ ...it });
  }
  placed = next;
}

// ------------------------------------------------------------------ motion
function tick() {
  if (ticking || !worldShown || !crew) return;
  ticking = true;
  const step = () => {
    if (!ticking) return;
    const now = performance.now() / 1000;
    const busy = crew.step(now);
    let doors = 0;   // open while robots come out of the lift or go back in
    for (const m of crew.members) {
      if (m.state !== 'walking' || m.start == null) continue;
      const t = now - m.start, back = m.leaving && m.legs[0] ? m.legs[0].L / 1.1 - t : Infinity;
      if (t >= 0 && t < 2 * DOORS + 1.4) doors = Math.max(doors, t < DOORS ? t / DOORS : t < DOORS + 1.4 ? 1 : 1 - (t - DOORS - 1.4) / DOORS);
      if (back < 1.4) doors = 1;
    }
    const cell = Math.round(Math.max(0, doors) * 4);
    if (world.items.has('lift') && world.items.get('lift').cell !== cell) world.set('lift', { cell });
    if (busy || doors > 0) requestAnimationFrame(step);
    else ticking = false;
  };
  requestAnimationFrame(step);
}
function stopTicking() { ticking = false; }
// a change of work held back by the dwell is taken up on a later look
setInterval(() => {
  if (!worldShown || !crew || !layout) return;
  if (crew.update(layout.runs, performance.now() / 1000)) tick();
}, 2000);

// ------------------------------------------------------------------ what is text: names, actions, lanterns, the lift
function renderOverlay(jobs, room) {
  for (const o of overlay) o.el.remove();
  overlay = [];
  const add = (cls, html, at, extra = {}) => {
    const el = document.createElement('div');
    el.className = cls; el.innerHTML = html; el.hidden = true;
    ui.append(el);
    overlay.push({ el, at, ...extra });
  };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  for (const it of layout.items) {
    if (it.sprite !== 'lantern' || !it.lantern) continue;
    const l = it.lantern;
    add('lantern-glyph', esc((GLYPH[l.kind] || '!') + (l.count > 1 ? ` ${l.count}` : '')), [it.at[0], it.at[1] - 0.12, it.at[2] + kit.lantern.slots.glyph[2]], { always: true });
  }
  // which floor of the building this is, and which floors have something that needs you
  const floors = doc.building ? doc.building.floors : {};
  const here = room.projectIds.map(id => floors[id]).find(Boolean) ?? null;
  const ids = Object.fromEntries((doc.projects || []).flatMap(p => (p.links || []).map(l => [l.label, p.id])));
  const needs = new Set((doc.attention || []).filter(a => a.state === 'open').map(a => floors[ids[a.project]]).filter(Boolean));
  layout.panel.buttons.forEach((b, i) => add('floor-button' + (i + 1 === here ? ' here' : '') + (needs.has(i + 1) ? ' attention' : ''), String(i + 1),
    [layout.panel.at[0] + b[0], layout.panel.at[1] + b[1], b[2]], { always: true, button: true }));
  add('lift-number', here == null ? '–' : String(here), layout.indicator, { always: true, button: true });
  for (const run of layout.runs) {
    const job = jobs.get(run.key);
    const name = String(job?.description || '').trim().split(/\s+/).filter(Boolean).slice(0, NAME_WORDS).join(' ');
    if (name) add('name', esc(name), null, { run, kind: 'name' });
    // (the glyph in the host's colour, deepened to read on the white card)
    if (job && isActive(run.status)) add('bubble', glyphHtml(actionOf(job)), null, { run, kind: 'bubble', color: mix(hostLook(run.host).color, '#1b2333', 0.35) });
  }
  place(world.camera.view);
}
function place(view) {
  if (!layout || !robots) return;
  const close = world.camera.zoomLevel(view.ppm) >= CLOSE;
  const scale = view.ppm / world.camera.max.ppm;
  const at = (el, p) => { const [x, y] = toScreen(view, p); el.style.left = `${x}px`; el.style.top = `${y}px`; };
  for (const o of overlay) {
    if (o.always) {
      o.el.hidden = false;
      at(o.el, o.at);
      // (the lift's numbers scale with the world, but never below a legible size)
      if (o.button) o.el.style.transform = `translate(-50%, -50%) scale(${Math.max(0.6, scale)})`;
      continue;
    }
    const m = crew && crew.members.find(x => x.run.key === o.run.key);
    const shown = close && m && m.clip && !m.leaving && (o.kind === 'name' || crew.seated.includes(o.run.key));
    o.el.hidden = !shown;
    if (!shown) continue;
    // over the helmet (or its kit, where that rises above it) of the frame it is in: the name, and the action above it
    const [ax, ay] = robots.anchor(m.clip, m.dir, 0, 'head_top', m.look.kit), [x, y] = toScreen(view, m.at);
    const k = Math.max(1, scale);
    o.el.style.left = `${x + ax * view.ppm}px`;
    if (o.kind === 'name') { o.el.style.top = `${y + ay * view.ppm - 22 * k}px`; o.el.style.transform = `translate(-50%, 0) scale(${k})`; }
    else {
      o.el.style.color = o.color;
      o.el.style.top = `${y + ay * view.ppm - 26 * k}px`;   // (above the name)
      o.el.style.transform = `translate(-50%, -100%) scale(${Math.max(1, 1.6 * scale)})`;
    }
  }
}

// ------------------------------------------------------------------ clicks
function tap(hit) {
  probe.taps.push(hit);
  const where = hit && hit.place;
  if (!where || !layout) return;
  const run = layout.runs.find(r => where === `run:${r.key}`);
  if (run) { select(run.key); return; }   // a robot, or its lantern: the job's panel
  const m = where.match(/^(?:bench|plan):(.+)$/), step = layout.runs.find(r => where.startsWith(`step:${r.key}:`));
  const bench = m ? layout.benches.find(b => b.key === m[1]) : step ? layout.benches.find(b => b.key === step.bench) : null;
  if (bench) { world.camera.frame(bench.frame, ZOOM_RATE); world.request(); }
}
probe.zoomTo = key => { const b = layout.benches.find(x => x.key === key); world.camera.frame(b.frame, ZOOM_RATE); world.request(); };
probe.frame = which => { world.camera.frame(which === 'near' ? layout.frames.near : layout.frames.far, ZOOM_RATE); world.request(); };
probe.state = () => ({ label, desks: Object.fromEntries(desks), runs: layout ? layout.runs.map(r => ({ key: r.key, desk: r.module, status: r.status })) : [],
  members: crew ? crew.members.map(m => ({ key: m.run.key, state: m.state, clip: m.clip, leaving: m.leaving, at: m.at, tone: m.look.tone, kit: m.look.kit, host: m.look.host, agent: m.look.agent })) : [],
  lanterns: layout ? layout.items.filter(it => it.sprite === 'lantern').map(it => ({ id: it.id, place: it.place, at: it.at, count: it.lantern.count })) : [] });
probe.robotAt = key => {   // where a robot is on screen (its chest), for clicking it
  const m = crew.members.find(x => x.run.key === key);
  return toScreen(world.camera.view, [m.at[0], m.at[1], m.clip && crew.seated.includes(key) ? 0.95 : 0.6]);
};
