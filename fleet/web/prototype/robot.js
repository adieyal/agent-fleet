// Robot sprite preview (/prototype/robot): the paper-doll sprite atlases from art/scripts/build_robot_sprites.py
// composited on one 2D canvas, no WebGL: host tint through the mask, one face and kit, carried items, and a
// robot walking a path round a bench (in front of it, round its end, behind it) and sitting down to type at it,
// beside B2's typing robot (desk 1) and a seated sprite robot (desk 3).
//   ?mode=walk|pose  &clip=Idle&dir=S  &host=%23ff9340&kit=antenna&face=codex&item=item_box  &zoom=0..1  &t=seconds (frozen)  &shot

const params = new URLSearchParams(location.search);
const SPRITES = '/assets/world/robot/sprites/';
const B2 = '/art/bakeoff/B2/';
const FROZEN = params.has('t') ? Number(params.get('t')) : null;
const PITCH = 44.5 * Math.PI / 180, YAW = 21.25 * Math.PI / 180;  // l2, as art/ and the bake-off
const BENCH = { x0: 3.8, deskW: 1.8, y: 4.35, deskD: 0.8, deskH: 0.74, seatH: 0.47 };
const seat = d => [BENCH.x0 + (d - 1) * BENCH.deskW + BENCH.deskW / 2 - 0.25, BENCH.y + BENCH.deskD / 2 + 0.34 - 0.2, BENCH.seatH];
const HOSTS = { orange: '#ff9340', teal: '#2dd4bf', violet: '#a78bfa', yellow: '#facc15', blue: '#2e62dc' };
const KITS = ['backpack', 'antenna', 'halo', 'crest'];
const FACES = { codex: 'face_eyes', claude: 'face_band' };
const CARRIED = ['item_box', 'item_book', 'item_sheet'];
const WORK = ['item_laptop', 'item_pencil', 'item_flask'];  // part of the clip they come with
const FACING = { S: [0, -1], E: [1, 0], N: [0, 1], W: [-1, 0] };
const SPEED = 0.9;  // m/s
const POSE_AT = [6.4, 2.9];

const RIGHT = [Math.cos(YAW), Math.sin(YAW), 0];
const UP = [-Math.sin(PITCH) * Math.sin(YAW), Math.sin(PITCH) * Math.cos(YAW), Math.cos(PITCH)];
const BACK = [Math.sin(YAW) * Math.cos(PITCH), -Math.cos(YAW) * Math.cos(PITCH), Math.sin(PITCH)];  // towards the camera
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const depth = p => dot(p, BACK);  // larger is nearer the camera

const canvas = document.getElementById('view');
const g = canvas.getContext('2d');
const view = { W: 0, H: 0, target: [6.2, 4.3, 0.6], height: 5, ppm: 1 };  // the bench and the whole path
const state = {
  mode: params.get('mode') || 'walk', host: params.get('host') || HOSTS.teal, kit: params.get('kit') || 'antenna',
  face: params.get('face') || 'codex', clip: params.get('clip') || 'Walking', dir: params.get('dir') || 'S',
  item: params.get('item') || 'none', zoom: params.has('zoom') ? Number(params.get('zoom')) : 0.2, anchors: params.has('anchors'),
};
function resize() {
  view.W = canvas.width = innerWidth;
  view.H = canvas.height = innerHeight;
  setZoom(state.zoom);
}
function setZoom(z) {
  state.zoom = z;
  view.height = Math.exp(Math.log(9) + (Math.log(1.6) - Math.log(9)) * z);  // metres of floor on screen, far to close
  view.ppm = view.H / view.height;
  floorCache = null;
}
function screen(p) {
  const d = [p[0] - view.target[0], p[1] - view.target[1], p[2] - view.target[2]];
  return [view.W / 2 + dot(d, RIGHT) * view.ppm, view.H / 2 - dot(d, UP) * view.ppm];
}

// --- loading ---------------------------------------------------------------------------------------------

const image = src => new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => fail(new Error('missing ' + src)); i.src = src; });
const json = url => fetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error('missing ' + url))));
let man = null, b2 = null;
const pages = {};  // res -> { color: [img], mask: [img], shadow: [img] }, loaded on demand

