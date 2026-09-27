// A project floor laid out from its room (workarea-model.js): the walls and lift, three wall lanes each with a
// bench, a plan wall and (the first) the question desk and lantern, open-floor benches, the library, the
// orchestrator's podium, the briefing board and a waiting crate. Pure: returns what to place, where each run sits,
// and the framings; the page places it in the sprite world. See docs/design/sprite-world.md, "Floor layout".
//
// Places follow l2 where l1 and l2 disagree: the lift is at the left end of the back wall, the first wall lane beside
// it, the library at the right end. Every job the room shows gets a desk (a bench is three desks): active jobs fill
// the first wall lane's desks, then the next; recently finished ones the lane after; the rest stand idle.

export const FLOOR = { w: 25.2, d: 10.8, h: 3.2 };   // 7 x 3 bays: l1's proportions at real furniture sizes
const BAY = 3.6;
const LANE_W = 7.8;
const WALL_LANES = [3.6, 11.4];                // wall lanes from these x; the library takes the rest of the wall
// a wall lane as l2 draws it, relative to its bench's centre (x) and the wall (y): measured against the bake-off's
// l2 layout, where the bench stands 1.65 m from the wall with the question desk and lantern left of it
const LANE = { bench: [3.9, -1.75], qdesk: [1.35, -0.45], board: 4.25, pilasters: [-0.5, 2.0] };
const OPEN_BENCHES = [[4.6, 5.2], [12.6, 5.2], [20.6, 5.2], [4.6, 1.9], [12.6, 1.9], [20.6, 1.9]];  // three desks each
const LIFT_X = 2.1;
// a veil over the floor texture: the concepts' floor is a mid grey, which the warm light shows up on
const FLOOR_TONE = 'rgba(64, 68, 96, 0.13)';
const NEAR = { offset: [-0.231, -0.121, 1.698], height: 5.486 };   // l2's framing, relative to its bench's centre
const MARK = { check: 'done', glow: 'running', cross: 'failed', dash: 'blank', blank: 'blank' };
const ACTIVE = new Set(['running', 'queued', 'stalled']);
const PROPS = ['laptop', 'pen-pot', 'paper-stack', 'sketch', 'mug', 'desk-plant', 'books', 'paper-tray'];
const POSES = ['b2/robot-typing', 'b2/robot-pencil', 'b2/robot-tube'];
// flat on the back wall, so nothing stands behind them: tiles, criteria lights, and the light that falls on the wall
// are painted with the ground (one snapshot) instead of sorted and drawn every frame
const WALL = 'ground';

const plus = (a, b) => a.map((v, i) => v + (b[i] || 0));
function hash(s) { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }

