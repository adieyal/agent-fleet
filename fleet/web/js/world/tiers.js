// Level of detail: each sprite comes at several densities (tiers, pixels per metre, ascending). The renderer wants
// the smallest tier at least as dense as the screen, loads it on demand, and draws the best tier it already has
// until then. Pure functions, so they can be tested alone.

const KEEP = 0.85;   // a finer tier is kept until the screen needs less than this share of the next tier down

// index of the tier to use at `need` screen pixels per metre, given the one in use (or -1)
export function pickTier(tiers, need, current = -1) {
  let want = tiers.findIndex(t => t.ppm >= need);
  if (want < 0) want = tiers.length - 1;
  // going finer is immediate (anything else is blurry); going coarser waits for a margin, so a zoom resting near a
  // boundary doesn't swap back and forth
  if (current > want && need > KEEP * tiers[current - 1].ppm) return current;
  return want;
}

// the tier to draw while `want` may still be loading: it if loaded, else the nearest finer loaded one, else the
// finest loaded coarser one; -1 when nothing is loaded
export function drawableTier(tiers, want, loaded) {
  if (loaded.has(want)) return want;
  for (let i = want + 1; i < tiers.length; i++) if (loaded.has(i)) return i;
  for (let i = want - 1; i >= 0; i--) if (loaded.has(i)) return i;
  return -1;
}

// crossfade from one tier to the next: the old tier stays fully drawn beneath while the new one fades in on top, so
// the sprite is never partly transparent
export const FADE = 0.25;   // seconds
export function fadeAlpha(since, now) {
  return Math.max(0, Math.min(1, (now - since) / FADE));
}
