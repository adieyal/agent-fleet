# Robot sprites: deck parity

The world is moving to a 2D canvas sprite runtime with no runtime 3D (decided by the bake-off, `art/bakeoff/`). The androids become Blender-rendered sprites of the concept robot: new meshes on RobotExpressive's rig and clips, from `art/scripts/build_robot.py` (`fleet/web/assets/world/robot/robot.glb`), rendered by `art/scripts/build_robot_sprites.py` into `fleet/web/assets/world/robot/sprites/`. They must keep every behaviour and look the deck has today.

This document lists what the deck does (`fleet/web/js/agents.js`, `motion.js`, `looks.js`, `activity.js`, plus the few other places that touch an android) and what the sprite set must provide for each, says what is rendered so far, and fixes the manifest the floor runtime reads (`sprites/sprites.json`).

Deck units below are room tiles (an android is `BOT_H` = 2.2 tiles tall; `BK` = 2.2 / 1.7 scales the kit). Angles are the deck's `facing`: 0 points towards the door (+y), π towards the back wall.

## How the sprites are built

Each drawn android is a stack of layers from the same frame, composited on the canvas:

| Layer | Holds | Runtime treatment |
|---|---|---|
| `shadow` | the contact shadow alone, from a shadow catcher under a soft overhead light; black with alpha, stored at a quarter of its size | drawn first, under everything at floor level, scaled ×4 (`shadow_scale`) |
| `body` (standing) or `body_low` + `body_high` (seated) | the whole robot but its face lights, kit and items: shell in neutral grey `#cccccc`, joints, visor, hands | the shell tinted by its mask (see *Tint*) |
| `face_eyes` / `face_band` | the agent face: Codex's cyan eyes, Claude's coral band, emissive | one of the two, per agent; dim with a canvas filter for the stalled and resting looks |
| `acc_<kind>` | one host kit: `backpack`, `antenna`, `halo`, `crest` | tinted by its mask; the halo is tinted whole |
| `item_<name>` | a carried item: `box`, `book`, `sheet`, `laptop`, `pencil`, `flask` | as is |

Every layer is a separate Cycles view layer of the same scene. Overlay layers (face, kit, item) see the robot as a holdout, so each comes out already cut where the body passes in front of it, and occlusion is right from every direction when they are drawn after the body in fixed order.

**Tint.** Masked layers carry a tint mask: the shell's coverage times its share of diffuse and emitted light, so specular highlights stay white. Per sRGB channel: `rgb = rgb * (1 - mask + mask * host / grey)`. Masks are stored smaller than their colour layer (`mask_scale`: half at 1x and 4x, a quarter at 2x) in 8 levels.

**Desk split.** Seated frames split the body at the desk top (by world height): draw `body_low`, then the desk, then `body_high`. At the bench (facing S) the desk hides the legs; at a terminal (facing N) the robot is in front of its desk, so both halves go after it.

**Camera and look.** The bake-off B1 camera and studio (`art/scripts/bakeoff.py`): l2's orthographic view, pitch 44.5°, yaw 21.25°, 171.5 px per metre at 1x; a soft disk key from the upper left of the view and a dim `white_studio_06` fill, with ambient occlusion from the path tracing. Cycles on the GPU renders every frame at 4x; 2x and 1x are scaled down from it.

**Directions.** Four facings along the room's axes, named by the deck's facing: `S` 0° (towards the door, the camera side), `E` 90°, `N` 180° (the back wall), `W` 270°. All four are rendered: the l2 camera is yawed 21.25°, so a mirrored sprite would face 42.5° off the room's axes, and the robot is not symmetric anyway (the antenna, the waving arm, the flask hand). Seated clips come in the two seat facings, `S` (the bench, sofas, armchair) and `N` (the terminals).

## Looks

