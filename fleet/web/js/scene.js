// Three.js renderer, camera and lights; loaded models; shared geometry and canvas textures.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { BK, BOT_H, FS, HALF, PI, RD, REDUCED, RW, canvas, vh, vw } from './env.js';
import { rgba, rr, seeded } from './util.js';
import { THEMES } from './looks.js';
import { isSession, working } from './activity.js';

// ------------------------------------------------------------------ renderer, camera, lights
export const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;   // r186 folded PCFSoft into PCF; shadow.radius softens it
renderer.setClearColor(0x000000, 0);

export const scene = new THREE.Scene();
export const deckGroup = new THREE.Group();   // floors, walls, furniture: rebuilt when the set of rooms changes
export const botGroup = new THREE.Group();
scene.add(deckGroup, botGroup);

scene.add(new THREE.HemisphereLight(0xb4c8ff, 0x2a2238, 1.35));
export const sun = new THREE.DirectionalLight(0xfff0dc, 2.4);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.02;
sun.shadow.radius = 3;
scene.add(sun, sun.target);
const rim = new THREE.DirectionalLight(0x7fa2ff, 0.7);
rim.position.set(-8, 6, -10);
scene.add(rim);
export const SUN_DIR = new THREE.Vector3(0.45, 1, 0.7).normalize();

// True isometric: the camera looks down (-1,-1,-1), so the back walls are y = 0 and x = 0.
export const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 600);
const ISO = new THREE.Vector3(1, 1, 1).normalize();
export const RIGHT = new THREE.Vector3(1, 0, -1).normalize();
export const UP = new THREE.Vector3(-1, 2, -1).normalize();
export const cam = { c: new THREE.Vector3(), z: 40, userMoved: false, tween: null };   // c: a point on the view axis; z: pixels per tile
export function applyCamera() {
  camera.left = -vw / 2 / cam.z; camera.right = vw / 2 / cam.z;
  camera.top = vh / 2 / cam.z; camera.bottom = -vh / 2 / cam.z;
  camera.position.copy(cam.c).addScaledVector(ISO, 200);
  camera.lookAt(cam.c);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
}
export const _p = new THREE.Vector3();
export function toScreen(v, out) { _p.copy(v).project(camera); out.x = (_p.x + 1) / 2 * vw; out.y = (1 - _p.y) / 2 * vh; return out; }
// the camera centre that puts world point P at screen (sx, sy)
export function centreFor(P, sx, sy, z) {
  return new THREE.Vector3().copy(P).addScaledVector(RIGHT, -(sx - vw / 2) / z).addScaledVector(UP, (sy - vh / 2) / z);
}

// ------------------------------------------------------------------ assets: Kenney furniture + RobotExpressive
const KIT_MODELS = [...new Set(['wall', 'wallWindow', 'desk', 'chairDesk', 'computerScreen', 'computerKeyboard', 'laptop', 'bookcaseOpen', 'books',
  'kitchenCabinetDrawer', 'kitchenCabinet', 'kitchenCoffeeMachine', 'cardboardBoxClosed',
  // (plus every model the room themes use)
  ...Object.values(THEMES).flatMap(t => [...Object.values(t.roles), ...t.decor.map(d => d[0])]).filter(m => m && m !== 'rack')])];
export const KIT = {};       // name → { parts: [{ geometry, material, matrix }], size }, footprint centred on the origin, scaled by FS
export let ROBOT = null;     // { scene, clips, main, headBox, torsoBox }

