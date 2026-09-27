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

**One camera for every sprite and every zoom.** Orthographic, pitch 28°, yaw 33°: the image model's own camera, measured from the AI furniture's silhouettes and matching l1 and l2 (floor review 1 below). Blender pieces, the props rendered from 3D models (floor review 2) and the walker are rendered with it; the few AI props left only approximate it. Changing it means re-rendering every Blender piece, so it is fixed. (It was pitch 44.5°, yaw 21.25° until floor review 1: the l2 landmark fit, which the AI furniture never matched.) The camera never turns; the prototype's ±15° turn is a 3D-only feature and is dropped.

With `c = (cx, cy, cz)` the view centre and `ppm` the zoom in screen pixels per metre, a world point `p` lands at:

```
d  = p − c
sx = W/2 + ppm · ( 0.8387·dx + 0.5446·dy          )
sy = H/2 + ppm · ( 0.2557·dx − 0.3937·dy − 0.8829·dz)
depth(p) = 0.4809·x − 0.7405·y + 0.4695·z         (larger is nearer the viewer)
```

So one metre along `x` goes right and slightly down (slope 0.305, the long edges of a desk and the foot of the back wall), along `y` up and right, and up the screen by 0.88 m per metre of height. The inverse onto the floor plane (`z = 0`) is a 2×2 solve, used for picking and for placing things under the pointer.

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
  "camera": { "pitch": 28, "yaw": 33 },
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

**The floor kit** (`fleet/web/assets/world/kit/`, built as described in `art/kit/README.md`) is the first family built. Its manifest adds a `layer` hint per sprite (`ground`, `standing` or `light`), named `slots` (seats, lamp and lantern points, the plan wall's tile grid, the lift's indicator), `cells` for sheets of states without a frame rate (an item picks one with `cell`), and a `scale` record measured against l1 and l2. Lamps are separate from benches, and every warm light (desk pool, lit shade, wall-washer scallop, floor spill, lantern halo) is its own additive sprite whose placed item carries an `intensity`, as *Glow* above requires. The engine loads several manifests side by side with a prefix per manifest.

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

**Budget.** The engine measures each frame's work (and, when frames run back to back, the gap between them, since a canvas can rasterise after the frame returns). When that runs over the budget (8 ms by default), ambient animation (items marked `ambient`, such as working robots' loops) plays at half speed, down to an eighth; it recovers once animation frames cost under half the budget. Only animation frames count: loading, zooming and full repaints are costly for other reasons. Measured on carbon without a GPU at 1672 × 941 with the bake-off scene: zooming 15 ms a frame (median), panning 2.5 ms, an animation step under 1 ms.

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

Both are one layout seen at two zooms. The floor follows l1's places along the back wall, and the active workarea follows l2 (review of round 3). So the lift is at the right end, as in l1, and l2's framing of the workarea shows the library beside it where l2 shows the lift.

```
 back wall (y = 10.8)
 x=0      2.85        7.9   9.75       12.3        14.75   16.1   18.8   21.7  23.35 25.2
 ┌────────┬───────────┬─────┬──────────────────────┬──────────────────────┬──────┬─────┐
 │ALCOVE  │ LIBRARY   │  ?  │   PLAN WALL          │ podium  briefing     │ LIFT │panel│  ← back wall
 │crate,  │ shelves,  │ lan-│   bench (workarea)   │         board        │      │     │
 │lamp    │ book cart │ tern│                      │                      │      │     │
 ├────────┴───────────┴─────┴──────────────────────┴──────────────────────┴──────┴─────┤
 │      bench                                 bench                                    │  ← l1's five
 │   bench                  bench                                                      │     long benches,
 │                                                  bench                              │     staggered
 └─────────────────────────────────────────────────────────────────────────────────────┘  y = 0 (slab edge)
   left wall (x = 0)                                                  right edge cut away
```

As built in `layout.js` (a pure function of the room from `workarea-model.js`), after eight rounds of side-by-sides against l1 and l2 (`art/scripts/shoot_floor.py`; rounds 4-6 and 7-8 follow reviews of rounds 3 and 6):

