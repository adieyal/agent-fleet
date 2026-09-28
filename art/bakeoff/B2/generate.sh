#!/usr/bin/env bash
# Variant B2: every generation behind art/bakeoff/B2, as run. Outputs are cached by
# gen_sprite.py, so rerunning only fills in what is missing (pass --force through $FORCE to redo).
# Needs ChatMock serving on 127.0.0.1:8010. Runs four generations at a time.
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=art/bakeoff/B2/raw
L2=docs/images/concept/l2.png
L1=docs/images/concept/l1.png

# Reference crops (x,y,w,h in the 1672x941 concept images).
L2_BENCH="$L2@370,360,950,540"   # the whole l2 bench with its three robots
L2_TRIO="$L2@530,370,660,300"    # the three robots, close
L2_TEAL="$L2@530,390,190,190"    # teal robot at its laptop
L2_BLUE="$L2@760,370,200,220"    # blue robot writing with a pencil
L2_GREEN="$L2@990,430,200,240"   # green robot holding a test tube
L2_PLANT="$L2@1500,460,140,270"  # plant in a square concrete planter
L1_BENCH="$L1@640,340,420,200"   # the active bench on the l1 floor
L1_PLANT="$L1@70,390,80,110"
L1_ROBOTS="$L1@660,350,370,130"

gen() {  # gen NAME "description" ref...
  local name=$1 desc=$2; shift 2
  local refs=(); for r in "$@"; do refs+=(--ref "$r"); done
  uv run -q python art/scripts/gen_sprite.py "$desc" -o "$OUT/$name.png" "${refs[@]}" ${FORCE:-} &
  while [ "$(jobs -rp | wc -l)" -ge 4 ]; do wait -n || true; done
}

BENCH='the long office workbench from the reference, with no robots: one long light-oak desk top \
(about four metres long, seating six) on thin dark-grey metal legs, three grey metal drawer pedestals \
under it, and three black office chairs with mesh backs on the near side, pulled up to the desk. The \
desk runs diagonally from the upper left of the image down to the lower right, exactly as in the \
reference, seen from the same side.'
gen bench-v1 "$BENCH A few small desk items as in the reference: two black desk lamps, pencil cups, \
loose papers. No computers, no chairs on the far side." "$L2_BENCH"
gen bench-v2 "$BENCH The desk top is completely empty. No chairs on the far side." "$L2_BENCH"
gen bench-v3 "$BENCH A few small desk items as in the references: two black desk lamps, pencil cups, \
loose papers. No computers, no chairs on the far side." "$L2_BENCH" "$L1_BENCH"

PLANT='a potted plant: a leafy green plant with long pointed leaves in a square light-grey concrete \
planter, the planter seen corner-on as in the reference'
gen plant-v1 "$PLANT." "$L2_PLANT"
gen plant-v2 "$PLANT." "$L2_PLANT" "$L1_PLANT"

robot() {  # robot COLOUR POSE
  echo "one small friendly robot exactly in the style of the reference robots: a rounded helmet-like \
head with a glossy black face visor and two glowing cyan eyes, round ear discs, chunky rounded limbs, \
matte plastic shell. Its whole body is $1. It sits on a black office chair behind a desk, facing \
toward the lower left of the image like the robots in the reference. Do not draw the desk. $2"
}
GREY='a neutral light grey, almost white like unpainted primer, with mid-grey joints, so it can be tinted'
TEAL='teal (like the teal robot in the reference), with grey joints'
TYPING='It types on an open dark-grey laptop that sits in front of it at desk height, both hands on the \
keyboard; the laptop floats where the desk top would be.'
PENCIL='It leans forward, writing with a yellow pencil on a sheet of paper that lies in front of it at \
desk height; the paper floats where the desk top would be.'
TUBE='It holds up a glass test tube of dark liquid in its raised hand and looks at it; the other hand \
rests at desk height.'

gen robot-typing-grey-v1 "$(robot "$GREY" "$TYPING")" "$L2_TEAL" "$L2_TRIO"
gen robot-pencil-grey-v1 "$(robot "$GREY" "$PENCIL")" "$L2_BLUE" "$L2_TRIO"
gen robot-tube-grey-v1 "$(robot "$GREY" "$TUBE")" "$L2_GREEN" "$L2_TRIO"
gen robot-typing-grey-v2 "$(robot "$GREY" "$TYPING")" "$L2_TEAL" "$L1_ROBOTS"
gen robot-pencil-grey-v2 "$(robot "$GREY" "$PENCIL")" "$L2_BLUE" "$L1_ROBOTS"
gen robot-tube-grey-v2 "$(robot "$GREY" "$TUBE")" "$L2_GREEN" "$L1_ROBOTS"

