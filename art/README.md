# Art pipeline

Builds Fleet's baked 3D scenes from CC0 sources: Blender assembles each scene from a script, Cycles bakes its lighting into lightmaps, and the result is exported as glTF for three.js. The look it aims at is in `docs/design/art-direction.md`.

```
art/build.sh              # everything: fetch, build, bake, export, size check
art/build.sh workbench    # one scene
PREVIEW=1 art/build.sh    # also render art/build/<scene>/preview.png from the scene camera
```

Output goes to `fleet/web/assets/world/<scene>/`, and that is the only thing committed. Each scene must stay under 15 MB; `build.sh` fails when one doesn't.

## Layout

| Path | What | In git |
|---|---|---|
| `assets.json` | The sources we use: Poly Haven and ambientCG ids, resolutions, and the Blender build | yes |
| `assets.lock.json` | The exact URLs and sha256 of every downloaded file | yes |
| `CREDITS.md` | Author, licence and URL of every source, written by `fetch_assets.py --resolve` | yes |
| `scripts/fetch_assets.py` | Downloads what the lock pins into `sources/` and verifies the hashes | yes |
| `scripts/artlib.py` | Helpers shared by the Blender steps: materials, bevelled primitives, UVs, lights | yes |
| `scripts/build_<scene>.py` | Builds one scene from the sources and saves `build/<scene>/<scene>.blend` | yes |
| `scripts/bake.py` | Bakes the lightmaps (Cycles on the GPU, then OpenImageDenoise) | yes |
| `scripts/export.py` | Writes the glb (WebP textures), the lightmaps (WebP) and `manifest.json` | yes |
| `scripts/preview.py` | Renders a reference image from the scene camera for comparison with the concept art | yes |
| `sources/` | Downloaded assets and the pinned Blender | **no** |
| `build/` | `.blend` files, raw EXR lightmaps, logs, previews | **no** |

## Sources and tools

`fetch_assets.py` without arguments downloads exactly what `assets.lock.json` pins and fails on any hash mismatch. To add or change a source, edit `assets.json`, then run `python3 art/scripts/fetch_assets.py --resolve`. That asks each provider's API for the file, checks it against the provider's own md5, sha256 or size, and rewrites the lock and `CREDITS.md`.

The pipeline uses the official Blender LTS build, pinned in `assets.json` and unpacked into `sources/tools/`. The distro package on this host (3.0.1) lacks OpenImageDenoise, OptiX, colour management and WebP glTF export. The scripts refuse to run on anything older than the pinned major version; set `BLENDER=/path/to/blender` to use another install.

## How a scene is made

1. **Build.** Every object is tagged with a `fleet` custom property:
   - `baked`: static. It gets a second UV map, `Lightmap`, in one shared atlas at 1.5 cm per texel. Faces that can never be seen, such as undersides on the floor and the outsides of walls, are removed first.
   - `dynamic`: lit at runtime, e.g. plants, plan tiles, bulbs, the lantern.

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