async function ensure(res) {
  if (!pages[res]) {
    const r = man.resolutions[res];
    pages[res] = Promise.all([
      Promise.all(r.pages.map(p => image(SPRITES + p.color))),
      Promise.all(r.pages.map(p => image(SPRITES + p.mask))),
      Promise.all(r.shadow_pages.map(p => image(SPRITES + p.image))),
    ]).then(([color, mask, shadow]) => ({ color, mask, shadow }));
  }
  return pages[res];
}
const loaded = {};  // res -> resolved pages, for the draw loop
function pickRes() {  // the smallest set at least as dense as the screen; 4x only when zoomed in
  const need = view.ppm * devicePixelRatio;
  const ppm1 = man.camera.px_per_m_1x;
  const keys = Object.keys(man.resolutions).sort((a, b) => man.resolutions[a].scale - man.resolutions[b].scale);
  return keys.find(k => ppm1 * man.resolutions[k].scale >= need) || keys[keys.length - 1];
}

// --- tint: rgb * (1 - mask + mask * host / grey), baked into a copy of each colour page per host ---------

const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const ri = res => Object.keys(man.resolutions).indexOf(res);  // a layer's entries follow the resolutions' order
// One layer image tinted for a host, made the first time it is drawn and kept: the cost follows what is shown.
const tintCache = new Map();
function tintedLayer(res, name, e, host) {
  const [p, x, y, w, h] = e;
  const key = `${res}|${p}|${x}|${y}|${host}`;
  if (tintCache.has(key)) return tintCache.get(key);
  const { color, mask } = loaded[res];
  const c = new OffscreenCanvas(w, h), o = c.getContext('2d', { willReadFrequently: true });
  let m = null;
  if (!man.tinted_whole.includes(name)) {  // the mask is stored mask_scale times smaller: stretched over the layer
    const ms = man.resolutions[res].mask_scale;
    o.drawImage(mask[p], x / ms, y / ms, w / ms, h / ms, 0, 0, w, h);
    m = o.getImageData(0, 0, w, h).data;
    o.clearRect(0, 0, w, h);
  }
  o.drawImage(color[p], x, y, w, h, 0, 0, w, h);
  const d = o.getImageData(0, 0, w, h);
  const grey = hex(man.grey), t = hex(host).map((v, i) => v / grey[i]);
  for (let i = 0; i < d.data.length; i += 4) {
    const k = m ? m[i] / 255 : 1;
    if (!k) continue;
    for (let j = 0; j < 3; j++) d.data[i + j] = Math.min(255, d.data[i + j] * (1 - k + k * t[j]));
  }
  o.putImageData(d, 0, 0);
  tintCache.set(key, c);
  return c;
}

// --- one robot: which frame, which layers, where ---------------------------------------------------------