// room: from workareaOf(state, label, now); kit: the kit manifest's sprites (for slots); colour(host): host colour
export function floorLayout(room, kit, colour) {
  const { w: W, d: D, h: H } = FLOOR;
  const items = [], runs = [], benches = [];
  const add = (id, sprite, at, extra = {}) => { items.push({ id, sprite, at, ...extra }); return id; };

  // --- shell -------------------------------------------------------------------------------------------------
  const planes = [
    { quad: [[0, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], texture: 'floor-tile', origin: [0, D, 0], u: [1, 0, 0], v: [0, -1, 0] },
    { quad: [[0, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], color: FLOOR_TONE },
    { quad: [[0, D, 0], [W, D, 0], [W, D, H], [0, D, H]], texture: 'wall-tile', origin: [0, D, H], u: [1, 0, 0], v: [0, 0, -1] },
    { quad: [[0, 0, 0], [0, D, 0], [0, D, H], [0, 0, H]], texture: 'wall-tile', origin: [0, 0, H], u: [0, 1, 0], v: [0, 0, -1] },
  ];
  for (let x = 0; x < W - 0.01; x += BAY) { add(`cap-x-${x}`, 'wall-cap-x', [x, D, 0]); add(`slab-f-${x}`, 'slab-front', [x, 0, 0]); }
  for (let y = 0; y < D - 0.01; y += BAY) { add(`cap-y-${y}`, 'wall-cap-y', [0, y, 0]); add(`slab-s-${y}`, 'slab-side', [W, y, 0]); }
  add('corner', 'wall-corner', [0, D, 0]);
  add('end-back', 'wall-end-back', [W, D, 0]);
  add('end-left', 'wall-end-left', [0, 0, 0]);
  for (const x of [...WALL_LANES.flatMap(x0 => LANE.pilasters.map(p => x0 + p)), WALL_LANES[1] + LANE_W]) add(`pilaster-${x}`, 'pilaster', [x, D, 0]);
  const lift = [LIFT_X, D, 0];
  add('lift', 'lift', lift, { cell: 0, place: 'lift' });
  const liftThreshold = plus(lift, kit.lift.slots.threshold);

  // --- desks for jobs ----------------------------------------------------------------------------------------
  const jobs = room ? room.benches : [];
  const active = jobs.filter(b => ACTIVE.has(b.status)), recent = jobs.filter(b => !ACTIVE.has(b.status));
  const lanes = WALL_LANES.map(() => []);
  // (more jobs than the wall lanes' nine desks are not shown yet: the floor would need its open benches)
  const fill = (list, from) => {
    let lane = from;
    for (const j of list) {
      while (lane < lanes.length && lanes[lane].length >= 3) lane++;
      if (lane < lanes.length) lanes[lane].push(j);
    }
  };
  fill(active, 0);
  const firstEmpty = lanes.findIndex(l => !l.length);
  if (firstEmpty >= 0) fill(recent, firstEmpty);

  WALL_LANES.forEach((x0, li) => {
    const benchAt = [x0 + LANE.bench[0], D + LANE.bench[1], 0], boardAt = [x0 + LANE.board, D, 0];
    const key = `lane-${li}`;
    const bench = add(key, 'bench', benchAt, { place: `bench:${key}` });
    const laneJobs = lanes[li];
    const live = laneJobs.some(j => j.active);
    benches.push({ key, at: benchAt, jobs: laneJobs.map(j => j.key), frame: { target: plus(benchAt, NEAR.offset), height: NEAR.height }, live });
    // the plan wall: a row per desk, one tile per step
    add(`board-${li}`, 'plan-wall', boardAt, { place: `plan:${key}` });
    const grid = kit['plan-wall'].slots.tiles;
    for (let r = 0; r < grid.rows; r++) for (let c = 0; c < grid.cols; c++) {
      const job = laneJobs[grid.rows - 1 - r];   // the top row is desk 0
      const tile = job && job.tiles[c];
      const at = plus(boardAt, plus(grid.first, [grid.col[0] * c, 0, grid.row[2] * r]));
      add(`tile-${li}-${c}-${r}`, 'tile-' + (tile ? MARK[tile.mark] : 'blank'), at, { place: tile ? `step:${job.key}:${tile.index}` : `plan:${key}`, layer: WALL });
    }
    const met = laneJobs.reduce((s, j) => s + j.criteria.met, 0), total = laneJobs.reduce((s, j) => s + j.criteria.total, 0);
    const lit = total ? Math.round(5 * met / total) : 0;
    kit['plan-wall'].slots.lights.forEach((p, i) => add(`crit-${li}-${i}`, i < lit ? 'criteria-on' : 'criteria-off', plus(boardAt, p), { layer: WALL }));
    for (const dx of [-1.4, 0, 1.4]) add(`wash-${li}-${dx}`, 'glow-wall-wash', [boardAt[0] + dx, D - 0.01, 3.05], { intensity: live ? 1 : 0, layer: WALL });
    add(`spill-${li}`, 'glow-floor-spill', plus(benchAt, [0, -1.3, 0]), { intensity: live ? 0.9 : 0 });
    const slots = kit.bench.slots;
    for (let d = 0; d < 3; d++) {
      const job = laneJobs[d];
      const seat = plus(benchAt, slots.seats[d]), lampAt = plus(benchAt, slots.lamps[d]);
      const on = !!(job && job.active);
      add(`lamp-${li}-${d}`, 'lamp', lampAt);
      add(`shade-${li}-${d}`, 'glow-shade', plus(lampAt, kit.lamp.slots.shade), { intensity: on ? 1 : 0 });
      add(`pool-${li}-${d}`, 'glow-desk-pool', plus(lampAt, [0.3, -0.25, 0.005]), { intensity: on ? 1 : 0 });
      add(`chair-far-${li}-${d}`, 'chair-front', [seat[0], seat[1] + 0.12, 0], { place: `bench:${key}` });
      add(`chair-near-${li}-${d}`, 'chair-back', plus(benchAt, [slots.seats[d][0] + 0.1, -0.6, 0]), { place: `bench:${key}` });
      const h = hash(`${key}:${d}`), n = job ? 2 + (h % 2) : h % 2;
      for (let k = 0; k < n; k++) {
        const p = PROPS[(h >>> (3 * k)) % PROPS.length];
        add(`prop-${li}-${d}-${k}`, p, plus(benchAt, [slots.desk_top[d][0] - 0.55 + k * 0.5 + ((h >>> 9) % 3) * 0.05, -0.22 + ((h >>> (5 + k)) % 3) * 0.06, 0.74]));
      }
      if (job && job.active) {
        runs.push({ key: job.key, host: job.host, agent: job.agent, bench: key, desk: d, seat, chair: `chair-far-${li}-${d}`,
          sprite: POSES[runs.length % POSES.length], tint: colour(job.host) });
      }
    }
    if (li === 0) {   // the room's question desk, with the lantern over it when something needs you
      const q = [x0 + LANE.qdesk[0], D + LANE.qdesk[1], 0];
      add('question-desk', 'question-desk', q, { place: 'question-desk' });
      if (room && room.desk.lantern) {
        const at = plus(q, kit['question-desk'].slots.lantern);
        add('lantern', 'lantern', at, { place: 'attention', lantern: room.desk.lantern });
        add('lantern-halo', 'glow-lantern-halo', [at[0], D - 0.01, at[2] - 0.1], { layer: WALL });
      }
      add('plant-qdesk', 'desk-plant', plus(q, [0.35, 0.1, 0.9]));
      add('plant-lift', 'plant-bush', [x0 - 0.1, D - 0.45, 0]);
    } else add(`shelf-lane-${li}`, 'shelf', [x0 + 1.0, D - 0.3, 0]);
  });

  // --- open floor: benches of three terminal desks, idle ------------------------------------------------------
  OPEN_BENCHES.forEach(([x, y], i) => {
    for (let d = 0; d < 3; d++) {
      const at = [x - 1.6 + d * 1.6, y, 0];
      add(`term-${i}-${d}`, 'terminal-desk', at, { place: `bench:open-${i}` });
      add(`term-chair-${i}-${d}`, 'chair-back', [at[0] + 0.05, y - 0.62, 0], { place: `bench:open-${i}` });
      if ((hash(`open${i}${d}`) & 3) === 0) add(`term-prop-${i}-${d}`, PROPS[hash(`p${i}${d}`) % PROPS.length], [at[0] + 0.5, y - 0.15, 0.74]);
    }
    benches.push({ key: `open-${i}`, at: [x, y, 0], jobs: [], frame: { target: plus([x, y, 0], NEAR.offset), height: NEAR.height }, live: false });
  });

  // --- stations ---------------------------------------------------------------------------------------------
  const lib = WALL_LANES[1] + LANE_W;   // 19.2: the library, to the wall's cut end
  add('shelf-a', 'shelf', [lib + 1.0, D - 0.3, 0], { place: 'library' });
  add('shelf-b', 'shelf', [lib + 2.3, D - 0.3, 0], { place: 'library' });
  add('book-cart', 'book-cart', [lib + 4.4, D - 1.6, 0], { place: 'library' });
  add('librarian-desk', 'librarian-desk', [lib + 2.2, D - 3.0, 0], { place: 'library' });
  add('plant-lib', 'plant-tall', [lib + 5.5, D - 0.45, 0]);
  add('podium', 'podium', [3.6, 7.0, 0], { place: 'orchestrator' });
  add('whiteboard', 'whiteboard', [0.9, 7.6, 0], { place: 'briefing' });
  if (room && room.waiting) add('crate', 'crate', [W - 1.1, D - 1.1, 0], { place: 'waiting' });
  add('plant-front-l', 'plant-tall', [0.6, 0.7, 0]);
  add('plant-front-r', 'plant-bush', [W - 0.6, 0.7, 0]);

  // footprints: from the lift to every desk where a run happened in the last hour
  const trails = (room ? room.footprints : []).map(f => {
    const li = lanes.findIndex(l => l.some(j => j.key === f.to));
    const d = li < 0 ? -1 : lanes[li].findIndex(j => j.key === f.to);
    return li < 0 ? null : { to: f.to, from: liftThreshold, seat: plus([WALL_LANES[li] + LANE.bench[0], D + LANE.bench[1], 0], kit.bench.slots.seats[d]) };
  }).filter(Boolean);

  return {
    size: FLOOR, planes, items, runs, benches, trails, lift: { id: 'lift', at: lift, threshold: liftThreshold },
    frames: { far: { box: [0, 0, -0.35, W, D, H], margin: 0.02 }, near: benches[0].frame },
    bounds: [0, 0, 0, W, D, H],
  };
}
