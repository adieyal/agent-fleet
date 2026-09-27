// The floor kit (fleet/web/assets/world/kit/) laid out in a small room, as a check that its pieces share one angle
// and scale: one wall bay as l2 (lift, question desk under the lantern, plan wall with tiles and criteria lights,
// the bench with robots, lamps and their glow) and the rest of the kit around it. The robots are the bake-off's
// placeholders. Zooms from the whole room to l2's framing of the bench.
//   ?shot    no panel
import { World } from '/js/world/engine.js';

const params = new URLSearchParams(location.search);
const W = 14.4, D = 9.6, H = 3.2;   // the room: four bays along the back wall (y = D), the left wall at x = 0
const BENCH = [7.9, 6.7, 0], BOARD = [7.9, D, 0], QDESK = [6.6, D - 0.75, 0], LIFT = [4.2, D, 0];   // (the alcove takes the corner)
const FAR = { box: [0, 0, 0, W, D, H], margin: 0.03 };
const NEAR = { target: [7.2, 7.4, 1.5], height: 5.486 };
const HOSTS = ['#27b3b8', '#2e62dc', '#7a8a32'];
const ROBOTS = ['b2/robot-typing', 'b2/robot-pencil', 'b2/robot-tube'];

if (params.has('shot')) document.body.classList.add('shot');
const world = new World(document.getElementById('world'), { camera: { far: FAR, near: NEAR, bounds: [0, 0, 0, W, D, H] } });
window.kit = { engine: world, ready: false, error: null };
const add = (id, sprite, at, extra = {}) => world.add({ id, sprite, at, ...extra });
const plus = (a, b) => a.map((v, i) => v + b[i]);