| Deck behaviour | Where | Sprite set must provide |
|---|---|---|
| **Host colour tint.** The `Main` material takes the host colour: fixed for `node-a`…`node-d` (`#ff9340`, `#2dd4bf`, `#a78bfa`, `#facc15`), otherwise `hsl(hash, 72%, 62%)`, so any hue can occur. | `looks.js` `hostLook`; `agents.js` `buildRobot` | A `body` layer per frame rendered white and shaded, for runtime multiply. The runtime tints each sheet once per colour into an offscreen canvas and caches it. |
| **One host accessory**, from `backpack`, `antenna`, `halo`, `crest`, bone-attached so it follows the animation. Backpack: host-coloured box on the torso back with a dark strap. Antenna: dark rod plus host-coloured ball off the head's right. Halo: unlit host-coloured torus above the head. Crest: host-coloured fin along the head. | `agents.js` `buildRobot` | `acc_<kind>` (host-coloured parts, tinted) and `acc_<kind>_dark` for every frame of every clip, 4 kinds. The halo renders emissive so it stays flat and bright. Each kind also gives its `kit_top` (how far it rises above the head) for tag placement. |
| **Agent face.** Claude: a band across the visor in `#ff8f6b` (coral-orange). Codex: the robot's own eyes glow `#7ce7ff` (emissive 1.4). Both carry a small dot of the agent colour on the back of the head, so the agent reads from behind. Unknown agents get `#cbd5e1` with the band. | `looks.js` `AGENT_COLOR`; `agents.js` | `face_band` (band plus back dot) and `face_eyes` (the concept's two eyes plus back dot), white emissive masks per frame; the runtime colours them. The concept visor stays dark under both. |
| **Stalled dim.** The body turns to `mix(host, #475163, 0.55)` and the face to `mix(agent, #1b2333, 0.6)`; Codex eyes drop to emissive 0.2. | `motion.js` `tone` | Nothing extra: a second tint colour for `body`/`acc` and a dimmed face colour. |
| **Resting look.** Done, cancelled and idle-session androids, once arrived, keep the body colour but lower the face to `mix(agent, #1b2333, 0.35)`. | `motion.js` `tone` | Nothing extra: a face colour. |
| **Panel portraits.** The side panel, host list and lobby key draw a small Idle android (0.5 s into Idle, turned 0.45 rad), and an `off` pose for an unhealthy host: dim body, face and eyes dark. | `panel.js` `portrait`, `miniBot` | A portrait frame: Idle at 0.5 s, the direction nearest 0.45 rad, all layers, at 2× so the 34×44 px canvas stays sharp. |

## Clips

The deck plays RobotExpressive clips through an `AnimationMixer` with 0.3 s crossfades, plus procedural bone offsets on top. Sprites cannot add bone offsets, so every offset the deck applies becomes its own rendered clip or pose.

| Deck behaviour | Where | Sprite set must provide |
|---|---|---|
| **Walking** between stations; timescale 1.25, and 0.6 for pacing strolls. | `motion.js` `updateEnt`, `clipFor` | `Walking` loop, 4 directions. The runtime sets the frame rate from the timescale (1.25 normal, 0.6 pacing). |
| **Idle** when standing at a spot with no seat. | `clipFor` | `Idle` loop, 4 directions. |
| **Sitting at terminal desks**, facing the back wall (π), seat at the chair in front of each of the 3 terminals. | `looks.js` `SPOTS.terminal`; `clipFor` | `SitDown` (the `Sitting` clip, played once) and `SitIdle` (its last frame held), direction π. |
| **Sitting at the bench**, worked from the far side, facing the viewer's side of the room (0). | `SPOTS.workbench` | Same clips, direction 0. Also used on the dock sofas and the reading armchair (both face 0). |
| **Typing** while seated at a `hands` activity (type, edit): lower arms oscillate `sin(t·15 + i·2.1)·0.14`. | `motion.js` `updateEnt` | `SitType` loop, directions π and 0, from the arm-only `Type` clip already in `robot.glb`. |
| **Standing up** before walking off from a seat or from Death (0.8 of the clip). | `stepMotion` | `StandUp` (the `Standing` clip, once), 4 directions (Death can happen anywhere). |
| **Wave** while delegating (active, act `delegate`), looping, facing the partner. | `clipFor` | `Wave` loop, 4 directions. |
| **Yes / No** after a test run passes, or on an error, when standing: the full clip once. | `motion.js` `noteEvents`, `react` | `Yes` and `No`, once, 4 directions. |
| **Head-only nod / shake when seated**: 1.5 s, head pitch (yes) or yaw ×1.3 (no) by `sin(·13)·0.32`, on top of whatever the arms do. | `react`, `updateEnt` | `SitNod` and `SitShake`, 1.5 s, directions π and 0, for each seated base the deck can combine them with: still, typing, and reading a sheet. That is 6 clips. |
| **ThumbsUp** once, when a job the page already knew turns done, before walking to the sofa. | `state.js` | `ThumbsUp`, once, 4 directions. |
| **Death** when failed: the clip once, clamped on its last frame, wherever the android stopped (a seated one falls from its seat). | `clipFor` | `Death`, once, 4 directions. |
| **Stalled**: the `Sitting` clip wherever the android is, head slumped forward 0.55 rad. On a seat it sits in it; elsewhere it sits on the floor. | `clipFor`, `updateEnt` | `SitSlump` (sit down, then held slumped), 4 directions. |
| **Reading a printout seated**: head tipped down 0.3 rad while holding a sheet. | `updateEnt` | `SitRead` pose, directions π and 0. |
| **Reduced motion**: once-clips jump to their last frame, loops hold frame 0, turns are instant, no nods, no pacing. | `playClip`, `react`, `pace` | Nothing extra: each clip's first and last frames. |
| **Background rooms** (calm): animation and walking run at 0.05×; no typing arms, nods or particles. | `updateEnt`, `stepMotion` | Nothing extra: the runtime slows the frame clock and uses `SitIdle` in place of `SitType`. |