- **The floor** is 21.6 × 10.8 m (six bays by three) on a 0.9 m slab with a pale rim, with 3.2 m walls 0.45 m thick under caps; the near and right walls are cut away. It is sized to read full with five benches and the workarea, as l1 does. Earlier cuts at 32.4 × 14.4 m and 25.2 × 10.8 m left too much empty floor.
- **Along the back wall**, as l1: the crate alcove with its pendant lamp (a Blender piece; the crate appears when a live session waits on its human), the library (shelves and book cart), the workarea, the orchestrator's podium, the briefing board, and the lift with its floor-button column. The buttons' numbers, this floor and floors with attention are DOM over the rendered buttons.
- **The workarea** keeps l2's proportions, relative to its bench's centre: the question desk and lantern 2.55 m to the left and 0.45 m off the wall, the plan wall 0.35 m right of centre, the bench 1.75 m from the wall. The plan wall is l2's 10 × 6 grid of 30 cm tiles.
- **Benches** are l1's long, solid benches, built from the kit's bench pieces. A left end, middles and a right end come from one AI generation, cut to one 1.6 m seat module each and tiled into one continuous top with a grey pedestal under each seat. Each bench has one soft contact shadow, and near-side chairs stand between the pedestals. There is the workarea's bench and five staggered on the open floor. Idle desks carry a monitor, a lamp and one to three small props; the workarea's carry five.
- **Desks for jobs.** Each job the room shows takes a desk. Active jobs fill the workarea bench, then the next; recently finished ones the bench after; the rest stand idle. Each workarea desk's row on the plan wall shows its job's steps, and the criteria lights show steps done out of all steps.
- **Warmth.** Every desk's lamp throws a gentle warm pool: dim (0.3) when idle, full where a run is at work, so brightness still means activity. An even warm grade (soft-light) is laid on the floor and walls, over a warm-grey floor tone, with a faint ceiling-light sheen per bay, so an idle floor looks lived in as l1 does.
- **Depth.** Props carry a two-layer baked contact shadow (a tight core and a soft falloff). Benches have one shadow each, and a seated robot gets a shadow under its chair.
- **Footprints.** One light trail per walk in the last hour, from the lift to the desk, fading with the walk's age.
- **Action bubbles** over working robots (the deck's glyphs, in the robot's host colour) appear from zoom level 0.75, as in l2.
- **Rooms** (epics) are clusters of benches with a shared floor plaque and a DOM headline, per the PRD's first L1 layout decision (not built yet).
- **The l2 framing** is fixed relative to a bench's centre, so clicking any bench zooms to the same composition.

The remaining differences, largest first (`floor-sbs/round-8` in the job outbox):
1. l1 lights every bench's lamps and l2's whole workarea is warm, whereas here only desks with active runs glow (PRD: warmth is activity); the ambient grade makes up part of it.
2. l2 shows the lift beside the workarea; here it is at the far right as in l1, and l2's framing shows the library there instead.
3. l2 is not a single projection: its question desk sits further from the bench's end than a real layout allows.
4. The robots are placeholders: bake-off B2 seated frames and a Blender Walking clip rendered from the robot job's glb (`art/scripts/build_walker.py`).

**Places are stable.** Lane slots are assigned when a lane first appears and stored with the floor's layout; they never change with state or focus. A lane's bench persists and resets for each new slice (PRD: *the bench resets for the next slice*). Done, active and next slices read on the lane's plan wall (ticked, lit and blank columns), and finished slices leave as dossiers to the archive shelf. More lanes than slots (nine per floor) is a rearrange-mode decision, not an automatic reflow.

**Levels on one floor.** Concept `l1.png` is PRD level L1 (a floor); concept `l2.png` frames a workarea, PRD level L3. Level bands are zoom ranges with hysteresis, each deciding what the DOM overlay and the sprites show:

| Band | ppm (1× DPR) | Frame | Sprites | Text (DOM) |
|---|---|---|---|---|
| L1 floor | < 70 | whole floor | ½× tier; robots as small figures, frame 0 held unless in the focused place; plan walls show only lit rows; lamps as glow only | Room headlines, ≤ 12 words |
| L2 room | 70–130 | a room's lanes | 1× tier; robots animate in the room | Lane names and one-line status |
| L3 workarea | > 130 | a lane's bench and plan wall (l2) | 1× or 2×; tile ticks, criteria lights, action glyph bubbles | Short tile titles |

