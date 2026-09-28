// The floor's robots and what they do, from each run's job: the deck's behaviour (activity.js, motion.js) on the v2
// robot sprites (robots.js). A robot comes out of the lift and walks to its desk (by way of the storage corner with a
// box when it is shipping; with a book or a printout when reading or reviewing), sits down and works: types, writes,
// reads or holds up a test tube. Stalled, it slumps and dims; finished, it gives a thumbs-up and rests with its face
// light low; failed, it falls in front of its desk and stays down. A test result or an error gets a nod or a shake.

import { activityFor } from '../activity.js';
import { along, length, route } from './nav.js';
import { facingOf, seat } from './robots.js';

const SPEED = 1.1;       // walking, metres per second
const SHIP_DWELL = 2.0;  // seconds holding the box at the storage corner before putting it down
// the seated loop for each activity (activity.js ACTS): hands on the keys, pencil and paper, a book, the test tube
const WORK = { type: 'Typing', edit: 'Typing', build: 'Typing', web: 'Typing', search: 'Typing', delegate: 'Typing', ship: 'Typing',
  doc: 'Writing', plan: 'Writing', think: 'Writing', read: 'SitRead', review: 'SitRead', test: 'Holding', wait: 'SitIdle' };
const CARRY = { ship: 'BoxWalk', read: 'BookWalk', review: 'SheetWalk' };

// what a run's robot does once it has arrived: { clip (a loop, or a once clip held), tone, after? (a once clip first) }
export function settle(run, act) {
  switch (run.status) {
    case 'failed': return { clip: 'Death', stand: true, tone: 'normal' };
    case 'stalled': return { clip: 'SitSlump', tone: 'stalled' };
    case 'queued': return { clip: 'SitIdle', tone: 'normal' };
    case 'done': case 'cancelled': return { clip: 'SitIdle', tone: 'resting', first: 'SitThumbsUp' };
  }
  return { clip: WORK[act] || 'Typing', tone: 'normal' };
}

// look: the host's colour and kit, the job's agent, a tone
export class Crew {
  constructor({ world, robots, grid, lift, store, runs, looks, acts, reduced = false, loop = false }) {
    Object.assign(this, { world, robots, grid, lift, store, reduced, loop });
    this.members = runs.map(run => {
      const act = acts(run), end = settle(run, act);
      // (motion.js tone: stalled dims throughout; the resting face comes once it has arrived)
      return { run, act, end, look: { ...looks(run), tone: end.tone === 'stalled' ? 'stalled' : 'normal' }, legs: [], leg: 0, state: 'waiting', id: `robot-${run.key}` };
    });
    this.seated = []; this.settled = [];
    this.onChange = null;
  }
  // every robot straight at its place (?seated, reduced motion)
  settleAll() { for (const m of this.members) this.arrive(m, true); }
  // the walks: one robot after another out of the lift
  start(now, stagger) {
    this.members.forEach((m, i) => {
      m.legs = this.plan(m);
      m.start = now + i * stagger;
      if (!m.legs) this.arrive(m, true);
    });
  }
  plan(m) {
    const { run, act } = m, dest = run.status === 'failed' ? run.spot : run.seat;
    const walk = (from, to, clip) => { const pts = route(this.grid, from, to); return pts && { walk: pts, L: length(pts), clip }; };
    const legs = [];
    if (act === 'ship' && run.status !== 'failed') {   // a parcel to the storage corner on the way in
      const a = walk(this.lift, this.store.spot, 'BoxWalk'), b = walk(this.store.spot, dest, 'Walking');
      if (!a || !b) return null;
      legs.push(a, { clip: 'BoxIdle', dir: 'W', secs: SHIP_DWELL }, b);
    } else {
      const a = walk(this.lift, dest, CARRY[act] || 'Walking');
      if (!a) return null;
      legs.push(a);
    }
    return legs;
  }

  // advance every walking robot to `now` (seconds); true while any still moves or plays a once clip
  step(now) {
    let busy = false;
    for (const m of this.members) {
      if (m.state === 'waiting' && m.legs && now < m.start) { busy = true; continue; }
      if (m.state === 'waiting' && m.legs) { m.state = 'walking'; m.leg = 0; m.legAt = m.start; }
      if (m.state === 'walking') {
        busy = true;
        const leg = m.legs[m.leg], t = now - m.legAt;
        if (leg.walk) {
          const { at, heading, done } = along(leg.walk, t * SPEED);
          if (done && this.loop) { leg.walk = leg.walk.slice().reverse(); m.legAt = now; continue; }   // ?loop: back and forth
          if (!done) { this.stand(m, leg.clip, facingOf(heading), [at[0], at[1], 0], null); continue; }
        } else if (t < leg.secs) {
          const c = this.robots.clip(leg.clip);
          this.stand(m, leg.clip, leg.dir, m.at, c.loop ? null : Math.min(c.frames - 1, Math.floor(t * c.fps)));
          continue;
        }
        m.leg++; m.legAt = now;
        if (m.leg >= m.legs.length) this.arrive(m, false, now);
      }
      if (m.once) busy = this.playOnce(m, now) || busy;
    }
    return busy;
  }

