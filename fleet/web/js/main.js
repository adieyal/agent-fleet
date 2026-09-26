// Deck entry point: the frame loop and boot.

import * as THREE from 'three';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import {
  BK, BOT_H, DEBUG, DEMO, DESK_TOP, GAP, HALF, PI, POLL_MS, QS, RD, REDUCED, RW, SMALL_Z, SPEED, TINY_Z, WALL_H,
  WARP, canvas, dpr, setDpr, setVh, setVw, tagsEl, vh, vw,
} from './env.js';
import { age, angleTo, clamp, clock, esc, hash, hsl, mix, rr, seeded, store, trunc } from './util.js';
import {
  ACTS, AGENT_COLOR, AISLES, APART_ACROSS, APART_ALONG, CROSSINGS, DOOR_X0, DOOR_X1, FACE_VIEWER, FLOOR,
  FURNITURE, KITCHEN, OUTBOX, OVERFLOW, RACK, SPOTS, TERMINALS, THEMES, TOOL_ICON, apart, blocked, hostLook,
  projectLook, stationOf, themeFor,
} from './looks.js';
import { DOC_FILE, activityFor, activityOf, isActive, isSession, mumble, shortId } from './activity.js';
import {
  G, KIT, M, Placer, RIGHT, ROBOT, SUN_DIR, UP, _col, _m4, _m4b, _p, _q, _sc, _v, _w, applyCamera, botGroup,
  cam, camera, canvasTex, centreFor, dashTexture, deckGroup, disposables, drawScreen, drawSign, loadAssets,
  renderer, scene, softDot, sun, toScreen,
} from './scene.js';
import {
  ents, everLoaded, feed, feedSeeded, hosts, live, seenEvents, selectedKey, setEverLoaded, setFeedSeeded,
  setHosts, setLive, setSelectedKey,
} from './model.js';

// ------------------------------------------------------------------ the deck: plates, rooms, furniture
let placer = null;

const edgeStrips = [];
function buildDeck() {
  for (const d of disposables.splice(0)) d.dispose();
  deckGroup.clear();
  edgeStrips.length = 0;
  placer = new Placer();
  for (const b of plates) buildPlate(b, placer);
  for (const r of rooms) buildRoom(r, placer);
  placer.build(deckGroup);
  docSig = null;
  // light the whole deck with one shadow map
  const b = deckBounds();
  const cx = (b.x0 + b.x1) / 2, cz = (b.y0 + b.y1) / 2, rad = Math.hypot(b.x1 - b.x0, b.y1 - b.y0) / 2 + 2;
  sun.target.position.set(cx, 0, cz);
  sun.position.set(cx, 0, cz).addScaledVector(SUN_DIR, rad + 10);
  const sc = sun.shadow.camera;
  sc.left = -rad; sc.right = rad; sc.top = rad; sc.bottom = -rad; sc.near = 1; sc.far = rad * 2 + 20;
  sc.updateProjectionMatrix();
}

function buildPlate(b, place) {
  const w = b.x1 - b.x0, d = b.y1 - b.y0, cx = (b.x0 + b.x1) / 2, cz = (b.y0 + b.y1) / 2;
  const slab = new THREE.Mesh(G.box, M.plate);
  slab.scale.set(w, 0.5, d); slab.position.set(cx, -0.3, cz); slab.receiveShadow = true;
  deckGroup.add(slab);
  // faint tile grid
  const pts = [];
  for (let x = Math.ceil(b.x0); x <= b.x1; x++) pts.push(x, -0.045, b.y0, x, -0.045, b.y1);
  for (let z = Math.ceil(b.y0); z <= b.y1; z++) pts.push(b.x0, -0.045, z, b.x1, -0.045, z);
  const gg = new THREE.BufferGeometry(); gg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  const gm = new THREE.LineBasicMaterial({ color: 0x7ea6e0, transparent: true, opacity: 0.07 });
  deckGroup.add(new THREE.LineSegments(gg, gm));
  disposables.push(gg, gm);
  // running edge lights on the two visible sides
  for (const [len, x, z, ry] of [[w, cx, b.y1 + 0.002, 0], [d, b.x1 + 0.002, cz, HALF]]) {
    const tex = dashTexture().clone(); tex.repeat.set(len * 1.4, 1); tex.needsUpdate = true;
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.8, depthWrite: false });
    const strip = new THREE.Mesh(G.plane, mat);
    strip.scale.set(len, 0.1, 1); strip.position.set(x, -0.3, z); strip.rotation.y = ry;
    deckGroup.add(strip);
    edgeStrips.push(tex);
    disposables.push(tex, mat);
  }
  for (const [x, z] of [[b.x0, b.y0], [b.x1, b.y0], [b.x0, b.y1], [b.x1, b.y1]]) place.add('beacon', x, z, 0, 0.02, new THREE.Vector3(0.09, 0.09, 0.09));
}

// a server rack from primitives: dark cabinet, blinking-free status lights on the front (rot: where the front faces)
function serverRack(at, S, x, y, rot) {
  const fx = Math.sin(rot), fy = Math.cos(rot), rx = Math.cos(rot), ry = -Math.sin(rot), H = 1.45;
  at('metalBox', x, y, H / 2, S(0.72, H, 0.5), '#232c3d', rot);
  at('box', x + fx * 0.251, y + fy * 0.251, H * 0.52, S(0.58, H * 0.84, 0.01), '#0b111d', rot);
  for (let k = 0; k < 8; k++) {
    const u = k % 2 ? 0.13 : -0.13, v = 0.3 + Math.floor(k / 2) * 0.28;
    at('glowBox', x + fx * 0.26 + rx * u, y + fy * 0.26 + ry * u, v, S(0.22, 0.035, 0.01), ['#22d3ee', '#4ade80', '#4ade80', '#f59e0b'][k % 4], rot);
  }
}
// Wall art from primitives, so it costs no extra draw calls: [model, across, up, width, height, colour, out from the wall]
function wallArt(at, S, wall, along, kind, look) {
  const a = look.accent, b = hsl((look.hue + 150) % 360, 60, 62), c = hsl((look.hue + 60) % 360, 55, 55);
  const P = {
    poster:   [['box', 0, 0, 0.56, 0.72, '#1b2333', 0], ['box', 0, 0.04, 0.46, 0.54, a, 0.012], ['box', 0, -0.26, 0.46, 0.1, b, 0.012]],
    chart:    [['box', 0, 0, 0.8, 0.56, '#e8eef7', 0], ['box', -0.24, -0.1, 0.12, 0.2, a, 0.012], ['box', -0.06, -0.05, 0.12, 0.3, b, 0.012],
               ['box', 0.12, 0.02, 0.12, 0.44, c, 0.012], ['box', 0.28, -0.14, 0.08, 0.12, a, 0.012]],
    painting: [['box', 0, 0, 0.8, 0.6, '#b08d57', 0], ['box', 0, 0, 0.68, 0.48, '#1d2a3a', 0.012], ['box', 0, -0.16, 0.68, 0.16, mix(c, '#1d2a3a', 0.3), 0.02],
               ['box', 0.18, 0.1, 0.1, 0.1, '#f2c14e', 0.02]],
    panel:    [['box', 0, 0, 0.72, 0.5, '#0b111d', 0], ...[0, 1, 2, 3, 4, 5].map(k => ['glowBox', -0.2 + (k % 3) * 0.2, k < 3 ? 0.1 : -0.1, 0.14, 0.1,
               ['#22d3ee', '#4ade80', '#f59e0b', '#4ade80', a, '#4ade80'][k], 0.012])],
    canvas:   [['box', 0, 0, 0.92, 0.62, '#f4efe6', 0], ['box', -0.14, 0.07, 0.5, 0.3, a, 0.012], ['box', 0.24, -0.06, 0.24, 0.4, b, 0.02],
               ['box', -0.2, -0.2, 0.3, 0.08, c, 0.02]],
  }[kind];
  const V = 1.2;
  for (const [model, u, v, w, h, color, dz] of P) {
    if (wall === 'back') at(model, along + u, 0.07 + dz, V + v, S(w, h, 0.03), color, 0);
    else at(model, 0.07 + dz, along - u, V + v, S(w, h, 0.03), color, HALF);
  }
}

// document press on the right-hand wall: a fabricator at the back, one tray per job along the console
const PRESS = { x: 11.35, y0: 2.95, y1: 5.65, top: 0.5, fab: 3.25, trays: 3.62 };
function buildRoom(r, place) {
  const { ox, oy, look } = r;
  const S = (x, y, z) => new THREE.Vector3(x, y, z);
  const at = (model, x, y, h, scale, color, rot = 0) => place.add(model, ox + x, oy + y, rot, h, scale, color);
  const T = THEMES[r.theme];
  at('floor:' + T.floor, RW / 2, RD / 2, -0.04, S(RW, 0.08, RD), look.floor);

  // back walls (Kenney wall panels, tinted per project) with windows onto space
  const wallScale = S(1, WALL_H / KIT.wall.size.y, 1);
  for (let i = 0; i < RW / 2; i++) place.add(i === 4 ? 'wallWindow' : 'wall', ox + i * 2 + 1, oy - 0.05, 0, 0, wallScale, look.wall);
  for (let i = 0; i < RD / 2; i++) place.add(i === 1 ? 'wallWindow' : 'wall', ox - 0.05, oy + i * 2 + 1, HALF, 0, wallScale, look.wall);
  at('glowBox', RW / 2 - 0.05, 0.02, WALL_H - 0.1, S(RW + 0.1, 0.05, 0.05), look.accent);
  at('glowBox', 0.02, RD / 2, WALL_H - 0.1, S(0.05, 0.05, RD), look.accent);

  // low front rims with a door gap
  at('box', DOOR_X0 / 2, RD, 0.08, S(DOOR_X0, 0.16, 0.12), look.rim);
  at('box', (DOOR_X1 + RW) / 2, RD, 0.08, S(RW - DOOR_X1, 0.16, 0.12), look.rim);
  at('box', RW, RD / 2, 0.08, S(0.12, 0.16, RD + 0.12), look.rim);
  at('lightBox', (DOOR_X0 + DOOR_X1) / 2, RD, 0.01, S(DOOR_X1 - DOOR_X0, 0.02, 0.3), mix(look.accent, '#000000', 0.4));

  // furniture (roles filled by the theme), then the theme's decor and wall art
  const lift = h => h === 'counter' ? KIT.kitchenCabinet.size.y : typeof h === 'string' ? KIT[h.slice(3)].size.y : h || 0;
  const put = (model, x, y, rot, h) => {
    if (model && model[0] === '@') model = T.roles[model.slice(1)];
    if (!model) return;
    if (model === 'rack') serverRack(at, S, x, y, rot);
    else at(model, x, y, lift(h), null, null, rot);
  };
  for (const [model, x, y, rot, h] of FURNITURE) put(model, x, y, rot, h);
  for (const [model, x, y, rot, h] of T.decor) put(model, x, y, rot, h);
  for (const [wall, along, kind] of T.art) wallArt(at, S, wall, along, kind, look);
  // books on the open shelves
  for (let s = 0; s < 2; s++) for (const [lift, dz] of [[0.05, -0.2], [0.47, 0.15], [0.9, -0.1], [1.32, 0.18]]) at('books', 0.3, (s ? 3.12 : 2.3) + dz, lift, null, null, HALF);

  // terminal screens: a glowing canvas in front of each monitor (their own draw calls: each shows its own text)
  const g = new THREE.Group();
  deckGroup.add(g);
  r.screens = TERMINALS.map((x, i) => {
    const s = { ...canvasTex(128, 80), busy: null, next: 0, seed: i * 3 + hash(r.name) % 5 };
    const mat = new THREE.MeshBasicMaterial({ map: s.tex, toneMapped: false });
    const m = new THREE.Mesh(G.plane, mat);
    m.scale.set(0.62, 0.36, 1); m.position.set(ox + x, DESK_TOP + 0.36, oy + 0.3 + KIT.computerScreen.size.z / 2 + 0.006);
    g.add(m);
    disposables.push(s.tex, mat);
    return s;
  });

  // whiteboard for planning, with a marker light that sketches while someone plans
  at('metalBox', 7.95, 0.05, 1.0, S(2.3, 1.1, 0.06), '#8b98ad');
  at('board', 7.95, 0.081, 1.0, S(2.16, 0.98, 1), '#ffffff');
  at('metalBox', 7.95, 0.12, 0.43, S(2.3, 0.06, 0.16), '#8b98ad');
  r.pen = at('orb', 7.95, 0.1, 1.0, S(1, 1, 1), '#4ade80');   // scaled per frame by updateRoom

  // comms dish for web lookups: the tip lights and waves ripple out while someone is on the web
  at('box', 10.75, 0.9, 0.07, S(0.6, 0.14, 0.6), '#1b2333');
  at('metalBox', 10.75, 0.9, 0.7, S(0.08, 1.25, 0.08), '#8b98ad');
  at('dish', 10.75, 0.95, 1.55, null, '#c7d2e3');
  r.tip = at('orb', 10.75, 1.32, 1.62, S(0.07, 0.07, 0.07), '#64748b');
  r.pulses = [0, 1].map(() => at('pulse', 10.75, 1.32, 1.62, S(1, 1, 1), '#38bdf8'));

  // kitchen corner: coffee machine on the counter (steams while someone waits there)
  at('kitchenCoffeeMachine', KITCHEN.x, KITCHEN.y, KIT.kitchenCabinet.size.y, null, null, HALF);
  r.steamAt = new THREE.Vector3(ox + KITCHEN.x + 0.1, KIT.kitchenCabinet.size.y + KIT.kitchenCoffeeMachine.size.y + 0.05, oy + KITCHEN.y);

  // outbox: a mail chute by the door, its slot lights up as a parcel goes in
  const O = OUTBOX;
  at('metalBox', O.x, O.y, O.h / 2, S(0.72, O.h, 0.42), '#3a475f');
  at('box', O.x, O.y, O.h + 0.02, S(0.8, 0.04, 0.5), '#1b2333');
  r.mailSlot = at('glowBox', O.x, O.y - 0.04, O.h + 0.045, S(0.52, 0.02, 0.09), mix(look.accent, '#000000', 0.5));
  at('cardboardBoxClosed', 2.9, 9.45, KIT.cardboardBoxClosed.size.y, S(0.8, 0.8, 0.8), null, -0.2);

  // build machine: a rack with a progress meter that fills while something builds or installs
  const K = RACK;
  at('metalBox', K.x, K.y, K.h / 2, S(0.82, K.h, 0.46), '#2a3446');
  at('box', K.x, K.y + 0.232, K.h * 0.55, S(0.62, K.h * 0.8, 0.01), '#0d1320');
  r.rackBars = [0, 1, 2, 3, 4, 5].map(k => at('glowBox', K.x, K.y + 0.24, 0.42 + k * 0.17, S(0.5, 0.1, 0.02), '#1e293b'));
  r.rackLed = at('orb', K.x + 0.28, K.y + 0.24, K.h - 0.1, S(0.035, 0.035, 0.035), '#334155');

  // cabinet drawers that slide out while someone rifles through them (fronts proud of the cabinets' own)
  r.drawers = [5.2, 6.08].map(y => at('box', 0.72, y, 0.62, S(0.56, 0.22, 0.7), '#d9d2c4'));

  // document press
  const P = PRESS, len = P.y1 - P.y0;
  at('box', P.x, (P.y0 + P.y1) / 2, P.top / 2, S(0.85, P.top, len), '#3a475f');
  at('glowBox', P.x + 0.43, (P.y0 + P.y1) / 2 + 0.2, 0.12, S(0.01, 0.025, len - 0.6), mix(look.accent, '#000000', 0.35));
  at('metalBox', P.x, P.fab, P.top + 0.25, S(0.8, 0.5, 0.55), '#7d8aa3');
  at('box', P.x, P.fab - 0.02, P.top + 0.53, S(0.6, 0.06, 0.4), '#3a475f');
  r.slit = at('slit', P.x, P.fab + 0.2755, P.top + 0.3, S(0.6, 0.07, 1), '#04070d');
  r.slitAt = new THREE.Vector3(ox + P.x, P.top + 0.3, oy + P.fab + 0.3);

  // project sign on top of the back wall
  r.sign = canvasTex(1024, 256);
  r.signKey = '';
  const signMat = new THREE.MeshBasicMaterial({ map: r.sign.tex, transparent: true, toneMapped: false });
  const sign = new THREE.Mesh(G.plane, signMat);
  sign.scale.set(6, 1.5, 1); sign.position.set(ox + 3.2, WALL_H + 0.8, oy + 0.02);
  g.add(sign);
  disposables.push(r.sign.tex, signMat);
  drawSign(r);
}

// ------------------------------------------------------------------ documents in 3D
// Every job with documents gets a tray on its room's press, tinted by host; its documents stack in it,
// oldest at the bottom. Kind sets the shape: reports are white paper, files are dark glowing tablets,
// outbox items are amber envelopes with a seal. All sheets across the deck share three instanced meshes.
const DOC_MAX = 6;
const SHEET = {
  report: { w: 0.46, h: 0.018, d: 0.34, mat: new THREE.MeshStandardMaterial({ color: '#eef1f6', roughness: 0.7 }) },
  file:   { w: 0.4,  h: 0.04,  d: 0.3,  mat: new THREE.MeshStandardMaterial({ color: '#12304a', emissive: '#7dd3fc', emissiveIntensity: 0.35, roughness: 0.3, metalness: 0.3 }) },
  outbox: { w: 0.46, h: 0.05,  d: 0.3,  mat: new THREE.MeshStandardMaterial({ color: '#e2b35b', roughness: 0.6 }) },
};
const FX = { print: 0.85, fly: 1.65, settle: 2.9 };  // produced-animation timeline (s)
const docGroup = new THREE.Group();
scene.add(docGroup);
let docSig = null;
const docMeshes = {};        // kind → InstancedMesh
const docSlots = {};         // kind → [{ e, doc, key, m }] in instance order
const docByKey = new Map();  // doc key → { kind, i, top: Vector3 (landing point), m }
let trayMesh = null, stripeMesh = null;
const badges = [];

