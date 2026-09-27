# Motion test: HY-Motion 1.0 clips

This is a throwaway test on branch `renovate/motion-test` that compares clip sources for the robot: Mixamo against HY-Motion 1.0 (Tencent's text-to-motion model). This file covers step 1, the HY-Motion clips. The robot sprite pipeline on `renovate/robot` is untouched.

## Result

The **full HY-Motion-1.0 model (1.0B) runs on the RTX 3090**, so the Lite model was not needed. It is downloaded but unused. All 9 prompts × 3 takes (27 clips) were generated. Each one exports as FBX and imports cleanly into Blender 4.2.23 (`hymotion_blender_check.json`).

Clips are in `~/.local/state/fleet/renovation/hymotion/HY-Motion-1.0/` and are not in the repo:

- `<slug>_00{0,1,2}.fbx` — one take per seed (11, 22, 33). Each file holds a 52-bone SMPL-H skeleton (root `Pelvis`, full finger joints), skinned to HY-Motion's wooden mannequin mesh, at 30 fps. Files are about 16 MB each because the textures are embedded.
- `<slug>_00N.npz` — raw SMPL-H parameters (`poses`, `trans`, `Rh`, `betas`), useful for retargeting without the FBX.
- `results.json` — timings and VRAM, also copied here as `hymotion_results.json`.
- `contact_all.png` — 5 frames from each of the 27 clips. `hymotion_take000.png` here shows take 000 of each prompt.

| slug | prompt | length | notes (from contact sheet / Blender) |
|---|---|---|---|
| a_typing | a person sits at a desk typing on a keyboard | 5 s | seated, hands forward at desk height |
| b_stand_up | a person stands up from a chair | 3 s | full sit → stand, root rises ~0.6 m |
| c_sit_down | a person sits down on a chair | 3 s | stand → sit, root drops ~0.6 m |
| d_walk | a person walks forward at a normal relaxed pace | 4 s | travels 3.7–4.9 m (≈1.0–1.2 m/s); not in place |
| e_nod_yes | a seated person nods yes | 3 s | seated, head-only motion |
| f_shake_no | a seated person shakes their head no | 3 s | seated, head-only motion |
| g_slump | a seated person slumps, tired and stuck | 4 s | subtle; torso sags forward |
| h_read_book | a seated person reads a book held in both hands, head down | 5 s | seated, both hands up, head down |
| i_thumbs_up | a standing person gives a thumbs up | 3 s | arm raises and returns; the thumb itself is too small to judge at contact-sheet size |

The seated clips have no chair: the pelvis hovers at seat height. There is no guarantee that seat height or pose matches between clips. That matters when chaining sit_down → typing → stand_up, and should be checked in step 2.

## Install (as done here)

```bash
git clone --depth 1 https://github.com/Tencent-Hunyuan/HY-Motion-1.0 ~/tools/HY-Motion-1.0   # commit 4e426f5
cd ~/tools/HY-Motion-1.0
uv venv -p 3.10 .venv
UV_HTTP_TIMEOUT=300 VIRTUAL_ENV=.venv uv pip install --index-strategy unsafe-best-match -r requirements.txt
#   -> torch 2.5.1+cu124 from PyPI, fbxsdkpy from the Inria index in requirements.txt
# weights, all under ~/.cache:
huggingface-cli download tencent/HY-Motion-1.0 --include "HY-Motion-1.0/*" "HY-Motion-1.0-Lite/*" \
    --local-dir ~/.cache/hymotion/tencent             # 4.2 GB full + 1.8 GB Lite
HF_HUB_DISABLE_XET=1 huggingface-cli download Qwen/Qwen3-8B              # 16 GB text encoder -> ~/.cache/huggingface
HF_HUB_DISABLE_XET=1 huggingface-cli download openai/clip-vit-large-patch14
```

Gotchas:

- The Xet download of Qwen3-8B failed partway with a CDN error. The resumed download left shard 3 corrupt (`InvalidHeaderDeserialization`), so check the sha256 of each blob against its blob name, delete bad blobs and re-fetch them.
- A pip config on this machine adds `pypi.ngc.nvidia.com`, and the cu121 index timed out. Plain PyPI torch (cu124) works with driver 555.
- The Text2MotionPrompter rewrite/duration LLM was not installed. Prompts were used verbatim with fixed durations.

## Running

```bash
cd ~/tools/HY-Motion-1.0 && PYTHONPATH=. USE_HF_MODELS=1 HF_HUB_OFFLINE=1 .venv/bin/python \
  <repo>/art/motion-test/hymotion_gen.py --model ~/.cache/hymotion/tencent/HY-Motion-1.0 \
  --out ~/.local/state/fleet/renovation/hymotion/HY-Motion-1.0 --takes 3 --encoder-device offload
~/.local/bin/blender -b --factory-startup --python <repo>/art/motion-test/blender_check.py -- <out> <out>/blender_check.json
~/.local/bin/blender -b --factory-startup --python <repo>/art/motion-test/blender_contact.py -- /tmp/contact <out>/*.fbx
```

`hymotion_gen.py` replaces the stock `local_infer.py`. It encodes all prompts first, frees the text encoder, and only then loads the DiT. The stock code puts Qwen3-8B (16 GB bf16) and the DiT on the GPU together, which is why the model table asks for 26 GB. A ComfyUI server on this machine holds 8.4–10.7 GB of the 3090. `--encoder-device cuda` therefore OOMed, and CPU bf16 was impractically slow (no AVX512-BF16). `offload` keeps the Qwen weights in CPU RAM and streams them to the GPU layer by layer with accelerate `cpu_offload`. The weights and numerics match stock and it needs about 16 GB of free system RAM for the weights.

## Run time and VRAM (full model, RTX 3090)

| stage | time | peak VRAM (torch allocated) |
|---|---|---|
| text encoder load (offload) | 2.7 s | — |
| encode one prompt ×3 (Qwen3-8B + CLIP-L) | 2.5–4.7 s | 2.2 GB |
| DiT load (4.2 GB ckpt) | 22 s | 4.0 GB weights |
| generate one prompt, 3 takes, 50 Euler steps | 13–25 s | 5.4 GB (3 s clip) – 6.2 GB (5 s clip) |
| FBX export per prompt (3 files) | 0.7 s | — |

Per take, generation takes about 4–8 s. It barely depends on clip length because the DiT always runs over 360 frames. The spread from 13 to 25 s comes from ComfyUI using the GPU at the same time, not from the prompts. An uncontended single-take smoke test took 5.3 s. The CUDA context adds roughly 0.3–0.5 GB on top of the torch figures. The whole 27-clip run took about 4 minutes including loads.

## Licence: Tencent HY-Motion 1.0 Community License (`License.txt` in the repo)

- **Territory restriction.** The licence "does not apply in the European Union, United Kingdom and South Korea". Use, reproduction, distribution and display of the model **or its Outputs** outside the Territory is unlicensed (§1l, §2, §5c, AUP 1). If Fleet or its users are in the EU, UK or South Korea, the generated clips cannot be used under this licence. **Someone needs to confirm where Fleet is built and used before HY-Motion clips ship.**
- **Commercial use** is allowed and royalty-free, except that products with more than 1 million monthly active users (measured at the 30 Dec 2025 release date) must request a licence from Tencent (§4).
- **Outputs.** Tencent claims no rights in Outputs (§6d). Outputs must not be used to train or improve any other AI model (§5b).
- **Distribution** of the model or derivatives requires shipping the licence plus a "Notice" file (§3). Shipping only the generated clips is not distributing the model, but the Territory and AUP limits still bind the Outputs.
- **Acceptable Use Policy** (Exhibit A). Among other things it bans military use and requires machine-generated content placed in public to be "expressly and conspicuously" identified as such (AUP 12). That could apply to publicly shown animations.
- Governing law is Hong Kong.
- The Qwen3-8B (Apache-2.0) and CLIP (MIT) encoders are only used at generation time.
