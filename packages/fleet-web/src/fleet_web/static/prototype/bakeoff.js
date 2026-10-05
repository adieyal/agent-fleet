// The art bake-off: the same small l2-like scene (floor, back wall, the bench, two plants, three seated
// robots in host colours and the attention lantern) drawn by each asset variant, unlit.
//   ?variant=A      pre-lit glTFs (art/bakeoff/A) drawn with MeshBasicMaterial in three.js
//   ?variant=B1     Blender sprites (art/bakeoff/B1) on a 2D canvas
//   ?variant=B2     AI sprites (art/bakeoff/B2) on a 2D canvas
//   ?variant=B2mix  B2 with the bench, plant and robots taken from other generations (consistency test)
//   ?view=arch      architecture only: a long wall with pilasters over a tiled floor, from the variant's tiles
//   ?zoom=0..1      floor framing (0) to l2 framing (1); ?layers shows the occlusion split; ?t= freezes time;
//   ?aa=0           A without antialiasing
// The layout is the one art/bakeoff/layout.js uses for A and B1 (the workbench's Blender coordinates,
// metres, Z up) and the camera is the prototype's l2 camera, so every variant is placed identically.

const params = new URLSearchParams(location.search);
const VARIANT = params.get('variant') || 'B2';
const VIEW_MODE = params.get('view') || 'scene';
const FROZEN = params.has('t') ? Number(params.get('t')) : null;
const ROOT = '/art/bakeoff/';
const VARIANTS = ['A', 'B1', 'B2', 'B2mix'];

const L2 = { target: [6.269, 4.229, 1.698], height: 5.486 };  // the prototype's l2 framing
const FLOOR = { target: [5.4, 2.6, 0.9], height: 11.5 };       // the whole room
const PITCH = 44.5 * Math.PI / 180, YAW = 21.25 * Math.PI / 180;
const HOSTS = { teal: '#27b3b8', blue: '#2e62dc', olive: '#7a8a32' };
const BENCH = { x0: 3.8, deskW: 1.8, y: 4.35, deskD: 0.8, deskH: 0.74, seatH: 0.47 };
const CAST = [
  { pose: 'type', b2: 'robot-typing', clip: 'Type', desk: 1, host: 'teal' },
  { pose: 'write', b2: 'robot-pencil', clip: 'Write', desk: 2, host: 'blue', prop: 'prop_pencil' },
  { pose: 'hold', b2: 'robot-tube', clip: 'Hold', desk: 3, host: 'olive', prop: 'prop_flask' },
];
const PLANTS = [[2.3, 5.4], [9.95, 3.9]];
const LANTERN = [3.1, 5.25, 2.25];
const WALL = { y: 6.0, x0: -1, x1: 12.5, h: 3.2 };
const ROOM = { x0: -1, x1: 12.5, y0: -3.5, y1: 6.0 };
const seat = d => [BENCH.x0 + (d - 1) * BENCH.deskW + BENCH.deskW / 2 - 0.25, BENCH.y + BENCH.deskD / 2 + 0.34 - 0.2, BENCH.seatH];
const lamp = d => [BENCH.x0 + (d - 1) * BENCH.deskW + 0.18, BENCH.y + 0.02, BENCH.deskH + 0.215];

// --- camera: orthographic at the l2 angle ------------------------------------------------------------

const RIGHT = [Math.cos(YAW), Math.sin(YAW), 0];
const UP = [-Math.sin(PITCH) * Math.sin(YAW), Math.sin(PITCH) * Math.cos(YAW), Math.cos(PITCH)];
const BACK = [Math.sin(YAW) * Math.cos(PITCH), -Math.cos(YAW) * Math.cos(PITCH), Math.sin(PITCH)];  // towards the camera
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const lerp = (a, b, k) => a + (b - a) * k;

