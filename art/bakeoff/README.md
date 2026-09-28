# Bake-off: variants A and B1

Three objects, made two ways, for comparison with variant B2 (AI sprites, produced elsewhere):
- the l2 bench, with its pedestals, chairs and desk props
- a tall plant in a square concrete planter
- the restyled RobotExpressive, seated: typing, writing with a pencil, holding up a test tube

Both variants need no real-time lighting. The runtime draws them unlit: no lights, shadow maps, reflections, bloom or other post-processing. Lamp pools are additive glow sprites toggled by activity.

```
art/bakeoff/build.sh        # both variants and their layout screenshots (art/bakeoff/build.sh A for one)
```

Built by `art/scripts/bakeoff.py` in Blender 4.2 with Cycles on the GPU. B1's PNGs are then losslessly re-compressed by `art/scripts/png_optimize.py`. `art/scripts/shoot_bakeoff.py` renders `layout.html` at 1672 x 941 to make each variant's `layout.jpg`, laid out like `l2.png`.

Both variants share one studio: a soft disk key from the upper left of the l2 view, a dim neutral fill from the `white_studio_06` HDRI, and contact shadows.

## A: pre-lit 3D (`A/`)

One glTF per object, drawn with `MeshBasicMaterial`:

- **`bench.glb`, `plant.glb`:** one colour texture per object holding albedo × studio light × soft shadowing, baked into its own UV atlas (bench 1280² at 6 mm per texel, plant 768² at 3 mm per texel). Each glb also holds a `*_contact_shadow` floor decal: a transparent plane with the object's baked shadow on the floor.
- **`robot.glb`:** keeps its rig and all its clips, plus the arm-only `Rest`, `Type`, `Write` and `Hold` clips. Its texture holds shading only, baked seated and typing, 1024² at 4 mm per texel. Materials keep their colours as `baseColorFactor`, so the page still tints the body per host. The eyes are plain bright colour.
- **`robot_shadow.glb`:** the seated robot's floor shadow, placed under the seat.
- **`manifest.json`:** atlas sizes, texel density and the robot's `seat_point`.

| File | Size |
|---|---|
| `bench.glb` | 1969 kB |
| `plant.glb` | 626 kB |
| `robot.glb` | 1509 kB |
| `robot_shadow.glb` | 6 kB |
| **Total** | **4.2 MB** |

Decoded GPU texture memory is about 16 MB for all three.

**Limits:**
- The robot's light is baked in one pose, so when its arms move the shading moves with the parts: a shoulder stays lit or shaded as posed.
- The shading is view-independent. It holds from any camera, but there are no glossy highlights.

## B1: Blender sprites (`B1/`)

Transparent PNGs rendered in Cycles with the l2 camera: orthographic, pitch 44.5°, yaw 21.25°, the prototype's home view. They come at 1×, 2× and 4× the home density (171.5, 343 and 686 px per metre) for zoom. A shadow catcher puts the contact shadows in the alpha channel.

Robot poses are 8-frame loops stored as one-row sheets, sampled evenly over each arm clip: `Type` at 8 fps, `Write` at 4 fps, `Hold` at 2.67 fps. Each is rendered seated at its own desk with the bench as a holdout, so the desk cuts it exactly as it overlaps in the scene. `manifest.json` gives each sprite's `ref_world` and its `ref_px`, the world point and the pixel it lands on, so sprites can be placed by projecting that point.

| Object | 1x | 2x | 4x |
|---|---|---|---|
| bench | 1117 × 755, 550 kB | 2234 × 1509, 1910 kB | 4468 × 3018, 6741 kB |
| plant | 321 × 393, 61 kB | 642 × 785, 210 kB | 1283 × 1570, 743 kB |
| robot typing (8 frames) | 166 × 201 each, 60 kB | 332 × 401 each, 182 kB | 663 × 801 each, 573 kB |
| robot writing (8 frames) | 166 × 201 each, 116 kB | 332 × 401 each, 308 kB | 663 × 801 each, 923 kB |
| robot holding (8 frames) | 199 × 201 each, 142 kB | 397 × 401 each, 394 kB | 794 × 801 each, 1189 kB |
| **All** | **0.93 MB** | **3.0 MB** | **10.2 MB** |

Total: 14.2 MB, lossless. pngquant would cut it about 3×, at the cost of banding in the soft shadows.

Decoded, the 4× bench alone is 54 MB of texture memory.

**Limits:**
- **Fixed colours:** a sprite can't be tinted per host, so each pose is rendered in the host colour its robot has in l2. A full set needs one render per host, or a separate tint mask.
- **One view:** sprites hold only the l2 angle, so camera turns (the prototype's ±15°) are not possible.
- **Robot shadows:** the robots' shadows on the desk are absent, because the desk is a holdout.
- **Fixed poses:** new poses need new renders.
