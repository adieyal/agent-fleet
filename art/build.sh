#!/usr/bin/env bash
# Rebuild every scene: fetch pinned sources, build, bake, export, then check output size.
#   art/build.sh                 all scenes
#   art/build.sh workbench       one scene
#   PREVIEW=1 art/build.sh       also render art/build/<scene>/preview.png
# BLENDER picks the Blender binary (default ~/.local/bin/blender); it must be 4.2 or newer.
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$ART")"
MAX_BYTES=$((15 * 1000 * 1000))
BLENDER="${BLENDER:-$HOME/.local/bin/blender}"

[[ -x "$BLENDER" ]] || { echo "build.sh: no Blender at $BLENDER; set BLENDER" >&2; exit 1; }
version="$("$BLENDER" --version 2>/dev/null | sed -n 's/^Blender \([0-9]*\.[0-9]*\).*/\1/p' | head -1)"
major="${version%%.*}" minor="${version#*.}"
if [[ -z "$version" ]] || (( major < 4 || (major == 4 && minor < 2) )); then
  echo "build.sh: $BLENDER is Blender ${version:-unknown}; 4.2 or newer is required" >&2
  exit 1
fi

python3 "$ART/scripts/fetch_assets.py"

if [[ $# -gt 0 ]]; then
  scenes=("$@")
else
  scenes=()
  for f in "$ART"/scripts/build_*.py; do s="$(basename "$f" .py)"; scenes+=("${s#build_}"); done
fi

run() {  # run a Blender step; full log in art/build/<scene>/<step>.log, progress and errors on screen
  local log="$ART/build/$2/${1%.py}.log" status=0
  mkdir -p "$(dirname "$log")"
  "$BLENDER" -b --factory-startup --python-exit-code 1 -P "$ART/scripts/$1" -- "${@:2}" >"$log" 2>&1 || status=$?
  grep -E '^(BUILD|bake|EXPORT|export|art|Traceback)|^  File|Error' "$log" || true
  return "$status"
}

for scene in "${scenes[@]}"; do
  echo "== $scene (Blender $version)"
  run "build_$scene.py" "$scene"
  run bake.py "$scene"
  run export.py "$scene"
  [[ "${PREVIEW:-0}" == 1 ]] && run preview.py "$scene"
  out="$REPO/fleet/web/assets/world/$scene"
  bytes="$(find "$out" -maxdepth 1 -type f -printf '%s\n' | awk '{s += $1} END {print s + 0}')"  # the scene, not its sprites/
  echo "$scene: $((bytes / 1000)) kB"
  if (( bytes > MAX_BYTES )); then
    echo "build.sh: $scene output is $((bytes / 1000000)) MB, over the 15 MB budget" >&2
    exit 1
  fi
done