Crossing a band crossfades its overlay text over 200 ms; sprites never pop, since the tier switch happens only when the zoom settles.

### Floor review 1: root causes

Each point was reproduced in `/prototype/floor` at `e2a1601` before any change (`art/scripts/shoot_review.py`, before views in `floor-sbs/review-1/`). The preview on port 8792 had been running since 02:27, before round 7. The server reads `/js` once at startup, so the review saw round-6 code (the old bench and seats) over newer assets. The causes below hold at `e2a1601` as well.

1. **Walls not aligned with the furniture.** The scene mixed two projections.
   - **The walls' camera:** everything Blender renders (walls, caps, pilasters, slab, lift, plan wall, alcove), the procedural sprites and the affine floor and wall textures use the world camera. That camera is pitch 44.5°, yaw 21.25°, fitted to l2's landmarks.
   - **The furniture's camera:** every AI sprite (benches, desks, shelves, podium, board, crate, chairs, robots) comes out at the image model's own camera. Measured from the silhouettes of eight box-shaped props, that is pitch 24–32° (median 28°) and yaw 23–39° (median 31°). l2's own bench measures about pitch 27°, yaw 31°, and l1 about 28°, 24°.
   - **What round 7 fixed, and didn't:** it sheared the bench pieces so their long edges matched the walls (slope 0.27). Their depth edges still ran at a slope of −0.70 against the walls' −1.80, and no other prop was corrected. The concept images and every AI sprite share one camera; our architecture did not.
2. **Robots sitting on the tables.**
   - **The split line:** a seated robot is a bake-off B2 sprite that carries its own chair. It is split into under-desk and over-desk parts at a fixed line in its own pixels, measured against the bake-off's B2 bench.
   - **Why it failed:** the floor's benches are different pieces at a different depth. The split no longer met the desk's far edge on screen, so knees and shins above the line drew after the bench, on top of it.
   - **The seat:** it was only 0.14 m behind the desk's far edge, so the feet projected below the desk's near edge and showed under it.
3. **Surfaces made of segments.**
   - **Caps and slab:** the wall caps and the slab's rim and face are one Blender box per 3.6 m bay. Each box's end face is visible at every joint.
   - **Floor texture:** a 0.6 m AI tile of 2 × 2 stone tiles, each shaded differently, so the floor repeats as a grid of shading steps.
   - **Wall texture:** the bake-off's 2 m wall tile doesn't wrap cleanly (its seams sit 1.65 m and 2.21 m in), so it repeats with visible edges.
   - **Floor sheen:** one sprite per bay, a regular grid of soft blobs.
4. **Flat lighting.**
   - **What light exists:** each sprite's own soft studio light, one uniform floor tone and grade, and additive lamp pools at low strength.
   - **What is missing:** nothing darkens where walls meet the floor, and under the benches there are only small contact shadows. Light doesn't fall off across the room, and there is no daylight source to set against the warm lamps.
5. **No lift numbers.** The lift's indicator display was left blank for the runtime, and nothing was drawn there. Only the floor-button column had DOM digits, scaled with zoom: about 6 px at the whole-floor framing, too small to read.
6. **No area for crates.** The floor had a single crate, in the alcove, shown only while a session waits on its human. There was no storage area, and nothing a robot could walk to.

**Fixes** (before and after for each point in `floor-sbs/review-1/`):

1. **One camera:** the world camera is now the image model's, pitch 28°, yaw 33°. Every Blender piece and the walker were re-rendered with it, and the affine textures and procedural sprites follow it through `projection.js` and `finish.py`. The bench pieces' remaining difference (long edges 0.34 against 0.305) is sheared as before. Wall feet, bench edges and every prop's edges now run parallel.
2. **Seating:**
   - **Seats:** 0.3 m behind the desk's far edge.
   - **Split lines:** the split between lower and upper body is a line per robot, through a point on its own desk top 15 cm in from the far edge (`cutAt`), so hands and a laptop stay above it. The lower body stops at the desk's near edge (`cutFloor`), so nothing shows under the desk.
   - **Order:** chair and lower body, then the desk module, then the upper body. `test_every_seated_robot_sits_behind_its_desk` checks order and geometry for every seat.
