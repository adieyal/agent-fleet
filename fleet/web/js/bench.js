import { openItemHistory } from './item-history.js';
import { mountRunHistory } from './run-history.js';
// Floor → epic room → milestone bench. All business state comes from /api/bench.
import { duration, esc, store } from './util.js';
import { hostLook } from './looks.js';
import { actionOf, glyphHtml, svg } from './glyphs.js';
import { ents, workOf } from './model.js';
import { openReader, openStoredReader } from './reader.js';
import { fallbackCopy, idChip, select } from './panel.js';
import { decisionsPanel, guidancePanel, guidanceSummary, triagePolicyPanel } from './guidance.js';

const el = document.body.appendChild(document.createElement('section'));
el.id = 'benchRoute';
el.hidden = true;
let project = null, rooms = [], room = null, bench = null, revision = 0;
let briefing = false;
// Guidance, keyed 'constitution' or by epic id: /api/guidance views and an epic's /api/decisions. `page` is
// 'constitution' while the floor's constitution is open; `editing` the open editor, which pauses redraws.
let page = null, editing = null, guidanceFeedback = null;
const views = new Map(), decisionLists = new Map(), historyOpen = new Set();
// Collapsed to its header so the floor shows; remembered per browser.
let collapsed = store('localStorage', 'fleet.bench.collapsed') === '1';
const kinds = { checked: '<rect x="5" y="5" width="14" height="14"/>',
  judged: '<path d="M12 2 22 12 12 22 2 12Z"/>', accepted: '<circle cx="12" cy="12" r="9"/>' };
const figure = svg('<circle cx="12" cy="5" r="3"/><path d="M6 21V11h12v10M12 14v7M3 12v6M21 12v6"/>');
const lantern = svg('<path d="M8 7V5a4 4 0 0 1 8 0v2M6 7h12l2 13H4ZM9 10v7m6-7v7"/>');

// State arrives many times a second; the floor and epic pages re-read their work at most this often.
const ROOMS_REFRESH_MS = 3000;
let roomsReadAt = 0, roomsTimer = null;

