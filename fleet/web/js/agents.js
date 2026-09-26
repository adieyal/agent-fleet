// Androids: the robot model, one entity per job or session, and their overlay tags.

import * as THREE from 'three';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import { BK, HALF, PI, RD, REDUCED, SMALL_Z, TINY_Z, tagsEl, vw } from './env.js';
import { clamp, clock, esc, trunc } from './util.js';
import { AGENT_COLOR, hostLook } from './looks.js';
import { isSession, mumble, shortId } from './activity.js';
import { G, M, ROBOT, _w, botGroup, cam, toScreen } from './scene.js';
import { ents, everLoaded, selectedKey } from './model.js';
import { roomByName } from './rooms.js';
import { select } from './panel.js';

// ------------------------------------------------------------------ the android
// One RobotExpressive per job: Main material in the host colour, a host accessory, and the agent's face light
// (Claude: a coral visor band; Codex: twin cyan eyes). Accessories hang off bones so they follow the animation.
export function buildRobot(look, agent) {
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
export function action(bot, name) {
  let a = bot.actions[name];
  if (!a) {
    a = bot.actions[name] = bot.mixer.clipAction(ROBOT.clips[name]);
    if (ONCE.has(name) && name !== 'Wave') { a.setLoop(THREE.LoopOnce, 1); a.clampWhenFinished = true; }
  }
  return a;
}
export function playClip(e, name, fade = 0.3) {
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

function makeTag(e) {
  const el = document.createElement('div');
  el.className = e.tagBase = isSession(e) ? 'tag sess' : 'tag';
  el.innerHTML = '<div class="bubble"></div><div class="stack"></div>';
  el.style.setProperty('--hc', e.look.color);
  el.addEventListener('click', ev => { ev.stopPropagation(); select(e.key); });
  tagsEl.appendChild(el);
  return el;
}

export function createEnt(key, hostName, job, kind = 'job') {
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
export function dropEnt(e) {
  e.el.remove();
  botGroup.remove(e.bot.root);
  e.bot.mixer.stopAllAction();
  e.bot.main.dispose(); e.bot.face.dispose(); e.bot.hostMat.dispose(); e.bot.eyes?.dispose(); e.ring.material.dispose();
  e.halo?.material.dispose();
}

// ------------------------------------------------------------------ overlay tags
function sessionBubble(s) {
  const a = s.activity;
  if (s.status === 'idle') return [`<span class="ic">⏸</span>waiting for you since ${esc(clock(s.updated_at).slice(0, 5))}`, 'wait'];
  if (a && a.kind === 'error') return [`<span class="ic">!</span>${esc(trunc(a.summary, 120))}`, 'bad'];
  if (a && a.kind === 'text') return [`<span class="ic">“</span>${esc(trunc(a.summary, 120))}`, ''];
  if (a) return [esc(mumble(a)), ''];
  return ['<span class="ic">∴</span>working…', ''];
}
export function updateTag(e) {
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
export function positionTags() {
  // phones have little room: speech bubbles appear once zoomed in (or for the selected android)
  const small = cam.z < (vw < 760 ? SMALL_Z * 2.2 : SMALL_Z), tiny = cam.z < TINY_Z;
  tagList.length = 0;
  for (const e of ents.values()) {
    const r = roomByName.get(e.room);
    if (!r) continue;
    e.calm = r.focus === 'background';   // a background room's androids keep their chatter to themselves
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
    const cls = e.tagBase + (e.calm ? ' calm' : '') + (tiny ? ' tiny' : e.key === selectedKey ? ' sel' : small ? ' small' : '');
    if (e.el.className !== cls) { e.el.className = cls; e.sizeDirty = true; }
  }
}