3. **Continuous surfaces:**
   - **Caps and slab:** these are flat, so they are drawn as projected planes the length of the floor in the ground snapshot, using colours sampled from the Blender renders. Removing the end faces alone still left a step at every bay, because each sprite is anti-aliased separately.
   - **Textures:** the floor and walls are seamless procedural materials (4.8 m and 4 m repeats of soft, wrapping noise) with no tile grid.
4. **Lighting:**
   - **Occlusion:** bands on the floor along both walls and at each wall's foot, and deeper bench shadows.
   - **Lamps:** larger, warmer pools, dim when idle and full at work.
   - **Falloff and daylight:** a radial falloff over the floor (warmer at the back, darker at the front corners), and cool daylight from the cut-away front wall's windows.
   - **Tone:** a darker warm-grey floor tone for contrast.

   All of it is baked sprites or canvas fills in the ground snapshot, so it costs nothing per frame.
5. **Lift number:** the floor's number glows on the lift's indicator over the doors (DOM, over the rendered display). The floor buttons' numbers keep a legible minimum size.
6. **Storage corner:** two stacks of plain crates on pallets (one AI generation, logged) stand in and beside the alcove, as furniture. In front of them go the hourglass crates, one per thing waiting on its human, up to three. A store spot in front is on the walking grid, reachable from the lift (tested).

### Floor review 2: root causes

Reproduced at `e727bad` (`shoot_review.py` views `shelves`, `wall` and `fringe`; before and after in `floor-sbs/review-2/`).

- **(a) Bookcases and cart at an angle to the wall.** Each AI prop comes out at the image model's camera for that one generation, which varies from image to image around 28°/33° (review 1 measured 24–32° pitch, 23–39° yaw). A shelf drawn at yaw 25° shows its front face nearly square to the viewer while the wall runs at 33°, and no amount of scaling fixes that. The shelves were also anchored at their base centre 0.3 m off the wall, not at their back.
- **(b) The back wall in segments.** Two causes, both in sprites that stop at their edges:
  - **Cast shadows cut off.** The alcove, the pilasters, the corner post and the lift's button column were rendered with shadow catchers on the wall and floor. A 3.2 m piece's shadow, cast by the key light from the front left, runs well past the sprite's margin and stops at its edge. The alcove left a darker band up to 1 m right of its fin, and every pilaster left a step. The occlusion bands were per-bay sprites too, with a joint every 3.6 m.
  - **The alcove fin's side.** It faces +x like the room's left wall. The render shades it much darker (142, 150, 156) than the flat wall texture (211, 203, 195), so it read as a separate panel of wall.
- **(c) Faint rectangles round the seated robots.** Three sprite-edge faults drew them:
  - **Tint outside the sprite.** `tinted()` in `paint.js` multiplies the host colour over the whole canvas and clips the result only by the tint mask. The pencil robot's mask is non-zero on 1,315 pixels where the sprite is transparent, including its frame edges, so the host colour traced the sheet's frames.
  - **Glow squares.** Every glow disc came from PIL's `radial_gradient`, which reaches white only in its corners (181 at the sides). The lamp-shade glow was cut off by its square at 14% alpha, and additive, so each lit lamp beside a robot drew a bright rectangle.
  - **Frame bleed.** A frame drawn scaled straight from its sheet samples its neighbour's edge column. The pencil sheet's frame 2 is opaque down its right edge, so frame 3 showed a thin line down its left.

**Fixes:**

