// Project library. Overview (the default): one project's workstreams, what each has done, what comes next and
// what needs you, with links into the documents behind it. All documents: every document as a folder tree, each
// project's working documents, and every job's documents from the project's store, with a text filter.

import { age, esc, store } from './util.js';
import { DOC_KIND, fmtSize, kindOf } from './docs3d.js';
import { openLibraryReader, openStoredReader } from './reader.js';

// ------------------------------------------------------------------ project library
export const libraryPane = document.getElementById('libraryPane');
const libSearch = document.getElementById('libSearch');
const libList = document.getElementById('libList');
const libProjects = document.getElementById('libProjects');
export let libraryDocs = [], libraryFocus = null;
let libraryProjects = [], overviews = [], view = 'overview', loading = null, refreshTimer = 0;
let chosen = store('localStorage', 'fleet.library.project');
const openCards = new Set(), openFolders = new Set();
// what a job's copy says about where the job is now; its documents read from the store either way
const WHERE = { 'on host': '', 'gone from host': 'left its host', 'host offline': 'host offline' };
const DOC_ORDER = { brief: 0, context: 1, report: 2, file: 3, outbox: 4 };
// The workspace PRD's text rules: a workstream's headline is 12 words or fewer; its briefing (needs you, next,
// done) shows up to three lines each by default and folds the rest away.
const HEADLINE_WORDS = 12, LIST_LIMIT = 3;
function headline(text) {
  const words = text.split(/\s+/);
  return words.length > HEADLINE_WORDS ? words.slice(0, HEADLINE_WORDS).join(' ') + '…' : text;
}
export function closeLibrary() {
  if (libraryPane.hidden) return;
  libraryPane.hidden = true;
  libraryFocus?.focus();
}

