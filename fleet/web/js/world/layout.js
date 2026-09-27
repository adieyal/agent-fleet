// A project floor laid out from its room (workarea-model.js), as l1 arranges it: along the back wall the crate alcove
// with its lamp, the library, the active workarea as l2 draws it (question desk and lantern, plan wall, bench), the
// orchestrator's podium, the briefing board and the lift with its floor buttons; on the open floor long benches in
// staggered rows. Pure: returns what to place, where each run sits, and the framings; the page places it in the
// sprite world. See docs/design/sprite-world.md, "Floor layout".
//
// Every job the room shows gets a desk (a bench is three desks): active jobs fill the workarea bench's desks, then
// the next bench's; recently finished ones the bench after; the rest stand idle.

export const FLOOR = { w: 21.6, d: 10.8, h: 3.2 };   // 6 x 3 bays: l1's proportions, full with five benches and a workarea
const BAY = 3.6;
// benches: the workarea first (against the wall, under its plan wall), then l1's five on the open floor, back to front
const BENCHES = [[10.9, 9.05], [4.3, 7.1], [17.6, 6.6], [10.7, 4.6], [3.7, 3.4], [17.3, 2.2]];   // centres
const SEATS = 3, DESK_D = 0.8, DESK_Z = 0.74;
const IDLE_LAMP = 0.3;
// the storage corner, metres from the left wall (x) and the back wall (y): crate stacks, the places for waiting
// crates, and the spot in front a robot walks to
const STORE = { stacks: [[1.2, 0.72], [0.85, 2.6]], waiting: [[2.1, 1.95], [2.15, 2.85], [2.2, 3.75]], spot: [2.9, 2.4] };
const WINDOWS = [2.2, 6.4, 10.6, 14.8, 19.0];   // windows along the cut-away front wall   // an idle desk's lamp pool, against 1 where a run is at work
// the workarea as l2 draws it, relative to its bench's centre: the question desk and lantern to the left, the plan
// wall just right of centre on the back wall (measured against the bake-off's l2 layout, rounds 1-3)
const WORK = { qdesk: [-2.55, 0], board: 0.35 };
const LIFT_X = 18.6, PANEL_X = 20.25;
const PILASTERS = [7.45, 13.3, 16.95];
// a veil over the floor texture: l1's floor is a warm mid grey, which the lamps' warm light shows up on
const FLOOR_TONE = 'rgba(96, 88, 86, 0.19)';
const NEAR = { offset: [-0.231, -0.121, 1.698], height: 5.0 };   // l2's framing, relative to its bench's centre (refitted for pitch 28°)
const MARK = { check: 'done', glow: 'running', cross: 'failed', dash: 'blank', blank: 'blank' };
const ACTIVE = new Set(['running', 'queued', 'stalled']);
const SMALL = ['pen-pot', 'paper-stack', 'sketch', 'mug', 'desk-plant', 'books', 'paper-tray'];
// where small props go on a desk, from its centre (the near half; the monitor and lamp take the back)
const SPOTS = [[-0.55, -0.2], [-0.1, -0.24], [0.32, -0.18], [0.66, -0.04], [0.72, 0.2], [-0.72, 0.12]];
const POSES = ['b2/robot-typing', 'b2/robot-pencil', 'b2/robot-tube'];
// flat on the back wall, so nothing stands behind them: tiles, criteria lights, and the light that falls on the wall
// are painted with the ground (one snapshot) instead of sorted and drawn every frame
const WALL = 'ground';

const plus = (a, b) => a.map((v, i) => v + (b[i] || 0));
// the caps and the slab's edges as planes (see floorLayout): quads, back to front
function box(W, D, H) {
  const T = WALL_T, C = 0.1, cz = H - 0.02, top = cz + C;
  return [
    { quad: [[0, D - 0.04, top], [W, D - 0.04, top], [W, D + T, top], [0, D + T, top]], color: CAP.top },         // back cap
    { quad: [[0, D - 0.04, cz], [W, D - 0.04, cz], [W, D - 0.04, top], [0, D - 0.04, top]], color: CAP.lip },
    { quad: [[-T, 0, top], [0.04, 0, top], [0.04, D + T, top], [-T, D + T, top]], color: CAP.top },            // left cap
    { quad: [[0.04, 0, cz], [0.04, D, cz], [0.04, D, top], [0.04, 0, top]], color: CAP.side },
    { quad: [[0, 0, -SLAB], [W, 0, -SLAB], [W, 0, -RIM], [0, 0, -RIM]], color: SLAB_C.front },                   // slab
    { quad: [[W, 0, -SLAB], [W, D, -SLAB], [W, D, -RIM], [W, 0, -RIM]], color: SLAB_C.side },
    { quad: [[0, -0.05, -RIM], [W + 0.05, -0.05, -RIM], [W + 0.05, -0.05, 0], [0, -0.05, 0]], color: SLAB_C.rim },   // its rim
    { quad: [[W + 0.05, -0.05, -RIM], [W + 0.05, D, -RIM], [W + 0.05, D, 0], [W + 0.05, -0.05, 0]], color: SLAB_C.rimSide },
    { quad: [[0, -0.05, 0], [W + 0.05, -0.05, 0], [W + 0.05, 0, 0], [0, 0, 0]], color: CAP.top },
    { quad: [[W, 0, 0], [W + 0.05, -0.05, 0], [W + 0.05, D, 0], [W, D, 0]], color: CAP.top },
  ];
}
const WALL_T = 0.45, SLAB = 0.9, RIM = 0.14;   // as build_kit.py
const CAP = { top: '#f0eeea', lip: '#dcd8d2', side: '#e6e2dc' };
const SLAB_C = { front: '#9ca0a9', side: '#838c98', rim: '#dcd6d0', rimSide: '#c4c0bc' };

