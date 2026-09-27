# The floor kit

The sprites a floor is built from, for the sprite world (`docs/design/sprite-world.md`). The built kit is
`fleet/web/assets/world/kit/`: WebP tiers and `manifest.json`. `/prototype/kit` lays it out in a small room: one wall
bay as l2, and the rest of the kit around it.

```
art/kit/generate.sh                        # AI props (ChatMock on 127.0.0.1:8010); cached, logged
blender -b --factory-startup -P art/scripts/build_kit.py -- art/build/kit   # Blender pieces (run on host home)
uv run --group dev python art/kit/finish.py [contact.jpg]                  # the kit, from both, plus procedural sprites
```

## Where each piece comes from

| Source | Pieces | Why |
|---|---|---|
| **AI**, one curated generation each (`raw/`, sidecars committed, PNGs not) | bench pieces (left end, middle seat module, right end: one generation, cut to 1.6 m modules that tile into benches of any length; see `bench_pieces` in `finish.py`), a whole bench (no lamps, no chairs, empty top), terminal desk, monitor, two chairs (seen from behind and from the front), desk lamp (off), eight desk props, three plants, shelf with binders, book cart, librarian's desk, podium, briefing whiteboard, question desk with its tent card, waiting crate with hourglass, the lantern (front facet blank for the glyph) | the concept's look on the first try, and nothing here animates |
| **Blender** (`art/scripts/build_kit.py`, Cycles, shadow catchers) | pilaster, wall caps along x and y, the corner, both cut wall ends, the slab's cut edges, the lift (a five-frame sheet from shut to open; the indicator's display is blank for the floor number), the plan-wall board, tiles (blank, done, running, failed) and criteria lights (on, off) | pieces that must line up exactly, and states that must match each other |
| **Procedural** (`finish.py`) | footprints in eight directions, glow: desk-lamp pool, lit shade, wall-washer scallop, floor spill, lantern halo | flat things in a plane, mapped onto it by the camera's affine projection |
| **Reused** | floor and wall textures from the bake-off (B2) | already one curated generation each |

Fourteen generations made the first round; two were redone (`generations.log`): the bench came back with warm
light pools on its desk tops, and the shelf showed its left end, which this camera never sees. Warmth is always
a separate sprite, so a bench with light baked in could not go dark.

## Scale and angle

Every sprite is for one camera: orthographic, pitch 44.5°, yaw 21.25° (`manifest.camera`). Scale is set from l2's
framing, 171.528 px/m (941 px over 5.486 m, as fitted by `art/scripts/fit_camera.py`), and every object has its
real size in metres (`size_m`). Blender pieces are modelled at that size; an AI prop is scaled so its trimmed
width (or height, for tall irregular things such as plants) matches the width of its real box on screen, and
anchored so the image's centre line and bottom sit on the box's. `manifest.scale` records the basis and the
measurements against the concept images: l2's bench agrees with the camera; l1 is not drawn to one scale (its lift
is about twice the size its benches imply), so the kit keeps real sizes rather than l1's proportions.

Tiers are 85.8, 171.5 and 343 px/m (½×, 1× and 2× l2), and an AI prop also keeps its own density when it is finer.
AI props get a soft contact shadow from their footprint; Blender pieces carry theirs from the render.

## Manifest

The sprite engine's format (`fleet/web/js/world/engine.js`), plus:

- `source`, `from`, `doc`, and for AI props `size_m` and `scale`;
- `layer`: `ground` (painted into the ground snapshot: walls, the plan-wall board, footprints), `standing`
  (sorted: the default) or `light` (additive glow, with an `intensity` per placed item);
- `slots`: named points in metres from the anchor: the bench's `seats`, `lamps` and `desk_top`, the lamp's
  `shade`, the question desk's `lantern`, the lantern's `glyph`, the lift's `indicator`, the plan wall's `tiles`
  grid and criteria `lights`;
- `cells` for a sheet of states without a frame rate (the lift's doors); an item picks one with `cell`.

Anchors are the base centre on the floor unless the sprite's `doc` says otherwise: wall pieces are anchored on the
wall's face, the lantern at the diamond's centre, tiles and lights at their centre.
