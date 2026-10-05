// Room radio: each floor plays its own SomaFM channel while you are in it. Off by default; the choice and the
// volume are remembered per browser. The deck overview plays nothing.
import { esc, store } from './util.js';

const STATIONS = [
  ['groovesalad', 'Groove Salad'], ['dronezone', 'Drone Zone'], ['deepspaceone', 'Deep Space One'],
  ['lush', 'Lush'], ['secretagent', 'Secret Agent'], ['spacestation', 'Space Station Soma'],
  ['beatblender', 'Beat Blender'], ['sonicuniverse', 'Sonic Universe'], ['missioncontrol', 'Mission Control'],
  ['thetrip', 'The Trip'], ['fluid', 'Fluid'], ['illstreet', 'Illinois Street Lounge'],
  ['bootliquor', 'Boot Liquor'], ['suburbsofgoa', 'Suburbs of Goa'], ['defcon', 'DEF CON Radio'],
];
const streamUrl = id => `https://ice2.somafm.com/${id}-128-mp3`;

const SPEAKER = '<path d="M3 6h2.5L9 3v10L5.5 10H3z"/>';
const WAVES = '<path d="M11.2 5.6a3.4 3.4 0 0 1 0 4.8M13 3.8a6 6 0 0 1 0 8.4"/>';
const MUTED = '<path d="m11 6 4 4m0-4-4 4"/>';
const icon = on => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPEAKER}${on ? WAVES : MUTED}</svg>`;

let on = store('localStorage', 'fleet.radio.on') === '1';
let volume = Number(store('localStorage', 'fleet.radio.volume') ?? 0.5);
if (!(volume >= 0 && volume <= 1)) volume = 0.5;
let station = null;    // [id, name] for the room you are in, or null outside a room
let audio = null;

const control = document.createElement('div');
control.className = 'radio';
document.getElementById('libraryOpen')?.after(control);

// Floor n plays station n, so neighbouring rooms never share one.
function stationFor(doc, projectId) {
  if (!projectId) return null;
  const floor = doc?.building?.floors?.[projectId];
  const index = Number.isInteger(floor) ? floor - 1 : [...projectId].reduce((sum, c) => sum + c.charCodeAt(0), 0);
  return STATIONS[index % STATIONS.length];
}

function play() {
  if (!on || !station) { audio?.pause(); return; }
  const url = streamUrl(station[0]);
  if (!audio) audio = new Audio();
  audio.volume = volume;
  if (audio.dataset.station !== station[0]) { audio.src = url; audio.dataset.station = station[0]; }
  audio.play().catch(error => {
    if (error.name === 'AbortError') return;   // a newer station replaced this one before it started
    // Blocked until a click (NotAllowedError), or the stream failed: show it off rather than pretend it plays.
    on = false;
    render();
    control.querySelector('#radioToggle').title = error.name === 'NotAllowedError'
      ? 'Turn on SomaFM radio (somafm.com); the browser needs a click before it plays sound'
      : `Try SomaFM radio again (somafm.com); ${station[1]} could not be played (${error.message})`;
  });
}

function render() {
  const label = !on ? `Turn on room radio: stream external audio from SomaFM (somafm.com)${station ? ` · ${station[1]}` : ' when you enter a floor'}` : `Turn off room radio${station ? ` · SomaFM ${station[1]}` : '; enter a floor to hear its SomaFM station (somafm.com)'}`;
  control.innerHTML = `<button id="radioToggle" aria-pressed="${on}" title="${esc(label)}" aria-label="${esc(label)}">${icon(on)}</button>${
    on ? `<span class="radio-station">${station ? esc(station[1]) : 'no room'}</span><input type="range" id="radioVolume" min="0" max="1" step="0.05" value="${volume}" aria-label="Radio volume">` : ''}`;
}

control.addEventListener('click', ev => {
  if (!ev.target.closest('#radioToggle')) return;
  on = !on;
  store('localStorage', 'fleet.radio.on', on ? '1' : '0');
  render();
  play();
});
control.addEventListener('input', ev => {
  if (ev.target.id !== 'radioVolume') return;
  volume = Number(ev.target.value);
  store('localStorage', 'fleet.radio.volume', String(volume));
  if (audio) audio.volume = volume;
});

// Called with every state document and the floor entered (a project ID, or null for the whole deck).
export function applyRadio(doc, entered) {
  const next = stationFor(doc, entered);
  if (next?.[0] === station?.[0]) return;
  station = next;
  render();
  play();
}

render();
