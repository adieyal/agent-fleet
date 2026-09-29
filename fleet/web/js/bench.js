// Floor → epic room → milestone bench. All business state comes from /api/bench.
import { esc, store } from './util.js';
import { hostLook } from './looks.js';
import { glyphHtml, svg } from './glyphs.js';
import { ents } from './model.js';
import { openReader } from './reader.js';
import { fallbackCopy } from './panel.js';

const el = document.body.appendChild(document.createElement('section'));
el.id = 'benchRoute';
el.hidden = true;
let project = null, rooms = [], room = null, bench = null, revision = 0;
let briefing = false;
// Collapsed to its header so the floor shows; remembered per browser.
let collapsed = store('localStorage', 'fleet.bench.collapsed') === '1';
const kinds = { checked: '<rect x="5" y="5" width="14" height="14"/>',
  judged: '<path d="M12 2 22 12 12 22 2 12Z"/>', accepted: '<circle cx="12" cy="12" r="9"/>' };
const figure = svg('<circle cx="12" cy="5" r="3"/><path d="M6 21V11h12v10M12 14v7M3 12v6M21 12v6"/>');
const lantern = svg('<path d="M8 7V5a4 4 0 0 1 8 0v2M6 7h12l2 13H4ZM9 10v7m6-7v7"/>');

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
  const glyph = glyphHtml(action === null ? 'unknown' : action);
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

// `epic` and `milestone` open straight at that epic's page or that milestone's bench.
export async function enterFloor(identity, { epic = null, milestone = null } = {}) {
  const request = ++revision;
  project = identity; room = bench = null;
  el.hidden = identity === null;
  if (identity === null) return;
  el.innerHTML = '<p>Loading work…</p>';
  try {
    const doc = await read();
    if (request !== revision) return;
    rooms = doc.rooms;
    room = rooms.find(r => r.id === epic) ?? null;
    if (milestone) {
      const slice = await read(milestone);
      if (request !== revision) return;
      bench = slice; briefing = false;
    }
    render();
  } catch (error) {
    if (request === revision) el.innerHTML = `<p role="alert">${esc(error.message)}</p>`;
  }
}

function streamProgress({ milestones: { complete, total }, next }) {
  if (!total) return '<small data-stream-progress>No milestones recorded</small>';
  return `<small data-stream-progress>${complete} of ${total} milestones · ${next ? `next ${esc(next.title)}` : 'all complete'}</small>`;
}

function epicCard(r) {
  const { complete, total } = r.milestones;
  const now = r.agents.length
    ? `${r.agents.length} running: ${r.agents.map(a => `${esc(a.title)} (${esc(a.host)})`).join(', ')}`
    : 'Nothing running';
  const next = r.upcoming.length
    ? `<ul>${r.upcoming.map(m => `<li data-upcoming="${esc(m.id)}">${esc(m.title)} — ${m.next_step === null ? '<i>Next step not recorded</i>' : esc(m.next_step)}</li>`).join('')}</ul>`
    : total ? 'All milestones complete' : 'No milestones recorded';
  const count = r.attention.length;
  return `<article data-epic-card="${esc(r.id)}" data-depth="${r.depth}" style="--depth:${r.depth}">
    <div data-epic-head><button data-epic="${esc(r.id)}">${esc(r.title)}</button>${r.parent ? `<small data-parent-epic>in ${esc(r.parent.title)}</small>` : ''}</div>
    <p data-goal title="${esc(r.goal)}">${esc(r.headline)}</p>
    <p data-milestones>${total ? `<progress max="${total}" value="${complete}"></progress> ${complete} of ${total} milestones` : 'No milestones recorded'}</p>
    ${r.workstreams.length ? `<ul data-workstreams aria-label="Workstreams">${r.workstreams.map(w =>
      `<li data-workstream="${esc(w.id)}"><b>${esc(w.title)}</b> ${streamProgress(w)}</li>`).join('')}</ul>` : ''}
    <p data-now><b data-label>Now:</b> ${now}</p>
    <div data-next><b data-label>Next:</b> ${next}</div>
    ${r.children.length ? `<p data-child-epics>Epics: ${r.children.map(c => esc(c.title)).join(', ')}</p>` : ''}
    <p data-attention-count="${count}">${count ? `${lantern} ${count} open ${count === 1 ? 'decision or blocker' : 'decisions or blockers'}` : 'No open decisions or blockers'}</p>
  </article>`;
}