export async function loadAssets() {
  const loader = new GLTFLoader();
  const kit = KIT_MODELS.map(async name => {
    const g = await loader.loadAsync(`/assets/models/furniture/${name}.glb`);
    const root = g.scene;
    root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(root);
    const norm = new THREE.Matrix4().makeScale(FS, FS, FS)
      .multiply(new THREE.Matrix4().makeTranslation(-(box.min.x + box.max.x) / 2, -box.min.y, -(box.min.z + box.max.z) / 2));
    const parts = [];
    root.traverse(o => { if (o.isMesh) parts.push({ geometry: o.geometry, material: o.material, matrix: norm.clone().multiply(o.matrixWorld) }); });
    KIT[name] = { parts, size: box.getSize(new THREE.Vector3()).multiplyScalar(FS) };
  });
  const bot = loader.loadAsync('/assets/models/robot/RobotExpressive.glb').then(g => {
    const tpl = g.scene;
    tpl.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(tpl);
    tpl.scale.setScalar(BOT_H / (box.max.y - box.min.y));
    tpl.updateMatrixWorld(true);
    // bones and meshes share names in this file, so the loader suffixes one of each pair ("Head", "Head_1")
    let main = null, headMesh = null, torsoMesh = null;
    const isPart = (o, name) => !o.isBone && new RegExp(`^${name}(_\\d+)?$`).test(o.name) && (o.isMesh || o.children.some(c => c.isMesh));
    tpl.traverse(o => {
      if (o.isMesh && !Array.isArray(o.material) && o.material.name === 'Main') main = o.material;
      if (!headMesh && isPart(o, 'Head')) headMesh = o;
      if (!torsoMesh && isPart(o, 'Torso')) torsoMesh = o;
    });
    if (!main || !headMesh || !torsoMesh) {
      const names = []; tpl.traverse(o => { if (!o.isBone) names.push(o.name); });
      throw new Error(`RobotExpressive.glb: no ${!main ? 'Main material' : !headMesh ? 'Head mesh' : 'Torso mesh'} (nodes: ${names.join(', ')})`);
    }
    // the face morph targets go away with the merge, so drop their tracks from the clips
    for (const c of g.animations) c.tracks = c.tracks.filter(t => !t.name.endsWith('.morphTargetInfluences'));
    ROBOT = { scene: tpl, clips: Object.fromEntries(g.animations.map(c => [c.name, c])), main,
      headBox: new THREE.Box3().setFromObject(headMesh), torsoBox: new THREE.Box3().setFromObject(torsoMesh) };
    mergeRobot(tpl);
  });
  await Promise.all([...kit, bot]);
  // pull the kit's daylight palette towards the night deck: slate upholstery, smoked wood
  const RECOLOR = { carpet: '#56648a', carpetWhite: '#8a96b4', wood: '#a88d72', woodDark: '#6e5a48' };
  for (const k of Object.values(KIT)) for (const p of k.parts) {
    const m = p.material;
    if (RECOLOR[m.name]) m.color.set(RECOLOR[m.name]);
    if (m.name === 'glass') { m.transparent = true; m.opacity = 0.25; m.depthWrite = false; }
  }
  registerPrims();
}

