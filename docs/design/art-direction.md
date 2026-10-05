# Art direction: matching the concept

The world must match the concept images in `docs/images/concept/`, not approximate them in low poly. `l2.png` (a workarea) is the reference for surface quality; `l0.png`, `l1.png`, `lobby.png`, `shutter.png` and `no vacancies.png` set scale, layout and state. The approach is baked 3D: scenes authored in Blender, global illumination and AO baked into lightmaps, exported as glTF and rendered in three.js with an HDRI environment, ACES tone mapping, soft shadows for dynamic objects and SMAA.

This document says what to match, gives a palette sampled from the concept images, the lighting recipe and the pipeline. Meaning (what each cue encodes) stays in the PRD's *State encoding* and *World lexicon*; this document covers looks only.

## Qualities to match

**Mass and ground.** Everything is solid and heavy. Walls have a visible cut thickness (about 25 cm at scale) with a pale cap; floors sit on a plinth; the building casts a long, soft, blue-tinted shadow on a pale ground. Every object meets the floor through contact shadow and AO: chair casters, pedestals, plant pots, desk legs. Edges are bevelled (2–5 mm at scale) so they catch a highlight line. Nothing floats, and nothing is a flat-shaded box.

**Three-quarter view.** One oblique projection for every render: yaw 30°, rays falling at atan(½) = 26.57°, verticals vertical and at full length (see *Camera*). The long back wall descends right at 16.1°, the side wall rises right at 40.9°. The near and side walls are cut away.

## Camera

Every render (the building, floors, workareas, robots, props, the world floor) uses `artlib.canonical_camera(scene, target, px_per_m)`. One metre along world X, Y and Z (x along the back wall, y towards it, z up) lands at (0.866, 0.25), (0.5, −0.433) and (0, −1) × px/m, in pixels right and down (`artlib.canonical_projection`). Blender has no oblique camera, so `canonical_camera` renders an orthographic camera at pitch 26.57°, yaw 30°, with `pixel_aspect_x = 1/cos 26.57° = 1.118` and `sensor_fit = 'HORIZONTAL'`. A square-pixel PNG of that render is the orthographic image stretched 1/cos in y, which is exactly the oblique projection. The stretch happens in image space, so lighting and shadows are the orthographic render's. `tests/test_canonical_camera.py` renders axis markers and checks where they land. `shoot_projection.py` checks the shadows: the render matches a stretched plain orthographic render to 0.94/255 mean, and its cast shadow lies inside the analytic one (IoU 0.93).

**What the concepts are.** They are not orthographic. Each is a level-camera, shift-lens perspective, like architectural photography: verticals are parallel (mean 89.9–90.1°, RMS 0.24–0.92° over 54–183 segments), while ground lines converge and steepen down the image. In l0 the X edges go from −7.6° in the top third to −16.3° in the bottom third. Two vanishing points on one level horizon fit 1.3–3.4× better than one direction per axis. A vertical picture plane leaves verticals at full length, so the parallel projection closest to these images is oblique, not a tilted orthographic camera. The l2 wall lights confirm it: they measure h/w 1.27. An oblique projection predicts 1.28, and an orthographic camera at the same ground slope predicts 1.17.

| Image | Parallel fit RMS | Two-point perspective RMS | Equivalent at centre (yaw, depression) | Depression, top → bottom |
|---|---|---|---|---|
| l1 | 2.83° | 2.12° | 26.8°, 26.6° | 24.8° → 28.4° |
| lobby | 3.24° | 1.80° | 29.9°, 26.1° | 22.9° → 29.2° |
| l2 | 3.05° | 1.21° | 36.8°, 25.1° | 21.0° → 28.9° |
| l0 | 3.70° | 1.10° | 42.5°, 13.0° | 7.7° → 18.0° |
| no vacancies | 3.94° | 1.48° | 41.7°, 13.3° | 7.6° → 18.8° |

