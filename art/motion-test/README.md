# Motion test: Mixamo vs HY-Motion 1.0

This is a throwaway test on branch `renovate/motion-test` that compares clip sources for the robot: Mixamo (motion capture) against HY-Motion 1.0 (Tencent's text-to-motion model). Step 1 generated the HY-Motion clips. Step 2 put both sources on a robot built from the user's 3D parts and rendered them with the floor's camera. The robot sprite pipeline on `renovate/robot` is untouched.

## Step 2 verdict

| clip | recommended source | why |
|---|---|---|
| (a) typing | **Mixamo** | Head up, so the face and both hands read at sprite size. The hands land on the desk, within 1–3 cm of the desk top. In HY-Motion's head-down pose, all three takes hide the hands and face behind the helmet, and the hands hover 6–10 cm above the desk. |
| (b) stand up | **Mixamo**; HY-Motion take 000 is equally usable | Both read well. HY-Motion is a little smoother. Mixamo pairs with (c) and starts from the same seat. |
| (c) sit down | **Mixamo** | Mixamo backs straight into the chair, facing the camera throughout. All three HY-Motion takes walk up facing the chair and then turn round; two of them end 60–72° off the desk. Take 001 ends square and plants its feet better (slip 0.03 vs 0.10 m/s), but it starts with its back to the camera. |
| (d) walk | **Mixamo** (walk-normal or walk) | Mixamo's clips are ready-made loops (31/36 frames). HY-Motion gives a 4 s one-off that would need a cycle cut out and blended. At sprite size the gait reads the same for all three. |
| (e) nod yes | **HY-Motion** (take 000) | A clear, readable nod. The face shows except at the bottom of the nod. |
| (f) shake no | **HY-Motion** (take 000), exaggerated | Correct but small. The head's yaw barely shows on the helmet at 1x; scale it up about 1.5× or key it by hand. |
| (g) slumped / stuck | **HY-Motion** (take 000) | Reads as defeated: the head drops forward and the hands fall to the lap. Take 001 collapses through the desk. |
| (h) reading a book | **neither as is** | In all three takes the head drops so far that the helmet hides the hands and any book, and the face is lost. HY-Motion can supply the arms, but the head pitch needs cutting roughly in half and a book prop needs adding. A hand-keyed pose is probably quicker. |
| (i) thumbs up | **HY-Motion** (take 001) plus a thumbs-up hand | The arm raise reads well. The thumb cannot show, because each hand is one rigid part. A swap-in "thumbs-up" hand mesh is needed, whichever source is used. |

**In short:** Mixamo is the better source for the four core clips. They're loop-ready and their facing is predictable, and typing keeps the head up. HY-Motion is worth keeping for the gap clips (nod, shake, slump, thumbs up), since Mixamo lacks them and HY-Motion makes a usable take in about 5 s. Budget for picking the best of 3 takes and for touching up the head pitch.

### What the renders showed

- **Reads at sprite size.** At the floor's 1x density (171.5 px/m, robot ≈ 230 px tall), every clip's silhouette reads. Anything that pitches the head forward reads worst, because the helmet is a third of the robot and covers the hands and face from the B1 camera (a, g, h and the middle of b/c). Head-down human clips need their head pitch reduced for this robot.
- **Jitter.** Noise is measured as each joint's distance from a centred 5-frame moving average (`step2_metrics.json`, `jitter_mm`). It is at most 7 mm, about 1 px at 1x, for every clip and take, and most are under 2 mm. **Neither source jitters visibly.** Mixamo's transitions (b, c) are slightly noisier than HY-Motion's (2.2–3.7 mm vs 1.2–1.8 mm for the chosen takes).
- **Foot sliding.**
  - Seated and standing clips keep planted feet still: slip ≤ 0.03 m/s everywhere, except Mixamo sit-down at 0.10 m/s.
  - Walks slip about 0.14–0.24 m/s around their treadmill speed, whichever the source (Mixamo walk.fbx best at 0.14). This comes from the retarget, not the source: root motion is scaled by hip height, but the robot's stride is limited by its much shorter thigh and shin. Foot IK, or scaling horizontal root motion by thigh-plus-shin length, would fix it.
  - The game should move a walking sprite at the clip's ground speed: 0.68 m/s (walk-normal), 1.0 m/s (walk), 0.61 m/s (HY take 002).
- **Hands on the keyboard or book.**
  - Mixamo typing lands both hands on the desk plane: low point median −0.8 cm, range −3.1 to +2.8 cm. They sit either side of a 30 cm keyboard (27% of frames on the keys). Cleanup: move the hands about 5–8 cm inwards and lift them 1–3 cm, a small IK pass.
  - HY-Motion typing hovers 6–10 cm above the desk, is never on the keys, and is hidden by the helmet. That needs a real re-pose.
  - For reading, the hands sit at the desk edge, which is plausible for holding a book, but they can't be seen (see h).
- **Proportions after retargeting.**
  - Rotations transfer cleanly, and the robot keeps its sheet proportions in every clip (hands at hip height standing, big helmet, short limbs). No limb stretches or inverts.
  - Two proportion problems show up with both sources. First, the robot's legs are too short for the floor kit's 0.47 m chair: seated, its feet hang in the air. The stand-in has to be lifted 0.25–0.33 m onto the seat, and b/c blend that lift in by pelvis height, so the robot rises onto the chair. Real clips need a hop or a lower seat.
  - Second, human head-down poses bury the face in the helmet (see above).
  - The fingers are rigid, so no finger motion (typing, thumbs) can show.

### How it was built

- **Robot** (`robot_parts.py`, `motion_rig.py`). It is built from the user's 21 AI-generated GLBs in `~/.local/state/fleet/renovation/robot-parts/`, which are not committed.
  - Each part was decimated from 40k to 1.5k–9k faces (about 100k for the whole robot, left and right included), rotated into the robot frame, scaled to metres, and given its origin at its joint pivot.
  - Left-side parts are mirrored copies of the right. Each part is parented rigidly to one bone, with no skinning.
  - Shells keep their baked texture multiplied by a host tint; the renders use teal `#27b3b8`, as on the sheet.
  - Limb shells are widened 1.2–1.55× across their length to match the sheet's chunky limbs.
  - Unused parts:
    - `helmet` — `helmet large` matches the sheet's taller dome.
    - `hand palm side` — it is a second complete hand, not half of one, so `hand back side` serves both hands.
  - The visor has no eyes, so two emissive capsules were added.
  - No part was unusable. See `step2/00_robot_turnaround.webp`.
- **Scale.** The skeleton follows `robot-sheet.png`'s proportions (helmet about a third of the height, hip joints at 0.32 H, hands at hip height). It is then scaled so the robot stands 1.34 m, the height of the floor's current robot (`art/build/robot/robot.blend`, 1.33 m). At the sheet's literal size (about 1.0 m with a 0.42 m helmet), the seated robot's shoulders are below the bench desk (0.74 m) and it cannot type at all.
- **Retargeting** is done in `motion_rig.py`, not with Blender's built-in retargeting, Rokoko or Auto-Rig Pro.
  - Each source skeleton (Mixamo 65 bones, SMPL-H 52) has its joints moved in edit mode to the robot's proportions, keeping every bone's rest orientation. The clip's local rotations therefore apply unchanged.
  - Root motion is scaled by the hip-height ratio (0.50 for Mixamo, 0.57 for HY-Motion).
  - Location and scale keys on other bones are dropped.
  - Each clip is turned to face −Y at its reference frame (the most seated frame for chair clips, otherwise frame 1) and moved so the pelvis sits at the origin.
  - Walks are pinned in place by removing the least-squares drift of the root.
- **Scene** (`render_motion.py`). It uses the floor kit's chair (`build_workbench.chair`) at the bench desk (`desk_frame`), plus a primitive 30 cm keyboard. The desk edge is 0.14 m ahead of the seat point, as in `build_robot.py`. Other details:
  - Clips (a), (e), (f), (g) and (h) are at the desk.
  - Clips (b) and (c) have the chair only, as if pulled out.
  - Clips (d) and (i) are on the floor.
  - Rendering uses the B1 camera and studio from `bakeoff.py` (orthographic, pitch 44.5°, yaw 21.25°), Cycles, and a shadow catcher.
  - Sheets render at 2x (343 px/m); `_1x` copies show sprite size. Webm frames render at 1x and are shown at 2x nearest-neighbour.
- **Takes.** The pass-1 renders of all three HY-Motion takes (`run_step2.sh pass1`) were compared by eye and with `step2_metrics.json` (`run_step2.sh metrics`). The best take went to the final render (`run_step2.sh pass2`).
- **Outputs.**
  - The outbox holds `motion-test/*_sheet.png`, `*_sheet_1x.png` and `*.webm`: a–d have Mixamo on top and HY-Motion below, e–i are HY-Motion only. The turnaround is `00_robot_turnaround.png`.
  - The repo holds small WebP copies of the 1x sheets in `step2/`.
  - Mixamo FBXs are never copied into the repo; only renders are.

## Step 1 result

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