function buildDocs() {
  const holders = [];
  for (const r of rooms) for (const e of r.ents) if (e.job.documents && e.job.documents.length) holders.push(e);
  const sig = holders.map(e => e.key + ':' + e.room + ':' + e.job.documents.map(d => d.id).join(',')).join('|');
  if (sig === docSig) return;
  docSig = sig;
  for (const k of Object.keys(docMeshes)) { docGroup.remove(docMeshes[k]); docMeshes[k].dispose(); delete docMeshes[k]; }
  for (const m of [trayMesh, stripeMesh]) if (m) { docGroup.remove(m); m.dispose(); }
  for (const b of badges.splice(0)) { docGroup.remove(b); b.material.map.dispose(); b.material.dispose(); }
  docByKey.clear();
  const slots = { report: [], file: [], outbox: [] }, trays = [], stripes = [];
  for (const r of rooms) {
    const mine = r.ents.filter(e => e.job.documents && e.job.documents.length);
    if (!mine.length) continue;
    const pitch = Math.min(0.62, (PRESS.y1 - 0.05 - PRESS.trays) / mine.length);
    mine.forEach((e, i) => {
      const x = r.ox + PRESS.x, z = r.oy + PRESS.trays + pitch * (i + 0.5);
      trays.push({ m: new THREE.Matrix4().compose(_v.set(x, PRESS.top + 0.025, z), _q.identity(), _sc.set(0.74, 0.05, pitch - 0.07)), c: mix(e.look.color, '#0b111d', 0.45) });
      const docs = docsOf(e.job), shown = docs.slice(-DOC_MAX), hidden = docs.length - shown.length;
      let y = PRESS.top + 0.05;
      shown.forEach(doc => {
        const kind = kindOf(doc), K = SHEET[kind], h = hash(doc.id), key = docKey(e, doc);
        const d = Math.min(K.d, pitch - 0.14);
        const pos = new THREE.Vector3(x + ((h % 7) - 3) * 0.012, y + K.h / 2, z + (((h >>> 3) % 7) - 3) * 0.008);
        const rot = new THREE.Quaternion().setFromAxisAngle(THREE.Object3D.DEFAULT_UP, (((h >>> 6) % 9) - 4) * 0.02);
        const m = new THREE.Matrix4().compose(pos, rot, new THREE.Vector3(K.w, K.h, d));
        const slot = { e, doc, key, m, kind, i: slots[kind].length };
        slots[kind].push(slot);
        docByKey.set(key, { ...slot, top: pos.clone().setY(pos.y + K.h / 2), d });
        // host stripe along the back edge; outbox envelopes also get a seal
        stripes.push({ m: new THREE.Matrix4().compose(_v.set(pos.x, pos.y + K.h / 2 + 0.004, pos.z - d / 2 + 0.035), rot, _sc.set(K.w, 0.008, 0.05)), c: e.look.color, key });
        if (kind === 'outbox') stripes.push({ m: new THREE.Matrix4().compose(_v.set(pos.x, pos.y + K.h / 2 + 0.004, pos.z + 0.03), rot, _sc.set(0.09, 0.008, 0.09)), c: '#8a5a17', key });
        y += K.h + 0.012;
      });
      if (hidden > 0) badges.push(makeBadge('+' + hidden, e.look.color, x + 0.3, y + 0.12, z));
    });
  }
  for (const kind of Object.keys(slots)) {
    const list = slots[kind];
    docSlots[kind] = list;
    if (!list.length) continue;
    const im = new THREE.InstancedMesh(G.box, SHEET[kind].mat, list.length);
    list.forEach((s, i) => im.setMatrixAt(i, s.m));
    im.castShadow = true; im.receiveShadow = true;
    im.userData.kind = kind;
    im.computeBoundingSphere();
    docMeshes[kind] = im;
    docGroup.add(im);
  }
  const inst = (list, mat, shadow) => {
    if (!list.length) return null;
    const im = new THREE.InstancedMesh(G.box, mat, list.length);
    list.forEach((it, i) => { im.setMatrixAt(i, it.m); im.setColorAt(i, _col.set(it.c)); });
    im.receiveShadow = true; im.castShadow = shadow;
    im.computeBoundingSphere();
    docGroup.add(im);
    return im;
  };
  trayMesh = inst(trays, TRAY_MAT, true);
  stripeMesh = inst(stripes, STRIPE_MAT, false);
  stripeKeys = stripes.map(s => s.key);
  stripeBase = stripes.map(s => s.m);
  for (const b of badges) docGroup.add(b);
  hoverShown = null;
  for (const key of docFx.keys()) showDoc(key, false);   // still being printed: hidden until it lands
}
const TRAY_MAT = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5, metalness: 0.3 });
const STRIPE_MAT = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
let stripeKeys = [], stripeBase = [];
function makeBadge(text, color, x, y, z) {
  const { g, c, tex } = canvasTex(96, 48);
  g.fillStyle = 'rgba(6,10,20,.94)'; rr(g, 3, 3, 90, 42, 20); g.fill();
  g.strokeStyle = color; g.lineWidth = 3; g.stroke();
  g.fillStyle = '#eef4ff'; g.font = '700 24px JetBrains Mono, monospace'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(text, 48, 25);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, toneMapped: false, depthTest: false }));
  s.scale.set(0.36, 0.18, 1); s.position.set(x, y, z);
  return s;
}
// show, hide or lift one sheet (and its stripes) by rewriting its instance matrices
function showDoc(key, visible, lift = 0) {
  const d = docByKey.get(key);
  if (!d || !docMeshes[d.kind]) return;
  const place = m => {
    if (!visible) return _m4.makeScale(0, 0, 0);
    _m4.copy(m); if (lift) { _m4b.makeTranslation(0, lift, 0); _m4.premultiply(_m4b); }
    return _m4;
  };
  docMeshes[d.kind].setMatrixAt(d.i, place(d.m));
  docMeshes[d.kind].instanceMatrix.needsUpdate = true;
  if (stripeMesh) stripeKeys.forEach((k, i) => { if (k === key) stripeMesh.setMatrixAt(i, place(stripeBase[i])); });
  if (stripeMesh) stripeMesh.instanceMatrix.needsUpdate = true;
}
let hoverShown = null;
function liftHovered() {
  const want = hoverDoc && hoverDoc.key;
  if (want === hoverShown) return;
  if (hoverShown && !docFx.has(hoverShown)) showDoc(hoverShown, true);
  if (want && !docFx.has(want)) showDoc(want, true, 0.06);
  hoverShown = want;
}

// the printing android's beam, the sheet sliding out of the slit, its flight to the tray and a landing glow
const flyers = new Map();   // doc key → { mesh, glow }
function stepDocFx(t) {
  for (const [key, fx] of docFx) {
    const e = ents.get(fx.entKey), d = docByKey.get(key), r = e && roomByName.get(e.room);
    const ph = t - fx.start;
    if (!e || !d || !r) { dropFlyer(key); docFx.delete(key); if (d) showDoc(key, true); continue; }
    let f = flyers.get(key);
    if (ph >= 0 && ph < FX.settle && !f) {
      const mesh = new THREE.Mesh(G.box, SHEET[d.kind].mat);
      const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: M.failGlow.map, color: e.look.color, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
      glow.visible = false;
      docGroup.add(mesh, glow);
      f = { mesh, glow };
      flyers.set(key, f);
    }
    if (!f) continue;
    const K = SHEET[d.kind], from = r.slitAt;
    if (ph < FX.print) {
      const k = ph / FX.print;
      f.mesh.visible = true;
      f.mesh.position.set(from.x, from.y, from.z + k * 0.2 - 0.1);
      f.mesh.scale.set(K.w * 0.9, K.h, 0.05 + k * K.d * 0.8);
      f.mesh.rotation.set(0, 0, 0);
      placer.setColor(r.slit, (REDUCED || Math.sin(t * 18) > -0.3) ? e.look.color : '#04070d');
      e.bot.head.getWorldPosition(_w); _w.y -= 0.3 * BK;
      dashedLine(_w, from, e.look.color, t, 0);
    } else if (ph < FX.fly) {
      placer.setColor(r.slit, '#04070d');
      const k = (ph - FX.print) / (FX.fly - FX.print), ease = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const x0 = from.x, y0 = from.y, z0 = from.z + 0.1;
      f.mesh.position.set(x0 + (d.top.x - x0) * ease, y0 + (d.top.y - y0) * ease + Math.sin(PI * k) * 0.55, z0 + (d.top.z - z0) * ease);
      f.mesh.rotation.set(Math.sin(k * PI * 4) * 0.35 * (1 - k), k * 0.6, 0);
      f.mesh.scale.set(K.w, K.h, d.d);
    } else {
      if (f.mesh.visible) { f.mesh.visible = false; showDoc(key, true); f.glow.visible = true; f.glow.position.copy(d.top); }
      const s = (ph - FX.fly) / (FX.settle - FX.fly);
      f.glow.scale.setScalar(0.6 + s * 0.9);
      f.glow.material.opacity = 0.9 * (1 - s);
    }
    if (ph >= FX.settle) { dropFlyer(key); docFx.delete(key); }
  }
}
function dropFlyer(key) {
  const f = flyers.get(key);
  if (!f) return;
  docGroup.remove(f.mesh, f.glow);
  f.glow.material.dispose();
  flyers.delete(key);
}

// ------------------------------------------------------------------ link lines: delegation arcs and print beams
// One line-segment buffer for the whole deck, refilled every frame; dashes march by skipping alternate segments.
const LN = 600;
const linePos = new Float32Array(LN * 6), lineCol = new Float32Array(LN * 6);
const lineGeo = new THREE.BufferGeometry();
lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3).setUsage(THREE.DynamicDrawUsage));
lineGeo.setAttribute('color', new THREE.BufferAttribute(lineCol, 3).setUsage(THREE.DynamicDrawUsage));
const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, depthWrite: false, toneMapped: false }));
lines.frustumCulled = false;
scene.add(lines);
let nSeg = 0;
const _la = new THREE.Vector3(), _lb = new THREE.Vector3();
function segment(a, b) {
  if (nSeg >= LN) return;
  const o = nSeg * 6;
  linePos[o] = a.x; linePos[o + 1] = a.y; linePos[o + 2] = a.z; linePos[o + 3] = b.x; linePos[o + 4] = b.y; linePos[o + 5] = b.z;
  lineCol[o] = lineCol[o + 3] = _col.r; lineCol[o + 1] = lineCol[o + 4] = _col.g; lineCol[o + 2] = lineCol[o + 5] = _col.b;
  nSeg++;
}
// a dashed arc from a to b, lifted by `arc` in the middle
function dashedLine(a, b, color, t, arc) {
  _col.set(color);
  const n = 14, phase = REDUCED ? 0 : Math.floor(t * 8);
  for (let j = 0; j < n; j++) {
    if ((j + phase) % 2) continue;
    const u0 = j / n, u1 = (j + 1) / n;
    _la.lerpVectors(a, b, u0); _la.y += Math.sin(PI * u0) * arc;
    _lb.lerpVectors(a, b, u1); _lb.y += Math.sin(PI * u1) * arc;
    segment(_la, _lb);
  }
}

// ------------------------------------------------------------------ the android
// One RobotExpressive per job: Main material in the host colour, a host accessory, and the agent's face light
// (Claude: a coral visor band; Codex: twin cyan eyes). Accessories hang off bones so they follow the animation.
function buildRobot(look, agent) {
  const root = new THREE.Group();
  const model = SkeletonUtils.clone(ROBOT.scene);
  root.add(model);
  const main = ROBOT.main.clone();
  main.color.set(look.color);
  const bones = {};
  const agentColor = AGENT_COLOR[agent] || '#cbd5e1';
  // Codex: the robot's own eyes light up cyan
  const eyes = agent === 'codex' ? new THREE.MeshStandardMaterial({ color: agentColor, emissive: agentColor, emissiveIntensity: 1.4, roughness: 0.3 }) : null;
  model.traverse(o => {
    // androids get a soft contact shadow instead of casting into the shadow map
    if (o.isMesh) {
      o.frustumCulled = false;
      const swap = m => m === ROBOT.main ? main : (eyes && m.name === 'Black') ? eyes : m;
      o.material = Array.isArray(o.material) ? o.material.map(swap) : swap(o.material);
    }
    if (o.isBone) bones[o.name.replace(/_\d+$/, '')] = o;
  });
  model.updateMatrixWorld(true);
  const hb = ROBOT.headBox, tb = ROBOT.torsoBox;
  const hc = hb.getCenter(new THREE.Vector3()), hs = hb.getSize(new THREE.Vector3());
  const tc = tb.getCenter(new THREE.Vector3()), ts = tb.getSize(new THREE.Vector3());
  const face = new THREE.MeshBasicMaterial({ color: agentColor, toneMapped: false });
  const hostMat = new THREE.MeshStandardMaterial({ color: look.color, roughness: 0.4, metalness: 0.3 });
  const darkMat = M.dark;
  const add = (bone, geo, mat, pos, scale, rot) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.copy(pos); if (scale) m.scale.copy(scale); if (rot) m.rotation.set(rot[0], rot[1], rot[2]);
    m.updateMatrixWorld();
    bone.attach(m);
    return m;
  };
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  const head = bones.Head, torso = bones.Torso || bones.Body;
  // Claude: a coral visor band across the eyes
  if (agent !== 'codex') add(head, G.box, face, V(hc.x, hc.y - hs.y * 0.06, hb.max.z - hs.z * 0.1), V(hs.x * 0.74, hs.y * 0.2, hs.z * 0.06));
  add(head, G.sphere, face, V(hc.x, hc.y + hs.y * 0.05, hb.min.z + hs.z * 0.03), V(hs.x * 0.05, hs.x * 0.05, hs.x * 0.025));   // seen from behind
  let kitTop = 0;   // how far the accessory rises above the head, so tags clear it
  switch (look.acc) {
    case 'antenna':
      add(head, G.box, darkMat, V(hc.x + hs.x * 0.25, hb.max.y + 0.12 * BK, hc.z), V(0.03 * BK, 0.26 * BK, 0.03 * BK));
      add(head, G.sphere, hostMat, V(hc.x + hs.x * 0.25, hb.max.y + 0.27 * BK, hc.z), V(0.06 * BK, 0.06 * BK, 0.06 * BK));
      kitTop = 0.33 * BK;
      break;
    case 'halo':
      add(head, new THREE.TorusGeometry(hs.x * 0.36, 0.022 * BK, 8, 32), new THREE.MeshBasicMaterial({ color: look.color, toneMapped: false }), V(hc.x, hb.max.y + 0.1 * BK, hc.z), null, [HALF, 0, 0]);
      kitTop = 0.13 * BK;
      break;
    case 'crest':
      add(head, G.box, hostMat, V(hc.x, hb.max.y - 0.06 * BK, hc.z - hs.z * 0.05), V(0.06 * BK, 0.16 * BK, hs.z * 0.7));
      kitTop = 0.02 * BK;
      break;
    default:  // backpack
      add(torso, G.box, hostMat, V(tc.x, tc.y + ts.y * 0.1, tb.min.z - 0.07 * BK), V(ts.x * 0.62, ts.y * 0.7, 0.16 * BK));
      add(torso, G.box, darkMat, V(tc.x, tc.y + ts.y * 0.1, tb.min.z - 0.155 * BK), V(ts.x * 0.4, ts.y * 0.1, 0.02 * BK));
  }
  // tags hang from the head bone, lifted to the top of the head (and kit) plus a margin
  const tagLift = hb.max.y - head.getWorldPosition(new THREE.Vector3()).y + kitTop + 0.12 * BK;
  const mixer = new THREE.AnimationMixer(model);
  const arms = [bones.LowerArmL, bones.LowerArmR].filter(Boolean);   // (three strips the dots from "LowerArm.L")
  return { root, model, mixer, main, face, eyes, hostMat, head, arms, tagLift, actions: {} };
}
const ONCE = new Set(['Death', 'Sitting', 'Standing', 'ThumbsUp', 'Wave']);
function action(bot, name) {
  let a = bot.actions[name];
  if (!a) {
    a = bot.actions[name] = bot.mixer.clipAction(ROBOT.clips[name]);
    if (ONCE.has(name) && name !== 'Wave') { a.setLoop(THREE.LoopOnce, 1); a.clampWhenFinished = true; }
  }
  return a;
}
function playClip(e, name, fade = 0.3) {
  const bot = e.bot;
  if (bot.clip === name) return;
  const next = action(bot, name), prev = bot.clip ? action(bot, bot.clip) : null;
  next.reset().setEffectiveTimeScale(name === 'Walking' ? 1.25 : 1).setEffectiveWeight(1);
  if (REDUCED) {
    next.play();
    if (next.loop === THREE.LoopOnce) next.time = next.getClip().duration - 0.001;
    if (prev) prev.stop();
    bot.mixer.update(0);
  } else {
    next.fadeIn(fade).play();
    if (prev) prev.fadeOut(fade);
  }
  bot.clip = name;
}

let rooms = [];
let plates = [];               // deck modules the rooms sit on
const roomByName = new Map();
let layoutKey = '';
let layoutNames = [];

function layoutRooms(names) {
  layoutNames = names;
  if (!names.length) names = ['lobby'];
  const column = vw < 760; // phones: rooms stack straight down the screen as separate modules
  const key = names.join('|') + (column ? '#column' : '#grid');
  if (key === layoutKey) return;
  layoutKey = key;
  const cols = Math.ceil(Math.sqrt(names.length));
  const taken = new Set(), themes = new Set();
  rooms = names.map((name, i) => {
    const forced = DEBUG && QS.get('themes') ? QS.get('themes').split(',')[i] : null;   // ?debug&themes=library,studio,…: for screenshots
    const look = projectLook(name, taken), theme = THEMES[forced] ? forced : themeFor(name, themes);
    Object.assign(look, THEMES[theme].pal(look.hue));
    return {
      name, label: name, theme,
      ox: column ? i * (RD + GAP) : (i % cols) * (RW + GAP),
      oy: column ? i * (RD + GAP) : Math.floor(i / cols) * (RD + GAP),
      look, ents: [], busy: {},
    };
  });
  plates = column
    ? rooms.map(r => ({ x0: r.ox - 1.8, y0: r.oy - 1.8, x1: r.ox + RW + 1.8, y1: r.oy + RD + 1.8 }))
    : [deckBounds()];
  roomByName.clear();
  for (const r of rooms) roomByName.set(r.name, r);
  buildDeck();
  if (!cam.userMoved) fit(true);
}

function makeTag(e) {
  const el = document.createElement('div');
  el.className = e.tagBase = isSession(e) ? 'tag sess' : 'tag';
  el.innerHTML = '<div class="bubble"></div><div class="stack"></div>';
  el.style.setProperty('--hc', e.look.color);
  el.addEventListener('click', ev => { ev.stopPropagation(); select(e.key); });
  tagsEl.appendChild(el);
  return el;
}

function createEnt(key, hostName, job, kind = 'job') {
  const look = hostLook(hostName);
  const e = {
    key, kind, host: hostName, job, look, room: job.project,
    local: { x: 5.5, y: RD + 0.7 }, path: [], target: null, facing: PI, walking: false,
    act: 'init', actSince: 0, anchor: null, partner: null, fresh: true, lift: 0, stage: 0, arrivedAt: null, dropped: false, nod: null,
    holdUntil: 0, holdClip: null, lastStatus: job.status, tone: '',
    nudge: { x: 0, y: 0 }, spotProp: null,
    placeNow: !everLoaded,   // jobs already running when the page opens start at their stations; later ones walk in
    sx: -999, sy: -999, sig: '',
  };
  e.bot = buildRobot(look, job.agent);
  e.bot.root.userData.ent = e;
  e.ring = new THREE.Mesh(G.ring, new THREE.MeshBasicMaterial({ color: look.color, transparent: true, opacity: 0.8, depthWrite: false }));
  e.ring.visible = false;
  e.glow = new THREE.Mesh(G.disc, M.doneDisc); e.glow.visible = false; e.glow.position.y = 0.01;
  e.fail = new THREE.Sprite(M.failGlow); e.fail.scale.setScalar(1.3); e.fail.position.y = 0.45; e.fail.visible = false;
  e.proxy = new THREE.Mesh(G.proxy, M.hidden); e.proxy.userData.ent = e;
  const blob = new THREE.Mesh(G.disc, M.blob); blob.position.y = 0.008; blob.scale.setScalar(0.9 * BK);
  e.bot.root.add(blob, e.ring, e.glow, e.fail, e.proxy);
  if (isSession(e)) {   // a live session stands in a wide pink halo; it breathes while the agent works
    e.halo = new THREE.Mesh(G.ring, new THREE.MeshBasicMaterial({ color: '#f472b6', transparent: true, opacity: 0.5, depthWrite: false }));
    e.halo.scale.setScalar(1.45 * BK); e.halo.position.y = 0.012;
    e.bot.root.add(e.halo);
  }
  botGroup.add(e.bot.root);
  playClip(e, 'Idle', 0);
  e.el = makeTag(e);
  return e;
}
function dropEnt(e) {
  e.el.remove();
  botGroup.remove(e.bot.root);
  e.bot.mixer.stopAllAction();
  e.bot.main.dispose(); e.bot.face.dispose(); e.bot.hostMat.dispose(); e.bot.eyes?.dispose(); e.ring.material.dispose();
  e.halo?.material.dispose();
}

// Dismissed agents are hidden in this browser only (the deck stays view-only). Each is remembered with
// the updated_at it had when dismissed, so any new activity brings it back.
const DISMISSED_KEY = 'fleet.dismissed';
let dismissed = {}, hiddenCount = 0, lastDoc = null;
try { dismissed = JSON.parse(store('localStorage', DISMISSED_KEY) || '{}') || {}; } catch (err) { dismissed = {}; }
function saveDismissed() { store('localStorage', DISMISSED_KEY, JSON.stringify(dismissed)); }
function visibleHosts(doc) {
  hiddenCount = 0;
  let changed = false;
  const present = new Set();
  // jobs can be dismissed unless running; live sessions only while idle
  const keep = (h, item, busy) => {
    const key = h.name + ':' + item.id;
    present.add(key);
    if (!(key in dismissed)) return true;
    if (dismissed[key] === (item.updated_at ?? null) && !busy) { hiddenCount++; return false; }
    delete dismissed[key]; changed = true;
    return true;
  };
  const out = (doc.hosts || []).map(h => ({ ...h,
    jobs: (h.jobs || []).filter(j => keep(h, j, j.status === 'running')),
    sessions: (h.sessions || []).filter(s => keep(h, s, s.status !== 'idle')) }));
  retireFinished(out);
  // forget dismissals for jobs a reachable host no longer reports
  const okHosts = new Set((doc.hosts || []).filter(h => h.ok !== false).map(h => h.name));
  for (const key of Object.keys(dismissed)) {
    if (okHosts.has(key.slice(0, key.indexOf(':'))) && !present.has(key)) { delete dismissed[key]; changed = true; }
  }
  if (changed) saveDismissed();
  return out;
}
// Finished jobs leave the deck on their own: each room keeps its few most recent for a while, the rest are counted
// in a header chip that shows them again. Failed and stalled jobs stay until dismissed — they need you.
const FINISHED_STATUSES = new Set(['done', 'cancelled']);
const FINISHED_LINGER_SECONDS = 10 * 60;
const FINISHED_PER_ROOM = 3;
const RETIRE_CHECK_MS = 30000;
let retiredCount = 0, showFinished = false;
function retireFinished(hostList) {
  retiredCount = 0;
  if (showFinished) return;
  const finished = [];
  for (const h of hostList) for (const j of h.jobs) if (FINISHED_STATUSES.has(j.status)) finished.push(j);
  finished.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  const nowSeconds = Date.now() / 1000, keptPerRoom = new Map(), retired = new Set();
  for (const j of finished) {
    const kept = keptPerRoom.get(j.project) || 0;
    if (nowSeconds - (j.updated_at || 0) > FINISHED_LINGER_SECONDS || kept >= FINISHED_PER_ROOM) retired.add(j);
    else keptPerRoom.set(j.project, kept + 1);
  }
  for (const h of hostList) h.jobs = h.jobs.filter(j => !retired.has(j));
  retiredCount = retired.size;
}
function toggleFinished() {
  showFinished = !showFinished;
  if (lastDoc) applyState(lastDoc);
}
// jobs age out between state updates too
setInterval(() => { if (lastDoc) applyState(lastDoc); }, RETIRE_CHECK_MS);
function dismiss(key) {
  const e = ents.get(key);
  if (!e || !lastDoc) return;
  dismissed[key] = e.job.updated_at ?? null;
  saveDismissed();
  if (selectedKey === key) closePanel();
  applyState(lastDoc);
}
function restoreDismissed() {
  dismissed = {};
  saveDismissed();
  if (lastDoc) applyState(lastDoc);
}