**The concepts disagree.** The floor views (l1, lobby, l2) look down about 26° at yaw 27–37°; their pooled best parallel projection is yaw 30.0°, depression 26.5°, which is where the canonical numbers come from. The building views (l0, no vacancies) look down only about 13° at yaw about 42°: their slab and plinth lines are shallow on both axes, and their plant-pot rims are flat ellipses (soil minor/major 0.25, i.e. 14°). The canonical camera follows the floors. The building therefore renders steeper than l0: the side faces rise at 41° instead of 11–19°, and the floor plates show more of their depth. No single projection serves both groups: the best compromise, yaw 35°, depression 20°, misses every image by 5.7–8.8° RMS. Earlier numbers were fits of positions with the sizes free, and sizes trade against pitch. The building job's pitch 9°, yaw 25.75° also took the points where the slab sides meet the lift (1147, 232…635) for the slab fronts' right ends; the fronts actually end at x ≈ 940–962. The world's pitch 28°, yaw 33° orthographic camera is within 1–9° of the floors' ground directions (the canonical camera is within 0.4–9°), but its verticals are 12% short.

**Method** (`art/scripts/measure_projection.py`, then `shoot_projection.py` in Blender, then `measure_projection.py --overlays DIR`):

1. Find LSD line segments of 60 px or longer and sort them by screen angle into the X, Y and vertical families.
2. Fit one direction per family (parallel) and two vanishing points on one horizontal horizon (level perspective), and compare the length-weighted angular RMS.
3. Convert slopes to a projection without using any size. For a parallel view with yaw ψ and ground factor g, tan|aX| · tan aY = g² and tan|aX| / tan aY = tan²ψ. Here g is tan(depression) for oblique projections and sin(pitch) for orthographic ones.
4. Check circles. Ground circles give g directly as minor/major: the lobby side table gives 0.43; pot soil gives 0.37–0.50 in the floor views and 0.25–0.33 in l0. Wall circles separate oblique from orthographic.
5. Tiles do not count. Painted joint spacing varies ±12% (l2: 98, 85, 108 px), and no square fits both sides.
6. Render box proxies at each candidate (landmark picks, per-axis sizes free) and overlay them at 50%. The overlays and the per-candidate landmark and line RMS are in the camera job's report.

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
  - The robot is modelled by `art/scripts/build_robot.py` on the deck's RobotExpressive rig (CC0): new meshes in the style of the B2 sprites, skinned to its bones, so its clips drive them. It gains arm-only seated clips. The scripted robot this replaced is gone.
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

**Spike result (2026-09-26).** `/prototype/bench` renders the baked workbench with three robots from the l2 camera: orthographic, pitch 44.5°, yaw 21.25°, fitted to l2 landmarks by `art/scripts/fit_camera.py`. l2 is not a consistent projection (its far and near horizontals slope differently), so no orthographic or perspective camera fits it better than about 50 px RMS. The fit weights the bench most. `art/scripts/shoot_bench.py` writes the side-by-side. What matches:
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
5. **Round 5.** Framing fitted to l2, with 1.8 m desks and a larger plan wall to match its proportions; the corner desk cropped to a corner; near-capsule clear-coated robot heads with bigger eyes; oak from a desaturated copy of Wood095; tinted binder spines; darker footprints.
6. **Round 6.** Tone calibrated by sampling l2:
   - a baked short-range AO layer for contact shadows, and a contrast grade
   - cool daylight, so warmth comes only from lamps and wall washers
   - floor, pilasters and oak matched to l2's sampled colours
   - a warm floor spill and a lamp halo at each working desk, both following activity
   - the olive robot's test tube raised beside its face; the joint angles were found by search

**Reviewer notes** (CLAUDE.local.md, judged at round 6):

| Note | Status |
|---|---|
| 1. Lighting and tone | **Resolved.** Open floor, pilasters and plan tiles sample within about 10 levels of l2, and contact shadows sit under furniture and in corners. l2's lamp glow is still stronger and more saturated. |
| 2. Framing | **Resolved.** The bench fills the frame as in l2, and the near desk shows only as a corner. |
| 3. Robots | **Resolved.** Each robot sits in a far-side chair at the desk. They have round, glossy, clear-coated helmets, big eyes, dark joints and saturated host colours. One types on a laptop, one writes with a pencil, one holds up a test tube. |
| 4. Materials | **Resolved.** Cool grey walls, bevelled pilasters with capping blocks, a thick top cap, and pale wood. |
| 5. Lantern | **Resolved.** A faceted six-sided crystal with an emissive magenta core and a soft halo, on a thin cord. |
| 6. Props | **Mostly resolved.** Desk plants, full pen pots, a stacked paper tray, coloured binder spines and darker footprints are in. The potted plant beside the lift and the shelf are at the frame's edges. |

