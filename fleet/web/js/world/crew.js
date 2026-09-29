// The floor's robots and what they do, from each run's job: the shared behaviour (behaviour.js, as the deck) on the v2
// robot sprites (robots.js). A robot comes out of the lift and walks to its desk (by way of the storage corner with a
// box when it is shipping; with a book or a printout when reading or reviewing), sits down and works: types, writes,
// reads or holds up a test tube. Stalled, it slumps and dims; finished while you watch, it gives a thumbs-up and walks
// back to the lift; failed, it falls in front of its desk and stays down. A test result or an error gets a nod or a
// shake. Live (update), robots come and go with their jobs; a change of work waits out the deck's dwell.

import { activityFor } from '../activity.js';
import { ACTS, FINISHED, mayChange, reactions } from '../behaviour.js';
import { along, length, route } from './nav.js';
import { facingOf, seat } from './robots.js';

const SPEED = 1.1;       // walking, metres per second
const SHIP_DWELL = 2.0;  // seconds holding the box at the storage corner before putting it down
// the seated loop for each activity (behaviour.js ACTS): hands on the keys, pencil and paper, a book, the test tube
const WORK = { type: 'Typing', edit: 'Typing', build: 'Typing', web: 'Typing', search: 'Typing', delegate: 'Typing', ship: 'Typing',
  doc: 'Writing', plan: 'Writing', think: 'Writing', read: 'SitRead', review: 'SitRead', test: 'Holding', wait: 'SitIdle' };
const CARRY = { box: 'BoxWalk', book: 'BookWalk', sheet: 'SheetWalk' };
const carryOf = act => CARRY[(ACTS[act] || {}).carry] || null;

// what a run's robot does once it has arrived: { clip (a loop, or a once clip held), tone, first? (a once clip first) }
export function settle(run, act) {
  switch (run.status) {
    case 'failed': return { clip: 'Death', stand: true, tone: 'normal' };
    case 'stalled': return { clip: 'SitSlump', tone: 'stalled' };
    case 'blocked': return { clip: 'SitIdle', tone: 'normal' };   // waiting on its supervisor, not broken
    case 'queued': return { clip: 'SitIdle', tone: 'normal' };
    case 'done': case 'cancelled': return { clip: 'SitIdle', tone: 'resting', first: 'SitThumbsUp' };
  }
  return { clip: WORK[act] || 'Typing', tone: 'normal' };
}

// look: the host's colour and kit, the job's agent, a tone
export class Crew {
  constructor({ world, robots, grid, lift, store, runs, looks, acts, reduced = false, loop = false }) {
    Object.assign(this, { world, robots, grid, lift, store, reduced, loop, looks, acts });
    this.members = runs.map(run => this.member(run, 0));
    this.seated = []; this.settled = [];
    this.onChange = null;
  }
  member(run, now) {
    const act = this.acts(run), end = settle(run, act);
    // (motion.js tone: stalled dims throughout; the resting face comes once it has arrived)
    return { run, act, actSince: now, end, look: { ...this.looks(run), tone: end.tone === 'stalled' ? 'stalled' : 'normal' }, legs: [], leg: 0,
      state: 'waiting', id: `robot-${run.key}`, lastStatus: run.status, leaving: false, mem: {} };
  }
  // every robot straight at its place (?seated, reduced motion)
  settleAll() { for (const m of this.members) if (m.state === 'waiting') this.arrive(m, true); }
  // the walks: one robot after another out of the lift
  start(now, stagger) {
    this.members.forEach((m, i) => {
      if (m.state !== 'waiting') return;
      m.legs = this.plan(m);
      m.start = now + i * stagger;
      if (!m.legs) this.arrive(m, true);
    });
  }
  walk(from, to, clip) { const pts = route(this.grid, from, to); return pts && { walk: pts, L: length(pts), clip }; }
  plan(m) {
    const { run, act } = m, dest = run.status === 'failed' ? run.spot : run.seat;
    const legs = [];
    if (act === 'ship' && run.status !== 'failed') {   // a parcel to the storage corner on the way in
      const a = this.walk(this.lift, this.store.spot, 'BoxWalk'), b = this.walk(this.store.spot, dest, 'Walking');
      if (!a || !b) return null;
      legs.push(a, { clip: 'BoxIdle', dir: 'W', secs: SHIP_DWELL }, b);
    } else {
      const a = this.walk(this.lift, dest, carryOf(act) || 'Walking');
      if (!a) return null;
      legs.push(a);
    }
    return legs;
  }

