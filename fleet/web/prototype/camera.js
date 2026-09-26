// Camera controls for the workbench prototype: an orthographic three-quarter view that can zoom (wheel or
// pinch, about the pointer), pan (drag) and turn a little (right-drag or shift-drag), with damping.
//
// Limits keep the view honest: zoom from ZOOM.min (the whole room) to ZOOM.max (the closest the baked
// lightmaps stay sharp), a small clamped turn so the cut-away walls never show their backs, and a target
// kept over the room. Reduced motion (or ?still) moves instantly, with no damping.
import * as THREE from 'three';

// max: measured by art/scripts/zoom_sharpness.py. The lightmaps bake 1.5 cm per texel; at zoom z a texel
// spans 2.57 z CSS px on a 941 px tall view. Baked contact shadows stay crisp to ~4.6 px per texel (1.8x)
// and smear visibly beyond 5 (2x); the albedo textures (0.6-2 mm per texel) stay sharp well past that.
export const ZOOM = { min: 0.6, max: 1.8 };
const TURN = { yaw: 15, pitch: 4 };  // degrees either side of the home view
const DRAG_PX = 5;                    // below this a press is a click, not a drag
const DAMPING = 10;                   // per second: higher settles faster

export class ViewController {
  /**
   * @param {THREE.OrthographicCamera} camera
   * @param {HTMLElement} element  the canvas receiving pointer and wheel input
   * @param {{target: THREE.Vector3, pitch: number, yaw: number, height: number}} home  in three's
   *   coordinates (Y up); pitch and yaw in degrees, yaw measured as in the Blender scene
   * @param {{min: THREE.Vector3, max: THREE.Vector3}} bounds  where the target may go
   * @param {boolean} instant  no damping (reduced motion)
   */
  constructor(camera, element, home, bounds, instant) {
    Object.assign(this, { camera, element, home, bounds, instant });
    this.goal = this.homeState();
    this.now = this.homeState();
    this.pointers = new Map();
    this.dragged = false;
    this.listen();
    this.apply();
  }

  homeState() {
    return { target: this.home.target.clone(), zoom: 1, yaw: 0, pitch: 0 };
  }

  reset() {
    this.goal = this.homeState();
  }

  /** Jump to a view (zoom about the home target; `target` in three's coordinates), clamped to the limits. */
  set({ zoom = this.goal.zoom, yaw = this.goal.yaw, pitch = this.goal.pitch, target } = {}) {
    Object.assign(this.goal, { zoom, yaw, pitch });
    if (target) this.goal.target.set(...target);
    this.clamp();
    this.now = { ...this.goal, target: this.goal.target.clone() };
    this.apply();
  }

