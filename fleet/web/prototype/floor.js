// The Restoke floor: the sprite world built by floorLayout (fleet/web/js/world/layout.js) over the deck's /api/state
// (the recorded Restoke fleet under the test server). Robots come out of the lift one by one, walk to their desks
// around the furniture and sit; lamps glow only where a run is active; plan-wall tiles show each job's steps.
// Opens on the whole floor (l1); click a bench to zoom onto it (l2); Escape zooms back out.
//   ?shot        no panel       ?seated      robots already at their places (for comparisons)
//   ?loop        robots walk between the lift and their desks without stopping (for measuring)
//   ?zoom=near   open on the first bench's l2 framing
//   ?as=0:ship,1:stalled   what the first, second... run does instead of its job's own activity or status (for
//                showing a behaviour the recorded state doesn't have: ship, read, review, test, stalled, done, failed...)
import { World } from '/js/world/engine.js';
import { floorLayout } from '/js/world/layout.js';
import { route, along, length, navGrid } from '/js/world/nav.js';
import { toScreen } from '/js/world/projection.js';
import { Robots } from '/js/world/robots.js';
import { Crew, crewSprites } from '/js/world/crew.js';
import { RECENT_S, workareaOf } from '/js/workarea-model.js';
import { hostLook } from '/js/looks.js';
import { actionOf, glyphHtml } from '/js/glyphs.js';
import { activityFor, activityOf } from '/js/activity.js';

const params = new URLSearchParams(location.search);
const ROOM = params.get('room') || 'restoke';
const STAGGER = 1.8;            // seconds between robots leaving the lift
const DOORS = 0.5;              // seconds for the lift doors to open or close
const ZOOM_RATE = 3.2;          // a click's zoom: slower than following the pointer
const GLYPH = { blocker: '✋', decision: '?', alert: '!' };
const LOOP = params.has('loop');   // robots walk lift ↔ desk for ever: a steady scene for measuring frame time
// a warm ambient over everything, so an idle floor looks lived in as l1 does; lamps still carry activity
const GRADE = { color: '#ffc088', alpha: 0.26, mode: 'soft-light' };
const PRINTS = 0.4;               // a fresh trail's strength: light, as l2's; it fades over the hour a walk stays recent
const BUBBLES_FROM = 0.75;        // zoom level (0 whole floor .. 1 l2) from which working robots show their action

if (params.has('shot')) document.body.classList.add('shot');
const panel = document.getElementById('panel'), overlay = document.getElementById('overlay');
const floor = window.floor = { ready: false, error: null, engine: null, layout: null, crew: null, seated: [], settled: [], taps: [] };
const STATUSES = new Set(['running', 'queued', 'stalled', 'done', 'cancelled', 'failed']);
// ?as: per run index, an activity or a status standing in for the job's own
const AS = new Map((params.get('as') || '').split(',').filter(Boolean).map(p => { const [i, v] = p.split(':'); return [Number(i), v]; }));