function hash(s) { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }

// room: from workareaOf(state, label, now); kit: the kit manifest's sprites (for slots); colour(host): host colour
export function floorLayout(room, kit, colour) {
  const { w: W, d: D, h: H } = FLOOR;
  const items = [], runs = [], benches = [];
  const add = (id, sprite, at, extra = {}) => { items.push({ id, sprite, at, ...extra }); return id; };

  // --- shell: floor, walls with their caps and cut ends, the slab's edges, pilasters, sheen --------------------
  const planes = [
    { quad: [[0, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], texture: 'floor-tile', origin: [0, D, 0], u: [1, 0, 0], v: [0, -1, 0] },
    { quad: [[0, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], color: FLOOR_TONE },
    // light falling off across the room: a little warmer and brighter at the back, darker towards the front corners
    { quad: [[0, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], gradient: { at: [W * 0.5, D * 0.7, 0], radius: W * 0.62,
      stops: [[0, 'rgba(255, 238, 214, 0.10)'], [0.55, 'rgba(255, 238, 214, 0)'], [1, 'rgba(24, 28, 44, 0.28)']] } },
    { quad: [[0, D, 0], [W, D, 0], [W, D, H], [0, D, H]], texture: 'wall-tile', origin: [0, D, H], u: [1, 0, 0], v: [0, 0, -1] },
    { quad: [[0, 0, 0], [0, D, 0], [0, D, H], [0, 0, H]], texture: 'wall-tile', origin: [0, 0, H], u: [0, 1, 0], v: [0, 0, -1] },
    // the walls' caps and the slab's cut faces are flat, so they are planes the full length of the floor: exact under
    // the camera and without joints (repeated per-bay sprites left a line at every bay). Colours sampled from the
    // Blender renders of those pieces, so the corner and cut-end pieces meet them.
    ...box(W, D, H),
  ];
  add('corner', 'wall-corner', [0, D, 0]);
  add('end-back', 'wall-end-back', [W, D, 0]);
  add('end-left', 'wall-end-left', [0, 0, 0]);
  for (const x of PILASTERS) add(`pilaster-${x}`, 'pilaster', [x, D, 0]);
  for (let x = BAY / 2; x < W; x += BAY) for (let y = BAY / 2; y < D; y += BAY) add(`sheen-${x}-${y}`, 'floor-sheen', [x, y, 0]);
  // occlusion where the walls meet the floor, one bay at a time (the bands are even along their length: no seams)
  for (let x = 0; x < W - 0.01; x += BAY) { add(`ao-floor-x-${x}`, 'ao-floor-x', [x, D, 0]); add(`ao-wall-x-${x}`, 'ao-wall-x', [x, D, 0]); }
  for (let y = 0; y < D - 0.01; y += BAY) { add(`ao-floor-y-${y}`, 'ao-floor-y', [0, y, 0]); add(`ao-wall-y-${y}`, 'ao-wall-y', [0, y, 0]); }
  // daylight through the windows of the cut-away front wall: cool patches reaching into the room
  for (const x of WINDOWS) add(`window-${x}`, 'glow-window', [x, 1.5, 0], { intensity: 0.35 });

  // --- the lift, its floor buttons, the alcove, the library, the orchestrator, the briefing board ---------------
  const lift = [LIFT_X, D, 0];
  add('lift', 'lift', lift, { cell: 0, place: 'lift' });
  add('lift-panel', 'lift-panel', [PANEL_X, D, 0], { place: 'lift' });
  const liftThreshold = plus(lift, kit.lift.slots.threshold);
  // the storage corner, as l1's alcove: stacked crates (furniture) in and beside the alcove, and in front of them one
  // hourglass crate per thing waiting on its human; robots can walk to the store spot in front
  add('alcove', 'alcove', [0, D, 0], { place: 'store' });
  STORE.stacks.forEach(([x, y], i) => add(`crate-stack-${i}`, 'crate-stack', [x, D - y, 0], { place: 'store' }));
  const waiting = room && room.waiting ? room.waiting.length : 0;
  for (let i = 0; i < Math.min(waiting, STORE.waiting.length); i++) {
    const [x, y] = STORE.waiting[i];
    add(i ? `crate-${i}` : 'crate', 'crate', [x, D - y, 0], { place: 'waiting' });
  }
  const store = { spot: [STORE.spot[0], D - STORE.spot[1]], frame: { target: [1.8, D - 1.8, 1.0], height: 4.2 } };
  add('shelf-a', 'shelf', [3.6, D - 0.3, 0], { place: 'library' });
  add('shelf-b', 'shelf', [4.95, D - 0.3, 0], { place: 'library' });
  add('book-cart', 'book-cart', [6.4, D - 1.1, 0], { place: 'library' });
  add('plant-lib', 'plant-tall', [6.95, D - 0.45, 0]);
  // (the podium stands clear of the workarea bench's right end: robots reach its seats along the wall behind it)
  add('podium', 'podium', [14.4, D - 0.75, 0], { place: 'orchestrator' });
  add('plant-podium', 'plant-bush', [15.35, D - 0.45, 0]);
  add('whiteboard', 'whiteboard', [16.35, D - 0.8, 0], { place: 'briefing' });
  add('plant-lift', 'plant-tall', [21.05, D - 0.5, 0]);
  add('plant-front-l', 'plant-tall', [0.6, 0.7, 0]);
  add('plant-front-r', 'plant-bush', [W - 0.6, 0.7, 0]);

  // --- desks for jobs ------------------------------------------------------------------------------------------
  const jobs = room ? room.benches : [];
  const active = jobs.filter(b => ACTIVE.has(b.status)), recent = jobs.filter(b => !ACTIVE.has(b.status));
  const seated = BENCHES.map(() => []);
  // (more jobs than the benches' desks are not shown yet)
  const fill = (list, from) => {
    let b = from;
    for (const j of list) {
      while (b < seated.length && seated[b].length >= 3) b++;
      if (b < seated.length) seated[b].push(j);
    }
  };
  fill(active, 0);
  const firstEmpty = seated.findIndex(l => !l.length);
  if (firstEmpty >= 0) fill(recent, firstEmpty);

  // a bench is SEATS modules of the kit's bench pieces (left end, middles, right end), MODULE apart along x, each
  // anchored at its desk top's far edge on its left seam; one soft shadow lies under the whole bench
  const M = kit['bench-mid'].module_m, piece = kit['bench-mid'].slots;
  const moduleAt = (bx, by, d) => [bx - SEATS * M / 2 + d * M, by + DESK_D / 2, DESK_Z];
  BENCHES.forEach(([bx, by], bi) => {
    const at = [bx, by, 0], key = `bench-${bi}`, onBench = seated[bi];
    const live = onBench.some(j => j.active);
    add(`${key}-shadow`, `shadow-bench-${SEATS}`, at);
    benches.push({ key, at, jobs: onBench.map(j => j.key), frame: { target: plus(at, NEAR.offset), height: NEAR.height }, live });
    add(`spill-${bi}`, 'glow-floor-spill', plus(at, [0, -1.3, 0]), { intensity: live ? 0.9 : 0 });
    for (let d = 0; d < SEATS; d++) {
      const job = onBench[d], on = !!(job && job.active);
      const m = moduleAt(bx, by, d), module = `${key}-m${d}`;
      add(module, d === 0 ? 'bench-left' : d === SEATS - 1 ? 'bench-right' : 'bench-mid', m, { place: `bench:${key}` });
      const seat = plus(m, piece.seat), lampAt = plus(m, piece.lamp), top = plus(m, piece.desk_top);
      add(`lamp-${bi}-${d}`, 'lamp', lampAt);
      // every lamp throws a gentle pool; a desk with a run at work is bright (brightness still means activity)
      add(`shade-${bi}-${d}`, 'glow-shade', plus(lampAt, kit.lamp.slots.shade), { intensity: on ? 1 : IDLE_LAMP / 2 });
      add(`pool-${bi}-${d}`, 'glow-desk-pool', plus(lampAt, [0.3, -0.25, 0.005]), { intensity: on ? 1 : IDLE_LAMP });
      // (right of the seat's pedestal, which stands under the module's left part: chairs between pedestals, as l2)
      add(`chair-near-${bi}-${d}`, 'chair-back', [top[0] + 0.3, by - 0.6, 0], { place: `bench:${key}` });
      if (bi === 0) add(`chair-far-${bi}-${d}`, 'chair-front', [seat[0], seat[1] + 0.12, 0], { place: `bench:${key}` });
      // a monitor at most desks, as l1's benches; a robot at work brings its own laptop or papers
      const h = hash(`${key}:${d}`);
      if (!on && (bi > 0 || d === 2)) add(`monitor-${bi}-${d}`, 'monitor', [top[0] + 0.15, by + 0.12, DESK_Z]);
      // small things on the near half of the desk: more at a busy workarea (l2), a few elsewhere (l1)
      const n = bi === 0 ? 5 : 1 + (h % 3);
      for (let k = 0; k < n; k++) {
        const [dx, dy] = SPOTS[(h + k * 2) % SPOTS.length];
        add(`prop-${bi}-${d}-${k}`, SMALL[(h >>> (3 * k)) % SMALL.length], [top[0] + dx, by + dy, DESK_Z]);
      }
      if (on) {
        // seated behind the desk: split into lower and upper body along the desk top (15 cm in from its far edge,
        // so hands and a laptop on the desk stay above the line), and nothing of the lower body below its near edge
        runs.push({ key: job.key, host: job.host, agent: job.agent, bench: key, module, desk: d, seat,
          chair: bi === 0 ? `chair-far-${bi}-${d}` : null, sprite: POSES[runs.length % POSES.length], tint: colour(job.host),
          cutAt: [seat[0], m[1] - 0.15, DESK_Z], cutFloor: [seat[0], m[1] - DESK_D, DESK_Z], farEdge: m[1], nearEdge: m[1] - DESK_D });
      }
    }
  });

  // --- the workarea as l2: plan wall (a row per desk, a tile per step), criteria lights, washers, question desk --
  const [wx, wy] = BENCHES[0], work = seated[0], live = work.some(j => j.active);
  const board = [wx + WORK.board, D, 0];
  add('board', 'plan-wall', board, { place: 'plan:bench-0' });
  const grid = kit['plan-wall'].slots.tiles;
  for (let r = 0; r < grid.rows; r++) for (let c = 0; c < grid.cols; c++) {
    const job = work[grid.rows - 1 - r];   // the top row is desk 0
    const tile = job && job.tiles[c];
    const at = plus(board, plus(grid.first, [grid.col[0] * c, 0, grid.row[2] * r]));
    add(`tile-${c}-${r}`, 'tile-' + (tile ? MARK[tile.mark] : 'blank'), at, { place: tile ? `step:${job.key}:${tile.index}` : 'plan:bench-0', layer: WALL });
  }
  const met = work.reduce((s, j) => s + j.criteria.met, 0), total = work.reduce((s, j) => s + j.criteria.total, 0);
  const lit = total ? Math.round(5 * met / total) : 0;
  kit['plan-wall'].slots.lights.forEach((p, i) => add(`crit-${i}`, i < lit ? 'criteria-on' : 'criteria-off', plus(board, p), { layer: WALL }));
  for (const dx of [-1.4, 0, 1.4]) add(`wash-${dx}`, 'glow-wall-wash', [board[0] + dx, D - 0.01, 3.05], { intensity: live ? 1 : 0, layer: WALL });
  const q = [wx + WORK.qdesk[0], D - 0.45, 0];
  add('question-desk', 'question-desk', q, { place: 'question-desk' });
  add('plant-qdesk', 'desk-plant', plus(q, [0.35, 0.1, 0.9]));
  if (room && room.desk.lantern) {   // over the question desk when something needs you
    const at = plus(q, kit['question-desk'].slots.lantern);
    add('lantern', 'lantern', at, { place: 'attention', lantern: room.desk.lantern });
    add('lantern-halo', 'glow-lantern-halo', [at[0], D - 0.01, at[2] - 0.1], { layer: WALL });
  }

  // footprints: from the lift to every desk where a run happened in the last hour
  const trails = (room ? room.footprints : []).map(f => {
    const bi = seated.findIndex(l => l.some(j => j.key === f.to));
    if (bi < 0) return null;
    const d = seated[bi].findIndex(j => j.key === f.to);
    return { to: f.to, from: liftThreshold, seat: plus(moduleAt(...BENCHES[bi], d), piece.seat), at: f.at };
  }).filter(Boolean);

  return {
    size: FLOOR, planes, items, runs, benches, trails, lift: { id: 'lift', at: lift, threshold: liftThreshold },
    panel: { at: [PANEL_X, D, 0], buttons: kit['lift-panel'].slots.buttons },
    indicator: plus(lift, kit.lift.slots.indicator), store,
    frames: { far: { box: [0, 0, -0.9, W, D, H], margin: 0.02 }, near: benches[0].frame },   // (-0.9: the slab)
    moduleM: M,
    bounds: [0, 0, 0, W, D, H],
  };
}
