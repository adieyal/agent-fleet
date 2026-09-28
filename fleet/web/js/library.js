// Project library: configured local Markdown, each project's working documents, and every job's documents
// from the project's store on this machine, readable after the job has left the floor or its host.

import { age, esc } from './util.js';
import { DOC_KIND, fmtSize, kindOf } from './docs3d.js';
import { openLibraryReader, openStoredReader } from './reader.js';

// ------------------------------------------------------------------ project library
export const libraryPane = document.getElementById('libraryPane');
const libSearch = document.getElementById('libSearch');
export let libraryDocs = [], libraryFocus = null;
let libraryProjects = [];
// what a job's copy says about where the job is now; its documents read from the store either way
const WHERE = { 'on host': '', 'gone from host': 'left its host', 'host offline': 'host offline' };
const DOC_ORDER = { brief: 0, context: 1, report: 2, file: 3, outbox: 4 };
export function closeLibrary() {
  if (libraryPane.hidden) return;
  libraryPane.hidden = true;
  libraryFocus?.focus();
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
function renderLibrary() {
  const query = libSearch.value.trim().toLowerCase();
  const has = text => (text || '').toLowerCase().includes(query);
  const list = document.getElementById('libList');
  const sections = groups().map(g => {
    const docs = g.docs.filter(d => has(`${d.project} ${d.title} ${d.id}`));
    const working = g.working.filter(d => has(d.id));
    const jobs = g.jobs.map(job => ({ job, docs: has(job.description) ? job.documents : job.documents.filter(d => has(d.name)) }))
      .filter(entry => entry.docs.length);
    if (!docs.length && !working.length && !jobs.length) return '';
    return `<section class="lib-group"><h3>${esc(g.name)}</h3>
      ${docs.map(d => `<button class="lib-doc" data-project="${esc(d.project)}" data-id="${esc(d.id)}">
        <i aria-hidden="true">▤</i><span><b>${esc(d.title)}</b><small>${esc(d.id)} · ${esc(fmtSize(d.size))}</small></span></button>`).join('')}
      ${working.length ? `<h4>Working</h4>${working.map(d => `<button class="lib-doc" data-working="${esc(g.id)}" data-id="${esc(d.id)}">
        <i aria-hidden="true">✎</i><span><b>${esc(d.name)}</b><small>${esc(d.id)} · ${esc(fmtSize(d.size))} · ${esc(age(d.mtime))} ago</small></span></button>`).join('')}` : ''}
      ${jobs.length ? `<h4>Job documents · ${jobs.length}</h4>${jobs.map(({ job, docs }) => jobHtml(g.id, job, docs)).join('')}` : ''}
    </section>`;
  }).join('');
  list.innerHTML = sections || `<p class="lib-empty">${libraryDocs.length || libraryProjects.length ? 'No matching documents.'
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
async function showLibrary() {
  libraryFocus = document.activeElement;
  libraryPane.hidden = false;
  document.getElementById('libList').innerHTML = '<p class="lib-empty">Loading documents…</p>';
  libSearch.focus();
  try {
    const response = await fetch('/api/library');
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    libraryDocs = body.documents || [];
    libraryProjects = body.projects || [];
    renderLibrary();
  } catch (error) {
    document.getElementById('libList').innerHTML = `<p class="lib-empty">Couldn’t load the library: ${esc(error.message)}</p>`;
  }
}
document.getElementById('libraryOpen').addEventListener('click', showLibrary);
libraryPane.addEventListener('click', ev => {
  if (ev.target.closest('[data-lib-close]')) { closeLibrary(); return; }
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
libSearch.addEventListener('input', renderLibrary);
