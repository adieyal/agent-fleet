# Sprite world: runtime architecture

> **Status:** design for the sprite runtime that replaces runtime 3D in the world view. The meaning of every cue stays in the [PRD](workspace-prd.md) (*State encoding*, *World lexicon*); the look stays in [art direction](art-direction.md). This document covers how the world is laid out, drawn, animated and picked.

## Decisions this builds on

- **2D canvas, no WebGL.** The world must stay above 60 fps with the GPU disabled (`--disable-gpu`, SwiftShader). The bake-off measured B2 sprites at 4.3 ms a frame without a GPU; the pre-lit glTF variant ran at 6–8 fps (the bake-off in `art/bakeoff/` and `fleet/web/prototype/bakeoff.*`; its report is in fleet job 90208f's outbox).
- **Assets come from three sources**, one per kind of object:

  | Kind | Source | Why |
  |---|---|---|
  | Static props (benches, plants, shelves, lantern, question desk, podium, lobby furniture) | AI, one curated generation each (`art/scripts/gen_sprite.py` via ChatMock), finished by `finish.py` | Closest match to the concept art on the first try; nothing animates |
  | Floors and flat walls | Tiled textures, affine-mapped onto their planes | Exact for an orthographic camera; any length, a few KB |
  | Architecture with volume (wall caps, pilasters, corners, cut wall ends, slab edge, lift bay) | Blender renders from the sprite camera, on host home | Pieces must line up exactly and carry real contact shading |
  | Robots | Blender paper-doll layers (parallel job, `fleet/web/assets/world/robot/`) | Consistent frames, tint masks and occlusion layers; bake-off B2 frames stand in until they land |

- **Glow is never baked into a sprite.** Lamps, wall washers, lit tiles and the lantern halo are separate sprites switched by state, so light can follow activity.

## Coordinates and projection

**World space** is metres on one floor: `x` runs along the back wall from the left wall, `y` runs from the front edge of the floor towards the back wall, `z` is up. This is the bake-off's orientation (Blender, Z up), so bake-off scenes drop in by translation.

**One camera for every sprite and every zoom.** Orthographic, pitch 44.5°, yaw 21.25°: the l2 fit from `art/scripts/fit_camera.py`, already used to render B1 and to lay out B2. Changing it means re-rendering every Blender piece and regenerating every prop, so it is fixed. The camera never turns; the prototype's ±15° turn is a 3D-only feature and is dropped.

With `c = (cx, cy, cz)` the view centre and `ppm` the zoom in screen pixels per metre, a world point `p` lands at:

```
d  = p − c
sx = W/2 + ppm · ( 0.9320·dx + 0.3624·dy          )
sy = H/2 + ppm · ( 0.2540·dx − 0.6533·dy − 0.7133·dz)
depth(p) = 0.2585·x − 0.6648·y + 0.7009·z         (larger is nearer the viewer)
```

So one metre along `x` goes right and slightly down (slope 0.2726, the desk-edge slope in l2), along `y` up and right, and up the screen by 0.71 m per metre of height. The inverse onto the floor plane (`z = 0`) is a 2×2 solve, used for picking and for placing things under the pointer.

**Zoom is continuous.** The whole floor at 1672 × 941 is about 42 px/m; l2's framing is 171.5 px/m (941 px / 5.486 m); the close limit is 2× l2 (343 px/m). Every place has a *frame*, a world-space box; entering a place eases the camera to fit that box. There is no scene swap between levels.

**The floor grid.** Floors are tiled at 0.6 m (the floor texture's tile). Positions and footprints are continuous metres; the navigation grid is 0.3 m (half a tile). The back wall is divided into 3.6 m structural bays by pilasters, and the layout snaps places to bays (see *Floor layout*).

## Sprites

A **sprite** is an image, drawn at one of several pixel densities, with an **anchor**: the pixel that lands on a named world point of the object (a footprint centre, the far edge of a desk top, a seat). Placing a sprite projects its world point and subtracts the anchor scaled to the current zoom.

**Density tiers.** Sprites ship at 85.75, 171.5 and 343 px/m (½×, 1× and 2× the l2 density). The renderer picks the smallest tier at or above `ppm × devicePixelRatio`, and 686 px/m (4×) loads on demand for close zoom on high-DPI screens. AI props are generated at about 257 px/m, so their 2× and 4× tiers are the generation itself; they soften past it and that is accepted.

**Footprint.** Every standing object has a box in world space: `[x0, y0, z0, x1, y1, z1]` relative to its anchor point. Footprints drive depth sorting, the navigation grid, contact shadows and the fallback hit box. Objects are rendered in one facing each; a second facing is a second render, never a mirror (with a 21.25° yaw, a mirror shows the wrong side).

**Tint.** Host colours are applied at load: the grey shell multiplied by the host colour through the sprite's tint mask, into a cached bitmap per (sprite, frame sheet, colour). The bake-off's tinting (`fleet/web/prototype/bakeoff.js`, `tinted`) is the method.

**State variants** are derived once, cached, and never computed per frame:
- *stale*: desaturated and lifted towards the fog colour;
- *unavailable*: an outline ghost traced from the alpha mask;
- *dim* and *rest* for robots, as the deck's `tone()`: shell towards slate, eyes low.

### Manifest

Each asset family has a `manifest.json` beside its files under `fleet/web/assets/world/<family>/` (`arch`, `props`, `textures`, `robot`, `glow`). Every manifest records the camera it was made for; the loader refuses a manifest whose camera differs from the runtime's.

```jsonc
{
  "version": 1,
  "camera": { "pitch": 44.5, "yaw": 21.25 },
  "sprites": {
    "bench-3": {
      "source": "ai",                          // ai | blender | procedural
      "from": "art/props/raw/bench-3-v1.png",  // the curated generation; its sidecar holds the prompt
      "tiers": [                               // size is one frame; sheets add "frames", "fps", and "mask" for tint
        { "ppm": 171.5, "file": "bench-3@1x.webp", "size": [1117, 755], "anchor_px": [558, 244] },
        { "ppm": 343,   "file": "bench-3@2x.webp", "size": [2234, 1510], "anchor_px": [1116, 488] }
      ],
      "anchor": "desk_top_far_edge_middle",
      "anchor_world": [0, 0, 0.74],            // the anchor's offset from the instance origin, metres
      "footprint": [-2.7, -0.8, 0, 2.7, 0.1, 0.74],
      "seats": [                               // where robots sit, and what covers them
        { "at": [-1.8, 0.45, 0.47], "facing": "viewer", "front": "bench-3.front-0" },
        { "at": [0, 0.45, 0.47],    "facing": "viewer", "front": "bench-3.front-1" },
        { "at": [1.8, 0.45, 0.47],  "facing": "viewer", "front": "bench-3.front-2" }
      ],
      "lights": [                              // glow anchors, see Glow
        { "id": "lamp-0", "kind": "desk-lamp", "at": [-2.52, 0.02, 0.95] }
      ],
      "hit": "alpha"                           // alpha | box
    },
    "bench-3.front-0": { "...": "a crop of the desk-top props in front of seat 0, drawn over a seated robot" }
  },
  "textures": {
    "floor-tile": { "file": "floor-tile.webp", "size": [256, 256], "metres": 0.6 }
  }
}
```

The robot manifest is defined by the robot job in `docs/design/robot-sprites.md`. The runtime needs from it, per pose and facing: frame sheets with fps and loop flag; `under` and `over` layers for seated poses (the part below and above the desk top); a shell tint mask; the eyes as a separate layer so they can dim; and per-frame anchors for `seat` or `feet`, `hand.R` (held items), `head` (nods and shakes move the head layer) and `bubble` (the action glyph). Until those sprites land, `robot/placeholder.json` presents the bake-off B2 frames through the same schema: the B2 `cut` line splits each frame into `under` and `over`, `mask` becomes the tint mask, and the `seat` anchor is B2's `ref_px`. B2 has seated poses only, facing the viewer, so placeholder robots walk as their seated frame gliding. Swapping to the real robots is a change of manifest path.

## Layers and drawing

The canvas is drawn in five passes, back to front. Text and controls are DOM, not canvas.

| Pass | Contents | Cached? |
|---|---|---|
| 1. Shell | Sky gradient, floor slab and its edge, floor tiles, back and left walls with pilasters and caps, wall-mounted fixtures (lift bay, plan-wall frames, posters, shelves against the wall) | Yes: the ground snapshot, see below |
| 2. Ground | Contact shadows, rugs, footprints, parcels on the floor, floor-level glow spill | In the ground snapshot; a change repaints the snapshot |
| 3. Standing | Every object that can occlude another: furniture, props, robots, held items, the lantern, crates, dust sheets | No; redrawn only inside changed rectangles |
| 4. Light | Additive glow sprites: lamp pools, wall-washer scallops, lit plan tiles, lantern halo | No; redrawn with the standing pass |
| 5. DOM overlay | Labels, headlines, action glyph bubbles, lantern glyph and count, focus ring, hit targets for keyboard | DOM; positioned from the projection |

Nothing stands behind the back or left wall, so the walls and everything fixed to them never occlude and can live in the ground snapshot.

**The ground snapshot** (`ground.js`). Tiled planes are drawn through a skewed pattern, which a software canvas fills at about 20 ns a pixel: 20–40 ms for a full screen, far too slow to repaint every frame. So passes 1 and 2 are painted into a bitmap 1.5 times the screen's size, then blitted: shifted while panning and scaled while zooming. When the view settles somewhere the snapshot doesn't serve (another zoom, or panned past its margin), a new one is painted in 96-row bands at up to 6 ms a frame and swapped in when complete. A whole-room snapshot from the widest framing sits beneath it and fills any edge the current one doesn't reach while zooming out.

**Standing objects by dirty rectangle.** While the camera moves, every frame is drawn in full: the snapshot, then the standing sprites in depth order, then glow. While it is still, a change (an animation frame, an item set by the caller, a glow) marks its old and new screen rectangles, and only their union is repainted, from the snapshot up. At a steady zoom each sprite frame is drawn from a copy pre-scaled to that zoom at whole device pixels, so a repaint is a set of plain copies; while zooming, sprites are scaled on the fly, and one full frame from the pre-scaled copies follows when the zoom comes to rest.

**Render on demand.** The loop draws only when something changed: a robot moved or advanced a frame, warmth is fading, the camera is moving, a tier finished loading. Between animation frames it sleeps on a timer until the next frame is due rather than running every display refresh. A still scene draws nothing, which is also the PRD's calm rule. Animation clocks for background places run at the deck's `CALM` rate or stop.

**Budget.** The engine measures each frame's work (and, when frames run back to back, the gap between them, since a canvas can rasterise after the frame returns). When that runs over the budget (8 ms by default), ambient animation (items marked `ambient`, such as working robots' loops) updates half as often, down to an eighth; it recovers when frames are cheap again. Frames still show the right frame for the time, just fewer of them. Measured on carbon without a GPU at 1672 × 941 with the bake-off scene: zooming 15 ms a frame (median), panning 2.5 ms, an animation step under 1 ms.

### Depth sorting

A single depth value per sprite fails for long objects: a 5.4 m bench's anchor can be behind a robot at one end and in front of a robot at the other. Standing objects are sorted by their footprint boxes instead, as isometric engines do. For two boxes `A` and `B` whose screen rectangles overlap, `A` is drawn first if they are separated along any axis in the viewer's favour:

- `A.y0 ≥ B.y1` (A is further back), or
- `A.x1 ≤ B.x0` (A is further left; the camera looks slightly from the right), or
- `A.z1 ≤ B.z0` (A is below B).

If no axis separates them the boxes intersect, and the one with the smaller `depth()` at its box centre goes first. Static objects are sorted once, topologically, when the layout loads; dynamic sprites are inserted into that order each frame against the static neighbours they overlap.

**Seated robots** are the case a box cannot solve, because a robot's body passes through the desk's box. A seat carries its own order:

```
chair  →  robot.under  →  bench  →  robot.over  →  bench.front-N
```

`robot.under` is the part below the desk top, `robot.over` the part above it, and `bench.front-N` the desk-top props in front of seat N (the laptop in front of the typing robot in l2). The seat's entry replaces the robot's box in the sort. The bake-off found that a single cut line lets the robot cover props behind it; the `front` crop is the fix, and it comes from the bench's generation (cropped by `finish.py` against a seat mask), not from the robot.

## Floor layout: reconciling l1 and l2

`l1.png` shows a floor: benches in rows, a library and librarian at the back left, the lantern over the middle, the orchestrator's podium and a briefing board at the back, the lift and its panel on the right. `l2.png` shows one workarea close up: the lift on the left, then the question desk under the lantern, the plan wall with its criteria lights, a poster, and shelves; the bench in front, footprints leading from the lift to it.

Both are one layout seen at two zooms. l2's order along the wall wins where they disagree, because l2 is the reference for the workarea and its footprints show the arrival path the PRD asks for (*Run starts*). So the floor is l1 mirrored in one respect: the lift is at the left end of the back wall, the library at the right.

```
 back wall (y = 14.4)
 x=0      3.6              10.8             18.0             25.2            32.4
 ┌────────┬────────────────┬────────────────┬────────────────┬───────────────┐
 │ LIFT   │ ?  PLAN WALL   │ ?  PLAN WALL   │ ?  PLAN WALL   │ LIBRARY       │  ← wall bays
 │ brief- │    bench       │    bench       │    bench       │ shelves, cart │
 │ ing    │                │                │                │ archive shelf │
 ├────────┴────────────────┴────────────────┴────────────────┴───────────────┤  y ≈ 9.6
 │  aisle           podium                                                   │
 │   [board] bench       [board] bench       [board] bench                   │  ← open-floor lanes
 │   [board] bench       [board] bench       [board] bench                   │
 └───────────────────────────────────────────────────────────────────────────┘  y = 0 (front edge, cut away)
   left wall (x = 0)                                                  right edge cut away
```

- **The floor** is 32.4 × 14.4 m (54 × 24 tiles), with a 3.2 m back wall and a left wall; the near and right walls are cut away to their plinth. That frames at about 42 px/m on a 1672 × 941 screen with margin, matching l1's proportions.
- **The lift core** fills the first bay (x 0–3.6): lift doors and call buttons (a Blender piece with open-door frames), with the briefing board beside the doors (PRD: *briefing board by the lift doors*). Every robot arrives and leaves through it.
- **Wall lanes.** Each workstream (lane) gets a workarea: one bench, its plan wall, a question desk with the lantern hook, and a report tray. Three lanes fit along the back wall, one per 7.2 m double bay: question desk at the bay's left, plan wall between the pilasters, a pier with a poster or shelf at the right. The bench stands about 2.2 m in front of the wall with its seats on the far side, facing the viewer, as in l2.
- **Open-floor lanes.** Up to six more lanes stand in two rows on the open floor. Their plan wall is a freestanding board on castors behind the bench (l1's whiteboard stand), low enough (1.5 m) not to hide the row behind it.
- **Rooms** (epics) are clusters of lanes: consecutive lane slots with a shared floor plaque and a DOM headline, and optional low dividers, per the PRD's first L1 layout decision.
- **Role stations** have fixed slots: the library, book cart and archive shelf at the right end of the wall, the orchestrator's podium in the aisle in front of the wall lanes.
- **The l2 framing** is the frame of the first wall lane (x 3.6–10.8) with the lift's edge in view. Bake-off scene coordinates map onto the floor by translation (bake-off wall `y = 6.0` → `14.4`); the prototype fits the x offset so its l2 framing reproduces the bake-off frame.

**Places are stable.** Lane slots are assigned when a lane first appears and stored with the floor's layout; they never change with state or focus. A lane's bench persists and resets for each new slice (PRD: *the bench resets for the next slice*). Done, active and next slices read on the lane's plan wall (ticked, lit and blank columns), and finished slices leave as dossiers to the archive shelf. More lanes than slots (nine per floor) is a rearrange-mode decision, not an automatic reflow.

**Levels on one floor.** Concept `l1.png` is PRD level L1 (a floor); concept `l2.png` frames a workarea, PRD level L3. Level bands are zoom ranges with hysteresis, each deciding what the DOM overlay and the sprites show:

| Band | ppm (1× DPR) | Frame | Sprites | Text (DOM) |
|---|---|---|---|---|
| L1 floor | < 70 | whole floor | ½× tier; robots as small figures, frame 0 held unless in the focused place; plan walls show only lit rows; lamps as glow only | Room headlines, ≤ 12 words |
| L2 room | 70–130 | a room's lanes | 1× tier; robots animate in the room | Lane names and one-line status |
| L3 workarea | > 130 | a lane's bench and plan wall (l2) | 1× or 2×; tile ticks, criteria lights, action glyph bubbles | Short tile titles |

Crossing a band crossfades its overlay text over 200 ms; sprites never pop, since the tier switch happens only when the zoom settles.

## Glow: activity as light

Warmth is a per-workarea value from 0 to 1, eased towards 1 while runs are active there and to 0 when they stop (1.5 s, instant under reduced motion). It drives glow sprites attached to the `lights` anchors in prop manifests and to the wall bay:

| Glow | Where | Colour (art direction) |
|---|---|---|
| Desk-lamp pool | On the desk top under each lamp, plus the lamp's lit-shade sprite | `#fee095` |
| Wall-washer scallops | On the plan-wall bay, one per washer | `#fed9a1` |
| Floor spill | On the floor in front of an active bench | `#fee095`, low |
| Lit plan tiles | Per tile, from task state (not warmth) | `#fcb957` |
| Window glow (L0) | Per window of a windowed floor | `#d4a36b` |
| Lantern halo | Attention only; on the nearest wall | `#8e577b` |

Glow sprites are pre-rendered radial bitmaps per kind and tier, drawn with `globalCompositeOperation = 'lighter'` at `alpha = warmth`. Gradients are never built per frame. Props are generated with their lamps unlit so the glow is the only warmth. The lantern and its halo are the only magenta in the world and are dynamic: one swing on arrival (a rotation about the cord anchor), then a steady glow.

## Routes and motion

The deck's android behaviour (`fleet/web/js/motion.js`) must reach parity, so the runtime separates behaviour from drawing:

- **`behaviour`** (renderer-agnostic, extracted from `motion.js`): activity to station (`ACTS`), spot allocation (`allocate`, keeping held spots, overflow, delegates beside their partner), dwell before changing station, pacing and wandering, event reactions (nod, head shake), clip choice, held items, tone. It outputs an agent pose each tick: `{x, y, facing, clip, clipTime, seated, held, nod, tone, bubble}`.
- **`actors`** (sprite-specific) turns a pose into draw calls: the clip's frame sheet for the nearest rendered facing, the head layer offset for nods, the held item drawn at the frame's `hand.R` anchor, the tone variant, the bubble position for the DOM overlay.

The three.js deck and the sprite world can then share `behaviour` while both exist. Stations are data: each place in the floor layout declares its spots (`[x, y, facing, sit]`, as `SPOTS` does), so the deck's vocabulary maps onto the new places: *terminal* and *workbench* to bench seats, *whiteboard* to the plan wall, *bookshelf* and *read* to the library, *mail* to the report tray, *dock* to the lift, *await* to standing at the question desk facing the viewer.

**Navigation grid.** A 0.3 m grid over the floor, blocked where footprints (padded by a robot's 0.3 m radius) cover it. Routes are A* on the grid with aisle cells cheaper than open floor, then string-pulled into straight segments, and cached per (from, to) cell pair until the layout changes. This replaces the deck's hand-coded aisles and crossings, which only fit one room shape. The deck's walker nudge (stepping sideways to pass another android) carries over unchanged, since it is computed on screen axes.

**Facings.** Walking and standing use eight facings rendered by the robot job; the runtime picks the nearest to the route direction. Seated poses exist only in their seat's facing. Under reduced motion robots are placed at their target, as in the deck.

**Particles** (smoke over failed runs, motes over finished ones, coffee steam) become a small 2D particle pool drawn in pass 3 above their owner, with the deck's rates and lifetimes.

## Hit testing

Picking walks the draw list front to back and returns the first hit:

1. The DOM overlay handles its own elements (bubbles, lantern glyph, labels).
2. For standing sprites, a screen-rectangle test, then a lookup in a 1-bit alpha mask of at most 128 cells a side, built once per sprite from the first tier to load. A seated robot's layers also test which side of the desk-top line the point is on. Sprites marked `"hit": "box"` use their projected footprint instead.
3. Otherwise the point is projected onto the floor plane and looked up in the place polygons (lane slot, room, floor).

Every instance carries a `place` id from the model (floor, room, lane, bench, seat, plan-wall tile, lantern, lift, library shelf), so a hit resolves to the thing it represents, and clicking enters that place by fitting the camera to its frame. Hover draws an outline traced from the same alpha mask. Keyboard focus and screen readers use invisible DOM buttons placed over each place's projected frame; they also make the redacted-text and text-budget tests (PRD *Testing*) read the DOM rather than the canvas.

## Modules

Under `fleet/web/js/world/`, plain ES modules with no dependencies and no build step. Built:

| Module | Role |
|---|---|
| `projection.js` | The camera's projection, depth, screen ↔ world, fitting a framing |
| `camera.js` | Pan and zoom between the far and near framings, damping, clamping, pointer, wheel and pinch input |
| `tiers.js` | Tier choice with hysteresis, the stand-in tier while loading, crossfade timing |
| `sort.js` | Footprint depth sort and attached layers |
| `hit.js` | Alpha masks and front-to-back picking |
| `paint.js` | Tinting through a mask, glow discs, hit masks, affine texture fill |
| `ground.js` | The ground snapshot |
| `engine.js` | `World`: manifests, items, the frame loop, dirty rectangles, budget, picking |

Planned: `layout.js` (floor layout: slots, bays, places, spots, frames), `glow.js` (warmth per workarea), `nav.js` (navigation grid and routes), `behaviour.js` (renderer-agnostic agent behaviour, extracted from `motion.js`), `actors.js` (robot poses to sprites) and the DOM overlay.

`/prototype/world` runs the engine over the bake-off's l2 scene (`art/bakeoff/world.json`: B2 bench, robots and lantern, B1 plants for their three tiers), from the whole room to l2's framing. The floor-kit prototype replaces it.

## Open questions

- **Wall-lane count.** Three lanes on the back wall and six on the open floor is a first fit. It needs checking against real projects' workstream counts.
- **Rendered facings for props.** Props come in one facing. If open-floor benches need their seats on the near side, that is a second generation per bench.
- **Lift side at L0.** The lift moves to the left on floors; the L0 building must show its lift shaft on the same side.