  /** The camera's right and up axes for a state: panning and zooming move the target along them. */
  axes(state) {
    const p = THREE.MathUtils.degToRad(this.home.pitch + state.pitch);
    const y = THREE.MathUtils.degToRad(this.home.yaw + state.yaw);
    // direction from target to camera, Blender (x, y, z) -> three (x, z, -y)
    const back = new THREE.Vector3(Math.sin(y) * Math.cos(p), Math.sin(p), Math.cos(y) * Math.cos(p));
    const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), back).normalize();
    const up = new THREE.Vector3().crossVectors(back, right).normalize();
    return { back, right, up };
  }

  halfSize(zoom) {
    const h = this.home.height / 2 / zoom;
    return { h, w: h * this.element.clientWidth / this.element.clientHeight };
  }

  clamp() {
    const g = this.goal;
    g.zoom = THREE.MathUtils.clamp(g.zoom, ZOOM.min, ZOOM.max);
    g.yaw = THREE.MathUtils.clamp(g.yaw, -TURN.yaw, TURN.yaw);
    g.pitch = THREE.MathUtils.clamp(g.pitch, -TURN.pitch, TURN.pitch);
    g.target.clamp(this.bounds.min, this.bounds.max);
  }

  /** Zoom by `factor` keeping the point under the pointer (normalised device coords) where it is. */
  zoomAt(factor, ndcX, ndcY) {
    const g = this.goal;
    const before = this.halfSize(g.zoom);
    g.zoom = THREE.MathUtils.clamp(g.zoom * factor, ZOOM.min, ZOOM.max);
    const after = this.halfSize(g.zoom);
    const { right, up } = this.axes(g);
    g.target.addScaledVector(right, ndcX * (before.w - after.w)).addScaledVector(up, ndcY * (before.h - after.h));
    this.clamp();
  }

  panPixels(dx, dy) {
    const g = this.goal;
    const { w, h } = this.halfSize(g.zoom);
    const { right, up } = this.axes(g);
    g.target.addScaledVector(right, -dx * 2 * w / this.element.clientWidth)
      .addScaledVector(up, dy * 2 * h / this.element.clientHeight);
    this.clamp();
  }

  turnPixels(dx, dy) {
    this.goal.yaw -= dx * 0.15;
    this.goal.pitch += dy * 0.08;
    this.clamp();
  }

  ndc(e) {
    const r = this.element.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1];
  }

  listen() {
    const el = this.element;
    el.style.touchAction = 'none';
    el.addEventListener('wheel', e => {
      e.preventDefault();
      this.zoomAt(Math.exp(-e.deltaY * 0.0015), ...this.ndc(e));
    }, { passive: false });
    el.addEventListener('contextmenu', e => e.preventDefault());
    el.addEventListener('pointerdown', e => {
      el.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY,
        turn: e.button === 2 || e.shiftKey });
      if (this.pointers.size === 1) this.dragged = false;
    });
    el.addEventListener('pointermove', e => {
      const p = this.pointers.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      if (Math.hypot(e.clientX - p.x0, e.clientY - p.y0) > DRAG_PX) this.dragged = true;
      if (this.pointers.size === 2) {
        // pinch: zoom by the change in finger distance about their midpoint, pan by the midpoint's move
        const [a, b] = [...this.pointers.values()];
        const before = Math.hypot(a.x - b.x, a.y - b.y);
        p.x = e.clientX;
        p.y = e.clientY;
        const after = Math.hypot(a.x - b.x, a.y - b.y);
        const mid = { clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 };
        if (before > 0) this.zoomAt(after / before, ...this.ndc(mid));
        this.panPixels(dx / 2, dy / 2);
        return;
      }
      p.x = e.clientX;
      p.y = e.clientY;
      if (!this.dragged) return;
      if (p.turn) this.turnPixels(dx, dy);
      else this.panPixels(dx, dy);
    });
    const end = e => {
      this.pointers.delete(e.pointerId);
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  }

  /** Ease the view towards the goal; returns whether it moved. */
  update(dt) {
    const k = this.instant ? 1 : 1 - Math.exp(-DAMPING * dt);
    const n = this.now, g = this.goal;
    const moving = n.target.distanceTo(g.target) > 1e-4 || Math.abs(n.zoom - g.zoom) > 1e-4
      || Math.abs(n.yaw - g.yaw) > 1e-3 || Math.abs(n.pitch - g.pitch) > 1e-3;
    if (!moving) return false;
    n.target.lerp(g.target, k);
    n.zoom += (g.zoom - n.zoom) * k;
    n.yaw += (g.yaw - n.yaw) * k;
    n.pitch += (g.pitch - n.pitch) * k;
    this.apply();
    return true;
  }

  apply() {
    const n = this.now;
    const { back } = this.axes(n);
    const { w, h } = this.halfSize(n.zoom);
    this.camera.position.copy(n.target).addScaledVector(back, 60);
    this.camera.lookAt(n.target);
    Object.assign(this.camera, { left: -w, right: w, top: h, bottom: -h });
    this.camera.updateProjectionMatrix();
  }

  /** The current view, for tests and the console. */
  state() {
    const n = this.now;
    return { zoom: n.zoom, yaw: n.yaw, pitch: n.pitch, target: n.target.toArray(), limits: { ...ZOOM, ...TURN } };
  }
}