function applyState(doc) {
  lastDoc = doc;
  setHosts(visibleHosts(doc));
  const projects = new Set();
  for (const h of hosts) for (const j of h.jobs || []) projects.add(j.project);
  // hosts on an older fleetd send no sessions; a session whose cwd fleetd could not tell has no room and is not drawn
  for (const h of hosts) for (const s of h.sessions || []) if (s.project) projects.add(s.project);
  layoutRooms([...projects].sort());
  for (const room of rooms) room.label = doc.project_labels?.[room.name] || room.name;
  const seen = new Set();
  const now = performance.now() / 1000;
  for (const h of hosts) {
    for (const j of h.jobs || []) {
      const key = h.name + ':' + j.id;
      seen.add(key);
      let e = ents.get(key);
      const known = !!e;
      if (!e) { e = createEnt(key, h.name, j); ents.set(key, e); }
      // a job that just finished gives a thumbs-up before heading for the sofa
      if (known && j.status === 'done' && e.lastStatus !== 'done' && !REDUCED) { e.holdClip = 'ThumbsUp'; e.holdUntil = now + ROBOT.clips.ThumbsUp.duration; }
      e.lastStatus = j.status;
      e.job = j; e.host = h.name;
      noteDocs(e, !known || !everLoaded);
      if (e.room !== j.project) { e.room = j.project; e.local = { x: 5.5, y: RD + 0.7 }; e.path = []; e.target = null; e.fresh = true; }
    }
    for (const s of h.sessions || []) {
      if (!s.project) continue;
      const key = h.name + ':' + s.id;
      seen.add(key);
      let e = ents.get(key);
      if (!e) { e = createEnt(key, h.name, s, 'session'); ents.set(key, e); }
      e.lastStatus = s.status;
      e.job = s; e.host = h.name;
      if (e.room !== s.project) { e.room = s.project; e.local = { x: 5.5, y: RD + 0.7 }; e.path = []; e.target = null; e.fresh = true; }
    }
  }
  for (const [k, e] of ents) if (!seen.has(k)) { dropEnt(e); ents.delete(k); if (selectedKey === k) closePanel(); }
  setEverLoaded(true);
  collectEvents();
  assignTargets();
  buildDocs();
  for (const e of ents.values()) updateTag(e);
  for (const r of rooms) drawSign(r);
  renderLegend(); renderStats(); renderLive(); renderFeed(); updateHint();
  if (selectedKey) renderPanel();
  openLinkedDoc();
}

// ?open=<host>:<job>:<docId> opens that document in the reader once it shows up on the deck (a link to a document)
let linkedDoc = QS.get('open');
function openLinkedDoc() {
  if (!linkedDoc) return;
  const [host, job, ...rest] = linkedDoc.split(':'), id = rest.join(':');
  const e = ents.get(host + ':' + job), doc = e && (e.job.documents || []).find(d => d.id === id);
  if (!doc) return;
  linkedDoc = null;
  openReader(e, doc);
}

function assignTargets() {
  const now = performance.now() / 1000;
  for (const r of rooms) r.ents = [];
  for (const e of ents.values()) { const r = roomByName.get(e.room); if (r) r.ents.push(e); }
  for (const r of rooms) {
    r.ents.sort((a, b) => a.key < b.key ? -1 : 1);
    for (const e of r.ents) {
      noteEvents(e, now);
      let want = activityFor(e.job);
      if (want === null) want = e.act === 'init' ? 'type' : e.act;
      if (want !== e.act) {
        // a change of station waits out a minimum dwell once there, so bursts of events don't send androids back and forth
        const settled = ['dock', 'failed', 'stalled', 'idle', 'await'].includes(want) || e.act === 'init' || stationOf(want) === stationOf(e.act, e.stage);
        const dwelt = now - e.actSince > DWELL && (e.slow || (e.arrivedAt != null && now - e.arrivedAt > 1.2));
        if (settled || dwelt) { e.act = want; e.actSince = now; e.anchor = null; e.stage = 0; e.dropped = false; }
      }
    }
    allocate(r);
  }
}
const DWELL = 3.5;   // seconds an android stays on an activity before walking off to another station

// React to what happened since the last poll: a test run followed by anything but an error passed (a nod), an error gets
// a head shake. Seated androids nod or shake just their head; standing ones play the full clip.
function noteEvents(e, now) {
  const evs = (e.job.events || []).filter(ev => ev.kind === 'tool' || ev.kind === 'text' || ev.kind === 'error');
  const last = evs.length ? evs[evs.length - 1] : null;
  if (e.evTs === undefined || !isActive(e.job.status)) { e.evTs = last ? last.ts : 0; e.testing = activityOf(last) === 'test'; return; }
  for (const ev of evs) {
    if (ev.ts <= e.evTs) continue;
    if (ev.kind === 'error') { react(e, false, now); e.testing = false; }
    else { if (e.testing) react(e, true, now); e.testing = activityOf(ev) === 'test'; }
  }
  if (last) e.evTs = Math.max(e.evTs, last.ts);
}
function react(e, yes, now) {
  if (REDUCED) return;
  if (e.target && e.target.prop === 'terminal') {   // the screen shows the verdict too
    const s = roomByName.get(e.room)?.screens[e.target.propIdx];
    if (s && (e.act === 'test' || !yes)) s.verdict = { ok: yes, until: now + 3 };
  }
  if (e.bot.clip === 'Sitting' && !e.walking) { e.nod = { yes, until: now + 1.5 }; return; }
  const clip = yes ? 'Yes' : 'No';
  e.holdClip = clip; e.holdUntil = now + ROBOT.clips[clip].duration;
}

// Give every android in a room its own spot. Androids keep the spot they already hold while their activity is
// unchanged; the rest take the first free spot at their station, then the nearest free overflow spot.
// Delegates stand beside their partner, on free floor, facing them.
function allocate(r) {
  const taken = [];
  const free = (x, y, self) => taken.every(t => t.e === self || apart(t.x, t.y, x, y));
  const claim = (e, spot) => { taken.push({ x: spot.x, y: spot.y, e }); e.spotProp = spot.prop; setTarget(e, spot); };
  const propOf = e => stationOf(e.act, e.stage);
  const rest = [];
  // 1. keep what's already held
  for (const e of r.ents) {
    const p = propOf(e), t = e.target;
    if (p === 'partner') continue;
    if (t && e.spotProp === p && (p !== 'stay' || e.anchor) && free(t.x, t.y, e)) { claim(e, t); continue; }
    rest.push(e);
  }
  const byDistance = (list, near) => list.sort((a, b) => Math.hypot(a[0] - near.x, a[1] - near.y) - Math.hypot(b[0] - near.x, b[1] - near.y));
  const overflow = (e, near, p) => {
    // the listed free-floor spots first, then any clear floor tile; only a packed room doubles up
    const s = byDistance(OVERFLOW.slice(), near).find(o => free(o[0], o[1], e))
      || byDistance(FLOOR.slice(), near).find(o => free(o[0], o[1], e))
      || OVERFLOW[0];
    return { x: s[0], y: s[1], face: s[2] ?? 0, prop: p };
  };
  // 2. everyone else, station by station
  for (const e of rest) {
    const p = propOf(e);
    let spot;
    if (p === 'stay') {
      // stalled or failed: stop where they are if that's clear, keeping the seat they were in
      const here = { x: e.local.x, y: e.local.y };
      spot = !e.fresh && e.target && free(here.x, here.y, e) && (e.target.sit != null || !blocked(here.x, here.y))
        ? { ...here, face: e.facing, sit: e.target.sit, prop: 'stay' }
        : overflow(e, here, 'stay');
      if (e.act === 'await' && spot.sit == null) spot.face = FACE_VIEWER;   // waiting on the human: look out of the screen
      e.anchor = spot;
    } else {
      const s = SPOTS[p].find(c => free(c[0], c[1], e));
      spot = s ? { x: s[0], y: s[1], face: s[2], prop: p, propIdx: s[3] || 0, sit: s[4] } : overflow(e, { x: SPOTS[p][0][0], y: SPOTS[p][0][1] }, p);
    }
    claim(e, spot);
  }
  // 3. delegates, beside whoever is working
  for (const e of r.ents) {
    if (propOf(e) !== 'partner') continue;
    const partner = r.ents.find(o => o !== e && !['partner', 'dock'].includes(propOf(o)) && o.target) || null;
    const same = e.spotProp === 'partner' && e.partner === partner && e.target && free(e.target.x, e.target.y, e);
    e.partner = partner;
    if (same) { claim(e, e.target); continue; }
    const t = partner ? partner.target : { x: 7.95, y: 1.35 };
    let spot = null;
    // beside the partner as seen on screen: offsets along (1,-1) move across the view
    for (const [dx, dy] of [[1.1, -1.1], [-1.1, 1.1], [1.9, 0.3], [0.3, 1.9], [1.4, -0.9], [-0.9, 1.4]]) {
      const x = t.x + dx, y = t.y + dy;
      if (!blocked(x, y) && free(x, y, e)) { spot = { x, y, face: Math.atan2(t.x - x, t.y - y), prop: 'partner' }; break; }
    }
    claim(e, spot || overflow(e, t, 'partner'));
  }
}

function setTarget(e, spot) {
  if (e.target && Math.abs(e.target.x - spot.x) < 0.01 && Math.abs(e.target.y - spot.y) < 0.01) { e.target = spot; return; }
  e.target = spot;
  e.arrivedAt = null; e.slow = false; e.away = false; e.paceAt = null;
  if (REDUCED || e.placeNow) { e.placeNow = false; e.local = { x: spot.x, y: spot.y }; e.path = []; e.facing = spot.face ?? e.facing; e.fresh = false; return; }
  e.path = route(e.local, spot);
}

// thinking androids pace and idle ones wander: a slow stroll a little way from their spot, a pause, and back
const PACE_SPEED = 0.4;   // of walking speed
function pace(e, now) {
  const reach = (ACTS[e.act] || {}).pace;
  if (!reach || REDUCED || !e.target || e.arrivedAt == null) return;
  if (e.paceAt == null) { e.paceAt = now + 2 + Math.random() * 2; return; }
  if (now < e.paceAt) return;
  let to = null;
  if (e.away) to = { x: e.target.x, y: e.target.y };
  else for (let k = 0; k < 8 && !to; k++) {
    const a = Math.random() * PI * 2, x = e.target.x + Math.sin(a) * reach, y = e.target.y + Math.cos(a) * reach;
    if (!blocked(x, y) && !blocked((x + e.target.x) / 2, (y + e.target.y) / 2)) to = { x, y };
  }
  if (!to) { e.paceAt = now + 3; return; }
  e.away = !e.away; e.slow = true; e.path = [to];
  e.paceAt = now + reach / (SPEED * PACE_SPEED) + 2.5 + Math.random() * 3;
}

// walk along the aisles so androids go around desks and sofas rather than through them
function route(a, b) {
  const aisle = y => y < 4.1 ? AISLES[0] : y < 7.1 ? AISLES[1] : AISLES[2];   // the bench desks start at 4.2
  const ya = aisle(a.y), yb = aisle(b.y);
  const pts = [{ x: a.x, y: ya }];
  if (ya !== yb) {
    const cx = CROSSINGS.reduce((best, c) => Math.abs(a.x - c) + Math.abs(b.x - c) < Math.abs(a.x - best) + Math.abs(b.x - best) ? c : best);
    pts.push({ x: cx, y: ya }, { x: cx, y: yb });
  }
  pts.push({ x: b.x, y: yb }, { x: b.x, y: b.y });
  const out = []; let prev = a;
  for (const p of pts) { if (Math.hypot(p.x - prev.x, p.y - prev.y) > 0.02) { out.push(p); prev = p; } }
  return out;
}

function stepMotion(dt, now) {
  for (const e of ents.values()) {
    if (!e.path.length) {
      if (e.target) e.facing = e.target.face ?? e.facing;
      e.walking = false;
      if (e.target) { e.fresh = false; if (e.arrivedAt == null) e.arrivedAt = now; }
      nextStage(e, now);
      pace(e, now);
      continue;
    }
    if (now < e.holdUntil) continue;
    // get up before walking off
    if (!REDUCED && (e.bot.clip === 'Sitting' || e.bot.clip === 'Death')) {
      e.holdClip = 'Standing'; e.holdUntil = now + ROBOT.clips.Standing.duration * 0.8;
      continue;
    }
    let remaining = SPEED * dt * (e.slow ? PACE_SPEED : 1);
    while (remaining > 0 && e.path.length) {
      const p = e.path[0], dx = p.x - e.local.x, dy = p.y - e.local.y, d = Math.hypot(dx, dy);
      if (d > 0.001) e.facing = Math.atan2(dx, dy);
      if (d <= remaining) { e.local.x = p.x; e.local.y = p.y; e.path.shift(); remaining -= d; }
      else { e.local.x += dx / d * remaining; e.local.y += dy / d * remaining; remaining = 0; }
    }
    e.walking = e.path.length > 0;
  }
  // walkers step around other androids: when two would overlap on screen, the walker is nudged sideways across the
  // view (along (1,-1)), easing back once clear
  const k = Math.min(1, dt * 4);
  for (const e of ents.values()) {
    let nx = 0, ny = 0;
    if (e.walking) for (const o of ents.values()) {
      if (o === e || o.room !== e.room) continue;
      const ax = e.local.x + e.nudge.x, ay = e.local.y + e.nudge.y, bx = o.local.x + o.nudge.x, by = o.local.y + o.nudge.y;
      const u = ((ax - ay) - (bx - by)) / Math.SQRT2, v = ((ax + ay) - (bx + by)) / Math.SQRT2;
      const q = (u / APART_ACROSS) ** 2 + (v / APART_ALONG) ** 2;
      if (q < 1) { const push = (1 - q) * 0.9 * (u >= 0 ? 1 : -1) / Math.SQRT2; nx += push; ny -= push; }
    }
    e.nudge.x += (clamp(nx, -0.8, 0.8) - e.nudge.x) * k;
    e.nudge.y += (clamp(ny, -0.8, 0.8) - e.nudge.y) * k;
  }
}

// activities with more than one station move on once the android has spent a moment at the first (a book off the shelf)
function nextStage(e, now) {
  const s = (ACTS[e.act] || {}).station;
  if (!Array.isArray(s) || e.stage >= s.length - 1 || e.arrivedAt == null || now - e.arrivedAt < 1.8 || !isActive(e.job.status)) return;
  e.stage++;
  const r = roomByName.get(e.room);
  if (r) allocate(r);
}

function clipFor(e, now) {
  if (now < e.holdUntil && e.holdClip) return e.holdClip;
  if (e.walking) return 'Walking';
  if (!e.target) return 'Idle';
  const st = e.job.status;
  if (st === 'failed') return 'Death';
  if (st === 'stalled') return 'Sitting';
  if (isActive(st) && e.act === 'delegate') return 'Wave';
  return e.target.sit != null ? 'Sitting' : 'Idle';
}

// ------------------------------------------------------------------ particles: smoke over failed androids, motes over finished ones
const PN = 320;
const part = {
  pos: new Float32Array(PN * 3), col: new Float32Array(PN * 4), vel: new Float32Array(PN * 3),
  age: new Float32Array(PN).fill(1), life: new Float32Array(PN).fill(1), kind: new Uint8Array(PN), next: 0,
};
const partGeo = new THREE.BufferGeometry();
partGeo.setAttribute('position', new THREE.BufferAttribute(part.pos, 3).setUsage(THREE.DynamicDrawUsage));
partGeo.setAttribute('color', new THREE.BufferAttribute(part.col, 4).setUsage(THREE.DynamicDrawUsage));
const partMat = new THREE.PointsMaterial({ size: 10, map: softDot('rgba(255,255,255,1)', 'rgba(255,255,255,0)'), vertexColors: true, transparent: true, depthWrite: false });
const points = new THREE.Points(partGeo, partMat);
points.frustumCulled = false;
scene.add(points);
function spawn(x, y, z, kind) {
  if (REDUCED) return;
  const i = part.next; part.next = (part.next + 1) % PN;
  part.pos[i * 3] = x; part.pos[i * 3 + 1] = y; part.pos[i * 3 + 2] = z;
  const smoke = kind === 0, steam = kind === 2;   // 0 smoke, 1 motes, 2 steam
  part.vel[i * 3] = (Math.random() - 0.5) * (smoke ? 0.25 : 0.1);
  part.vel[i * 3 + 1] = smoke ? 0.5 + Math.random() * 0.3 : steam ? 0.3 + Math.random() * 0.15 : 0.35 + Math.random() * 0.2;
  part.vel[i * 3 + 2] = (Math.random() - 0.5) * (smoke ? 0.25 : 0.1);
  part.age[i] = 0; part.life[i] = smoke ? 1.8 + Math.random() : steam ? 1.4 + Math.random() * 0.5 : 1.3; part.kind[i] = kind;
}
function stepParticles(dt) {
  for (let i = 0; i < PN; i++) {
    const c = i * 4;
    if (part.age[i] >= part.life[i]) { part.col[c + 3] = 0; continue; }
    part.age[i] += dt;
    const k = 1 - Math.min(1, part.age[i] / part.life[i]);
    part.pos[i * 3] += part.vel[i * 3] * dt; part.pos[i * 3 + 1] += part.vel[i * 3 + 1] * dt; part.pos[i * 3 + 2] += part.vel[i * 3 + 2] * dt;
    if (part.kind[i] === 0) { part.col[c] = 0.5; part.col[c + 1] = 0.52; part.col[c + 2] = 0.58; part.col[c + 3] = 0.35 * k; }
    else if (part.kind[i] === 2) { part.col[c] = 0.9; part.col[c + 1] = 0.93; part.col[c + 2] = 0.97; part.col[c + 3] = 0.3 * k; }
    else { part.col[c] = 0.29; part.col[c + 1] = 0.87; part.col[c + 2] = 0.5; part.col[c + 3] = 0.9 * k; }
  }
  partGeo.attributes.position.needsUpdate = true;
  partGeo.attributes.color.needsUpdate = true;
  partMat.size = 0.45 * cam.z;
}

// ------------------------------------------------------------------ per-frame update of androids and props