const statuses = { complete: svg('<path d="M5 12.5 10 17.5 19 7"/>'),
  active: svg('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>'),
  next: svg('<circle cx="12" cy="12" r="8"/>'),
  blocked: svg('<circle cx="12" cy="12" r="8"/><path d="M6.5 17.5 17.5 6.5"/>'),
  'on hold': svg('<path d="M9 6v12M15 6v12"/>') };

function progressText({ basis, complete, total }) {
  if (basis === 'unknown') return 'Progress not recorded';
  return `<progress max="${total}" value="${complete}"></progress> ${complete} of ${total} ${basis === 'milestones' ? 'milestones' : 'criteria met'}`;
}

function planLine(item, opens) {
  const title = opens ? `<button data-slice="${esc(item.id)}">${esc(item.title)}</button>` : `<b>${esc(item.title)}</b>`;
  return `<li data-plan-item="${esc(item.id)}" data-status="${esc(item.status)}">
    <span data-glyph role="img" aria-label="${esc(item.status)}" title="${esc(item.status)} · ${esc(item.condition)}">${statuses[item.status]}</span>
    <div>${title}<p>${esc(item.headline)}</p><small data-next-step>${item.next_step === null ? 'Next step not recorded' : `Next: ${esc(item.next_step)}`}</small></div></li>`;
}

function epicPage(r) {
  return `<article data-epic-page="${esc(r.id)}"><h2>${esc(r.title)}</h2>
    <p data-goal>${esc(r.goal)}</p>
    ${r.criteria.length ? `<section data-epic-criteria aria-label="Criteria"><h3>Criteria</h3><ul>${r.criteria.map(c =>
      `<li data-criterion-state="${esc(c.state)}">${esc(c.text)} <small>${esc(c.verification)} · ${esc(c.state)}</small></li>`).join('')}</ul></section>` : ''}
    <p data-progress>${r.milestones.total
      ? progressText({ basis: 'milestones', ...r.milestones }) : progressText(r.progress)}</p>
    <h3>Milestones</h3>${r.plan.length ? `<ol data-plan-list>${r.plan.map(m => planLine(m, true)).join('')}</ol>`
      : r.workstreams.length ? '' : '<p>No milestones recorded</p>'}
    ${r.workstreams.map(w => `<section data-workstream="${esc(w.id)}" aria-label="${esc(w.title)}"><h4>${esc(w.title)} ${streamProgress(w)}</h4>${
      w.plan.length ? `<ol data-plan-list>${w.plan.map(m => planLine(m, true)).join('')}</ol>` : ''}</section>`).join('')}
    ${r.tasks.length ? `<h3>Tasks</h3><ol data-plan-list>${r.tasks.map(t => planLine(t, false)).join('')}</ol>` : ''}
    ${r.children.length ? `<h3>Epics</h3><p data-child-epics>${r.children.map(c => `<button data-epic="${esc(c.id)}">${esc(c.title)}</button>`).join('')}</p>` : ''}
  </article>`;
}

// A location shortened for reading: the host and the file name (fleet://home/…/result-0.md → home · result-0.md).
function reportWhere(location) {
  const m = /^fleet:\/\/([^/]+)\/(?:.*\/)?([^/]+)$/.exec(location || '');
  return m ? `${m[1]} · ${m[2]}` : (location || '');
}
const OPEN_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M4 1.8h5.2L12.5 5v9.2H4z"/><path d="M9 1.8V5.3h3.5M6 8.2h4.3M6 10.8h4.3"/></svg>';
const COPY_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 5.5V3.6A1.1 1.1 0 0 0 9.4 2.5H3.6a1.1 1.1 0 0 0-1.1 1.1v5.8a1.1 1.1 0 0 0 1.1 1.1h1.9"/></svg>';
// A step report's reader address from its fleet:// location: the host, and the report's document id (report-<step>).
function reportRef(report) {
  const m = /^fleet:\/\/([^/]+)\/.*\/result-(\d+)\.md$/.exec(report.canonical_location || '');
  return m && report.run ? { host: m[1], doc: `report-${m[2]}` } : null;
}