## Movement

All of this stays in the runtime: it is position, heading and timing, not art. The sprite set only has to be placeable and turnable.

| Deck behaviour | Where | Sprite set must provide |
|---|---|---|
| **Stations**: each activity maps to a station (terminal, workbench, whiteboard, comms, bookshelf then armchair, cabinet, think, lounge, dock, kitchen, mail, rack, press, partner, stay), with an ordered list of spots, some seated. | `looks.js` `ACTS`, `SPOTS`; `motion.js` `allocate` | Root pixel per frame, so an android lands on its spot. |
| **Walking routes** along the three aisles and two crossings, around furniture. | `motion.js` `route` | Nothing. |
| **Dwell**: a change of station waits 3.5 s and 1.2 s after arrival, so bursts don't send androids back and forth. Multi-station activities move on after 1.8 s (book off the shelf, then the armchair). | `assignTargets`, `nextStage` | Nothing. |
| **Pacing**: thinking androids stroll 1.4 tiles and back, idle ones 2.2, at 0.4× speed with the slow walk. | `pace` | The `Walking` loop at the pacing rate. |
| **Delegates** stand beside their partner, facing them, with a dashed arc between their heads. | `allocate`; `main.js` | Head anchor per frame (below). |
| **Walkers step around each other** across the view; seated androids lift by the seat height (0 today). | `stepMotion`; `updateEnt` | Nothing. |
| **Waiting on the human** (idle session): stays put, facing the viewer (π/4). | `allocate` | Nothing beyond the 4 directions. |
| **Turned to the press** while a document prints, with a dashed beam from 0.3 kit-units below the head to the slit. | `updateEnt`; `docs3d.js` | Head anchor per frame. |
| **Sitting behind furniture**: at terminals the chair back is between the android and the camera; at the bench the desk is. The deck gets this from the depth buffer. | scene | Sprites render unoccluded, without holdouts. The floor must supply chairs and desks as separate occluder sprites drawn after a seated android. B1 baked the desk in as a holdout; that ties a sprite to one desk and does not scale to every seat. |

## Carried items

The deck fixes these items in the android's own frame, not in a hand. The sprites hang them from the torso in front of the chest, so they ride its motion, and render them per frame of the clips that carry them. Positions in the deck are `[x, y, z, tilt]` in deck units.

| Item | Carried when | Stance | Sprite set must provide |
|---|---|---|---|
| **Box** (`#b98b52` parcel, 0.5 × 0.38 × 0.4) | `ship` (commit/push), from the start until it goes in the outbox 0.6 s after arrival | standing, walking | `item_box`, standing, 4 directions: rendered with Walking and Idle |
| **Book** (`#b4463c`, tilted −1.0; seated −1.1) | `read`, once off the shelf, to the armchair | standing, walking, seated | `item_book`: rendered with Walking, Idle and Sitting |
| **Sheet** (`#f4f6fa` printout, tilted −1.1; seated −0.55) | `review`, at the bench, not while walking | standing, seated | `item_sheet`: rendered with Idle and Sitting |

The deck also defines a seated box pose, but no station seats a shipper, so it is left out. Three more items belong to the seated work loops: `item_laptop` (on the desk under the typing hands, with Typing), `item_pencil` (with Writing) and `item_flask` (with Holding), the last two in the right hand.

## Effects and overlays

These are drawn by the runtime. The sprite set only supplies anchors.