// The robot ships as a couple of dozen meshes, some skinned and some rigid parts hanging off bones. Merge them all
// into one skinned mesh with a group per material, so an android costs three draw calls. Every part is baked into
// the skeleton's bind space (rigid parts become single-bone skins); in attached bind mode that makes the result
// independent of where the merged mesh sits.
function mergeRobot(tpl) {
  const parts = [];
  tpl.traverse(o => { if (o.isMesh) parts.push(o); });
  parts.sort((a, b) => b.isSkinnedMesh - a.isSkinnedMesh);   // skins first: their inverse bind matrices are authoritative
  const bones = [], inverses = [], boneIndex = new Map(), byMat = new Map(), merged = [];
  const toLoad = tpl.matrixWorld.clone().invert();      // bind poses were recorded before the robot was scaled
  const boneOf = (b, inverse) => {
    if (!boneIndex.has(b)) { boneIndex.set(b, bones.length); bones.push(b); inverses.push(inverse()); }
    return boneIndex.get(b);
  };
  const indexed = parts.every(p => p.geometry.index);
  for (const p of parts) {
    const src = p.geometry, g = new THREE.BufferGeometry(), n = src.attributes.position.count;
    for (const name of ['position', 'normal']) g.setAttribute(name, src.attributes[name].clone());
    const idx = new Uint16Array(n * 4);
    if (p.isSkinnedMesh) {
      // skin indices point into this part's skeleton; remap them onto one shared bone list
      const remap = p.skeleton.bones.map((b, i) => boneOf(b, () => p.skeleton.boneInverses[i].clone()));
      const si = src.attributes.skinIndex;
      for (let i = 0; i < n; i++) for (let k = 0; k < 4; k++) idx[i * 4 + k] = remap[si.getComponent(i, k)];
      const sw = src.attributes.skinWeight, w = new Float32Array(n * 4);   // may be normalised bytes; rigid parts use floats
      for (let i = 0; i < n; i++) for (let k = 0; k < 4; k++) w[i * 4 + k] = sw.getComponent(i, k) / (sw.normalized && sw.array instanceof Uint8Array ? 255 : sw.normalized && sw.array instanceof Uint16Array ? 65535 : 1);
      g.setAttribute('skinWeight', new THREE.BufferAttribute(w, 4));
      g.applyMatrix4(p.bindMatrix);
    } else {
      let b = p.parent;
      while (b && !b.isBone) b = b.parent;
      if (!b) continue;
      // bone · inverse · v' must equal bone · (bone at load)⁻¹ · (part at load) · v, whichever inverse the bone got
      const boneLoad = toLoad.clone().multiply(b.matrixWorld);
      const bi = boneOf(b, () => boneLoad.clone().invert());
      const w = new Float32Array(n * 4);
      for (let i = 0; i < n; i++) { idx[i * 4] = bi; w[i * 4] = 1; }
      g.setAttribute('skinWeight', new THREE.BufferAttribute(w, 4));
      g.applyMatrix4(inverses[bi].clone().invert().multiply(boneLoad.invert()).multiply(toLoad.clone().multiply(p.matrixWorld)));
    }
    g.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(idx, 4));
    if (indexed) g.setIndex(src.index.clone());
    const list = byMat.get(p.material) || byMat.set(p.material, []).get(p.material);
    list.push(indexed ? g : g.toNonIndexed());
    merged.push(p);
  }
  const mats = [...byMat.keys()];
  const geo = mergeGeometries(mats.map(m => mergeGeometries(byMat.get(m), false)), true);
  if (!geo) throw new Error('RobotExpressive.glb: could not merge the robot parts');
  for (const p of merged) p.removeFromParent();
  const mesh = new THREE.SkinnedMesh(geo, mats);
  mesh.name = 'Robot';
  tpl.add(mesh);
  mesh.bind(new THREE.Skeleton(bones, inverses), new THREE.Matrix4());
}

// shared bits of geometry and material
export const G = {
  box: new THREE.BoxGeometry(1, 1, 1),
  plane: new THREE.PlaneGeometry(1, 1),
  ring: new THREE.RingGeometry(0.4, 0.5, 48).rotateX(-HALF),
  disc: new THREE.CircleGeometry(0.62, 40).rotateX(-HALF),
  sphere: new THREE.SphereGeometry(1, 16, 12),
  proxy: new THREE.CylinderGeometry(0.42 * BK, 0.42 * BK, BOT_H, 10).translate(0, BOT_H / 2, 0),
};
export function softDot(inner, outer) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, inner); gr.addColorStop(1, outer);
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
export const M = {
  plate: new THREE.MeshStandardMaterial({ color: 0x0f1726, roughness: 0.9, metalness: 0.2 }),
  dark:new THREE.MeshStandardMaterial({ color: 0x1b2333, roughness: 0.6, metalness: 0.4 }),
  wallTint: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85 }),
  beacon: new THREE.MeshBasicMaterial({ color: 0xf87171 }),
  // steady: the room's attention lantern is the one thing that calls for you
  failGlow: new THREE.SpriteMaterial({ map: softDot('rgba(255,80,80,1)', 'rgba(255,60,60,0)'), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.6 }),
  doneRing: new THREE.MeshBasicMaterial({ color: 0x4ade80, transparent: true, opacity: 0.55, depthWrite: false }),
  doneDisc: new THREE.MeshBasicMaterial({ map: softDot('rgba(74,222,128,.55)', 'rgba(74,222,128,0)'), transparent: true, depthWrite: false }),
  hidden: new THREE.MeshBasicMaterial({ visible: false }),
  blob: new THREE.MeshBasicMaterial({ map: softDot('rgba(0,0,0,.6)', 'rgba(0,0,0,0)'), transparent: true, depthWrite: false }),
};