export async function refreshBench() {
  if (el.hidden || project === null || page === 'history') return;
  if (!bench) { refreshRooms(); return; }
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

// The floor's epic cards and an epic's page: re-read, and redraw only when the work changed, keeping the
// scroll position and which step plans are open.
function refreshRooms() {
  const wait = roomsReadAt + ROOMS_REFRESH_MS - Date.now();
  if (wait > 0) { roomsTimer ??= setTimeout(() => { roomsTimer = null; refreshBench(); }, wait); return; }
  roomsReadAt = Date.now();
  if (room) loadDecisions(room.id);
  else if (page === 'decisions') loadDecisions('project');
  // Its own check, not `revision`: a background read must never cancel a page the user just asked for.
  const asked = { project, revision };
  read().then(doc => {
    if (asked.project !== project || asked.revision !== revision || bench || page === 'history' || editing
        || JSON.stringify(doc.rooms) === JSON.stringify(rooms)) return;
    rooms = doc.rooms;
    if (room) room = rooms.find(r => r.id === room.id) ?? null;
    const open = new Set([...el.querySelectorAll('[data-step-plan][open]')].map(d => d.closest('[data-plan-item]')?.dataset.planItem));
    const scroll = el.scrollTop;
    render();
    el.querySelectorAll('[data-plan-item]').forEach(line => {
      if (open.has(line.dataset.planItem)) line.querySelector('[data-step-plan]')?.setAttribute('open', '');
    });
    el.scrollTop = scroll;
  }).catch(() => {});   // a missed refresh keeps the last good page; the next state update tries again
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

async function json(url, body) {
  const response = await fetch(url, body === undefined ? undefined
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const doc = await response.json();
  if (!response.ok) throw new Error(doc.error);
  return doc;
}

const guidanceUrl = (key, version) => '/api/guidance?' + new URLSearchParams({ project,
  ...(key === 'constitution' ? {} : { epic: key }), ...(version ? { version } : {}) });

// Read into the cache, then redraw unless the page moved on or someone is typing.
async function loadGuidance(key) {
  const asked = project;
  try {
    const view = await json(guidanceUrl(key));
    if (asked !== project) return;
    views.set(key, view);
  } catch (error) {
    if (asked !== project) return;
    views.set(key, { error: error.message });
  }
  if (!editing && page !== 'history' && !el.hidden) render();
}

async function loadDecisions(epic) {
  const asked = project;
  let list;
  try { list = await json('/api/decisions?' + new URLSearchParams(epic === 'project' ? { project } : { epic })); }
  catch (error) { list = { decisions: decisionLists.get(epic)?.decisions ?? [], error: error.message }; }
  if (asked !== project || JSON.stringify(list) === JSON.stringify(decisionLists.get(epic))) return;
  decisionLists.set(epic, list);
  if (!editing && !bench && (room?.id === epic || epic === 'project' && page === 'decisions')) render();
}

function openRoom(r) {
  room = r;
  render();
  if (r) { loadGuidance(r.id); loadDecisions(r.id); }
}

// `epic` and `milestone` open straight at that epic's page or that milestone's bench.
export async function enterFloor(identity, { epic = null, milestone = null } = {}) {
  const request = ++revision;
  project = identity; room = bench = page = editing = guidanceFeedback = null;
  views.clear(); decisionLists.clear(); historyOpen.clear();
  el.hidden = identity === null;
  if (identity === null) return;
  el.innerHTML = '<p>Loading work…</p>';
  try {
    const doc = await read();
    if (request !== revision) return;
    rooms = doc.rooms;
    if (milestone) {
      const slice = await read(milestone);
      if (request !== revision) return;
      bench = slice; briefing = false;
    }
    openRoom(rooms.find(r => r.id === epic) ?? null);
    loadGuidance('constitution');
  } catch (error) {
    if (request !== revision) return;
    rooms = [];   // so the next refresh draws the rooms over this error (the server may just be restarting)
    el.innerHTML = `<p role="alert">${esc(error.message)}</p>`;
  }
}

function streamProgress({ milestones: { complete, total }, next }) {
  if (!total) return '<small data-stream-progress>No milestones recorded</small>';
  return `<small data-stream-progress>${complete} of ${total} milestones · ${next ? `next ${esc(next.title)}` : 'all complete'}</small>`;
}

// The work that replaced this item: an epic opens its room; one from another project shows by id.
function successorLine(list) {
  if (!list.length) return '';
  return `<small data-superseded-by>Superseded by ${list.map(s => rooms.some(r => r.id === s.id)
    ? `<button data-epic="${esc(s.id)}">${esc(s.title)}</button>`
    : s.title === null ? `<span title="${esc(s.id)}">${esc(s.id.slice(0, 8))} (another project)</span>` : esc(s.title)).join(', ')}</small>`;
}

// How an epic's work stands: one segment per status, sized by its count, over milestones or else tasks.
const SEGMENTS = [['complete', 'complete'], ['ran', 'ran, awaiting acceptance'], ['active', 'running'],
  ['blocked', 'blocked'], ['on hold', 'on hold'], ['next', 'not started']];
function progressBar({ basis, total, counts }) {
  if (!total) return '<p data-milestones>No milestones or tasks recorded</p>';
  const done = counts.complete || 0, running = counts.active || 0;
  const segments = SEGMENTS.filter(([key]) => counts[key]).map(([key, label]) =>
    `<i data-segment="${key}" style="flex:${counts[key]}" title="${counts[key]} ${label}"></i>`).join('');
  return `<div data-milestones data-basis="${esc(basis)}"><div data-progress-bar role="img" aria-label="${done} of ${total} ${basis} complete">${segments}</div>
    <small>${done} of ${total} ${basis} complete${running ? ` · ${running} running` : ''}${counts.blocked ? ` · ${counts.blocked} blocked` : ''}</small></div>`;
}

function epicCard(r) {
  const { total } = r.milestones;
  const now = r.now.length
    ? `${r.now.length} running${r.now.map(j => `<span data-now-row><span data-now-item>${esc(j.title)}</span>${jobChip(j)}</span>`).join('')}`
    : 'Nothing running';
  const next = r.upcoming.length
    ? `<ul>${r.upcoming.map(m => `<li data-upcoming="${esc(m.id)}">${esc(m.title)} — ${m.next_step === null ? '<i>Next step not recorded</i>' : esc(m.next_step)}</li>`).join('')}</ul>`
    : total ? 'All milestones complete' : 'No milestones recorded';
  const count = r.attention.length;
  return `<article data-epic-card="${esc(r.id)}" data-condition="${esc(r.condition)}" data-depth="${r.depth}" style="--depth:${r.depth}">
    <div data-epic-head><button data-epic="${esc(r.id)}">${esc(r.title)}</button>${r.condition === 'dropped' ? '<small data-dropped>Dropped</small>' : ''}${r.parent ? `<small data-parent-epic>in ${esc(r.parent.title)}</small>` : ''}</div>
    ${successorLine(r.superseded_by)}
    <p data-goal title="${esc(r.goal)}">${esc(r.headline)}</p>
    ${itemHistoryButton(r)}${progressBar(r.breakdown)}
    ${r.workstreams.length ? `<ul data-workstreams aria-label="Workstreams">${r.workstreams.map(w =>
      `<li data-workstream="${esc(w.id)}"><b>${esc(w.title)}</b> ${streamProgress(w)}</li>`).join('')}</ul>` : ''}
    <p data-now><b data-label>Now:</b> ${now}</p>
    <div data-next><b data-label>Next:</b> ${next}</div>
    ${r.children.length ? `<p data-child-epics>Epics: ${r.children.map(c => esc(c.title)).join(', ')}</p>` : ''}
    <p data-attention-count="${count}">${count ? `${lantern} ${count} open ${count === 1 ? 'attention item' : 'attention items'}` : 'No open attention items'}</p>
  </article>`;
}

const STATUS_LABELS = { ran: 'run finished, not yet accepted as complete' };
const TICK ='<path d="M8 12.5 11 15.5 16.5 9"/>';
const statuses = { complete: svg(`<circle cx="12" cy="12" r="9" fill="currentColor"/>${TICK.replace('/>', ' stroke="#0b111d"/>')}`),
  ran: svg(`<circle cx="12" cy="12" r="8"/>${TICK}`),
  active: svg('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>'),
  next: svg('<circle cx="12" cy="12" r="8"/>'),
  blocked: svg('<circle cx="12" cy="12" r="8"/><path d="M6.5 17.5 17.5 6.5"/>'),
  'on hold': svg('<path d="M9 6v12M15 6v12"/>'),
  dropped: svg('<circle cx="12" cy="12" r="8"/><path d="M8 12h8"/>') };

function progressText({ basis, complete, total }) {
  if (basis === 'unknown') return 'Progress not recorded';
  return `<progress max="${total}" value="${complete}"></progress> ${complete} of ${total} ${basis === 'milestones' ? 'milestones' : 'criteria met'}`;
}

const runningFor = since => duration((Date.now() - Date.parse(since)) / 1000);
const ranLine = ({ start, end }) => `<small data-ran-for title="${esc(new Date(start).toLocaleString())} – ${esc(new Date(end).toLocaleString())}">Ran for ${duration((Date.parse(end) - Date.parse(start)) / 1000)}</small>`;
const runningLine = since => `<small data-running-since="${esc(since)}" title="Started ${esc(new Date(since).toLocaleString())}">Running for ${runningFor(since)}</small>`;
// The page is re-rendered only when the work changes, so the running times tick on their own.
setInterval(() => el.querySelectorAll('[data-running-since]').forEach(line => {
  line.textContent = `Running for ${runningFor(line.dataset.runningSince)}`;
}), 15000);

// A job serving a line: host:id, its status as a glyph, the step it is on and its branch. Clicking opens its panel
// while the deck still has the job; a finished job that is the line's latest shows quieter.
const RUN_ACTIONS = { succeeded: 'done', failed: 'failed', stopped: 'cancelled', 'unknown outcome': 'unknown' };
function jobChip(j) {
  const key = `${j.host}:${j.job}`, live = workOf(key);
  const action = j.status === 'running' ? (live ? actionOf(live) : 'unknown') : RUN_ACTIONS[j.status];
  const step = j.step === null ? '<small data-job-step title="Step not known">step ?</small>'
    : `<small data-job-step title="Step ${j.step.index + 1}${j.step.count === null ? ' (step count not known)' : ` of ${j.step.count}`} serves ${esc(j.step.work_item.title ?? j.step.work_item.id)}">step ${j.step.index + 1}${j.step.count === null ? '' : `/${j.step.count}`}</small>`;
  return `<span class="job-chip" data-job-chip="${esc(key)}" data-status="${esc(j.status)}"${j.past ? ' data-past' : ''}${live ? '' : ' data-gone'}
    title="${live ? 'Open this job’s panel' : 'Not on the deck'} · ${esc(j.runtime ?? 'runtime unknown')} · ${esc(j.status)}${j.status === j.job_status ? '' : ` here, job ${esc(j.job_status)}`}"><button data-job-open${live ? '' : ' disabled'} aria-label="${live ? 'Open the job panel' : 'Not on the deck'}: ${esc(j.host)} ${esc(j.job.slice(0, 8))}">${glyphHtml(action)}<b style="--hc:${hostLook(j.host).color}">${esc(j.host)}:</b></button>${idChip(j.job)}${step}${branchTag(j)}</span>`;
}

// The branch, or detached head; the worktree path and repository in its title, the path one click to copy.
function branchTag({ workspace: w, workspace_reason: reason }) {
  if (w === null) return `<small data-job-branch data-unknown title="${esc(reason)}"><span>workspace unknown</span></small>`;
  const name = w.detached ? `detached @${w.head}` : w.branch;
  const where = `${name} · ${w.linked_worktree ? 'worktree' : 'checkout'} ${w.toplevel}${w.linked_worktree ? ` of ${w.repository}` : ''} · ${w.dirty} uncommitted`;
  return `<small data-job-branch title="${esc(where)}"><span>${esc(name)}</span>${w.dirty ? `<i data-dirty>*</i>` : ''}<button data-copy="${esc(w.toplevel)}" title="Copy the ${w.linked_worktree ? 'worktree' : 'checkout'} path" aria-label="Copy the ${w.linked_worktree ? 'worktree' : 'checkout'} path">${COPY_ICON}</button></small>`;
}
const jobChips = jobs => jobs.length ? `<span data-job-chips>${jobs.map(jobChip).join('')}</span>` : '';

function planLine(item, opens) {
  const first = item.documents.find(docRef);
  const title = opens ? `<button data-slice="${esc(item.id)}">${esc(item.title)}</button>`
    : first ? `<button ${openDocAttrs(docRef(first), esc(item.title))} title="Read its latest ${esc(first.kind)}">${esc(item.title)}</button>`
    : `<b>${esc(item.title)}</b>`;
  return `<li data-plan-item="${esc(item.id)}" data-status="${esc(item.status)}">
    <span data-glyph role="img" aria-label="${esc(STATUS_LABELS[item.status] ?? item.status)}" title="${esc(STATUS_LABELS[item.status] ?? item.status)} · ${esc(item.condition)}">${statuses[item.status]}</span>
    <div>${title}${itemHistoryButton(item)}<p>${esc(item.headline)}</p>${successorLine(item.superseded_by)}${item.running_since ? runningLine(item.running_since) : item.last_run ? ranLine(item.last_run) : ''}${jobChips(item.jobs)}<small data-next-step>${item.next_step === null ? 'Next step not recorded' : `Next: ${esc(item.next_step)}`}</small>${
      item.plan === null ? '' : `<details data-step-plan><summary>Plan</summary><div>${esc(item.plan)}</div></details>`}${docsList(item.documents)}</div></li>`;
}

function epicPage(r) {
  return `<article data-epic-page="${esc(r.id)}" data-condition="${esc(r.condition)}"><h2>${esc(r.title)}${r.condition === 'dropped' ? ' <small data-dropped>Dropped</small>' : ''}</h2>
    ${successorLine(r.superseded_by)}
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
    <section data-room-attention aria-label="Room attention"><h3>Room attention</h3>${r.attention.length ? `<ul>${r.attention.map(a => `<li data-attention="${esc(a.id)}"><b>${esc(a.headline)}</b><small>${esc(a.kind)} · ${idChip(a.id)}</small></li>`).join('')}</ul>` : '<p>No open attention items.</p>'}</section>
    <section data-room-policy><button data-open-constitution>Constitution</button><small>${guidanceSummary(views.get('constitution'))}</small>${triagePolicyPanel(views.get(r.id))}</section>
    ${guidancePanel('charter', views.get(r.id), guidanceState(r.id))}
    ${decisionsPanel(decisionLists.get(r.id))}
  </article>`;
}

function itemHistoryButton(i, compact = false) { return `<button data-work-history="${esc(i.id)}" data-title="${esc(i.title)}" data-status="${esc(i.status || i.condition || '')}" aria-label="History for ${esc(i.title)}" title="Read this item’s stored changes and runs; opening changes no stored state">${compact ? '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><circle cx="8" cy="8" r="6"/><path d="M8 4v4l3 2"/></svg>' : 'History'}</button>`; }

const guidanceState = key => ({ editing: editing?.key === key ? editing : null, history: historyOpen.has(key) });
const guidanceKey = kind => kind === 'charter' ? room.id : 'constitution';

function constitutionCard() {
  return `<article data-constitution-card><div data-epic-head><button data-open-constitution>Constitution</button></div>
    <p><small>${guidanceSummary(views.get('constitution'))}</small></p></article>`;
}

// A location shortened for reading: the host and the file name (fleet://home/…/result-0.md → home · result-0.md).
function reportWhere(location) {
  const m = /^fleet:\/\/([^/]+)\/(?:.*\/)?([^/]+)$/.exec(location || '');
  return m ? `${m[1]} · ${m[2]}` : (location || '');
}
const OPEN_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M4 1.8h5.2L12.5 5v9.2H4z"/><path d="M9 1.8V5.3h3.5M6 8.2h4.3M6 10.8h4.3"/></svg>';
const COPY_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 5.5V3.6A1.1 1.1 0 0 0 9.4 2.5H3.6a1.1 1.1 0 0 0-1.1 1.1v5.8a1.1 1.1 0 0 0 1.1 1.1h1.9"/></svg>';
// A job document's reader address from its fleet:// location: the host, the job, and the document id fleetd gives it
// (report-<step>, brief-<step>, outbox-<path>, context-<path>); null for anything the reader can't open.
function docRef(entry) {
  const m = /^fleet:\/\/([^/]+)\/(?:.*\/)?jobs\/([^/]+)\/((?:result|brief)-\d+\.md|(?:outbox|context)\/.+)$/
    .exec(entry.canonical_location || '');
  if (!m) return null;
  const [, host, job, rest] = m;
  const doc = rest.replace(/^result-(\d+)\.md$/, 'report-$1').replace(/^brief-(\d+)\.md$/, 'brief-$1')
    .replace(/^(outbox|context)\//, '$1-');
  return { host: decodeURIComponent(host), job, doc: decodeURIComponent(doc) };
}
const docIcon = paths => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const DOC_ICONS = {
  report: OPEN_ICON,   // a page of text
  brief: docIcon('<rect x="3.5" y="3" width="9" height="11.5" rx="1.2"/><path d="M6 1.8h4v2.4H6zM6 8h4.5M6 10.8h4.5"/>'),   // a clipboard
  outbox: docIcon('<path d="M2 9.5h3.2l1 1.8h3.6l1-1.8H14v3.8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/><path d="M8 8V1.8M5.5 4.2 8 1.8l2.5 2.4"/>'),   // a tray, arrow out
  context: docIcon('<path d="M10.5 4.5 5.6 9.4a1.4 1.4 0 0 0 2 2l5.2-5.2a2.8 2.8 0 0 0-4-4L3.6 7.4a4.2 4.2 0 0 0 6 6L13.5 9.5"/>'),   // a paperclip
};
const openDocAttrs =(at, title) => `data-open-report data-host="${esc(at.host)}" data-job="${esc(at.job)}" data-doc="${esc(at.doc)}" data-title="${title}"`;
// A step's documents: the title opens its latest report, the list holds every one the reader can open.
function docsList(list) {
  const open = list.map(entry => ({ entry, at: docRef(entry) })).filter(x => x.at);
  if (!open.length) return '';
  return `<details data-step-docs><summary>Documents <b>${open.length}</b></summary><ul>${open.map(({ entry, at }) => {
    const title = entry.title === null ? 'Title unknown' : esc(entry.title);
    return `<li><button ${openDocAttrs(at, title)} title="Read this ${esc(entry.kind)}: ${title}">${DOC_ICONS[entry.kind]}<span>${title}</span></button></li>`;
  }).join('')}</ul></details>`;
}

function render(flipped = new Set()) {
  el.dataset.level = bench ? 'bench' : room ? 'room' : page ?? 'floor';
  el.toggleAttribute('data-collapsed', collapsed);
  el.toggleAttribute('data-editing', editing !== null);
  const crumbs = `<div data-bench-head><nav id="benchBreadcrumb" aria-label="Breadcrumb"><button data-back-floor>Floor</button>${
    room ? ` / <button data-back-room>${esc(room.title)}</button>` : page && !bench ? ` / <span>${page === 'history' ? 'History' : page === 'decisions' ? 'Project decisions' : 'Constitution'}</span>` : ''}${bench ? ` / <span>${esc(bench.title)}</span>` : ''}</nav>
    <button data-collapse aria-expanded="${!collapsed}" title="${collapsed ? 'Show' : 'Hide'} the plan" aria-label="${collapsed ? 'Show' : 'Hide'} the plan"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${collapsed ? 'M6 3.5 10.5 8 6 12.5' : 'M3.5 6 8 10.5 12.5 6'}"/></svg></button></div>`;
  let content;
  if (bench) {
    content = `<h2>${esc(bench.title)}</h2><div class="slice-bench"><section data-plan aria-label="Plan wall">${bench.tasks.length ? '' : '<p data-empty>No tasks</p>'}<ol>${bench.tasks.map(task =>
      `<li data-task="${esc(task.id)}" data-flipped="${flipped.has(task.id)}" data-lane="${task.lane}" data-condition="${esc(task.condition)}" title="${esc(task.condition)}"><i></i>${esc(task.title)}${itemHistoryButton(task, true)}</li>`).join('')}</ol></section>
      <section aria-label="Criteria">${bench.criteria.length ? '' : '<p data-empty>No criteria</p>'}${bench.criteria.map(c => `<span data-verification="${c.verification}" data-state="${c.state}" aria-label="${c.verification} ${c.state}" title="${esc(c.text)}">${svg(kinds[c.verification])}</span>`).join('')}
      <p>${bench.progress.total === null ? 'Progress unknown' : `${bench.progress.complete} / ${bench.progress.total}`}</p></section>
      <section data-agents aria-label="Agents">${bench.agents.length > 5
        ? `<div data-agent-group data-count="${bench.agents.length}" aria-label="${bench.agents.length} agents">${figure}${figure}<b>${bench.agents.length}</b></div>`
        : bench.agents.map(agentMarkup).join('')}</section>
      <section data-desk aria-label="Question desk">${bench.attention.length ? `<span data-lantern aria-label="Open attention">${lantern}</span>` : '<p data-empty>Desk clear</p>'}</section>
      <details data-tray><summary>Reports <b>${bench.reports.length}</b></summary><ul>${bench.reports.map(report => {
        const at = docRef(report), title = report.title === null ? 'Title unknown' : esc(report.title);
        const where = reportWhere(report.canonical_location);
        return `<li data-availability="${esc(report.availability)}"><span data-report-title title="${title}">${title}</span>${at ? `<button ${openDocAttrs(at, title)} title="Read this report" aria-label="Read ${title}">${OPEN_ICON}</button>` : ''}
          <small>${esc(report.availability)} · <span title="${esc(report.canonical_location)}">${esc(where)}</span>${report.canonical_location ? `<button data-copy="${esc(report.canonical_location)}" title="Copy the full location" aria-label="Copy the full location">${COPY_ICON}</button>` : ''}</small></li>`;
      }).join('')}</ul></details>
      <section><button data-briefing aria-expanded="${briefing}">Briefing</button><div data-summary ${briefing ? '' : 'hidden'}>${bench.summary === null ? 'Summary unknown' : ['purpose', 'done', 'doing', 'next'].map(key => `<p><b>${key}</b> ${esc(bench.summary[key])}</p>`).join('')}</div></section></div>`;
  } else if (room) {
    content = epicPage(room);
  } else if (page === 'history') {
    content = '<div data-history-mount></div>';
  } else if (page === 'decisions') {
    content = `<article data-project-decisions>${decisionsPanel(decisionLists.get('project'))}</article>`;
  } else if (page === 'constitution') {
    content = `<article data-constitution-page>${guidancePanel('constitution', views.get('constitution'), guidanceState('constitution'))}${triagePolicyPanel(views.get('constitution'))}</article>`;
  } else {
    content = `<div class="epic-cards">${constitutionCard()}<article data-project-decisions-card><button data-open-decisions title="Read every decision in this project, including work outside epic rooms; opening changes no stored state">Project decisions</button></article>${rooms.map(epicCard).join('')}</div>`;
    if (!rooms.length) content += '<p>No epic rooms recorded.</p>';
  }
  if (room || bench) content = `<nav aria-label="Work item views">${itemHistoryButton(bench || room)}</nav>` + content;
  el.innerHTML = crumbs + ((!room && !bench) ? `<nav aria-label="Floor views"><button data-floor-overview aria-current="${page ? 'false' : 'page'}">Overview</button><button data-open-history aria-current="${page === 'history' ? 'page' : 'false'}">History</button></nav>` : '') + (guidanceFeedback ? `<p data-guidance-feedback role="status">${esc(guidanceFeedback)}</p>` : '') + content;
  if (page === 'history') mountRunHistory(el.querySelector('[data-history-mount]'), project);
}

async function saveGuidance() {
  const at = editing;
  at.saving = true; at.error = null;
  render();
  try {
    const view = await json('/api/guidance', { project, epic: at.kind === 'charter' ? at.key : null,
                                               markdown: at.text, base: at.base });
    if (editing !== at) return;
    views.set(at.key, view);
    editing = null;
    guidanceFeedback = `Saved ${at.kind} version ${view.guidance.version.number} as ${view.guidance.version.actor}. Previous versions remain in history.`;
    if (at.kind === 'constitution' && room) loadGuidance(room.id);   // what the charter inherits moved on
    if (at.kind === 'charter') loadDecisions(at.key);   // a charter now exists to promote into
  } catch (error) {
    if (editing !== at) return;
    at.saving = false; at.error = error.message;
    loadGuidance(at.key);   // a newer version may have come first; it shows once the edit is discarded
  }
  render();
  el.querySelector('[data-guidance-text]')?.focus();
  el.querySelector('[data-guidance-editor] [role="alert"]')?.scrollIntoView({ block: 'nearest' });   // below the fold under a long editor
}

async function promote(decision) {
  const epic = room.id;
  try {
    const view = await json('/api/guidance/promote', { epic, decision });
    views.set(epic, view);
    guidanceFeedback = `Promoted decision to charter version ${view.guidance.version.number}. It is now in decisions in force.`;
  } catch (error) {
    decisionLists.set(epic, { ...decisionLists.get(epic), error: error.message });
  }
  render();
  loadDecisions(epic);
}

// Typing goes into the editor's state, so a redraw (a failed save) keeps the text.
el.addEventListener('input', ev => {
  if (editing && ev.target.matches('[data-guidance-text]')) editing.text = ev.target.value;
});

// Every editor exit uses the same check; cancelling leaves the text and route untouched.
function discardGuidance() {
  if (!editing) return true;
  if (editing.saving) return false;
  if ((editing.text.trim() || editing.text !== editing.original)
      && !window.confirm(`Discard the unsaved ${editing.kind} edit? Its text cannot be recovered. No new version will be saved.`)) return false;
  guidanceFeedback = `Discarded the ${editing.kind} edit. No new version was saved.`;
  editing = null;
  return true;
}

// Guidance clicks; true when the click was one of them.
function guidanceClick(target) {
  if (target.closest('[data-open-constitution]')) { if (!discardGuidance()) return true; room = bench = null; page = 'constitution'; render(); loadGuidance('constitution'); return true; }
  const edit = target.closest('[data-guidance-edit]');
  if (edit) {
    const key = guidanceKey(edit.dataset.guidanceEdit), view = views.get(key);
    guidanceFeedback = null;
    editing = { key, kind: edit.dataset.guidanceEdit, original: view.guidance ? view.markdown : '', text: view.guidance ? view.markdown : '',
                base: view.guidance ? view.guidance.version.number : 0, saving: false, error: null };
    render();
    el.querySelector('[data-guidance-text]')?.focus();
    return true;
  }
  if (target.closest('[data-guidance-save]')) { saveGuidance(); return true; }
  if (target.closest('[data-guidance-cancel]')) { if (discardGuidance()) render(); return true; }
  const history = target.closest('[data-guidance-history]');
  if (history) {
    const key = guidanceKey(history.dataset.guidanceHistory);
    if (!historyOpen.delete(key)) historyOpen.add(key);
    render();
    return true;
  }
  const version = target.closest('[data-guidance-open]');
  if (version) {
    const kind = version.dataset.guidanceOpen, number = version.dataset.version;
    const label = kind === 'charter' ? `Charter: ${room.title}` : 'Constitution';
    openStoredReader(guidanceUrl(guidanceKey(kind), number), { id: `${kind} version ${number}`,
      name: `${label} · version ${number}`, kind: 'file' }, null);
    return true;
  }
  const decision = target.closest('[data-promote]');
  if (decision) { if (discardGuidance()) { guidanceFeedback = null; promote(decision.dataset.promote); } return true; }
  return false;
}

el.addEventListener('click', async ev => {
  const history = ev.target.closest('[data-work-history]');
  if (history) { openItemHistory({ id: history.dataset.workHistory, title: history.dataset.title, kind: 'work item', status: history.dataset.status, project }); return; }
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
  const chip = ev.target.closest('[data-job-chip]');
  if (chip) { if (workOf(chip.dataset.jobChip)) select(chip.dataset.jobChip); return; }
  if (collapsed && ev.target.closest('#benchBreadcrumb button')) { collapsed = false; store('localStorage', 'fleet.bench.collapsed', '0'); }
  if (ev.target.closest('[data-collapse]')) {
    collapsed = !collapsed;
    store('localStorage', 'fleet.bench.collapsed', collapsed ? '1' : '0');
    render();
    return;
  }
  if (ev.target.closest('[data-open-history]')) { if (!discardGuidance()) return; ++revision; room = bench = null; page = 'history'; render(); return; }
  if (ev.target.closest('[data-open-decisions]')) { page = 'decisions'; render(); loadDecisions('project'); return; }
  if (guidanceClick(ev.target)) return;
  if (ev.target.closest('[data-back-floor], [data-floor-overview]')) { if (!discardGuidance()) return; ++revision; room = bench = page = editing = null; render(); return; }
  if (ev.target.closest('[data-back-room]')) { if (!discardGuidance()) return; ++revision; bench = editing = null; render(); return; }
  const epic = ev.target.closest('[data-epic]'), slice = ev.target.closest('[data-slice]');
  if (epic) { if (!discardGuidance()) return; editing = null; openRoom(rooms.find(r => r.id === epic.dataset.epic)); }
  if (slice) {
    if (!discardGuidance()) return;
    const request = ++revision;
    try {
      const doc = await read(slice.dataset.slice);
      if (request !== revision) return;
      bench = doc; briefing = false; editing = null; render();
    } catch (error) {
      if (request === revision) el.innerHTML += `<p role="alert">${esc(error.message)}</p>`;
    }
  }
});

// Capture at window so the building cannot consume this same Escape.
window.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || el.hidden) return;
  if (!document.getElementById('reader').hidden || document.getElementById('panel').hasAttribute('data-archived')) return;
  // A milestone with no epic above it opens with no room; stepping back from it lands on the floor list.
  if (!room && !bench && !page) { enterFloor(null); return; }
  ev.stopImmediatePropagation();
  ev.preventDefault();
  if (editing) {
    if (!discardGuidance()) return;
  } else if (bench) bench = null; else if (room) room = null; else page = null;
  ++revision;
  render();
}, { capture: true });
