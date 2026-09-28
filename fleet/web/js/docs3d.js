// Documents agents produced: their 3D paper trays, link lines, and bookkeeping.

import * as THREE from 'three';
import { animationNow } from './clock.js';
import { BK, PI, REDUCED } from './env.js';
import { age, hash, mix, rr } from './util.js';
import { G, M, _col, _m4, _m4b, _q, _sc, _v, _w, canvasTex, scene } from './scene.js';
import { ents } from './model.js';
import { PRESS, placer, roomByName, rooms } from './rooms.js';

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
export let docSig = null;
export const docMeshes = {};        // kind → InstancedMesh
export const docSlots = {};         // kind → [{ e, doc, key, m }] in instance order
const docByKey = new Map();  // doc key → { kind, i, top: Vector3 (landing point), m }
let trayMesh = null, stripeMesh = null;
const badges = [];

export function buildDocs() {
  const holders = [];
  for (const r of rooms) for (const e of r.ents) if (docsOf(e.job).length) holders.push(e);
  const sig = holders.map(e => e.key + ':' + e.room + ':' + docsOf(e.job).map(d => d.id).join(',')).join('|');
  if (sig === docSig) return;
  docSig = sig;
  for (const k of Object.keys(docMeshes)) { docGroup.remove(docMeshes[k]); docMeshes[k].dispose(); delete docMeshes[k]; }
  for (const m of [trayMesh, stripeMesh]) if (m) { docGroup.remove(m); m.dispose(); }
  for (const b of badges.splice(0)) { docGroup.remove(b); b.material.map.dispose(); b.material.dispose(); }
  docByKey.clear();
  const slots = { report: [], file: [], outbox: [] }, trays = [], stripes = [];
  for (const r of rooms) {
    const mine = r.ents.filter(e => docsOf(e.job).length);
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
export function liftHovered() {
  const want = hoverDoc && hoverDoc.key;
  if (want === hoverShown) return;
  if (hoverShown && !docFx.has(hoverShown)) showDoc(hoverShown, true);
  if (want && !docFx.has(want)) showDoc(want, true, 0.06);
  hoverShown = want;
}

// the printing android's beam, the sheet sliding out of the slit, its flight to the tray and a landing glow
const flyers = new Map();   // doc key → { mesh, glow }
export function stepDocFx(t) {
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
export const lineGeo = new THREE.BufferGeometry();
lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3).setUsage(THREE.DynamicDrawUsage));
lineGeo.setAttribute('color', new THREE.BufferAttribute(lineCol, 3).setUsage(THREE.DynamicDrawUsage));
const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, depthWrite: false, toneMapped: false }));
lines.frustumCulled = false;
scene.add(lines);
export let nSeg = 0;
export const _la = new THREE.Vector3(), _lb = new THREE.Vector3();
function segment(a, b) {
  if (nSeg >= LN) return;
  const o = nSeg * 6;
  linePos[o] = a.x; linePos[o + 1] = a.y; linePos[o + 2] = a.z; linePos[o + 3] = b.x; linePos[o + 4] = b.y; linePos[o + 5] = b.z;
  lineCol[o] = lineCol[o + 3] = _col.r; lineCol[o + 1] = lineCol[o + 4] = _col.g; lineCol[o + 2] = lineCol[o + 5] = _col.b;
  nSeg++;
}
// a dashed arc from a to b, lifted by `arc` in the middle
export function dashedLine(a, b, color, t, arc) {
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

// ------------------------------------------------------------------ documents: what the agents produced
// (the 3D document objects and printer come next; the panel and reader already use this metadata)
export const DOC_KIND = {
  report: { label: 'Report', glyph: '▤' },
  file:   { label: 'File',   glyph: '✎' },
  outbox: { label: 'Outbox', glyph: '⇪' },
  brief:   { label: 'Brief',   glyph: '☰' },
  context: { label: 'Context', glyph: '⧉' },
};
// what the job was given rather than what it produced: listed and readable, but never printed onto the press
const INPUT_KINDS = new Set(['brief', 'context']);
// a document changed this recently, on a running job, is still being written
export const DOC_UPDATING_SECONDS = 60;
const seenDocs = new Set();
const docFx = new Map();                            // doc key → { entKey, start }
export let hoverDoc = null;                                // { key } of the document under the pointer

export function docKey(e, doc) { return e.key + ':' + doc.id; }
export function docsOf(job) { return (job.documents || []).filter(d => !INPUT_KINDS.has(d.kind)).sort((a, b) => (a.mtime || 0) - (b.mtime || 0)); }
// step briefs in step order, then context files by name
export function inputDocsOf(job) {
  const inputs = (job.documents || []).filter(d => INPUT_KINDS.has(d.kind));
  return [...inputs.filter(d => d.kind === 'brief').sort((a, b) => (a.step ?? 0) - (b.step ?? 0)), ...inputs.filter(d => d.kind === 'context')];
}
export function isUpdating(job, doc) {
  return job.status === 'running' && !INPUT_KINDS.has(doc.kind) && doc.mtime != null && Date.now() / 1000 - doc.mtime < DOC_UPDATING_SECONDS;
}
export function kindOf(doc) { return DOC_KIND[doc.kind] ? doc.kind : 'file'; }
export function fmtSize(n) { if (!n && n !== 0) return ''; if (n < 1024) return n + ' B'; if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB'; return (n / 1048576).toFixed(1) + ' MB'; }
export function docMeta(doc) { return [doc.step != null ? `step ${doc.step + 1}` : '', fmtSize(doc.size), doc.mtime ? age(doc.mtime) + ' ago' : ''].filter(Boolean).join(' · '); }

// remember every document; ones that appear on a job we already knew about get printed
export function noteDocs(e, quiet) {
  const now = animationNow() / 1000;
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

export function setDocSig(value) { docSig = value; }
export function setNSeg(value) { nSeg = value; }
export function setHoverDoc(value) { hoverDoc = value; }