function tone(e, t) {
  // stalled androids dim; finished ones rest with their face light low
  const st = e.job.status, arrived = !e.walking && !!e.target;
  const key = st === 'stalled' ? 'dim' : ((st === 'done' || st === 'cancelled' || st === 'idle') && arrived ? 'rest' : '');
  if (key === e.tone) return;
  e.tone = key;
  e.bot.main.color.set(key === 'dim' ? mix(e.look.color, '#475163', 0.55) : e.look.color);
  const lit = AGENT_COLOR[e.job.agent] || '#cbd5e1', low = mix(lit, '#1b2333', key === 'rest' ? 0.35 : 0.6);
  e.bot.face.color.set(key ? low : lit);
  if (e.bot.eyes) { e.bot.eyes.color.set(key ? low : lit); e.bot.eyes.emissiveIntensity = key ? 0.2 : 1.4; }
}
// things androids carry: a parcel for the outbox, a book from the shelf, a printout to read (one small mesh, made on first use)
// [x, y, z, tilt] in the android's frame, standing and seated
const HELD = {
  box:   { mat: new THREE.MeshStandardMaterial({ color: '#b98b52', roughness: 0.9 }), size: [0.5, 0.38, 0.4], stand: [0, 1.05, 0.42, 0], sit: [0, 0.95, 0.45, 0] },
  book:  { mat: new THREE.MeshStandardMaterial({ color: '#b4463c', roughness: 0.7 }), size: [0.36, 0.08, 0.46], stand: [0, 1.3, 0.4, -1.0], sit: [0, 1.2, 0.5, -1.1] },
  sheet: { mat: new THREE.MeshBasicMaterial({ color: '#f4f6fa' }), size: [0.4, 0.012, 0.52], stand: [0, 1.3, 0.4, -1.1], sit: [0, 1.02, 0.45, -0.55] },
};
function heldItem(e, now) {
  if (!isActive(e.job.status) || !e.target) return null;
  switch (e.act) {
    case 'ship': return e.dropped ? null : 'box';
    case 'read': return e.stage > 0 || (!e.walking && e.arrivedAt != null && now - e.arrivedAt > 1) ? 'book' : null;   // pulled off the shelf
    case 'review': return e.walking ? null : 'sheet';
  }
  return null;
}
function hold(e, item, seated) {
  if (!item) { if (e.held) e.held.visible = false; return; }
  if (!e.held) { e.held = new THREE.Mesh(G.box); e.bot.root.add(e.held); }
  const H = HELD[item], [x, y, z, tilt] = seated ? H.sit : H.stand;
  e.held.material = H.mat; e.held.visible = true;
  e.held.scale.set(...H.size); e.held.position.set(x, y, z); e.held.rotation.set(tilt, 0, 0);
}

function updateEnt(e, r, dt, t, now) {
  const bot = e.bot, root = bot.root;
  playClip(e, clipFor(e, now));
  if (bot.clip === 'Walking') bot.actions.Walking.timeScale = e.slow ? 0.6 : 1.25;   // pacing is a slow amble
  bot.mixer.update(REDUCED ? 0 : dt);
  const st = e.job.status, arrived = !e.walking && !!e.target;
  if (st === 'stalled' && arrived) bot.head.rotation.x += 0.55;   // slumped over
  // on top of the clip: typing forearms, a nod or head shake, eyes down on a printout
  const A = ACTS[e.act] || {}, seated = arrived && bot.clip === 'Sitting' && isActive(st);
  if (!REDUCED && seated && A.hands) bot.arms.forEach((arm, i) => { arm.rotation.x += Math.sin(t * 15 + i * 2.1) * 0.14; });
  if (e.nod && now < e.nod.until) {
    const k = Math.sin((e.nod.until - now) * 13) * 0.32;
    if (e.nod.yes) bot.head.rotation.x += k; else bot.head.rotation.y += k * 1.3;
  }
  const item = heldItem(e, now);
  if (item === 'sheet' && seated) bot.head.rotation.x += 0.3;
  hold(e, item, seated);
  // a parcel goes into the outbox a moment after the android gets there
  if (e.act === 'ship' && !e.dropped && arrived && e.target.prop === 'mail' && e.arrivedAt != null && now - e.arrivedAt > 0.6) { e.dropped = true; r.mailAt = now; }
  const lift = arrived && e.target.sit != null && bot.clip === 'Sitting' ? e.target.sit : 0;
  e.lift += (lift - e.lift) * Math.min(1, dt * 6);
  root.position.set(r.ox + e.local.x + e.nudge.x, e.lift, r.oy + e.local.y + e.nudge.y);
  let face = e.facing;
  if (e.produceUntil && t >= e.produceFrom && t < e.produceUntil) face = Math.atan2(PRESS.x - e.local.x, PRESS.fab - e.local.y);   // turned to the press
  root.rotation.y += REDUCED ? angleTo(root.rotation.y, face) : angleTo(root.rotation.y, face) * Math.min(1, dt * 10);
  tone(e, t);
  e.ring.visible = e.key === selectedKey;
  if (e.ring.visible) e.ring.material.opacity = REDUCED ? 0.8 : 0.55 + Math.sin(t * 4) * 0.3;
  if (e.halo) e.halo.material.opacity = st === 'working' && !REDUCED ? 0.4 + Math.sin(t * 3) * 0.25 : 0.3;
  e.glow.visible = st === 'done' && arrived;
  e.fail.visible = st === 'failed' && arrived;
  if (e.fail.visible && Math.random() < 0.12) spawn(root.position.x + (Math.random() - 0.5) * 0.3, 0.5, root.position.z + (Math.random() - 0.5) * 0.3, 0);
  if (e.glow.visible && Math.random() < 0.03) spawn(root.position.x + (Math.random() - 0.5) * 0.8, 0.2, root.position.z + (Math.random() - 0.5) * 0.5, 1);
}

const WAVE_ON = new THREE.Color('#38bdf8'), WAVE = new THREE.Color();
// busy props: terminal screens scroll, the comms dish sends out waves, a marker light sketches on the whiteboard
function updateRoom(r, t, dt, now) {
  for (let i = 0; i < r.screens.length; i++) {
    const s = r.screens[i], busy = !!(r.busyTerm & (1 << i)), test = !!(r.testTerm & (1 << i));
    const verdict = s.verdict && now < s.verdict.until ? s.verdict.ok : null;
    if (busy !== s.busy || test !== s.test || verdict !== s.shown || t >= s.next) {
      s.busy = busy; s.test = test; s.shown = verdict; s.next = t + (busy ? 0.12 : 0.5); drawScreen(s, t);
    }
  }
  // cabinet drawers slide out while searched
  for (let i = 0; i < r.drawers.length; i++) {
    const h = r.drawers[i], want = r.busyCab & (1 << i) ? 1 : 0;
    const open = h.open ?? 0, next = REDUCED ? want : open + (want - open) * Math.min(1, dt * 5);
    if (Math.abs(next - open) < 0.002 && h.open != null) continue;
    h.open = next;
    _m4.copy(h.base); _m4.elements[12] += next * 0.5;   // along +x, out of the cabinet
    placer.setMatrix(h, _m4);
  }
  // build rack: the progress meter fills bar by bar while something builds, the status light blinks amber
  if (r.busyRack || r.wasRack) {
    const lit = r.busyRack ? (REDUCED ? 3 : Math.floor(t * 2.2) % (r.rackBars.length + 2)) : 0;
    r.rackBars.forEach((h, k) => placer.setColor(h, k < lit ? (k >= 4 ? '#facc15' : '#4ade80') : '#1e293b'));
    placer.setColor(r.rackLed, r.busyRack && (REDUCED || Math.sin(t * 7) > 0) ? '#f59e0b' : '#334155');
  }
  r.wasRack = r.busyRack;
  // coffee machine steams while someone waits for it
  if (r.busyKitchen && Math.random() < dt * 9) spawn(r.steamAt.x + (Math.random() - 0.5) * 0.12, r.steamAt.y, r.steamAt.z + (Math.random() - 0.5) * 0.12, 2);
  // outbox slot flashes as a parcel goes in
  const mail = r.mailAt != null && now - r.mailAt < 0.9 ? 1 - (now - r.mailAt) / 0.9 : 0;
  if (mail || r.wasMail) placer.setColor(r.mailSlot, mix(mix(r.look.accent, '#000000', 0.5), '#fff7d6', mail));
  r.wasMail = mail > 0;
  // the press's slit glows in the writer's colour while a document is being written
  if (r.busyPress || r.wasPress) placer.setColor(r.slit, r.busyPress && (REDUCED || Math.sin(t * 5) > -0.4) ? mix(r.busyPress, '#04070d', 0.35) : '#04070d');
  r.wasPress = !!r.busyPress;
  const web = r.busyWeb;
  if (web !== r.wasWeb || web) {
    placer.setColor(r.tip, web && (REDUCED || Math.sin(t * 8) > -0.2) ? '#38bdf8' : '#64748b');
    for (let k = 0; k < r.pulses.length; k++) {
      const h = r.pulses[k], u = REDUCED ? 0.5 : (t * 0.9 + k * 0.5) % 1, s = web ? 0.15 + u * 0.55 : 0;
      _m4.copy(h.base); _m4b.makeScale(s || 1e-4, s || 1e-4, s || 1e-4); _m4.multiply(_m4b);
      placer.setMatrix(h, _m4);
      placer.setColor(h, WAVE.copy(WAVE_ON).multiplyScalar(web ? 1 - u : 0));
    }
  }
  if (r.busyPlan !== r.wasPlan || r.busyPlan) {
    const u = REDUCED ? 0.3 : t * 0.7;
    const s = r.busyPlan ? 0.045 : 1e-4;
    _m4.copy(r.pen.base); _m4b.compose(_v.set(Math.sin(u * 2.1) * 0.8, Math.sin(u * 3.3) * 0.3, 0.02), _q.identity(), _sc.set(s, s, s)); _m4.multiply(_m4b);
    placer.setMatrix(r.pen, _m4);
  }
  r.wasWeb = web; r.wasPlan = r.busyPlan;
}

// ------------------------------------------------------------------ frame loop
let lastT = 0;
function frame(ts) {
  requestAnimationFrame(frame);
  if (document.hidden || !reader.hidden) { lastT = 0; return; }   // the reader covers the deck; don't render under it
  if (ts < panelScrollUntil) { lastT = 0; return; }             // hold the deck still while the panel scrolls, so the scroll gets the frame
  const t = ts / 1000, now = performance.now() / 1000;
  const dt = Math.min(0.1, lastT ? t - lastT : 0.016) * WARP;
  lastT = t;
  stepMotion(dt, now);
  if (cam.tween) {
    const k = REDUCED ? 1 : Math.min(1, dt * 7);
    cam.c.lerp(cam.tween, k);
    if (cam.c.distanceTo(cam.tween) < 0.01) cam.tween = null;
  }
  applyCamera();
  nSeg = 0;
  for (const r of rooms) { r.busyTerm = 0; r.testTerm = 0; r.busyWeb = false; r.busyPlan = false; r.busyCab = 0; r.busyRack = false; r.busyKitchen = false; r.busyPress = null; }
  for (const e of ents.values()) {
    const r = roomByName.get(e.room);
    if (!r) continue;
    updateEnt(e, r, dt, t, now);
    if (e.walking || !e.target || !isActive(e.job.status)) continue;
    const p = e.target.prop;
    if (p === 'terminal') { r.busyTerm |= 1 << e.target.propIdx; if (e.act === 'test') r.testTerm |= 1 << e.target.propIdx; }
    else if (p === 'cabinet') r.busyCab |= 1 << e.target.propIdx;
    else if (p === 'rack') r.busyRack = true;
    else if (p === 'kitchen') r.busyKitchen = true;
    else if (p === 'press') r.busyPress = e.look.color;
    else if (p === 'comms') r.busyWeb = true;
    else if (p === 'whiteboard') r.busyPlan = true;
    else if (p === 'partner' && e.partner && ents.has(e.partner.key)) {   // delegation: a marching arc to the helper
      e.bot.head.getWorldPosition(_la); e.partner.bot.head.getWorldPosition(_lb);
      _la.y += 0.35 * BK; _lb.y += 0.35 * BK;
      _w.copy(_la); _p.copy(_lb);
      dashedLine(_w, _p, e.look.color, t, 0.7);
    }
  }
  for (const r of rooms) updateRoom(r, t, dt, now);
  stepDocFx(t);
  liftHovered();
  lineGeo.setDrawRange(0, nSeg * 2);
  lineGeo.attributes.position.needsUpdate = true;
  lineGeo.attributes.color.needsUpdate = true;
  if (!REDUCED) for (const tex of edgeStrips) tex.offset.x = -t * 0.35;
  M.beacon.color.set(REDUCED || Math.sin(t * 2.4) > 0.6 ? 0xf87171 : 0x5a1d1d);
  M.failGlow.opacity = REDUCED ? 0.6 : 0.45 + Math.sin(t * 8) * 0.3;
  stepParticles(dt);
  renderer.render(scene, camera);
  positionTags();
  if (fpsEl) {
    fpsN++;
    if (t - fpsT > 1) { fpsEl.textContent = `${Math.round(fpsN / (t - fpsT))} fps · ${renderer.info.render.calls} calls`; fpsN = 0; fpsT = t; }
  }
}
const fpsEl = DEBUG ? document.body.appendChild(Object.assign(document.createElement('div'), { style: 'position:fixed;right:70px;bottom:14px;z-index:99;font:12px monospace;color:#9fe' })) : null;
let fpsN = 0, fpsT = 0;

// ------------------------------------------------------------------ overlay tags
function sessionBubble(s) {
  const a = s.activity;
  if (s.status === 'idle') return [`<span class="ic">⏸</span>waiting for you since ${esc(clock(s.updated_at).slice(0, 5))}`, 'wait'];
  if (a && a.kind === 'error') return [`<span class="ic">!</span>${esc(trunc(a.summary, 120))}`, 'bad'];
  if (a && a.kind === 'text') return [`<span class="ic">“</span>${esc(trunc(a.summary, 120))}`, ''];
  if (a) return [esc(mumble(a)), ''];
  return ['<span class="ic">∴</span>working…', ''];
}
function updateTag(e) {
  const j = e.job;
  if (isSession(e)) {
    const [bubble, cls] = sessionBubble(j);
    const stack = `<span class="lv${j.status === 'idle' ? ' idle' : ''}">LIVE</span><span class="id">${esc(trunc(j.title || shortId(j.id), vw < 760 ? 16 : 28))}</span><span class="ag">${esc(j.agent)}</span>`;
    const sig = bubble + '|' + cls + '|' + stack;
    if (sig === e.sig) return;
    e.sig = sig;
    e.el.firstChild.className = 'bubble ' + cls;
    e.el.firstChild.innerHTML = `<span class="bt">${bubble}</span>`;
    e.el.lastChild.innerHTML = stack;
    e.sizeDirty = true;
    return;
  }
  const steps = j.steps || [];
  const done = steps.filter(s => s.status === 'done').length;
  const cur = steps.findIndex(s => s.status === 'running' || s.status === 'failed');
  let bubble, cls = '';
  const a = j.activity;
  switch (j.status) {
    case 'running':
      if (a && a.kind === 'error') { bubble = `<span class="ic">!</span>${esc(trunc(a.summary, 120))}`; cls = 'bad'; }
      else if (a && a.kind === 'text') bubble = `<span class="ic">“</span>${esc(trunc(a.summary, 120))}`;
      else if (a) bubble = esc(mumble(a));
      else bubble = '<span class="ic">∴</span>warming up…';
      break;
    case 'queued': bubble = 'queued · waiting at the door'; cls = 'quiet'; break;
    case 'done': bubble = `✓ done · ${done}/${steps.length}`; cls = 'done'; break;
    case 'failed': bubble = `✗ step ${cur + 1} failed`; cls = 'bad'; break;
    case 'stalled': bubble = 'runner stalled mid-step'; cls = 'stall'; break;
    case 'cancelled': bubble = 'cancelled'; cls = 'quiet'; break;
    default: bubble = esc(j.status); cls = 'quiet';
  }
  let window0 = 0;
  if (steps.length > 12) window0 = clamp((cur < 0 ? done : cur) - 5, 0, steps.length - 12);
  const pips = steps.slice(window0, window0 + 12).map(s => `<i class="pip ${esc(s.status)}"></i>`).join('');
  const sig = bubble + '|' + cls + '|' + pips + done;
  if (sig === e.sig) return;
  e.sig = sig;
  e.el.firstChild.className = 'bubble ' + cls;
  e.el.firstChild.innerHTML = `<span class="bt">${bubble}</span>`;
  e.el.lastChild.innerHTML = `<span class="id">${esc(j.id)}</span>${window0 > 0 ? '<b>…</b>' : ''}${pips}<b>${done}/${steps.length}</b>`;
  e.sizeDirty = true;
}

// Tags hang above each android's head, projected from 3D. Nearer androids are placed first and
// colliding tags are pushed upward, with a thin lead line back to their android.
const tagList = [], placed = [];
const _s = { x: 0, y: 0 };
function positionTags() {
  // phones have little room: speech bubbles appear once zoomed in (or for the selected android)
  const small = cam.z < (vw < 760 ? SMALL_Z * 2.2 : SMALL_Z), tiny = cam.z < TINY_Z;
  tagList.length = 0;
  for (const e of ents.values()) {
    if (!roomByName.has(e.room)) continue;
    if (e.sizeDirty) { e.tw = e.el.offsetWidth; e.th = e.el.offsetHeight; e.sizeDirty = false; }
    // anchor on the head bone, lifted clear of the head and its kit, plus a few pixels at every zoom
    e.bot.head.getWorldPosition(_w);
    _w.y += e.bot.tagLift;
    toScreen(_w, _s);
    e.sx = _s.x; e.sy = _s.y - 6;
    e.depth = e.bot.root.position.x + e.bot.root.position.z;
    tagList.push(e);
  }
  tagList.sort((a, b) => (b.key === selectedKey) - (a.key === selectedKey) || b.depth - a.depth);
  let n = 0;
  for (const e of tagList) {
    let bottom = e.sy;
    const w = e.tw || 120, h = e.th || 40;
    if (!tiny) {
      for (let guard = 0, moved = true; moved && guard < 24; guard++) {
        moved = false;
        for (let i = 0; i < n; i++) {
          const b = placed[i];
          if (e.sx - w / 2 < b.x + b.w / 2 + 3 && e.sx + w / 2 > b.x - b.w / 2 - 3 && bottom - h < b.bottom + 2 && bottom > b.bottom - b.h - 2) {
            bottom = b.bottom - b.h - 4; moved = true;
          }
        }
      }
      const slot = placed[n] || (placed[n] = {});
      slot.x = e.sx; slot.w = w; slot.h = h; slot.bottom = bottom; n++;
    }
    // only touch the DOM when a tag actually moved by a pixel
    const qx = Math.round(e.sx), qy = Math.round(bottom), lead = Math.max(0, Math.round(e.sy - bottom));
    if (qx !== e.qx || qy !== e.qy) { e.qx = qx; e.qy = qy; e.el.style.transform = `translate(${qx}px,${qy}px) translate(-50%,-100%)`; }
    if (lead !== e.qlead) { e.qlead = lead; e.el.style.setProperty('--lead', lead > 2 ? lead + 'px' : '0px'); }
  }
  tagList.sort((a, b) => a.depth - b.depth);
  let order = 0;
  for (const e of tagList) {
    const z = ++order + (e.key === selectedKey ? 1000 : 0);
    if (z !== e.qz) { e.qz = z; e.el.style.zIndex = String(z); }
    const cls = e.tagBase + (tiny ? ' tiny' : e.key === selectedKey ? ' sel' : small ? ' small' : '');
    if (e.el.className !== cls) { e.el.className = cls; e.sizeDirty = true; }
  }
}

// ------------------------------------------------------------------ documents: what the agents produced
// (the 3D document objects and printer come next; the panel and reader already use this metadata)
const DOC_KIND = {
  report: { label: 'Report', glyph: '▤' },
  file:   { label: 'File',   glyph: '✎' },
  outbox: { label: 'Outbox', glyph: '⇪' },
};
const seenDocs = new Set();
const docFx = new Map();                            // doc key → { entKey, start }
let hoverDoc = null;                                // { key } of the document under the pointer

