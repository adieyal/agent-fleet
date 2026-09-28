// The v2 robot sprites (fleet/web/assets/world/robot/sprites/, see docs/design/robot-sprites.md) in the sprite world.
// A drawn robot is a stack of layers from one frame: its contact shadow, the body (split at the desk top when seated),
// the agent's face, the host's kit and the clip's items. Each look (host colour, kit, agent, tone), clip, facing and
// part becomes one engine sprite whose frames are composed from those layers the first time they are drawn: the body
// and kit tinted through their masks, the white face multiplied by the agent's colour. The engine then sorts, scales,
// caches and hit-tests them like any other sprite.
//
// Parts: 'all' (a standing or walking robot, shadow included), 'low' and 'high' (a seated robot's body below and above
// the desk top: drawn before and after its desk), 'shadow' (a seated robot's shadow alone, for the ground).

import { PITCH, YAW } from './projection.js';
import { canvas, loadImage } from './paint.js';

export const ROBOT_SPRITES = '/assets/world/robot/sprites/';
const AGENT = { claude: '#ff8f6b', codex: '#7ce7ff' };   // looks.js AGENT_COLOR
const FACE = { codex: 'face_eyes', claude: 'face_band' };
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const mixHex = (a, b, k) => '#' + hex(a).map((v, i) => Math.round(v + (hex(b)[i] - v) * k).toString(16).padStart(2, '0')).join('');
const CELLS = 160 * 1024 * 1024;   // bytes of composed frames kept; the least recently drawn go first

// motion.js tone: a stalled robot dims body and face; a resting one (finished, or waiting) lowers its face light
export function colours({ host, agent, tone }) {
  const lit = AGENT[agent] || '#cbd5e1';
  if (tone === 'stalled') return { body: mixHex(host, '#475163', 0.55), face: mixHex(lit, '#1b2333', 0.6) };
  if (tone === 'resting') return { body: host, face: mixHex(lit, '#1b2333', 0.35) };
  return { body: host, face: lit };
}

