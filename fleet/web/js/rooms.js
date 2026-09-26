// Projects as rooms: laying them out and building the deck, furniture and signs.

import * as THREE from 'three';
import { DEBUG, DESK_TOP, GAP, HALF, QS, RD, RW, WALL_H, vw } from './env.js';
import { hash, hsl, mix } from './util.js';
import {
  DOOR_X0, DOOR_X1, FURNITURE, KITCHEN, OUTBOX, RACK, TERMINALS, THEMES, projectLook, themeFor,
} from './looks.js';
import {
  G, KIT, M, Placer, SUN_DIR, cam, canvasTex, dashTexture, deckGroup, disposables, drawSign, sun,
} from './scene.js';
import { setDocSig } from './docs3d.js';
import { deckBounds, fit } from './camera.js';
import { carryFocus } from './focus.js';
import { applyAttention } from './attention.js';

// ------------------------------------------------------------------ the deck: plates, rooms, furniture
export let placer = null;

export const edgeStrips = [];
function buildDeck() {
  for (const d of disposables.splice(0)) d.dispose();
  deckGroup.clear();
  edgeStrips.length = 0;
  placer = new Placer();
  for (const b of plates) buildPlate(b, placer);
  for (const r of rooms) buildRoom(r, placer);
  placer.build(deckGroup);
  setDocSig(null);
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
  return { kind, wall, along, w: P[0][3], h: P[0][4], up: V, out: 0.1 };
}

// document press on the right-hand wall: a fabricator at the back, one tray per job along the console
export const PRESS = { x: 11.35, y0: 2.95, y1: 5.65, top: 0.5, fab: 3.25, trays: 3.62 };
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
  r.onWalls = T.art.map(([wall, along, kind]) => wallArt(at, S, wall, along, kind, look));   // what a wall screen must keep clear of
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
  r.onWalls.push({ kind: 'whiteboard', wall: 'back', along: 7.95, w: 2.3, h: 1.1, up: 1.0, out: 0.12 });

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
  r.signMesh = sign;
  disposables.push(r.sign.tex, signMat);
  drawSign(r);
}

export let rooms = [];
export let plates = [];               // deck modules the rooms sit on
export const roomByName = new Map();
let layoutKey = '';
export let layoutNames = [];

export function layoutRooms(names) {
  layoutNames = names;
  if (!names.length) names = ['lobby'];
  const column = vw < 760; // phones: rooms stack straight down the screen as separate modules
  const key = names.join('|') + (column ? '#column' : '#grid');
  if (key === layoutKey) return;
  layoutKey = key;
  const cols = Math.ceil(Math.sqrt(names.length));
  const taken = new Set(), themes = new Set(), before = rooms;
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
  carryFocus(before, rooms);
  applyAttention(rooms);
  if (!cam.userMoved) fit(true);
}
