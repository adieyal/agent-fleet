// Local project library: list and open configured Markdown.

import { esc } from './util.js';
import { fmtSize } from './docs3d.js';
import { openLibraryReader } from './reader.js';

// ------------------------------------------------------------------ local project library
export const libraryPane = document.getElementById('libraryPane');
const libSearch = document.getElementById('libSearch');
export let libraryDocs = [], libraryFocus = null;
export function closeLibrary() {
  if (libraryPane.hidden) return;
  libraryPane.hidden = true;
  libraryFocus?.focus();
}
function renderLibrary() {
  const query = libSearch.value.trim().toLowerCase();
  const docs = libraryDocs.filter(d => `${d.project} ${d.title} ${d.id}`.toLowerCase().includes(query));
  const list = document.getElementById('libList');
  if (!docs.length) {
    list.innerHTML = `<p class="lib-empty">${libraryDocs.length ? 'No matching documents.' : 'No project libraries configured. Add one with <code>fleet library add PROJECT /path/to/repo</code>.'}</p>`;
    return;
  }
  const groups = new Map();
  for (const doc of docs) {
    if (!groups.has(doc.project)) groups.set(doc.project, []);
    groups.get(doc.project).push(doc);
  }
  list.innerHTML = [...groups].map(([project, items]) => `<section class="lib-group"><h3>${esc(project)}</h3>
    ${items.map(d => `<button class="lib-doc" data-project="${esc(d.project)}" data-id="${esc(d.id)}">
      <i aria-hidden="true">▤</i><span><b>${esc(d.title)}</b><small>${esc(d.id)} · ${esc(fmtSize(d.size))}</small></span></button>`).join('')}</section>`).join('');
}
async function showLibrary() {
  libraryFocus = document.activeElement;
  libraryPane.hidden = false;
  document.getElementById('libList').innerHTML = '<p class="lib-empty">Loading documents…</p>';
  libSearch.focus();
  try {
    const response = await fetch('/api/library');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    libraryDocs = (await response.json()).documents || [];
    renderLibrary();
  } catch (error) {
    document.getElementById('libList').innerHTML = `<p class="lib-empty">Couldn’t load the library: ${esc(error.message)}</p>`;
  }
}
document.getElementById('libraryOpen').addEventListener('click', showLibrary);
libraryPane.addEventListener('click', ev => {
  if (ev.target.closest('[data-lib-close]')) { closeLibrary(); return; }
  const button = ev.target.closest('.lib-doc');
  if (!button) return;
  const doc = libraryDocs.find(d => d.project === button.dataset.project && d.id === button.dataset.id);
  if (doc) openLibraryReader(doc);
});
libSearch.addEventListener('input', renderLibrary);