// ------------------------------------------------------------------ overview
const traces = new Map();   // trace id in the markup → trace, rebuilt with every render
function traceButton(trace, label, cls = 'tr') {
  if (!trace) return `<span class="${cls} none">${esc(label)}</span>`;
  const id = String(traces.size);
  traces.set(id, trace);
  return `<button class="${cls}" data-trace="${id}" title="Open ${esc(trace.name)}">${esc(label)}</button>`;
}
function itemList(items, empty) {
  if (!items.length) return `<p class="ws-empty">${empty}</p>`;
  const li = item => `<li>${traceButton(item.trace, item.label)}</li>`;
  return `<ul class="ws-list">${items.slice(0, LIST_LIMIT).map(li).join('')}</ul>${items.length > LIST_LIMIT
    ? `<details class="ws-more"><summary>${items.length - LIST_LIMIT} more</summary><ul class="ws-list">${items.slice(LIST_LIMIT).map(li).join('')}</ul></details>` : ''}`;
}
function facts(ws) {
  const running = ws.jobs.filter(job => job.status === 'running').length;
  return [ws.stories ? `${ws.stories.passing}/${ws.stories.total} stories` : '',
    ws.questions && ws.questions.open ? `${ws.questions.open} open question${ws.questions.open === 1 ? '' : 's'}` : '',
    running ? `${running} job${running === 1 ? '' : 's'} running` : ws.jobs.length ? `${ws.jobs.length} job${ws.jobs.length === 1 ? '' : 's'}` : '',
  ].filter(Boolean).join(' · ');
}
function cardHtml(ws) {
  const key = `${chosen}:${ws.id}`;
  return `<details class="ws st-${esc(ws.state.replace(' ', '-'))}" data-ws="${esc(key)}"${openCards.has(key) ? ' open' : ''}>
    <summary title="${esc(ws.title)}"><span class="ws-head"><b>${esc(headline(ws.title))}</b><span class="ws-state">${esc(ws.state)}</span></span>
      <span class="ws-facts">${esc(facts(ws))}</span></summary>
    <div class="ws-body">
      ${ws.summary ? `<p class="ws-sub">${esc(ws.summary)}</p>` : ''}
      ${ws.needs.length ? `<h5>Needs you</h5>${itemList(ws.needs, '')}` : ''}
      <h5>Next</h5>${itemList(ws.next, 'Nothing planned.')}
      <h5>Done</h5>${itemList(ws.done, 'Nothing done yet.')}
      <h5>Traces</h5><div class="ws-traces">${ws.traces.map(t => traceButton(t.trace, t.label, 'chip-tr')).join('')}</div>
    </div></details>`;
}
function renderOverview() {
  traces.clear();
  const project = overviews.find(p => key(p) === chosen) || overviews[0];
  if (!project) {
    libList.innerHTML = '<p class="lib-empty">No projects yet. Jobs’ documents collect here as they are written; add a project’s repository or Ralph folder with <code>fleet library add PROJECT /path</code>.</p>';
    return;
  }
  chosen = key(project);
  const streams = project.workstreams;
  const active = streams.filter(ws => ws.state === 'in progress');
  const waiting = streams.filter(ws => ws.state === 'blocked' || ws.state === 'planned' || ws.state === 'unknown');
  const done = streams.filter(ws => ws.state === 'done');
  libList.innerHTML = `<section class="ov">
    <p class="ov-summary">${esc(project.summary)}</p>
    ${active.length ? `<h4>Active now</h4>${active.map(cardHtml).join('')}` : ''}
    ${waiting.length ? `<h4>Blocked or planned</h4>${waiting.map(cardHtml).join('')}` : ''}
    ${done.length ? `<details class="ov-done" data-fold="done"${openCards.has(chosen + ':done') ? ' open' : ''}><summary><h4>Done · ${done.length}</h4></summary>${done.map(cardHtml).join('')}</details>` : ''}
    ${project.other_work.length ? `<h4>Other work</h4>${project.other_work.map(week => `<div class="ov-week"><h5>${esc(week.week)}</h5><ul class="ws-list">${
      week.jobs.map(job => `<li>${traceButton(job.report, `${job.description} · ${job.status}${WHERE[job.availability] ? ' · ' + WHERE[job.availability] : ''}`)}</li>`).join('')}</ul></div>`).join('')}` : ''}
    ${project.other_documents.length ? `<details class="ov-docs" data-fold="docs"${openCards.has(chosen + ':docs') ? ' open' : ''}><summary><h4>${
      streams.some(ws => !ws.id.startsWith('job:')) ? 'Other documents' : 'Documents'} · ${project.other_documents.reduce((n, g) => n + g.documents.length, 0)}</h4></summary>${
      project.other_documents.map(group => `<div class="ov-week"><h5>${esc(group.folder === '.' ? 'top level' : group.folder + '/')}</h5><ul class="ws-list">${
        group.documents.map(doc => `<li>${traceButton(doc.trace, doc.label)}</li>`).join('')}</ul></div>`).join('')}</details>` : ''}
  </section>`;
}
function key(project) { return project.project_id || 'library:' + project.library; }
function renderProjects() {
  const shown = view === 'overview' && overviews.length > 1;
  libProjects.hidden = !shown;
  libProjects.innerHTML = shown ? overviews.map(p => `<button data-project-key="${esc(key(p))}" aria-pressed="${key(p) === chosen}">${esc(p.name)}</button>`).join('') : '';
}

