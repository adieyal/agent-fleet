// The animation clock follows real time until a browser test steps it.
let manual = null;
export const animationNow = () => manual === null ? performance.now() : manual;
export const isStepping = () => manual !== null;
export function advanceClock(seconds) {
  if (manual === null) manual = performance.now();
  manual += seconds * 1000;
}
