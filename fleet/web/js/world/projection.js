// The sprite world's one camera: orthographic, pitch 28°, yaw 33°.
// World space is metres: x along the back wall, y towards it, z up. "Plane" coordinates (u, v) are screen axes in
// metres, so a view is just a centre (u, v) and a zoom in pixels per metre (ppm); see docs/design/sprite-world.md.

// The image model's own camera, measured from the AI furniture and the concept images (sprite-world.md, floor review
// 1): rendered architecture uses it too, so walls and furniture share one projection.
export const PITCH = 28, YAW = 33;
const P = PITCH * Math.PI / 180, Y = YAW * Math.PI / 180;
const RIGHT = [Math.cos(Y), Math.sin(Y), 0];
const DOWN = [Math.sin(P) * Math.sin(Y), -Math.sin(P) * Math.cos(Y), -Math.cos(P)];
const TOWARDS = [Math.sin(Y) * Math.cos(P), -Math.cos(Y) * Math.cos(P), Math.sin(P)];
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
