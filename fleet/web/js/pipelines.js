// Pipelines on the deck: a wall screen in each declared pipeline's room with a live thumbnail of its run.

import * as THREE from 'three';
import { HALF, REDUCED, WALL_H } from './env.js';
import { rgba, rr } from './util.js';
import { G, canvasTex, scene } from './scene.js';
import { roomByName, rooms } from './rooms.js';
import { bandPath, fmt, layout, processed, runStatus, tonesOf, updateSankey } from './sankey.js';

export let pipelines = [];   // as in the state document: declared ones and any a host reports
const group = new THREE.Group();
scene.add(group);
export const screenMeshes = [];
const screens = new Map();   // "host:pipeline" → { p, mesh, frame, tex, g, c, room }
let screensKey = '';

export const keyOf = p => `${p.host}:${p.pipeline}`;
// the rooms pipelines need: a declared pipeline's room is on the deck even with no work in it
export function pipelineRooms(entered) {
  return pipelines.filter(p => p.project && (!entered || p.project_id === entered)).map(p => p.project);
}
export function setPipelines(list) {
  pipelines = list || [];
}
// A pipeline event between state documents: only its screen and the open Sankey change.
export function applyPipeline(p) {
  const i = pipelines.findIndex(x => keyOf(x) === keyOf(p));
  if (i >= 0) pipelines[i] = p; else pipelines.push(p);
  const s = screens.get(keyOf(p));
  if (s) { s.p = p; drawThumb(s); }
  updateSankey(p);
}

// Screens stand on top of the walls like the room sign, big enough to read without zooming in: the back wall's free
// end first (the sign takes 0.2–6.2 of its 12 tiles), then along the left wall.
const SPOTS = [['back', 8.95], ['left', 2.6], ['left', 7.4]];
const SCREEN_W = 4.6, SCREEN_H = 2.875, SCREEN_Y = WALL_H + 0.12 + SCREEN_H / 2;
const GLOW = 0x38bdf8;
export function buildScreens() {
  const placed = [];
  const perRoom = new Map();
  for (const p of pipelines) {
    const room = p.project && roomByName.get(p.project);
    if (!room) continue;
    const n = perRoom.get(room.name) || 0;
    perRoom.set(room.name, n + 1);
    if (n < SPOTS.length) placed.push([p, room, SPOTS[n]]);
  }
  for (const r of rooms) r.pipelineCount = perRoom.get(r.name) || 0;
  const key = placed.map(([p, r, [wall]]) => `${keyOf(p)}@${r.name}:${r.ox},${r.oy}:${wall}`).join('|') + rooms.length;
  if (key !== screensKey) {
    screensKey = key;
    for (const s of screens.values()) { s.tex.dispose(); s.mat.dispose(); s.frameMat.dispose(); }
    screens.clear(); screenMeshes.length = 0; group.clear();
    for (const [p, room, [wall, along]] of placed) {
      const t = canvasTex(640, 400), mat = new THREE.MeshBasicMaterial({ map: t.tex, toneMapped: false });
      const frameMat = new THREE.MeshStandardMaterial({ color: 0x1b2333, roughness: 0.5, metalness: 0.3, emissive: GLOW, emissiveIntensity: 0 });
      const mesh = new THREE.Mesh(G.plane, mat), frame = new THREE.Mesh(G.box, frameMat);
      mesh.scale.set(SCREEN_W, SCREEN_H, 1);
      frame.scale.set(SCREEN_W + 0.16, SCREEN_H + 0.16, 0.05);
      if (wall === 'back') {
        frame.position.set(room.ox + along, SCREEN_Y, room.oy - 0.02);
        mesh.position.set(room.ox + along, SCREEN_Y, room.oy + 0.015);
      } else {
        frame.position.set(room.ox - 0.02, SCREEN_Y, room.oy + along); frame.rotation.y = HALF;
        mesh.position.set(room.ox + 0.015, SCREEN_Y, room.oy + along); mesh.rotation.y = HALF;
      }
      frame.castShadow = true;
      group.add(frame, mesh);
      const s = { ...t, mat, frameMat, mesh, frame, p, room, key: keyOf(p) };
      mesh.userData.pipeline = s.key; frame.userData.pipeline = s.key;
      screens.set(s.key, s);
      screenMeshes.push(mesh, frame);
    }
  }
  for (const s of screens.values()) { s.p = pipelines.find(p => keyOf(p) === s.key) || s.p; drawThumb(s); }
}
export function pipelineByKey(key) { return pipelines.find(p => keyOf(p) === key) || null; }
export function screenOf(key) { return screens.get(key) || null; }
let hot = null;
export function hoverScreen(key) {
  hot = key;
}
// The frame glows so a screen reads as something to press: a slow pulse while its run is live (steady with reduced
// motion), a steady low glow otherwise, and full when hovered.
export function stepScreens(t) {
  for (const s of screens.values()) {
    const live = runStatus(s.p).kind === 'running';
    s.frameMat.emissiveIntensity = s.key === hot ? 1.1
      : live ? (REDUCED ? 0.6 : 0.35 + 0.4 * (0.5 + 0.5 * Math.sin(t * 2.2))) : 0.25;
  }
}
export function glowOf(key) { return screens.get(key)?.frameMat.emissiveIntensity ?? null; }