async function main() {
  const state = await fetch('/api/state').then(r => r.json());
  const room = workareaOf(state, ROOM, state.time);
  if (!room) throw new Error(`no room ${ROOM} in the state`);
  // which floor of the building this is, and which floors have something that needs you
  const floors = state.building ? state.building.floors : {};
  const here = room.projectIds.map(id => floors[id]).find(Boolean) ?? null;
  const ids = Object.fromEntries(Object.entries(state.projects || {}).map(([id, p]) => [p.name, id]));
  const attentionFloors = new Set((state.attention || []).filter(a => a.state === 'open').map(a => floors[ids[a.project]]).filter(Boolean));
  // a live session idle in this project is waiting on its human: the floor shows a waiting crate
  room.waiting = (state.hosts || []).flatMap(h => h.sessions || []).filter(s => s.project === ROOM && s.status === 'idle');
  const kitManifest = await fetch('/assets/world/kit/manifest.json').then(r => r.json());
  const robotManifest = await fetch('/assets/world/robot/sprites/sprites.json').then(r => r.json());
  const layout = floor.layout = floorLayout(room, kitManifest.sprites, robotManifest.seat_furniture);

  const world = floor.engine = new World(document.getElementById('world'), {
    camera: { far: layout.frames.far, near: layout.frames.near, bounds: layout.bounds }, grade: GRADE,
    motionDpr: params.get('motion') || 'auto' });
  await world.load('/assets/world/kit/manifest.json');
  const robots = floor.robots = await Robots.load(world);
  const seatNow = params.has('seated') || world.reduced;
  for (const p of layout.planes) world.addPlane(p);
  for (const it of layout.items) world.add(it);

  // walking: a grid from everything standing on the floor below head height
  const blocks = [];
  for (const it of layout.items) {
    const s = kitManifest.sprites[it.sprite];
    // (chairs are pushed aside, not walked around: the row behind a bench is how its seats are reached)
    if (!s || s.layer === 'light' || /^(footprints|slab|chair|floor-sheen|shadow)|-shadow$/.test(it.sprite)) continue;   // (flat on the floor)
    const f = s.footprint, z0 = it.at[2] + f[2];
    if (z0 > 1.8) continue;   // hanging (the lantern)
    blocks.push([it.at[0] + f[0], it.at[1] + f[1], it.at[0] + f[3], it.at[1] + f[4]]);
  }
  const grid = navGrid({ x0: 0, y0: 0, x1: layout.size.w, y1: layout.size.d }, blocks);
  floor.grid = grid;
  const liftOut = [layout.lift.at[0], layout.lift.at[1] - 0.15];

  // one light trail of footprints per recent walk, from the lift to the desk, fading as the walk grows older
  layout.trails.forEach((t, i) => {
    const fade = PRINTS * Math.max(0, 1 - (state.time - t.at) / RECENT_S);
    const pts = fade > 0.02 && route(grid, liftOut, t.seat);
    if (!pts) return;
    const L = length(pts);
    for (let d = 0.9, k = 0; d < L - 0.9; d += 0.75, k++) {
      const { at, heading } = along(pts, d);
      const deg = ((Math.round(heading / (Math.PI / 4)) % 8) + 8) % 8 * 45;
      world.add({ id: `steps-${i}-${k}`, sprite: `footprints-${String(deg).padStart(3, '0')}`, at: [at[0], at[1], 0], intensity: fade });
    }
  });

  // the runs' robots: each in its host's colour and kit with its agent's face, doing what its job does (crew.js)
  const jobs = new Map((state.hosts || []).flatMap(h => (h.jobs || []).map(j => [`${h.name}:${j.id}`, j])));
  layout.runs.forEach((run, i) => {   // ?as: a status stands in for the run's own
    const v = AS.get(i);
    if (v && STATUSES.has(v)) run.status = v;
  });
  const acts = run => {
    const v = AS.get(layout.runs.indexOf(run));
    if (v && !STATUSES.has(v)) return v;
    const job = jobs.get(run.key);
    return activityFor({ ...(job || {}), status: run.status }) || 'type';
  };
  const looks = run => { const h = hostLook(run.host); return { host: h.color, kit: h.acc, agent: run.agent }; };
  const crew = floor.crew = new Crew({ world, robots, grid, lift: liftOut, store: layout.store, runs: layout.runs, looks, acts,
    reduced: world.reduced, loop: LOOP });
  floor.seated = crew.seated; floor.settled = crew.settled;
  crew.onChange = () => world.onView && world.onView(world.camera.view);
  const t0 = performance.now() / 1000 + 0.6;
  if (seatNow) crew.settleAll();
  else { crew.start(t0, STAGGER); world.use(...crewSprites(crew)); }
  function tick() {
    const now = performance.now() / 1000;
    const busy = crew.step(now);
    let doors = 0;   // open while robots come out one after another
    for (const m of crew.members) {
      const t = now - m.start;
      if (!m.legs || t < 0 || t > 2 * DOORS + 1.4) continue;
      doors = Math.max(doors, t < DOORS ? t / DOORS : t < DOORS + 1.4 ? 1 : Math.max(0, 1 - (t - DOORS - 1.4) / DOORS));
    }
    const cell = Math.round(doors * 4);
    if (world.items.get('lift').cell !== cell) world.set('lift', { cell });
    if (busy || doors > 0) requestAnimationFrame(tick);
  }
  if (!seatNow) requestAnimationFrame(tick);
  // nods and shakes: a test that finished or an error, as the deck (motion.js noteEvents), from the state as it moves
  const seen = new Map([...jobs].map(([k, j]) => [k, { ts: Math.max(0, ...(j.events || []).map(e => e.ts || 0)), testing: activityOf(j.activity) === 'test' }]));
  floor.react = (key, yes) => { const r = crew.react(key, yes, performance.now() / 1000); if (r) requestAnimationFrame(tick); return r; };
  if (!params.has('shot')) setInterval(async () => {
    const next = await fetch('/api/state').then(r => r.json()).catch(() => null);
    for (const h of (next && next.hosts) || []) for (const j of h.jobs || []) {
      const key = `${h.name}:${j.id}`, s = seen.get(key);
      if (!s) continue;
      for (const ev of (j.events || []).filter(e => (e.ts || 0) > s.ts)) {
        if (ev.kind === 'error') { floor.react(key, false); s.testing = false; }
        else if (ev.kind === 'tool' || ev.kind === 'text') { if (s.testing) floor.react(key, true); s.testing = activityOf(ev) === 'test'; }
        s.ts = Math.max(s.ts, ev.ts || 0);
      }
    }
  }, 5000);

  // the lantern's glyph and count, in the DOM so it stays text (see "Hit testing" in the design)
  const lantern = layout.items.find(it => it.id === 'lantern');
  let glyph = null;
  if (lantern) {
    glyph = document.createElement('div');
    glyph.className = 'lantern-glyph';   // (not 'glyph': that is the deck's action glyph)
    glyph.textContent = (GLYPH[lantern.lantern.kind] || '!') + (lantern.lantern.count > 1 ? ` ${lantern.lantern.count}` : '');
    overlay.append(glyph);
  }
  const glyphAt = lantern && [lantern.at[0], lantern.at[1] - 0.12, lantern.at[2] + kitManifest.sprites.lantern.slots.glyph[2]];
  // the lift's floor buttons: this floor lit, floors with something that needs you marked (PRD: the lift panel)
  const buttons = layout.panel.buttons.map((b, i) => {
    const el = document.createElement('div');
    el.className = 'floor-button' + (i + 1 === here ? ' here' : '') + (attentionFloors.has(i + 1) ? ' attention' : '');
    el.textContent = String(i + 1);
    overlay.append(el);
    return { el, at: [layout.panel.at[0] + b[0], layout.panel.at[1] + b[1], b[2]] };
  });
  // the lift's indicator over its doors: this floor's number, as in the concept art
  const indicator = document.createElement('div');
  indicator.className = 'lift-number';
  indicator.textContent = here ?? '–';
  overlay.append(indicator);
  // what each working robot is doing, as a glyph in a bubble over its head (l2), once zoomed in enough to read it
  const bubbles = layout.runs.map(run => {
    const el = document.createElement('div');
    el.className = 'bubble';
    el.style.color = run.tint;
    el.innerHTML = glyphHtml(actionOf({ ...jobs.get(run.key), status: run.status }));   // (?as may stand in a status)
    el.hidden = true;
    overlay.append(el);
    return { el, run, member: crew.members.find(m => m.run === run) };
  });
  floor.bubbles = bubbles;
  const place = (el, view, at) => { const [x, y] = toScreen(view, at); el.style.left = `${x}px`; el.style.top = `${y}px`; };
  world.onView = view => {
    if (glyph) place(glyph, view, glyphAt);
    const scale = view.ppm / world.camera.max.ppm;
    // (text over the world scales with it, but never below a legible size)
    for (const b of buttons) { place(b.el, view, b.at); b.el.style.transform = `translate(-50%, -50%) scale(${Math.max(0.6, scale)})`; }
    place(indicator, view, layout.indicator);
    indicator.style.transform = `translate(-50%, -50%) scale(${Math.max(0.7, scale)})`;
    const near = world.camera.zoomLevel(view.ppm) >= BUBBLES_FROM;
    for (const b of bubbles) {
      b.el.hidden = !near || !floor.seated.includes(b.run.key);
      if (b.el.hidden) continue;
      // over the helmet (or its kit, where that rises above it) of the frame it sits in
      const m = b.member, [ax, ay] = robots.anchor(m.clip, m.dir, 0, 'head_top', m.look.kit), [x, y] = toScreen(view, m.at);
      b.el.style.left = `${x + ax * view.ppm}px`; b.el.style.top = `${y + ay * view.ppm - 12 * Math.max(1, scale)}px`;
      b.el.style.transform = `translate(-50%, -100%) scale(${Math.max(1, 1.6 * scale)})`;
    }
  };

  // clicks: a bench (or anything at it) zooms onto that bench; Escape goes back to the whole floor
  const benchOf = place => {
    if (!place) return null;
    const m = place.match(/^(?:bench|plan):(.+)$/);
    if (m) return layout.benches.find(b => b.key === m[1]);
    const run = layout.runs.find(r => place === `run:${r.key}` || place.startsWith(`step:${r.key}:`));
    return run ? layout.benches.find(b => b.key === run.bench) : null;
  };
  world.onTap = hit => {
    floor.taps.push(hit);
    const b = benchOf(hit.place);
    if (b) world.camera.frame(b.frame, ZOOM_RATE), world.request();
  };
  addEventListener('keydown', e => { if (e.key === 'Escape') { world.camera.frame(layout.frames.far, ZOOM_RATE); world.request(); } });
  addEventListener('resize', () => world.resize());
  if (params.get('zoom') === 'near') world.camera.jump(world.camera.max);

  world.onView(world.camera.view);   // (the first frames may have come before the overlay existed)
  await world.whenLoaded();
  floor.ready = true;
  panel.textContent = `${ROOM}: ${room.benches.length} jobs on ${layout.benches.length} benches, ${layout.runs.filter(r => r.status === 'running').length} running\n`
    + 'click a bench to zoom in, Esc to zoom out';
}
floor.zoomTo = key => { const b = floor.layout.benches.find(x => x.key === key); floor.engine.camera.frame(b.frame, ZOOM_RATE); floor.engine.request(); };
main().catch(e => { console.error(e); floor.error = String(e.message || e); panel.textContent = floor.error; });