- **(a) Props from 3D models.** Every prop with a model (`~/.cache/agent-fleet-assets/models`, not committed) is rendered in Blender with the world camera (`art/scripts/render_props.py`):
  - **Setup:** the same studio and tiers as `build_kit.py`. Each model is scaled uniformly to its real size and turned so its front faces the camera. Chairs are turned by their backrest's direction, since the model is not square to its axes.
  - **Wall-backed props:** bookcases, the whiteboard, the crate shelf and the wall light are anchored at the middle of their back. They are placed on the wall face, flush and turned to its axis. The crate shelf is turned a quarter for the left wall.
  - **Desks:** the desk model is a long low table (0.34 m high by 2 m), so the bench's desk module and the question desk are exact boxes (oak top, steel legs), with the drawers model as their pedestal.
  - **Shadows:** each prop's contact shadow is a separate render, with the prop as a holdout. It becomes a ground sprite placed with the prop, or is laid into the sprite for props on a desk.
  - **Still AI:** only the old whole bench, the terminal desk and the librarian's desk, none of them on the floor. The lift stays Blender-built: its model is squat, would need stretching to 2.75 m, and bakes floor numbers 1–5 that would contradict the runtime indicator.
- **(b) One continuous wall.** The tall architecture pieces have no shadow catchers. The occlusion at the walls' feet is four planes the length of each wall, with linear gradients held parallel to the wall on screen (`linear.along` in `engine.js`). The left wall has a light shade over it, and the fin's side has a paler skin that renders to that same shade, so faces turned the same way agree. Pilasters stay as geometry, with no shading around them.
- **(c) Clean edges.**
  - **Tint:** clipped to the sprite's own alpha (`test_a_tint_stays_inside_its_sprite`).
  - **Glow and planes:** glow discs reach zero at their radius, and every flat drawing is padded before projection.
  - **Frames:** each is copied out of its sheet 1:1 before scaling (`cellOf`; `test_a_frame_of_a_sheet_is_drawn_without_its_neighbours_pixels`).
  - **Guard:** `test_sprites_fade_out_inside_their_edges` checks that every rendered or procedural sprite ends at alpha ≤ 8 on its border. The only exceptions are pieces that butt against their neighbours.

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
| `layout.js` | The floor from a room: shell, lanes, desks for jobs, stations, runs' seats, trails, framings |
| `nav.js` | The walking grid from footprints, A* with string-pulling, walking along a route |

Planned: `glow.js` (warmth fading per workarea), `behaviour.js` (renderer-agnostic agent behaviour, extracted from `motion.js`), `actors.js` (robot poses to sprites) and the rest of the DOM overlay (the lantern's glyph is the first piece).

`/prototype/world` runs the engine over the bake-off's l2 scene, `/prototype/kit` lays out the floor kit, and `/prototype/floor` builds the Restoke floor from the recorded fixture. On the floor, robots leave the lift one by one, walk around the furniture to their desks and sit. Clicking a bench zooms to it, and Escape zooms out.

**Measured** (`art/scripts/perf_floor.py`: 1440 × 900, three robots walking, frame rate uncapped, median of three fresh-browser runs; GPU is the Intel Meteor Lake iGPU through ANGLE GL, no GPU is SwiftShader):

| Mode | DPR | Whole floor | Bench (l2) | Zooming between them |
|---|---|---|---|---|
| no GPU | 1 | 1.2 ms (823 fps) | 2.7 ms (375 fps) | 12.9 ms (77 fps), p95 23 |
| no GPU | 2 | 2.8 ms (356 fps) | 6.2 ms (161 fps) | 51 ms (20 fps), p95 137 |
| GPU | 1 | 0.8 ms (1288 fps) | 1.6 ms (631 fps) | 2.0 ms (500 fps) |
| GPU | 2 | 1.9 ms (531 fps) | 2.2 ms (457 fps) | 7.8 ms (128 fps) |

Walking robots stay far above 50 fps everywhere. Zooming at pixel ratio 2 without a GPU is the open case. After three slow frames of camera motion the engine draws motion at ratio 1 into a side canvas and scales it up, and the frame at rest is sharp again. That took it from 15 to 20 fps (after moving the warm grade out of the per-frame path). What remains is reading sprite copies and a ground snapshot made at ratio 2; ratio-1 copies of both for motion would close it.

## Open questions

- **Wall-lane count.** Three lanes on the back wall and six on the open floor is a first fit. It needs checking against real projects' workstream counts.
- **Rendered facings for props.** Props come in one facing. If open-floor benches need their seats on the near side, that is a second generation per bench.
- **Lift side at L0.** The lift moves to the left on floors; the L0 building must show its lift shaft on the same side.