let zoom = params.has('zoom') ? Number(params.get('zoom')) : 1;
const view = { W: innerWidth, H: innerHeight, target: L2.target, height: L2.height, ppm: 1 };
function setZoom(z) {
  zoom = z;
  view.target = FLOOR.target.map((v, i) => lerp(v, L2.target[i], z));
  view.height = Math.exp(lerp(Math.log(FLOOR.height), Math.log(L2.height), z));
  view.ppm = view.H / view.height;
  onView.forEach(f => f());
}
const onView = [];
function screen(p) {
  const d = [p[0] - view.target[0], p[1] - view.target[1], p[2] - view.target[2]];
  return [view.W / 2 + dot(d, RIGHT) * view.ppm, view.H / 2 - dot(d, UP) * view.ppm];
}
const depth = p => dot(p, BACK);  // larger is nearer the camera

// --- layers: the room (2D), the variant (2D or WebGL), then the stats --------------------------------

function layer(kind = '2d', opts = {}) {
  const c = document.createElement('canvas');
  c.width = view.W;
  c.height = view.H;
  document.body.appendChild(c);
  return kind === '2d' ? c.getContext('2d', opts) : c;
}
const room = layer();
function quad(g, pts, fill) {
  g.beginPath();
  pts.map(screen).forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
  g.closePath();
  g.fillStyle = fill;
  g.fill();
}
const floorPts = [[ROOM.x0, ROOM.y0, 0], [ROOM.x1, ROOM.y0, 0], [ROOM.x1, ROOM.y1, 0], [ROOM.x0, ROOM.y1, 0]];
const wallPts = [[WALL.x0, WALL.y, 0], [WALL.x1, WALL.y, 0], [WALL.x1, WALL.y, WALL.h], [WALL.x0, WALL.y, WALL.h]];
// The room is static: painted once per view into a cache, then blitted each frame.
let roomPainter = g => {  // flat, the same for every variant: the objects carry the look
  g.fillStyle = '#dcebf8';
  g.fillRect(0, 0, view.W, view.H);
  quad(g, floorPts, '#d3d1db');
  quad(g, wallPts, '#b7b1b4');
};
let roomCache = null;
onView.push(() => { roomCache = null; });
function drawRoom() {
  if (!roomCache) {
    roomCache = new OffscreenCanvas(view.W, view.H);
    roomPainter(roomCache.getContext('2d'));
  }
  room.drawImage(roomCache, 0, 0);
}

// --- loading and measuring ---------------------------------------------------------------------------

function image(src) {
  return new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => fail(new Error('missing ' + src)); i.src = src; });
}
const json = url => fetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error('missing ' + url))));
const missing = [];
const frameLog = [];  // [interval ms, work ms]
function bytes() {
  const out = { assets: 0, code: 0, files: 0 };
  for (const e of performance.getEntriesByType('resource')) {
    const size = e.encodedBodySize || e.transferSize || 0;
    if (e.name.includes('/art/bakeoff/')) { out.assets += size; out.files++; }
    else if (e.name.includes('/vendor/') || e.name.includes('/prototype/')) out.code += size;
  }
  return out;
}
function frameStats() {
  const xs = frameLog.slice(1);
  if (!xs.length) return null;
  const q = (arr, p) => arr.slice().sort((a, b) => a - b)[Math.min(arr.length - 1, Math.floor(p * arr.length))];
  const iv = xs.map(x => x[0]), work = xs.map(x => x[1]);
  const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
  return { frames: xs.length, interval_mean: mean(iv), interval_p95: q(iv, 0.95), work_mean: mean(work), work_p95: q(work, 0.95) };
}

// --- tinting: host colour multiplied over the masked shell, done once at load -------------------------

