// Applying fleet state from the server stream: entities, dismissals and finished-job retirement.

import { QS, RD, REDUCED } from './env.js';
import { animationNow } from './clock.js';
import { store } from './util.js';
import { ROBOT, cam, drawSign } from './scene.js';
import { fit } from './camera.js';
import { ents, everLoaded, hosts, live, offFloor, selectedKey, setEverLoaded, setHosts, setLive, workOf } from './model.js';
import { hostLook } from './looks.js';
import { departed as quietFor, farewell, offFloor as offTheFloor, retired } from './behaviour.js';
import { layoutRooms, rooms } from './rooms.js';
import { buildDocs, noteDocs } from './docs3d.js';
import { createEnt, dropEnt, updateTag } from './agents.js';
import { assignTargets } from './motion.js';
import {
  closePanel, collectEvents, renderFeed, renderLegend, renderLive, renderPanel, renderStats, updateHint,
} from './panel.js';
import { followDoc, openReader, readerTarget } from './reader.js';
import { libraryKeyOf, refreshLibrary } from './library.js';
import { applyFocus } from './focus.js';
import { applyAttention } from './attention.js';
import { patchScene } from './dim.js';
import { applyBuilding } from './building.js';
import { applyWorkarea } from './workarea.js';
import { refreshBench } from './bench.js';
import { applyPipeline, buildScreens, pipelineRooms, pipelines, setPipelines } from './pipelines.js';
import { updateSankey } from './sankey.js';

// Dismissed agents are hidden in this browser only (the deck stays view-only). Each is remembered with
// the updated_at it had when dismissed, so any new activity brings it back.
const DISMISSED_KEY = 'fleet.dismissed';
export let dismissed = {}, hiddenCount = 0, lastDoc = null;
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
// Finished jobs leave the deck: one that finishes while you watch walks out through the door (motion.js), one
// already finished never shows. A header chip counts them and shows them again. Failed and stalled jobs have no
// android either: their room's lantern (attention.js) carries them and opens their panel. Nor has any work in a
// background room: the room is lit warm while it runs (focus.js).
// (the rules: behaviour.js)
export let retiredCount = 0, showFinished = false;
function retireFinished(hostList) {
  retiredCount = 0;
  if (showFinished) return;
  for (const h of hostList) h.jobs = h.jobs.filter(j => {
    if (j.status !== 'done' && j.status !== 'cancelled') return true;
    retiredCount++;
    return !retired(j, ents.get(h.name + ':' + j.id) || null, { reduced: REDUCED });
  });
}
// An idle live session leaves the deck after half an hour quiet and comes back with its next activity; one waiting
// on a decision keeps its android. The header still counts it.
function departed(h, s, doc) {
  return quietFor(h.name + ':' + s.id, s, doc.attention, Date.now() / 1000);
}
export function departIdle() {
  if (lastDoc && hosts.some(h => h.sessions.some(s => ents.has(h.name + ':' + s.id) && departed(h, s, lastDoc)))) applyState(lastDoc);
}
export function toggleFinished() {
  showFinished = !showFinished;
  if (lastDoc) applyState(lastDoc);
}
export function dismiss(key) {
  const e = workOf(key);
  if (!e || !lastDoc) return;
  dismissed[key] = e.job.updated_at ?? null;
  saveDismissed();
  if (selectedKey === key) closePanel();
  applyState(lastDoc);
}
export function restoreDismissed() {
  dismissed = {};
  saveDismissed();
  if (lastDoc) applyState(lastDoc);
}

// A floor entered from the building shows only its project's work on the deck (a stand-in for the floor view, L1).
export let entered = null;   // the project ID, or null for the whole deck
export function enterProject(projectId) {
  entered = projectId;
  cam.userMoved = false;
  if (lastDoc) applyState(lastDoc);
  fit(false);
}