export class Robots {
  static async load(world, url = ROBOT_SPRITES + 'sprites.json') {
    const man = await fetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error('missing ' + url))));
    if (man.version !== 2) throw new Error(`${url}: robot sprites version ${man.version}, the floor reads 2`);
    const c = man.camera;
    if (Math.abs(c.pitch_deg - PITCH) > 0.01 || Math.abs(c.yaw_deg - YAW) > 0.01) {
      throw new Error(`${url} was made for pitch ${c.pitch_deg}°, yaw ${c.yaw_deg}°; the world is ${PITCH}°, ${YAW}°`);
    }
    const robots = new Robots(world, man, new URL(url, location.href));
    await Promise.all(robots.res.filter(r => man.resolutions[r].load === 'eager').map(r => robots.ready(r)));
    return robots;
  }
  constructor(world, man, base) {
    this.world = world; this.man = man; this.base = base;
    this.res = Object.keys(man.resolutions).sort((a, b) => man.resolutions[a].scale - man.resolutions[b].scale);
    this.pages = new Map();   // resolution -> Promise of { color, mask, shadow } images
    this.loaded = new Map();
    this.tints = new Map();   // one layer image coloured for a colour, kept
    this.cells = new Map();   // composed frames, least recently drawn first
    this.bytes = 0;
    this.stats = { composed: 0, evicted: 0 };
  }
  get seatFurniture() { return this.man.seat_furniture; }
  clip(name) {
    const c = this.man.clips[name];
    if (!c) throw new Error('no robot clip ' + name);
    return c;
  }
  // the facing to draw a clip in: the one asked for if the clip has it, else the nearest it has (StandUp, ThumbsUp
  // and BoxIdle come in S and N only)
  facing(name, dir) {
    const dirs = this.clip(name).dirs;
    if (dirs[dir]) return dir;
    const deg = d => this.man.directions[d].facing_deg, want = deg(dir);
    const apart = d => { const a = Math.abs(deg(d) - want) % 360; return Math.min(a, 360 - a); };
    return Object.keys(dirs).sort((a, b) => apart(a) - apart(b))[0];
  }
  duration(name) { const c = this.clip(name); return c.frames / c.fps; }

  // the engine sprite for a look, clip, facing and part, defined the first time it is asked for. look: { host (hex),
  // kit, agent, tone }
  sprite(look, name, dir, part = 'all') {
    const c = this.clip(name), d = this.facing(name, dir);
    const id = `robot/${look.host}/${look.kit}/${look.agent}/${look.tone || 'normal'}/${name}/${d}/${part}`;
    if (this.world.sprites.has(id)) return id;
    const dd = c.dirs[d], ppm1 = this.man.camera.px_per_m_1x;
    const once = !c.loop;
    this.world.define(id, {
      layer: part === 'shadow' ? 'ground' : 'standing', hit: part === 'shadow' ? 'none' : 'alpha',
      footprint: [-0.25, -0.25, 0, 0.25, 0.25, this.man.robot.height_m],
      tiers: this.res.map(r => {
        const k = this.man.resolutions[r].scale;
        return { ppm: ppm1 * k, size: [dd.canvas[0] * k, dd.canvas[1] * k], anchor_px: [dd.foot[0] * k, dd.foot[1] * k],
          frames: dd.frames.length, ...(once || part === 'shadow' ? {} : { fps: c.fps }), file: `robot ${r}` };
      }),
      compose: { load: i => this.ready(this.res[i]), cell: (i, f) => this.cell(look, name, d, part, this.res[i], f) },
      robot: { clip: name, dir: d, part, once, hold: !!c.hold_last },
    });
    // the eager sets (1x, 2x) are ready at once, so a robot is never missing while a closer set loads on demand
    const s = this.world.sprites.get(id);
    this.res.forEach((r, i) => { if (this.man.resolutions[r].load === 'eager') this.world.fetchTier(s, i); });
    return id;
  }
  ready(res) {
    if (!this.pages.has(res)) {
      const r = this.man.resolutions[res], url = f => new URL(f, this.base).href;
      this.pages.set(res, Promise.all([
        Promise.all(r.pages.map(p => loadImage(url(p.color)))),
        Promise.all(r.pages.map(p => loadImage(url(p.mask)))),
        Promise.all(r.shadow_pages.map(p => loadImage(url(p.image)))),
      ]).then(([color, mask, shadow]) => { this.loaded.set(res, { color, mask, shadow }); }));
    }
    return this.pages.get(res);
  }

  // the layers of a part, in draw order (the manifest's draw_order); the desk goes between 'low' and 'high'
  layers(look, name, part) {
    const face = FACE[look.agent] || 'face_band', kit = 'acc_' + look.kit, items = this.clip(name).items;
    if (part === 'shadow') return ['shadow'];
    if (part === 'low') return ['body_low'];
    if (part === 'high') return ['body_high', face, kit, ...items];
    return ['shadow', 'body_low', 'body', 'body_high', face, kit, ...items];
  }
  // one frame of a part at a resolution, composed and kept (up to CELLS bytes)
  cell(look, name, dir, part, res, frame) {
    const key = `${res}|${look.host}|${look.kit}|${look.agent}|${look.tone || ''}|${name}|${dir}|${part}|${frame}`;
    const had = this.cells.get(key);
    if (had) { this.cells.delete(key); this.cells.set(key, had); return had; }
    const dd = this.clip(name).dirs[dir], f = dd.frames[frame], k = this.man.resolutions[res].scale;
    const out = canvas(dd.canvas[0] * k, dd.canvas[1] * k), g = out.getContext('2d');
    this.draw(g, look, f, res, this.layers(look, name, part));
    this.cells.set(key, out);
    this.bytes += out.width * out.height * 4;
    this.stats.composed++;
    for (const [old, c] of this.cells) {
      if (this.bytes <= CELLS) break;
      this.cells.delete(old); this.bytes -= c.width * c.height * 4; this.stats.evicted++;
    }
    return out;
  }
  draw(g, look, frame, res, names) {
    const man = this.man, lp = this.loaded.get(res), ri = this.res.indexOf(res), col = colours(look);
    for (const n of names) {
      const entries = frame.layers[n];
      if (!entries) continue;   // empty in this frame
      const e = entries[ri], [p, x, y, w, h, ox, oy] = e;
      if (man.masked.includes(n) || man.tinted_whole.includes(n)) {
        g.drawImage(this.tinted(res, e, col.body, man.tinted_whole.includes(n), man.grey), ox, oy);
      } else if (n in man.faces) {   // white emissive: multiplied by the agent's colour
        g.drawImage(this.tinted(res, e, col.face, true, '#ffffff'), ox, oy);
      } else if (n === 'shadow') {
        g.drawImage(lp.shadow[p], x, y, w, h, ox, oy, w * man.shadow_scale, h * man.shadow_scale);
      } else {
        g.drawImage(lp.color[p], x, y, w, h, ox, oy, w, h);
      }
    }
  }
  // a layer image coloured: per sRGB channel rgb * (1 - mask + mask * colour / base); `whole` ignores the mask
  tinted(res, e, colour, whole, base) {
    const [p, x, y, w, h] = e, key = `${res}|${p}|${x}|${y}|${colour}|${whole ? 1 : 0}`;
    if (this.tints.has(key)) return this.tints.get(key);
    const { color, mask } = this.loaded.get(res);
    const c = canvas(w, h), o = c.getContext('2d', { willReadFrequently: true });
    let m = null;
    if (!whole) {   // the mask is stored mask_scale times smaller: stretched over the layer
      const ms = this.man.resolutions[res].mask_scale;
      o.drawImage(mask[p], x / ms, y / ms, w / ms, h / ms, 0, 0, w, h);
      m = o.getImageData(0, 0, w, h).data;
      o.clearRect(0, 0, w, h);
    }
    o.drawImage(color[p], x, y, w, h, 0, 0, w, h);
    const d = o.getImageData(0, 0, w, h), b = hex(base), t = hex(colour).map((v, i) => v / b[i]);
    for (let i = 0; i < d.data.length; i += 4) {
      const k = m ? m[i] / 255 : 1;
      if (!k || !d.data[i + 3]) continue;
      for (let j = 0; j < 3; j++) d.data[i + j] = Math.min(255, d.data[i + j] * (1 - k + k * t[j]));
    }
    o.putImageData(d, 0, 0);
    this.tints.set(key, c);
    return c;
  }

  // a frame's anchor ('head_top', 'hand_l', 'hand_r', or a kit's top) as screen metres (right, down) from the foot,
  // at 1x: for bubbles and tags hung over a robot
  anchor(name, dir, frame, which, kit) {
    const dd = this.clip(name).dirs[this.facing(name, dir)], f = dd.frames[Math.min(frame, dd.frames.length - 1)];
    const p = (kit && f.anchors.kit_top[kit]) || f.anchors[which];
    const ppm1 = this.man.camera.px_per_m_1x;
    return [(p[0] - dd.foot[0]) / ppm1, (p[1] - dd.foot[1]) / ppm1];
  }
}

