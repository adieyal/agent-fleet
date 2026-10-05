// The sprite engine (fleet/web/js/world/) over an l2-like scene from the floor kit: a textured floor and wall, the bench, three
// seated v2 robots (robots.js) in their hosts' colours and kits on raised chairs, plants, the lantern and lamp glow. Drag to pan, wheel or pinch to zoom from the
// whole room to l2's framing; a click names what it hit.
//   ?anim=0   robots hold still (an idle scene: nothing redraws)
//   ?shot     no panel
import { World } from '/js/world/engine.js';
import { Robots, seat } from '/js/world/robots.js';

const params = new URLSearchParams(location.search);
const ROOM = { x0: -1, x1: 12.5, y0: -3.5, y1: 6.0 }, WALL_H = 3.2;
const FAR = { box: [ROOM.x0, ROOM.y0, 0, ROOM.x1, ROOM.y1, WALL_H], margin: 0.03 };   // the whole room, as l1.png frames a floor
const NEAR = { target: [6.269, 4.229, 1.698], height: 5.486 };                    // l2.png's framing
const BENCH = { x0: 3.8, deskW: 1.6, y: 4.35, deskD: 0.8, deskH: 0.74 };   // the kit's bench: three 1.6 m desks, centred on y
// desk, host name, its colour and kit and the agent, clip
const CAST = [[1, 'teal', { host: '#27b3b8', kit: 'antenna', agent: 'codex' }, 'Typing'], [2, 'blue', { host: '#2e62dc', kit: 'backpack', agent: 'claude' }, 'Writing'],
  [3, 'olive', { host: '#7a8a32', kit: 'crest', agent: 'claude' }, 'Holding']];
const PLANTS = [[2.3, 5.4], [9.95, 3.9], [-0.4, 1.2], [11.6, 0.4]];
const lamp = d => [BENCH.x0 + (d - 1) * BENCH.deskW + 0.18, BENCH.y + 0.02, BENCH.deskH + 0.215];

if (params.has('shot')) document.body.classList.add('shot');
const el = document.getElementById('world');
const world = new World(el, {
  camera: { far: FAR, near: NEAR, bounds: [ROOM.x0, ROOM.y0, 0, ROOM.x1, ROOM.y1, WALL_H] },
  budgetMs: Number(params.get('budget') || 8),
});
window.world = { engine: world, ready: false, error: null, taps: [] };

async function main() {
  const kit = (await world.load('/assets/world/kit/manifest.json', { prefix: 'kit/' })).sprites;
  // (a prop rendered from a model brings its contact shadow, a ground sprite of its own)
  const add = (item) => {
    world.add(item);
    const shadow = kit[item.sprite.slice(4)]?.shadow;
    if (shadow) world.add({ id: item.id + '-shadow', sprite: 'kit/' + shadow, at: item.at });
  };
  const robots = await Robots.load(world), sf = robots.seatFurniture;
  const { x0, x1, y0, y1 } = ROOM;
  world.addPlane({ quad: [[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]], texture: 'kit/floor-tile', origin: [x0, y1, 0], u: [1, 0, 0], v: [0, -1, 0] });
  world.addPlane({ quad: [[x0, y1, 0], [x1, y1, 0], [x1, y1, WALL_H], [x0, y1, WALL_H]], texture: 'kit/wall-tile', origin: [x0, y1, WALL_H], u: [1, 0, 0], v: [0, 0, -1] });
  add({ id: 'bench', sprite: 'kit/bench', at: [BENCH.x0 + 1.5 * BENCH.deskW, BENCH.y, 0], place: 'workarea:supplier' });
  const still = params.get('anim') === '0';
  CAST.forEach(([d, name, look, clip], i) => {   // behind the desk's far edge, facing the viewer
    const at = [BENCH.x0 + (d - 1) * BENCH.deskW + BENCH.deskW / 2, BENCH.y + BENCH.deskD / 2 + sf.desk_edge_ahead_m, 0];
    const chair = [at[0], at[1] + sf.chair_behind_m, 0];
    world.add({ id: 'chair-' + i, sprite: 'kit/chair-front', at: chair, attach: { to: 'bench', order: -2 } });
    seat(world, robots, { id: 'robot-' + i, look, clip, seat: at, chair, desk: 'bench', place: 'run:' + name, still });
  });
  PLANTS.forEach(([x, y], i) => add({ id: 'plant-' + i, sprite: 'kit/plant-bush', at: [x, y, 0] }));
  world.add({ id: 'lantern', sprite: 'kit/lantern', at: [3.1, 5.25, 2.25], place: 'attention' });
  for (const d of [1, 2, 3]) world.glow({ id: 'lamp-' + d, at: lamp(d), radius: 0.3, color: '#ffaa50' });
  world.onTap = (hit, x, y) => { window.world.taps.push({ ...hit, x, y }); };
  addEventListener('resize', () => world.resize());
  await world.whenLoaded();
  window.world.ready = true;
  const stats = document.getElementById('stats');
  setInterval(() => {
    const s = world.stats, v = world.camera.cur;
    stats.textContent = `zoom ${world.camera.zoomLevel().toFixed(2)}  ${v.ppm.toFixed(0)} px/m\n`
      + `frames ${s.frames} (full ${s.full}, partial ${s.partial})  last ${(world.lastWork || 0).toFixed(2)} ms\n`
      + `ambient throttle ×${world.throttle}  loaded ${s.loaded.length} files`;
    document.getElementById('missing').textContent = s.missing.length ? '\n' + s.missing.join('\n') : '';
  }, 500);
}
main().catch(e => { console.error(e); window.world.error = String(e.message || e); document.getElementById('missing').textContent = window.world.error; });