**Performance** at 1440 x 900 on this host's RTX 3090, measured by `art/scripts/perf_bench.py` in headless Chromium through ANGLE on Vulkan with vsync off (the GL paths fall back to llvmpipe). The scripts and tests launch Chromium without `DISPLAY`: an inherited, dead X forward stops ANGLE from creating a WebGL context.

| Frame time (median / p95 / worst) | Frame rate | First frame | Page load (3D assets / code) |
|---|---|---|---|
| 4.0 / 4.4 / 7.0 ms | ~250 fps | 2.1 s | 8.7 MB (6.3 / 2.45) |

The 60 fps target leaves about 4x headroom. The floor reflection roughly doubled frame cost (1.6 ms before round 1).

**Camera controls.** The prototype zooms (wheel or pinch, about the pointer), pans (drag) and turns (right-drag or shift-drag). The turn is clamped to ±15° azimuth and ±4° tilt, so cut-away walls never show their backs. Movement is damped, and instant under reduced motion. The controller is `fleet/web/prototype/camera.js`.

Zoom runs from 0.6× (the whole room) to 1.8×, where the baked lighting stops looking sharp:
- The lightmaps bake 1.5 cm per texel, so at zoom z a texel spans 2.57 z CSS px on a 941 px tall view.
- `art/scripts/zoom_sharpness.py` renders the same spot on the bench at 1–2.5×. Baked contact shadows (under mugs, pen pots and plant pots) stay crisp to about 4.6 px per texel, which is 1.8×. They smear visibly beyond 5 px (2×).
- Albedo textures (0.6–2 mm per texel) stay sharp well past that, so the lightmap sets the limit.

Sharp close-ups need finer lightmaps. Estimates below scale texel count from the current bake: 10 layers (base, 9 warm, AO), 0.94 MB of WebP, about 120 MB of GPU memory with mipmaps.

| Sharp to zoom | Texel | Base atlas | Lightmap files | GPU memory |
|---|---|---|---|---|
| 1.8× (now) | 15 mm | 2304² | 0.94 MB | ~120 MB |
| 2.5× | 11 mm | 3200² | ~1.8 MB | ~230 MB |
| 3× | 9 mm | 3840² | ~2.6 MB | ~335 MB |
| 4× | 7 mm | 5120² (over the 4096 cap: two atlases) | ~4.7 MB | ~590 MB |

Download size stays modest; GPU memory is the real cost. Two cheaper routes:
- **A detail atlas.** Bake a second, fine atlas for just the desk tops and props (about 15 of 357 m²) at 3–5 mm. That adds roughly 0.5 MB and 6 MB of GPU memory.
- **Packed warm layers.** Pack the nine warm layers into the RGB channels of three textures (each warm group has a fixed colour), cutting their memory by two thirds.

Gaps still open, largest first:
1. **Lamp glow.** l2's working lamps throw a strong, saturated amber glow and the shades glow from inside. Ours is a faint halo, because the bulbs face down under closed shades. An open, emissive shade interior would close it; about half a day.
2. **Composition.** l2 is not a single projection, so the question desk and lantern sit about 50–100 px from where l2 draws them when the bench matches. Nudging those props per view is cheap, but it bends the layout away from real geometry.
3. **Edges of frame.** The lift's potted plant and the binder shelf are cut by the frame, where l2 shows them in full. l2 compresses the room sideways; placing them nearer the centre fixes it.
4. **Finish.** l2's surfaces have painterly gradients and crisp highlight edges. Closing that means more bevel detail, finer lightmaps (1 cm) and light-probe reflections on the live objects; several days.