async function alphaMask(src) {  // a greyscale mask image as an alpha-only canvas
  const im = await image(src);
  const c = new OffscreenCanvas(im.width, im.height);
  const g = c.getContext('2d');
  g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height);
  for (let i = 0; i < d.data.length; i += 4) { d.data[i + 3] = d.data[i]; d.data[i] = d.data[i + 1] = d.data[i + 2] = 0; }
  g.putImageData(d, 0, 0);
  return c;
}
function tinted(img, mask, hex) {
  const lift = [1, 3, 5].map(i => Math.min(255, parseInt(hex.slice(i, i + 2), 16) / 0.8));
  const shell = new OffscreenCanvas(img.width, img.height);
  const s = shell.getContext('2d');
  s.drawImage(img, 0, 0);
  s.globalCompositeOperation = 'multiply';
  s.fillStyle = `rgb(${lift.join(',')})`;
  s.fillRect(0, 0, img.width, img.height);
  s.globalCompositeOperation = 'destination-in';
  s.drawImage(mask, 0, 0);
  const out = new OffscreenCanvas(img.width, img.height);
  const o = out.getContext('2d');
  o.drawImage(img, 0, 0);
  o.drawImage(shell, 0, 0);
  return out;
}

// --- sprite variants (B1, B2): one 2D canvas, far to near ----------------------------------------------

const showLayers = () => document.getElementById('layers').checked;

function spriteScene(items, g) {
  items.forEach(i => { i.key = depth(i.depthAt) + (i.bias || 0); });
  items.sort((a, b) => a.key - b.key);
  return t => { for (const i of items) i.draw(g, t); };  // over the room, drawn first
}
// a sprite whose ref_px sits on a world point; `frame` picks a cell from a one-row sheet
function place(g, img, s, at, { frame = 0, frames = 1, clip = null, tag = null } = {}) {
  const k = view.ppm / s.px_per_m;
  const fw = img.width / frames, fh = img.height;
  const [x, y] = screen(at);
  const dx = x - s.ref_px[0] * k, dy = y - s.ref_px[1] * k;
  g.save();
  if (clip) {  // the desk-top cut: a line through (cut.x, cut.y) in sprite px; keep one side of it
    const c = s.cut, lx0 = dx, lx1 = dx + fw * k;
    const ly = sx => dy + k * (c.y + c.slope * ((sx - dx) / k - c.x));
    g.beginPath();
    const edge = clip === 'above' ? dy - 10 : dy + fh * k + 10;
    g.moveTo(lx0, ly(lx0)); g.lineTo(lx1, ly(lx1)); g.lineTo(lx1, edge); g.lineTo(lx0, edge); g.closePath();
    g.clip();
  }
  if (tag && showLayers()) {  // debug: the sprite's own pixels washed with the layer's colour
    const c = new OffscreenCanvas(fw, fh), o = c.getContext('2d');
    o.drawImage(img, frame * fw, 0, fw, fh, 0, 0, fw, fh);
    o.globalCompositeOperation = 'source-atop';
    o.fillStyle = tag;
    o.fillRect(0, 0, fw, fh);
    g.drawImage(c, dx, dy, fw * k, fh * k);
  } else {
    g.drawImage(img, frame * fw, 0, fw, fh, dx, dy, fw * k, fh * k);
  }
  g.restore();
}
function glows(g, on) {  // warm lamp pools: additive, toggled by activity, never baked into the room
  if (!on) return;
  g.save();
  g.globalCompositeOperation = 'lighter';
  for (const d of [1, 2, 3]) {
    const [x, y] = screen(lamp(d)), r = 0.3 * view.ppm;
    const grad = g.createRadialGradient(x, y, 0, x, y, r);
    grad.addColorStop(0, 'rgba(255, 170, 80, 0.6)');
    grad.addColorStop(1, 'rgba(255, 170, 80, 0)');
    g.fillStyle = grad;
    g.fillRect(x - r, y - r, 2 * r, 2 * r);
  }
  g.restore();
}

