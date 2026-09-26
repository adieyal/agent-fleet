// The workarea bridge (L3 before work items exist): what each room's benches show, derived from an /api/state document
// alone. Pure — no DOM, no deck state — so the flat view here and a later baked-art scene read the same thing.
//
// A room is a label, as on the deck. A bench is one fleet job in it that is active (running, queued or stalled) or ran
// in the last hour; finished benches older than that are cleared away. Each bench has:
// - a plan wall: one tile per step, marked so it reads without colour — done flips to a check, running glows, failed
//   shows a cross, cancelled a dash, pending stays blank;
// - criteria lights: steps done out of the total (the steps stand in for completion criteria until milestones exist);
// - a report tray: the job's step reports, oldest step first.
// The room has a question desk with its open and acknowledged attention items, and the lantern over it (one per place,
// with a count), and footprints from the entrance to every bench where a run happened in the last hour.

export const RECENT_S = 3600;
const ACTIVE = new Set(['running', 'queued', 'stalled']);
const MARK = { done: 'check', running: 'glow', failed: 'cross', cancelled: 'dash', pending: 'blank' };
const SHOWN = new Set(['open', 'acknowledged']);

// When a job last did anything: its own update time or a step starting or finishing, whichever is latest.
export function lastRun(job) {
  const times = [job.updated_at, job.created_at, ...(job.steps || []).flatMap(s => [s.started_at, s.finished_at])];
  return Math.max(0, ...times.filter(t => typeof t === 'number'));
}

export function benchOf(host, job, now) {
  const steps = [...(job.steps || [])].sort((a, b) => a.index - b.index);
  const tiles = steps.map(s => ({ index: s.index, title: s.title || '', status: s.status, mark: MARK[s.status] ?? 'blank' }));
  const met = tiles.filter(t => t.status === 'done').length;
  const at = lastRun(job);
  return {
    key: `${host}:${job.id}`, host, id: job.id, title: job.description || '', agent: job.agent, status: job.status,
    active: ACTIVE.has(job.status), lastRun: at, recent: at >= now - RECENT_S,
    tiles,
    criteria: { met, total: tiles.length, lights: tiles.map(t => t.status === 'done') },
    reports: (job.documents || []).filter(d => d.kind === 'report').sort((a, b) => (a.step ?? 0) - (b.step ?? 0))
      .map(d => ({ id: d.id, name: d.name, step: d.step ?? null })),
  };
}

// The lantern for a set of items: null when there are none to show.
export function lanternOf(items) {
  if (!items.length) return null;
  return { count: items.length, level: items.some(i => i.state === 'open') ? 'open' : 'acknowledged',
    kind: items.some(i => i.kind === 'blocker') ? 'blocker' : 'decision' };
}

// Every room with a bench or an attention item, in name order.
export function workareasOf(state, now) {
  const rooms = new Map();
  const room = label => {
    if (!rooms.has(label)) rooms.set(label, { room: label, projectIds: [], benches: [], desk: { items: [], lantern: null }, footprints: [] });
    return rooms.get(label);
  };
  for (const h of state.hosts || []) for (const job of h.jobs || []) {
    if (!job.project) continue;
    const bench = benchOf(h.name, job, now);
    if (!bench.active && !bench.recent) continue;
    const r = room(job.project);
    r.benches.push({ ...bench, createdAt: job.created_at });
    if (job.project_id && !r.projectIds.includes(job.project_id)) r.projectIds.push(job.project_id);
  }
  for (const item of state.attention || []) {
    if (!item.project || !SHOWN.has(item.state)) continue;
    room(item.project).desk.items.push({ id: item.id, kind: item.kind, state: item.state, summary: item.summary,
      owner: item.owner?.key ?? null });
  }
  for (const r of rooms.values()) {
    r.benches.sort((a, b) => a.createdAt - b.createdAt || a.key.localeCompare(b.key));
    for (const b of r.benches) delete b.createdAt;
    r.desk.lantern = lanternOf(r.desk.items);
    r.footprints = r.benches.filter(b => b.recent).map(b => ({ from: 'entrance', to: b.key, at: b.lastRun }));
  }
  return [...rooms.values()].sort((a, b) => a.room.localeCompare(b.room));
}

export function workareaOf(state, label, now) {
  return workareasOf(state, now).find(r => r.room === label) ?? null;
}
