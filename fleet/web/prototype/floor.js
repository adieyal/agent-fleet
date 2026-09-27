// The Restoke floor: the sprite world built by floorLayout (fleet/web/js/world/layout.js) over the deck's /api/state
// (the recorded Restoke fleet under the test server). Robots come out of the lift one by one, walk to their desks
// around the furniture and sit; lamps glow only where a run is active; plan-wall tiles show each job's steps.
// Opens on the whole floor (l1); click a bench to zoom onto it (l2); Escape zooms back out.
//   ?shot        no panel       ?seated      robots already at their desks (for comparisons)
//   ?loop        robots walk between the lift and their desks without stopping (for measuring)
//   ?zoom=near   open on the first bench's l2 framing
import { World } from '/js/world/engine.js';
import { floorLayout } from '/js/world/layout.js';
import { route, along, length, navGrid } from '/js/world/nav.js';
import { toScreen } from '/js/world/projection.js';
import { RECENT_S, workareaOf } from '/js/workarea-model.js';
import { hostLook } from '/js/looks.js';
import { actionOf, glyphHtml } from '/js/glyphs.js';

const params = new URLSearchParams(location.search);
const ROOM = params.get('room') || 'restoke';
const SPEED = 1.1;              // walking, metres per second
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
const floor = window.floor = { ready: false, error: null, engine: null, layout: null, walkers: [], seated: [], taps: [] };

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
  const layout = floor.layout = floorLayout(room, kitManifest.sprites, host => hostLook(host).color);

  const world = floor.engine = new World(document.getElementById('world'), {
    camera: { far: layout.frames.far, near: layout.frames.near, bounds: layout.bounds }, grade: GRADE,
    motionDpr: params.get('motion') || 'auto' });
  await world.load('/assets/world/kit/manifest.json');
  await world.load('/art/bakeoff/world.json', { prefix: 'b2/' });
  const seatNow = params.has('seated') || world.reduced;   // (no walking: no walker sprites needed)
  if (!seatNow) await world.load('/assets/world/robot-placeholder/manifest.json', { prefix: 'walker/' });
  world.use(...new Set(layout.runs.map(r => r.sprite)), ...(seatNow ? [] : [...Array(8).keys()].map(h => `walker/walk-${h}`)));
  for (const p of layout.planes) world.addPlane(p);
  for (const it of layout.items) world.add(it);

  // walking: a grid from everything standing on the floor below head height
  const blocks = [];
  for (const it of layout.items) {
    const s = kitManifest.sprites[it.sprite];
    // (chairs are pushed aside, not walked around: the row behind a bench is how its seats are reached)
    if (!s || s.layer === 'light' || /^(footprints|slab|chair|floor-sheen|shadow)/.test(it.sprite)) continue;   // (flat on the floor)
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

  // the runs: each walks from the lift to its desk and sits, one after another
  const t0 = performance.now() / 1000 + 0.6;
  floor.walkers = layout.runs.map((run, i) => {
    const pts = route(grid, liftOut, run.seat);
    return { run, pts, L: pts ? length(pts) : 0, start: t0 + i * STAGGER, state: 'waiting' };
  });
  const sit = w => {
    const { run } = w;
    if (w.state === 'walking') world.remove(w.id);
    if (run.chair) world.set(run.chair, { visible: false });
    world.seat(run.key, { sprite: run.sprite, at: run.seat, on: run.module, tint: run.tint, ambient: true, place: `run:${run.key}` });
    world.add({ id: `seat-shadow-${run.key}`, sprite: 'shadow-seat', at: [run.seat[0], run.seat[1] + 0.05, 0] });   // its contact shadow
    w.state = 'seated';
    floor.seated.push(run.key);
    if (world.onView) world.onView(world.camera.view);
  };
  if (seatNow) floor.walkers.forEach(sit);
  function tick() {
    const now = performance.now() / 1000;
    let busy = false, doors = 0;
    for (const w of floor.walkers) {
      if (w.state === 'seated') continue;
      busy = true;
      const t = now - w.start;
      if (t < 0) continue;
      doors = Math.max(doors, t < DOORS ? t / DOORS : t < DOORS + 1.4 ? 1 : Math.max(0, 1 - (t - DOORS - 1.4) / DOORS));
      if (t < DOORS * 0.8) continue;   // out once the doors are most of the way open
      if (!w.pts) { sit(w); continue; }
      const { at, heading, done } = along(w.pts, (t - DOORS * 0.8) * SPEED);
      if (done && LOOP) { w.pts = w.pts.slice().reverse(); w.start = now - DOORS * 0.8; continue; }   // ?loop: back and forth
      if (done) { sit(w); continue; }
      const h = ((Math.round(heading / (Math.PI / 4)) % 8) + 8) % 8;
      const item = { sprite: `walker/walk-${h}`, at: [at[0], at[1], 0] };
      if (w.state === 'waiting') { w.id = `walker-${w.run.key}`; world.add({ id: w.id, ...item, tint: w.run.tint, place: `run:${w.run.key}` }); w.state = 'walking'; }
      else world.set(w.id, item);
    }
    const cell = Math.round(doors * 4);
    if (world.items.get('lift').cell !== cell) world.set('lift', { cell });
    if (busy) requestAnimationFrame(tick);
  }
  if (!seatNow) requestAnimationFrame(tick);

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
  // what each working robot is doing, as a glyph in a bubble over its head (l2), once zoomed in enough to read it
  const jobs = new Map((state.hosts || []).flatMap(h => (h.jobs || []).map(j => [`${h.name}:${j.id}`, j])));
  const bubbles = layout.runs.map(run => {
    const el = document.createElement('div');
    el.className = 'bubble';
    el.style.color = run.tint;
    el.innerHTML = glyphHtml(actionOf(jobs.get(run.key)));
    el.hidden = true;
    overlay.append(el);
    return { el, run, at: [run.seat[0] - 0.1, run.seat[1] - 0.05, run.seat[2] + 1.05] };
  });
  floor.bubbles = bubbles;
  const place = (el, view, at) => { const [x, y] = toScreen(view, at); el.style.left = `${x}px`; el.style.top = `${y}px`; };
  world.onView = view => {
    if (glyph) place(glyph, view, glyphAt);
    const scale = view.ppm / world.camera.max.ppm;
    for (const b of buttons) { place(b.el, view, b.at); b.el.style.transform = `translate(-50%, -50%) scale(${scale})`; }
    const near = world.camera.zoomLevel(view.ppm) >= BUBBLES_FROM;
    for (const b of bubbles) {
      b.el.hidden = !near || !floor.seated.includes(b.run.key);
      if (!b.el.hidden) { place(b.el, view, b.at); b.el.style.transform = `translate(-50%, -100%) scale(${Math.max(1, 1.6 * scale)})`; }
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
  panel.textContent = `${ROOM}: ${room.benches.length} jobs on ${layout.benches.length} benches, ${layout.runs.length} running\n`
    + 'click a bench to zoom in, Esc to zoom out';
}
floor.zoomTo = key => { const b = floor.layout.benches.find(x => x.key === key); floor.engine.camera.frame(b.frame, ZOOM_RATE); floor.engine.request(); };
main().catch(e => { console.error(e); floor.error = String(e.message || e); panel.textContent = floor.error; });