function frameAt(clip, time) {
  const c = man.clips[clip];
  let i = Math.floor(time * c.fps);
  if (c.loop) i %= c.frames;
  else i = Math.min(i, c.frames - 1);
  return i;
}
// the layers of one robot, split at the desk top when seated: [below, above]
function layersOf(clip, frame, opts) {
  const f = frame.layers;
  const face = FACES[opts.face], kit = 'acc_' + opts.kit;
  const items = [...(CARRIED.includes(opts.item) ? [opts.item] : []), ...man.clips[clip].items.filter(n => WORK.includes(n))];
  const below = ['shadow', 'body_low'], above = ['body', 'body_high', face, kit, ...items];
  return [below.filter(n => f[n]), above.filter(n => f[n])];
}
function drawLayers(ctx, names, frame, res, host, ox0, oy0, k) {
  const lp = loaded[res];
  for (const n of names) {
    const e = frame.layers[n][ri(res)], [p, x, y, w, h, ox, oy] = e;
    if (man.masked.includes(n) || man.tinted_whole.includes(n)) {
      ctx.drawImage(tintedLayer(res, n, e, host), ox0 + ox * k, oy0 + oy * k, w * k, h * k);
      continue;
    }
    const src = n === 'shadow' ? lp.shadow[p] : lp.color[p];
    const s = n === 'shadow' ? man.shadow_scale : 1;
    ctx.drawImage(src, x, y, w, h, ox0 + ox * k, oy0 + oy * k, w * s * k, h * s * k);
  }
}
// a robot's sprite placed on the floor at `foot` (world), lifted by `lift` metres (a chair)
function robotDraw(r, part) {
  return () => {
    const res = pickRes();
    if (!loaded[res]) return;
    const c = man.clips[r.clip], dd = c.dirs[r.dir], f = dd.frames[r.frame];
    const sc = man.resolutions[res].scale, k = view.ppm / (man.camera.px_per_m_1x * sc);
    const [sx, sy] = screen([r.foot[0], r.foot[1], r.lift]);
    const [below, above] = layersOf(r.clip, f, r);
    drawLayers(g, part === 'below' ? below : part === 'above' ? above : [...below, ...above], f, res, r.host,
      sx - dd.foot[0] * sc * k, sy - dd.foot[1] * sc * k, k);
    if (state.anchors && part !== 'below') drawAnchors(r, dd, f, sx, sy, view.ppm / man.camera.px_per_m_1x);
  };
}
function drawAnchors(r, dd, f, sx, sy, k) {
  const at = p => [sx + (p[0] - dd.foot[0]) * k, sy + (p[1] - dd.foot[1]) * k];
  g.save();
  g.lineWidth = 1.5;
  g.strokeStyle = '#e02424';
  g.beginPath(); g.ellipse(sx, sy, dd.footprint[0] * k, dd.footprint[1] * k, 0, 0, 2 * Math.PI); g.stroke();
  const [hx, hy] = at(f.anchors.head_top);
  g.strokeStyle = '#1d6fe0'; g.beginPath(); g.moveTo(hx - 8, hy); g.lineTo(hx + 8, hy); g.stroke();
  g.strokeStyle = '#f08c00';
  for (const hand of ['hand_l', 'hand_r']) { const [x, y] = at(f.anchors[hand]); g.beginPath(); g.arc(x, y, 4, 0, 2 * Math.PI); g.stroke(); }
  const [bx, by] = at(f.hit);
  g.strokeStyle = '#15a34a'; g.strokeRect(bx, by, f.hit[2] * k, f.hit[3] * k);
  g.restore();
}

// --- the walk: along the front of the bench, round its end, behind it, then sit and type at desk 2 --------

const SEAT2 = seat(2);
let seatFoot = null, seatLift = 0;
function path() {
  const sp = man.seat_point_m;
  seatFoot = [SEAT2[0] - sp[0], SEAT2[1] - sp[1]];
  seatLift = SEAT2[2] - sp[2];
  const back = 5.55, front = 2.6, left = 2.2, right = 10.1;
  const pts = [[seatFoot[0], back], [left, back], [left, front], [right, front], [right, back], [seatFoot[0], back], seatFoot];
  const legs = [];
  for (let i = 1; i < pts.length; i++) legs.push({ walk: [pts[i - 1], pts[i]] });
  const sit = (man.clips.Sitting.frames - 1) / man.clips.Sitting.fps;
  // (no stand-up clip is rendered yet: it stands straight back up)
  return [{ clip: 'Idle', secs: 0.6, lift: [0, 0], ease: 1 }, ...legs,
    { clip: 'Sitting', secs: sit + 0.3, lift: [0, 1], ease: sit }, { clip: 'Typing', secs: 5, lift: [1, 1], ease: 1 },
    { clip: 'Idle', secs: 0.4, lift: [1, 0], ease: 0.2 }]
    .map(s => (s.walk ? { ...s, secs: Math.hypot(s.walk[1][0] - s.walk[0][0], s.walk[1][1] - s.walk[0][1]) / SPEED } : s));
}
let legs = null;
// where the robot is at time t: walking a leg, or at desk 2 (standing at its seat, sitting down, typing)
function walker(t) {
  const total = legs.reduce((s, l) => s + l.secs, 0);
  let u = ((t % total) + total) % total;
  for (const l of legs) {
    if (u > l.secs) { u -= l.secs; continue; }
    if (l.walk) {
      const [a, b] = l.walk, k = u / l.secs, dx = b[0] - a[0], dy = b[1] - a[1];
      const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'E' : 'W') : (dy > 0 ? 'N' : 'S');
      return { clip: 'Walking', dir, time: t, foot: [a[0] + dx * k, a[1] + dy * k], lift: 0 };
    }
    const k = Math.min(1, u / l.ease);  // the chair lift eases in as it sits, out as it stands
    return { clip: l.clip, dir: 'S', time: u, foot: seatFoot, lift: seatLift * (l.lift[0] + (l.lift[1] - l.lift[0]) * k), atDesk: true };
  }
}