async function variantB2(mix) {
  const dir = ROOT + 'B2/';
  const m = await json(dir + 'sprites.json');
  const S = name => ({ ...m.sprites[(mix && m.sprites['mixed/' + name]) ? 'mixed/' + name : name], px_per_m: m.px_per_m });
  const g = room;  // sprites share the room's canvas: one 2D context, no WebGL at all
  const bench = S('bench'), plant = S('plant'), lantern = S('lantern');
  const [benchImg, plantImg, lanternImg] = await Promise.all([bench, plant, lantern].map(s => image(dir + s.file)));
  const robots = await Promise.all(CAST.map(async c => {
    const s = S(c.b2);
    const anim = !mix && s.anim;
    const img = await image(dir + (anim ? anim.file : s.file));
    const mask = await alphaMask(dir + (anim ? anim.mask : s.mask));
    return { ...c, s, frames: anim ? anim.frames : 1, fps: anim ? anim.fps : 0, img: tinted(img, mask, HOSTS[c.host]) };
  }));
  // the bench sprite is pinned by the middle of its desk top's far edge, where the robots sit
  const benchAt = [BENCH.x0 + 1.5 * BENCH.deskW, BENCH.y + BENCH.deskD / 2, BENCH.deskH];
  const benchKey = [benchAt[0], BENCH.y, 0.5];
  const items = [
    { depthAt: benchKey, draw: g2 => place(g2, benchImg, bench, benchAt) },
    ...PLANTS.map(([x, y]) => ({ depthAt: [x, y, 0.5], draw: g2 => place(g2, plantImg, plant, [x, y, 0]) })),
    ...robots.flatMap(r => {
      const at = seat(r.desk);
      const frame = t => (r.frames > 1 ? Math.floor(t * r.fps) % r.frames : 0);
      // a long bench defeats sorting by footprint centre, so the halves are ordered against it directly:
      // below the desk top (legs, seat) just before the bench; above it (arms, laptop, paper) just after
      return [
        { depthAt: benchKey, bias: -0.001,
          draw: (g2, t) => place(g2, r.img, r.s, at, { frame: frame(t), frames: r.frames, clip: 'below', tag: 'rgba(220,40,40,.45)' }) },
        { depthAt: benchKey, bias: 0.001,
          draw: (g2, t) => place(g2, r.img, r.s, at, { frame: frame(t), frames: r.frames, clip: 'above', tag: 'rgba(40,90,220,.45)' }) },
      ];
    }),
    { depthAt: LANTERN, bias: 100, draw: g2 => place(g2, lanternImg, lantern, LANTERN) },
  ];
  missing.push('B2 lamps: glow is baked into the bench sprite where the model put the lamps, so it cannot follow activity');
  const paint = spriteScene(items, g);
  return t => { drawRoom(); paint.call(null, t); };
}

async function variantB1() {
  const dir = ROOT + 'B1/';
  const m = await json(dir + 'manifest.json');
  const o = m.objects;
  const need = view.ppm * devicePixelRatio;  // pick the smallest render that is at least screen density
  const pick = entry => {
    const keys = Object.keys(entry).filter(k => /^\dx$/.test(k)).sort();
    return entry[keys.find(k => entry[k].px_per_m >= need) || keys[keys.length - 1]];
  };
  const load = async entry => { const e = pick(entry); return { ...e, img: await image(dir + e.file) }; };
  const bench = await load(o.bench), plant = await load(o.plant);
  const robots = await Promise.all(CAST.map(async c => {
    const e = await load(o.robot[c.pose]);
    const maskFile = e.mask;
    const img = maskFile ? tinted(e.img, await alphaMask(dir + maskFile), HOSTS[c.host]) : e.img;
    return { ...c, e, img };
  }));
  if (!robots.some(r => r.e.mask)) missing.push('B1 robots: no tint mask in the manifest, drawn as rendered');
  if (!o.lantern) missing.push('B1: no lantern sprite');
  const lantern = o.lantern ? await load(o.lantern) : null;
  const items = [
    { depthAt: [6.5, 4.35, 0.5], draw: g => place(g, bench.img, bench, bench.ref_world) },
    ...PLANTS.map(([x, y]) => ({ depthAt: [x, y, 0.5], draw: g => place(g, plant.img, plant, [x, y, 0]) })),
    // B1 robots were rendered with the bench as a holdout, so the desk has already cut them: over the bench
    ...robots.map(r => ({ depthAt: [6.5, 4.35, 0.5], bias: 0.001, draw: (g, t) => place(g, r.img, r.e, r.e.ref_world,
      { frame: Math.floor(t * r.e.fps) % r.e.frames, frames: r.e.frames, tag: 'rgba(40,90,220,.45)' }) })),
    ...(lantern ? [{ depthAt: LANTERN, bias: 100, draw: g => place(g, lantern.img, lantern, LANTERN) }] : []),
  ];
  const paint = spriteScene(items, room);
  return t => { drawRoom(); paint(t); glows(room, true); };
}

