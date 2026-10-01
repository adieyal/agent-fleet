// What an android does, from the state of its job or session: the rules the deck (motion.js, state.js, agents.js) and
// the sprite world (world/crew.js) share. Pure: no renderer, no DOM, no clock; callers pass the time and keep the
// memory. Each renderer turns the answers into its own motion and clips.
//
// - Presence: which work has an android. Finished jobs leave (one that finishes while watched says goodbye first),
//   failed, blocked and stalled ones have none (their lantern carries them), an idle session leaves after half an hour quiet
//   unless a decision waits on it.
// - Activity: what it does (activityFor), held for a minimum dwell before it walks off to another station.
// - Reactions: a nod when a test run is followed by anything but an error, a head shake at an error.
// - Held items, and the resting look of finished and idle work.
// - Crowds: more than five at one station gather into one figure.

import { activityFor, activityOf, isActive } from './activity.js';

// What an android does for each activity (see activityOf). station: where it works (a list is walked in turn: a book
// from the shelf, then the armchair); hands: typing while seated; carry: what it holds; pace: strolls this far and back.
export const ACTS = {
  type:     { station: 'terminal', hands: true },
  test:     { station: 'terminal' },                   // watches the run; nods or shakes its head at the result
  ship:     { station: 'mail', carry: 'box' },         // commit/push: a parcel into the outbox
  build:    { station: 'rack' },                       // installs, builds, containers: waits by the machine
  edit:     { station: 'workbench', hands: true },
  review:   { station: 'workbench', carry: 'sheet' },  // diff/log/status: reads a printout at the desk
  doc:      { station: 'press' },                      // writing markdown: at the document press
  read:     { station: ['bookshelf', 'armchair'], carry: 'book' },
  search:   { station: 'cabinet' },
  web:      { station: 'comms' },
  plan:     { station: 'whiteboard' },
  think:    { station: 'think', pace: 1.4 },
  wait:     { station: 'kitchen' },                    // sleep, background jobs, questions: a coffee
  delegate: { station: 'partner' },
  idle:     { station: 'lounge', pace: 2.2 },
  await:    { station: 'stay' },                       // a live session waiting on its human: stays put, faces you
  dock: { station: 'dock' },
};
export function stationOf(act, stage = 0) {
  const s = (ACTS[act] || ACTS.think).station;
  return Array.isArray(s) ? s[Math.min(stage, s.length - 1)] : s;
}

// ------------------------------------------------------------------ presence
export const FINISHED = new Set(['done', 'cancelled']);
export const BLOCKED = new Set(['failed', 'lost', 'blocked', 'stalled']);   // lost: its agent died, as good as failed
export const LEAVE_WITHIN_S = 30;        // a finished android is off the floor within this, however slow the frames
export const IDLE_LEAVE_S = 30 * 60;

// A finished job is retired (no android) unless its android is on its way out, or saw it finish. prev: what the
// renderer knew of it ({ leaving, lastStatus }), or null.
export function retired(job, prev, { showFinished = false, reduced = false } = {}) {
  if (showFinished || !FINISHED.has(job.status)) return false;
  return reduced || !prev || !(prev.leaving || !FINISHED.has(prev.lastStatus));
}
// Work shown by its lantern, not an android: failed, lost, blocked and stalled jobs, and any in a quiet (background) room.
export const offFloor = (job, quiet = false) => BLOCKED.has(job.status) || quiet;
// A job that finished while watched says goodbye before it leaves: a thumbs-up when done, a wave when cancelled.
// null when there is nothing to say.
export function farewell(job, prev, { reduced = false } = {}) {
  if (reduced || !prev || !FINISHED.has(job.status) || FINISHED.has(prev.lastStatus)) return null;
  return { clip: job.status === 'done' ? 'ThumbsUp' : 'Wave', within: LEAVE_WITHIN_S };
}
// An idle session leaves after half an hour quiet and comes back with its next activity; one waiting on a decision
// keeps its android. nowS: seconds since the epoch.
export function departed(key, session, attention, nowS) {
  return session.status === 'idle' && nowS - session.updated_at > IDLE_LEAVE_S
    && !(attention || []).some(i => i.kind === 'decision' && i.state !== 'resolved' && i.owner.key === key);
}

// ------------------------------------------------------------------ activity
export const DWELL = 3.5;   // seconds an android stays on an activity before walking off to another station
// the activity a job asks for now; an unknown one keeps the current (a fresh android starts typing)
export function wanted(job, act) {
  const want = activityFor(job);
  return want === null ? (act === 'init' ? 'type' : act) : want;
}
// whether to take up `want` now: at once when it needs no walk (docked, idle, waiting, or the same station), otherwise
// once the dwell is over and the android has settled where it is (m: { act, stage, actSince, arrivedAt, slow })
export function mayChange(want, m, now) {
  if (want === m.act) return false;
  const settled = ['dock', 'idle', 'await'].includes(want) || m.act === 'init' || stationOf(want) === stationOf(m.act, m.stage);
  const dwelt = now - m.actSince > DWELL && (m.slow || (m.arrivedAt != null && now - m.arrivedAt > 1.2));
  return settled || dwelt;
}

// ------------------------------------------------------------------ reactions
// What happened since the last look: a test run followed by anything but an error passed (true, a nod), an error gets
// a head shake (false). mem: { evTs, testing }, kept by the caller per android (empty on first sight).
export function reactions(mem, job) {
  const evs = (job.events || []).filter(ev => ev.kind === 'tool' || ev.kind === 'text' || ev.kind === 'error');
  const last = evs.length ? evs[evs.length - 1] : null, out = [];
  if (mem.evTs === undefined || !isActive(job.status)) { mem.evTs = last ? last.ts : 0; mem.testing = activityOf(last) === 'test'; return out; }
  for (const ev of evs) {
    if (ev.ts <= mem.evTs) continue;
    if (ev.kind === 'error') { out.push(false); mem.testing = false; }
    else { if (mem.testing) out.push(true); mem.testing = activityOf(ev) === 'test'; }
  }
  if (last) mem.evTs = Math.max(mem.evTs, last.ts);
  return out;
}

// ------------------------------------------------------------------ looks
// What it holds: a parcel until it is posted, a book once pulled off the shelf, a printout at the desk.
// m: { act, stage, target (has one), walking, arrivedAt, dropped }
export function heldItem(status, m, now) {
  if (!isActive(status) || !m.target) return null;
  switch (m.act) {
    case 'ship': return m.dropped ? null : 'box';
    case 'read': return m.stage > 0 || (!m.walking && m.arrivedAt != null && now - m.arrivedAt > 1) ? 'book' : null;
    case 'review': return m.walking ? null : 'sheet';
  }
  return null;
}
// Finished and idle work rests with its face light low once it has arrived.
export const resting = (status, arrived) => (status === 'done' || status === 'cancelled' || status === 'idle') && arrived;

// ------------------------------------------------------------------ crowds
export const CROWD = 5;
// Androids at one station of a room, when more than CROWD: "room|station" → members, the selected one left out (it
// always stands on its own). list: [{ key, room, station, leaving }]
export function crowdsOf(list, selectedKey) {
  const at = new Map();
  for (const e of list) {
    if (e.leaving || !e.station || e.station === 'stay' || e.station === 'partner') continue;
    const key = e.room + '|' + e.station;
    if (!at.has(key)) at.set(key, []);
    at.get(key).push(e);
  }
  const out = new Map();
  for (const [key, all] of at) if (all.length > CROWD) out.set(key, all.filter(e => e.key !== selectedKey));
  return out;
}