// --- the scene: floor, bench, B2's robot, two sprite robots; ordered far to near ---------------------------

let floorCache = null, floorImg = null, benchImg = null, b2Robot = null;
function drawFloor() {
  if (!floorCache) {
    floorCache = new OffscreenCanvas(view.W, view.H);
    const o = floorCache.getContext('2d');
    o.fillStyle = '#dcebf8';
    o.fillRect(0, 0, view.W, view.H);
    // the floor plane is an affine image of the tile: x and y along the room's axes, one tile per `metres`
    const tile = b2.textures['floor-tile'], s = tile.metres / tile.size[0];
    const [ox, oy] = screen([0, 0, 0]), [ax, ay] = screen([1, 0, 0]), [bx, by] = screen([0, 1, 0]);
    o.setTransform((ax - ox) * s, (ay - oy) * s, (bx - ox) * s, (by - oy) * s, ox, oy);
    o.fillStyle = o.createPattern(floorImg, 'repeat');
    o.fillRect(-20 / s, -20 / s, 40 / s, 40 / s);
  }
  g.drawImage(floorCache, 0, 0);
}
function placeB2(img, s, at, clip = null) {  // a B2 sprite whose ref_px sits on a world point; `clip` keeps one side of its desk cut
  const k = view.ppm / b2.px_per_m;
  const [x, y] = screen(at);
  const dx = x - s.ref_px[0] * k, dy = y - s.ref_px[1] * k;
  g.save();
  if (clip) {
    const c = s.cut, lx0 = dx, lx1 = dx + img.width * k;
    const ly = sx => dy + k * (c.y + c.slope * ((sx - dx) / k - c.x));
    const edge = clip === 'above' ? dy - 10 : dy + img.height * k + 10;
    g.beginPath(); g.moveTo(lx0, ly(lx0)); g.lineTo(lx1, ly(lx1)); g.lineTo(lx1, edge); g.lineTo(lx0, edge); g.closePath();
    g.clip();
  }
  g.drawImage(img, dx, dy, img.width * k, img.height * k);
  g.restore();
}
// the nearest point of the bench's footprint to `p`: a long bench can't be ordered by its centre
function benchNear(p) {
  const x = Math.max(BENCH.x0, Math.min(BENCH.x0 + 3 * BENCH.deskW, p[0]));
  const y = Math.max(BENCH.y - BENCH.deskD / 2, Math.min(BENCH.y + BENCH.deskD / 2, p[1]));
  return [x, y, 0];
}
function items(w, t) {
  const r = { ...w, host: state.host, kit: state.kit, face: state.face, item: state.item };
  r.frame = frameAt(r.clip, w.time);
  const benchKey = depth(benchNear([r.foot[0], r.foot[1], 0]));
  const desk3 = seat(3), sp = man.seat_point_m;
  const still = { clip: 'Typing', dir: 'S', frame: frameAt('Typing', t), foot: [desk3[0] - sp[0], desk3[1] - sp[1]], lift: desk3[2] - sp[2],
    host: HOSTS.violet, kit: 'crest', face: 'claude', item: 'none' };
  const out = [
    { label: 'bench', key: benchKey, draw: () => placeB2(benchImg, b2.sprites.bench, [BENCH.x0 + 1.5 * BENCH.deskW, BENCH.y + BENCH.deskD / 2, BENCH.deskH]) },
    { label: 'b2 below', key: benchKey - 0.003, draw: () => placeB2(b2Robot, b2.sprites['robot-typing'], seat(1), 'below') },
    { label: 'b2 above', key: benchKey + 0.003, draw: () => placeB2(b2Robot, b2.sprites['robot-typing'], seat(1), 'above') },
    { label: 'desk3 below', key: benchKey - 0.002, draw: robotDraw(still, 'below') },
    { label: 'desk3 above', key: benchKey + 0.002, draw: robotDraw(still, 'above') },
  ];
  if (w.atDesk && man.clips[r.clip].seated) {  // seated: legs under the desk, the rest over it
    out.push({ label: 'robot below', key: benchKey - 0.001, draw: robotDraw(r, 'below') },
      { label: 'robot above', key: benchKey + 0.001, draw: robotDraw(r, 'above') });
  } else if (w.atDesk) {  // standing at its seat, behind the desk
    out.push({ label: 'robot', key: benchKey - 0.001, draw: robotDraw(r, 'all') });
  } else {
    out.push({ label: 'robot', key: depth([r.foot[0], r.foot[1], 0]), draw: robotDraw(r, 'all') });
  }
  out.sort((a, b) => a.key - b.key);
  return { list: out, robot: r };
}
function pose(t) {
  const c = man.clips[state.clip];
  const dir = c.dirs[state.dir] ? state.dir : Object.keys(c.dirs)[0];
  if (!c.seated) return { clip: state.clip, dir, time: t, foot: POSE_AT, lift: 0 };
  const sp = man.seat_point_m;
  return { clip: state.clip, dir, time: t, foot: [SEAT2[0] - sp[0], SEAT2[1] - sp[1]], lift: SEAT2[2] - sp[2], atDesk: true };
}

