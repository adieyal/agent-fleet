// Floor → epic room → milestone bench. All business state comes from /api/bench.
import { esc } from './util.js';

const el = document.body.appendChild(document.createElement('section'));
el.id = 'benchRoute';
el.hidden = true;
let project = null, rooms = [], room = null, bench = null, revision = 0;

async function read(slice) {
  const query = new URLSearchParams({ project });
  if (slice) query.set('slice', slice);
  const response = await fetch(`/api/bench?${query}`);
  const doc = await response.json();
  if (!response.ok) throw new Error(doc.error);
  return doc;
}

export async function enterFloor(identity) {
  const request = ++revision;
  project = identity; room = bench = null;
  el.hidden = identity === null;
  if (identity === null) return;
  el.innerHTML = '<p>Loading work…</p>';
  try {
    const doc = await read();
    if (request !== revision) return;
    rooms = doc.rooms;
    render();
  } catch (error) {
    if (request === revision) el.innerHTML = `<p role="alert">${esc(error.message)}</p>`;
  }
}

function render() {
  el.dataset.level = bench ? 'bench' : room ? 'room' : 'floor';
  const crumbs = `<nav id="benchBreadcrumb" aria-label="Breadcrumb"><button data-back-floor>Floor</button>${
    room ? ` / <button data-back-room>${esc(room.title)}</button>` : ''}${bench ? ` / <span>${esc(bench.title)}</span>` : ''}</nav>`;
  let content;
  if (bench) {
    content = `<h2>${esc(bench.title)}</h2><ol>${bench.tasks.map(task =>
      `<li data-lane="${task.lane}" data-condition="${esc(task.condition)}">${esc(task.title)}</li>`).join('')}</ol>
      <div aria-label="Criteria">${bench.criteria.map(c => `<span data-verification="${c.verification}" data-state="${c.state}" title="${esc(c.text)}">${
        { checked: '■', judged: '◆', accepted: '●' }[c.verification]}</span>`).join('')}</div>
      <p>${bench.progress.total === null ? 'Progress unknown' : `${bench.progress.complete} / ${bench.progress.total}`}</p>`;
  } else if (room) {
    content = `<div class="bench-cluster">${room.benches.map(b => `<button data-slice="${esc(b.id)}"><i aria-hidden="true"></i>${esc(b.title)}</button>`).join('')}</div>`;
  } else {
    content = rooms.map(r => `<button data-epic="${esc(r.id)}">${esc(r.title)}</button>`).join('');
    if (!rooms.length) content = '<p>No epic rooms recorded.</p>';
  }
  el.innerHTML = crumbs + content;
}

el.addEventListener('click', async ev => {
  if (ev.target.closest('[data-back-floor]')) { ++revision; room = bench = null; render(); return; }
  if (ev.target.closest('[data-back-room]')) { ++revision; bench = null; render(); return; }
  const epic = ev.target.closest('[data-epic]'), slice = ev.target.closest('[data-slice]');
  if (epic) { room = rooms.find(r => r.id === epic.dataset.epic); render(); }
  if (slice) {
    const request = ++revision;
    try {
      const doc = await read(slice.dataset.slice);
      if (request !== revision) return;
      bench = doc; render();
    } catch (error) {
      if (request === revision) el.innerHTML += `<p role="alert">${esc(error.message)}</p>`;
    }
  }
});

// Capture at window so the building cannot consume this same Escape.
window.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || el.hidden) return;
  if (!document.getElementById('reader').hidden) return;
  if (!room) { enterFloor(null); return; }
  ev.stopImmediatePropagation();
  ++revision;
  if (bench) bench = null; else room = null;
  render();
}, { capture: true });