const TONE_COLOR = { good: '#4ade80', warn: '#fbbf24', bad: '#f87171', muted: '#7384a0' };
const STATUS_COLOR ={ running: '#4ade80', done: '#38bdf8', failed: '#f87171', quiet: '#fbbf24', offline: '#f87171', none: '#7384a0' };
function drawThumb(s) {
  const { g, c, p } = s, w = c.width, h = c.height, st = runStatus(p), r = p.run;
  // few things, large: name, status, a sketch of the bands and how far the run has got (the run's label is in the tooltip)
  g.fillStyle = '#0d2742'; g.fillRect(0, 0, w, h);
  g.strokeStyle = rgba('#38bdf8', 0.7); g.lineWidth = 6; rr(g, 3, 3, w - 6, h - 6, 12); g.stroke();
  g.textBaseline = 'alphabetic';
  g.fillStyle = '#7dd3fc'; g.font = '700 26px JetBrains Mono, monospace'; g.textAlign = 'right';
  g.fillText('OPEN ▸', w - 26, 44);
  g.textAlign = 'left';
  g.fillStyle = '#f4f8ff'; g.font = '700 62px Space Grotesk, system-ui, sans-serif';
  g.fillText(p.pipeline, 26, 104, w - 52);
  g.fillStyle = STATUS_COLOR[st.kind]; g.beginPath(); g.arc(42, 150, 14, 0, Math.PI * 2); g.fill();
  g.font = '700 40px JetBrains Mono, monospace'; g.fillText(st.text, 68, 164, w - 94);
  if (r) {
    const L = layout(r.nodes, r.counts, r.edges, w - 52, 130, { nodeW: 8, gap: 4 });
    g.save(); g.translate(26, 192);
    const tones = tonesOf(r);
    for (const b of [...L.bands].sort((p, q) => q.w - p.w)) {
      g.fillStyle = rgba(TONE_COLOR[tones.get(b.t.name)] || '#38bdf8', 0.75);
      g.fill(new Path2D(bandPath(b)));
    }
    g.fillStyle = '#f4f8ff';
    for (const n of L.nodes.values()) g.fillRect(n.x, n.y, L.nodeW, n.hc);
    g.restore();
    const done = processed(r);
    g.fillStyle = '#f4f8ff'; g.font = '700 42px JetBrains Mono, monospace';
    g.fillText(`${fmt(done)}${r.total ? ' / ' + fmt(r.total) : ''}`, 26, h - 26, w - 52);
  } else {
    g.fillStyle = '#c3cfe2'; g.font = '500 36px Inter, system-ui, sans-serif';
    g.fillText(p.host_ok ? 'Waiting for a run' : (p.host_error || 'host offline'), 26, 250, w - 52);
  }
  s.tex.needsUpdate = true;
}
// ages and "no writes for" move on without new reports
setInterval(() => { for (const s of screens.values()) drawThumb(s); }, 15000);
