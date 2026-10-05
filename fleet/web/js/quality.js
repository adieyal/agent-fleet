// Quality: high draws the deck's overlays translucent over a backdrop blur and refreshes shadows every frame; low draws
// them opaque and refreshes shadows every few frames, which integrated GPUs need (re-blurring the live deck behind each
// overlay held one to ~18 fps). Auto starts high and settles on low, for this browser, the first time the deck runs
// late; choosing auto again gives high another try. The choice is remembered per browser.
import { store } from './util.js';

const MODES = ['auto', 'high', 'low'];
const saved = store('localStorage', 'fleet.quality');
let mode = MODES.includes(saved) ? saved : 'auto';
export let quality = 'high';   // what the deck draws now: 'high' or 'low'

const button = document.createElement('button');
button.id = 'qualityToggle';
document.getElementById('live').before(button);

// a gauge, its needle low or high
const icon = level => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><path d="M2.3 12a6 6 0 1 1 11.4 0"/><path d="${level === 'high' ? 'M8 10.5 11.2 6.2' : 'M8 10.5 4.8 6.2'}"/></svg>`;

function apply() {
  quality = mode === 'auto' ? (store('localStorage', 'fleet.quality.auto') === 'low' ? 'low' : 'high') : mode;
  document.documentElement.dataset.quality = quality;
  const next = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
  const label = `Quality: ${mode}${mode === 'auto' ? ` (${quality} now${quality === 'high' ? ', low if the deck runs slow' : ': the deck ran slow here'})` : ''} — click for ${next}`;
  button.innerHTML = `${icon(quality)}<span>${mode}</span>`;
  button.title = label;
  button.setAttribute('aria-label', label);
}

button.addEventListener('click', () => {
  mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
  store('localStorage', 'fleet.quality', mode);
  if (mode === 'auto') store('localStorage', 'fleet.quality.auto', 'high');
  apply();
});

// The frame loop calls this while frames run late: on auto, the first time drops to low. True when it did.
export function lowerQuality() {
  if (mode !== 'auto' || quality === 'low') return false;
  store('localStorage', 'fleet.quality.auto', 'low');
  apply();
  return true;
}

apply();
