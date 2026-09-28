// Robot sprite preview (/prototype/robot): the paper-doll sprite atlases from art/scripts/build_robot_sprites.py
// composited on one 2D canvas, no WebGL: host tint through the mask, the agent face coloured, one host kit, the
// clip's items, and a robot walking round a bench (in front of it, round its end, behind it), sitting down on its
// raised chair, typing and standing up again, beside two seated robots at the other desks. The bench is drawn from
// the manifest's seat_furniture with the manifest's own camera.
//   ?mode=walk|pose  &clip=Idle&dir=S  &host=%23ff9340&kit=antenna&face=codex&look=stalled  &zoom=0..1 | &ppm=171.5
//   &scene=bench (desk 2 types; the walker only walks, so it passes behind the bench)  &t=seconds (frozen)  &shot

const params = new URLSearchParams(location.search);
const SPRITES = '/assets/world/robot/sprites/';
const FROZEN = params.has('t') ? Number(params.get('t')) : null;
const HOSTS = { orange: '#ff9340', teal: '#2dd4bf', violet: '#a78bfa', yellow: '#facc15', blue: '#2e62dc' };
const KITS = ['backpack', 'antenna', 'halo', 'crest'];
const FACES = { codex: 'face_eyes', claude: 'face_band' };
const AGENT = { codex: '#7ce7ff', claude: '#ff8f6b' };  // looks.js AGENT_COLOR
const LOOKS = ['normal', 'stalled', 'resting'];  // motion.js tone
const SPEED = 0.9;  // m/s
const POSE_AT = [6.4, 2.9];

let PITCH = 28 * Math.PI / 180, YAW = 33 * Math.PI / 180;  // replaced by the manifest's camera
let RIGHT, UP, BACK;
function camera(c) {
  PITCH = c.pitch_deg * Math.PI / 180;
  YAW = c.yaw_deg * Math.PI / 180;
  RIGHT = [Math.cos(YAW), Math.sin(YAW), 0];
  UP = [-Math.sin(PITCH) * Math.sin(YAW), Math.sin(PITCH) * Math.cos(YAW), Math.cos(PITCH)];
  BACK = [Math.sin(YAW) * Math.cos(PITCH), -Math.cos(YAW) * Math.cos(PITCH), Math.sin(PITCH)];  // towards the camera
}
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const depth = p => dot(p, BACK);  // larger is nearer the camera

const canvas = document.getElementById('view');
const g = canvas.getContext('2d');
const view = { W: 0, H: 0, target: [6.2, 4.3, 0.5], height: 5, ppm: 1 };
const state = {
  mode: params.get('mode') || 'walk', host: params.get('host') || HOSTS.teal, kit: params.get('kit') || 'antenna',
  face: params.get('face') || 'codex', clip: params.get('clip') || 'Walking', dir: params.get('dir') || 'S',
  look: params.get('look') || 'normal', zoom: params.has('zoom') ? Number(params.get('zoom')) : 0.2, anchors: params.has('anchors'),
};
function resize() {
  view.W = canvas.width = innerWidth;
  view.H = canvas.height = innerHeight;
  setZoom(state.zoom);
}
const BENCH_SCENE = params.get('scene') === 'bench';
function setZoom(z) {
  state.zoom = z;
  view.height = Math.exp(Math.log(9) + (Math.log(1.6) - Math.log(9)) * z);  // metres on screen, far to close
  view.ppm = view.H / view.height;
  if (params.has('ppm')) view.ppm = Number(params.get('ppm'));  // a fixed density: the floor's 1x is 171.5 px/m
}
function screen(p) {
  const d = [p[0] - view.target[0], p[1] - view.target[1], p[2] - view.target[2]];
  return [view.W / 2 + dot(d, RIGHT) * view.ppm, view.H / 2 - dot(d, UP) * view.ppm];
}

// --- loading ---------------------------------------------------------------------------------------------

const image = src => new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => fail(new Error('missing ' + src)); i.src = src; });
const json = url => fetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error('missing ' + url))));
let man = null;
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
const loaded = {};
function pickRes() {  // the smallest set at least as dense as the screen; 4x only when zoomed in
  const need = view.ppm * devicePixelRatio;
  const ppm1 = man.camera.px_per_m_1x;
  const keys = Object.keys(man.resolutions).sort((a, b) => man.resolutions[a].scale - man.resolutions[b].scale);
  return keys.find(k => ppm1 * man.resolutions[k].scale >= need) || keys[keys.length - 1];
}