function docKey(e, doc) { return e.key + ':' + doc.id; }
function docsOf(job) { return (job.documents || []).slice().sort((a, b) => (a.mtime || 0) - (b.mtime || 0)); }
function kindOf(doc) { return DOC_KIND[doc.kind] ? doc.kind : 'file'; }
function fmtSize(n) { if (!n && n !== 0) return ''; if (n < 1024) return n + ' B'; if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB'; return (n / 1048576).toFixed(1) + ' MB'; }
function docMeta(doc) { return [doc.step != null ? `step ${doc.step + 1}` : '', fmtSize(doc.size), doc.mtime ? age(doc.mtime) + ' ago' : ''].filter(Boolean).join(' · '); }

// remember every document; ones that appear on a job we already knew about get printed
function noteDocs(e, quiet) {
  const now = performance.now() / 1000;
  let delay = 0;
  for (const doc of docsOf(e.job)) {
    const k = docKey(e, doc);
    if (seenDocs.has(k)) continue;
    seenDocs.add(k);
    if (quiet || REDUCED) continue;
    const start = now + delay;
    docFx.set(k, { entKey: e.key, start });
    e.produceFrom = e.produceUntil > now ? e.produceFrom : start;
    e.produceUntil = start + FX.print + 0.35;
    delay += 0.75;
  }
}

// ------------------------------------------------------------------ camera: fit, zoom, focus
function deckBounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of rooms) { x0 = Math.min(x0, r.ox); y0 = Math.min(y0, r.oy); x1 = Math.max(x1, r.ox + RW); y1 = Math.max(y1, r.oy + RD); }
  return { x0: x0 - 1.8, y0: y0 - 1.8, x1: x1 + 1.8, y1: y1 + 1.8 };
}
// extent of the deck along the screen axes, in tiles
function screenExtent() {
  let minR = Infinity, maxR = -Infinity, minU = Infinity, maxU = -Infinity;
  for (const b of plates) for (const x of [b.x0, b.x1]) for (const z of [b.y0, b.y1]) for (const y of [-0.55, WALL_H + 1.4]) {
    _p.set(x, y, z);
    const a = _p.dot(RIGHT), u = _p.dot(UP);
    minR = Math.min(minR, a); maxR = Math.max(maxR, a); minU = Math.min(minU, u); maxU = Math.max(maxU, u);
  }
  return { minR, maxR, minU, maxU };
}
function fit(silent) {
  if (!rooms.length || !vw) return;
  const b = screenExtent();
  const mobile = vw < 760;
  const legendOpen = !document.getElementById('legend').classList.contains('closed');
  const left = (!mobile && vw > 1100 && legendOpen) ? 270 : 8;
  const top = mobile ? 100 : 60, bottom = mobile ? 70 : 24, right = 8;
  const aw = vw - left - right, ah = vh - top - bottom;
  const W = b.maxR - b.minR, H = b.maxU - b.minU;
  let z, cy;
  if (mobile) {
    // fit the column's width and let it scroll (drag) vertically
    z = clamp(aw / W, 6, 60);
    cy = H * z < ah ? top + ah / 2 : top + H * z / 2;
  } else {
    z = clamp(Math.min(aw / W, ah / H), 6, 60);
    cy = top + ah / 2;
  }
  const cx = left + aw / 2;
  cam.z = z;
  // the world point at the middle of the extent goes to (cx, cy)
  const mid = new THREE.Vector3().addScaledVector(RIGHT, (b.minR + b.maxR) / 2).addScaledVector(UP, (b.minU + b.maxU) / 2);
  cam.c.copy(centreFor(mid, cx, cy, z));
  cam.tween = null;
  if (!silent) cam.userMoved = false;
}
function zoomAt(sx, sy, f) {
  const nz = clamp(cam.z * f, 5, 140);
  const P = new THREE.Vector3().copy(cam.c).addScaledVector(RIGHT, (sx - vw / 2) / cam.z).addScaledVector(UP, -(sy - vh / 2) / cam.z);
  cam.c.copy(centreFor(P, sx, sy, nz));
  cam.z = nz;
  cam.userMoved = true; cam.tween = null;
}
function panBy(dx, dy) {
  cam.c.addScaledVector(RIGHT, -dx / cam.z).addScaledVector(UP, dy / cam.z);
  cam.userMoved = true; cam.tween = null;
}
function focusOn(e) {
  if (!e || !e.bot) return;
  const mobile = vw < 760;
  // centre the android in whatever is left visible beside the side panel / above the bottom sheet
  const cx = mobile ? vw / 2 : (vw - 420) / 2, cy = mobile ? 52 + (vh * 0.38 - 52) * 0.5 + 40 : vh / 2;
  const want = mobile ? 30 : 44;
  if (cam.z < want) zoomAt(cx, cy, want / cam.z);
  const P = e.bot.root.position.clone(); P.y += BOT_H * 0.7;
  cam.tween = centreFor(P, cx, cy, cam.z);
  cam.userMoved = true;
}

const stars = document.getElementById('stars');
function drawStars() {
  stars.width = Math.round(vw * dpr); stars.height = Math.round(vh * dpr);
  const g = stars.getContext('2d'), rand = seeded(7);
  g.scale(dpr, dpr);
  for (let i = 0; i < 170; i++) {
    g.fillStyle = `rgba(200,220,255,${0.25 + rand() * 0.4})`;
    const s = rand() * 1.2 + 0.2;
    g.fillRect(rand() * vw, rand() * vh, s, s);
  }
}

function resize() {
  setDpr(Math.min(window.devicePixelRatio || 1, 2));
  setVw(window.innerWidth); setVh(window.innerHeight);
  renderer.setPixelRatio(dpr);
  renderer.setSize(vw, vh, false);
  drawStars();
  if (!ROBOT) return;
  layoutRooms(layoutNames);
  if (!cam.userMoved) fit(true);
}
window.addEventListener('resize', resize);

// ------------------------------------------------------------------ pointer: drag to pan, pinch/wheel to zoom, tap to select
const pointers = new Map();
let drag = null, pinch = null;
canvas.addEventListener('pointerdown', ev => {
  try { canvas.setPointerCapture(ev.pointerId); } catch (err) { /* synthetic pointer (test hook) */ }
  pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  if (pointers.size === 1) drag = { x: ev.clientX, y: ev.clientY, moved: false };
  else if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }; drag = null; }
});
canvas.addEventListener('pointermove', ev => {
  if (pointers.has(ev.pointerId)) pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  if (pinch && pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    panBy(mx - pinch.mx, my - pinch.my);
    if (pinch.d > 0) zoomAt(mx, my, d / pinch.d);
    pinch = { d, mx, my };
    return;
  }
  if (drag) {
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; canvas.classList.add('dragging'); }
    if (drag.moved) { panBy(dx, dy); drag.x = ev.clientX; drag.y = ev.clientY; hideDocTip(); }
    return;
  }
  const hit = pick(ev.clientX, ev.clientY);
  canvas.classList.toggle('hot', !!hit);
  if (hit && hit.doc && ev.pointerType !== 'touch') showDocTip(hit, ev.clientX, ev.clientY); else hideDocTip();
});
function endPointer(ev) {
  pointers.delete(ev.pointerId);
  if (pointers.size < 2) pinch = null;
  if (drag && !drag.moved && ev.type === 'pointerup') {
    const hit = pick(ev.clientX, ev.clientY);
    if (hit && hit.doc) openReader(hit.e, hit.doc);
    else if (hit) select(hit.e.key);
    else if (selectedKey) closePanel();
  }
  if (pointers.size === 0) { drag = null; canvas.classList.remove('dragging'); }
}
canvas.addEventListener('pointerup', endPointer);
canvas.addEventListener('pointercancel', endPointer);
canvas.addEventListener('pointerleave', hideDocTip);

const docTip = document.getElementById('docTip');
function showDocTip(hit, px, py) {
  const { e, doc } = hit, key = docKey(e, doc);
  if (!hoverDoc || hoverDoc.key !== key) {
    hoverDoc = { key };
    const K = DOC_KIND[kindOf(doc)];
    docTip.style.setProperty('--hc', e.look.color);
    docTip.innerHTML = `<div class="th"><span class="kb">${K.label}</span><b>${esc(doc.name)}</b></div>
      <div class="tm">${esc(docMeta(doc))}</div><div class="tc">${esc(e.host)}:${esc(e.job.id)} · click to read</div>`;
    docTip.hidden = false;
  }
  const w = docTip.offsetWidth, h = docTip.offsetHeight;
  const x = px + 16 + w > vw - 8 ? px - 16 - w : px + 16, y = py + 20 + h < vh - 8 ? py + 20 : Math.max(60, py - h - 60);
  docTip.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
}
function hideDocTip() { hoverDoc = null; docTip.hidden = true; }
canvas.addEventListener('wheel', ev => { ev.preventDefault(); zoomAt(ev.clientX, ev.clientY, Math.exp(-ev.deltaY * 0.0015)); }, { passive: false });

const raycaster = new THREE.Raycaster();
const _ndc = new THREE.Vector2();
const proxies = [];
// the nearest android (an invisible capsule around each) or document sheet under the pointer
function pick(px, py) {
  proxies.length = 0;
  for (const e of ents.values()) if (roomByName.has(e.room)) proxies.push(e.proxy);
  for (const kind in docMeshes) proxies.push(docMeshes[kind]);
  _ndc.set(px / vw * 2 - 1, -(py / vh) * 2 + 1);
  raycaster.setFromCamera(_ndc, camera);
  const hit = raycaster.intersectObjects(proxies, false)[0];
  if (!hit) return null;
  if (hit.object.isInstancedMesh) {
    const slot = docSlots[hit.object.userData.kind][hit.instanceId];
    return slot ? { e: slot.e, doc: slot.doc } : null;
  }
  return { e: hit.object.userData.ent };
}

document.getElementById('zoom').addEventListener('click', ev => {
  const z = ev.target.closest('button')?.dataset.z;
  if (z === 'in') zoomAt(vw / 2, vh / 2, 1.25);
  else if (z === 'out') zoomAt(vw / 2, vh / 2, 0.8);
  else if (z === 'fit') fit(false);
});
document.addEventListener('keydown', ev => {
  if (ev.target.closest && ev.target.closest('input,textarea')) return;
  if (!reader.hidden) { if (ev.key === 'Escape') closeReader(); return; }
  if (ev.key === 'Escape') { if (!libraryPane.hidden) closeLibrary(); else closePanel(); }
  else if (ev.key === '+' || ev.key === '=') zoomAt(vw / 2, vh / 2, 1.2);
  else if (ev.key === '-' || ev.key === '_') zoomAt(vw / 2, vh / 2, 1 / 1.2);
  else if (ev.key === 'f' || ev.key === 'F') fit(false);
});
for (const b of document.querySelectorAll('[data-toggle]')) {
  b.addEventListener('click', () => {
    const card = document.getElementById(b.dataset.toggle);
    card.classList.toggle('closed');
    try { localStorage.setItem('fleet.deck.' + b.dataset.toggle, card.classList.contains('closed') ? 'closed' : 'open'); } catch (err) { /* storage unavailable */ }
  });
}
for (const id of ['legend', 'feed']) {
  let saved = null;
  try { saved = localStorage.getItem('fleet.deck.' + id); } catch (err) { /* storage unavailable */ }
  if (saved === 'closed' || (saved === null && window.innerWidth < 760)) document.getElementById(id).classList.add('closed');
}

