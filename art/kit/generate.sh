#!/usr/bin/env bash
# The floor kit's AI props: every generation behind art/kit, as run. Outputs are cached by
# gen_sprite.py, so rerunning only fills in what is missing (pass --force through $FORCE to redo).
# Each generation is appended to art/kit/generations.log. Needs ChatMock serving on 127.0.0.1:8010.
# Runs four generations at a time. finish.py turns the kept ones into sprites.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=art/kit/raw
LOG=art/kit/generations.log
L2=docs/images/concept/l2.png
L1=docs/images/concept/l1.png

# Reference crops (x,y,w,h in the 1672x941 concept images).
L2_BENCH="$L2@370,360,950,540"     # the l2 bench
L2_CHAIRS="$L2@460,560,700,300"    # its near-side chairs, from behind
L2_LAMP="$L2@430,410,120,140"      # the teal robot's desk lamp
L2_DESKTOP="$L2@560,480,720,300"   # desk-top clutter: pen pots, papers, laptop, tray
L2_PLANT="$L2@1500,460,140,270"    # plant in a square concrete planter
L2_SHELF="$L2@1510,330,162,420"    # shelving with binders
L2_QDESK="$L2@400,190,260,210"     # the question desk with its tent card
L2_LANTERN="$L2@450,0,130,210"
L1_TERMINALS="$L1@255,280,380,170" # a bench of desks with monitors
L1_PLANTS="$L1@830,230,120,110"
L1_SHELF="$L1@530,150,115,130"
L1_CART="$L1@640,205,140,105"      # the librarian at the book cart
L1_PODIUM="$L1@915,240,100,130"    # the orchestrator at the podium
L1_BOARD="$L1@1125,260,125,130"    # the briefing whiteboard on its stand
L1_CRATE="$L1@285,165,100,105"     # the waiting crate with its hourglass

gen() {  # gen NAME "description" ref...
  local name=$1 desc=$2; shift 2
  [[ -f "$OUT/$name.png" && -z "${FORCE:-}" ]] && return 0   # cached: no request, nothing to log
  local refs=(); for r in "$@"; do refs+=(--ref "$r"); done
  (
    start=$(date +%s)
    if uv run -q python art/scripts/gen_sprite.py "$desc" -o "$OUT/$name.png" "${refs[@]}" ${FORCE:-}; then status=ok; else status=failed; fi
    printf '%s\t%s\t%s\t%ss\n' "$(date -Is)" "$name" "$status" "$(( $(date +%s) - start ))" >> "$LOG"
  ) &
  while [ "$(jobs -rp | wc -l)" -ge 4 ]; do wait -n || true; done
}

SHEET='Draw them as separate objects side by side in one row, well apart with clear transparent space \
between them, all at the same scale and from the same camera angle as the references, none touching the \
image edge.'

gen bench 'the long office workbench from the reference: one long light-oak desk top made of three desks \
end to end (about five and a half metres long, eighty centimetres deep) on thin dark-grey metal legs, with \
three grey metal drawer pedestals under it. The desk runs diagonally from the upper left of the image down \
to the lower right, exactly as in the reference, seen from the same side. The desk top is completely \
empty: no lamps, no computers, no papers, no plants. No chairs at all.' "$L2_BENCH"

gen chairs "two identical black office task chairs with mesh backs, armrests and five-star bases on \
castors, like the chairs in the reference. The left one is seen from behind, its backrest towards the \
viewer, facing the upper right as if pulled up to the near side of a desk. The right one faces the viewer \
and the lower left, as if on the far side of a desk. $SHEET" "$L2_CHAIRS"

gen lamp 'one black adjustable desk lamp, exactly like the lamps in the reference: a round weighted base, \
a thin two-part arm and a dome shade angled down towards the lower left. The lamp is switched off: no \
light, no glow.' "$L2_LAMP"

gen desk-props "eight small desk items like those on the reference desk: an open dark-grey laptop, a grey \
pen pot full of pencils and pens, a neat stack of white paper, a single sheet with a pencil sketch of boxes, \
a white coffee mug, a small succulent in a grey cube pot, two stacked closed books, and a two-tier black \
paper tray with papers. Each lies as it would on a desk top. $SHEET Two rows of four are fine." "$L2_DESKTOP"

gen terminal-desk 'one office desk with a computer: a light-oak desk top (1.6 m wide, 0.8 m deep) on thin \
dark-grey metal legs, a grey drawer pedestal under it, a black flat monitor on a stand near the back edge, \
a black keyboard and mouse in front of it. The desk runs from the upper left to the lower right as in the \
reference. No chair, no lamp, no other items.' "$L1_TERMINALS"

gen plants "three potted plants like the ones in the references: a tall leafy plant with long pointed \
leaves in a square light-grey concrete planter; a bushy plant with broad leaves in a round pale grey pot; \
and a small plant in a small grey cube pot for a desk. $SHEET" "$L2_PLANT" "$L1_PLANTS"

gen shelf 'a freestanding grey metal shelving unit, about 1.2 m wide, 0.4 m deep and 1.8 m tall, standing \
against a wall, with four shelves holding upright ring binders with muted coloured spines (grey, dusty \
blue, sand, sage), a few stacked books and one cardboard file box. Its long side faces the viewer, running \
from the upper left to the lower right like the wall in the references.' "$L2_SHELF" "$L1_SHELF"