// The server's build when this page loaded: a document from a newer build means the deck was redeployed under us, and
// old code would draw new assets wrongly, so the page reloads (the view, tab and theme are remembered across it).
let pageBuild = null;
export function applyState(doc) {
  if (doc.build) {
    if (pageBuild === null) pageBuild = doc.build;
    else if (doc.build !== pageBuild) { location.reload(); return; }
  }
  lastDoc = doc;
  setPipelines(doc.pipelines);
  applyBuilding(doc);   // from the whole document: dismissed and finished work still counts there
  applyWorkarea(doc);
  refreshBench();
  const shown = visibleHosts(doc);
  setHosts(entered ? shown.map(h => ({ ...h, jobs: h.jobs.filter(j => j.project_id === entered),
    sessions: h.sessions.filter(s => s.project_id === entered) })) : shown);
  const projects = new Set();
  for (const h of hosts) for (const j of h.jobs || []) projects.add(j.project);
  // hosts on an older fleetd send no sessions; a session whose cwd fleetd could not tell has no room and is not drawn
  for (const h of hosts) for (const s of h.sessions || []) if (s.project) projects.add(s.project);
  for (const label of pipelineRooms(entered)) projects.add(label);
  layoutRooms([...projects].sort());
  buildScreens();
  for (const room of rooms) room.label = doc.project_labels?.[room.name] || room.name;
  for (const room of rooms) room.libraryKey = libraryKeyOf(doc, room.name);
  applyFocus(doc, rooms);
  const seen = new Set();
  const now = animationNow() / 1000;
  const quiet = new Set(rooms.filter(r => r.focus === 'background').map(r => r.name));
  offFloor.clear();
  for (const h of hosts) {
    for (const j of h.jobs || []) {
      const key = h.name + ':' + j.id;
      if (offTheFloor(j, quiet.has(j.project))) { offFloor.set(key, { key, kind: 'job', host: h.name, job: j, look: hostLook(h.name) }); continue; }
      seen.add(key);
      let e = ents.get(key);
      const known = !!e;
      if (!e) { e = createEnt(key, h.name, j); ents.set(key, e); }
      // a job that just finished gives a thumbs-up (a cancelled one waves) before it leaves, or heads for the sofa
      // while finished jobs are shown
      const bye = known ? farewell(j, e, { reduced: REDUCED }) : null;
      if (bye) {
        e.holdClip = bye.clip; e.holdUntil = now + ROBOT.clips[e.holdClip].duration;
        e.leaving = !showFinished; e.leaveBy = now + bye.within;
      }
      e.lastStatus = j.status;
      e.job = j; e.host = h.name;
      noteDocs(e, !known || !everLoaded);
      if (e.room !== j.project) { e.room = j.project; e.local = { x: 5.5, y: RD + 0.7 }; e.path = []; e.target = null; e.fresh = true; }
    }
    for (const s of h.sessions || []) {
      if (!s.project || departed(h, s, doc)) continue;
      const key = h.name + ':' + s.id;
      if (quiet.has(s.project)) { offFloor.set(key, { key, kind: 'session', host: h.name, job: s, look: hostLook(h.name) }); continue; }
      seen.add(key);
      let e = ents.get(key);
      if (!e) { e = createEnt(key, h.name, s, 'session'); ents.set(key, e); }
      e.lastStatus = s.status;
      e.job = s; e.host = h.name;
      if (e.room !== s.project) { e.room = s.project; e.local = { x: 5.5, y: RD + 0.7 }; e.path = []; e.target = null; e.fresh = true; }
    }
  }
  for (const [k, e] of ents) if (!seen.has(k)) { dropEnt(e); ents.delete(k); if (selectedKey === k && !offFloor.has(k)) closePanel(); }
  setEverLoaded(true);
  applyAttention(rooms, doc);
  patchScene();
  collectEvents();
  assignTargets();
  buildDocs();
  for (const e of ents.values()) updateTag(e);
  for (const r of rooms) drawSign(r);
  renderLegend(); renderStats(); renderLive(); renderFeed(); updateHint();
  if (selectedKey) renderPanel();
  for (const p of pipelines) updateSankey(p);
  openLinkedDoc();
  followDoc(workOf(readerTarget()));
  refreshLibrary();
  document.dispatchEvent(new CustomEvent('fleet:state', { detail: doc }));   // (the sprite-world floor follows: world/floor-view.js)
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

// ------------------------------------------------------------------ data sources
// Server-sent events: a full state document whenever anything changes, pings in between.
// EventSource reconnects by itself after an error.
export function stream() {
  const source = new EventSource('/api/stream');
  source.addEventListener('state', (message) => {
    setLive({ ok: true, at: Date.now(), err: null });
    applyState(JSON.parse(message.data));
  });
  source.addEventListener('pipeline', (message) => {
    setLive({ ok: true, at: Date.now(), err: null });
    const p = JSON.parse(message.data);
    applyPipeline(p);
    if (lastDoc) lastDoc.pipelines = pipelines;
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