// ------------------------------------------------------------------ all documents
function treeHtml(docs, has) {
  // folders before files, each level alphabetical; a filter opens every folder that still holds a match
  const root = { folders: new Map(), files: [] };
  for (const d of docs) {
    const parts = d.id.split('/');
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), files: [] });
      node = node.folders.get(part);
    }
    node.files.push(d);
  }
  const count = node => node.files.length + [...node.folders.values()].reduce((n, f) => n + count(f), 0);
  const level = (node, path) => [...node.folders.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, folder]) => {
    const id = `${path}${name}/`;
    return `<details class="tree-folder" data-folder="${esc(id)}"${has || openFolders.has(id) ? ' open' : ''}><summary><span>${esc(name)}/</span><small>${count(folder)}</small></summary>
      <div class="tree-in">${level(folder, id)}</div></details>`;
  }).join('') + node.files.sort((a, b) => a.name.localeCompare(b.name)).map(d => `<button class="lib-doc tree-doc" data-project="${esc(d.project)}" data-id="${esc(d.id)}">
      <i aria-hidden="true">${d.kind === 'prd' ? '☰' : '▤'}</i><span><b>${esc(d.kind === 'prd' ? d.name : d.title)}</b><small>${esc(d.name)} · ${esc(fmtSize(d.size))}</small></span></button>`).join('');
  return level(root, docs[0] ? docs[0].project + ':' : '');
}
function groups() {
  const byKey = new Map();
  const group = (key, name) => {
    if (!byKey.has(key)) byKey.set(key, { name, docs: [], working: [], jobs: [], id: null });
    return byKey.get(key);
  };
  for (const doc of libraryDocs) group(doc.project_id || 'library:' + doc.project, doc.project).docs.push(doc);
  for (const project of libraryProjects) {
    const g = group(project.id, project.name || project.id);
    g.name = project.name || g.name;
    g.id = project.id; g.working = project.working; g.jobs = project.jobs;
  }
  return [...byKey.values()];
}
function renderAll() {
  const query = libSearch.value.trim().toLowerCase();
  const has = text => (text || '').toLowerCase().includes(query);
  const sections = groups().map(g => {
    const docs = g.docs.filter(d => has(`${d.project} ${d.title} ${d.id}`));
    const working = g.working.filter(d => has(d.id));
    const jobs = g.jobs.map(job => ({ job, docs: has(job.description) ? job.documents : job.documents.filter(d => has(d.name)) }))
      .filter(entry => entry.docs.length);
    if (!docs.length && !working.length && !jobs.length) return '';
    return `<section class="lib-group"><h3>${esc(g.name)}</h3>
      <div class="tree">${treeHtml(docs, !!query)}</div>
      ${working.length ? `<h4>Working</h4>${working.map(d => `<button class="lib-doc" data-working="${esc(g.id)}" data-id="${esc(d.id)}">
        <i aria-hidden="true">✎</i><span><b>${esc(d.name)}</b><small>${esc(d.id)} · ${esc(fmtSize(d.size))} · ${esc(age(d.mtime))} ago</small></span></button>`).join('')}` : ''}
      ${jobs.length ? `<h4>Job documents · ${jobs.length}</h4>${jobs.map(({ job, docs }) => jobHtml(g.id, job, docs)).join('')}` : ''}
    </section>`;
  }).join('');
  libList.innerHTML = sections || `<p class="lib-empty">${libraryDocs.length || libraryProjects.length ? 'No matching documents.'
    : 'No project documents yet. Jobs’ documents collect here as they are written; add a project’s repository with <code>fleet library add PROJECT /path/to/repo</code>.'}</p>`;
}
function jobHtml(projectId, job, docs) {
  const where = WHERE[job.availability] ?? job.availability;
  const sorted = docs.slice().sort((a, b) => (DOC_ORDER[a.kind] ?? 9) - (DOC_ORDER[b.kind] ?? 9) || (a.step ?? -1) - (b.step ?? -1));
  return `<div class="lib-job${job.availability === 'host offline' ? ' offline' : ''}" data-job="${esc(job.key)}">
    <div class="lib-job-head"><b>${esc(job.description)}</b>
      <small>${esc(job.host)} · <span class="st-${esc(job.status)}">${esc(job.status)}</span> · ${esc(new Date((job.created_at || 0) * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }))}${
        where ? ` · <span class="where">${esc(where)}</span>` : ''}</small></div>
    ${sorted.map(d => {
      const kind = kindOf(d);
      return `<button class="lib-doc" data-stored="${esc(projectId)}" data-job="${esc(job.key)}" data-id="${esc(d.id)}"${d.stored ? '' : ' disabled'}
        title="${d.stored ? `Read ${esc(d.name)}` : `No copy yet${d.error ? `: ${esc(d.error)}` : ''}`}">
        <i class="dk ${kind}" aria-hidden="true">${DOC_KIND[kind].glyph}</i><span><b>${esc(d.name)}</b><small>${
          esc(DOC_KIND[kind].label.toLowerCase())}${d.step != null ? ` · step ${d.step + 1}` : ''}${d.size != null ? ` · ${esc(fmtSize(d.size))}` : ''}${
          d.stored ? '' : ` · <span class="where">not copied${d.error ? ': ' + esc(d.error) : ' yet'}</span>`}</small></span></button>`;
    }).join('')}</div>`;
}

