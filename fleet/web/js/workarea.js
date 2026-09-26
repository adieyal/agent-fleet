// The workarea view (L3): a room's benches as workarea-model.js derives them, drawn flat over the deck. Deliberately
// minimal: the entrance, the question desk with its lantern hanging over it, a bench per job (plan wall, criteria
// lights, the agent's action glyph and a report tray) and footprints from the entrance to benches used in the last
// hour. Every place carries a data attribute (data-entrance, data-desk, data-bench, data-tile, data-tray) so new art
// can re-skin it without changing the model.
//
// Opened from a job's panel ("Workarea") or with ?workarea=<room>; Esc or ✕ closes it. The tray opens the job's
// latest step report in the document reader; each paper in it opens its own.

import { QS } from './env.js';
import { esc } from './util.js';
import { hostLook } from './looks.js';
import { actionOf, glyphHtml } from './glyphs.js';
import { ents } from './model.js';
import { openReader } from './reader.js';
import { workareaOf } from './workarea-model.js';

const el = document.getElementById('workarea');
let room = null, doc = null, jobs = new Map();   // jobs: bench key → the job, for the reader

export function openWorkarea(label) {
  room = label;
  el.hidden = false;
  render();
}
export function closeWorkarea() {
  room = null;
  el.hidden = true;
  el.innerHTML = el.lastHtml = '';
}
export const workareaOpen = () => room !== null;
export function applyWorkarea(state) {
  doc = state;
  jobs = new Map((state.hosts || []).flatMap(h => (h.jobs || []).map(j => [`${h.name}:${j.id}`, { host: h.name, job: j }])));
  if (room === null && QS.has('workarea')) room = QS.get('workarea');
  if (room !== null) { el.hidden = false; render(); }
}

const MARK = { check: '✓', cross: '✗', glow: '●', dash: '–', blank: '' };
const GLYPH = '✱';
function render() {
  if (!doc) return;
  const w = workareaOf(doc, room, Date.now() / 1000);
  const lantern = w?.desk.lantern;
  const bench = b => {
    const job = jobs.get(b.key)?.job;
    return `<section class="bench" data-bench="${esc(b.key)}" data-status="${esc(b.status)}"${b.recent ? ' data-recent' : ''} style="--hc:${hostLook(b.host).color}">
      <div class="bench-head">${job ? glyphHtml(actionOf(job)) : ''}<b title="${esc(b.title)}">${esc(b.title)}</b><small>${esc(b.host)} · ${esc(b.id)}</small></div>
      <ol class="plan-wall" aria-label="Plan">${b.tiles.map(t => `<li class="tile" data-tile="${t.index}" data-status="${esc(t.status)}" data-mark="${t.mark}"
        title="Step ${t.index + 1}: ${esc(t.title)} (${esc(t.status)})"><span class="mk" aria-hidden="true">${MARK[t.mark]}</span><span class="tt">${esc(t.title)}</span></li>`).join('')}</ol>
      <div class="criteria" role="img" aria-label="${b.criteria.met} of ${b.criteria.total} steps done">${
        b.criteria.lights.map(on => `<i${on ? ' data-on' : ''}></i>`).join('')}<b>${b.criteria.met}/${b.criteria.total}</b></div>
      <button class="tray" data-tray="${esc(b.key)}"${b.reports.length ? '' : ' disabled'} aria-label="Report tray: ${b.reports.length} report${b.reports.length === 1 ? '' : 's'}"
        title="${b.reports.length ? 'Open the latest report' : 'No reports yet'}">${b.reports.map(r => `<span class="paper" data-report="${esc(r.id)}" title="${esc(r.name)}"></span>`).join('')}</button>
    </section>`;
  };
  const html = `<div class="wa-sheet">
    <div class="wa-head"><h2>${esc(room)}</h2><button data-wa-close aria-label="Close workarea">✕</button></div>
    ${w ? `<div class="wa-floor">
      <svg class="wa-steps" aria-hidden="true"></svg>
      <div class="wa-front">
        <div class="wa-door" data-entrance title="Entrance" aria-label="Entrance"></div>
        <div class="wa-desk" data-desk>
          ${lantern ? `<span class="wa-lantern${lantern.level === 'acknowledged' ? ' ack' : ''}" data-state="${lantern.level}" data-kind="${lantern.kind}"
            data-count="${lantern.count}" role="img" aria-label="${lantern.count > 1 ? `${lantern.count} things need you` : 'something needs you'}"><span class="lg">${
            lantern.kind === 'blocker' ? '✋' : '?'}</span><b>${lantern.count > 1 ? lantern.count : ''}</b></span>` : ''}
          <div class="desk-top" title="Question desk">${w.desk.items.map(i => `<span class="slip" data-kind="${esc(i.kind)}" title="${esc(i.summary)}"></span>`).join('')}</div>
        </div>
      </div>
      <div class="wa-benches">${w.benches.length ? w.benches.map(bench).join('') : '<p class="none">Nobody’s working here right now.</p>'}</div>
    </div>` : '<p class="none">Nothing here: no jobs in this room in the last hour.</p>'}
  </div>`;
  if (html === el.lastHtml) return;   // state arrives many times a second; redraw only what changed
  el.lastHtml = el.innerHTML = html;
  requestAnimationFrame(() => drawFootprints(w));
}

// Dotted trails from the entrance to each bench used in the last hour.
function drawFootprints(w) {
  const svg = el.querySelector('.wa-steps'), door = el.querySelector('[data-entrance]');
  if (!svg || !door || !w) return;
  const box = svg.getBoundingClientRect(), d = door.getBoundingClientRect();
  const x0 = d.left + d.width / 2 - box.left, y0 = d.top + d.height / 2 - box.top;
  svg.innerHTML = w.footprints.map(f => {
    const b = el.querySelector(`[data-bench="${CSS.escape(f.to)}"]`)?.getBoundingClientRect();
    if (!b) return '';
    const x1 = b.left + b.width / 2 - box.left, y1 = b.top - box.top;
    return `<path data-to="${esc(f.to)}" d="M${x0.toFixed(1)} ${y0.toFixed(1)} C ${x0.toFixed(1)} ${((y0 + y1) / 2).toFixed(1)}, ${
      x1.toFixed(1)} ${((y0 + y1) / 2).toFixed(1)}, ${x1.toFixed(1)} ${y1.toFixed(1)}"/>`;
  }).join('');
}
window.addEventListener('resize', () => { if (room !== null && doc) drawFootprints(workareaOf(doc, room, Date.now() / 1000)); });

el.addEventListener('click', ev => {
  if (ev.target.closest('[data-wa-close]') || ev.target === el) { closeWorkarea(); return; }
  const tray = ev.target.closest('[data-tray]');
  if (!tray || tray.disabled) return;
  const found = jobs.get(tray.dataset.tray);
  if (!found) return;
  const paper = ev.target.closest('[data-report]');
  const reports = (found.job.documents || []).filter(d => d.kind === 'report');
  const report = paper ? reports.find(d => d.id === paper.dataset.report) : reports.sort((a, b) => (b.step ?? 0) - (a.step ?? 0))[0];
  if (report) openReader(ents.get(tray.dataset.tray) || found, report);
});
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || room === null || !document.getElementById('reader').hidden) return;
  ev.stopImmediatePropagation();   // one level: the panel and the floor stay as they are
  closeWorkarea();
}, { capture: true });

// read-only probe for browser tests
window.fleetWorkarea = Object.freeze({ room: () => room, model: () => (doc && room !== null ? workareaOf(doc, room, Date.now() / 1000) : null) });
