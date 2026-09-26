// Pipelines on the deck: a wall screen in each declared pipeline's room with a live thumbnail of its run.

import * as THREE from 'three';
import { HALF, WALL_H } from './env.js';
import { rgba, rr } from './util.js';
import { G, canvasTex, scene } from './scene.js';
import { roomByName, rooms } from './rooms.js';
import { bandPath, fmt, layout, runStatus, updateSankey } from './sankey.js';

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

// Screens stand on top of the walls like the room sign, big enough to read without zooming in: the back wall's
// free end first, then along the left wall.
const SPOTS = [['back', 8.75], ['left', 3.2], ['left', 7.2]];
const SCREEN_W = 3.2, SCREEN_H = 2.0, SCREEN_Y = WALL_H + 1.12;
const frameMat = new THREE.MeshStandardMaterial({ color: 0x1b2333, roughness: 0.5, metalness: 0.4 });
const hotMat = new THREE.MeshStandardMaterial({ color: 0x38bdf8, roughness: 0.5, metalness: 0.2, emissive: 0x0c4a6e });
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
    for (const s of screens.values()) { s.tex.dispose(); s.mat.dispose(); }
    screens.clear(); screenMeshes.length = 0; group.clear();
    for (const [p, room, [wall, along]] of placed) {
      const t = canvasTex(512, 320), mat = new THREE.MeshBasicMaterial({ map: t.tex, toneMapped: false });
      const mesh = new THREE.Mesh(G.plane, mat), frame = new THREE.Mesh(G.box, frameMat);
      mesh.scale.set(SCREEN_W, SCREEN_H, 1);
      frame.scale.set(SCREEN_W + 0.12, SCREEN_H + 0.12, 0.05);
      if (wall === 'back') {
        frame.position.set(room.ox + along, SCREEN_Y, room.oy - 0.02);
        mesh.position.set(room.ox + along, SCREEN_Y, room.oy + 0.015);
      } else {
        frame.position.set(room.ox - 0.02, SCREEN_Y, room.oy + along); frame.rotation.y = HALF;
        mesh.position.set(room.ox + 0.015, SCREEN_Y, room.oy + along); mesh.rotation.y = HALF;
      }
      frame.castShadow = true;
      group.add(frame, mesh);
      const s = { ...t, mat, mesh, frame, p, room, key: keyOf(p) };
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
  if (key === hot) return;
  if (hot && screens.get(hot)) screens.get(hot).frame.material = frameMat;
  hot = key;
  if (key && screens.get(key)) screens.get(key).frame.material = hotMat;
}

const STATUS_COLOR = { running: '#4ade80', done: '#38bdf8', failed: '#f87171', quiet: '#fbbf24', offline: '#f87171', none: '#7384a0' };
function drawThumb(s) {
  const { g, c, p } = s, w = c.width, h = c.height, st = runStatus(p), r = p.run;
  g.fillStyle = '#071a30'; g.fillRect(0, 0, w, h);
  g.strokeStyle = rgba('#38bdf8', 0.35); g.lineWidth = 4; rr(g, 2, 2, w - 4, h - 4, 10); g.stroke();
  g.textBaseline = 'alphabetic';
  g.fillStyle = '#e6edf8'; g.font = '700 34px Space Grotesk, system-ui, sans-serif';
  g.fillText(p.pipeline, 22, 46, w - 44);
  g.fillStyle = '#a3b2cb'; g.font = '500 22px JetBrains Mono, monospace';
  g.fillText(r ? (r.label || r.run_id) : p.host, 22, 78, w - 44);
  g.fillStyle = STATUS_COLOR[st.kind]; g.beginPath(); g.arc(30, 104, 7, 0, Math.PI * 2); g.fill();
  g.font = '600 22px JetBrains Mono, monospace'; g.fillText(st.text, 46, 112, w - 70);
  if (r) {
    const L = layout(r.nodes, r.counts, r.edges, w - 44, 130, { nodeW: 6, gap: 3 });
    g.save(); g.translate(22, 132);
    g.fillStyle = rgba('#38bdf8', 0.45);
    for (const b of L.bands) g.fill(new Path2D(bandPath(b)));
    g.fillStyle = '#e6edf8';
    for (const n of L.nodes.values()) g.fillRect(n.x, n.y, L.nodeW, n.h);
    g.restore();
    const source = r.nodes[0]?.[0], done = r.counts[source] || 0;
    g.fillStyle = '#e6edf8'; g.font = '600 24px JetBrains Mono, monospace';
    g.fillText(`${source ?? ''} ${fmt(done)}${r.total ? ' / ' + fmt(r.total) : ''}`, 22, h - 22, w - 44);
  } else {
    g.fillStyle = '#7384a0'; g.font = '500 22px Inter, system-ui, sans-serif';
    g.fillText(p.host_ok ? 'Waiting for a run' : (p.host_error || 'host offline'), 22, 190, w - 44);
  }
  s.tex.needsUpdate = true;
}
// ages and "no writes for" move on without new reports
setInterval(() => { for (const s of screens.values()) drawThumb(s); }, 15000);
