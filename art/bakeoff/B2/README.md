# Bake-off variant B2: AI-generated sprites

Pre-lit sprites generated with `art/scripts/gen_sprite.py` (ChatMock → `gpt-5.5` with the
`image_generation` tool, image model `gpt-image-2-codex`), using crops of the concept art as style
references. Rendering needs no lighting at runtime: draw the WebP images in order.

- `generate.sh` runs every generation that went into this folder, exactly as run (cached; set
  `FORCE=--force` to redo). The image model has no seed, so reruns give different pictures.
- `finish.py` trims the kept generations, scales them, writes the sprites, the tint masks,
  `sprites.json`, `contact.jpg` (every generation) and `tint.jpg` (tinting vs generating teal).
- `raw/*.json` are the sidecars for all 14 generations: prompt, reference crops with sha256,
  model, revised prompt, size, time. The raw PNGs (14 MB) are not committed; they are in the
  job outbox (`B2-raw/`).

## Sprites

Scaled to 1.5× their size in l2 (`l2_scale` 0.6667 in `sprites.json`), so l2 framing on a 1x
screen draws them at 2/3 size.

| Sprite | From | Size (px) | Bytes |
|---|---|---|---|
| `bench.webp` | `bench-v1` | 1395×789 | 167,806 |
| `plant.webp` | `plant-v1` | 224×360 | 27,888 |
| `robot-typing.webp` + `.mask.png` | `robot-typing-grey-v2` | 264×308 | 22,204 + 13,067 |
| `robot-pencil.webp` + `.mask.png` | `robot-pencil-grey-v2` | 214×308 | 21,888 + 13,480 |
| `robot-tube.webp` + `.mask.png` | `robot-tube-grey-v2` | 224×308 | 24,016 + 14,435 |
| `robot-typing-teal.webp` | `robot-typing-teal-v2` | 264×308 | 25,554 |
| `robot-pencil-teal.webp` | `robot-pencil-teal-v2` | 239×308 | 24,098 |
| `robot-tube-teal.webp` | `robot-tube-teal-v2` | 218×308 | 24,220 |

The bench, plant and three grey robots with masks add up to about 305 KB. Scales were set from
l2: the bench spans 930 px there, the plant 240 px tall, and a seated robot 205 px from head to
castors (matched by head size). Each generation took 38–64 s.

### Robots and the desk

Robots are drawn sitting in their own chair, with the laptop or paper floating at desk height
and no desk drawn. They sit on the far side of the bench, so to composite: draw the robot's part
below the desk-top line before the bench, and the rest (arms, laptop, paper) after it. That line
isn't recorded here yet; the comparison page sets it per pose.

### Tinting to host colours

`<pose>.mask.png` marks the grey shell (soft edges; chair, visor, laptop and flat white paper
excluded). The runtime recipe, as in `finish.py`'s `tinted()`, is to multiply the masked shell by
the host colour lifted by 1/0.8. See `tint.jpg`, where each row shows grey, grey tinted teal, and
teal generated directly.

- Tinting works: the shell takes the colour and keeps its shading and highlights. The small leaks
  are the paper's edge, the test tube's glass and the laptop logo, which a hand-touched mask
  would fix. It also tints the chest plate, which the generated teal robots leave grey.
- Generating per colour looks slightly richer but drifts. The teal typing robot came out smaller
  relative to its laptop and with longer legs, and each pose differs from its grey twin in arm
  and chair details. For every host colour you would need a new generation per pose, with new
  drift. **Tinting one grey set is the better approach.**

## Prompts

The shared style text is `STYLE` in `gen_sprite.py`: soft, slightly stylised 3D render of an
isometric office, three-quarter view about 30° down, warm soft key light from the upper left,
pale desaturated palette, the object alone on a fully transparent background. Descriptions, in
full in `generate.sh`:

- **Bench:** the long l2 workbench with no robots. A light-oak top on thin dark-grey metal legs,
  three grey drawer pedestals, and three black mesh-back chairs on the near side. It runs
  diagonally from the upper left down to the lower right, as in the reference.
- **Plant:** a leafy green plant with long pointed leaves in a square light-grey concrete
  planter, seen corner-on.
- **Robot:** one small robot in the style of the references: a rounded helmet head with a black
  visor, two glowing cyan eyes, ear discs, chunky limbs and a matte shell. It is coloured either
  *neutral light grey, like unpainted primer, with mid-grey joints* or *teal*. It sits on a black
  office chair behind a desk, facing the lower left, and the desk is not drawn. The three poses:
  typing on a floating laptop, writing with a yellow pencil on floating paper, and holding up a
  test tube.

## Variations tried

| Generation | References | Result |
|---|---|---|
| `bench-v1` ✔ | l2 bench | Desk slope 0.21, the same as l2's front edge; right materials; props like l2 |
| `bench-v2` | l2 bench | Empty desk top; same angle, but reads as unfinished next to l2 |
| `bench-v3` | l2 bench + l1 bench | Same design, slightly different crop (1536×1024); no gain from l1 |
| `plant-v1` ✔ | l2 plant | Tall planter as in l2 |
| `plant-v2` | l2 plant + l1 plant | Squatter planter, bushier plant |
| `robot-*-grey-v1` | pose crop + l2 trio | Typing and pencil good; the tube pose came out as a close-up (head and torso only) at a different scale |
| `robot-*-grey-v2` ✔ | pose crop + l1 robots | All three full-body in the same framing; kept as a set |
| `robot-*-teal-v2` | pose crop + l1 robots | For the tint comparison; see above |

Upstream ignores the requested size and quality: images come back at about 1.2–1.7 MP in the
model's own aspect ratio, always at "medium" quality.

## Consistency

- **Angle:** the bench matched l2's angle on every try. The robots are all seen from the same
  side and face lower left, like l2's. They look slightly more from the front than l2's robots.
- **Scale:** each generation fills its frame, so scale comes only from `finish.py`'s per-object
  l2 measure. Proportions within a set drift (for example head against body, or robot against
  laptop), and that can't be fixed by scaling.
- **Style:** the reference crops keep the shell, visor and eyes on-model. Without a crop, the
  first probe plant was photo-real.