// ------------------------------------------------------------------ static geometry: instanced across every room
// Kenney models and the deck's own primitives each become one InstancedMesh per part, shared by every room, so ten
// rooms cost the same few dozen draw calls as one. Per-room colour comes from instance colours.
export class Placer {
  constructor() { this.byModel = new Map(); this.meshes = new Map(); }
  // returns a handle for instances that change later (lights, pulses)
  add(model, x, z, rot = 0, y = 0, scale = null, tint = null) {
    const m = new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), new THREE.Quaternion().setFromAxisAngle(THREE.Object3D.DEFAULT_UP, rot),
      scale || new THREE.Vector3(1, 1, 1));
    if (!this.byModel.has(model)) this.byModel.set(model, []);
    const list = this.byModel.get(model);
    list.push({ m, tint });
    return { model, i: list.length - 1, base: m };
  }
  build(group, sink = disposables) {   // sink: where merged geometry goes to be disposed with its owner
    const tmp = new THREE.Matrix4(), col = new THREE.Color();
    const merged = new Map();   // material signature → { material, geos, shadow }
    for (const [model, list] of this.byModel) {
      const kit = KIT[model], meshes = [];
      if (!kit.prim) { mergeKit(kit, list, merged); continue; }
      for (const part of kit.parts) {
        const tinted = kit.prim || (list[0].tint && part.material.name !== 'metalDark' && part.material.name !== 'glass');
        const im = new THREE.InstancedMesh(part.geometry, tinted && !kit.prim ? M.wallTint : part.material, list.length);
        list.forEach((it, i) => {
          im.setMatrixAt(i, tmp.multiplyMatrices(it.m, part.matrix));
          if (tinted) im.setColorAt(i, col.set(it.tint || '#ffffff'));
        });
        im.castShadow = part.shadow ?? part.material.name !== 'glass';
        im.receiveShadow = !kit.prim || part.shadow !== false;
        im.computeBoundingSphere();
        group.add(im);
        meshes.push({ im, part });
      }
      this.meshes.set(model, meshes);
    }
    for (const { material, geos, shadow } of merged.values()) {
      const geo = mergeGeometries(geos, false);
      for (const g of geos) g.dispose();
      const mesh = new THREE.Mesh(geo, material);
      mesh.castShadow = shadow; mesh.receiveShadow = true;
      group.add(mesh);
      sink.push(geo);
    }
  }
  setColor(h, hex) {
    for (const { im } of this.meshes.get(h.model)) { im.setColorAt(h.i, _col.set(hex)); im.instanceColor.needsUpdate = true; }
  }
  setMatrix(h, m) {
    for (const { im, part } of this.meshes.get(h.model)) { im.setMatrixAt(h.i, _m4.multiplyMatrices(m, part.matrix)); im.instanceMatrix.needsUpdate = true; }
  }
}
// Kenney models never change once placed, so every instance of every model is baked into one mesh per material
// across the whole deck: the draw calls stay flat however many models and themes the rooms use. Parts that are tinted
// (walls in the project colour) take the tint as a vertex colour on one shared material.
const TINT_MAT = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85, vertexColors: true });
const mergedMats = new Map();
function mergeKit(kit, list, merged) {
  const col = new THREE.Color();
  for (const part of kit.parts) {
    const m = part.material, tinted = !!list[0].tint && m.name !== 'metalDark' && m.name !== 'glass';
    const sig = tinted ? 'tint' : [m.name, m.color.getHexString(), m.opacity, m.transparent, m.roughness, m.metalness, m.map ? m.map.uuid : ''].join('|');
    if (!merged.has(sig)) {
      if (!tinted && !mergedMats.has(sig)) mergedMats.set(sig, m);
      merged.set(sig, { material: tinted ? TINT_MAT : mergedMats.get(sig), geos: [], shadow: m.name !== 'glass' && !kit.flat });
    }
    const bucket = merged.get(sig);
    for (const it of list) {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', part.geometry.attributes.position.clone());
      g.setAttribute('normal', part.geometry.attributes.normal.clone());
      if (m.map) g.setAttribute('uv', part.geometry.attributes.uv.clone());
      g.setIndex(part.geometry.index ? part.geometry.index.clone() : [...Array(g.attributes.position.count).keys()]);
      g.applyMatrix4(_m4.multiplyMatrices(it.m, part.matrix));
      if (tinted) {
        col.set(it.tint || '#ffffff');
        const n = g.attributes.position.count, c = new Float32Array(n * 3);
        for (let i = 0; i < n; i++) { c[i * 3] = col.r; c[i * 3 + 1] = col.g; c[i * 3 + 2] = col.b; }
        g.setAttribute('color', new THREE.BufferAttribute(c, 3));
      }
      bucket.geos.push(g);
    }
  }
}
export const _col = new THREE.Color(), _m4 = new THREE.Matrix4(), _m4b = new THREE.Matrix4();
export const _q = new THREE.Quaternion(), _v = new THREE.Vector3(), _sc = new THREE.Vector3();

