// Draw order for standing objects, by their footprint boxes (world metres [x0, y0, z0, x1, y1, z1]). A long bench
// can be behind a robot at one end and in front of another at the other, so a single depth per sprite won't do.
// Two boxes are only ordered when their screen rectangles overlap; attached entries (a seated robot's under- and
// over-desk layers, a bench's front overlay) are placed around their host instead of being sorted.

import { depth } from './projection.js';

const centre = b => [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2];

// true when box a must be drawn before box b. Separated along an axis in the viewer's favour: further back (larger
// y), further left (the camera looks from slightly right) or lower. Intersecting boxes fall back to centre depth.
export function behind(a, b) {
  if (a[1] >= b[4]) return true;
  if (b[1] >= a[4]) return false;
  if (a[3] <= b[0]) return true;
  if (b[3] <= a[0]) return false;
  if (a[5] <= b[2]) return true;
  if (b[5] <= a[2]) return false;
  return depth(centre(a)) < depth(centre(b));
}

const overlaps = (r, s) => r.x < s.x + s.w && s.x < r.x + r.w && r.y < s.y + s.h && s.y < r.y + r.h;

// entries: { id, box, rect, attach?: { to, order } } with rect in plane metres. Returns them in draw order.
// Unattached entries are ordered topologically over the "behind" relation among overlapping pairs, ties and
// independent entries by centre depth; a cycle (only possible with intersecting boxes) is broken at the entry
// furthest back. Attached entries go immediately before (order < 0) or after (order > 0) their host, by order.
export function sortEntries(entries) {
  const free = entries.filter(e => !e.attach);
  const n = free.length;
  const d = free.map(e => depth(centre(e.box)));
  const after = free.map(() => []), deg = new Array(n).fill(0);
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    if (!overlaps(free[i].rect, free[j].rect)) continue;
    const [a, b] = behind(free[i].box, free[j].box) ? [i, j] : [j, i];
    after[a].push(b); deg[b]++;
  }
  const done = new Array(n).fill(false), order = [];
  for (let k = 0; k < n; k++) {
    let pick = -1;
    for (let i = 0; i < n; i++) if (!done[i] && deg[i] === 0 && (pick < 0 || d[i] < d[pick] || (d[i] === d[pick] && i < pick))) pick = i;
    if (pick < 0) for (let i = 0; i < n; i++) if (!done[i] && (pick < 0 || d[i] < d[pick])) pick = i;   // a cycle
    done[pick] = true; order.push(free[pick]);
    for (const b of after[pick]) deg[b]--;
  }
  const byHost = new Map();
  for (const e of entries) if (e.attach) {
    if (!byHost.has(e.attach.to)) byHost.set(e.attach.to, []);
    byHost.get(e.attach.to).push(e);
  }
  const out = [];
  for (const e of order) {
    const att = (byHost.get(e.id) || []).sort((a, b) => a.attach.order - b.attach.order);
    out.push(...att.filter(a => a.attach.order < 0), e, ...att.filter(a => a.attach.order > 0));
    byHost.delete(e.id);
  }
  for (const rest of byHost.values()) out.push(...rest);   // a host that isn't drawn: its attachments go last
  return out;
}
