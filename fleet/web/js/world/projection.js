// The sprite world's one camera: the canonical camera every Fleet render shares (docs/design/art-direction.md,
// "Camera"; art/scripts/artlib.py canonical_projection): oblique onto a vertical picture plane, yaw 30°, rays falling
// at atan(1/2), so verticals stay vertical and full length.
// World space is metres: x along the back wall, y towards it, z up. "Plane" coordinates (u, v) are screen axes in
// metres, so a view is just a centre (u, v) and a zoom in pixels per metre (ppm); see docs/design/sprite-world.md.

export const YAW = 30, DEPRESSION = Math.atan(0.5) * 180 / Math.PI;
const Y = YAW * Math.PI / 180, T = 0.5;   // T: tan of the depression
const RIGHT = [Math.cos(Y), Math.sin(Y), 0];
const DOWN = [T * Math.sin(Y), -T * Math.cos(Y), -1];
// the rays' direction, towards the viewer: every point along it lands on the same screen point
const TOWARDS = [Math.sin(Y), -Math.cos(Y), T].map(c => c / Math.hypot(1, T));
// one metre along world x, y and z on the screen (right, down) at 1 px/m: what a sprite's manifest records
export const AXES = [0, 1, 2].map(i => [RIGHT[i], DOWN[i]]);
// true when a manifest's camera ({ projection, axes_px_per_m }) is this one
export function sameCamera(c) {
  return c?.projection === 'oblique' && Array.isArray(c.axes_px_per_m) && c.axes_px_per_m.length === 3
    && c.axes_px_per_m.every((a, i) => Math.abs(a[0] - AXES[i][0]) < 1e-3 && Math.abs(a[1] - AXES[i][1]) < 1e-3);
}
// the screen slope (down per right) of any line along x: the back wall's foot, a bench's long edges
export const EDGE_SLOPE = DOWN[0] / RIGHT[0];

// a world point on the screen plane, in metres (u right, v down)
export function plane(p) {
  return [RIGHT[0] * p[0] + RIGHT[1] * p[1], DOWN[0] * p[0] + DOWN[1] * p[1] + DOWN[2] * (p[2] || 0)];
}
// larger is nearer the viewer
export function depth(p) {
  return TOWARDS[0] * p[0] + TOWARDS[1] * p[1] + TOWARDS[2] * (p[2] || 0);
}
// the world point at height z under a plane point
export function unplane(u, v, z = 0) {
  const vz = v - DOWN[2] * z;
  const det = RIGHT[0] * DOWN[1] - RIGHT[1] * DOWN[0];
  return [(u * DOWN[1] - RIGHT[1] * vz) / det, (RIGHT[0] * vz - DOWN[0] * u) / det, z];
}

// view = { u, v, ppm, W, H } in CSS pixels
export function toScreen(view, p) {
  const [u, v] = plane(p);
  return [view.W / 2 + (u - view.u) * view.ppm, view.H / 2 + (v - view.v) * view.ppm];
}
export function fromScreen(view, sx, sy, z = 0) {
  return unplane(view.u + (sx - view.W / 2) / view.ppm, view.v + (sy - view.H / 2) / view.ppm, z);
}

// the plane rectangle { x, y, w, h } covering a world box [x0, y0, z0, x1, y1, z1]
export function boxRect(b) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const x of [b[0], b[3]]) for (const y of [b[1], b[4]]) for (const z of [b[2], b[5]]) {
    const [u, v] = plane([x, y, z]);
    x0 = Math.min(x0, u); y0 = Math.min(y0, v); x1 = Math.max(x1, u); y1 = Math.max(y1, v);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// A framing: { target: [x, y, z], height } (metres of world shown top to bottom, as the prototypes' l2 camera) or
// { box: [x0, y0, z0, x1, y1, z1], margin } (fitted to the screen). Returns { u, v, ppm }.
export function fit(frame, W, H) {
  if (frame.box) {
    const r = boxRect(frame.box), m = 1 + (frame.margin ?? 0.05) * 2;
    return { u: r.x + r.w / 2, v: r.y + r.h / 2, ppm: Math.min(W / (r.w * m), H / (r.h * m)) };
  }
  const [u, v] = plane(frame.target);
  return { u, v, ppm: H / frame.height };
}
