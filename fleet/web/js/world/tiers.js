// Level of detail: each sprite comes at several densities (tiers, pixels per metre, ascending). The renderer wants
// the smallest tier at least as dense as the screen (scaled down, never up), loads it ahead of the zoom, and draws
// the best tier it already has until then. Pure functions, so they can be tested alone.

const KEEP = 0.85;   // a finer tier is kept until the screen needs less than this share of the next tier down
// an on-demand tier (`lazy`) is wanted only past this much upscaling of the tier below: the robots' 4x set decodes
// to ~940 MB, and l2 at pixel ratio 2 needs 4.9% past their 2x, a softening no one sees
const LAZY = 1.05;

// index of the tier to use at `need` screen pixels per metre, given the one in use (or -1)
export function pickTier(tiers, need, current = -1) {
  let want = tiers.findIndex(t => t.ppm >= need);
  if (want < 0) want = tiers.length - 1;
  if (tiers[want].lazy && want > 0 && need <= LAZY * tiers[want - 1].ppm) want--;
  // going finer is immediate (anything else is blurry); going coarser waits for a margin, so a zoom resting near a
  // boundary doesn't swap back and forth
  if (current > want && need > KEEP * tiers[current - 1].ppm) return current;
  return want;
}

// the tier to load ahead of use: the next finer one, unless it is on demand (only a zoom heading there loads that)
export function nextTier(tiers, want) {
  const i = want + 1;
  return i < tiers.length && !tiers[i].lazy ? i : -1;
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
export const FADE = 0.12;   // seconds
export function fadeAlpha(since, now) {
  return Math.max(0, Math.min(1, (now - since) / FADE));
}
