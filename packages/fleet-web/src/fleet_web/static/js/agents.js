// Androids: the robot model, one entity per job or session, and their overlay tags.

import * as THREE from 'three';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import { BK, HALF, PI, RD, PHONE_ROOM_FILL, REDUCED, ROOM_FILL, RW, TINY_Z, tagsEl, vh, vw } from './env.js';
import { clamp, clock, esc, offlineLabel, trunc } from './util.js';
import { AGENT_COLOR, hostLook } from './looks.js';
import { isSession, mumble, shortId } from './activity.js';
import { crowdsOf } from './behaviour.js';
import { actionOf, glyphHtml } from './glyphs.js';
import { G, M, ROBOT, _w, botGroup, cam, toScreen } from './scene.js';
import { ents, everLoaded, fanned, selectedKey, setFanned } from './model.js';
import { roomByName, rooms } from './rooms.js';
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
const ONCE = new Set(['Sitting', 'Standing', 'ThumbsUp', 'Wave']);
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
  e.proxy = new THREE.Mesh(G.proxy, M.hidden); e.proxy.userData.ent = e;
  const blob = new THREE.Mesh(G.disc, M.blob); blob.position.y = 0.008; blob.scale.setScalar(0.9 * BK);
  e.bot.root.add(blob, e.ring, e.glow, e.proxy);
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
// The bubble is an action glyph (glyphs.js); what it stands for in words is its tooltip, and in full in the agent panel
// (a click away) and the deck log.
function sessionWords(s) {
  const a = s.activity;
  if (s.status === 'idle') return [`idle since ${clock(s.updated_at).slice(0, 5)}`, 'wait'];
  if (a && a.kind === 'error') return [trunc(a.summary, 120), 'bad'];
  if (a && a.kind === 'text') return [trunc(a.summary, 120), ''];
  if (a) return [mumble(a), ''];
  return ['working…', ''];
}
function jobWords(j, done, total) {
  const a = j.activity;
  switch (j.status) {
    case 'running':
      if (a && a.kind === 'retry') return [trunc(a.summary, 120), 'quiet'];
      if (a && a.kind === 'error') return [trunc(a.summary, 120), 'bad'];
      if (a && a.kind === 'text') return [trunc(a.summary, 120), ''];
      return [a ? mumble(a) : 'warming up…', ''];
    case 'queued': return ['queued · waiting at the door', 'quiet'];
    case 'done': return [`done · ${done}/${total}`, 'done'];
    case 'cancelled': return ['cancelled', 'quiet'];
  }
  return [j.status, 'quiet'];
}
function setBubble(e, action, words, cls) {
  const b = e.el.firstChild;
  const changed = b.dataset.action !== action;
  b.className = 'bubble glyphs ' + cls + (changed && e.sig && !REDUCED ? ' pop' : '');   // a new action pops in once
  b.dataset.action = action;
  b.title = words;
  b.innerHTML = glyphHtml(action);
}
export function updateTag(e) {
  const j = e.job;
  e.el.toggleAttribute('data-stale', !!j.stale);
  e.el.title = j.stale ? `${offlineLabel(j)}: ${j.stale_reason || 'host offline'}. Showing last-known ${j.status}; current status is unknown.` : '';
  const stale = j.stale ? `<span class="stale-label">${esc(offlineLabel(j, true))}</span>` : '';
  if (isSession(e)) {
    const [words, cls] = sessionWords(j), action = actionOf(j);
    const stack = `${stale}<span class="lv${j.status === 'idle' ? ' idle' : ''}">LIVE</span><span class="id">${esc(trunc(j.title || shortId(j.id), vw < 760 ? 16 : 28))}</span><span class="ag">${esc(j.agent)}</span>`;
    const sig = action + '|' + words + '|' + cls + '|' + stack;
    if (sig === e.sig) return;
    setBubble(e, action, words, cls);
    e.sig = sig;
    e.el.lastChild.innerHTML = stack;
    e.sizeDirty = true;
    return;
  }
  const steps = j.steps || [];
  const done = steps.filter(s => s.status === 'done').length;
  const cur = steps.findIndex(s => s.status === 'running' || s.status === 'failed' || s.status === 'blocked');
  const [words, cls] = jobWords(j, done, steps.length), action = actionOf(j);
  let window0 = 0;
  if (steps.length > 12) window0 = clamp((cur < 0 ? done : cur) - 5, 0, steps.length - 12);
  const pips = steps.slice(window0, window0 + 12).map(s => `<i class="pip ${esc(s.status)}"></i>`).join('');
  // A job is named by the start of its description; the short id stays, dimmed, to match the CLI.
  const name = trunc(j.description || '', vw < 760 ? 14 : 22);
  const label = `${stale}<span class="id" title="${esc(j.id)}">${name ? `${esc(name)} <i class="sid">${esc(shortId(j.id))}</i>` : esc(shortId(j.id))}</span>`;
  const sig = action + '|' + words + '|' + cls + '|' + pips + done + '|' + label;
  if (sig === e.sig) return;
  setBubble(e, action, words, cls);
  e.sig = sig;
  e.el.lastChild.innerHTML = `${label}${window0 > 0 ? '<b>…</b>' : ''}${pips}<b>${done}/${steps.length}</b>`;
  e.sizeDirty = true;
}