  // a standing or walking robot: one item, its clip, facing and frame (null: the clip's own loop)
  stand(m, clip, dir, at, cell) {
    const sprite = this.robots.sprite(m.look, clip, dir, 'all');
    const patch = { sprite, at, ...(cell != null ? { cell } : {}) };
    m.at = at; m.clip = clip; m.dir = this.robots.facing(clip, dir);
    if (!this.world.items.has(m.id)) this.world.add({ id: m.id, ...patch, ambient: true, place: `run:${m.run.key}` });
    else this.world.set(m.id, patch);
  }
  // seated at its desk (robots.js seat)
  sit(m, clip, cell) {
    const { run } = m, changed = m.clip !== clip;
    m.clip = clip; m.dir = 'S'; m.at = run.seat;
    if (this.world.items.has(m.id)) this.world.remove(m.id);
    seat(this.world, this.robots, { id: m.id, look: m.look, clip, seat: run.seat, chair: run.chair, desk: run.module, place: `run:${run.key}`, cell });
    if (changed) this.changed();   // (its bubble hangs from the new clip's head)
  }
  // at its place: sit down (or fall), then its loop
  arrive(m, instant, now = 0) {
    const { end } = m;
    m.state = 'arrived';
    m.look = { ...m.look, tone: end.tone };
    this.settled.push(m.run.key);
    if (end.stand) {   // failed: falls where it stopped, and stays down
      if (instant || this.reduced) { this.stand(m, end.clip, 'E', m.run.spot, this.robots.clip(end.clip).frames - 1); return; }
      m.at = m.at || m.run.spot;
      this.once(m, end.clip, 'E', now, null, true);
      return;
    }
    this.seated.push(m.run.key);
    if (instant || this.reduced) { this.sit(m, end.clip, null); this.changed(); return; }
    this.once(m, 'Sitting', 'S', now, () => (end.first ? this.once(m, end.first, 'S', m.onceEnd, () => this.sit(m, end.clip, null)) : this.sit(m, end.clip, null)));
    this.changed();
  }
  // a once clip, seated or standing; then `next`, or held on its last frame
  once(m, clip, dir, now, next, standing = false) {
    m.once = { clip, dir, from: now, next, standing, seated: !standing && this.robots.clip(clip).seated };
    m.onceEnd = now + this.robots.duration(clip);
    this.playOnce(m, now);
  }
  playOnce(m, now) {
    const o = m.once, c = this.robots.clip(o.clip);
    const f = Math.min(c.frames - 1, Math.floor((now - o.from) * c.fps));
    if (o.seated) this.sit(m, o.clip, f); else this.stand(m, o.clip, o.dir, m.at, f);
    if (now - o.from < c.frames / c.fps) return true;
    m.once = null;
    if (o.next) o.next();
    return !!m.once;
  }
  // a nod or a shake at a test result or an error (motion.js react): seated, the head only
  react(key, yes, now) {
    const m = this.members.find(x => x.run.key === key);
    if (!m || m.state !== 'arrived' || m.once || this.reduced || m.end.stand) return false;
    if (this.seated.includes(key)) this.once(m, yes ? 'SitNod' : 'SitShake', 'S', now, () => this.sit(m, m.end.clip, null));
    else this.once(m, yes ? 'Yes' : 'No', m.dir, now, () => this.stand(m, m.end.clip, m.dir, m.at, null), true);
    return true;
  }
  changed() { if (this.onChange) this.onChange(); }
}

// the sprites a crew will need soon (the walk and the first clips), so they appear without a delay
export function crewSprites(crew) {
  const ids = [];
  for (const m of crew.members) {
    for (const d of ['S', 'E', 'N', 'W']) ids.push(crew.robots.sprite(m.look, 'Walking', d, 'all'));
    if (CARRY[m.act]) for (const d of ['S', 'E', 'N', 'W']) ids.push(crew.robots.sprite(m.look, CARRY[m.act], d, 'all'));
  }
  return ids;
}
export { activityFor };
