// Applying fleet state from the server stream: entities, dismissals and finished-job retirement.

import { QS, RD, REDUCED } from './env.js';
import { store } from './util.js';
import { ROBOT, drawSign } from './scene.js';
import { ents, everLoaded, hosts, live, selectedKey, setEverLoaded, setHosts, setLive } from './model.js';
import { layoutRooms, rooms } from './rooms.js';
import { buildDocs, noteDocs } from './docs3d.js';
import { createEnt, dropEnt, updateTag } from './agents.js';
import { assignTargets } from './motion.js';
import {
  closePanel, collectEvents, renderFeed, renderLegend, renderLive, renderPanel, renderStats, updateHint,
} from './panel.js';
import { openReader } from './reader.js';

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
// Finished jobs leave the deck on their own: each room keeps its few most recent for a while, the rest are counted
// in a header chip that shows them again. Failed and stalled jobs stay until dismissed — they need you.
const FINISHED_STATUSES = new Set(['done', 'cancelled']);
const FINISHED_LINGER_SECONDS = 10 * 60;
const FINISHED_PER_ROOM = 3;
const RETIRE_CHECK_MS = 30000;
export let retiredCount = 0, showFinished = false;
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
export function toggleFinished() {
  showFinished = !showFinished;
  if (lastDoc) applyState(lastDoc);
}
// jobs age out between state updates too
setInterval(() => { if (lastDoc) applyState(lastDoc); }, RETIRE_CHECK_MS);
export function dismiss(key) {
  const e = ents.get(key);
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

export function applyState(doc) {
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

// ------------------------------------------------------------------ data sources
// Server-sent events: a full state document whenever anything changes, pings in between.
// EventSource reconnects by itself after an error.
export function stream() {
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