// ------------------------------------------------------------------ loading and events
function render() {
  const scroll = libList.scrollTop;
  for (const b of libraryPane.querySelectorAll('[data-lib-view]')) b.setAttribute('aria-pressed', String(b.dataset.libView === view));
  libSearch.hidden = view !== 'all';
  renderProjects();
  if (view === 'overview') renderOverview(); else renderAll();
  libList.scrollTop = scroll;
}
async function load() {
  const [listing, overview] = await Promise.all([fetch('/api/library'), fetch('/api/library/overview')]);
  const body = await listing.json(), summary = await overview.json();
  if (!listing.ok) throw new Error(body.error || `HTTP ${listing.status}`);
  if (!overview.ok) throw new Error(summary.error || `HTTP ${overview.status}`);
  libraryDocs = body.documents || [];
  libraryProjects = body.projects || [];
  overviews = summary.projects || [];
}
async function showLibrary() {
  libraryFocus = document.activeElement;
  libraryPane.hidden = false;
  libList.innerHTML = '<p class="lib-empty">Loading documents…</p>';
  libraryPane.querySelector(`[data-lib-view="${view}"]`).focus();
  try {
    await (loading = load());
    render();
  } catch (error) {
    libList.innerHTML = `<p class="lib-empty">Couldn’t load the library: ${esc(error.message)}</p>`;
  }
}
// Documents and jobs change while the pane is open: fetch again a moment after a state update, keeping what is open.
export function refreshLibrary() {
  if (libraryPane.hidden || refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = 0;
    try { await load(); render(); } catch (error) { /* the open view stays as it was; the next update tries again */ }
  }, 2000);
}
document.getElementById('libraryOpen').addEventListener('click', showLibrary);
libraryPane.addEventListener('toggle', ev => {
  const card = ev.target.closest('[data-ws],[data-fold],[data-folder]');
  if (!card || card !== ev.target) return;
  const id = card.dataset.ws || card.dataset.folder || `${chosen}:${card.dataset.fold}`;
  const set = card.dataset.folder ? openFolders : openCards;
  if (card.open) set.add(id); else set.delete(id);
}, true);
libraryPane.addEventListener('click', ev => {
  if (ev.target.closest('[data-lib-close]')) { closeLibrary(); return; }
  const viewButton = ev.target.closest('[data-lib-view]');
  if (viewButton) { view = viewButton.dataset.libView; render(); return; }
  const projectButton = ev.target.closest('[data-project-key]');
  if (projectButton) { chosen = projectButton.dataset.projectKey; store('localStorage', 'fleet.library.project', chosen); render(); return; }
  const traceLink = ev.target.closest('[data-trace]');
  if (traceLink) { openTrace(traces.get(traceLink.dataset.trace)); return; }
  const button = ev.target.closest('.lib-doc');
  if (!button || button.disabled) return;
  const { stored, working, job: jobKey, id } = button.dataset;
  if (stored) {
    const job = libraryProjects.find(p => p.id === stored)?.jobs.find(j => j.key === jobKey);
    const doc = job?.documents.find(d => d.id === id);
    if (doc) openStoredReader('/api/library/job?' + new URLSearchParams({ project: stored, job: jobKey, id }), doc, job);
    return;
  }
  if (working) {
    const doc = libraryProjects.find(p => p.id === working)?.working.find(d => d.id === id);
    if (doc) openStoredReader('/api/library/working?' + new URLSearchParams({ project: working, id }), doc, null);
    return;
  }
  const doc = libraryDocs.find(d => d.project === button.dataset.project && d.id === id);
  if (doc) openLibraryReader(doc);
});
function openTrace(trace) {
  if (!trace) return;
  if (trace.source === 'library') {
    const doc = libraryDocs.find(d => d.project === trace.project && d.id === trace.id);
    openLibraryReader(doc || { project: trace.project, id: trace.id, name: trace.name, title: trace.name, kind: trace.kind });
  } else {
    openStoredReader('/api/library/job?' + new URLSearchParams({ project: trace.project, job: trace.job, id: trace.id }),
      { id: trace.id, name: trace.name, kind: trace.kind }, { host: trace.host, id: trace.job_id, description: trace.description, agent: trace.agent });
  }
}
libSearch.addEventListener('input', () => { if (view === 'all') renderAll(); });