  // Live: bring the crew in line with the floor's runs (layout.js runs, each at its desk). New runs walk in from the
  // lift (or are placed at once: instant), changed ones take up their new work and look, runs gone walk back to the
  // lift and leave. Seconds; true when anything changed.
  update(runs, now, instant = false) {
    const byKey = new Map(runs.map(r => [r.key, r]));
    let changed = false;
    for (const m of [...this.members]) {
      const run = byKey.get(m.run.key);
      byKey.delete(m.run.key);
      if (!run) { if (!m.leaving) { this.leave(m, now, instant); changed = true; } continue; }
      if (m.leaving) continue;
      const before = m.run;
      m.run = run;
      m.lastStatus = run.status;
      const act = this.acts(run);
      if (m.state !== 'arrived') {   // (takes effect on arrival; finished on the way in, it turns back)
        if (FINISHED.has(run.status) && !FINISHED.has(before.status)) { this.leave(m, now, instant); changed = true; continue; }
        if (act !== m.act) { m.act = act; m.actSince = now; }
        m.end = settle(run, act);
        continue;
      }
      if (run.status !== before.status) { this.change(m, act, now, before.status, instant); changed = true; continue; }
      // a change of work at the desk waits out the deck's dwell, so bursts of events don't flicker it
      if (act !== m.act && mayChange(act, { act: m.act, stage: 0, actSince: m.actSince, arrivedAt: m.arrivedAt, slow: false }, now)) {
        m.act = act; m.actSince = now;
        const end = settle(run, act);
        if (end.clip !== m.end.clip && !m.once && !end.stand) { m.end = end; this.sit(m, end.clip, null); changed = true; }
        else m.end = end;
      }
    }
    for (const run of byKey.values()) {
      const m = this.member(run, now);
      this.members.push(m);
      if (instant || this.reduced) { this.arrive(m, true, now); continue; }
      m.legs = this.plan(m); m.start = now;
      if (!m.legs) this.arrive(m, true, now);
      changed = true;
    }
    return changed;
  }
  // its job's status changed while it sits at its desk
  change(m, act, now, was, instant) {
    const { run } = m, end = settle(run, act);
    m.act = act; m.actSince = now; m.end = end;
    m.look = { ...m.look, tone: end.tone === 'stalled' ? 'stalled' : m.look.tone === 'stalled' ? 'normal' : m.look.tone };
    if (FINISHED.has(run.status) && !FINISHED.has(was)) {   // finished while watched: a thumbs-up (done), then off it goes
      if (instant || this.reduced || run.status !== 'done') { this.leave(m, now, instant); return; }
      this.once(m, 'SitThumbsUp', 'S', now, () => this.leave(m, m.onceEnd));
      return;
    }
    if (end.stand) {   // failed: up, a few steps out in front of the desk, and down
      const out = !instant && !this.reduced && this.walk(run.seat, run.spot, 'Walking');
      if (!out) { this.unseat(m); this.arrive(m, true, now); return; }
      this.unseat(m);
      m.legs = [out]; m.leg = 0; m.legAt = now; m.state = 'walking'; m.start = now;
      this.forget(m);
      return;
    }
    m.look = { ...m.look, tone: end.tone };
    if (m.once) m.once.next = () => this.sit(m, end.clip, null);
    else this.sit(m, end.clip, null);
  }
  // back to the lift and gone
  leave(m, now, instant = false) {
    m.leaving = true; m.once = null;
    this.forget(m);
    const from = m.at || m.run.seat;
    const out = !instant && !this.reduced && this.walk([from[0], from[1]], this.lift, 'Walking');
    this.unseat(m);
    if (!out) { this.drop(m); return; }
    m.legs = [out]; m.leg = 0; m.legAt = now; m.start = now; m.state = 'walking';
    this.changed();
  }
  drop(m) {
    this.unseat(m);
    if (this.world.items.has(m.id)) this.world.remove(m.id);
    this.members = this.members.filter(x => x !== m);
    this.forget(m);
    this.changed();
  }
  forget(m) {
    this.seated = this.seated.filter(k => k !== m.run.key);
    this.settled = this.settled.filter(k => k !== m.run.key);
  }
  unseat(m) {
    for (const p of ['low', 'high', 'shadow']) if (this.world.items.has(`${m.id}:${p}`)) this.world.remove(`${m.id}:${p}`);
  }
  // nods and shakes at what its job did since the last look (behaviour.js reactions); jobs: key → job
  noticeAll(jobs, now) {
    let any = false;
    for (const m of this.members) {
      const job = jobs.get(m.run.key);
      if (!job || m.leaving) continue;
      for (const yes of reactions(m.mem, job)) any = this.react(m.run.key, yes, now) || any;
    }
    return any;
  }

  // advance every walking robot to `now` (seconds); true while any still moves or plays a once clip
  step(now) {
    let busy = false;
    for (const m of [...this.members]) {
      if (m.state === 'waiting' && m.legs && now < m.start) { busy = true; continue; }
      if (m.state === 'waiting' && m.legs) { m.state = 'walking'; m.leg = 0; m.legAt = m.start; }
      if (m.state === 'walking') {
        busy = true;
        const leg = m.legs[m.leg], t = now - m.legAt;
        if (leg.walk) {
          const { at, heading, done } = along(leg.walk, t * SPEED);
          if (done && this.loop && !m.leaving) { leg.walk = leg.walk.slice().reverse(); m.legAt = now; continue; }   // ?loop: back and forth
          if (!done) { this.stand(m, leg.clip, facingOf(heading), [at[0], at[1], 0], null); continue; }
        } else if (t < leg.secs) {
          const c = this.robots.clip(leg.clip);
          this.stand(m, leg.clip, leg.dir, m.at, c.loop ? null : Math.min(c.frames - 1, Math.floor(t * c.fps)));
          continue;
        }
        m.leg++; m.legAt = now;
        if (m.leg >= m.legs.length) { if (m.leaving) { this.drop(m); continue; } this.arrive(m, false, now); }
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
    m.state = 'arrived'; m.arrivedAt = now;
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
    if (!m || m.state !== 'arrived' || m.once || this.reduced || m.end.stand || m.leaving) return false;
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
    const carry = carryOf(m.act);
    if (carry) for (const d of ['S', 'E', 'N', 'W']) ids.push(crew.robots.sprite(m.look, carry, d, 'all'));
  }
  return ids;
}
export { activityFor };