// --- loop and measuring ------------------------------------------------------------------------------------

const frameLog = [];
let last = null, current = null;
function tick(now) {
  const t0 = performance.now();
  const t = FROZEN ?? now / 1000;
  const res = pickRes();
  if (!loaded[res]) ensure(res).then(p => { loaded[res] = p; });
  drawFloor();
  current = items(state.mode === 'walk' ? walker(t) : pose(t), t);
  for (const i of current.list) i.draw();
  const work = performance.now() - t0;
  if (last !== null) frameLog.push([now - last, work]);
  if (frameLog.length > 600) frameLog.shift();
  last = now;
  if (frameLog.length % 20 === 1) showStats();
  requestAnimationFrame(tick);
}
function frameStats() {
  const xs = frameLog.slice(1);
  if (!xs.length) return null;
  const q = (arr, p) => arr.slice().sort((a, b) => a - b)[Math.min(arr.length - 1, Math.floor(p * arr.length))];
  const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
  const iv = xs.map(x => x[0]), work = xs.map(x => x[1]);
  return { frames: xs.length, interval_mean: mean(iv), interval_p95: q(iv, 0.95), work_mean: mean(work), work_p95: q(work, 0.95) };
}
function bytes() {
  const out = { sprites: 0, b2: 0, files: 0 };
  for (const e of performance.getEntriesByType('resource')) {
    const size = e.encodedBodySize || e.transferSize || 0;
    if (e.name.includes(SPRITES)) { out.sprites += size; out.files++; }
    else if (e.name.includes(B2)) { out.b2 += size; out.files++; }
  }
  return out;
}
function showStats() {
  const f = frameStats(), b = bytes();
  document.getElementById('stats').textContent =
    `res ${pickRes()}  ${current ? current.robot.clip + ' ' + current.robot.dir : ''}\n` +
    (f ? `frame ${f.interval_mean.toFixed(1)} ms (p95 ${f.interval_p95.toFixed(1)})\ndraw ${f.work_mean.toFixed(2)} ms\n` : '') +
    `sprites ${(b.sprites / 1e6).toFixed(2)} MB in ${b.files} files`;
}

// --- controls ------------------------------------------------------------------------------------------------