// --- A: pre-lit glTFs, unlit, in a transparent three.js layer over the room -------------------------------

async function variantA() {
  const THREE = await import('three');
  const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
  const SkeletonUtils = await import('three/addons/utils/SkeletonUtils.js');
  const bl = (x, y, z) => new THREE.Vector3(x, z, -y);
  const antialias = params.get('aa') !== '0';  // ?aa=0: without MSAA, the cheapest A can be
  const renderer = new THREE.WebGLRenderer({ canvas: layer('webgl'), alpha: true, antialias });
  renderer.setPixelRatio(1);
  renderer.setSize(view.W, view.H, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 200);
  const fit = () => {
    const t = bl(...view.target);
    camera.position.copy(t).add(bl(...BACK).multiplyScalar(60));
    camera.lookAt(t);
    const h = view.height / 2, w = h * view.W / view.H;
    Object.assign(camera, { left: -w, right: w, top: h, bottom: -h });
    camera.updateProjectionMatrix();
  };
  onView.push(fit);
  fit();
  const dir = ROOT + 'A/';
  const manifest = await json(dir + 'manifest.json');
  const load = f => new GLTFLoader().loadAsync(dir + f);
  const [bench, plant, robot, robotShadow] = await Promise.all(['bench.glb', 'plant.glb', 'robot.glb',
    manifest.objects.robot.decal.file].map(load));
  const unlit = (root, tint) => root.traverse(o => {  // as art/bakeoff/layout.js
    if (!o.isMesh) return;
    const src = o.material;
    if (o.name.endsWith('contact_shadow')) {
      o.material = new THREE.MeshBasicMaterial({ map: src.map, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 });
      o.renderOrder = 1;
      return;
    }
    const mat = new THREE.MeshBasicMaterial({ map: src.map, color: src.color.clone() });
    if (src.name === 'robot_body' && tint) mat.color.set(tint);
    if (src.name === 'robot_eye') mat.color.copy(src.emissive).multiplyScalar(1.2);
    if (src.name === 'robot_glass') Object.assign(mat, { transparent: true, opacity: 0.6 });
    o.material = mat;
  });
  unlit(bench.scene);
  scene.add(bench.scene);
  for (const [x, y] of PLANTS) {
    const p = plant.scene.clone();
    unlit(p);
    p.position.copy(bl(x, y, 0));
    scene.add(p);
  }
  const mixers = [];
  const armTrack = /Shoulder|UpperArm|LowerArm|Palm|Hand|Thumb|Index|Middle|Ring|Pinky|Neck|Head/;
  const clip = (name, keep) => {
    const c = robot.animations.find(a => a.name === name);
    return new THREE.AnimationClip(name, c.duration, c.tracks.filter(t => keep(armTrack.test(t.name))));
  };
  const seated = clip('Sitting', arm => !arm);
  const seatPoint = bl(...manifest.objects.robot.seat_point);
  for (const c of CAST) {
    const r = SkeletonUtils.clone(robot.scene);
    unlit(r, HOSTS[c.host]);
    for (const n of ['prop_pencil', 'prop_flask']) { const p = r.getObjectByName(n); if (p) p.visible = n === c.prop; }
    const s = seat(c.desk);
    r.position.copy(bl(...s)).sub(seatPoint);
    scene.add(r);
    const shadow = robotShadow.scene.clone();
    unlit(shadow);
    shadow.position.copy(bl(s[0], s[1], 0));
    scene.add(shadow);
    const mixer = new THREE.AnimationMixer(r);
    const sit = mixer.clipAction(seated);
    sit.setLoop(THREE.LoopOnce, 1);
    sit.clampWhenFinished = true;
    sit.play();
    sit.time = seated.duration;
    mixer.clipAction(clip(c.clip, arm => arm)).play();
    mixers.push(mixer);
  }
  // the lantern: an unlit magenta octahedron with a dark frame, as simple geometry (A has no lantern asset)
  const lanternGeo = new THREE.OctahedronGeometry(0.17);
  lanternGeo.scale(1, 1.35, 1);
  lanternGeo.rotateY(Math.PI / 4 - YAW);  // a vertex towards the camera, as the concept's diamond
  const lantern = new THREE.Mesh(lanternGeo, new THREE.MeshBasicMaterial({ color: '#d63ce8' }));
  lantern.add(new THREE.LineSegments(new THREE.EdgesGeometry(lanternGeo), new THREE.LineBasicMaterial({ color: '#3a2d3c' })));
  lantern.position.copy(bl(...LANTERN));
  scene.add(lantern);
  const cable = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 1), new THREE.MeshBasicMaterial({ color: '#3a2d3c' }));
  cable.position.copy(bl(LANTERN[0], LANTERN[1], LANTERN[2] + 0.73));
  scene.add(cable);
  missing.push('A: no lantern asset; the lantern is plain three.js geometry');
  const glowCanvas = layer();
  let last = 0;
  return t => {
    drawRoom();
    const dt = FROZEN !== null ? 0 : t - last;
    last = t;
    mixers.forEach(mx => (FROZEN !== null ? mx.setTime(FROZEN) : mx.update(dt)));
    renderer.render(scene, camera);
    glowCanvas.clearRect(0, 0, view.W, view.H);
    glows(glowCanvas, true);
  };
}

