#!/usr/bin/env bash
# Rebuild the bake-off variants A and B1 and their layout screenshots.
#   art/bakeoff/build.sh          both
#   art/bakeoff/build.sh A        one variant (A or B1)
# BLENDER picks the Blender binary (default ~/.local/bin/blender; 4.2 or newer). Needs the robot built first
# (art/build.sh robot) and the fetched sources.
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(dirname "$ART")"
BLENDER="${BLENDER:-$HOME/.local/bin/blender}"
variants=("${@:-A B1}")
[[ $# -eq 0 ]] && variants=(A B1)

python3 "$ART/scripts/fetch_assets.py"
[[ -f "$ART/build/robot/robot.blend" ]] || "$ART/build.sh" robot

for v in "${variants[@]}"; do
  echo "== bake-off $v"
  log="$ART/build/bakeoff/$v.log"
  mkdir -p "$(dirname "$log")"
  "$BLENDER" -b --factory-startup --python-exit-code 1 -P "$ART/scripts/bakeoff.py" -- "$v" >"$log" 2>&1 \
    || { grep -E 'Error|Traceback|File "' "$log" >&2; exit 1; }
  grep BAKEOFF "$log"
  [[ "$v" == B1 ]] && python3 "$ART/scripts/png_optimize.py"
done
cd "$REPO" && ~/.local/bin/uv run --group dev python art/scripts/shoot_bakeoff.py art/build/bakeoff "${variants[@]}"
du -sh "$ART"/bakeoff/A "$ART"/bakeoff/B1
