#!/usr/bin/env bash
# Motion test step 2. pass1: 10-frame sheets of Mixamo + all three HY takes per clip (to pick the best take).
# pass2 <clip> <hy take>...: final renders (sheet + every frame for the webm) of Mixamo + the chosen take.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
BL=~/.local/bin/blender
M=~/.local/state/fleet/renovation/mixamo
H=~/.local/state/fleet/renovation/hymotion/HY-Motion-1.0
OUT=~/.cache/fleet-motion-test/renders

declare -A SETTING=([a]=desk [b]=chair [c]=chair [d]=floor [e]=desk [f]=desk [g]=desk [h]=desk [i]=floor)
declare -A MIX=([a]="mixamo=$M/typing.fbx" [b]="mixamo=$M/sit-to-stand.fbx" [c]="mixamo=$M/stand-to-sit.fbx"
                [d]="mixamo_normal=$M/walk-normal.fbx mixamo_walk=$M/walk.fbx")
declare -A HY=([a]=a_typing [b]=b_stand_up [c]=c_sit_down [d]=d_walk [e]=e_nod_yes [f]=f_shake_no
               [g]=g_slump [h]=h_read_book [i]=i_thumbs_up)

render() {  # render <dir> <clip> <flags> <sources...>
  local dir=$1 clip=$2 flags=$3; shift 3
  local pin=""; [[ $clip == d ]] && pin=--pin
  $BL -b --factory-startup --python "$REPO/art/motion-test/render_motion.py" -- \
    clip "$dir" "${SETTING[$clip]}" $pin $flags "$@" > "$dir.log" 2>&1
  grep -q '^DONE' "$dir.log" || { echo "FAILED $dir"; tail -5 "$dir.log"; return 1; }
  echo "ok $dir"
}

mkdir -p "$OUT"
case $1 in
  pass1)
    for c in a b c d e f g h i; do
      src=(${MIX[$c]:-})
      for t in 000 001 002; do src+=("hy$t=$H/${HY[$c]}_$t.fbx"); done
      render "$OUT/pass1_$c" "$c" --sheet=10 "${src[@]}"
    done ;;
  pass2)
    c=$2; shift 2
    src=(${MIX[$c]:-})
    for t in "$@"; do src+=("hy$t=$H/${HY[$c]}_$t.fbx"); done
    render "$OUT/final_$c" "$c" "--sheet=10 --all" "${src[@]}" ;;
  metrics)  # numbers only (no renders) for Mixamo + every HY take; collected into step2_metrics.json
    for c in a b c d e f g h i; do
      src=(${MIX[$c]:-})
      for t in 000 001 002; do src+=("hy$t=$H/${HY[$c]}_$t.fbx"); done
      render "$OUT/metrics_$c" "$c" --sheet=1 "${src[@]}"
    done
    python3 - "$OUT" "$REPO/art/motion-test/step2_metrics.json" <<'EOF'
import json, sys
from pathlib import Path
out = {c: json.loads((Path(sys.argv[1]) / f'metrics_{c}' / 'info.json').read_text()) for c in 'abcdefghi'}
Path(sys.argv[2]).write_text(json.dumps(out, indent=1) + '\n')
EOF
    ;;
esac
