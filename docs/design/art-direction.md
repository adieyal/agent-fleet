# Art direction: matching the concept

The world must match the concept images in `docs/images/concept/`, not approximate them in low poly. `l2.png` (a workarea) is the reference for surface quality; `l0.png`, `l1.png`, `lobby.png`, `shutter.png` and `no vacancies.png` set scale, layout and state. The approach is baked 3D: scenes authored in Blender, global illumination and AO baked into lightmaps, exported as glTF and rendered in three.js with an HDRI environment, ACES tone mapping, soft shadows for dynamic objects and SMAA.

This document says what to match, gives a palette sampled from the concept images, the lighting recipe and the pipeline. Meaning (what each cue encodes) stays in the PRD's *State encoding* and *World lexicon*; this document covers looks only.

## Qualities to match

**Mass and ground.** Everything is solid and heavy. Walls have a visible cut thickness (about 25 cm at scale) with a pale cap; floors sit on a plinth; the building casts a long, soft, blue-tinted shadow on a pale ground. Every object meets the floor through contact shadow and AO: chair casters, pedestals, plant pots, desk legs. Edges are bevelled (2–5 mm at scale) so they catch a highlight line. Nothing floats, and nothing is a flat-shaded box.

**Three-quarter view.** An orthographic camera looking down about 28–32° and yawed about 20–25° from square-on to the long back wall, so the back wall reads almost frontally and the side wall steeply (measured from wall edges in `l1.png`: back wall descends right at ~12°, side wall descends left at ~46°). Verticals stay vertical. The near and side walls are cut away. L0 keeps the same three-quarter idea (the building's front face wide, side face narrow). The spike fits the exact camera to `l2.png` in Blender and carries the numbers into `camera.js`.

**Restrained neutral palette.** The shell and furniture are warm whites, putty greys and steel greys with pale oak desk tops. Saturation belongs to three things only: warm light (activity), host colours on the agents, and plants. Magenta is reserved for the attention lantern and its halo. The concept's lobby uses magenta pendants as decoration; we do not. That is stricter than the PRD, which allows decorative magenta if the lantern's form carries meaning; reserving the colour as well costs nothing and removes any ambiguity.

**Warm light carries activity.** An active workarea is where the warm light is: desk-lamp pools on the oak, wall washers scalloping the plan wall, lit plan tiles glowing amber, warm windows at L0. Idle or free areas are the same room in cool daylight only. Warmth is a state, so the renderer must be able to switch it per workarea (see *Lighting*).

**Lived-in specificity.** Each desk is set, not generic: laptop or monitor, desk lamp, pen pot, mug, small plant, paper sheets with drawn plans, a binder or in-tray, cable. Two desks never carry the same arrangement. Shelves hold binders in varied heights; plants vary in species and size. Footprints on the floor, a question card on the desk, check marks on tiles: the world records what happened.

**Controls placed in the world.** Navigation and actions are objects: the lift door and its call-button column, the numbered lift panel, floor-name plaques on the facade, the question desk under the lantern, the plan wall's tiles, the lobby's search kiosk. They are modelled at real scale, lit like everything else and separate named nodes in the glTF so they can be picked.

**Daylight warmth.** The whole scene sits in soft, high-key daylight from a pale blue sky: low contrast, open shadows, never black. Interior warm light (2700–3000 K) is layered on top. Floors are satin-glossy tile that picks up soft reflections of lamps and glowing tiles.

**Friendly robots.** Rounded, toy-like proportions: a large capsule head with a black glass visor and two glowing cyan eyes, ear discs, a small rounded torso, jointed arms with mitten hands, jointed legs. The body shell is the host colour; joints and hands are a lighter neutral grey. They sit, type, write, hold things up, walk and carry boxes.

## Palette

Sampled from the concept images (median of a 5×5 patch). Values are the rendered, tone-mapped result: use them as targets when comparing renders, not as albedo inputs. Albedo is set lower so that lit and tone-mapped output lands on them. Swatches: `docs/images/art/palette.png`.

| Group | Name | Hex | Source |
|---|---|---|---|
| Air and ground | Sky backdrop | `#d7ecfd` | l2 |
| | Backdrop, L0 | `#dfedfa` | l0 |
| | Ground shadow | `#bac9de` | l0 |
| | Deep ground shadow | `#7fa3cc` | l0 |
| Shell | Wall cap, plinth | `#e6dad0` | l0 |
| | Wall, daylit | `#d0d5e5` | l2 |
| | Wall, warm-lit | `#c7ac9f` | l2 |
| | Wall, shaded | `#877772` | l2 |
| | Pilaster | `#aa9e9d` | l2 |
| | Floor tile, lit | `#dbd4db` | l2 |
| | Floor tile, shaded | `#bfb8bf` | l2 |
| Furniture | Oak desk top, lit | `#fcccaa` | l2 |
| | Oak desk top, shaded | `#b8917a` | l2 |
| | Walnut (reception) | `#9f6b43` | lobby |
| | Pedestal steel | `#717178` | l2 |
| | Desk frame | `#565256` | l2 |
| | Chair, monitor | `#272627` | l2 |
| | Lift doors | `#75737a` | l2 |
| Life | Paper | `#feebcf` | l2 |
| | Leaf, lit / mid | `#5c6f2c` / `#334114` | l2 |
| | Pot | `#aa9c98` | l2 |
| | Crate | `#59443c` | l0 |
| | Plan tile, unlit | `#bab2b6` | l2 |
| Warm light | Bulb | `#fefddd` | l2 |
| | Wall pool | `#fed9a1` | l2 |
| | Desk pool | `#fee095` | l2 |
| | Plan tile, lit | `#fcb957` | l2 |
| | Window glow, L0 | `#d4a36b` | l0 |
| Agents | Host teal | `#41ced1` | l2 |
| | Host blue | `#4082f3` | l2 |
| | Host olive | `#878638` | l2 |
| | Eye glow | `#4e9e9d` | l2 |
| | Visor | `#090d0b` | l2 |
| Attention only | Lantern core | `#fa9ffa` | l2 |
| | Lantern body | `#a60e9b` | l2 |
| | Lantern halo on wall | `#8e577b` | l2 |

## Lighting recipe

Blender bakes in scene-linear values (view transform *Standard*, no look); three.js alone tone-maps, so the same curve applies to baked and dynamic light.

1. **Sky.** A soft, low-contrast daylight HDRI (overcast or studio-daylight, CC0) as world light at a modest strength, rotated so its bright side comes from upper left, behind the camera. It gives the blue-tinted open shadows and the cool daylit walls.
2. **Sun.** One sun lamp, low strength, 5–10° angular size for very soft shadows, from upper left. It sets the direction of the building's ground shadow at L0 and the long soft shadows under chairs. At L1–L2 it is secondary to the sky.
3. **Ceiling fill.** Large, dim area lights over each room at ceiling height, neutral white, so interiors are not lit only through the cut-away wall.
4. **Warm practicals** (per workarea, switchable): desk-lamp spots (2700 K, tight cone, pool on the oak), wall-washer spots above the plan wall (2900 K, scalloped pools), emissive plan tiles, window glow at L0. Baked into separate *warm layers*, not into the base lightmap.
5. **Attention.** The lantern is emissive magenta with a small magenta point light for its halo on the nearest wall; it is dynamic, never baked.

**Baking.** Cycles on the GPU, one base lightmap per static set (combined direct + indirect diffuse, AO included by the GI) and one warm-layer lightmap per switchable light group, both on a dedicated second UV channel. Denoise bakes through the compositor. Target texel density about 1 cm on desks and 2–3 cm on floors and walls at the l2 zoom.

**In three.js.**
- Static baked meshes use an unlit material (`MeshBasicMaterial`: albedo × lightmap, plus a weak environment reflection on the floor) so real-time lights never double-light them. A small shader chunk adds each warm layer scaled by its workarea's warmth (0–1), which is how activity switches light on and off with a fade.
- Dynamic objects (robots, the lantern, tiles, boxes, shutters) use `MeshStandardMaterial`, lit by a PMREM of the same HDRI, one shadow-casting directional light matching the baked sun, and small unshadowed warm point lights at active desk lamps.
- Dynamic shadows reach the baked floor through a `ShadowMaterial` receiver just above it, tinted towards the ground-shadow blue at low opacity.
- ACES Filmic tone mapping, exposure tuned against l2. The spike should also compare `AgXToneMapping` and `NeutralToneMapping`, since ACES pushes saturated magenta and amber towards white. Selective bloom (high threshold) gives the glow on bulbs, lit tiles and the lantern, then SMAA. The backdrop is a flat pale-sky gradient, not the HDRI.

## Pipeline plan

**Tooling found on this host (2026-09-26).**
- `/usr/bin/blender` is the Debian package **3.0.1**. Cycles sees the RTX 3090 through **CUDA** in background mode; a headless 1024² diffuse bake of a small scene took 2.5 s once kernels were compiled (the first run spent ~85 s compiling). **OptiX is not available** in this build.
- The build ships **no OpenColorIO config**: only *Linear* and *sRGB* colour spaces and no view transforms (no Filmic, no AgX). Linear float bakes still work, but reference renders can't be colour-managed the way three.js tone-maps.
- The glTF exporter (`io_scene_gltf2`) is present, but it is the 3.0-era version.
- **Decision:** the pipeline uses Blender **4.2 LTS or newer**, installed at `~/.local/bin/blender` on this host (4.2.23; override with `BLENDER`). It brings OptiX, OCIO with AgX, OIDN denoising and WebP glTF export. `art/build.sh` and the scripts refuse older versions.
- Python on this host can reach Poly Haven, ambientCG and download.blender.org.

**Layout.** As built; details in `art/README.md`.
- `art/`: sources, build scripts and `build.sh`. Nothing here is shipped.
  - `art/assets.json` and `art/assets.lock.json`: what we use, and the exact URL and sha256 of every file.
  - `art/scripts/`: fetching, then the Blender steps (`build_<scene>.py`, `bake.py`, `export.py`, `preview.py`) run with `blender -b -P`.
  - `art/sources/` and `art/build/`: downloads and intermediates, gitignored.
  - The robot is scripted too (`art/scripts/build_robot.py`). It is a rigid-skinned mesh on a 12-bone rig, so there's no hand-tuned `.blend` to commit.
- `fleet/web/assets/world/<scene>/`: built output only, i.e. `<scene>.glb`, `lightmap-<layer>.webp`, and `manifest.json` naming the warm groups and dynamic nodes. Source credits are in `art/CREDITS.md`.

**Sources.** All CC0.
- Poly Haven: HDRIs, PBR textures and the occasional prop.
- ambientCG: tile, plaster, oak veneer, brushed steel and fabric materials.
- Kenney and Quaternius: blockout or background props, restyled.

Furniture that defines the look (bench desks, pedestals, task chairs, plan wall, lift, shelving) is modelled in Blender to the concept's proportions with bevels, because stock kits are the wrong style. Downloaded models are restyled: materials swapped to the palette, bevels added, scale normalised.

**Scene build.**
1. Build from script: shell, furniture, props, placement, with seeded variation for lived-in desks.
2. Author the second UV channel with lightmap pack and a margin.
3. Bake the base lightmap and warm layers.
4. Export the glb (TEXCOORD_1 carries lightmap UVs; three.js reads it with `lightMap.channel = 1`).
5. Write the manifest.

Lightmaps start as 8-bit sRGB WebP with a per-scene scale; if banding shows in the soft gradients, switch to RGBE `.hdr`. KTX2 compression waits until sizes matter.

**Robots.** One base mesh (subdivided, rounded), with the host colour as a single material slot tinted at runtime, and emissive eyes. The rig is a simple humanoid armature (about 20 bones). Actions are exported as glTF animations and played through `AnimationMixer`: `sit_idle`, `sit_type`, `sit_write`, `hold_up`, `perk_up`, `walk`, `carry`. Seat and desk heights are shared constants between furniture and rig so seated poses land without per-desk fixes. Prop attach points are bones: `hand.R` holds a pencil, flask or box.

**three.js additions.** WebP textures need nothing new: the vendored r186 `GLTFLoader` reads `EXT_texture_webp` and browsers decode WebP natively. KTX2 would need `KTX2Loader` plus the Basis transcoder; it isn't used because WebP keeps scenes well under budget. Vendor these from r186 when the renderer is built:
- `postprocessing/`: EffectComposer, RenderPass, UnrealBloomPass, SMAAPass, OutputPass and their shaders.
- `loaders/HDRLoader.js`.

**Spike: l2 workbench.** The first deliverable proves the pipeline end to end on one scene:
- One bench of three desks with chairs and pedestals.
- The plan wall with lit and unlit tiles, wall washers, the question desk and lantern, plants and a shelf.
- One rigged robot seated and typing.

It renders in a standalone page beside the current deck, with the camera fitted to `l2.png`. Acceptance is a side-by-side screenshot of `l2.png` and the render, judged on the qualities above, with warmth toggled on and off for the bench.

**Spike result (2026-09-26).** `/prototype/bench` renders the baked workbench with three robots from the l2 camera: orthographic, pitch 37°, yaw 23°. `art/scripts/shoot_bench.py` writes the side-by-side. What matches:
- the layout: lift, question desk under the lantern, plan wall with criteria lights, poster, bench, near desk
- mass and contact shadows
- amber lit tiles with ticks
- the magenta lantern with its glyph
- action bubbles
- desk lamps that follow activity

Iteration rounds, each committed as `style(art):`, with side-by-sides in the job outbox:
1. **Round 1.** Warmer, darker tone with warm downlight scallops per wall bay; deep grey pilasters and a thick cap; a blurred floor reflection; glossier robots with grey joints, a larger face plate and smaller eyes.
2. **Round 2.** Cooler grey wall bays and paler oak; potted desk plants, plan sketches, full pen pots and tall planter plants; the lantern became an elongated six-sided diamond; sprites and the lantern were kept out of the floor reflection.
3. **Round 3.** A light-grey bezel and whiter tiles on the plan wall, with larger criteria lamps; broad baked desk washes instead of hot spots; robots 12% larger and filled by a hemisphere light that reaches only live objects.
4. **Round 4.** Legible pencil lines on the sketches; the olive robot's flask arm raised higher.

**Performance** at 1440 x 900 on this host's RTX 3090, measured by `art/scripts/perf_bench.py` in headless Chromium through ANGLE on Vulkan with vsync off (the GL paths fall back to llvmpipe):

| Frame time (median / p95 / worst) | Frame rate | First frame | Page load (3D assets / code) |
|---|---|---|---|
| 3.9 / 4.3 / 7.8 ms | ~256 fps | 1.9 s | 8.5 MB (6.1 / 2.45) |

The 60 fps target leaves about 4x headroom. The floor reflection roughly doubled frame cost (1.6 ms before round 1).

Gaps still open, largest first:
1. **Oak colour.** The Wood095 texture is orange, so the oak reads orange; l2's desks are pale and take their warmth from the lamps. The fix is a paler source texture or a desaturated copy made at build time.
2. **Olive robot.** Its flask is still mostly hidden by the monitor, where l2 holds it up in clear view. It needs its own pose or a different monitor placement.
3. **Floor tone.** l2's floor is a cooler lilac-grey with brighter warm reflections under the desks. The reflection strength and floor albedo need a pass together.
4. **Right edge.** The shelf of binders and the lit box are only half in frame; l2 shows them fully.