| Deck behaviour | Where | Sprite set must provide |
|---|---|---|
| **Smoke over failed androids**: grey (0.5, 0.52, 0.58) puffs rising from 0.5 tiles up, at a 12% chance per frame, living 1.8–2.8 s. A red additive glow (1.3 tiles, at 0.45 up) sits on them. | `motion.js` `updateEnt`, `spawn` | Nothing: runtime particles and a soft-dot glow from the root point. |
| **Motes over finished androids**: green (0.29, 0.87, 0.5) motes rising from near the floor, 3% per frame, over a soft green floor disc (radius 0.62). | same | Nothing, as above. |
| **Contact shadow**: a soft floor disc, 0.9 BK. | `createEnt` | The `shadow` layer, which replaces the disc with the rendered pose's shadow. |
| **Selection ring**: host colour, pulsing opacity. **Session halo**: pink `#f472b6` ring, breathing while the session works. | `createEnt`, `updateEnt` | Nothing: floor decals at the root. |
| **Tags and bubbles** hang from the head bone, lifted by head top + accessory height (`kit_top`) + 0.12 BK, pushed up to avoid overlap, with a lead line back down. | `agents.js` `positionTags` | Per frame, the head anchor in frame pixels; per accessory, `kit_top`. The runtime adds the margin. |
| **Click hit area**: an invisible upright cylinder, radius 0.42 BK, height `BOT_H`, around the root; the nearest hit wins. | `createEnt`; `camera.js` `pick` | Nothing: the runtime projects the same cylinder and takes the frontmost in draw order. |
| **Camera focus** centres the android's root plus 0.7 of its height. | `camera.js` `focusOn` | Nothing. |

## Deliberate differences

- **Crossfades.** The deck blends clips over 0.3 s. Sprites cut at the next frame boundary. A 2-frame cross-dissolve is possible if the cut reads harshly; it is not planned.
- **Headings** snap to the 4 facings rather than easing through every angle. Diagonal headings (a delegate facing its partner, an idle session facing the viewer at π/4) take the nearest facing.
- **Death** knocks the head off: RobotExpressive's clip moves the head bone far from the neck.
- **Nods on other bases.** A seated nod or shake is rendered for the still, typing and reading bases only. Those are the only seated states that receive test verdicts or errors, since stalled and failed androids do not react.

## What is rendered

| Clip | Source | Frames | Facings | Loop | Items |
|---|---|---|---|---|---|
| `Walking` | Walking | 12 | S E N W | yes | box, book |
| `Idle` | Idle | 8 | S E N W | yes | box, book, sheet |
| `Wave` | Wave | 12 | S E N W | yes | |
| `Yes`, `No` | Yes, No | 8 each | S E N W | once | |
| `Death` | Death | 16 | S E N W | once, held on the last frame | |
| `Sitting` | Sitting (sitting down) | 8 | S N | once, held on the last frame | book, sheet |
| `Typing`, `Writing`, `Holding` | Type, Write, Hold arm clips over the end of Sitting | 8 each | S N | yes | laptop, pencil, flask |

Still to render for full parity: `ThumbsUp`, `StandUp` (the Standing clip), the seated nod and shake (`SitNod`, `SitShake` over still, typing and reading), `SitSlump` (stalled), `SitRead` (head down over a sheet), and the panel portrait.

## Budget

320 frames, 2,689 layer images; 2,309 after layers that barely change within a clip and facing are shared.

| Set | Colour (WebP) | Masks (PNG) | Shadows (WebP) | Total | Loaded |
|---|---|---|---|---|---|
| 1x | 5 pages | 5 pages | 1 page | 2.61 MB | eager |
| 2x | 18 pages | 18 pages | 2 pages | 5.04 MB | eager |
| 4x | 70 pages | 70 pages | 9 pages | 13.45 MB | on demand, when zoomed close |
| `sprites.json` | | | | 0.33 MB | eager |

1x + 2x + manifest: 7.99 MB. WebP quality is 64 at 1x, 50 at 2x and 78 at 4x (alpha 50 / 40 / 70); pages are at most 2048².

## Manifest

`fleet/web/assets/world/robot/sprites/sprites.json`, written by `build_robot_sprites.py`. The floor runtime (branch `renovate/floor`) reads it. Pixel values are at 1x unless they sit under a resolution key.

