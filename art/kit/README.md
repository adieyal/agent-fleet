# The floor kit

The sprites a floor is built from, for the sprite world (`docs/design/sprite-world.md`). The built kit is
`fleet/web/assets/world/kit/`: WebP tiers and `manifest.json`. `/prototype/kit` lays it out in a small room: one wall
bay as l2, and the rest of the kit around it.

```
art/kit/generate.sh                        # AI props (ChatMock on 127.0.0.1:8010); cached, logged
blender -b --factory-startup -P art/scripts/build_kit.py -- art/build/kit   # Blender pieces (run on host home)
blender -b --factory-startup -P art/scripts/render_props.py -- MODELS art/build/props   # props from 3D models (home)
uv run --group dev python art/kit/finish.py [contact.jpg]                  # the kit, from all three, plus procedural sprites
```

MODELS is the 3D model library (`~/.cache/agent-fleet-assets/models` on carbon; copy it to home to render). Its
GLBs are not committed.

## Where each piece comes from

| Source | Pieces | Why |
|---|---|---|
| **3D models**, rendered in Blender (`art/scripts/render_props.py`), each with its contact shadow as a separate render | the bench's desk module (as its left, middle and right pieces) and the question desk (exact boxes on the drawers model), two chairs (from behind and from the front), shelf and low shelf, book cart, briefing whiteboard, podium (a lectern), three plants, floor lamp, waiting crate, crate stack, crate shelf, wall light, monitor with keyboard and mouse, laptop, desk lamp, the desk props, the lantern | the world camera by construction: every prop is square to the walls and the floor grid (floor review 2) |
| **AI**, one curated generation each (`raw/`, sidecars committed, PNGs not) | only what has no model: a whole bench (the kit and world prototypes), the terminal desk, the librarian's desk | no model yet; none is placed on the floor |
| **Blender** (`art/scripts/build_kit.py`, Cycles, shadow catchers) | pilaster, wall caps along x and y, the corner, both cut wall ends, the slab's cut edges, the lift (a five-frame sheet from shut to open; the indicator's display is blank for the floor number), the plan-wall board, tiles (blank, done, running, failed) and criteria lights (on, off) | pieces that must line up exactly, and states that must match each other |
| **Procedural** (`finish.py`) | footprints in eight directions, glow: desk-lamp pool, lit shade, wall-washer scallop, floor spill, lantern halo | flat things in a plane, mapped onto it by the camera's affine projection |
| **Reused** | floor and wall textures from the bake-off (B2) | already one curated generation each |

Fourteen generations made the first round; two were redone (`generations.log`): the bench came back with warm
light pools on its desk tops, and the shelf showed its left end, which this camera never sees. Warmth is always
a separate sprite, so a bench with light baked in could not go dark.

## Scale and angle

Every sprite is for one camera: orthographic, pitch 28°, yaw 33° (`manifest.camera`). Scale is set from l2's
framing, 171.528 px/m (941 px over 5.486 m, as fitted by `art/scripts/fit_camera.py`), and every object has its
real size in metres (`size_m`). Blender pieces are modelled at that size; an AI prop is scaled so its trimmed
width (or height, for tall irregular things such as plants) matches the width of its real box on screen, and
anchored so the image's centre line and bottom sit on the box's. `manifest.scale` records the basis and the
measurements against the concept images: l2's bench agrees with the camera; l1 is not drawn to one scale (its lift
is about twice the size its benches imply), so the kit keeps real sizes rather than l1's proportions.

Tiers are 85.8, 171.5 and 343 px/m (½×, 1× and 2× l2), and an AI prop also keeps its own density when it is finer.
AI props get a soft contact shadow from their footprint. Props from models have a rendered one: for a prop on the
floor or a wall it is its own ground sprite, named in the prop's `shadow` and placed with it; for a prop on a desk
it is laid into the sprite. Blender pieces on the walls carry no cast shadow: one that ran past the sprite's edge
would stop there in a line. The shell's occlusion planes ground them instead.

## Manifest

The sprite engine's format (`fleet/web/js/world/engine.js`), plus:

- `source`, `from`, `doc`, and for AI props `size_m` and `scale`;
- `layer`: `ground` (painted into the ground snapshot: walls, the plan-wall board, footprints), `standing`
  (sorted: the default) or `light` (additive glow, with an `intensity` per placed item);
- `slots`: named points in metres from the anchor: the bench's `seats`, `lamps` and `desk_top`, the lamp's
  `shade`, the question desk's `lantern`, the lantern's `glyph`, the lift's `indicator`, the plan wall's `tiles`
  grid and criteria `lights`;
- `cells` for a sheet of states without a frame rate (the lift's doors); an item picks one with `cell`;
- `shadow`: the ground sprite that is the prop's contact shadow, placed at the prop's anchor; `wall`: `back` or
  `left` for a wall-backed prop, anchored at the middle of its back and placed on that wall's face.

Anchors are the base centre on the floor unless the sprite's `doc` says otherwise: wall pieces are anchored on the
wall's face, the lantern at the diamond's centre, tiles and lights at their centre.
