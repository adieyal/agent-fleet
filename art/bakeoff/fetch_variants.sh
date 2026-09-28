#!/usr/bin/env bash
# Variants A and B1 are built on branch renovate/art (by Blender on another host). The comparison page
# reads them from this checkout: copy them in from that branch, unstaged, so this branch doesn't carry
# a second copy. Usage: art/bakeoff/fetch_variants.sh [remote] (default: home)
set -euo pipefail
cd "$(dirname "$0")/../.."
remote=${1:-home}
git fetch -q "$remote" renovate/art
for v in A B1; do
  if git ls-tree -d "$remote/renovate/art" "art/bakeoff/$v" | grep -q .; then
    rm -rf "art/bakeoff/$v"
    git archive "$remote/renovate/art" "art/bakeoff/$v" | tar -x
    echo "art/bakeoff/$v from $(git rev-parse --short "$remote/renovate/art")"
  else
    echo "art/bakeoff/$v: not on $remote/renovate/art yet" >&2
  fi
done
