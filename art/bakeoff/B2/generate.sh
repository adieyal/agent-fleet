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
wait