// --- architecture: walls of any length and a tiled floor, from a variant's tiles --------------------------

async function archB2() {
  const dir = ROOT + 'B2/';
  const m = await json(dir + 'sprites.json');
  const [floorImg, wallImg] = await Promise.all([image(dir + m.textures['floor-tile'].file), image(dir + m.textures['wall-tile'].file)]);
  const pil = { ...m.sprites.pilaster, px_per_m: m.px_per_m };
  const pilImg = await image(dir + pil.file);
  // a plane textured by an affine map: exact for an orthographic camera
  const plane = (g, img, origin, u, v, metres) => {
    const [ox, oy] = screen(origin), [ux, uy] = screen(origin.map((c, i) => c + u[i] * metres));
    const [vx, vy] = screen(origin.map((c, i) => c + v[i] * metres));
    g.save();
    g.setTransform((ux - ox) / img.width, (uy - oy) / img.width, (vx - ox) / img.height, (vy - oy) / img.height, ox, oy);
    g.fillStyle = g.createPattern(img, 'repeat');
    const n = 40;
    g.fillRect(-n * img.width, -n * img.height, 2 * n * img.width, 2 * n * img.height);
    g.restore();
  };
  const pilasters = [];
  for (let x = WALL.x0 + 1.2; x < WALL.x1; x += 2.9) pilasters.push(x);
  roomPainter = g => {
    g.fillStyle = '#dcebf8';
    g.fillRect(0, 0, view.W, view.H);
    g.save();
    quadPath(g, floorPts); g.clip();
    plane(g, floorImg, [ROOM.x0, ROOM.y1, 0], [1, 0, 0], [0, -1, 0], m.textures['floor-tile'].metres);
    g.restore();
    g.save();
    quadPath(g, wallPts); g.clip();
    plane(g, wallImg, [WALL.x0, WALL.y, WALL.h], [1, 0, 0], [0, 0, -1], m.textures['wall-tile'].metres);
    g.restore();
    for (const x of pilasters) place(g, pilImg, pil, [x, WALL.y - 0.15, 0]);
  };
  roomCache = null;
  return drawRoom;
}
function quadPath(g, pts) {
  g.beginPath();
  pts.map(screen).forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
  g.closePath();
}