function select(id, options, value, set) {
  const el = document.getElementById(id);
  el.innerHTML = options.map(([v, label]) => `<option value="${v}">${label}</option>`).join('');
  el.value = value;
  el.onchange = () => set(el.value);
}
function controls() {
  const refreshDirs = () => select('dir', Object.keys(man.clips[state.clip].dirs).map(d => [d, `${d} (${man.directions[d].facing_deg}°)`]),
    man.clips[state.clip].dirs[state.dir] ? state.dir : Object.keys(man.clips[state.clip].dirs)[0], v => { state.dir = v; });
  document.getElementById('mode').value = state.mode;
  document.getElementById('mode').onchange = e => { state.mode = e.target.value; };
  select('host', Object.entries(HOSTS).map(([k, v]) => [v, k]), state.host, v => { state.host = v; });
  select('kit', KITS.map(k => [k, k]), state.kit, v => { state.kit = v; });
  select('face', Object.keys(FACES).map(k => [k, k]), state.face, v => { state.face = v; });
  select('clip', Object.keys(man.clips).map(k => [k, k]), state.clip, v => { state.clip = v; state.mode = 'pose'; document.getElementById('mode').value = 'pose'; refreshDirs(); });
  refreshDirs();
  select('item', [['none', 'none'], ...CARRIED.map(k => [k, k.slice(5)])], state.item, v => { state.item = v; });
  const zoom = document.getElementById('zoom');
  zoom.value = state.zoom;
  zoom.oninput = () => setZoom(Number(zoom.value));
  document.getElementById('anchors').checked = state.anchors;
  document.getElementById('anchors').onchange = e => { state.anchors = e.target.checked; };
}

// --- for tests: one frame composed at a resolution's own pixels, and the draw order for a robot position -------

async function composeCanvas(o) {
  const res = o.res || '1x';
  loaded[res] = await ensure(res);
  const c = man.clips[o.clip], dd = c.dirs[o.dir], f = dd.frames[o.frame || 0], sc = man.resolutions[res].scale;
  const out = new OffscreenCanvas(dd.canvas[0] * sc, dd.canvas[1] * sc);
  const names = o.layers || layersOf(o.clip, f, { face: o.face || 'codex', kit: o.kit || 'antenna', item: o.item || 'none' }).flat();
  drawLayers(out.getContext('2d'), names, f, res, o.host || man.grey, 0, 0, 1);
  return out;
}
// the draw order, far to near, with the robot standing at `foot` or (seated) typing at desk 2
function order(foot, seated) {
  const w = seated ? { clip: 'Typing', dir: 'S', time: 0, foot: seatFoot, lift: seatLift, atDesk: true }
    : { clip: 'Idle', dir: 'S', time: 0, foot, lift: 0 };
  return items(w, 0).list.map(i => i.label);
}

async function main() {
  try {
    [man, b2] = await Promise.all([json(SPRITES + 'sprites.json'), json(B2 + 'sprites.json')]);
    [floorImg, benchImg] = await Promise.all([image(B2 + b2.textures['floor-tile'].file), image(B2 + b2.sprites.bench.file)]);
    const s = b2.sprites['robot-typing'];
    b2Robot = await b2Tinted(await image(B2 + s.file), await image(B2 + s.mask), HOSTS.teal);
    legs = path();
    if (params.has('shot')) document.body.classList.add('shot');
    resize();
    addEventListener('resize', resize);
    const res = pickRes();
    loaded[res] = await ensure(res);
    controls();
    window.robotPreview.ready = true;
    requestAnimationFrame(tick);
  } catch (err) {
    window.robotPreview.error = String(err);
    throw err;
  }
}
async function b2Tinted(img, mask, host) {  // B2's own recipe: the masked shell times the host colour lifted by 1/0.8
  const c = new OffscreenCanvas(img.width, img.height), o = c.getContext('2d');
  o.drawImage(mask, 0, 0);
  const m = o.getImageData(0, 0, c.width, c.height).data;
  o.clearRect(0, 0, c.width, c.height);
  o.drawImage(img, 0, 0);
  const d = o.getImageData(0, 0, c.width, c.height), t = hex(host).map(v => v / 204);
  for (let i = 0; i < d.data.length; i += 4) {
    const k = m[i] / 255;
    for (let j = 0; j < 3; j++) d.data[i + j] = Math.min(255, d.data[i + j] * (1 - k + k * t[j]));
  }
  o.putImageData(d, 0, 0);
  return c;
}

window.robotPreview = {
  ready: false, error: null, state,
  stats: () => ({ frame: frameStats(), bytes: bytes(), res: pickRes(), loaded: !!loaded[pickRes()], robot: current && { clip: current.robot.clip, dir: current.robot.dir, foot: current.robot.foot } }),
  resetStats: () => { frameLog.length = 0; last = null; },
  manifest: () => man, composeCanvas, order, setZoom, walkerAt: t => walker(t), pathSeconds: () => legs.reduce((s, l) => s + l.secs, 0),
};
main();
