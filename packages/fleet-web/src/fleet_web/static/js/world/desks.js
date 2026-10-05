// Which desk each job on a floor sits at. A job keeps its desk for as long as it is on the floor, across data updates
// and reloads, and others coming or going never move it: a new job takes the lowest free desk (the workarea bench's
// first), in order of creation, then id. The same jobs always give the same desks. Given desks are remembered (per
// project, in this browser, the last MEMORY of them), so a job that is away for an update, or a reload that first sees
// an older state, gets its own desk back when it is free. Jobs beyond the floor's desks are not seated (`unseated`).

const KEY = 'fleet.world.desks';
const MEMORY = 200;

// jobs: [{ key, createdAt }]; held: Map key → desk remembered (present or not); count: desks on the floor; current:
// the desks given last time (they win: a job sitting at its desk is never moved). Returns the desks of the jobs given,
// and the memory to keep.
export function assignDesks(jobs, held, count, current = new Map()) {
  const out = new Map(), taken = new Set(), unseated = [];
  const order = [...jobs].sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  for (const from of [current, held]) for (const j of order) {   // its desk now, then a remembered one if free
    const d = from.get(j.key);
    if (!out.has(j.key) && d != null && d < count && !taken.has(d)) { out.set(j.key, d); taken.add(d); }
  }
  let next = 0;
  for (const j of order) {
    if (out.has(j.key)) continue;
    while (taken.has(next)) next++;
    if (next >= count) { unseated.push(j.key); continue; }
    out.set(j.key, next); taken.add(next);
  }
  const memory = new Map([...held].filter(([k]) => !out.has(k)));
  for (const [k, d] of out) memory.set(k, d);   // (the most recent last)
  while (memory.size > MEMORY) memory.delete(memory.keys().next().value);
  return { desks: out, unseated, memory };
}

// the desks remembered for a project in this browser
export function heldDesks(project) {
  try {
    const all = JSON.parse(localStorage.getItem(KEY) || '{}');
    return new Map(Object.entries(all[project] || {}));
  } catch (err) {
    return new Map();   // (storage unavailable or unreadable: assignment still follows creation order)
  }
}
export function keepDesks(project, desks) {
  try {
    const all = JSON.parse(localStorage.getItem(KEY) || '{}');
    all[project] = Object.fromEntries(desks);
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch (err) { /* private mode: desks are still stable within the page */ }
}
