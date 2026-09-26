# Art pipeline

Builds Fleet's baked 3D scenes from CC0 sources: Blender assembles each scene from a script, Cycles bakes its lighting into lightmaps, and the result is exported as glTF for three.js. The look it aims at is in `docs/design/art-direction.md`.

```
art/build.sh              # everything: fetch, build, bake, export, size check
art/build.sh workbench    # one scene
PREVIEW=1 art/build.sh    # also render art/build/<scene>/preview.png from the scene camera
uv run --group dev python art/scripts/shoot_bench.py [DIR]   # screenshot /prototype/bench beside l2.png
```

Output goes to `fleet/web/assets/world/<scene>/`, and that is the only thing committed. Each scene must stay under 15 MB; `build.sh` fails when one doesn't.

There are two scenes:
- `workbench`: the l2 room, baked.
- `robot`: the deck's RobotExpressive (CC0), restyled, not baked.
  - It keeps its rig and original clips (`Sitting`, `Idle`, `Wave`, ...).
  - The restyle adds a glossy body that the page tints per host, dark joints, a dark face screen with cyan eyes, and one subdivision level.
  - It adds arm-only clips `Rest`, `Type`, `Write` and `Hold`, posed on the end of `Sitting`.
  - A pencil and a test tube hang from its right hand.
  - The page plays `Sitting` without its arm tracks, plus one arm clip, and seats the robot using `runtime.seat_point` from the manifest.

The prototype page `/prototype/bench` combines them in three.js. It is served by `fleet/web/server.py` and not linked from the deck.

## Layout

| Path | What | In git |
|---|---|---|
| `assets.json` | The sources we use: Poly Haven and ambientCG ids and resolutions | yes |
| `assets.lock.json` | The exact URLs and sha256 of every downloaded file | yes |
| `CREDITS.md` | Author, licence and URL of every source, written by `fetch_assets.py --resolve` | yes |
| `scripts/fetch_assets.py` | Downloads what the lock pins into `sources/` and verifies the hashes | yes |
| `scripts/artlib.py` | Helpers shared by the Blender steps: materials, bevelled primitives, UVs, lights | yes |
| `scripts/build_<scene>.py` | Builds one scene from the sources and saves `build/<scene>/<scene>.blend` | yes |
| `scripts/bake.py` | Bakes the lightmaps (Cycles on the GPU, then OpenImageDenoise) | yes |
| `scripts/export.py` | Writes the glb (WebP textures), the lightmaps (WebP) and `manifest.json` | yes |
| `scripts/preview.py` | Renders a reference image from the scene camera for comparison with the concept art | yes |
| `scripts/shoot_bench.py` | Screenshots the prototype page at 1672 x 941 and composes it beside `l2.png` | yes |
| `scripts/perf_bench.py` | Frame times and load size of the prototype page on the GPU | yes |
| `scripts/fit_camera.py` | Fits the prototype's camera to landmarks in `l2.png` | yes |
| `scripts/zoom_sharpness.py` | Renders a bench detail at several zooms to find the closest sharp zoom for the lightmaps | yes |
| `sources/` | Downloaded assets | **no** |
| `build/` | `.blend` files, raw EXR lightmaps, logs, previews | **no** |

## Sources and tools

`fetch_assets.py` without arguments downloads exactly what `assets.lock.json` pins and fails on any hash mismatch. To add or change a source, edit `assets.json`, then run `python3 art/scripts/fetch_assets.py --resolve`. That asks each provider's API for the file, checks it against the provider's own md5, sha256 or size, and rewrites the lock and `CREDITS.md`.

The pipeline needs Blender 4.2 LTS or newer: OptiX baking, OpenImageDenoise, colour management and WebP glTF export. `build.sh` uses `$BLENDER`, defaulting to `~/.local/bin/blender`, and fails clearly on anything older. The distro package at `/usr/bin/blender` on this host is 3.0.1 and must not be used; the scripts refuse it too.

## How a scene is made

1. **Build.** Every object is tagged with a `fleet` custom property:
   - `baked`: static. It gets a second UV map, `Lightmap`, in one shared atlas at 1.5 cm per texel. Faces that can never be seen, such as undersides on the floor and the outsides of walls, are removed first.
   - `dynamic`: lit at runtime, e.g. plants, plan tiles, bulbs, the lantern.
   - `anchor`: an empty the runtime places things at: `seat_desk*`, `lantern_anchor`, `footprint_*`.
   - `character` / `prop`: the robot's skinned mesh and the props on its bones.

   Lights that belong to a workarea carry `warm = <group>`. Random choices use fixed seeds. Islands are packed by our own shelf packer because Blender's packer gives a different layout on every run. Avoid UV spheres on baked objects: `smart_project` unwraps them differently run to run.
2. **Bake.** The baked objects are joined into one proxy mesh and baked in a single GPU pass. Per-object baking is CPU-bound.
   - The `base` layer holds sky, sun and fill light.
   - Each warm group gets its own half-resolution layer holding only its lamps.
   - Emissive materials are switched off during the bake, because the runtime drives them.
   - Each layer is denoised with OIDN through the compositor.
3. **Export.**
   - Lightmaps are scaled so the 99.9th percentile maps to white, sRGB-encoded and saved as WebP. The scale is recorded in the manifest.
   - The glb carries WebP textures (`EXT_texture_webp`, read by the vendored `GLTFLoader` with no extra decoder), `TEXCOORD_1` on baked meshes, and every node's `fleet`/`warm` tags as extras.
   - `manifest.json` explains how to apply the lightmaps, lists the warm groups and dynamic nodes, and records file hashes and the tools used.

Rebuilding from the same lock gives a byte-identical glb. Lightmaps match to within a few 8-bit levels on under 0.1% of texels, from GPU bake and denoiser noise, so a rebuild may show small lightmap diffs.

## Using the output in three.js

Load the glb with `GLTFLoader`. For each node whose `userData.fleet` is `baked`, use a `MeshBasicMaterial` with the node's colour map and:

- `lightMap` = `lightmap-base.webp`
- `lightMap.channel = 1`
- `lightMap.colorSpace = SRGBColorSpace`
- `lightMapIntensity = scale * Math.PI`

Warm layers add to the base layer, scaled by each workarea's warmth (0–1); that needs a small shader chunk. Dynamic nodes keep their standard materials and are lit by the environment and real-time lights.
