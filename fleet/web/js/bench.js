// Floor → epic room → milestone bench. All business state comes from /api/bench.
import { esc } from './util.js';
import { hostLook } from './looks.js';
import { glyphHtml } from './glyphs.js';

const el = document.body.appendChild(document.createElement('section'));
el.id = 'benchRoute';
el.hidden = true;
let project = null, rooms = [], room = null, bench = null, revision = 0;
let briefing = false;
const svg = paths => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
const kinds = { checked: '<rect x="5" y="5" width="14" height="14"/>',
  judged: '<path d="M12 2 22 12 12 22 2 12Z"/>', accepted: '<circle cx="12" cy="12" r="9"/>' };
const figure = svg('<circle cx="12" cy="5" r="3"/><path d="M6 21V11h12v10M12 14v7M3 12v6M21 12v6"/>');
const lantern = svg('<path d="M8 7V5a4 4 0 0 1 8 0v2M6 7h12l2 13H4ZM9 10v7m6-7v7"/>');
const activityShapes = {
  web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3v18"/>',
  plan: '<path d="M5 5h14v16H5ZM8 9h8M8 13h8M8 17h4"/>',
  delegate: '<path d="M12 3v8M4 21v-7h16v7M12 14v7"/>',
  type: '<path d="M2 7h20v12H2ZM5 11h2m3 0h2m3 0h2M6 15h12"/>',
  doc: '<path d="M5 2h10l4 4v16H5ZM9 10h6M9 14h6M9 18h4"/>',
  ship: '<path d="m3 12 9-9 9 9M12 3v18M4 21h16"/>',
  review: '<path d="M2 12q10-14 20 0-10 14-20 0Z"/><circle cx="12" cy="12" r="3"/>',
  build: '<path d="M3 21V10l9-7 9 7v11ZM8 21v-8h8v8"/>',
};

export async function refreshBench() {
  if (!bench || el.hidden) return;
  const request = ++revision;
  try {
    const doc = await read(bench.id);
    if (request !== revision) return;
    const previous = new Map(bench.tasks.map(task => [task.id, task.lane]));
    const trayOpen = el.querySelector('[data-tray]')?.open === true;
    bench = doc;
    render(new Set(doc.tasks.filter(task => previous.has(task.id) && previous.get(task.id) !== 'done' && task.lane === 'done').map(task => task.id)));
    el.querySelector('[data-tray]').open = trayOpen;
  } catch (error) {
    if (request === revision) el.innerHTML = `<p role="alert">${esc(error.message)}</p>`;
  }
}

function agentMarkup(agent) {
  const action = agent.action_glyph;
  const glyph = action === null ? `<span data-action="unknown" aria-label="Action unknown">${svg('<path d="M5 5 19 19M19 5 5 19"/>')}</span>`
    : activityShapes[action] ? `<span class="glyph" data-action="${action}" role="img" aria-label="${action}">${svg(activityShapes[action])}</span>`
      : glyphHtml(({ read: 'search', wait: 'ask' })[action] ?? action);
  return `<div data-agent="${esc(agent.run)}" data-freshness="${esc(agent.action_freshness)}" title="${esc(agent.host)} · ${esc(agent.status)} · ${esc(agent.action_freshness)}" style="--hc:${hostLook(agent.host).color}">${figure}${glyph}</div>`;
}

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

function render(flipped = new Set()) {
  el.dataset.level = bench ? 'bench' : room ? 'room' : 'floor';
  const crumbs = `<nav id="benchBreadcrumb" aria-label="Breadcrumb"><button data-back-floor>Floor</button>${
    room ? ` / <button data-back-room>${esc(room.title)}</button>` : ''}${bench ? ` / <span>${esc(bench.title)}</span>` : ''}</nav>`;
  let content;
  if (bench) {
    content = `<h2>${esc(bench.title)}</h2><div class="slice-bench"><section data-plan aria-label="Plan wall"><ol>${bench.tasks.map(task =>
      `<li data-task="${esc(task.id)}" data-flipped="${flipped.has(task.id)}" data-lane="${task.lane}" data-condition="${esc(task.condition)}" title="${esc(task.condition)}"><i></i>${esc(task.title)}</li>`).join('')}</ol></section>
      <section aria-label="Criteria">${bench.criteria.map(c => `<span data-verification="${c.verification}" data-state="${c.state}" aria-label="${c.verification} ${c.state}" title="${esc(c.text)}">${svg(kinds[c.verification])}</span>`).join('')}
      <p>${bench.progress.total === null ? 'Progress unknown' : `${bench.progress.complete} / ${bench.progress.total}`}</p></section>
      <section data-agents aria-label="Agents">${bench.agents.length > 5
        ? `<div data-agent-group data-count="${bench.agents.length}" aria-label="${bench.agents.length} agents">${figure}${figure}<b>${bench.agents.length}</b></div>`
        : bench.agents.map(agentMarkup).join('')}</section>
      <section data-desk aria-label="Question desk">${bench.attention.length ? `<span data-lantern aria-label="Open attention">${lantern}</span>` : ''}</section>
      <details data-tray><summary>Reports <b>${bench.reports.length}</b></summary><ul>${bench.reports.map(report => `<li data-availability="${esc(report.availability)}">${report.title === null ? 'Title unknown' : esc(report.title)}<small>${esc(report.availability)} · ${esc(report.canonical_location)}</small></li>`).join('')}</ul></details>
      <section><button data-briefing aria-expanded="${briefing}">Briefing</button><div data-summary ${briefing ? '' : 'hidden'}>${bench.summary === null ? 'Summary unknown' : ['purpose', 'done', 'doing', 'next'].map(key => `<p><b>${key}</b> ${esc(bench.summary[key])}</p>`).join('')}</div></section></div>`;
  } else if (room) {
    content = `<div class="bench-cluster">${room.benches.map(b => `<button data-slice="${esc(b.id)}"><i aria-hidden="true"></i>${esc(b.title)}</button>`).join('')}</div>`;
  } else {
    content = rooms.map(r => `<button data-epic="${esc(r.id)}">${esc(r.title)}</button>`).join('');
    if (!rooms.length) content = '<p>No epic rooms recorded.</p>';
  }
  el.innerHTML = crumbs + content;
}

el.addEventListener('click', async ev => {
  if (ev.target.closest('[data-briefing]')) {
    briefing = !briefing;
    el.querySelector('[data-summary]').hidden = !briefing;
    el.querySelector('[data-briefing]').setAttribute('aria-expanded', briefing);
    return;
  }
  if (ev.target.closest('[data-back-floor]')) { ++revision; room = bench = null; render(); return; }
  if (ev.target.closest('[data-back-room]')) { ++revision; bench = null; render(); return; }
  const epic = ev.target.closest('[data-epic]'), slice = ev.target.closest('[data-slice]');
  if (epic) { room = rooms.find(r => r.id === epic.dataset.epic); render(); }
  if (slice) {
    const request = ++revision;
    try {
      const doc = await read(slice.dataset.slice);
      if (request !== revision) return;
      bench = doc; briefing = false; render();
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