gen book-cart 'a librarian’s wheeled book cart: a small dark-grey metal trolley with two slanted shelves \
full of books with muted coloured spines, on four small castors, like the cart in the reference. No robot, \
no person.' "$L1_CART"

gen librarian-desk 'a small library reference desk: a light-oak desk top on a grey cabinet body with a \
bank of small card-catalogue drawers on the front, two stacks of books and an open ledger on top. The desk \
runs from the upper left to the lower right. No lamp, no chair, no person.' "$L1_SHELF" "$L1_CART"

gen podium 'the orchestrator’s podium from the reference: a grey standing lectern desk, a box-shaped \
pedestal with a slightly overhanging light-oak top, holding a clipboard with a checklist. Its front faces \
the viewer and the lower left. No robot, no lamp.' "$L1_PODIUM"

gen whiteboard 'a mobile briefing whiteboard like the one in the reference: a white board in a thin grey \
aluminium frame on a two-legged stand with castors, with a few simple drawn marks (two circles, short \
lines like bullet points) and three small sticky notes. The board faces the viewer and the lower left. \
No text.' "$L1_BOARD"

gen question-desk 'the small question desk from the reference: a light-oak desk top on a grey metal \
cabinet body with a drawer, and on the desk top a white folded paper tent card showing a large dark \
question mark. No plant, no lamp, no other items. It runs from the upper left to the lower right as in \
the reference.' "$L2_QDESK"

gen crate 'a closed wooden shipping crate, about 70 cm on each side, made of pale pine planks with a \
diagonal brace on each face, like the crate in the reference, with a dark hourglass symbol stencilled on \
its front face.' "$L1_CRATE"

gen lantern 'a hanging attention lantern: an elongated faceted diamond (octahedron) of glowing magenta \
glass in a thin dark metal frame, hanging from a thin cable that runs straight up out of the image, like \
the lantern in the reference. The front facet facing the viewer is flat and plain, with nothing on it, \
so a symbol can be drawn there later.' "$L2_LANTERN"
wait

# Round 2: the bench came back with warm light pools on its desk tops (warmth must be a separate sprite);
# the shelf showed its left end, which this camera never sees.
gen bench-v2 'the long office workbench from the reference: one long light-oak desk top made of three desks \
end to end (about five and a half metres long, eighty centimetres deep) on thin dark-grey metal legs, with \
three grey metal drawer pedestals under it. The desk runs diagonally from the upper left of the image down \
to the lower right, exactly as in the reference, seen from the same side. The desk top is completely \
empty: no lamps, no computers, no papers, no plants. No chairs at all. Light it evenly with soft cool \
daylight only: no warm light pools, spots or bright patches on the desk top.' "$L2_BENCH"

gen shelf-v2 'a freestanding grey metal shelving unit, about 1.2 m wide, 0.4 m deep and 1.8 m tall, with \
four shelves holding upright ring binders with muted coloured spines (grey, dusty blue, sand, sage), a few \
stacked books and one cardboard file box. Its open front faces the lower left of the image, and the only \
end panel you can see is its right-hand end, facing the lower right; its left end is hidden. The same \
angle as the desks in the reference: the long front runs from the upper left down to the lower right.' \
  "$L2_SHELF" "$L2_BENCH"
wait

# Round 3 (reviewer, floor round 4): l1's long benches carry a monitor at most desks.
gen monitor 'one black flat computer monitor on a slim stand, with a black keyboard and a mouse in front of it, \
as they stand on the desks in the reference, seen from the same angle: the screen faces the lower left of the \
image, the keyboard in front of it. The screen is dark. No desk, no other items.' "$L1_TERMINALS"
wait

# Round 4 (reviewer, floor round 7): benches of any length from matching pieces, all from one generation so they
# match exactly; finish.py cuts them apart and trims each to one seat module along the desk's depth.
gen bench-pieces "three separate pieces of one modular office workbench, drawn side by side in one row with clear \
transparent space between them, all at the same scale and from the same camera angle as the reference bench, which \
they must match exactly in materials and proportions: one continuous light-oak desk top, thin dark-grey metal legs, \
and a grey metal drawer pedestal under each seat. The pieces, left to right: (1) the LEFT END section: one seat's \
length of desk top with the dark legs at its left end and a drawer pedestal under it, its right end cut straight \
across; (2) a MIDDLE section: one seat's length of desk top with a drawer pedestal under it, both ends cut straight \
across, no legs, made to repeat; (3) the RIGHT END section: one seat's length of desk top with a drawer pedestal \
under it and the dark legs at its right end, its left end cut straight across. All three are exactly the same length \
and depth, the desk tops at the same height, so they butt together into one long bench running from the upper left \
to the lower right as in the reference. Empty desk tops: no lamps, no computers, no papers. No chairs." \
  "$L2_BENCH" "art/bakeoff/B2/bench.webp"
wait

# Round 5 (floor review 1, point 6): a storage corner needs stacked crates. Plain crates (furniture, like the
# shelves); the hourglass crate stays the one that means waiting.
gen crate-stack 'a low wooden pallet with plain pale-pine shipping crates stacked on it, like the crate in the \
reference but without any symbol: two crates side by side on the pallet and a third crate on top of them, all with \
diagonal braces on their faces. Seen from the same angle as the reference.' "$L1_CRATE" "art/kit/raw/crate.png"
wait