# Round 2: the v2 recipe (most consistent full-body framing) in teal, to judge tint vs generate.
gen robot-typing-teal-v2 "$(robot "$TEAL" "$TYPING")" "$L2_TEAL" "$L1_ROBOTS"
gen robot-pencil-teal-v2 "$(robot "$TEAL" "$PENCIL")" "$L2_BLUE" "$L1_ROBOTS"
gen robot-tube-teal-v2 "$(robot "$TEAL" "$TUBE")" "$L2_GREEN" "$L1_ROBOTS"

# Round 3, for the comparison page: the lantern, animation, and architecture.
L2_LANTERN="$L2@450,0,130,210"
L2_FLOOR="$L2@60,640,360,260"
L2_WALL="$L2@700,40,640,200"
L2_PILASTER="$L2@1320,130,210,600"
B2=art/bakeoff/B2
gen lantern-v1 'a hanging attention lantern: a diamond (octahedron) of glowing magenta glass in a thin \
dark metal frame, hanging from a thin cable that runs straight up out of the image, exactly like the \
lantern in the reference' "$L2_LANTERN"
SHEET='An animation sprite sheet: exactly four frames side by side in one row of four equal cells, no \
gaps, no borders, no numbers. Every frame shows exactly the robot in the attached sprite, same size, same \
position in its cell, same chair, same camera angle and light; only its'
gen anim-typing-v1 "$SHEET hands and fingers move, as a looping typing cycle on the laptop keyboard." \
  "$B2/robot-typing.webp"
gen anim-pencil-v1 "$SHEET writing hand and pencil move, as a looping writing cycle, and its head nods \
slightly." "$B2/robot-pencil.webp"
FLAT='Style: a flat texture seen straight on, with no perspective and no objects. Soft, even, slightly warm \
light; pale, desaturated colours matching the attached reference crop. It must tile seamlessly: the left \
edge continues into the right edge and the top into the bottom. Fill the whole square image edge to edge.'
uv run -q python art/scripts/gen_sprite.py 'a seamless floor texture seen from straight above: exactly \
two by two large square pale grey polished stone floor tiles with thin light grout lines, subtle marbling' \
  -o $OUT/floor-v1.png --ref "$L2_FLOOR" --style "$FLAT" ${FORCE:-} &
uv run -q python art/scripts/gen_sprite.py 'a seamless wall texture seen straight on: a pale warm grey \
painted plaster wall, smooth, very faint texture, no joints, no skirting' \
  -o $OUT/wall-v1.png --ref "$L2_WALL" --style "$FLAT" ${FORCE:-} &
gen pilaster-v1 'one tall square wall pilaster (a pale grey rectangular column that projects from a wall \
face) running from the floor to a top cap, with the wall face to either side cut off at the column; \
seen at the same angle as the reference' "$L2_PILASTER"
wait

# Round 3b: v1 textures came back vignetted (transparent background forced), so opaque; the v1
# pilaster had the wrong shape.
uv run -q python art/scripts/gen_sprite.py 'a seamless floor texture seen from straight above: exactly \
two by two large square pale grey polished stone floor tiles with thin light grout lines, subtle marbling. \
Perfectly even brightness everywhere, no vignette, no darker corners.' \
  -o $OUT/floor-v2.png --ref "$L2_FLOOR" --style "$FLAT" --background opaque ${FORCE:-} &
uv run -q python art/scripts/gen_sprite.py 'a seamless wall texture seen straight on: a pale warm grey \
painted plaster wall, smooth, very faint texture, no joints, no skirting. Perfectly even brightness \
everywhere, no vignette.' -o $OUT/wall-v2.png --ref "$L2_WALL" --style "$FLAT" --background opaque ${FORCE:-} &
gen pilaster-v2 'a single rectangular pilaster: one plain box, 40 cm wide, 30 cm deep and 3 m tall, pale \
warm grey, standing upright; its left side face and its front face are visible, the front face lit, the \
side face in soft shade; a slightly wider box-shaped cap on top. Nothing else: no wall, no second column.' \
  "$L2_PILASTER"
wait