// the deck's own shapes, registered next to the Kenney models so the Placer can instance them
function registerPrims() {
  const I = new THREE.Matrix4();
  const mat = {
    std: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.8 }),
    metal: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.45, metalness: 0.6 }),
    dish: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.35, metalness: 0.5, side: THREE.DoubleSide }),
    glow: new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }),
    add: new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false, side: THREE.DoubleSide }),
    board: new THREE.MeshStandardMaterial({ map: whiteboardTexture(), roughness: 0.4 }),
  };
  const prim = (geometry, material, shadow) => ({ prim: true, parts: [{ geometry, material, matrix: I, shadow }] });
  Object.assign(KIT, {
    box: prim(G.box, mat.std, true),
    metalBox: prim(G.box, mat.metal, true),
    glowBox: prim(G.box, mat.glow, false),
    lightBox: prim(G.box, mat.add, false),
    board: prim(G.plane, mat.board, false),
    slit: prim(G.plane, mat.glow, false),
    dish: prim(new THREE.SphereGeometry(0.5, 24, 10, 0, PI * 2, 0, 0.95).rotateX(-2.2), mat.dish, true),
    orb: prim(G.sphere, mat.glow, false),
    pulse: prim(new THREE.RingGeometry(0.8, 1, 40).rotateX(-0.6), mat.add, false),
    beacon: prim(G.sphere, M.beacon, false),
  });
  // one floor per pattern (room themes pick one); rooms sharing a pattern share its draw call
  for (const style of FLOORS) KIT['floor:' + style] = prim(G.box, new THREE.MeshStandardMaterial({ color: 0xffffff, map: floorTexture(style),
    roughness: style === 'grate' ? 0.55 : 0.8, metalness: style === 'grate' ? 0.35 : 0.1 }), false);
}