// --- page -------------------------------------------------------------------------------------------------

function link(label, patch, current) {
  const q = new URLSearchParams(location.search);
  Object.entries(patch).forEach(([k, v]) => (v === null ? q.delete(k) : q.set(k, v)));
  q.set('zoom', zoom.toFixed(2));
  const a = document.createElement('a');
  a.textContent = label;
  a.href = '?' + q;
  if (current) a.setAttribute('aria-current', 'true');
  return a;
}

async function main() {
  if (params.has('shot')) document.body.classList.add('shot');
  document.getElementById('layers').checked = params.has('layers');
  const zoomInput = document.getElementById('zoom');
  zoomInput.value = zoom;
  setZoom(zoom);
  const vs = document.getElementById('variants');
  VARIANTS.forEach(v => vs.append(link(v, { variant: v }, v === VARIANT)));
  const views = document.getElementById('views');
  views.append(link('scene', { view: null }, VIEW_MODE === 'scene'), link('architecture', { view: 'arch' }, VIEW_MODE === 'arch'));
  const overlay = document.getElementById('overlay');
  document.getElementById('l2').addEventListener('change', e => { overlay.style.display = e.target.checked ? 'block' : 'none'; });

  let draw;
  if (VIEW_MODE === 'arch') {
    if (VARIANT.startsWith('B2')) draw = await archB2();
    else { missing.push(`${VARIANT}: no floor tile, wall segment or pilaster assets`); draw = drawRoom; }
  } else {
    draw = await ({ A: variantA, B1: variantB1, B2: () => variantB2(false), B2mix: () => variantB2(true) })[VARIANT]();
  }
  zoomInput.addEventListener('input', () => setZoom(Number(zoomInput.value)));
  document.getElementById('missing').textContent = missing.join('\n');

  const stats = document.getElementById('stats');
  const t0 = performance.now();
  let prev = null, frames = 0;
  const loop = now => {
    const start = performance.now();
    draw(FROZEN !== null ? FROZEN : (now - t0) / 1000);
    const work = performance.now() - start;
    if (prev !== null) frameLog.push([now - prev, work]);
    if (frameLog.length > 600) frameLog.shift();
    prev = now;
    if (++frames === 3) window.bakeoff.ready = true;
    if (frames % 30 === 0) {
      const s = frameStats(), b = bytes();
      stats.textContent = `${VARIANT} ${VIEW_MODE}\nframe ${s.interval_mean.toFixed(1)} ms (p95 ${s.interval_p95.toFixed(1)})\n`
        + `draw  ${s.work_mean.toFixed(2)} ms (p95 ${s.work_p95.toFixed(2)})\n`
        + `assets ${(b.assets / 1024).toFixed(0)} KB in ${b.files} files, code ${(b.code / 1024).toFixed(0)} KB`;
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

window.bakeoff = {
  ready: false, variant: VARIANT, missing,
  stats: () => ({ frame: frameStats(), bytes: bytes(), missing }),
  resetStats: () => { frameLog.length = 0; },
  setZoom: z => setZoom(z),
};
main().catch(e => {
  console.error(e);
  missing.push(String(e.message || e));
  document.getElementById('missing').textContent = missing.join('\n');
  window.bakeoff.error = String(e.message || e);
});
