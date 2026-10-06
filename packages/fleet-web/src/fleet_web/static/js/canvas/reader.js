// The deck's document reader on the canvas: the same sheet, type and renderer, opened on a job's documents.
import { age, clamp, esc, store } from '../util.js';
import { rethemeDiagrams } from '../rich.js';
import { DOC_KIND, THEME_ICON, WIDE, documentClick, errorMarkup, fitTables, jobDocSequence, kindOf, loadingMarkup,
  renderDocument, showFullscreen, syncTocMode } from '../doc-render.js';

const reader = document.getElementById('reader');
const sheet = reader.querySelector('.rd-sheet');
const body = document.getElementById('rdBody');
const progress = document.getElementById('rdProgress');
// one open document at a time: its job, the job's documents in the deck's order, and what was read
const rd = { host: null, job: null, agent: null, list: [], doc: null, data: null, req: 0, lastFocus: null, raf: 0 };

// Opens `id` among `documents`, the job's list as /api/job-documents returned it.
export function openJobDocument(host, job, documents, id, agent = null) {
  rd.host = host; rd.job = job; rd.agent = agent;
  rd.list = jobDocSequence({ documents });
  rd.doc = rd.list.find(doc => doc.id === id) || documents.find(doc => doc.id === id);
  if (!rd.doc) return;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  sheet.focus();
  load();
}

export function closeReader() {
  if (reader.hidden) return;
  rd.req++;
  reader.hidden = true;
  body.innerHTML = '';
  if (rd.lastFocus && rd.lastFocus.isConnected) rd.lastFocus.focus();
}

async function load() {
  const req = ++rd.req;
  rd.data = null;
  renderHead();
  progress.style.transform = 'scaleX(0)';
  body.innerHTML = loadingMarkup();
  body.scrollTop = 0;
  try {
    const response = await fetch('/api/doc?' + new URLSearchParams({ host: rd.host, job: rd.job, id: rd.doc.id }));
    const data = await response.json();
    if (req !== rd.req) return;
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    rd.data = data;
    renderHead();
    renderDocument(body, data, { assetUrl, theme: sheet.dataset.theme });
    onScroll();
  } catch (error) {
    if (req !== rd.req) return;
    body.innerHTML = errorMarkup(error.message, 'rdRetry');
    document.getElementById('rdRetry').addEventListener('click', load);
  }
}

// images resolve beside the document, under the job's own roots
function assetUrl(path) {
  const params = new URLSearchParams({ host: rd.host, job: rd.job, id: rd.doc.id, path });
  if (rd.data?.media === 'image') params.set('v', `${rd.data.mtime ?? rd.doc.mtime}-${rd.data.size ?? rd.doc.size}`);
  return '/api/doc/asset?' + params;
}

function renderHead() {
  const doc = rd.doc, d = rd.data || {}, kind = kindOf(doc);
  const kindEl = document.getElementById('rdKind');
  kindEl.className = 'rd-kind ' + kind;
  kindEl.textContent = DOC_KIND[kind].label;
  document.getElementById('rdTitle').textContent = d.name || doc.name;
  const step = d.step ?? doc.step;
  document.getElementById('rdMeta').innerHTML = [
    `<span title="${esc(d.job_description || '')}">${esc(rd.host)} · ${esc(rd.job)}${d.agent || rd.agent ? ' · ' + esc(d.agent || rd.agent) : ''}</span>`,
    step != null ? `<span>step ${step + 1}</span>` : '',
    d.minutes && d.media !== 'image' ? `<span>${d.minutes} min read</span>` : '',
    (d.mtime || doc.mtime) ? `<span>updated ${age(d.mtime || doc.mtime)} ago</span>` : '',
  ].join('');
  const image = d.media === 'image' || doc.media === 'image';
  const full = document.getElementById('rdFull');
  full.hidden = !image || !document.fullscreenEnabled;
  full.disabled = !rd.data;
  const at = rd.list.findIndex(x => x.id === doc.id);
  document.getElementById('rdStep').hidden = rd.list.length < 2;
  document.getElementById('rdPos').textContent = at >= 0 ? `${at + 1} / ${rd.list.length}` : '';
  document.getElementById('rdPrev').disabled = at <= 0;
  document.getElementById('rdNext').disabled = at < 0 || at >= rd.list.length - 1;
}

function step(delta) {
  const at = rd.list.findIndex(x => x.id === rd.doc.id);
  const next = rd.list[at + delta];
  if (at < 0 || !next) return;
  rd.doc = next;
  load();
}

function setTheme(theme) {
  sheet.dataset.theme = theme;
  rethemeDiagrams(body, theme);
  const button = document.getElementById('rdTheme');
  button.innerHTML = THEME_ICON[theme];
  button.setAttribute('aria-label', theme === 'dark' ? 'Switch to the paper theme' : 'Switch to the dark theme');
}

// the progress bar follows the reading position
function onScroll() {
  rd.raf = 0;
  const top = body.scrollTop, max = body.scrollHeight - body.clientHeight;
  progress.style.transform = `scaleX(${max > 0 ? clamp(top / max, 0, 1) : 1})`;
}

// the reading theme is shared with the deck
setTheme(store('localStorage', 'fleet.reader.theme') === 'paper' ? 'paper' : 'dark');
document.getElementById('rdTheme').addEventListener('click', () => {
  const theme = sheet.dataset.theme === 'dark' ? 'paper' : 'dark';
  setTheme(theme);
  store('localStorage', 'fleet.reader.theme', theme);
});
document.getElementById('rdPrev').addEventListener('click', () => step(-1));
document.getElementById('rdNext').addEventListener('click', () => step(1));
document.getElementById('rdFull').addEventListener('click', () => {
  const img = body.querySelector('.prose img');
  if (img) showFullscreen(img);
});
reader.addEventListener('click', ev => { if (ev.target.closest('[data-close]')) closeReader(); });
body.addEventListener('click', documentClick);
body.addEventListener('scroll', () => { if (!rd.raf) rd.raf = requestAnimationFrame(onScroll); }, { passive: true });
WIDE.addEventListener('change', () => syncTocMode(body));
window.addEventListener('resize', () => { if (!reader.hidden) fitTables(body); });
document.addEventListener('keydown', ev => {
  if (reader.hidden || document.fullscreenElement) return;
  if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closeReader(); return; }
  if ((ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') && !ev.altKey && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey
      && !ev.target.closest('input, textarea, select, [contenteditable]')) {
    ev.preventDefault();
    step(ev.key === 'ArrowLeft' ? -1 : 1);
  }
}, true);
