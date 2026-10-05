# Robot sprites: deck parity

The world is moving to a 2D canvas sprite runtime with no runtime 3D (decided by the bake-off, `art/bakeoff/`). The androids are Blender-rendered sprites of the robot of `robot-sheet.png`, rendered by `art/scripts/build_robot_sprites.py` into `packages/fleet-web/src/fleet_web/static/assets/world/robot/sprites/`. They must keep every behaviour and look the deck has today.

**v2 (this version)** renders the robot rebuilt from the whole-body model (`art/motion-test/`, approved at rebuild 3): Mixamo clips retargeted onto rigid pieces cut from the model, with the user's posed hands, at 1.081 m, with the floor's one camera. v1 was the RobotExpressive robot with the B1 camera. `robot.glb` and `robot/manifest.json` (the deck's 3D robot) are unchanged.

This document lists what the deck does (`packages/fleet-web/src/fleet_web/static/js/agents.js`, `motion.js`, `looks.js`, `activity.js`, plus the few other places that touch an android) and what the sprite set provides for each, marks each item, and fixes the manifest the floor runtime reads (`sprites/sprites.json`).

Deck units below are room tiles (an android is `BOT_H` = 2.2 tiles tall; `BK` = 2.2 / 1.7 scales the kit). Angles are the deck's `facing`: 0 points towards the door (+y), π towards the back wall.

## Changes for the floor (v1 to v2)

The format is the same (`version` 2); these are the differences a reader of v1 must handle.

| Change | v1 | v2 |
|---|---|---|
| Camera | `l2`, pitch 44.5°, yaw 21.25° | `canonical`: oblique, yaw 30°, rays falling at atan(1/2) (every render's camera); 171.528 px/m at 1x as before |
| Robot | RobotExpressive rig, about 1.33 m | the rebuilt robot, 1.081 m (`robot.height_m`) |
| Seated frames | the robot's frame had its seat at `seat_point_m` = [0, 0.19, 0.21]; the floor lifted the frame onto the chair | the frame is rendered on the floor it will stand on: `foot` is the floor under the seat point, `seat_point_m` = [0, 0, 0.549]. Place a seated frame by its `foot` on the floor point under the chair's seat anchor (or by `seat` on a seat anchor 0.549 m up). |
| Chair and desk | the kit's chair (seat 0.47 m) and desk (0.74 m) | the kit's desk unchanged; the kit's chair with its gas lift raised to **0.549 m**. `seat_furniture` gives the seat height, the chair's centre behind the seat point (0.244 m) and the desk's far edge ahead of it (0.159 m), fitted so the seated robot matches l2 at this camera (chest emblem 21 px, helmet top 112 px above the desk's far edge at 1x). |
| `desk_top_m` | 0.4807, in the robot's frame | 0.74, the kit's desk top, in the same frame (floor at 0) |
| Faces | `face_eyes` / `face_band` | the same names; white emissive, coloured by the runtime. Both carry the agent dot on the back of the helmet, so from behind a face layer is present (the dot only). |
| Items | `item_box`, `item_book`, `item_sheet` rode on the torso and were drawn over `Walking` and `Idle` | carried items are held in the hands by their own clips: `BoxIdle`, `BoxWalk`, `BookWalk`, `SheetWalk`, `SitRead`. A clip's `items` are the only items drawn with it. `item_paper`, `item_pencil`, `item_laptop` and `item_flask` as before. |
| Clip names | `Sitting` (sit down), `Typing`, `Writing`, `Holding`, `Walking`, `Idle`, `Wave`, `Yes`, `No`, `Death` | the same, plus `StandUp`, `ThumbsUp`, `SitThumbsUp`, `SitIdle`, `SitRead`, `SitNod`, `SitShake`, `Slump`, `SitSlump`, `BoxIdle`, `BoxWalk`, `BookWalk`, `SheetWalk` |
| Per clip | | `source`: the Mixamo clip it came from |

## How the sprites are built

Each drawn android is a stack of layers from the same frame, composited on the canvas:

| Layer | Holds | Runtime treatment |
|---|---|---|
| `shadow` | the contact shadow alone, from a shadow catcher under a soft overhead light; black with alpha, stored at a quarter of its size | drawn first, under everything at floor level, scaled ×4 (`shadow_scale`) |
| `body` (standing) or `body_low` + `body_high` (seated) | the whole robot but its face lights, kit and items: the teal shell rendered in neutral grey `#cccccc`; black joints, visor, hands and neck; cream torso; the glowing ear rings and chest light | the shell tinted by its mask (see *Tint*) |
| `face_eyes` / `face_band` | the agent face: Codex's eyes (two capsules), Claude's band across the visor, and the back-of-helmet dot; white, emissive | multiplied by the agent colour; dimmed for the stalled and resting looks |
| `acc_<kind>` | one host kit: `backpack`, `antenna`, `halo`, `crest`, modelled for this robot and fixed to its bones | host parts tinted by their mask; the halo is emissive and tinted whole |
| `item_<name>` | a carried or work item: `box`, `book`, `sheet`, `laptop`, `paper`, `pencil`, `flask` | as is |

Every layer is a separate Cycles view layer of the same scene. Overlay layers (face, kit, item) see the robot as a holdout, so each comes out already cut where the body passes in front of it, and occlusion is right from every direction when they are drawn after the body in fixed order.

**Tint.** Masked layers carry a tint mask: the teal shell's coverage times its share of diffuse and emitted light, so specular highlights stay white. Only the teal is masked: joints, face, hands, eyes, ear rings and the cream torso never tint. Per sRGB channel: `rgb = rgb * (1 - mask + mask * host / grey)`. Masks are stored smaller than their colour layer (`mask_scale`: a quarter at 1x and 4x, an eighth at 2x) in 8 levels.

**Desk split.** Seated frames split the body at the desk top (world height 0.74 m): draw `body_low`, then the desk, then `body_high`. At the bench (facing S) the desk hides the legs; at a terminal (facing N) the robot is in front of its desk, so both halves go after it.

**Camera and look.** The canonical camera every render shares (`artlib.canonical_camera`, `docs/design/art-direction.md`, "Camera"): oblique, yaw 30°, rays falling at atan(1/2), 171.528 px per metre at 1x, 343 at 2x, 686 at 4x. The studio is the bake-off's (`art/scripts/bakeoff.py`): a soft disk key from the upper left of the view and a dim `white_studio_06` fill. Cycles on the GPU renders every frame at 4x; 2x and 1x are scaled down from it.

**Directions.** Four facings along the room's axes, named by the deck's facing: `S` 0° (towards the door, the camera side), `E` 90°, `N` 180° (the back wall), `W` 270°. All four are rendered: the camera is yawed, so a mirror would face off the room's axes, and the robot's motion is not symmetric. Seated clips come in the two seat facings, `S` (the bench, sofas, armchair) and `N` (the terminals), as are `StandUp`, `ThumbsUp` and `BoxIdle` (budget). Eight facings do not fit the 8 MB budget alongside the full clip list (see *Budget*); the manifest's `directions` carries the count.

**The robot.** `art/motion-test/robot_body.py` cuts the whole-body model into rigid pieces with black ball joints, colours it by smoothed colour regions (flat sheet colours), and models the face plate, eyes, Claude band, ear discs and agent dot. `robot_hands.py` makes the posed-hand library. `motion_rig.py` retargets each Mixamo clip by moving the Mixamo skeleton's joints onto the model's, keeping every bone's rest orientation. It also poses the arms directly where a clip needs it: typing and writing on the desk, props gripped by their sides, the thumbs-up hand kept off the visor, the flask. Seated clips keep only a little of Mixamo's lean, so the face stays visible over the desk.

## Looks

| Deck behaviour | Where | Sprite set provides | Status |
|---|---|---|---|
| **Host colour tint.** The `Main` material takes the host colour: fixed for `node-a`…`node-d` (`#ff9340`, `#2dd4bf`, `#a78bfa`, `#facc15`), otherwise `hsl(hash, 72%, 62%)`. | `looks.js` `hostLook` | the `body` layers in grey with the teal tint mask; the runtime tints each image once per colour and caches it | ✅ |
| **One host accessory**: `backpack`, `antenna`, `halo`, `crest`, following the animation. | `agents.js` `buildRobot` | `acc_<kind>` for every frame of every clip, remodelled for this robot: a host-coloured pack with a dark strap on the torso's back; a dark rod and host-coloured ball off the helmet's right; an emissive halo over the helmet; a host-coloured fin along the helmet's crown. `kit_top` anchors where a kit rises above the helmet. | ✅ |
| **Agent face.** Claude: a coral-orange (`#ff8f6b`) band across the visor. Codex: the eyes glow `#7ce7ff`. Both carry a dot of the agent colour on the back of the head. Unknown agents get `#cbd5e1` with the band. | `looks.js` `AGENT_COLOR` | `face_band` (band plus dot) and `face_eyes` (the sheet's two capsule eyes plus dot), white emissive per frame; the runtime colours them. The visor stays dark under both. | ✅ |
| **Stalled dim.** The body turns to `mix(host, #475163, 0.55)` and the face to `mix(agent, #1b2333, 0.6)`. | `motion.js` `tone` | nothing extra: a second tint colour and a dimmed face colour (shown in the preview and in `faces-kits.png`) | ✅ |
| **Resting look.** Face lowered to `mix(agent, #1b2333, 0.35)`. | `motion.js` `tone` | nothing extra | ✅ |
| **Panel portraits.** A small Idle android (0.5 s in, turned 0.45 rad), and an `off` pose. | `panel.js` | the runtime takes `Idle` frame 0, direction S (the nearest), all layers, at 2x | ✅ (no separate frame: `Idle` S frame 0) |

## Clips

The deck plays RobotExpressive clips through an `AnimationMixer` with 0.3 s crossfades, plus procedural bone offsets on top. Sprites cannot add bone offsets, so every offset the deck applies becomes its own rendered clip or pose. All clips are Mixamo, retargeted (`source` per clip); loops are made seamless (the last 30% of each curve eased onto the first frame).

| Deck behaviour | Where | Sprite set provides | Status |
|---|---|---|---|
| **Walking** between stations; timescale 1.25, and 0.6 for pacing. | `motion.js` `updateEnt`, `clipFor` | `Walking` (walk-normal, in place), loop, 4 directions; the runtime sets the rate | ✅ |
| **Idle** standing at a spot with no seat. | `clipFor` | `Idle` (standing-idle) loop, 4 directions | ✅ |
| **Sitting at terminal desks**, facing the back wall. | `looks.js` `SPOTS.terminal` | `Sitting` (stand-to-sit, once, held on its last frame) and `SitIdle` (sitting-idle loop), direction N | ✅ |
| **Sitting at the bench**, facing the viewer's side (also the dock sofas and the reading armchair). | `SPOTS.workbench` | the same, direction S | ✅ |
| **Typing** while seated at a `hands` activity. | `motion.js` `updateEnt` | `Typing` loop, S and N: typing's arms laid on the desk, fists on a laptop's keys, back straight, head level; `item_laptop` | ✅ |
| **Standing up** before walking off from a seat or from Death. | `stepMotion` | `StandUp` (sit-to-stand, once), S and N | ◐ S and N only (budget): Death can happen anywhere, so an android standing up facing E or W takes the nearest of S or N |
| **Wave** while delegating, looping. | `clipFor` | `Wave` (waving, the open hand) loop, 4 directions | ✅ |
| **Yes / No** after a test run passes or on an error, standing. | `noteEvents`, `react` | `Yes` (nod-yes), `No` (shake-no), once, 4 directions | ✅ |
| **Head-only nod / shake when seated**, over whatever the arms do. | `react`, `updateEnt` | `SitNod`, `SitShake`: the nod-yes and shake-no heads on the seated still body, S and N | ◐ still base only: the typing and reading bases (motion_rig.LAYERED `type-nod` … `read-shake`) are built but not rendered, for the budget. The runtime plays `SitNod` / `SitShake` for any seated base. |
| **ThumbsUp** once, when a known job turns done. | `state.js` | `ThumbsUp` (standing) and `SitThumbsUp`, S and N (budget; a standing E or W android turns to the nearest): the hand held in front of the chest and out to the side, never over the visor | ✅ |
| **Death** when failed: once, clamped on its last frame, wherever the android stopped. | `clipFor` | `Death` (dying-back, once, held), 4 directions; the pieces are rigid on their bones, so limbs stay connected | ✅ |
| **Stalled**: slumped, on a seat or standing. | `clipFor`, `updateEnt` | `Slump` (sad-idle loop) 4 directions; `SitSlump` (sitting-idle with the spine curled and the head dropped) S and N | ✅ (a stalled android away from a seat stands slumped rather than sitting on the floor) |
| **Reading a printout / book seated**. | `updateEnt` | `SitRead`: a book held at chest height in both hands, the head lowered just enough, the face visible; S and N | ✅ |
| **Writing** and **Holding** work loops. | `activity.js` | `Writing` (the pinch hand, a pencil, a sheet of paper on the desk) and `Holding` (a test tube held up in the right fist), S and N | ✅ |
| **Reduced motion**: once-clips jump to their last frame, loops hold frame 0. | `playClip`, `react` | nothing extra | ✅ |
| **Background rooms** (calm): 0.05×; no typing arms, nods or particles. | `updateEnt` | nothing extra: `SitIdle` in place of `Typing` | ✅ |

## Movement

All of this stays in the runtime: it is position, heading and timing, not art. The sprite set only has to be placeable and turnable.

| Deck behaviour | Sprite set provides | Status |
|---|---|---|
| **Stations** and **spots**, some seated. | `foot` per clip and facing (and `seat` for seated clips), so an android lands on its spot | ✅ |
| **Walking routes**, **dwell**, **pacing**, **walkers stepping round each other**. | nothing (the `Walking` loop at the pacing rate) | ✅ |
| **Delegates** facing their partner with an arc between heads; **turned to the press** with a beam from the head. | `head_top` per frame | ✅ |
| **Waiting on the human**, facing the viewer (π/4). | the nearest of the 4 facings | ✅ |
| **Sitting behind furniture**. | sprites unoccluded, split at the desk top; the floor draws chair, lower body, desk, upper body | ✅ |

## Carried items

v2 holds the items in the hands, so each is drawn with the clips that carry it:

| Item | Carried when | Clips |
|---|---|---|
| **Box** (0.30 × 0.24 × 0.22 m cardboard, tape strip), gripped by its sides in every frame | `ship` | `BoxIdle`, `BoxWalk` |
| **Book** (0.15 × 0.20 m, blue cover), held up in both hands | `read` | `BookWalk`, `SitRead` |
| **Sheet** (a curled printout with grey lines, between two pinch hands) | `review` | `SheetWalk` |
| **Laptop**, **paper and pencil**, **flask** | the seated work loops | `Typing`, `Writing`, `Holding` |

A standing android holding a book or sheet without walking uses frame 0 of `BookWalk` / `SheetWalk`; the deck's seated box pose is left out, as before.

## Effects and overlays

Drawn by the runtime; the sprite set supplies anchors.

| Deck behaviour | Sprite set provides | Status |
|---|---|---|
| **Smoke** over failed androids, **motes** over finished ones, **selection ring**, **session halo**. | nothing: particles and decals at the root | ✅ |
| **Contact shadow**. | the `shadow` layer | ✅ |
| **Tags and bubbles** hang from the head, lifted by the kit. | `head_top` (the helmet's top as seen) per frame; `kit_top` per kit where it rises above it | ✅ |
| **Click hit area**. | `hit`: the body's bounds per frame | ✅ |
| **Hands** (held items, the action glyph). | `hand_l`, `hand_r`: the centres of the hand pieces per frame | ✅ |
| **Camera focus**. | nothing | ✅ |

## Deliberate differences

- **Crossfades.** The deck blends clips over 0.3 s. Sprites cut at the next frame boundary.
- **Headings** snap to the 4 facings.
- **Seated nods** are rendered over the still base only (see *Clips*).
- **Slow idles.** The idle loops are Mixamo's own slow breathing clips, sampled at 6–8 frames (1.5–2 fps); `Typing` and `Writing` loop a 1.2 s and a 2 s window of their clips (6.7 and 4 fps), `Walking` its 1.17 s cycle at 12 frames.

## What is rendered

| Clip | Source (Mixamo) | Frames | Facings | Loop | Items |
|---|---|---|---|---|---|
| `Walking` | walk-normal, in place | 12 | S E N W | yes | |
| `Idle` | standing-idle | 8 | S E N W | yes | |
| `Wave` | waving | 8 | S E N W | yes | |
| `Yes`, `No` | nod-yes, shake-no | 8 each | S E N W | once | |
| `Death` | dying-back | 12 | S E N W | once, held | |
| `ThumbsUp` | thumbs-up-standing | 8 | S N | once | |
| `Slump` | sad-idle | 6 | S E N W | yes | |
| `BoxIdle` | box-idle | 6 | S N | yes | box |
| `BoxWalk` | box-walk-arc, in place | 8 | S E N W | yes | box |
| `BookWalk` | walk-normal, in place, book in both hands | 8 | S E N W | yes | book |
| `SheetWalk` | walking-reading-phone, in place (1.2 s) | 8 | S E N W | yes | sheet |
| `StandUp` | sit-to-stand | 8 | S N | once | |
| `Sitting` | stand-to-sit | 8 | S N | once, held | |
| `SitIdle` | sitting-idle | 8 | S N | yes | |
| `Typing` | typing (1.2 s), arms on the desk | 8 | S N | yes | laptop |
| `Writing` | writing-seated (2 s) | 8 | S N | yes | paper, pencil |
| `SitRead` | sitting-idle, reading upper body, book held | 8 | S N | yes | book |
| `Holding` | sitting-idle, the flask held up | 8 | S N | yes | flask |
| `SitThumbsUp` | thumbs-up-sitting | 8 | S N | once | |
| `SitSlump` | sitting-idle, slumped | 6 | S N | yes | |
| `SitNod`, `SitShake` | nod-yes, shake-no heads on sitting-idle | 6 each | S N | once | |

## Budget

23 clips, 536 frames, 4609 layer images; 4308 after layers that barely change within a clip and facing are shared.

| Set | Colour (WebP) | Masks (PNG) | Shadows (WebP) | Total | Loaded |
|---|---|---|---|---|---|
| 1x | 4 pages | 4 pages | 1 pages | 2.48 MB | eager |
| 2x | 15 pages | 15 pages | 2 pages | 4.95 MB | eager |
| 4x | 56 pages | 56 pages | 6 pages | 15.64 MB | on demand, when zoomed close |
| `sprites.json` | | | | 0.55 MB | eager |

1x + 2x + manifest: **7.99 MB**, at the 8 MB limit. To fit the full clip list, v2 stores the tint masks at a quarter (1x, 4x) and an eighth (2x) of their layer's size, WebP quality is 42 at 1x and 31 at 2x (alpha 28 / 22), 80 at 4x; pages are at most 2048². At 2x the lower quality shows only as slight softening on the shell's highlights. `StandUp`, `ThumbsUp` and `BoxIdle` are rendered in S and N only, and eight facings do not fit: every further facing of the standing clips costs about 1.5 MB up front. Rendering took about 20 s a frame on the RTX 3090 (about 3 h).

## Manifest

`packages/fleet-web/src/fleet_web/static/assets/world/robot/sprites/sprites.json`, written by `build_robot_sprites.py`. The floor runtime (branch `renovate/floor`) reads it. Pixel values are at 1x unless they sit under a resolution key.

```jsonc
{
  "version": 2,
  "camera": { "name": "canonical", "projection": "oblique", "yaw_deg": 30.0, "depression_deg": 26.5651, "axes_px_per_m": [[0.86603, 0.25], [0.5, -0.43301], [0.0, -1.0]], "px_per_m_1x": 171.528 },
  "robot": { "height_m": 1.081, "source": "art/motion-test (whole-body model, Mixamo clips)" },
  "resolutions": {
    "1x": { "scale": 1, "load": "eager", "mask_scale": 4,     // masks are stored this many times smaller
            "pages": [ { "color": "1x/color-0.webp", "mask": "1x/mask-0.png", "size": [2048, 1990] } ],
            "shadow_pages": [ { "image": "1x/shadow-0.webp", "size": [1204, 840] } ] },
    "2x": { "scale": 2, "load": "eager", "mask_scale": 8, ... },
    "4x": { "scale": 4, "load": "on demand", "mask_scale": 4, ... }
  },
  "directions": { "S": { "facing_deg": 0 }, "E": { "facing_deg": 90 }, "N": { "facing_deg": 180 }, "W": { "facing_deg": 270 } },
  "grey": "#cccccc",
  "tint": "rgb = rgb * (1 - mask + mask * host / grey), per sRGB channel",
  "masked": ["body", "body_low", "body_high", "acc_backpack", "acc_antenna", "acc_crest"],
  "tinted_whole": ["acc_halo"],
  "shadow_scale": 4,                        // shadows are stored this many times smaller
  "draw_order": ["shadow", "body_low", "(desk)", "body", "body_high", "face_*", "acc_*", "item_*"],
  "faces": { "face_eyes": "codex", "face_band": "claude" },   // white: multiply by the agent colour
  "accessories": ["acc_backpack", "acc_antenna", "acc_halo", "acc_crest"],
  "items": ["item_box", "item_book", "item_sheet", "item_laptop", "item_paper", "item_pencil", "item_flask"],
  "seat_point_m": [0.0, 0.0, 0.549],       // the seated robot's seat point in its frame: the floor under it is `foot`
  "desk_top_m": 0.74,                      // the desk split height, in the same frame
  "seat_furniture": { "seat_height_m": 0.549, "chair_behind_m": 0.244, "desk_edge_ahead_m": 0.159, "desk_top_m": 0.74 },
  "clips": {
    "Walking": {
      "fps": 10.29, "loop": true, "hold_last": false, "frames": 12, "seated": false, "source": "walk-normal",
      "items": [],
      "dirs": {
        "S": {
          "canvas": [202, 330],            // the frame's size
          "foot": [101.4, 250.2],          // where the robot's root (its floor point) lands
          "footprint": [51.46, 25.73],     // radii of the floor ellipse it stands on
          "seat": [...],                   // seated clips only: where seat_point lands
          "frames": [ {
            "anchors": { "head_top": [...], "kit_top": { "halo": [...], ... }, "hand_l": [...], "hand_r": [...] },
            "hit": [x, y, w, h],             // the body's bounds
            "layers": {
              "body":  [[page, x, y, w, h, ox, oy], [...], [...]],   // one entry per resolution, in their order
              "shadow": [...], "face_eyes": [...], "acc_antenna": [...], "item_box": [...]
            }
          } ]
        }
      }
    }
  }
}
```

A layer has one entry per resolution, in the order of `resolutions`: its page, its rect in that page, and its offset in the frame's canvas, all in that resolution's pixels. Shadows index `shadow_pages` and are drawn `shadow_scale` times their stored size; everything else indexes `pages`. A masked layer's mask is its rect divided by `mask_scale` in the page's mask image (grey, 8 levels), stretched back over the layer. A layer missing from a frame is empty there. Layers that barely change within a clip and facing share one image (`stats` counts them).

Rules for the runtime:
- Place a frame so its `foot` lands on the robot's floor position. A seated frame's `foot` is the floor under the seat point: put it under the chair's seat (whose seat is at `seat_furniture.seat_height_m`), or equivalently put `seat` on the seat anchor.
- `loop: false` clips play once; `hold_last` ones stay on their last frame. Under reduced motion, loops show frame 0 and once-clips their last frame.
- A clip is drawn only in the facings it lists, with only the items it lists.
- Faces are white: multiply by the agent colour (dimmed for the stalled and resting looks).
- Tags hang from the host kit's `kit_top` if the frame lists one for it, else from `head_top`, plus a margin. Items can be anchored to `hand_l` / `hand_r`. `hit` is the click box, frontmost first.

## Preview

`/prototype/robot` (`packages/fleet-web/src/fleet_web/static/prototype/robot.js`, served by the deck, not linked from it) is a reference runtime on one 2D canvas, no WebGL. It reads the camera from the manifest, tints each layer image through its mask the first time it is drawn for a host (and keeps it), colours the face, composites the layers in the order above, and draws a bench of the kit's size with raised chairs from `seat_furniture`. A robot walks round it (in front, round its end, behind), sits down, types and stands up; desks 1 and 3 hold a writing and a reading robot. Every clip, facing, face, kit and look can be picked. `?scene=bench` puts a typist at desk 2 and keeps the walker walking, so it passes behind the bench. Its frame time is measured by `art/scripts/measure_robot_preview.py` in headless Chromium at 1672 × 941, frame rate uncapped:

| Mode | Zoom | Sprites | Frame ms (mean) | p95 | fps | Draw ms | Runs (ms) |
|---|---|---|---|---|---|---|---|
| no GPU (SwiftShader) | far | 1x | 2.41 | 3.2 | 414.9 | 0.171 | 2.07, 2.41, 2.99 |
| no GPU (SwiftShader) | mid | 2x | 3.49 | 5.1 | 286.5 | 2.767 | 3.17, 3.49, 3.55 |
| no GPU (SwiftShader) | close | 4x | 4.2 | 3.7 | 238.1 | 3.432 | 2.62, 4.2, 5.14 |
| GPU (ANGLE GL) | far | 1x | 1.81 | 2.1 | 552.5 | 0.105 | 1.62, 1.81, 1.93 |
| GPU (ANGLE GL) | mid | 2x | 3.12 | 4.5 | 320.5 | 2.482 | 2.7, 3.12, 3.48 |
| GPU (ANGLE GL) | close | 4x | 2.76 | 3.7 | 362.3 | 2.25 | 2.62, 2.76, 3.22 |

## Open

- **Seated nods over typing and reading** are not rendered (budget); `SitNod` / `SitShake` stand in.
- **StandUp, ThumbsUp and BoxIdle** exist in S and N only; E and W would add about 1.3 MB up front.
- **Slump away from a seat** stands slumped (`Slump`) rather than sitting on the floor.
- **The floor's chair** is raised to 0.549 m (its gas lift, `art/scripts/render_props.py`) and spaced by `seat_furniture` on `renovate/floor`.
- **`Holding`'s free hand** (S) rests below the 0.74 m desk top, so at a desk it is hidden but for a sliver of fingertips on the desk, detached from the arm. The rig should lift that hand onto the desk, as `Typing` does.
- **Seated shadows** are drawn under the chair, not under the seat point: with the robot's feet hanging clear, the floor under the seat showed its shadow below the desk's near edge, detached (fixed in the preview and on the floor; `docs/design/sprite-world.md`, *Robots*).