// ------------------------------------------------------------------ portraits for the manifest and the panel
// Rendered once per look into an offscreen target with the main renderer, then copied into small 2D canvases.
const portraits = new Map();
let portraitStage = null;
function renderPortrait(look, agent, pose, W, H) {
  if (!portraitStage) {
    const s = new THREE.Scene();
    s.add(new THREE.HemisphereLight(0xc8d8ff, 0x302838, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(2, 3, 4); s.add(key);
    const c = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 50);
    portraitStage = { scene: s, camera: c };
  }
  const { scene: s, camera: c } = portraitStage;
  const bot = buildRobot(look, agent);
  action(bot, 'Idle').play();
  bot.mixer.update(0.5);
  if (pose === 'off') { bot.main.color.set(mix(look.color, '#475163', 0.55)); bot.face.color.set('#3a4252'); if (bot.eyes) { bot.eyes.color.set('#3a4252'); bot.eyes.emissiveIntensity = 0; } }
  bot.root.rotation.y = 0.45 + (DEBUG && QS.get('bots') === 'back' ? PI : 0);
  s.add(bot.root);
  const hgt = BOT_H + 0.4, wid = hgt * W / H;
  c.left = -wid / 2; c.right = wid / 2; c.top = hgt / 2; c.bottom = -hgt / 2;
  c.position.set(0, hgt / 2 + 0.6, 6); c.lookAt(0, hgt / 2 - 0.05, 0); c.updateProjectionMatrix();
  const rt = new THREE.WebGLRenderTarget(W * 2, H * 2);
  rt.texture.colorSpace = THREE.SRGBColorSpace;
  renderer.setRenderTarget(rt);
  renderer.clear();
  renderer.render(s, c);
  const px = new Uint8Array(W * 2 * H * 2 * 4);
  renderer.readRenderTargetPixels(rt, 0, 0, W * 2, H * 2, px);
  renderer.setRenderTarget(null);
  s.remove(bot.root);
  rt.dispose(); bot.main.dispose(); bot.face.dispose(); bot.hostMat.dispose(); bot.eyes?.dispose();
  const out = document.createElement('canvas'); out.width = W * 2; out.height = H * 2;
  const img = out.getContext('2d').createImageData(W * 2, H * 2);
  const row = W * 2 * 4;
  for (let y = 0; y < H * 2; y++) img.data.set(px.subarray((H * 2 - 1 - y) * row, (H * 2 - y) * row), y * row);
  out.getContext('2d').putImageData(img, 0, 0);
  return out;
}
function miniBot(canvasEl, look, agent, pose) {
  const w = canvasEl.clientWidth || 34, h = canvasEl.clientHeight || 44;
  const r = Math.min(window.devicePixelRatio || 1, 2);
  canvasEl.width = w * r; canvasEl.height = h * r;
  if (!ROBOT) return;
  const key = [look.color, look.acc, agent, pose, w, h].join('|');
  let img = portraits.get(key);
  if (!img) { img = renderPortrait(look, agent, pose, w * r, h * r); portraits.set(key, img); }
  const g = canvasEl.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.drawImage(img, 0, 0, canvasEl.width, canvasEl.height);
}

// ------------------------------------------------------------------ side panel
const panel = document.getElementById('panel');
const PANEL_SCROLL_HOLD_MS = 180;
let panelScrollUntil = 0, panelRenderPending = false;
document.getElementById('panelBody').addEventListener('scroll', () => {
  panelScrollUntil = performance.now() + PANEL_SCROLL_HOLD_MS;
}, { passive: true });
function select(key) {
  if (key !== selectedKey) document.getElementById('panelBody').scrollTop = 0;
  setSelectedKey(key);
  panel.classList.add('open');
  panel.setAttribute('aria-hidden', 'false');
  renderPanel();
  focusOn(ents.get(key));
}
function closePanel() {
  setSelectedKey(null);
  panel.classList.remove('open');
  panel.setAttribute('aria-hidden', 'true');
}
function renderPanel() {
  // mid-scroll, updates wait until the scroll settles rather than rewriting content under it
  const wait = panelScrollUntil - performance.now();
  if (wait > 0) {
    if (!panelRenderPending) { panelRenderPending = true; setTimeout(() => { panelRenderPending = false; renderPanel(); }, wait + 20); }
    return;
  }
  const e = ents.get(selectedKey);
  if (!e) return;
  if (isSession(e)) { renderSessionPanel(e); return; }
  const j = e.job;
  const ref = `${e.host}:${j.id}`;
  const headHtml = `<canvas style="width:46px;height:60px"></canvas>
    <div style="min-width:0;flex:1"><h2>${esc(j.description)}</h2>
      <div class="sub">
        <span class="chip"><i style="background:${e.look.color}"></i><b>${esc(e.host)}</b></span>
        <span class="chip"><i style="background:${AGENT_COLOR[j.agent] || '#ccc'}"></i>${esc(j.agent)}</span>
        <span class="chip st-${esc(j.status)}">${esc(j.status)}</span>
      </div></div>
    ${j.status !== 'running' ? DISMISS_BUTTON : ''}
    <button id="close" aria-label="Close">✕</button>`;
  const steps = j.steps || [];
  const stepIcon = { done: '✓', running: '▶', failed: '✗', cancelled: '⊘', pending: '○' };
  const events = (j.events || []).filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').slice(-18).reverse();
  const cmds = [`fleet attach ${ref}`, `fleet tail ${ref} -f`, `fleet show ${ref}`];
  // state updates arrive for every job, many times a second: replace only the sections that changed,
  // so the rest of the panel keeps its nodes and the scroll position stays put
  const sections = [`
    <h3>Job</h3>
    <dl class="meta">
      <dt>ref</dt><dd>${esc(ref)}</dd>
      <dt>project</dt><dd>${esc(j.project)}</dd>
      <dt>model</dt><dd>${esc(j.model || 'default')}</dd>
      <dt>cwd</dt><dd>${esc(j.cwd)}</dd>
      ${j.permission ? `<dt>perms</dt><dd>${esc(j.permission)}</dd>` : ''}
      <dt>updated</dt><dd>${esc(age(j.updated_at))} ago</dd>
    </dl>`, `
    <h3>Steps · ${steps.filter(s => s.status === 'done').length}/${steps.length}</h3>
    <ol class="steps">${steps.map(s => `<li class="${esc(s.status)}"><span class="si">${stepIcon[s.status] || '?'}</span>
      <span class="t">${s.index + 1}. ${esc(s.title)}</span>${s.result ? `<span class="r">${esc(trunc(s.result, 400))}</span>` : ''}</li>`).join('')}</ol>`,
    docsPanelHtml(e),
    (j.todos && j.todos.length) ? `<h3>Agent's own todo list</h3><ul class="todos">${j.todos.map(td => `<li class="${esc(td.status)}">${td.status === 'completed' ? '✓' : td.status === 'in_progress' ? '▸' : '·'} ${esc(td.text)}</li>`).join('')}</ul>` : '', `
    <h3>Recent activity</h3>
    ${events.length ? `<ul class="evs">${events.map(ev => `<li class="${ev.kind === 'error' ? 'err' : ''}"><time>${clock(ev.ts)}</time><span class="k">${esc(eventIcon(ev))}</span><span class="${isCodeEvent(ev) ? 'code' : ''}">${esc(trunc(ev.summary || ev.status || ev.kind, 220))}</span></li>`).join('')}</ul>` : '<p class="muted" style="font-size:12px">No events yet.</p>'}`, `
    <h3>Commands</h3>
    ${cmds.map(c => `<div class="cmd"><code>${esc(c)}</code><button data-copy="${esc(c)}">copy</button></div>`).join('')}`];
  patchPanel(headHtml, sections, e, j.status === 'done' ? 'off' : j.status === 'stalled' ? 'slump' : 'normal');
}
const DISMISS_BUTTON = '<button id="dismiss" title="Hide this agent from the deck until it has new activity">Dismiss</button>';
// State updates arrive for every job, many times a second: rewrite the head and each body section only when its
// markup changed, so the rest of the panel keeps its nodes and the scroll position stays put.
function patchPanel(headHtml, sections, e, pose) {
  const head = document.getElementById('panelHead');
  if (head.lastHtml !== headHtml) {
    head.lastHtml = headHtml;
    head.innerHTML = headHtml;
    miniBot(head.querySelector('canvas'), e.look, e.job.agent, pose);
    head.querySelector('#close').addEventListener('click', closePanel);
    head.querySelector('#dismiss')?.addEventListener('click', () => dismiss(selectedKey));
  }
  const body = document.getElementById('panelBody');
  if (body.childElementCount !== sections.length) body.replaceChildren(...sections.map(() => document.createElement('div')));
  sections.forEach((html, i) => {
    const part = body.children[i];
    if (part.lastHtml !== html) { part.lastHtml = html; part.innerHTML = html; }
  });
}
// An interactive session: what it is, where it runs, its todos and recent activity. No steps or fleet commands —
// the one useful command is resuming it in a terminal.
function renderSessionPanel(e) {
  const s = e.job;
  const headHtml = `<canvas style="width:46px;height:60px"></canvas>
    <div style="min-width:0;flex:1"><h2>${s.title ? esc(s.title) : '<span class="untitled">no title yet</span>'}</h2>
      <div class="sub">
        <span class="chip sess st-${esc(s.status)}"><i></i>live · ${esc(s.status === 'idle' ? 'waiting for you' : s.status)}</span>
        <span class="chip"><i style="background:${e.look.color}"></i><b>${esc(e.host)}</b></span>
        <span class="chip"><i style="background:${AGENT_COLOR[s.agent] || '#ccc'}"></i>${esc(s.agent)}</span>
      </div></div>
    ${s.status === 'idle' ? DISMISS_BUTTON : ''}
    <button id="close" aria-label="Close">✕</button>`;
  const events = (s.events || []).filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').slice(-18).reverse();
  const sections = [`
    <h3>Interactive session</h3>
    <dl class="meta">
      <dt>session</dt><dd>${esc(s.id)}</dd>
      <dt>project</dt><dd>${esc(s.project)}</dd>
      <dt>model</dt><dd>${esc(s.model || 'not reported yet')}</dd>
      <dt>cwd</dt><dd>${esc(s.cwd)}</dd>
      <dt>started</dt><dd>${s.started_at ? `${esc(clock(s.started_at))} · ${esc(age(s.started_at))} ago` : '<span class="muted">unknown</span>'}</dd>
      <dt>updated</dt><dd>${esc(clock(s.updated_at))} · ${esc(age(s.updated_at))} ago</dd>
    </dl>`,
    (s.todos && s.todos.length) ? `<h3>Agent's own todo list</h3><ul class="todos">${s.todos.map(td => `<li class="${esc(td.status)}">${td.status === 'completed' ? '✓' : td.status === 'in_progress' ? '▸' : '·'} ${esc(td.text)}</li>`).join('')}</ul>` : '', `
    <h3>Recent activity</h3>
    ${events.length ? `<ul class="evs">${events.map(ev => `<li class="${ev.kind === 'error' ? 'err' : ''}"><time>${clock(ev.ts)}</time><span class="k">${esc(eventIcon(ev))}</span><span class="${isCodeEvent(ev) ? 'code' : ''}">${esc(trunc(ev.summary || ev.kind, 220))}</span></li>`).join('')}</ul>` : '<p class="muted" style="font-size:12px">No activity in the transcript tail.</p>'}`,
    s.resume ? `<h3>Resume in a terminal</h3><div class="cmd"><code>${esc(s.resume)}</code><button data-copy="${esc(s.resume)}">copy</button></div>` : ''];
  patchPanel(headHtml, sections, e, 'normal');
}
function docsPanelHtml(e) {
  const docs = docsOf(e.job).reverse();
  if (!docs.length) return '';
  return `<h3>Documents · ${docs.length}</h3><ul class="docs" style="--hc:${e.look.color}">${docs.map(d => {
    const kind = kindOf(d);
    return `<li><button data-doc="${esc(d.id)}" title="Read ${esc(d.name)}"><span class="dk ${kind}" aria-hidden="true">${DOC_KIND[kind].glyph}</span>
      <span class="dn">${esc(d.name)}</span><span class="dm">${DOC_KIND[kind].label.toLowerCase()} · ${esc(docMeta(d))}</span><span class="go">Read →</span></button></li>`;
  }).join('')}</ul>`;
}
panel.addEventListener('click', ev => {
  const open = ev.target.closest('[data-doc]');
  if (open) {
    const e = ents.get(selectedKey), doc = e && (e.job.documents || []).find(d => d.id === open.dataset.doc);
    if (doc) openReader(e, doc);
    return;
  }
  const b = ev.target.closest('[data-copy]');
  if (!b) return;
  const text = b.dataset.copy;
  const done = () => { b.textContent = 'copied'; b.classList.add('ok'); setTimeout(() => { b.textContent = 'copy'; b.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  else fallbackCopy(text, done);
});
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (err) { /* nothing else to try */ }
  ta.remove();
}
function eventIcon(ev) {
  if (ev.kind === 'tool') return TOOL_ICON[ev.tool] || '•';
  return { text: '“', error: '!', step: '▸', job: '◆', result: '✓', log: '·' }[ev.kind] || '·';
}

// ------------------------------------------------------------------ local project library
const libraryPane = document.getElementById('libraryPane');
const libSearch = document.getElementById('libSearch');
let libraryDocs = [], libraryFocus = null;
function closeLibrary() {
  if (libraryPane.hidden) return;
  libraryPane.hidden = true;
  libraryFocus?.focus();
}
function renderLibrary() {
  const query = libSearch.value.trim().toLowerCase();
  const docs = libraryDocs.filter(d => `${d.project} ${d.title} ${d.id}`.toLowerCase().includes(query));
  const list = document.getElementById('libList');
  if (!docs.length) {
    list.innerHTML = `<p class="lib-empty">${libraryDocs.length ? 'No matching documents.' : 'No project libraries configured. Add one with <code>fleet library add PROJECT /path/to/repo</code>.'}</p>`;
    return;
  }
  const groups = new Map();
  for (const doc of docs) {
    if (!groups.has(doc.project)) groups.set(doc.project, []);
    groups.get(doc.project).push(doc);
  }
  list.innerHTML = [...groups].map(([project, items]) => `<section class="lib-group"><h3>${esc(project)}</h3>
    ${items.map(d => `<button class="lib-doc" data-project="${esc(d.project)}" data-id="${esc(d.id)}">
      <i aria-hidden="true">▤</i><span><b>${esc(d.title)}</b><small>${esc(d.id)} · ${esc(fmtSize(d.size))}</small></span></button>`).join('')}</section>`).join('');
}
async function showLibrary() {
  libraryFocus = document.activeElement;
  libraryPane.hidden = false;
  document.getElementById('libList').innerHTML = '<p class="lib-empty">Loading documents…</p>';
  libSearch.focus();
  try {
    const response = await fetch('/api/library');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    libraryDocs = (await response.json()).documents || [];
    renderLibrary();
  } catch (error) {
    document.getElementById('libList').innerHTML = `<p class="lib-empty">Couldn’t load the library: ${esc(error.message)}</p>`;
  }
}
document.getElementById('libraryOpen').addEventListener('click', showLibrary);
libraryPane.addEventListener('click', ev => {
  if (ev.target.closest('[data-lib-close]')) { closeLibrary(); return; }
  const button = ev.target.closest('.lib-doc');
  if (!button) return;
  const doc = libraryDocs.find(d => d.project === button.dataset.project && d.id === button.dataset.id);
  if (doc) openLibraryReader(doc);
});
libSearch.addEventListener('input', renderLibrary);

// ------------------------------------------------------------------ reader
const reader = document.getElementById('reader');
const rdSheet = reader.querySelector('.rd-sheet');
const rdBody = document.getElementById('rdBody');
const WIDE = matchMedia('(min-width: 1101px)');
const rdProgress = document.getElementById('rdProgress');
const rd = { key: null, source: 'job', host: null, job: null, doc: null, data: null, req: 0, raf: 0, lastFocus: null, tocLinks: [], tocCurrent: null };
const THEME_ICON = {
  dark: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/></svg><span class="lb">Paper</span>',
  paper: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z"/></svg><span class="lb">Dark</span>',
};

function setReaderTheme(theme) {
  rdSheet.dataset.theme = theme;
  const b = document.getElementById('rdTheme');
  b.innerHTML = THEME_ICON[theme];
  b.setAttribute('aria-label', theme === 'dark' ? 'Switch to the paper theme' : 'Switch to the dark theme');
}
setReaderTheme(store('localStorage','fleet.reader.theme') === 'paper' ? 'paper' : 'dark');

function openReader(e, doc) {
  hideDocTip();
  rd.req++;
  rd.key = `${e.host}:${e.job.id}:${doc.id}`;
  rd.source = 'job';
  rd.host = e.host; rd.job = e.job; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderLoading();
  rdSheet.focus();
  loadDoc(rd.req);
}
function openLibraryReader(doc) {
  if (!reader.hidden) saveReaderScroll();
  rd.req++;
  rd.key = `library:${doc.project}:${doc.id}`;
  rd.source = 'library';
  rd.host = null; rd.job = { id: 'library', description: doc.project }; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderLoading();
  rdSheet.focus();
  loadDoc(rd.req);
}
function closeReader() {
  if (reader.hidden) return;
  saveReaderScroll();
  rd.req++; rd.key = null;
  reader.hidden = true;
  if (rd.lastFocus && rd.lastFocus.focus) rd.lastFocus.focus();
}
async function loadDoc(req) {
  try {
    const data = rd.source === 'library' ? await fetchLibraryDoc(rd.doc.project, rd.doc.id)
      : DEMO ? await demoDoc(rd.host, rd.job.id, rd.doc.id) : await fetchDoc(rd.host, rd.job.id, rd.doc.id);
    if (req !== rd.req) return;
    rd.data = data;
    renderReaderHead();
    renderReaderBody();
  } catch (err) {
    if (req === rd.req) renderReaderError(err.message || String(err));
  }
}
async function fetchDoc(host, job, id) {
  const res = await fetch('/api/doc?' + new URLSearchParams({ host, job, id }));
  let body = null;
  try { body = await res.json(); } catch (err) { /* not JSON */ }
  if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status} ${res.statusText}`);
  if (!body || typeof body.html !== 'string') throw new Error('The server returned an empty document.');
  return body;
}
async function fetchLibraryDoc(project, id) {
  const res = await fetch('/api/library/doc?' + new URLSearchParams({ project, id }));
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function renderReaderHead() {
  const doc = rd.doc, d = rd.data || {}, kind = kindOf(doc);
  const kindEl = document.getElementById('rdKind');
  kindEl.className = 'rd-kind ' + kind; kindEl.textContent = DOC_KIND[kind].label;
  document.getElementById('rdTitle').textContent = rd.source === 'library' ? (d.title || doc.title || d.name || doc.name) : (d.name || doc.name);
  const step = d.step ?? doc.step;
  document.getElementById('rdMeta').innerHTML = [
    rd.source === 'library' ? `<span>${esc(doc.project)} · ${esc(doc.id)}</span>`
      : `<span title="${esc(d.job_description || rd.job.description)}"><i class="hd" style="background:${hostLook(rd.host).color}"></i>${esc(rd.host)} · ${esc(rd.job.id)} · ${esc(d.agent || rd.job.agent)}</span>`,
    step != null ? `<span>step ${step + 1}</span>` : '',
    d.minutes ? `<span>${d.minutes} min read</span>` : '',
    (d.mtime || doc.mtime) ? `<span>updated ${age(d.mtime || doc.mtime)} ago</span>` : '',
  ].join('');
  document.getElementById('rdCopy').disabled = !rd.data;
  document.getElementById('rdDownload').disabled = !rd.data;
}
function renderReaderLoading() {
  document.getElementById('rdProgress').style.transform = 'scaleX(0)';
  rdBody.innerHTML = `<div class="rd-grid"><div class="rd-state rd-skel" aria-label="Loading document" role="status">
    <i class="h"></i>${[96, 88, 93, 60, 0, 91, 97, 85, 70].map(w => w ? `<i style="width:${w}%"></i>` : '<br>').join('')}</div></div>`;
  rdBody.scrollTop = 0;
}
function renderReaderError(message) {
  rdBody.innerHTML = `<div class="rd-grid"><div class="rd-state rd-error" role="alert"><b>Couldn’t open this document</b>
    <code>${esc(message)}</code><br><button class="rd-retry" id="rdRetry">Try again</button></div></div>`;
  document.getElementById('rdRetry').addEventListener('click', () => { renderReaderLoading(); loadDoc(++rd.req); });
}
function renderReaderBody() {
  const d = rd.data;
  const toc = (d.toc || []).filter(x => x.id && x.level <= 3);
  const showToc = toc.length >= 3, top = Math.min(...toc.map(x => x.level));
  rdBody.innerHTML = `<div class="rd-grid${showToc ? ' has-toc' : ''}">
    ${showToc ? `<details class="rd-toc"><summary>Contents<span>${toc.length}</span></summary><nav aria-label="Contents"><p class="lbl">Contents</p>
      ${toc.map(x => `<a href="#doc-${esc(x.id)}" class="l${x.level - top + 1}">${esc(x.text)}</a>`).join('')}</nav></details>` : ''}
    <article class="prose"></article></div>`;
  const prose = rdBody.querySelector('.prose');
  prose.innerHTML = d.html;   // rendered server-side with raw HTML escaped
  if (d.truncated) prose.insertAdjacentHTML('beforeend', '<p class="rd-note">This document was truncated for the reader. Download the Markdown for the full text.</p>');
  tidyProse(prose);
  syncTocMode();
  fitTables();
  if (document.fonts) document.fonts.ready.then(fitTables);
  rd.tocLinks = [...rdBody.querySelectorAll('.rd-toc a')]
    .map(a => ({ a, h: document.getElementById(a.getAttribute('href').slice(1)) }))
    .filter(x => x.h);
  rd.tocCurrent = null;
  rdBody.scrollTop = Number(store('sessionStorage','fleet.reader.scroll.' + rd.key)) || 0;
  onReaderScroll();
}
// heading ids are prefixed so a heading called "panel" or "legend" can't collide with the page's own ids
function tidyProse(prose) {
  for (const el of prose.querySelectorAll('[id]')) el.id = 'doc-' + el.id;
  for (const a of prose.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (href.startsWith('#')) a.setAttribute('href', '#doc-' + href.slice(1));
    else if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    else if (rd.source === 'library') {
      try {
        const url = new URL(href, location.origin + '/' + rd.doc.id);
        const id = decodeURIComponent(url.pathname.slice(1));
        if (url.origin === location.origin && libraryDocs.some(doc => doc.project === rd.doc.project && doc.id === id)) {
          a.dataset.libraryDoc = id;
          a.href = '#';
        }
      } catch (error) { /* leave an invalid link untouched */ }
    }
  }
  for (const table of prose.querySelectorAll('table')) {
    // single tokens (ids, codes) stay on one line; figures get tabular digits
    for (const cell of table.querySelectorAll('td')) {
      const text = cell.textContent.trim();
      if (text.length <= 24 && !/\s/.test(text)) cell.classList.add('nw');
      if (/^[−–+\-]?[£$€]?\d[\d.,]*\s*(%|¢|s|ms|B|KB|MB)?$/.test(text)) cell.classList.add('num');
    }
    const wrap = document.createElement('div'), scroller = document.createElement('div');
    wrap.className = 'rd-table'; scroller.className = 'rd-scroller'; scroller.tabIndex = 0;
    table.replaceWith(wrap); wrap.appendChild(scroller); scroller.appendChild(table);
    scroller.addEventListener('scroll', () => edgeFades(scroller), { passive: true });
  }
  for (const pre of prose.querySelectorAll('pre')) pre.tabIndex = 0;
}
function syncTocMode() {
  const toc = rdBody.querySelector('.rd-toc');
  if (toc) toc.open = WIDE.matches;
}
WIDE.addEventListener('change', syncTocMode);
// give tables that don't fit the 68ch measure a wider column (wide screens only; phones scroll them)
function fitTables() {
  const grid = rdBody.querySelector('.rd-grid');
  if (!grid) return;
  const scrollers = [...grid.querySelectorAll('.rd-scroller')];
  grid.classList.remove('wide');
  if (scrollers.some(s => s.scrollWidth > s.clientWidth + 1)) grid.classList.add('wide');
  scrollers.forEach(edgeFades);
}
// fade the edge a table can still scroll towards, so a clipped column reads as "more this way"
function edgeFades(s) {
  const wrap = s.parentElement, max = s.scrollWidth - s.clientWidth;
  wrap.classList.toggle('more-l', s.scrollLeft > 1);
  wrap.classList.toggle('more-r', s.scrollLeft < max - 1);
}
window.addEventListener('resize', () => { if (!reader.hidden) fitTables(); });

// reads first, then writes, so a scroll frame never forces a second layout
function onReaderScroll() {
  rd.raf = 0;
  const top = rdBody.scrollTop, max = rdBody.scrollHeight - rdBody.clientHeight;
  const current = currentTocLink(top, max);
  rdProgress.style.transform = `scaleX(${max > 0 ? clamp(top / max, 0, 1) : 1})`;
  if (current !== rd.tocCurrent) {
    if (rd.tocCurrent) rd.tocCurrent.removeAttribute('aria-current');
    if (current) current.setAttribute('aria-current', 'location');
    rd.tocCurrent = current;
  }
}
function currentTocLink(top, max) {
  const links = rd.tocLinks;
  if (!links.length) return null;
  if (max > 0 && top >= max - 2) return links[links.length - 1].a;
  const line = rdBody.getBoundingClientRect().top + 110;
  let current = links[0].a;
  for (const { a, h } of links) {
    if (h.getBoundingClientRect().top > line) break;
    current = a;
  }
  return current;
}
function saveReaderScroll() {
  if (rd.key && rd.data) store('sessionStorage','fleet.reader.scroll.' + rd.key, String(Math.round(rdBody.scrollTop)));
}
rdBody.addEventListener('scroll', () => { if (!rd.raf) rd.raf = requestAnimationFrame(onReaderScroll); }, { passive: true });
addEventListener('pagehide', saveReaderScroll);
rdBody.addEventListener('click', ev => {
  const linked = ev.target.closest('a[data-library-doc]');
  if (linked) {
    ev.preventDefault();
    const doc = libraryDocs.find(item => item.project === rd.doc.project && item.id === linked.dataset.libraryDoc);
    if (doc) openLibraryReader(doc);
    return;
  }
  const a = ev.target.closest('a[href^="#"]');
  if (!a) return;
  const target = document.getElementById(a.getAttribute('href').slice(1));
  if (!target) return;
  ev.preventDefault();
  target.scrollIntoView({ block: 'start', behavior: REDUCED ? 'auto' : 'smooth' });
  const toc = a.closest('.rd-toc');
  if (toc && !WIDE.matches) toc.open = false;
});
reader.addEventListener('click', ev => { if (ev.target.closest('[data-close]')) closeReader(); });
document.getElementById('rdTheme').addEventListener('click', () => {
  const theme = rdSheet.dataset.theme === 'dark' ? 'paper' : 'dark';
  setReaderTheme(theme);
  store('localStorage', 'fleet.reader.theme', theme);
});
document.getElementById('rdCopy').addEventListener('click', ev => {
  if (!rd.data) return;
  const b = ev.currentTarget, label = b.querySelector('.lb');
  const done = () => { label.textContent = 'Copied'; b.classList.add('ok'); setTimeout(() => { label.textContent = 'Copy'; b.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(rd.data.markdown).then(done, () => fallbackCopy(rd.data.markdown, done));
  else fallbackCopy(rd.data.markdown, done);
});
document.getElementById('rdDownload').addEventListener('click', () => {
  if (!rd.data) return;
  const prefix = rd.source === 'library' ? rd.doc.project : rd.job.id;
  const base = `${prefix}-${(rd.data.name || rd.doc.name).replace(/\.(md|markdown|mdx)$/i, '')}`.replace(/[^\w.-]+/g, '-').replace(/-+/g, '-');
  const url = URL.createObjectURL(new Blob([rd.data.markdown], { type: 'text/markdown;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: base + '.md' });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
// keep Tab inside the open reader
reader.addEventListener('keydown', ev => {
  if (ev.key !== 'Tab') return;
  const items = [...reader.querySelectorAll('button:not(:disabled),a[href],summary,[tabindex="0"]')].filter(el => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && (document.activeElement === first || document.activeElement === rdSheet)) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
});

// ------------------------------------------------------------------ legend, stats, feed
function renderLegend() {
  const body = document.getElementById('legendBody');
  if (!hosts.length) { body.innerHTML = '<div class="crew"><small>No hosts reported yet.</small></div>'; return; }
  body.innerHTML = hosts.map(h => {
    const look = hostLook(h.name);
    const jobs = h.jobs || [];
    const running = jobs.filter(j => j.status === 'running').length, sessions = (h.sessions || []).length;
    const sub = h.ok
      ? `${look.label} ·${running ? running + ' working' : jobs.length ? jobs.length + ' job' + (jobs.length === 1 ? '' : 's') : 'idle'}${sessions ? ` · ${sessions} live` : ''}`
      : `offline — ${trunc((h.error || '').replace(/^[^:]+:\s*/, ''), 60)}`;
    return `<div class="crew${h.ok ? '' : ' off'}" title="${esc(h.ok ? h.name : h.error || 'unreachable')}">
      <canvas data-host="${esc(h.name)}"></canvas>
      <div class="who"><b style="color:${look.color}">${esc(h.name)}</b><small>${esc(sub)}</small></div><i class="st"></i></div>`;
  }).join('') + `<div class="agents"><span><i class="visor"></i>visor band · Claude</span><span><span class="eyes"><i></i><i></i></span>twin eyes · Codex</span></div>`;
  for (const cv of body.querySelectorAll('canvas[data-host]')) {
    const h = hosts.find(x => x.name === cv.dataset.host);
    miniBot(cv, hostLook(cv.dataset.host), 'claude', h && !h.ok ? 'off' : 'normal');
  }
}
function renderStats() {
  const count = { running: 0, queued: 0, done: 0, failed: 0, stalled: 0 }, live = { working: 0, idle: 0 };
  for (const e of ents.values()) {
    const tally = isSession(e) ? live : count;
    if (tally[e.job.status] !== undefined) tally[e.job.status]++;
  }
  document.getElementById('stats').innerHTML = `
    ${live.working + live.idle ? `<span class="chip sess" title="interactive Claude Code / Codex sessions"><i></i><b>${live.working + live.idle}</b> live${live.idle ? `<span class="opt"> · ${live.idle} waiting</span>` : ''}</span>` : ''}
    <span class="chip"><i style="background:var(--run)"></i><b>${count.running}</b> working</span>
    <span class="chip opt"><i style="background:var(--warn)"></i><b>${count.queued}</b> queued</span>
    <span class="chip opt"><i style="background:var(--ok)"></i><b>${count.done}</b> done</span>
    <span class="chip"><i style="background:var(--bad)"></i><b>${count.failed + count.stalled}</b> need you</span>
    ${retiredCount ? `<button class="chip restore" id="toggleFinished" title="Show finished jobs that have left the deck"><b>${retiredCount}</b> finished · show</button>`
      : showFinished ? '<button class="chip restore" id="toggleFinished" title="Let finished jobs leave the deck again">hide finished</button>' : ''}
    ${hiddenCount ? `<button class="chip restore" id="restoreDismissed" title="Show dismissed agents again"><b>${hiddenCount}</b> hidden · show</button>` : ''}`;
}
document.getElementById('stats').addEventListener('click', ev => {
  if (ev.target.closest('#restoreDismissed')) restoreDismissed();
  else if (ev.target.closest('#toggleFinished')) toggleFinished();
});
function renderLive() {
  const el = document.getElementById('live');
  if (DEMO) { el.className = 'live demo'; el.innerHTML = '<i></i><span>demo data</span>'; return; }
  if (live.ok) { el.className = 'live'; el.innerHTML = `<i></i><span>live · ${clock(Date.now() / 1000)}</span>`; }
  else { el.className = 'live bad'; el.innerHTML = `<i></i><span>${everLoaded ? 'server lost · retrying' : 'no server'}</span>`; }
}
function collectEvents() {
  const fresh = [];
  for (const h of hosts) for (const j of h.jobs || []) for (const ev of j.events || []) {
    if (ev.kind === 'todos' || ev.kind === 'session' || !ev.summary) continue;
    const k = `${h.name}:${j.id}:${ev.ts}:${ev.kind}:${ev.summary}`;
    if (seenEvents.has(k)) continue;
    seenEvents.add(k);
    fresh.push({ host: h.name, id: j.id, label: j.id, ev, isNew: feedSeeded });
  }
  for (const h of hosts) for (const s of h.sessions || []) for (const ev of s.events || []) {
    if (ev.kind === 'todos' || !ev.summary || !s.project) continue;
    const k = `${h.name}:${s.id}:${ev.ts}:${ev.kind}:${ev.summary}`;
    if (seenEvents.has(k)) continue;
    seenEvents.add(k);
    fresh.push({ host: h.name, id: s.id, label: `${s.agent}·${shortId(s.id)}`, live: true, ev, isNew: feedSeeded });
  }
  fresh.sort((a, b) => a.ev.ts - b.ev.ts);
  for (const f of fresh) feed.unshift(f);
  if (!feedSeeded) { feed.sort((a, b) => b.ev.ts - a.ev.ts); setFeedSeeded(true); }
  feed.length = Math.min(feed.length, 60);
  if (seenEvents.size > 5000) { seenEvents.clear(); for (const f of feed) seenEvents.add(`${f.host}:${f.id}:${f.ev.ts}:${f.ev.kind}:${f.ev.summary}`); }
}
// Commands, paths and patterns read better in monospace; agent prose reads better in Inter.
const CODE_TOOLS = new Set(['bash', 'edit', 'read', 'search', 'web']);
function isCodeEvent(ev) { return ev.kind === 'tool' && CODE_TOOLS.has(ev.tool); }

function renderFeed() {
  const ul = document.getElementById('feedList');
  if (!feed.length) { ul.innerHTML = '<li class="empty">Nothing has happened yet.</li>'; return; }
  ul.innerHTML = feed.map(f => `<li class="${f.ev.kind === 'error' ? 'err ' : ''}${f.isNew ? 'new' : ''}" data-key="${esc(f.host + ':' + f.id)}">
    <i style="background:${hostLook(f.host).color}"></i>${f.live ? '<span class="lv">LIVE</span>' : ''}<b>${esc(f.host)}:${esc(f.label)}</b><span class="k">${esc(eventIcon(f.ev))}</span>
    <span class="s${isCodeEvent(f.ev) ? ' code' : ''}">${esc(f.ev.summary)}</span><time>${clock(f.ev.ts)}</time></li>`).join('');
  for (const f of feed) f.isNew = false;
}
document.getElementById('feedList').addEventListener('click', ev => {
  const li = ev.target.closest('li[data-key]');
  if (li && ents.has(li.dataset.key)) select(li.dataset.key);
});
function updateHint() {
  const hint = document.getElementById('hint');
  if (ents.size) { hint.hidden = true; return; }
  hint.hidden = false;
  hint.innerHTML = everLoaded
    ? `<h2>The deck is quiet</h2><p>No jobs on any host in the last day. Send one with</p><p><code>fleet send -H worker -p myrepo -d "…" -C ~/src/myrepo -s "…"</code></p><p><a href="?demo">See the demo crew</a></p>`
    : `<h2>Waiting for the fleet server</h2><p>This page is served by <code>fleet web</code>. It couldn&apos;t reach <code>/api/stream</code> yet.</p><p><a href="?demo">Open the demo instead</a></p>`;
}

// ------------------------------------------------------------------ data sources
// Server-sent events: a full state document whenever anything changes, pings in between.
// EventSource reconnects by itself after an error.
function stream() {
  const source = new EventSource('/api/stream');
  source.addEventListener('state', (message) => {
    setLive({ ok: true, at: Date.now(), err: null });
    applyState(JSON.parse(message.data));
  });
  source.addEventListener('ping', () => {
    setLive({ ok: true, at: Date.now(), err: null });
    renderLive();
  });
  source.onerror = () => {
    setLive({ ok: false, at: live.at, err: 'stream disconnected — reconnecting' });
    renderLive(); updateHint();
  };
}

// Demo only: a small Markdown renderer producing the same shapes as the server's markdown-it
// (heading ids, tables, task lists, footnotes). Everything is escaped before any tag is added.
function mdInline(s) {
  return s.split(/(`[^`]+`)/).map(part => part.length > 1 && part.startsWith('`') && part.endsWith('`')
    ? `<code>${esc(part.slice(1, -1))}</code>`
    : esc(part)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
      .replace(/\[\^(\w+)\]/g, '<sup class="footnote-ref"><a href="#fn-$1" id="fnref-$1">[$1]</a></sup>')
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>')).join('');
}
function mdList(lines) {
  const indent = l => l.match(/^\s*/)[0].length, bullet = /^\s*([-*]|\d+\.)\s+/;
  const base = indent(lines[0]), items = [];
  for (const l of lines) {
    if (indent(l) <= base && bullet.test(l)) items.push({ text: l.replace(bullet, ''), kids: [] });
    else if (items.length) items[items.length - 1].kids.push(l);
  }
  const tag = /^\s*\d+\./.test(lines[0]) ? 'ol' : 'ul';
  let tasks = false;
  const lis = items.map(it => {
    const m = it.text.match(/^\[([ xX])\]\s+(.*)$/);
    if (m) tasks = true;
    const box = m ? `<input class="task-list-item-checkbox" disabled type="checkbox"${m[1] === ' ' ? '' : ' checked'}>` : '';
    return `<li${m ? ' class="task-list-item"' : ''}>${box}${mdInline(m ? m[2] : it.text)}${it.kids.length ? mdList(it.kids) : ''}</li>`;
  }).join('');
  return `<${tag}${tasks ? ' class="contains-task-list"' : ''}>${lis}</${tag}>`;
}
function mdToHtml(md) {
  const lines = md.split('\n'), out = [], toc = [], notes = [], used = new Set();
  const slug = text => { const b = text.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-') || 'section'; let k = b, n = 1; while (used.has(k)) k = `${b}-${n++}`; used.add(k); return k; };
  const starts = l => /^(#{1,6}\s|```|>|\||\s*([-*]|\d+\.)\s|\[\^\w+\]:)/.test(l) || /^(-{3,}|\*{3,})\s*$/.test(l);
  const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  let i = 0, m;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if ((m = l.match(/^(#{1,6})\s+(.*)$/))) {
      const level = m[1].length, text = m[2].trim();
      if (level <= 3) { const id = slug(text); toc.push({ level, id, text }); out.push(`<h${level} id="${id}">${mdInline(text)}</h${level}>`); }
      else out.push(`<h${level}>${mdInline(text)}</h${level}>`);
      i++;
    } else if (l.startsWith('```')) {
      const lang = l.slice(3).trim(), buf = [];
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) buf.push(lines[i]);
      i++;
      out.push(`<pre><code${lang ? ` class="language-${esc(lang)}"` : ''}>${esc(buf.join('\n'))}\n</code></pre>`);
    } else if (/^(-{3,}|\*{3,})\s*$/.test(l)) { out.push('<hr>'); i++; }
    else if (l.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith('>')) buf.push(lines[i++].replace(/^>\s?/, ''));
      out.push(`<blockquote>${mdToHtml(buf.join('\n')).html}</blockquote>`);
    } else if (l.startsWith('|')) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith('|')) rows.push(lines[i++]);
      const align = cells(rows[1] || '').map(c => /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : '');
      const at = k => align[k] ? ` style="text-align:${align[k]}"` : '';
      out.push(`<table><thead><tr>${cells(rows[0]).map((c, k) => `<th${at(k)}>${mdInline(c)}</th>`).join('')}</tr></thead><tbody>${
        rows.slice(2).map(r => `<tr>${cells(r).map((c, k) => `<td${at(k)}>${mdInline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
    } else if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const buf = [];
      while (i < lines.length && (/^\s*([-*]|\d+\.)\s/.test(lines[i]) || /^\s{2,}\S/.test(lines[i]))) buf.push(lines[i++]);
      out.push(mdList(buf));
    } else if ((m = l.match(/^\[\^(\w+)\]:\s*(.*)$/))) { notes.push({ id: m[1], text: m[2] }); i++; }
    else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !(buf.length && starts(lines[i]))) buf.push(lines[i++].trim());
      out.push(`<p>${mdInline(buf.join(' '))}</p>`);
    }
  }
  if (notes.length) out.push(`<hr class="footnotes-sep"><section class="footnotes"><ol class="footnotes-list">${notes.map(n =>
    `<li id="fn-${esc(n.id)}" class="footnote-item"><p>${mdInline(n.text)} <a href="#fnref-${esc(n.id)}" class="footnote-backref">↩︎</a></p></li>`).join('')}</ol></section>`);
  return { html: out.join('\n'), toc };
}

const DEMO_DOCS = {
  shadowDiff: `# Shadow-parse vs Textract: line-item diff

Ran the shadow parser over **60 invoices** from today’s sample (12 suppliers) and diffed every line item against the Textract baseline. 51 invoices match exactly; 9 have at least one mismatch, 23 rows in total.

> The shadow parser is ahead on credit notes and multi-page invoices, but still trails Textract on handwritten quantity corrections. Nothing here blocks the rollout to the 12 pilot restaurants.

## Summary

| Measure | Shadow parser | Textract | Δ |
|---|---:|---:|---:|
| Invoices parsed | 60 | 60 | 0 |
| Exact line-item match | 51 | 47 | +4 |
| Rows with a mismatch | 23 | 31 | −8 |
| Median latency (s) | 2.8 | 6.1 | −3.3 |
| Cost per invoice (¢) | 0.9 | 1.5 | −0.6 |

## Method

1. Pulled the sample with \`fleet-sample --date 2026-09-25 --limit 60\` and hashed every page image so both parsers saw identical input.
2. Ran both parsers with the same images and no retries.
3. Normalised supplier codes and units before comparing:
   - \`KG\`, \`kg\` and \`Kilo\` all become \`kg\`
   - pack sizes such as \`6x1L\` split into quantity and unit
4. Compared line by line on *description, quantity, unit price and line total*, with a 0.5 % tolerance on totals.

\`\`\`python
def rows_match(a: LineItem, b: LineItem, tolerance: Decimal = Decimal("0.005")) -> bool:
    if normalise(a.description) != normalise(b.description):
        return False
    if a.quantity != b.quantity:
        return False
    return abs(a.line_total - b.line_total) <= tolerance * abs(b.line_total)
\`\`\`

## Mismatches by cause

### Handwritten corrections

Seven rows. A driver crossed out the printed quantity and wrote a new one beside it; Textract read the handwriting in four of them and we read none. This is the only category where the shadow parser is clearly behind.

| Invoice | Supplier | Line | Printed | Written | Shadow | Textract |
|---|---|---|---:|---:|---:|---:|
| INV-1001 | Supplier A | Chicken thigh 2 kg | 6 | 4 | 6 | 4 |
| INV-1001 | Supplier A | Rapeseed oil 20 L | 2 | 1 | 2 | 1 |
| INV-1002 | Supplier B | Double cream 2 L | 10 | 8 | 10 | 10 |
| INV-1003 | Supplier B | Free-range eggs ×180 | 3 | 2 | 3 | 2 |
| INV-1006 | Supplier C | Lemons (box of 80) | 2 | 1 | 2 | 1 |

### Credit notes

Textract flipped negative line totals to positive in five of six credit notes. The shadow parser keeps the sign because it reads the document type before any line items[^1]. This matters more than the row count suggests: a flipped credit note overstates spend twice, once for the credit and once for the original.

### Multi-page invoices

Textract dropped the carried-forward subtotal on page two of three invoices, so its totals were short by exactly the page-one amount:

\`\`\`text
INV-1004  page 1 subtotal   £412.60   carried forward: missing
INV-1004  page 2 total      £198.35   expected £610.95
INV-1005  page 1 subtotal   £1,084.20 carried forward: missing
\`\`\`

### Unit-price rounding

Four rows differ by a penny because the supplier prints unit prices to three decimal places. Both parsers are arguably right. The relative tolerance should absorb these, and does, except for line totals under £1, where half a percent is less than a penny.

## What I changed

- Added \`luc_tolerance_abs = Decimal("0.01")\` alongside the relative tolerance, and a test for a £0.84 line.
- Kept document-type detection ahead of line parsing, with a regression test for each of the six credit notes.
- Did **not** touch handwriting: that needs a model change, not a rule, and belongs in its own job.

## Checklist

- [x] Sample pulled and hashed
- [x] Both parsers run on identical images
- [x] Mismatches clustered by cause
- [ ] Taxonomy update drafted (next step)
- [ ] Findings reviewed by the invoices team

## Open questions

1. Should handwritten corrections be *flagged for review* rather than parsed? A flag is cheap and safe; parsing them wrong is expensive.
2. When printed and written quantities disagree and there is no delivery note, which one do we trust?
3. Is a penny of rounding worth surfacing to the restaurant at all, or should it be silently absorbed?

---

The full row-level list is in the outbox as \`mismatches.md\`.

[^1]: \`DocumentKind.CREDIT_NOTE\` is detected from the header block before any line items are read, so the sign is applied once, at the end.`,

  mismatches: `# Mismatches (23 rows)

Row-level list behind the step 3 report. Amounts in GBP.

| # | Invoice | Supplier | Cause | Shadow | Textract | Expected |
|---:|---|---|---|---:|---:|---:|
| 1 | INV-1001 | Supplier A | handwriting | 6 | 4 | 4 |
| 2 | INV-1001 | Supplier A | handwriting | 2 | 1 | 1 |
| 3 | INV-1002 | Supplier B | handwriting | 10 | 10 | 8 |
| 4 | CN-1001 | Supplier B | credit sign | −12.40 | 12.40 | −12.40 |
| 5 | CN-1002 | Supplier A | credit sign | −3.10 | 3.10 | −3.10 |
| 6 | INV-1004 | Supplier C | carried forward | 610.95 | 198.35 | 610.95 |
| 7 | INV-1006 | Supplier C | rounding | 0.84 | 0.85 | 0.84 |
| 8 | INV-1007 | Supplier C | rounding | 0.63 | 0.62 | 0.63 |

Rows 9–23 follow the same four causes; see the step report for the breakdown.`,

  diffMethod: `# How the Textract diff works

A short note so the next run can reuse the method.

- Both parsers get the **same page images**, hashed before the run.
- Units and supplier codes are normalised first; see \`normalise()\` in \`shadow/diff.py\`.
- Totals use a relative tolerance of 0.5 % *and* an absolute floor of one penny.

\`\`\`bash
python -m shadow.diff --sample samples/2026-09-25 --baseline textract --out outbox/
\`\`\``,

  article: `# PAR by weekday

Par levels tell you how much of each item to keep on hand. Most kitchens set one number and live with it, but Friday is not Tuesday. **PAR by weekday** lets you set a different par for each day, so you order for the service you are actually going to have.

## When to use it

- Your covers swing a lot across the week (for example, quiet Mondays and a packed Saturday).
- You receive deliveries on fixed days and want each order to cover the gap until the next one.
- You waste perishables early in the week and run short at the weekend.

## Setting it up

1. Open **Inventory → Par levels** and pick an item.
2. Switch *Same every day* to **By weekday**.
3. Enter a par for each day. Leave a day blank to fall back to the item’s default.

| Day | Covers (avg) | Par: salmon fillets |
|---|---:|---:|
| Monday | 62 | 8 |
| Friday | 148 | 20 |
| Saturday | 171 | 24 |

> Start with your busiest day and your quietest day. The days in between are usually obvious once those two are right.

## What changes in ordering

Suggested orders now use the par for the day the delivery **covers**, not the day you place it. If Tuesday’s delivery has to last until Friday, the suggestion uses the highest par across those days.`,

  deckLayout: `# Deck layout notes

How the rooms are laid out, so the next change doesn’t fight the walk paths.

## Room grid

| Prop | Tiles (x, y) | Who stands there |
|---|---|---|
| Terminals | 0.9–6.3, 0.25 | bash |
| Whiteboard | 6.9–9.5, 0 | plan, delegate fallback |
| Workbench | 4.8–7.6, 4.2 | edit |
| Document press | 11.1–11.9, 2.9–5.5 | nobody; documents land here |

## Walk paths

- Two aisles, at y = 2.6 and y = 7.3.
- Crossings at x = 3.6 and x = 8.7, so nobody walks through the workbench.

## Still to do

- [x] Trays tinted by host
- [x] Printed-sheet animation
- [ ] Tune stack height at 390 px`,

  rootCause: `# Root cause: flaky invoice upload e2e

The upload test failed about one run in twelve. The poller and the upload handler both called \`commit()\` on the same session, and whichever landed second raised \`StaleDataError\`.

## Fix

\`\`\`python
with upload_lock(invoice.id):
    session.refresh(invoice)
    invoice.status = Status.PARSED
    session.commit()
\`\`\`

## Evidence

| Runs | Before | After |
|---:|---:|---:|
| 50 | 4 failures | 0 failures |
| 200 | 17 failures | 0 failures |

- [x] Reproduced 20× before the fix
- [x] 250 green runs after`,
};

function stepReport(job, i) {
  const s = job.steps[i];
  return `# Step ${i + 1}: ${s.title}

${s.result || 'Done.'}

## What I did

- Read the code paths involved and the existing tests before changing anything.
- Made the change in small commits in \`${job.cwd}\`, re-running the affected tests after each one.
- Checked the result against the step’s goal rather than just the tests.

## Checks

| Check | Command | Result |
|---|---|---|
| Unit tests | \`pytest -q\` | 142 passed |
| Lint | \`ruff check .\` | clean |
| Types | \`mypy .\` | no issues |

## Left open

Nothing blocking for the next step.`;
}

let demoDoc = null;   // set by demoSource: (host, job, id) → document, as /api/doc would return it

function demoSource() {
  const rand = seeded(42);
  const pick = arr => arr[Math.floor(rand() * arr.length)];
  const now = () => Date.now() / 1000;
  const SUMMARIES = {
    bash: ['pytest tests/invoices/test_upload.py -q', 'npx vitest run src/v2/suppliers', 'git diff --stat', 'ruff check app/suppliers', 'make migrate', 'npm run lint -- --fix', 'git log --oneline -5',
      'git commit -m "Port supplier filters to the V2 route"', 'git push -u origin HEAD', 'npm install', 'npm run build', 'docker compose up -d db', 'sleep 30',
      'curl -s https://api.github.com/repos/example/demo-store/pulls', 'ssh node-b fleet ls', 'psql -c "select count(*) from invoices"', 'ls -la fleet/web', 'mypy app/invoices',
      'git add -A', 'python scripts/export_suppliers.py --dry-run', 'cat package.json', 'git checkout -b fix/upload-poller'],
    edit: ['frontend/src/v2/routes/suppliers.tsx', 'app/invoices/parser/luc.py', 'fleet/web/index.html', 'docs/guides/onboarding.md', 'app/suppliers/adapters.py', 'tests/test_fleetd_parsers.py'],
    read: ['app/suppliers/views.py', 'docs/adr/0002-module-refactor.md', 'frontend/src/v2/router.tsx', 'fleet/remote/fleetd.py', 'invoices/sample-0412.json'],
    search: ['SupplierRow', '**/*.spec.ts', 'luc_tolerance', 'docs/**/*.png'],
    web: ['https://tanstack.com/router/latest/docs/guide/data-loading', 'https://docs.python.org/3/library/decimal.html', 'https://playwright.dev/docs/screenshots'],
    think: ['Weighing whether the tolerance should be relative to the line total…', 'The flake only happens when the poller fires twice…', 'Two ways to split the loader; the second keeps parity…'],
    plan: ['{"todos": […]}'],
    delegate: ['Find every caller of get_active_restaurants', 'List routes still on the legacy table'],
  };
  // shaped like fleetd's events: the Claude tool name, its main argument, and for shell calls sometimes Claude's description
  const INTENTS = { 'make migrate': 'Run the database migrations', 'git diff --stat': 'Show what changed', 'ruff check app/suppliers': 'Lint the suppliers module',
    'git push -u origin HEAD': 'Push the branch', 'sleep 30': 'Wait for the server to come up' };
  const demoTool = kind => {
    const summary = pick(SUMMARIES[kind]);
    const name = { bash: 'Bash', edit: DOC_FILE.test(summary) ? 'Write' : 'Edit', read: 'Read', web: 'WebFetch', think: '', plan: 'TodoWrite', delegate: 'Agent' }[kind]
      ?? (summary.includes('*') ? 'Glob' : 'Grep');
    const ev = { kind: 'tool', tool: kind, name, summary };
    if (kind === 'bash' && INTENTS[summary] && rand() < 0.7) ev.intent = INTENTS[summary];
    return ev;
  };
  const WEIGHTS = [['bash', 8], ['edit', 4], ['read', 4], ['search', 2], ['web', 1], ['think', 3], ['plan', 1], ['delegate', 1.2]];
  // ?debug&pin=ship,read,…: running jobs only do these activities (job i does the i-th, round the list), for close-ups;
  // an entry like type+ship alternates between them
  const PINS = DEBUG && QS.get('pin') ? QS.get('pin').split(',') : null;
  const pinned = act => {
    for (let k = 0; k < 400; k++) { const ev = demoTool(pickTool()); if (activityOf(ev) === act) return ev; }
    return demoTool('bash');
  };
  const pickTool = () => { let x = rand() * WEIGHTS.reduce((s, w) => s + w[1], 0); for (const [k, w] of WEIGHTS) { if ((x -= w) <= 0) return k; } return 'bash'; };
  const RESULTS = ['Done — 14 files touched, tests green.', 'Found the race: poller and upload both call commit(). Patched with a lock.', 'Parity confirmed against master for all 6 filters.', 'Wrote findings to outbox/mismatches.md (23 rows).', 'All steps reproduced locally; nothing left open.'];
  const TODO_POOL = ['Read the failing test', 'Reproduce locally', 'Write the fix', 'Run the affected tests', 'Update the docs', 'Check parity with master', 'Summarise for the orchestrator'];
  const specs = [
    ['node-a', 'demo-store', 'Migrate the suppliers list to a V2 React route', 'claude', 'claude-opus-5-5', ['Map legacy supplier list behaviour', 'Build SuppliersRoute with a TanStack loader', 'Port filters and sort', 'Add parity tests', 'Run vitest + e2e', 'Write the PR description'], 2, 'running'],
    ['node-b', 'demo-store', 'Fix the flaky invoice upload e2e test', 'codex', 'gpt-5-codex', ['Reproduce the flake 20×', 'Find the race in the upload poller', 'Patch and rerun 50×', 'Summarise the root cause'], 1, 'running'],
    ['node-c', 'demo-store', 'Apply review feedback', 'claude', 'claude-opus-5-5', ['Collect review threads', 'Apply naming fixes', 'Re-run affected tests'], 0, 'running'],
    ['node-c', 'demo-parser', 'Shadow-parse 60 invoices and diff against Textract', 'claude', 'claude-opus-5-5', ['Pull today’s invoice sample', 'Run the shadow parser', 'Diff line items', 'Cluster mismatches', 'Write findings to outbox', 'Draft taxonomy update', 'Summarise'], 3, 'running'],
    ['node-b', 'demo-parser', 'Tune LUC tolerance for credit notes', 'codex', 'gpt-5-codex', ['Add a failing test for negative LUC', 'Adjust the tolerance', 'Run the parser suite'], 1, 'failed'],
    ['node-a', 'agent-fleet', 'Isometric deck view for fleet web', 'claude', 'claude-opus-5-5', ['Sketch the room layout', 'Draw androids per host', 'Walk-to-prop animation', 'Detail panel', 'Screenshot at 390px'], 2, 'running'],
    ['node-c', 'agent-fleet', 'Unit tests for the fleetd parsers', 'codex', 'gpt-5-codex', ['Claude stream fixtures', 'Codex exec fixtures', 'Runner lock tests'], 0, 'queued'],
    ['node-b', 'demo-docs', 'Refresh the onboarding guide screenshots', 'claude', 'claude-opus-5-5', ['List stale screenshots', 'Capture new ones', 'Update the markdown'], 1, 'stalled'],
    ['node-a', 'demo-docs', 'Support article: PAR by weekday', 'claude', 'claude-opus-5-5', ['Read the feature PR', 'Draft the article', 'Tighten the copy'], 3, 'done'],
  ];
  const t0 = now();
  function newTodos(job) { const start = Math.floor(rand() * 4); job.todos = TODO_POOL.slice(start, start + 3).map((text, i) => ({ text, status: i === 0 ? 'in_progress' : 'pending' })); }
  const jobs = specs.map(([host, project, description, agent, model, titles, cur, status], i) => {
    const id = hash(description).toString(16).padStart(8, '0').slice(0, 6);
    const job = {
      id, host, project, description, agent, model, status, cwd: `~/src/${project}`,
      permission: agent === 'claude' ? 'acceptEdits' : 'workspace-write',
      created_at: t0 - 3600 + i * 240, updated_at: t0 - (status === 'stalled' ? 1500 : 20),
      session_id: null, tmux: `tmux -L fleet attach -t fleet-${id}`, todos: [], events: [], activity: null, ticks: 0, documents: [],
      steps: titles.map((title, idx) => ({
        index: idx, title, result: null, started_at: null, finished_at: null,
        status: status === 'done' || idx < cur ? 'done' : idx > cur ? 'pending'
          : (status === 'running' || status === 'stalled') ? 'running' : status === 'failed' ? 'failed' : 'pending',
      })),
    };
    job.steps.forEach(s => { if (s.status === 'done') s.result = pick(RESULTS); });
    if (status === 'failed') job.steps[cur].result = 'test_negative_luc_credit_note still fails: expected -12.40, got -12.00 (tolerance applied before sign).';
    const at = (k, e) => job.events.push({ ts: t0 - 400 + k * 30 + i, step: Math.max(0, Math.min(cur, titles.length - 1)), ...e });
    at(0, { kind: 'job', status: 'queued', summary: `job created: ${description}` });
    if (status !== 'queued') for (let k = 1; k < 7; k++) at(k, demoTool(pickTool()));
    if (status === 'failed') at(8, { kind: 'error', summary: 'exit 1: pytest tests/test_luc.py' });
    if (status === 'done') at(9, { kind: 'job', status: 'done', summary: 'job done' });
    if (status === 'stalled') at(9, { kind: 'log', summary: 'runner exited unexpectedly (SIGKILL)' });
    if (status === 'running') newTodos(job);
    job.activity = [...job.events].reverse().find(e => e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') || null;
    return job;
  });

  // interactive CLI sessions, shaped like `fleetd sessions` output: a working Claude, an idle one, a working Codex
  const sessionSpecs = [
    ['node-a', 'agent-fleet', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000001', 'Show live CLI sessions on the deck', 'working', 1900],
    ['node-b', 'demo-store', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000002', 'Why does the stocktake import modal re-render twice?', 'idle', 2600],
    ['node-c', 'demo-parser', 'codex', 'gpt-6-sol', '00000000-0000-4000-8000-000000000003', 'review the unstaged changes are they correct and safe?', 'working', 900],
  ];
  const sessions = sessionSpecs.map(([host, project, agent, model, id, title, status, startedAgo], i) => {
    const cwd = `~/src/${project}`;
    const session = {
      id, host, agent, cwd, project, title, status, model, started_at: t0 - startedAgo,
      updated_at: t0 - (status === 'idle' ? 380 : 4), todos: [], events: [], activity: null,
      resume: `cd ${cwd} && ${agent === 'codex' ? 'codex resume' : 'claude --resume'} ${id}`,
    };
    for (let k = 0; k < 6; k++) session.events.push({ ...demoTool(pickTool()), ts: t0 - 700 + k * 50 + i });
    if (status === 'idle') session.events.push({ kind: 'text', summary: 'The modal re-renders because the loader returns a new object each time. Want me to memoise it or move the fetch up?', ts: session.updated_at });
    if (agent === 'claude' && status === 'working') session.todos = [
      { text: 'Discover sessions in fleetd', status: 'completed' }, { text: 'Carry them through the server', status: 'completed' },
      { text: 'Session androids on the deck', status: 'in_progress' }, { text: 'Screenshots', status: 'pending' }];
    session.activity = [...session.events].reverse().find(e => e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') || null;
    return session;
  });

  // documents: jobs carry metadata only, as from the server; the markdown stays here for demoDoc
  const docText = new Map();
  function addDoc(job, id, kind, name, step, markdown, mtime) {
    if (job.documents.some(d => d.id === id)) return;
    const path = kind === 'report' ? `~/.fleet/jobs/${job.id}/result-${step}.md` : kind === 'outbox' ? `~/.fleet/jobs/${job.id}/outbox/${name}` : `${job.cwd}/${name}`;
    job.documents.push({ id, kind, name, step, path, size: new TextEncoder().encode(markdown).length, mtime: mtime || now() });
    docText.set(`${job.host}:${job.id}:${id}`, markdown);
  }
  function addReport(job, i, mtime) { addDoc(job, `report-${i}`, 'report', `Step ${i + 1}: ${job.steps[i].title}`, i, stepReport(job, i), mtime); }
  const [suppliers, flaky, , shadow, luc, deck, , , article] = jobs;
  addReport(suppliers, 0, t0 - 2400); addReport(suppliers, 1, t0 - 1300);
  addDoc(suppliers, 'file-0', 'file', 'docs/suppliers-v2-parity.md', 1, DEMO_DOCS.deckLayout.replace('Deck layout notes', 'Suppliers V2 parity notes'), t0 - 1250);
  addReport(flaky, 0, t0 - 900);
  addReport(shadow, 0, t0 - 3000); addReport(shadow, 1, t0 - 2100);
  addDoc(shadow, 'file-0', 'file', 'notes/textract-diff-method.md', 2, DEMO_DOCS.diffMethod, t0 - 1500);
  addDoc(shadow, 'report-2', 'report', 'Step 3: Diff line items', 2, DEMO_DOCS.shadowDiff, t0 - 700);
  addDoc(shadow, 'outbox-mismatches.md', 'outbox', 'mismatches.md', null, DEMO_DOCS.mismatches, t0 - 650);
  addReport(luc, 0, t0 - 1800);
  addReport(deck, 0, t0 - 2600); addReport(deck, 1, t0 - 1400);
  addReport(article, 0, t0 - 5200);
  addDoc(article, 'file-0', 'file', 'drafts/par-by-weekday-v1.md', 1, DEMO_DOCS.article, t0 - 4800);
  addDoc(article, 'file-1', 'file', 'drafts/par-by-weekday-v2.md', 1, DEMO_DOCS.article, t0 - 4300);
  addReport(article, 1, t0 - 4000);
  addDoc(article, 'file-2', 'file', 'articles/par-by-weekday.md', 2, DEMO_DOCS.article, t0 - 3500);
  addReport(article, 2, t0 - 3300);
  addDoc(article, 'outbox-par-by-weekday.md', 'outbox', 'par-by-weekday.md', null, DEMO_DOCS.article, t0 - 3200);
  let tickCount = 0;
  demoDoc = async (host, jobId, id) => {
    await new Promise(resolve => setTimeout(resolve, 350));
    const job = jobs.find(j => j.host === host && j.id === jobId);
    const meta = job && job.documents.find(d => d.id === id);
    if (!meta) throw new Error(`no document ${id} on ${host}:${jobId}`);
    const markdown = docText.get(`${host}:${jobId}:${id}`), { html, toc } = mdToHtml(markdown);
    const words = markdown.split(/\s+/).filter(Boolean).length;
    return { ...meta, job: job.id, project: job.project, agent: job.agent, host, job_description: job.description,
      markdown, html, toc, words, minutes: Math.max(1, Math.round(words / 230)), truncated: false };
  };
  function push(job, e) {
    job.events.push({ ts: now(), step: job.steps.findIndex(s => s.status === 'running'), ...e });
    if (job.events.length > 15) job.events.splice(0, job.events.length - 15);
    job.updated_at = now();
    if (e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') job.activity = job.events[job.events.length - 1];
  }
  function tick() {
    tickCount++;
    if (tickCount === 3) { addDoc(deck, 'file-0', 'file', 'docs/deck-layout.md', 2, DEMO_DOCS.deckLayout); push(deck, { kind: 'tool', tool: 'edit', summary: 'docs/deck-layout.md' }); }
    if (tickCount === 7) addDoc(flaky, 'outbox-root-cause.md', 'outbox', 'root-cause.md', null, DEMO_DOCS.rootCause);
    for (const job of jobs) {
      job.ticks++;
      if (job.status === 'queued' && job.ticks > 7) {
        job.status = 'running'; job.steps[0].status = 'running'; job.steps[0].started_at = now(); newTodos(job);
        push(job, { kind: 'step', status: 'running', summary: job.steps[0].title });
        continue;
      }
      if (job.status === 'done' && job.ticks > 16) {   // re-queued: idles in the lounge for a while, then starts over
        job.steps.forEach(s => { s.status = 'pending'; s.result = null; });
        job.status = 'queued'; job.ticks = 0;
        push(job, { kind: 'job', status: 'queued', summary: `${job.steps.length} step(s) re-queued` });
        continue;
      }
      if (job.status !== 'running') continue;
      const i = jobs.indexOf(job);
      if (PINS) {
        const seq = PINS[i % PINS.length].split('+'), act = seq[Math.floor(tickCount / 5) % seq.length];   // type+ship: alternate every 10 s
        // a pinned test run alternates with its outcome: an error half the time, otherwise the next command
        if (act === 'test' && job.tested) push(job, rand() < 0.5 ? { kind: 'error', summary: 'exit 1: pytest -q (2 failed, 41 passed)' } : { kind: 'tool', tool: 'bash', name: 'Bash', summary: 'git add -A' });
        else push(job, pinned(act));
        job.tested = act === 'test' && !job.tested;
        continue;
      }
      // a test run is followed by its outcome: sometimes a failure, otherwise the agent simply carries on
      if (job.tested) {
        job.tested = false;
        if (rand() < 0.35) { push(job, { kind: 'error', summary: 'exit 1: 2 failed, 41 passed' }); continue; }
      }
      if (rand() < 0.55) {
        const tool = pickTool();
        const sameRoom = jobs.some(o => o !== job && o.project === job.project);
        const kind = tool === 'delegate' && !sameRoom ? 'read' : tool;
        if (kind === 'think' && rand() < 0.4) push(job, { kind: 'text', summary: pick(SUMMARIES.think) });
        else push(job, demoTool(kind));
        job.tested = activityOf(job.activity) === 'test';
      }
      if (rand() < 0.3 && job.todos.length) {
        const i = job.todos.findIndex(td => td.status !== 'completed');
        if (i >= 0) { job.todos[i].status = 'completed'; if (job.todos[i + 1]) job.todos[i + 1].status = 'in_progress'; }
      }
      if (rand() < 0.08) {
        const i = job.steps.findIndex(s => s.status === 'running');
        if (i < 0) continue;
        job.steps[i].status = 'done'; job.steps[i].finished_at = now(); job.steps[i].result = pick(RESULTS);
        addReport(job, i);
        push(job, { kind: 'step', status: 'done', summary: job.steps[i].result });
        if (job.steps[i + 1]) { job.steps[i + 1].status = 'running'; job.steps[i + 1].started_at = now(); newTodos(job); push(job, { kind: 'step', status: 'running', summary: job.steps[i + 1].title }); }
        else { job.status = 'done'; job.ticks = 0; job.todos = []; push(job, { kind: 'job', status: 'done', summary: 'job done' }); }
      }
    }
    for (const s of sessions) {
      if (s.status !== 'working' || rand() > 0.5) continue;
      const tool = pickTool();
      s.events.push(tool === 'think' && rand() < 0.4 ? { kind: 'text', summary: pick(SUMMARIES.think), ts: now() }
        : { ...demoTool(tool === 'delegate' ? 'read' : tool), ts: now() });
      if (s.events.length > 15) s.events.splice(0, s.events.length - 15);
      s.activity = s.events[s.events.length - 1];
      s.updated_at = now();
    }
    const doc = { time: now(), hosts: [
      ...['node-a', 'node-b', 'node-c'].map(name => ({ name, ok: true, error: null, jobs: jobs.filter(j => j.host === name),
        sessions: sessions.filter(s => s.host === name) })),
      { name: 'node-d', ok: false, error: 'node-d: ssh: connect to host 192.0.2.10 port 22: Connection timed out', jobs: [] },
    ] };
    return JSON.parse(JSON.stringify(doc));
  }
  return tick;
}

// ------------------------------------------------------------------ boot
resize();
loadAssets().then(() => {
  resize();
  layoutRooms([]);
  if (DEBUG && QS.get('cam')) {   // ?debug&cam=x,z,zoom: look at one spot, for close-up screenshots
    const [x, z, zoom] = QS.get('cam').split(',').map(Number);
    cam.z = zoom || 60; cam.c.set(x, 0.7, z); cam.userMoved = true;
  }
  if (DEBUG && QS.has('hoverdoc')) {   // ?debug&hoverdoc=S: after S seconds, hover a 3D document through the raycast path
    setTimeout(() => {
      const all = Object.values(docSlots).flat();
      const slot = all.find(s => /Diff line items/.test(s.doc.name)) || all[0];
      if (!slot) { console.error('hoverdoc: no documents on the deck'); return; }
      _w.setFromMatrixPosition(slot.m); _w.y += 0.01;
      const at = toScreen(_w, { x: 0, y: 0 });
      canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: at.x, clientY: at.y, pointerId: 99, pointerType: 'mouse', bubbles: true }));
    }, Number(QS.get('hoverdoc')) * 1000 || 5000);
  }
  if (DEBUG && QS.has('clickbot')) {   // ?debug&clickbot=S: click the first running android through the raycast path
    setTimeout(() => {
      const e = [...ents.values()].find(x => x.job.status === 'running') || ents.values().next().value;
      if (!e) { console.error('clickbot: no androids on the deck'); return; }
      e.bot.head.getWorldPosition(_w);
      const at = toScreen(_w, { x: 0, y: 0 });
      const opts = { clientX: at.x, clientY: at.y, pointerId: 98, pointerType: 'mouse', bubbles: true };
      canvas.dispatchEvent(new PointerEvent('pointerdown', opts)); canvas.dispatchEvent(new PointerEvent('pointerup', opts));
    }, Number(QS.get('clickbot')) * 1000 || 5000);
  }
  if (DEBUG && QS.has('bots')) {  // ?debug&bots: large portraits of every android variant
    const wrap = document.body.appendChild(Object.assign(document.createElement('div'), { style: 'position:fixed;left:280px;top:70px;z-index:98;display:flex;flex-wrap:wrap;gap:8px;background:#0b111d;padding:8px' }));
    for (const [host, agent, pose] of [['node-a', 'claude'], ['node-b', 'codex'], ['node-c', 'claude'], ['node-d', 'codex'], ['node-a', 'codex', 'off']]) {
      const cv = wrap.appendChild(Object.assign(document.createElement('canvas'), { style: 'width:200px;height:260px;background:#1a2336' }));
      miniBot(cv, hostLook(host), agent, pose || 'normal');
    }
  }
  // read-only probe for browser tests: rooms live only in WebGL, so they have no DOM to query
  window.fleetDeck = Object.freeze({
    rooms: () => rooms.map(r => ({ name: r.name, label: r.label })),
    agents: () => [...ents.values()].map(e => ({ key: e.key, kind: e.kind, room: e.room, status: e.job.status })),
  });
  if (DEMO) {
    const tick = demoSource();
    applyState(tick());
    setInterval(() => applyState(tick()), POLL_MS);
  } else {
    stream();
  }
  setInterval(renderLive, 1000);
  requestAnimationFrame(frame);
}, err => {
  const hint = document.getElementById('hint');
  hint.hidden = false;
  hint.innerHTML = `<h2>The deck couldn’t load its 3D assets</h2><p><code>${esc(err.message || String(err))}</code></p><p>They are served from <code>/vendor/</code> and <code>/assets/</code> by <code>fleet web</code>.</p>`;
  console.error(err);
});
