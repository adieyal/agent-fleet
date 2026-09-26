# Robot sprites: deck parity

The world is moving to a 2D canvas sprite runtime with no runtime 3D (decided by the bake-off, `art/bakeoff/`). The androids become Blender-rendered sprites of the concept robot: RobotExpressive's rig and clips with the restyled shell, visor and joints from `art/scripts/build_robot.py` (`fleet/web/assets/world/robot/robot.glb`). They must keep every behaviour and look the deck has today.

This document lists what the deck does (`fleet/web/js/agents.js`, `motion.js`, `looks.js`, `activity.js`, plus the few other places that touch an android) and what the sprite set must provide for each. The last section fixes the manifest the floor runtime reads.

Deck units below are room tiles (an android is `BOT_H` = 2.2 tiles tall; `BK` = 2.2 / 1.7 scales the kit). Angles are the deck's `facing`: 0 points towards the door (+y), π towards the back wall.

## How the sprites are built

Each drawn android is a stack of layers from the same frame, composited on the canvas:

| Layer | Holds | Runtime treatment |
|---|---|---|
| `shadow` | contact shadow from a shadow catcher | drawn first, under everything at floor level |
| `base` | joints, visor, hands, everything not host-coloured, with full shading | drawn as is |
| `body` | the `robot_body` shell rendered white, shaded | multiplied by the host colour (see *Tint*) |
| `face_band` / `face_eyes` | the agent face, emissive only, white | coloured by the agent colour; one of the two per agent |
| `acc_<kind>` / `acc_<kind>_dark` | one host accessory: host-coloured parts white, dark parts as is | the first is tinted like `body` |
| `item_<name>` | a carried item | drawn after the robot |

Overlay layers (face, accessory, item) are rendered with the robot as a holdout, so each is already cut where the body passes in front of it. The runtime then draws them in fixed order after `base` and `body`, and occlusion comes out right from every direction without per-frame depth.

Frames are rendered in Cycles with Blender 4.2 (`~/.local/bin/blender`), unlit at runtime like the bake-off's B1, from the floor's camera (orthographic; today the l2 camera, pitch 44.5°, yaw 21.25°, named in the workbench manifest as `camera_l2`), at the floor's 1×/2×/4× densities. Each frame carries the pixel its root point (floor, between the feet) lands on, so the runtime places it by projecting the android's world position.

**Directions.** The deck turns androids smoothly to any angle. Sprites come in 8 directions (45° apart, starting at facing 0); the runtime shows the nearest to the android's current eased heading. Seated poses are only reached at seats, which face π (terminals) or 0 (bench, sofas, armchair), so seated clips need those 2 directions; poses that can happen anywhere need all 8.

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
| **Walking** between stations; timescale 1.25, and 0.6 for pacing strolls. | `motion.js` `updateEnt`, `clipFor` | `Walking` loop, 8 directions. The runtime sets the frame rate from the timescale (1.25 normal, 0.6 pacing). |
| **Idle** when standing at a spot with no seat. | `clipFor` | `Idle` loop, 8 directions. |
| **Sitting at terminal desks**, facing the back wall (π), seat at the chair in front of each of the 3 terminals. | `looks.js` `SPOTS.terminal`; `clipFor` | `SitDown` (the `Sitting` clip, played once) and `SitIdle` (its last frame held), direction π. |
| **Sitting at the bench**, worked from the far side, facing the viewer's side of the room (0). | `SPOTS.workbench` | Same clips, direction 0. Also used on the dock sofas and the reading armchair (both face 0). |
| **Typing** while seated at a `hands` activity (type, edit): lower arms oscillate `sin(t·15 + i·2.1)·0.14`. | `motion.js` `updateEnt` | `SitType` loop, directions π and 0, from the arm-only `Type` clip already in `robot.glb`. |
| **Standing up** before walking off from a seat or from Death (0.8 of the clip). | `stepMotion` | `StandUp` (the `Standing` clip, once), 8 directions (Death can happen anywhere). |
| **Wave** while delegating (active, act `delegate`), looping, facing the partner. | `clipFor` | `Wave` loop, 8 directions. |
| **Yes / No** after a test run passes, or on an error, when standing: the full clip once. | `motion.js` `noteEvents`, `react` | `Yes` and `No`, once, 8 directions. |
| **Head-only nod / shake when seated**: 1.5 s, head pitch (yes) or yaw ×1.3 (no) by `sin(·13)·0.32`, on top of whatever the arms do. | `react`, `updateEnt` | `SitNod` and `SitShake`, 1.5 s, directions π and 0, for each seated base the deck can combine them with: still, typing, and reading a sheet. That is 6 clips. |
| **ThumbsUp** once, when a job the page already knew turns done, before walking to the sofa. | `state.js` | `ThumbsUp`, once, 8 directions. |
| **Death** when failed: the clip once, clamped on its last frame, wherever the android stopped (a seated one falls from its seat). | `clipFor` | `Death`, once, 8 directions. |
| **Stalled**: the `Sitting` clip wherever the android is, head slumped forward 0.55 rad. On a seat it sits in it; elsewhere it sits on the floor. | `clipFor`, `updateEnt` | `SitSlump` (sit down, then held slumped), 8 directions. |
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
| **Waiting on the human** (idle session): stays put, facing the viewer (π/4). | `allocate` | Nothing beyond the 8 directions. |
| **Turned to the press** while a document prints, with a dashed beam from 0.3 kit-units below the head to the slit. | `updateEnt`; `docs3d.js` | Head anchor per frame. |
| **Sitting behind furniture**: at terminals the chair back is between the android and the camera; at the bench the desk is. The deck gets this from the depth buffer. | scene | Sprites render unoccluded, without holdouts. The floor must supply chairs and desks as separate occluder sprites drawn after a seated android. B1 baked the desk in as a holdout; that ties a sprite to one desk and does not scale to every seat. |