async function main() {
  const m = await world.load('/assets/world/kit/manifest.json');
  await world.load('/art/bakeoff/world.json', { prefix: 'b2/' });
  const S = m.sprites;
  // shell: textured floor and walls, then the Blender pieces along them
  world.addPlane({ quad: [[0, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], texture: 'floor-tile', origin: [0, D, 0], u: [1, 0, 0], v: [0, -1, 0] });
  world.addPlane({ quad: [[0, D, 0], [W, D, 0], [W, D, H], [0, D, H]], texture: 'wall-tile', origin: [0, D, H], u: [1, 0, 0], v: [0, 0, -1] });
  world.addPlane({ quad: [[0, 0, 0], [0, D, 0], [0, D, H], [0, 0, H]], texture: 'wall-tile', origin: [0, 0, H], u: [0, 1, 0], v: [0, 0, -1] });
  for (let x = 0; x < W; x += 3.6) { add('cap-x' + x, 'wall-cap-x', [x, D, 0]); add('slab-f' + x, 'slab-front', [x, 0, 0]); }
  for (let y = 0; y < D; y += 3.6) { add('cap-y' + y, 'wall-cap-y', [0, y, 0]); add('slab-s' + y, 'slab-side', [W, y, 0]); }
  add('corner', 'wall-corner', [0, D, 0]);
  add('end-back', 'wall-end-back', [W, D, 0]);
  add('end-left', 'wall-end-left', [0, 0, 0]);
  for (const x of [3.6, 10.8]) add('pilaster' + x, 'pilaster', [x, D, 0]);
  add('lift', 'lift', LIFT, { cell: 0, place: 'lift' });
  add('lift-panel', 'lift-panel', [LIFT[0] + 1.65, D, 0], { place: 'lift' });
  for (let x = 1.8; x < W; x += 3.6) for (let y = 1.8; y < D; y += 3.6) add(`sheen-${x}-${y}`, 'floor-sheen', [x, y, 0]);

  // the l2 bay: question desk and lantern, plan wall with tiles and lights, the bench
  add('question-desk', 'question-desk', QDESK, { place: 'question-desk' });
  const lantern = plus(QDESK, S['question-desk'].slots.lantern);
  add('lantern', 'lantern', lantern, { place: 'attention' });
  add('lantern-halo', 'glow-lantern-halo', [lantern[0], D - 0.01, lantern[2] - 0.1]);
  add('plan-wall', 'plan-wall', BOARD, { place: 'plan' });
  const tiles = S['plan-wall'].slots.tiles;
  const state = (c, r) => (r === 2 && c < 6 ? 'running' : r === 3 && c === 4 ? 'failed' : (c * 7 + r * 3) % 5 === 0 ? 'done' : 'blank');
  for (let r = 0; r < tiles.rows; r++) for (let c = 0; c < tiles.cols; c++) {
    const at = plus(BOARD, plus(tiles.first, plus(tiles.col.map(v => v * c), tiles.row.map(v => v * r))));
    add(`tile-${c}-${r}`, 'tile-' + state(c, r), at, { place: 'tile' });
  }
  S['plan-wall'].slots.lights.forEach((p, i) => add('light' + i, i < 3 ? 'criteria-on' : 'criteria-off', plus(BOARD, p)));
  for (const dx of [-1.4, 0, 1.4]) add('wash' + dx, 'glow-wall-wash', [BOARD[0] + dx, D - 0.01, 3.05], { intensity: 0.9 });
  add('bench', 'bench', BENCH, { place: 'workarea' });
  const slots = S.bench.slots;
  slots.seats.forEach((s, i) => {
    world.seat('robot' + i, { sprite: ROBOTS[i], at: plus(BENCH, s), on: 'bench', tint: HOSTS[i], ambient: true, place: 'run' });
    add('chair-near' + i, 'chair-back', plus(BENCH, [s[0] + 0.1, -0.75, 0]));
  });
  slots.lamps.forEach((p, i) => {
    const at = plus(BENCH, p);
    add('lamp' + i, 'lamp', at);
    add('lamp-glow' + i, 'glow-shade', plus(at, S.lamp.slots.shade));
    add('pool' + i, 'glow-desk-pool', plus(at, [0.3, -0.25, 0.005]), { intensity: 0.85 });
  });
  const props = [['laptop', -1.95, -0.1], ['pen-pot', -1.2, 0.05], ['paper-stack', -0.4, -0.2], ['sketch', 0.3, -0.18],
    ['mug', 0.8, 0.0], ['books', 1.5, -0.15], ['desk-plant', 2.05, 0.1], ['paper-tray', 2.35, -0.15]];
  for (const [p, dx, dy] of props) add('prop-' + p, p, plus(BENCH, [dx, dy, 0.74]));
  add('spill', 'glow-floor-spill', plus(BENCH, [0, -1.3, 0]), { intensity: 0.8 });
  // arrival: footprints from the lift towards the bench
  for (let k = 0; k < 5; k++) add('steps' + k, 'footprints-315', [LIFT[0] + 0.4 + k * 0.62, D - 1.0 - k * 0.52, 0]);

  // around it: the library, the orchestrator, the briefing board, a terminal desk, a waiting crate, plants
  add('shelf', 'shelf', [12.6, D - 0.3, 0]);
  add('plant-tall', 'plant-tall', [11.4, D - 0.4, 0]);
  add('plant-bush', 'plant-bush', [3.1, D - 0.45, 0]);
  add('plant-small', 'plant-small', [13.8, D - 0.3, 0]);
  add('book-cart', 'book-cart', [12.9, 7.2, 0]);
  add('librarian-desk', 'librarian-desk', [11.6, 5.4, 0]);
  add('podium', 'podium', [9.8, 3.6, 0]);
  add('whiteboard', 'whiteboard', [12.4, 2.6, 0]);
  add('terminal-desk', 'terminal-desk', [4.4, 3.0, 0]);
  add('terminal-chair', 'chair-back', plus([4.4, 3.0, 0], S['terminal-desk'].slots.seat.map((v, i) => (i === 2 ? 0 : v))));
  add('alcove', 'alcove', [0, D, 0], { place: 'waiting' });
  add('crate', 'crate', [1.2, D - 0.6, 0], { place: 'waiting' });
  add('monitor', 'monitor', [8.0, 3.1, 0.74]);
  add('monitor-desk', 'terminal-desk', [7.8, 3.0, 0]);

  await world.whenLoaded();
  window.kit.ready = true;
  document.getElementById('panel').textContent = `floor kit: ${Object.keys(S).length} sprites, ${world.items.size} placed\n`
    + `drag to pan, wheel to zoom (room ↔ l2)`;
}
main().catch(e => { console.error(e); window.kit.error = String(e.message || e); document.getElementById('panel').textContent = window.kit.error; });