// A seated robot facing the viewer at a desk: its lower body drawn just before the desk item `desk`, its upper body
// (face, kit, items) just after, and its shadow on the ground under its chair, where a seated robot's shadow falls
// (the chair's seat takes it, not the floor under the robot, whose feet hang clear). seat: the floor point under the
// seat point; chair: the chair's floor point; cell: a frame of a once clip, or null for the clip's loop.
export function seat(world, robots, { id, look, clip, seat: at, chair, desk, place, cell = null, still = false }) {
  const frame = cell != null ? { cell } : {};
  for (const [part, order] of [['low', -1], ['high', 1]]) {
    const pid = `${id}:${part}`, sprite = robots.sprite(look, clip, 'S', part);
    if (world.items.has(pid)) world.set(pid, { sprite, at, ...frame });
    else world.add({ id: pid, of: id, sprite, at, attach: { to: desk, order }, ambient: true, still, place, ...frame });
  }
  const sid = `${id}:shadow`, shadow = robots.sprite(look, clip, 'S', 'shadow');
  if (!world.items.has(sid)) world.add({ id: sid, sprite: shadow, at: chair, still: true, hit: false });
  else if (world.items.get(sid).sprite !== shadow) world.set(sid, { sprite: shadow });
}

// the facing nearest a heading (radians from +x, anticlockwise seen from above): E +x, N +y (the back wall), W, S
// (towards the viewer)
export function facingOf(heading) {
  const dx = Math.cos(heading), dy = Math.sin(heading);
  return Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'E' : 'W') : (dy > 0 ? 'N' : 'S');
}
