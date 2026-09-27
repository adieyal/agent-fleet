// Walking routes on the floor: a grid of 0.3 m cells, blocked wherever furniture stands (its footprint padded by a
// robot's radius), searched with A* over eight neighbours and then pulled taut into straight segments. Pure: the
// floor layout and the renderer supply the footprints. See docs/design/sprite-world.md, "Routes and motion".

export const CELL = 0.3, RADIUS = 0.25;

// bounds: { x0, y0, x1, y1 } metres; blocks: [x0, y0, x1, y1] floor rectangles
export function navGrid(bounds, blocks, { cell = CELL, pad = RADIUS } = {}) {
  const cols = Math.ceil((bounds.x1 - bounds.x0) / cell), rows = Math.ceil((bounds.y1 - bounds.y0) / cell);
  const free = new Uint8Array(cols * rows).fill(1);
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const x = bounds.x0 + (c + 0.5) * cell, y = bounds.y0 + (r + 0.5) * cell;
    if (x < bounds.x0 + pad || x > bounds.x1 - pad || y < bounds.y0 + pad || y > bounds.y1 - pad) free[r * cols + c] = 0;
    else if (blocks.some(b => x > b[0] - pad && x < b[2] + pad && y > b[1] - pad && y < b[3] + pad)) free[r * cols + c] = 0;
  }
  return { ...bounds, cell, cols, rows, free };
}

const cellOf = (g, [x, y]) => [Math.floor((x - g.x0) / g.cell), Math.floor((y - g.y0) / g.cell)];
const centre = (g, c, r) => [g.x0 + (c + 0.5) * g.cell, g.y0 + (r + 0.5) * g.cell];
const isFree = (g, c, r) => c >= 0 && r >= 0 && c < g.cols && r < g.rows && g.free[r * g.cols + c] === 1;
export const walkable = (g, p) => isFree(g, ...cellOf(g, p));

// the free cell nearest a point (a seat's point is inside its desk's padding), or null
function nearestFree(g, p) {
  const [c0, r0] = cellOf(g, p);
  for (let k = 0; k < Math.max(g.cols, g.rows); k++) {
    let best = null, bd = Infinity;
    for (let r = r0 - k; r <= r0 + k; r++) for (let c = c0 - k; c <= c0 + k; c++) {
      if (Math.max(Math.abs(c - c0), Math.abs(r - r0)) !== k || !isFree(g, c, r)) continue;
      const [x, y] = centre(g, c, r), d = Math.hypot(x - p[0], y - p[1]);
      if (d < bd) { bd = d; best = [c, r]; }
    }
    if (best) return best;
  }
  return null;
}

// can a walker go straight from a to b? (sampled at a quarter cell)
export function clear(g, a, b) {
  const n = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (g.cell / 4));
  for (let i = 0; i <= n; i++) if (!walkable(g, [a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n])) return false;
  return true;
}

// points from `from` to `to` (both included), or null when there is no way through. Either end may be inside
// furniture's padding (a lift threshold, a seat): the route leaves and enters it by the nearest free cell.
export function route(g, from, to) {
  const s = nearestFree(g, from), t = nearestFree(g, to);
  if (!s || !t) return null;
  const idx = (c, r) => r * g.cols + c, goal = idx(...t);
  const cost = new Float64Array(g.cols * g.rows).fill(Infinity), prev = new Int32Array(g.cols * g.rows).fill(-1);
  const h = i => Math.hypot(i % g.cols - t[0], Math.floor(i / g.cols) - t[1]);
  const open = [[h(idx(...s)), idx(...s)]];
  cost[idx(...s)] = 0;
  const done = new Uint8Array(g.cols * g.rows);
  while (open.length) {
    let bi = 0;
    for (let i = 1; i < open.length; i++) if (open[i][0] < open[bi][0]) bi = i;
    const [, cur] = open[bi];
    open[bi] = open[open.length - 1]; open.pop();
    if (done[cur]) continue;
    done[cur] = 1;
    if (cur === goal) break;
    const c = cur % g.cols, r = Math.floor(cur / g.cols);
    for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const nc = c + dc, nr = r + dr;
      if (!isFree(g, nc, nr)) continue;
      if (dc && dr && (!isFree(g, c + dc, r) || !isFree(g, c, r + dr))) continue;   // no cutting corners
      const n = idx(nc, nr), nd = cost[cur] + (dc && dr ? Math.SQRT2 : 1);
      if (nd < cost[n]) { cost[n] = nd; prev[n] = cur; open.push([nd + h(n), n]); }
    }
  }
  if (!done[goal]) return null;
  const cells = [];
  for (let i = goal; i !== -1; i = prev[i]) cells.push(centre(g, i % g.cols, Math.floor(i / g.cols)));
  cells.reverse();
  const pts = [from, ...cells, to];
  // pull taut: from each kept point, jump to the furthest point still in a straight clear line
  const out = [pts[0]];
  let i = 0;
  while (i < pts.length - 1) {
    let j = pts.length - 1;
    while (j > i + 1 && !clear(g, pts[i], pts[j])) j--;
    out.push(pts[j]);
    i = j;
  }
  return out;
}

export const length = pts => pts.slice(1).reduce((s, p, i) => s + Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), 0);

// where along a route a walker is after `d` metres: { at: [x, y], heading (radians, anticlockwise from +x), done }
export function along(pts, d) {
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const heading = Math.atan2(b[1] - a[1], b[0] - a[0]);
    if (d <= seg || i === pts.length - 1) {
      const k = seg ? Math.min(1, d / seg) : 1;
      return { at: [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k], heading, done: i === pts.length - 1 && d >= seg };
    }
    d -= seg;
  }
  return { at: pts[pts.length - 1], heading: 0, done: true };
}