```jsonc
{
  "version": 1,
  "camera": { "name": "l2", "projection": "orthographic", "pitch_deg": 44.5, "yaw_deg": 21.25, "px_per_m_1x": 171.528 },
  "resolutions": {
    "1x": { "scale": 1, "load": "eager", "mask_scale": 2,     // masks are stored this many times smaller
            "pages": [ { "color": "1x/color-0.webp", "mask": "1x/mask-0.png", "size": [2048, 1990] } ],
            "shadow_pages": [ { "image": "1x/shadow-0.webp", "size": [1204, 840] } ] },
    "2x": { "scale": 2, "load": "eager", "mask_scale": 4, ... },
    "4x": { "scale": 4, "load": "on demand", "mask_scale": 2, ... }
  },
  "directions": { "S": { "facing_deg": 0 }, "E": { "facing_deg": 90 }, "N": { "facing_deg": 180 }, "W": { "facing_deg": 270 } },
  "grey": "#cccccc",
  "tint": "rgb = rgb * (1 - mask + mask * host / grey), per sRGB channel",
  "masked": ["body", "body_low", "body_high", "acc_backpack", "acc_antenna", "acc_crest"],
  "tinted_whole": ["acc_halo"],
  "shadow_scale": 4,                        // shadows are stored this many times smaller
  "draw_order": ["shadow", "body_low", "(desk)", "body", "body_high", "face_*", "acc_*", "item_*"],
  "faces": { "face_eyes": "codex", "face_band": "claude" },
  "accessories": ["acc_backpack", "acc_antenna", "acc_halo", "acc_crest"],
  "items": ["item_box", "item_book", "item_sheet", "item_laptop", "item_pencil", "item_flask"],
  "seat_point_m": [0.0, 0.2202, 0.2115],   // where the seated robot rests on its chair, in its own frame
  "desk_top_m": 0.4815,                    // the desk split height, in the same frame
  "clips": {
    "Walking": {
      "fps": 12.5, "loop": true, "hold_last": false, "frames": 12, "seated": false,
      "items": ["item_box", "item_book"],
      "dirs": {
        "S": {
          "canvas": [264, 386],            // the frame's size
          "foot": [131.2, 300.4],          // where the robot's root (its floor point) lands
          "footprint": [51.46, 36.07],     // radii of the floor ellipse it stands on
          "seat": [131.2, 250.1],          // seated clips only: where seat_point lands
          "frames": [ {
            "anchors": { "head_top": [...], "kit_top": { "antenna": [...], ... }, "hand_l": [...], "hand_r": [...] },
            // head_top: the helmet's top as seen; kit_top: only the kits that rise above it
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

A layer has one entry per resolution, in the order of `resolutions`: its page, its rect in that page, and its offset in the frame's canvas, all in that resolution's pixels. Shadows index `shadow_pages` and are drawn `shadow_scale` times their stored size; everything else indexes `pages`. A masked layer's mask is its rect divided by `mask_scale` in the page's mask image (grey, 8 levels), stretched back over the layer. A layer missing from a frame is empty there (the face from behind, for instance). Layers that barely change within a clip and facing share one image (`stats` counts them).

Rules for the runtime:
- Place a frame so its `foot` lands on the robot's floor position; a seated frame so its `seat` lands on the chair's seat anchor.
- `loop: false` clips play once; `hold_last` ones stay on their last frame. Under reduced motion, loops show frame 0 and once-clips their last frame.
- A clip is drawn only in the facings it lists.
- Tags hang from the host kit's `kit_top` if the frame lists one for it, else from `head_top`, plus a margin. Items can be anchored to `hand_l` / `hand_r`. `hit` is the click box, frontmost first.

## Preview

`/prototype/robot` (`fleet/web/prototype/robot.js`, served by the deck, not linked from it) is a reference runtime on one 2D canvas, no WebGL. It tints each layer image through its mask the first time it is drawn for a host (and keeps it), composites the layers in the order above, and walks a robot round the B2 bench: in front of it, round its end, behind it, then down onto a chair to type, drawn under and over the desk by the desk split. B2's typing robot sits at desk 1 for comparison. Its frame time is measured by `art/scripts/measure_robot_preview.py` in headless Chromium at 1672 × 941, frame rate uncapped:

| Chromium | Far (1x) | Mid (2x) | Close (4x) |
|---|---|---|---|
| GPU disabled (SwiftShader) | 1.31 ms | 3.78 ms | 4.59 ms |
| GPU (ANGLE GL) | 1.33 ms | 3.49 ms | 4.20 ms |

## Open

- **Scale against B2.** The concept robot's helmet is 0.71 m wide (`HEAD_M` in `build_robot.py`, fitted to the 3D l2 bench); B2's robots, fitted to l2 by head size, have 0.40 m helmets. On the B2 bench in the preview the concept robot is about 1.75 times B2's. One of the two fits is wrong; changing `HEAD_M` means rebuilding the robot and re-rendering the sprites (about 2 hours on the GPU).
- **Standing up** cuts from seated to standing: no `StandUp` frames yet.