## Carried items

Items are fixed in the android's own frame, not attached to a hand, so they need no per-frame render. Positions are `[x, y, z, tilt]` in deck units.

| Item | Carried when | Stance | Sprite set must provide |
|---|---|---|---|
| **Box** (`#b98b52` parcel, 0.5 × 0.38 × 0.4) | `ship` (commit/push), from the start until it goes in the outbox 0.6 s after arrival | standing, walking | `item_box`, standing, 8 directions |
| **Book** (`#b4463c`, tilted −1.0; seated −1.1) | `read`, once off the shelf, to the armchair | standing, walking, seated | `item_book`, standing (8) and seated (0) |
| **Sheet** (`#f4f6fa` printout, tilted −1.1; seated −0.55) | `review`, at the bench, not while walking | standing, seated | `item_sheet`, standing (8) and seated (0, π) |

The deck also defines a seated box pose, but no station seats a shipper, so it is left out. Items are rendered with an Idle (standing) or SitIdle (seated) robot as holdout. The walk's arm swing can cross a standing item for a frame or two; that is accepted.

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
- **Headings** snap to 8 directions rather than easing through every angle.
- **Nods on other bases.** A seated nod or shake is rendered for the still, typing and reading bases only. Those are the only seated states that receive test verdicts or errors, since stalled and failed androids do not react.

## Budget

Frames per direction at 12 fps (Idle at 8 fps):

| Set | Clips | Frames | Directions | Total |
|---|---|---|---|---|
| standing | Walking 12, Idle 27, Wave 22, Yes 20, No 20, ThumbsUp 19, Death 12, StandUp 5, SitSlump 6 | 143 | 8 | 1144 |
| seated | SitDown 5, SitIdle 1, SitType 12, SitRead 1, 6 × nod/shake 18 | 127 | 2 | 254 |
| items | box 1, book 2, sheet 2 | 5 | 8 or 2 | 26 |

About 1,400 frames, each with up to 12 layers (`shadow`, `base`, `body`, 2 faces, 4 × 2 accessory). Layers are cropped to their own bounds and packed into WebP atlases per clip and density, so the runtime loads the 1× set first and 2×/4× on zoom.

## Manifest

The sprite set goes in `fleet/web/assets/world/robot/`, next to `robot.glb`. Its description is a `sprites` key added to the existing `manifest.json`; the glb keys stay as they are. The floor runtime (branch `renovate/floor`) reads it.

```jsonc
"sprites": {
  "version": 1,
  "camera": "camera_l2",                    // the floor camera the frames were rendered with
  "densities": { "1x": 171.528, "2x": 343.055, "4x": 686.11 },   // px per metre
  "directions": 8,                          // index d faces d * 45° (deck facing, 0 = towards the door)
  "layers": ["shadow", "base", "body", "face_band", "face_eyes",
             "acc_backpack", "acc_backpack_dark", "acc_antenna", "acc_antenna_dark",
             "acc_halo", "acc_halo_dark", "acc_crest", "acc_crest_dark"],
  "tint": ["body", "acc_backpack", "acc_antenna", "acc_halo", "acc_crest"],   // multiplied by the host colour
  "face": { "claude": "face_band", "codex": "face_eyes" },
  "kit_top": { "backpack": 0, "antenna": 0.33, "halo": 0.13, "crest": 0.02 },   // above the head top, in android heights / 1.7 (the deck's BK)
  "clips": {
    "Walking": {
      "fps": 12, "loop": true, "directions": [0, 1, 2, 3, 4, 5, 6, 7],
      "frames": 12,
      "atlas": { "1x": "walking@1x.webp", "2x": "walking@2x.webp", "4x": "walking@4x.webp" },
      // per density, per direction, per frame
      "cells": { "1x": [[ {
        "root_px": [83, 190],               // where the root point lands, in frame pixels
        "head_px": [83, 38],                // head top at the head bone, in frame pixels
        "size": [166, 201],
        "layers": { "base": [x, y, w, h, dx, dy], "body": [x, y, w, h, dx, dy] /* … */ }
        // atlas rect, then offset of the cropped rect within the frame
      } ]] }
    }
    // "Idle", "Wave", "Yes", "No", "ThumbsUp", "Death", "StandUp", "SitSlump",
    // "SitDown", "SitIdle", "SitType", "SitRead",
    // "SitNod", "SitShake", "SitTypeNod", "SitTypeShake", "SitReadNod", "SitReadShake"
  },
  "items": {
    "box":   { "stand": { "directions": [0, 1, 2, 3, 4, 5, 6, 7], "atlas": { /* … */ }, "cells": { /* as above, one frame */ } } },
    "book":  { "stand": { /* … */ }, "sit": { "directions": [0] } },
    "sheet": { "stand": { /* … */ }, "sit": { "directions": [0, 4] } }
  },
  "portrait": { "clip": "Idle", "frame": 4, "direction": 0, "density": "2x" }
}
```

Rules for the runtime:
- `loop: false` clips hold their last frame. Under reduced motion, loops show frame 0 and once-clips show their last frame.
- A clip is drawn only in the directions it lists. Seated clips list 0 and 4 (facing 0 and π). The runtime must never ask for another direction.
- Draw order: `shadow`, `base`, `body` (tinted), the agent's face layer (coloured), then `acc_<kind>` (tinted) and `acc_<kind>_dark` for the host's accessory, then the item.
- The tag anchor is `head_px` lifted by `kit_top` plus the margin. The dashed beams and arcs start from `head_px`.