// ------------------------------------------------------------------ crowds
// More than five androids at one station of a room gather into one figure (the first of them) with a count badge in
// place of their tags. Clicking it fans them out until you click elsewhere or close the panel. The selected android
// always stands on its own.
export const crowds = new Map();   // "room|station" → { key, room, station, members, el }
function makeBadge(key) {
  const el = document.createElement('div');
  el.className = 'tag crowd';
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  el.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ev.stopPropagation(); setFanned(key); }
  });
  el.innerHTML = '<div class="stack"><b></b></div>';
  el.addEventListener('click', ev => { ev.stopPropagation(); setFanned(key); });
  tagsEl.appendChild(el);
  return el;
}
function gatherCrowds() {
  for (const e of ents.values()) e.crowd = null;
  const at = crowdsOf([...ents.values()].map(e => ({ key: e.key, room: e.room, station: e.spotProp, leaving: e.leaving, e })), selectedKey);
  for (const [key, c] of crowds) {
    if (at.has(key)) continue;
    c.el.remove(); crowds.delete(key);
    if (fanned === key) setFanned(null);
  }
  for (const [key, list] of at) {
    const members = list.map(m => m.e);
    let c = crowds.get(key);
    if (!c) { c = { key, room: members[0].room, station: members[0].spotProp, el: makeBadge(key) }; crowds.set(key, c); }
    c.members = members;
    c.el.title = `${members.length} agents here; click to spread them. Click elsewhere to regroup.`;
    c.el.setAttribute('aria-label', c.el.title);
    c.el.firstChild.firstChild.textContent = members.length;
    if (fanned !== key) for (const e of members) e.crowd = c;
  }
  for (const e of ents.values()) e.bot.root.visible = !e.crowd || e === e.crowd.members[0];
}

// Tags hang above each android's head, projected from 3D. Nearer androids are placed first and
// colliding tags are pushed upward, with a thin lead line back to their android.
const tagList = [], placed = [];
const _s = { x: 0, y: 0 };
// A room you have zoomed into: its floor spans a good share of the view's width and its middle is on screen.
function zoomedInto(r) {
  let x0 = Infinity, x1 = -Infinity;
  for (const [x, z] of [[r.ox, r.oy], [r.ox + RW, r.oy], [r.ox, r.oy + RD], [r.ox + RW, r.oy + RD]]) {
    toScreen(_w.set(x, 0, z), _s);
    x0 = Math.min(x0, _s.x); x1 = Math.max(x1, _s.x);
  }
  toScreen(_w.set(r.ox + RW / 2, 0, r.oy + RD / 2), _s);
  return x1 - x0 >= vw * (vw < 760 ? PHONE_ROOM_FILL : ROOM_FILL) &&_s.x > 0 && _s.x < vw && _s.y > 0 && _s.y < vh;
}
export function positionTags() {
  // the overview stays quiet: speech bubbles appear only in a room you have zoomed into (or for the selected android)
  const tiny = cam.z < TINY_Z;
  gatherCrowds();
  for (const r of rooms) r.close = zoomedInto(r);
  tagList.length = 0;
  for (const e of ents.values()) {
    const r = roomByName.get(e.room);
    if (!r) continue;
    e.far = !r.close;
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
    if (!tiny && !e.crowd) {
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
    // an idle session rests without a word
    const cls = e.tagBase + (e.job.status === 'idle' ? ' calm' : '') + (e.crowd ? ' gathered' : '')
      + (tiny ? ' tiny' : e.key === selectedKey ? ' sel' : e.far ? ' far' : '');
    if (e.el.className !== cls) { e.el.className = cls; e.sizeDirty = true; }
  }
  // a crowd's badge hangs where its figure's tag would
  for (const c of crowds.values()) {
    const e = c.members[0];
    const cls = 'tag crowd' + (fanned === c.key ? ' fanned' : tiny ? ' tiny' : '');
    if (c.el.className !== cls) c.el.className = cls;
    c.el.style.transform = `translate(${Math.round(e.sx)}px,${Math.round(e.sy)}px) translate(-50%,-100%)`;
    c.el.style.zIndex = String(e.qz);
  }
}