// ------------------------------------------------------------------ canvas textures: screens, whiteboard, signs
export function canvasTex(w, h) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return { c, g: c.getContext('2d'), tex };
}
const SCREEN_LINES = [0.55, 0.3, 0.72, 0.42, 0.62, 0.5, 0.36, 0.68];
export function drawScreen(s, t) {
  const { g, c } = s, w = c.width, h = c.height;
  g.fillStyle = s.shown === false ? '#2a0707' : s.busy ? '#03140c' : '#071a30';
  g.fillRect(0, 0, w, h);
  if (s.shown != null) {
    // a test run's verdict: a big pass bar or a red fail
    g.fillStyle = s.shown ? '#22c55e' : '#ef4444';
    g.fillRect(10, 22, w - 20, h - 44);
    g.fillStyle = s.shown ? '#03140c' : '#2a0707';
    g.font = '700 26px JetBrains Mono, monospace'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(s.shown ? 'PASS' : 'FAIL', w / 2, h / 2 + 1);
    g.textAlign = 'start'; g.textBaseline = 'alphabetic';
  } else if (s.test) {
    // tests running: a grid of dots filling in, the odd one amber
    const n = REDUCED ? 20 : Math.floor(t * 9 + s.seed * 5) % 41;
    for (let k = 0; k < 40; k++) {
      g.fillStyle = k < n ? ((k * 7 + s.seed) % 13 === 0 ? '#facc15' : '#4ade80') : 'rgba(74,222,128,.15)';
      g.fillRect(9 + (k % 10) * 11.2, 12 + Math.floor(k / 10) * 15, 7, 8);
    }
  } else if (s.busy) {
    g.fillStyle = '#4ade80';
    const scroll = REDUCED ? 0 : (t * 26) % 12;
    for (let k = 0; k < 9; k++) {
      const y = h - 8 - k * 12 + scroll;
      if (y < 4 || y > h - 4) continue;
      g.globalAlpha = k === 0 ? 1 : 0.85;
      g.fillRect(8, y, SCREEN_LINES[(k + s.seed + Math.floor(REDUCED ? 0 : t * 2.2)) % 8] * (w - 16), 5);
    }
    g.globalAlpha = 1;
  } else {
    g.fillStyle = 'rgba(125,180,255,.75)';
    g.fillRect(8, 10, 34, 5);
    if (REDUCED || Math.sin(t * 3 + s.seed) > 0) g.fillRect(8, 22, 8, 7);
  }
  s.tex.needsUpdate = true;
}
let boardTex = null;
function whiteboardTexture() {
  if (boardTex) return boardTex;
  const { g, tex } = canvasTex(512, 256);
  g.fillStyle = '#e8eef7'; g.fillRect(0, 0, 512, 256);
  g.lineWidth = 5; g.lineCap = 'round'; g.lineJoin = 'round';
  const line = (pts, c) => { g.strokeStyle = c; g.beginPath(); pts.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y)); g.stroke(); };
  line([[30, 50], [190, 50]], '#1e3a8a'); line([[30, 90], [150, 90]], '#1e3a8a'); line([[30, 130], [210, 130]], '#b91c1c');
  line([[260, 150], [320, 70], [380, 120], [450, 40]], '#047857');
  g.strokeStyle = '#1e3a8a'; g.lineWidth = 3; g.strokeRect(250, 30, 220, 150);
  for (const [x, c] of [[40, '#fde047'], [110, '#f9a8d4'], [180, '#86efac']]) { g.fillStyle = c; g.fillRect(x, 170, 55, 55); }
  boardTex = tex;
  return tex;
}
export function drawSign(room) {
  const { g, c, tex } = room.sign;
  const { look } = room;
  const running = room.ents.filter(e => working(e.job)).length, sessions = room.ents.filter(isSession).length;
  const jobs = room.ents.length - sessions;
  const lines = room.pipelineCount ? `${room.pipelineCount} pipeline${room.pipelineCount === 1 ? '' : 's'}` : '';
  const sub = room.ents.length ? `${running} running · ${jobs} job${jobs === 1 ? '' : 's'}${sessions ? ` · ${sessions} live` : ''}${lines ? ' · ' + lines : ''}`
    : lines || 'empty';
  const key = room.label + sub;
  if (room.signKey === key) return;
  room.signKey = key;
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = 'rgba(6,10,20,.94)'; rr(g, 4, 4, c.width - 8, c.height - 8, 22); g.fill();
  g.strokeStyle = rgba(look.accent, 0.8); g.lineWidth = 5; g.stroke();
  g.fillStyle = look.accent; g.fillRect(4, 26, 12, c.height - 52);
  g.fillStyle = '#f2f7ff'; g.font = '700 92px Space Grotesk, system-ui, sans-serif';
  g.shadowColor = look.accent; g.shadowBlur = 18;
  g.fillText(room.label.toUpperCase(), 46, 118, c.width - 80);
  g.shadowBlur = 0;
  g.fillStyle = rgba(look.accent, 0.95); g.font = '500 46px JetBrains Mono, monospace';
  g.fillText(sub, 48, 196, c.width - 80);
  tex.needsUpdate = true;
}
// floor patterns, drawn light so the room's floor colour tints them; one 128px tile covers 2×2 room tiles
function floorTexture(style) {
  const { g, tex } = canvasTex(128, 128);
  const line = (x0, y0, x1, y1) => { g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke(); };
  g.fillStyle = '#ffffff'; g.fillRect(0, 0, 128, 128);
  if (style === 'planks') {        // boards with staggered joints
    for (let k = 0; k < 8; k++) {
      g.fillStyle = ['#f2ede6', '#e2d9cc', '#ebe4d9', '#d9cfc1'][k % 4]; g.fillRect(0, k * 16, 128, 16);
      g.strokeStyle = 'rgba(0,0,0,.22)'; g.lineWidth = 1.5; line(0, k * 16, 128, k * 16);
      const j = (k * 47) % 128; line(j, k * 16, j, k * 16 + 16);
    }
  } else if (style === 'tile') {   // small square tiles with grout
    g.strokeStyle = 'rgba(0,0,0,.16)'; g.lineWidth = 2;
    for (let k = 0; k <= 128; k += 32) { line(k, 0, k, 128); line(0, k, 128, k); }
  } else if (style === 'carpet') { // flecked
    const rand = seeded(7);
    for (let k = 0; k < 900; k++) { g.fillStyle = rand() < 0.5 ? 'rgba(0,0,0,.07)' : 'rgba(255,255,255,.5)'; g.fillRect(rand() * 128, rand() * 128, 2, 2); }
  } else if (style === 'grate') {  // raised-floor panels with vent slots
    g.fillStyle = '#c9ced8'; g.fillRect(0, 0, 128, 128);
    g.strokeStyle = 'rgba(0,0,0,.35)'; g.lineWidth = 3;
    for (let k = 0; k <= 128; k += 64) { line(k, 0, k, 128); line(0, k, 128, k); }
    g.fillStyle = 'rgba(0,0,0,.25)';
    for (let k = 0; k < 5; k++) { g.fillRect(12, 12 + k * 8, 40, 3); g.fillRect(76, 76 + k * 8, 40, 3); }
  } else {                         // checker
    g.fillStyle = '#d2d6de'; g.fillRect(0, 0, 64, 64); g.fillRect(64, 64, 64, 64);
    g.strokeStyle = 'rgba(0,0,0,.18)'; g.lineWidth = 2;
    for (let k = 0; k <= 128; k += 64) { line(k, 0, k, 128); line(0, k, 128, k); }
  }
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(RW / 2, RD / 2);
  return tex;
}
const FLOORS = ['checker', 'planks', 'tile', 'carpet', 'grate'];
let dashTex = null;
export function dashTexture() {
  if (dashTex) return dashTex;
  const { g, tex } = canvasTex(64, 8);
  g.fillStyle = '#38bdf8'; g.fillRect(0, 2, 26, 4);
  tex.wrapS = THREE.RepeatWrapping;
  dashTex = tex;
  return tex;
}

export const disposables = [];

export const _w = new THREE.Vector3();