// --- colour: the shell through its mask, the halo and the faces whole ------------------------------------

const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const mixHex = (a, b, k) => '#' + hex(a).map((v, i) => Math.round(v + (hex(b)[i] - v) * k).toString(16).padStart(2, '0')).join('');
// motion.js tone: stalled dims the body and face, resting only the face
function colours(r) {
  const agent = AGENT[r.face];
  if (r.look === 'stalled') return { body: mixHex(r.host, '#475163', 0.55), face: mixHex(agent, '#1b2333', 0.6) };
  if (r.look === 'resting') return { body: r.host, face: mixHex(agent, '#1b2333', 0.35) };
  return { body: r.host, face: agent };
}
const ri = res => Object.keys(man.resolutions).indexOf(res);
// One layer image coloured for a host (or a face colour), made the first time it is drawn and kept.
const tintCache = new Map();
function tintedLayer(res, name, e, colour, whole, base) {
  const [p, x, y, w, h] = e;
  const key = `${res}|${p}|${x}|${y}|${colour}`;
  if (tintCache.has(key)) return tintCache.get(key);
  const { color, mask } = loaded[res];
  const c = new OffscreenCanvas(w, h), o = c.getContext('2d', { willReadFrequently: true });
  let m = null;
  if (!whole) {  // the mask is stored mask_scale times smaller: stretched over the layer
    const ms = man.resolutions[res].mask_scale;
    o.drawImage(mask[p], x / ms, y / ms, w / ms, h / ms, 0, 0, w, h);
    m = o.getImageData(0, 0, w, h).data;
    o.clearRect(0, 0, w, h);
  }
  o.drawImage(color[p], x, y, w, h, 0, 0, w, h);
  const d = o.getImageData(0, 0, w, h);
  const b = hex(base), t = hex(colour).map((v, i) => v / b[i]);
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
// the layers of one robot, split at the desk top when seated: [below, above]. A seated robot's shadow is drawn with
// its chair instead (seatShadow): its feet hang clear of the floor, and the chair's seat takes its shadow
function layersOf(clip, frame, opts) {
  const f = frame.layers;
  const face = FACES[opts.face], kit = 'acc_' + opts.kit;
  const below = man.clips[clip].seated ? ['body_low'] : ['shadow', 'body_low'], above = ['body', 'body_high', face, kit, ...man.clips[clip].items];
  return [below.filter(n => f[n]), above.filter(n => f[n])];
}
function drawLayers(ctx, names, frame, res, r, ox0, oy0, k) {
  const lp = loaded[res], col = colours(r);
  for (const n of names) {
    if (!frame.layers[n]) continue;  // empty in this frame
    const e = frame.layers[n][ri(res)], [p, x, y, w, h, ox, oy] = e;
    if (man.masked.includes(n) || man.tinted_whole.includes(n)) {
      ctx.drawImage(tintedLayer(res, n, e, col.body, man.tinted_whole.includes(n), man.grey), ox0 + ox * k, oy0 + oy * k, w * k, h * k);
      continue;
    }
    if (n in man.faces) {  // white emissive: multiplied by the agent's colour
      ctx.drawImage(tintedLayer(res, n, e, col.face, true, '#ffffff'), ox0 + ox * k, oy0 + oy * k, w * k, h * k);
      continue;
    }
    const src = n === 'shadow' ? lp.shadow[p] : lp.color[p];
    const s = n === 'shadow' ? man.shadow_scale : 1;
    ctx.drawImage(src, x, y, w, h, ox0 + ox * k, oy0 + oy * k, w * s * k, h * s * k);
  }
}
// a robot's sprite placed with its `foot` on a floor point (seated frames: the floor under the seat point)
function robotDraw(r, part) {
  return () => {
    const res = pickRes();
    if (!loaded[res]) return;
    const c = man.clips[r.clip], dd = c.dirs[r.dir], f = dd.frames[r.frame];
    const sc = man.resolutions[res].scale, k = view.ppm / (man.camera.px_per_m_1x * sc);
    const [sx, sy] = screen([r.foot[0], r.foot[1], 0]);
    const [below, above] = layersOf(r.clip, f, r);
    drawLayers(g, part === 'below' ? below : part === 'above' ? above : [...below, ...above], f, res, r,
      sx - dd.foot[0] * sc * k, sy - dd.foot[1] * sc * k, k);
    if (state.anchors && part !== 'below') drawAnchors(r, dd, f, sx, sy, view.ppm / man.camera.px_per_m_1x);
  };
}
// a seated robot's contact shadow on the floor under its chair (drawn before the chair)
const shadowPoint = foot => [foot[0], foot[1] + SF.chair_behind_m, 0];
function seatShadow(r) {
  const res = pickRes();
  if (!loaded[res] || !man.clips[r.clip].seated) return;
  const dd = man.clips[r.clip].dirs[r.dir], f = dd.frames[r.frame];
  const sc = man.resolutions[res].scale, k = view.ppm / (man.camera.px_per_m_1x * sc);
  const [sx, sy] = screen(shadowPoint(r.foot));
  drawLayers(g, ['shadow'], f, res, r, sx - dd.foot[0] * sc * k, sy - dd.foot[1] * sc * k, k);
}
function drawAnchors(r, dd, f, sx, sy, k) {
  const at = p => [sx + (p[0] - dd.foot[0]) * k, sy + (p[1] - dd.foot[1]) * k];
  g.save();
  g.lineWidth = 1.5;
  g.strokeStyle = '#e02424';
  g.beginPath(); g.ellipse(sx, sy, dd.footprint[0] * k, dd.footprint[1] * k, 0, 0, 2 * Math.PI); g.stroke();
  const [hx, hy] = at(f.anchors.head_top);
  g.strokeStyle = '#1d6fe0'; g.beginPath(); g.moveTo(hx - 8, hy); g.lineTo(hx + 8, hy); g.stroke();
  const kt = f.anchors.kit_top[r.kit];
  if (kt) { const [x, y] = at(kt); g.strokeStyle = '#9333ea'; g.beginPath(); g.moveTo(x - 6, y); g.lineTo(x + 6, y); g.stroke(); }
  g.strokeStyle = '#f08c00';
  for (const hand of ['hand_l', 'hand_r']) { const [x, y] = at(f.anchors[hand]); g.beginPath(); g.arc(x, y, 4, 0, 2 * Math.PI); g.stroke(); }
  const [bx, by] = at(f.hit);
  g.strokeStyle = '#15a34a'; g.strokeRect(bx, by, f.hit[2] * k, f.hit[3] * k);
  g.restore();
}

// --- the bench: three desks side by side, a raised chair behind each (manifest seat_furniture) -------------

const BENCH = { x0: 3.8, deskW: 1.8, y: 4.35, deskD: 0.8, deskH: 0.74, top: 0.03 };
let SF = null;  // manifest seat_furniture
// the floor point under desk d's seat (the robots sit on the far side, facing the camera: S)
const seat = d => [BENCH.x0 + (d - 1) * BENCH.deskW + BENCH.deskW / 2, BENCH.y + BENCH.deskD / 2 + SF.desk_edge_ahead_m];
function poly(pts, fill, stroke) {
  g.beginPath();
  pts.forEach((p, i) => { const [x, y] = screen(p); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.closePath();
  g.fillStyle = fill; g.fill();
  if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1; g.stroke(); }
}
function boxAt([x0, y0, z0], [x1, y1, z1], top, side, front) {  // the three faces seen from this camera
  poly([[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], top);
  poly([[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]], front);
  poly([[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]], side);
}
function drawDesk() {
  const { x0, deskW, y, deskD, deskH, top } = BENCH, x1 = x0 + 3 * deskW, ya = y - deskD / 2, yb = y + deskD / 2;
  for (const lx of [x0 + 0.05, x0 + deskW, x0 + 2 * deskW, x1 - 0.05]) {
    for (const ly of [ya + 0.05, yb - 0.05]) boxAt([lx - 0.02, ly - 0.02, 0], [lx + 0.02, ly + 0.02, deskH - top], '#5b6270', '#4b515c', '#646b78');
  }
  boxAt([x0, ya, deskH - top], [x1, yb, deskH], '#dcc09a', '#b99a73', '#c6a67d');
}
function drawChair(d) {
  const [sx, sy] = seat(d), h = SF.seat_height_m, cy = sy + SF.chair_behind_m;
  for (let i = 0; i < 5; i++) {   // the five-spoke base on the floor, which the seated robot's shadow lies under
    const a = i * 2 * Math.PI / 5 + 0.3, ex = sx + Math.cos(a) * 0.28, ey = cy + Math.sin(a) * 0.28, nx = -Math.sin(a) * 0.02, ny = Math.cos(a) * 0.02;
    poly([[sx + nx, cy + ny, 0.05], [ex + nx, ey + ny, 0.03], [ex - nx, ey - ny, 0.03], [sx - nx, cy - ny, 0.05]], '#3a3d44');
  }
  boxAt([sx - 0.012, cy - 0.012, 0.05], [sx + 0.012, cy + 0.012, h - 0.06], '#8a8f99', '#787d86', '#8a8f99');  // the gas lift
  boxAt([sx - 0.25, cy - 0.24, h - 0.07], [sx + 0.25, cy + 0.24, h], '#2c2f35', '#22252a', '#34373d');  // seat
  boxAt([sx - 0.23, cy + 0.2, h + 0.08], [sx + 0.23, cy + 0.26, h + 0.58], '#2c2f35', '#22252a', '#34373d');  // back
}
function drawFloor() {
  g.fillStyle = '#e4e8ef';
  g.fillRect(0, 0, view.W, view.H);
  g.strokeStyle = 'rgba(120, 130, 150, 0.25)';
  g.lineWidth = 1;
  for (let i = -4; i <= 30; i++) {
    const a = screen([i * 0.6, -4, 0]), b = screen([i * 0.6, 12, 0]), c = screen([-4, i * 0.6, 0]), e = screen([20, i * 0.6, 0]);
    g.beginPath(); g.moveTo(...a); g.lineTo(...b); g.moveTo(...c); g.lineTo(...e); g.stroke();
  }
}

// --- the walk: along the front of the bench, round its end, behind it, then sit, type and stand at desk 2 ---

let legs = null;
function path() {
  const s2 = seat(2);
  const back = s2[1] + 0.6, front = 3.0, left = 2.2, right = 10.1;
  const pts = [[s2[0], back], [left, back], [left, front], [right, front], [right, back], [s2[0], back], s2];
  const out = [{ clip: 'Idle', secs: 0.6 }];
  for (let i = 1; i < pts.length; i++) out.push({ walk: [pts[i - 1], pts[i]] });
  const once = c => man.clips[c].frames / man.clips[c].fps;
  if (BENCH_SCENE) out.pop();  // it walks the loop only: desk 2 has its own typist
  else out.push({ clip: 'Sitting', secs: once('Sitting') + 0.2 }, { clip: 'Typing', secs: 5 }, { clip: 'StandUp', secs: once('StandUp') });
  return out.map(s => (s.walk ? { ...s, secs: Math.hypot(s.walk[1][0] - s.walk[0][0], s.walk[1][1] - s.walk[0][1]) / SPEED } : s));
}
function walker(t) {
  const total = legs.reduce((s, l) => s + l.secs, 0);
  let u = ((t % total) + total) % total;
  for (const l of legs) {
    if (u > l.secs) { u -= l.secs; continue; }
    if (l.walk) {
      const [a, b] = l.walk, k = u / l.secs, dx = b[0] - a[0], dy = b[1] - a[1];
      const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'E' : 'W') : (dy > 0 ? 'N' : 'S');
      return { clip: 'Walking', dir, time: t, foot: [a[0] + dx * k, a[1] + dy * k] };
    }
    return { clip: l.clip, dir: 'S', time: u, foot: seat(2), atDesk: true };
  }
}

// --- the scene, ordered far to near -------------------------------------------------------------------------

function benchNear(p) {  // the nearest point of the bench's footprint: a long bench can't be ordered by its centre
  const x = Math.max(BENCH.x0, Math.min(BENCH.x0 + 3 * BENCH.deskW, p[0]));
  const y = Math.max(BENCH.y - BENCH.deskD / 2, Math.min(BENCH.y + BENCH.deskD / 2, p[1]));
  return [x, y, 0];
}
function items(w, t) {
  const r = { ...w, host: state.host, kit: state.kit, face: state.face, look: state.look };
  r.frame = frameAt(r.clip, w.time);
  const benchKey = depth(benchNear([r.foot[0], r.foot[1], 0]));
  const sitter = (d, clip, host, kit, face) => ({ clip, dir: 'S', frame: frameAt(clip, t), foot: seat(d), host, kit, face, look: 'normal' });
  const one = sitter(1, 'Writing', HOSTS.orange, 'backpack', 'claude'), three = sitter(3, 'SitRead', HOSTS.violet, 'crest', 'claude');
  const two = sitter(2, 'Typing', HOSTS.teal, 'antenna', 'codex');
  const seated = [one, three, ...(BENCH_SCENE ? [two] : []), ...(w.atDesk && man.clips[r.clip].seated ? [r] : [])];
  const out = [
    { label: 'chairs', key: benchKey - 0.01, draw: () => { seated.forEach(seatShadow); [1, 2, 3].forEach(drawChair); } },
    ...(BENCH_SCENE ? [{ label: 'desk2 below', key: benchKey - 0.002, draw: robotDraw(two, 'below') },
      { label: 'desk2 above', key: benchKey + 0.002, draw: robotDraw(two, 'above') }] : []),
    { label: 'bench', key: benchKey, draw: drawDesk },
    { label: 'desk1 below', key: benchKey - 0.002, draw: robotDraw(one, 'below') },
    { label: 'desk1 above', key: benchKey + 0.002, draw: robotDraw(one, 'above') },
    { label: 'desk3 below', key: benchKey - 0.002, draw: robotDraw(three, 'below') },
    { label: 'desk3 above', key: benchKey + 0.002, draw: robotDraw(three, 'above') },
  ];
  if (w.atDesk && man.clips[r.clip].seated) {  // seated: legs under the desk, the rest over it
    out.push({ label: 'robot below', key: benchKey - 0.001, draw: robotDraw(r, 'below') },
      { label: 'robot above', key: benchKey + 0.001, draw: robotDraw(r, 'above') });
  } else {
    out.push({ label: 'robot', key: depth([r.foot[0], r.foot[1], 0]), draw: robotDraw(r, 'all') });
  }
  out.sort((a, b) => a.key - b.key);
  return { list: out, robot: r };
}
function pose(t) {
  const c = man.clips[state.clip];
  const dir = c.dirs[state.dir] ? state.dir : Object.keys(c.dirs)[0];
  if (!c.seated) return { clip: state.clip, dir, time: t, foot: POSE_AT };
  return { clip: state.clip, dir, time: t, foot: seat(2), atDesk: true };
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
  const out = { sprites: 0, files: 0 };
  for (const e of performance.getEntriesByType('resource')) {
    if (!e.name.includes(SPRITES)) continue;
    out.sprites += e.encodedBodySize || e.transferSize || 0;
    out.files++;
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
  select('look', LOOKS.map(k => [k, k]), state.look, v => { state.look = v; });
  select('clip', Object.keys(man.clips).map(k => [k, `${k}${man.clips[k].seated ? ' (seated)' : ''}`]), state.clip,
    v => { state.clip = v; state.mode = 'pose'; document.getElementById('mode').value = 'pose'; refreshDirs(); });
  refreshDirs();
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
  const r = { face: o.face || 'codex', kit: o.kit || 'antenna', host: o.host || man.grey, look: o.look || 'normal' };
  const names = o.layers || layersOf(o.clip, f, r).flat();
  drawLayers(out.getContext('2d'), names, f, res, r, 0, 0, 1);
  return out;
}
function order(foot, seated) {
  const w = seated ? { clip: 'Typing', dir: 'S', time: 0, foot: seat(2), atDesk: true } : { clip: 'Idle', dir: 'S', time: 0, foot };
  return items(w, 0).list.map(i => i.label);
}

async function main() {
  try {
    man = await json(SPRITES + 'sprites.json');
    camera(man.camera);
    SF = man.seat_furniture;
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

window.robotPreview = {
  ready: false, error: null, state,
  stats: () => ({ frame: frameStats(), bytes: bytes(), res: pickRes(), loaded: !!loaded[pickRes()], robot: current && { clip: current.robot.clip, dir: current.robot.dir, foot: current.robot.foot } }),
  resetStats: () => { frameLog.length = 0; last = null; },
  manifest: () => man, composeCanvas, order, setZoom, walkerAt: t => walker(t), pathSeconds: () => legs.reduce((s, l) => s + l.secs, 0),
  seat, shadowPoint, layersOf: (clip, dir, i) => layersOf(clip, man.clips[clip].dirs[dir].frames[i || 0], { face: 'codex', kit: 'antenna' }),
};
main();