function render(flipped = new Set()) {
  el.dataset.level = bench ? 'bench' : room ? 'room' : 'floor';
  el.toggleAttribute('data-collapsed', collapsed);
  const crumbs = `<header data-bench-head><nav id="benchBreadcrumb" aria-label="Breadcrumb"><button data-back-floor>Floor</button>${
    room ? ` / <button data-back-room>${esc(room.title)}</button>` : ''}${bench ? ` / <span>${esc(bench.title)}</span>` : ''}</nav>
    <button data-collapse aria-expanded="${!collapsed}" title="${collapsed ? 'Show' : 'Hide'} the plan" aria-label="${collapsed ? 'Show' : 'Hide'} the plan">${collapsed ? '▸' : '▾'}</button></header>`;
  let content;
  if (bench) {
    content = `<h2>${esc(bench.title)}</h2><div class="slice-bench"><section data-plan aria-label="Plan wall">${bench.tasks.length ? '' : '<p data-empty>No tasks</p>'}<ol>${bench.tasks.map(task =>
      `<li data-task="${esc(task.id)}" data-flipped="${flipped.has(task.id)}" data-lane="${task.lane}" data-condition="${esc(task.condition)}" title="${esc(task.condition)}"><i></i>${esc(task.title)}</li>`).join('')}</ol></section>
      <section aria-label="Criteria">${bench.criteria.length ? '' : '<p data-empty>No criteria</p>'}${bench.criteria.map(c => `<span data-verification="${c.verification}" data-state="${c.state}" aria-label="${c.verification} ${c.state}" title="${esc(c.text)}">${svg(kinds[c.verification])}</span>`).join('')}
      <p>${bench.progress.total === null ? 'Progress unknown' : `${bench.progress.complete} / ${bench.progress.total}`}</p></section>
      <section data-agents aria-label="Agents">${bench.agents.length > 5
        ? `<div data-agent-group data-count="${bench.agents.length}" aria-label="${bench.agents.length} agents">${figure}${figure}<b>${bench.agents.length}</b></div>`
        : bench.agents.map(agentMarkup).join('')}</section>
      <section data-desk aria-label="Question desk">${bench.attention.length ? `<span data-lantern aria-label="Open attention">${lantern}</span>` : '<p data-empty>Desk clear</p>'}</section>
      <details data-tray><summary>Reports <b>${bench.reports.length}</b></summary><ul>${bench.reports.map(report => {
        const at = reportRef(report), title = report.title === null ? 'Title unknown' : esc(report.title);
        const where = reportWhere(report.canonical_location);
        return `<li data-availability="${esc(report.availability)}"><span data-report-title title="${title}">${title}</span>${at ? `<button data-open-report data-host="${esc(at.host)}" data-job="${esc(report.run)}" data-doc="${esc(at.doc)}" data-title="${title}" title="Read this report" aria-label="Read ${title}">${OPEN_ICON}</button>` : ''}
          <small>${esc(report.availability)} · <span title="${esc(report.canonical_location)}">${esc(where)}</span>${report.canonical_location ? `<button data-copy="${esc(report.canonical_location)}" title="Copy the full location" aria-label="Copy the full location">${COPY_ICON}</button>` : ''}</small></li>`;
      }).join('')}</ul></details>
      <section><button data-briefing aria-expanded="${briefing}">Briefing</button><div data-summary ${briefing ? '' : 'hidden'}>${bench.summary === null ? 'Summary unknown' : ['purpose', 'done', 'doing', 'next'].map(key => `<p><b>${key}</b> ${esc(bench.summary[key])}</p>`).join('')}</div></section></div>`;
  } else if (room) {
    content = epicPage(room);
  } else {
    content = `<div class="epic-cards">${rooms.map(epicCard).join('')}</div>`;
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
  const open = ev.target.closest('[data-open-report]');
  if (open) {
    const { host, job, doc } = open.dataset;
    const name = open.dataset.title;
    openReader(ents.get(`${host}:${job}`) || { host, job: { id: job, description: name } }, { id: doc, kind: 'report', name });
    return;
  }
  const copy = ev.target.closest('[data-copy]');
  if (copy) {
    const done = () => { copy.dataset.copied = ''; setTimeout(() => delete copy.dataset.copied, 1400); };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(copy.dataset.copy).then(done, () => fallbackCopy(copy.dataset.copy, done));
    else fallbackCopy(copy.dataset.copy, done);
    return;
  }
  if (collapsed && ev.target.closest('#benchBreadcrumb button')) { collapsed = false; store('localStorage', 'fleet.bench.collapsed', '0'); }
  if (ev.target.closest('[data-collapse]')) {
    collapsed = !collapsed;
    store('localStorage', 'fleet.bench.collapsed', collapsed ? '1' : '0');
    render();
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
  // A milestone with no epic above it opens with no room; stepping back from it lands on the floor list.
  if (!room && !bench) { enterFloor(null); return; }
  ev.stopImmediatePropagation();
  ++revision;
  if (bench) bench = null; else room = null;
  render();
}, { capture: true });
