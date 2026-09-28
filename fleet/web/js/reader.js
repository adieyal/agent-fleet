// Document reader: loads a job or library document and renders it with a table of contents.

import { DEMO, REDUCED } from './env.js';
import { age, clamp, esc, store } from './util.js';
import { hostLook } from './looks.js';
import { DOC_KIND, kindOf } from './docs3d.js';
import { hideDocTip } from './camera.js';
import { fallbackCopy } from './panel.js';
import { libraryDocs } from './library.js';
import { demoDoc } from './demo.js';

// ------------------------------------------------------------------ reader
export const reader = document.getElementById('reader');
const rdSheet = reader.querySelector('.rd-sheet');
const rdBody = document.getElementById('rdBody');
const WIDE = matchMedia('(min-width: 1101px)');
const rdProgress = document.getElementById('rdProgress');
const rd = { key: null, source: 'job', host: null, job: null, doc: null, data: null, req: 0, raf: 0, lastFocus: null, tocLinks: [], tocCurrent: null };
const THEME_ICON = {
  dark: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/></svg><span class="lb">Paper</span>',
  paper: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z"/></svg><span class="lb">Dark</span>',
};

function setReaderTheme(theme) {
  rdSheet.dataset.theme = theme;
  const b = document.getElementById('rdTheme');
  b.innerHTML = THEME_ICON[theme];
  b.setAttribute('aria-label', theme === 'dark' ? 'Switch to the paper theme' : 'Switch to the dark theme');
}
setReaderTheme(store('localStorage','fleet.reader.theme') === 'paper' ? 'paper' : 'dark');

export function openReader(e, doc) {
  hideDocTip();
  rd.req++;
  rd.key = `${e.host}:${e.job.id}:${doc.id}`;
  rd.source = 'job';
  rd.host = e.host; rd.job = e.job; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderLoading();
  rdSheet.focus();
  loadDoc(rd.req);
}
export function openLibraryReader(doc) {
  if (!reader.hidden) saveReaderScroll();
  rd.req++;
  rd.key = `library:${doc.project}:${doc.id}`;
  rd.source = 'library';
  rd.host = null; rd.job = { id: 'library', description: doc.project }; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderLoading();
  rdSheet.focus();
  loadDoc(rd.req);
}
export function closeReader() {
  if (reader.hidden) return;
  saveReaderScroll();
  rd.req++; rd.key = null;
  reader.hidden = true;
  if (rd.lastFocus && rd.lastFocus.focus) rd.lastFocus.focus();
}
export function openAttentionReader(item) {
  rd.req++;
  rd.key = `attention:${item.id}`;
  rd.source = 'attention';
  rd.doc = { id: item.id, name: item.summary, kind: 'file' };
  const lines = [item.summary, `Source: ${item.source}`, `Context: ${item.context_reference}`,
    `State: ${item.state}`, `Last seen: ${new Date(item.last_seen * 1000).toISOString()}`];
  rd.data = { name: item.summary, markdown: lines.join('\n\n'), html: lines.map(line => `<p>${esc(line)}</p>`).join(''), toc: [] };
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderBody();
  rdSheet.focus();
  if (item.kind === 'decision') loadDecision(item.id, rd.req);
}
async function loadDecision(id, req) {
  try {
    const res = await fetch('/api/decision?' + new URLSearchParams({ id }));
    const detail = await res.json();
    if (!res.ok) throw new Error(detail.error);
    if (req !== rd.req) return;
    const prose = rdBody.querySelector('.prose');
    prose.innerHTML = `<h2>${esc(detail.question)}</h2><p class="decision-context">${esc(detail.context)}</p>
      ${detail.proposal === null ? '' : `<h3>Proposed change</h3><pre>${esc(detail.proposal.change)}</pre><p>${esc(detail.proposal.reason)}</p>`}
      <form class="decision-answer">
        ${detail.options.length ? `<fieldset><legend>Choices</legend>${detail.options.map(option =>
          `<label><input type="radio" name="choice" value="${esc(option)}"> ${esc(option)}</label>`).join('')}</fieldset>` : ''}
        <label>Your answer<textarea name="answer" rows="4" required></textarea></label>
        <button type="submit">Submit answer</button><p role="alert"></p><p role="status"></p>
      </form>`;
    const form = prose.querySelector('form');
    form.addEventListener('change', ev => {
      if (ev.target.name === 'choice') form.elements.answer.value = ev.target.value;
    });
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const button = form.querySelector('button');
      button.disabled = true;
      form.querySelector('[role="alert"]').textContent = '';
      try {
        const response = await fetch('/api/decision/answer', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, answer: form.elements.answer.value }) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        form.querySelector('[role="status"]').textContent = 'Answer recorded';
        for (const input of form.querySelectorAll('input, textarea')) input.disabled = true;
      } catch (error) {
        form.querySelector('[role="alert"]').textContent = error.message;
        button.disabled = false;
      }
    });
  } catch (error) {
    if (req === rd.req) rdBody.querySelector('.prose').insertAdjacentHTML('beforeend', `<p role="alert">${esc(error.message)}</p>`);
  }
}
async function loadDoc(req) {
  try {
    const data = rd.source === 'library' ? await fetchLibraryDoc(rd.doc.project, rd.doc.id)
      : DEMO ? await demoDoc(rd.host, rd.job.id, rd.doc.id) : await fetchDoc(rd.host, rd.job.id, rd.doc.id);
    if (req !== rd.req) return;
    rd.data = data;
    renderReaderHead();
    renderReaderBody();
  } catch (err) {
    if (req === rd.req) renderReaderError(err.message || String(err));
  }
}
async function fetchDoc(host, job, id) {
  const res = await fetch('/api/doc?' + new URLSearchParams({ host, job, id }));
  let body = null;
  try { body = await res.json(); } catch (err) { /* not JSON */ }
  if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status} ${res.statusText}`);
  if (!body || typeof body.html !== 'string') throw new Error('The server returned an empty document.');
  return body;
}
async function fetchLibraryDoc(project, id) {
  const res = await fetch('/api/library/doc?' + new URLSearchParams({ project, id }));
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function renderReaderHead() {
  const doc = rd.doc, d = rd.data || {}, kind = kindOf(doc);
  const kindEl = document.getElementById('rdKind');
  kindEl.className = 'rd-kind ' + kind; kindEl.textContent = DOC_KIND[kind].label;
  document.getElementById('rdTitle').textContent = rd.source === 'library' ? (d.title || doc.title || d.name || doc.name) : (d.name || doc.name);
  const step = d.step ?? doc.step;
  document.getElementById('rdMeta').innerHTML = [
    rd.source === 'attention' ? `<span>Attention item · ${esc(doc.id)}</span>`
      : rd.source === 'library' ? `<span>${esc(doc.project)} · ${esc(doc.id)}</span>`
      : `<span title="${esc(d.job_description || rd.job.description)}"><i class="hd" style="background:${hostLook(rd.host).color}"></i>${esc(rd.host)} · ${esc(rd.job.id)} · ${esc(d.agent || rd.job.agent)}</span>`,
    step != null ? `<span>step ${step + 1}</span>` : '',
    d.minutes ? `<span>${d.minutes} min read</span>` : '',
    (d.mtime || doc.mtime) ? `<span>updated ${age(d.mtime || doc.mtime)} ago</span>` : '',
  ].join('');
  document.getElementById('rdCopy').disabled = !rd.data;
  document.getElementById('rdDownload').disabled = !rd.data;
}
function renderReaderLoading() {
  document.getElementById('rdProgress').style.transform = 'scaleX(0)';
  rdBody.innerHTML = `<div class="rd-grid"><div class="rd-state rd-skel" aria-label="Loading document" role="status">
    <i class="h"></i>${[96, 88, 93, 60, 0, 91, 97, 85, 70].map(w => w ? `<i style="width:${w}%"></i>` : '<br>').join('')}</div></div>`;
  rdBody.scrollTop = 0;
}
function renderReaderError(message) {
  rdBody.innerHTML = `<div class="rd-grid"><div class="rd-state rd-error" role="alert"><b>Couldn’t open this document</b>
    <code>${esc(message)}</code><br><button class="rd-retry" id="rdRetry">Try again</button></div></div>`;
  document.getElementById('rdRetry').addEventListener('click', () => { renderReaderLoading(); loadDoc(++rd.req); });
}
function renderReaderBody() {
  const d = rd.data;
  const toc = (d.toc || []).filter(x => x.id && x.level <= 3);
  const showToc = toc.length >= 3, top = Math.min(...toc.map(x => x.level));
  rdBody.innerHTML = `<div class="rd-grid${showToc ? ' has-toc' : ''}">
    ${showToc ? `<details class="rd-toc"><summary>Contents<span>${toc.length}</span></summary><nav aria-label="Contents"><p class="lbl">Contents</p>
      ${toc.map(x => `<a href="#doc-${esc(x.id)}" class="l${x.level - top + 1}">${esc(x.text)}</a>`).join('')}</nav></details>` : ''}
    <article class="prose"></article></div>`;
  const prose = rdBody.querySelector('.prose');
  prose.innerHTML = d.html;   // rendered server-side with raw HTML escaped
  if (d.truncated) prose.insertAdjacentHTML('beforeend', '<p class="rd-note">This document was truncated for the reader. Download the Markdown for the full text.</p>');
  tidyProse(prose);
  syncTocMode();
  fitTables();
  if (document.fonts) document.fonts.ready.then(fitTables);
  rd.tocLinks = [...rdBody.querySelectorAll('.rd-toc a')]
    .map(a => ({ a, h: document.getElementById(a.getAttribute('href').slice(1)) }))
    .filter(x => x.h);
  rd.tocCurrent = null;
  rdBody.scrollTop = Number(store('sessionStorage','fleet.reader.scroll.' + rd.key)) || 0;
  onReaderScroll();
}
// heading ids are prefixed so a heading called "panel" or "legend" can't collide with the page's own ids
function tidyProse(prose) {
  for (const el of prose.querySelectorAll('[id]')) el.id = 'doc-' + el.id;
  for (const a of prose.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (href.startsWith('#')) a.setAttribute('href', '#doc-' + href.slice(1));
    else if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    else if (rd.source === 'library') {
      try {
        const url = new URL(href, location.origin + '/' + rd.doc.id);
        const id = decodeURIComponent(url.pathname.slice(1));
        if (url.origin === location.origin && libraryDocs.some(doc => doc.project === rd.doc.project && doc.id === id)) {
          a.dataset.libraryDoc = id;
          a.href = '#';
        }
      } catch (error) { /* leave an invalid link untouched */ }
    }
  }
  for (const table of prose.querySelectorAll('table')) {
    // single tokens (ids, codes) stay on one line; figures get tabular digits
    for (const cell of table.querySelectorAll('td')) {
      const text = cell.textContent.trim();
      if (text.length <= 24 && !/\s/.test(text)) cell.classList.add('nw');
      if (/^[−–+\-]?[£$€]?\d[\d.,]*\s*(%|¢|s|ms|B|KB|MB)?$/.test(text)) cell.classList.add('num');
    }
    const wrap = document.createElement('div'), scroller = document.createElement('div');
    wrap.className = 'rd-table'; scroller.className = 'rd-scroller'; scroller.tabIndex = 0;
    table.replaceWith(wrap); wrap.appendChild(scroller); scroller.appendChild(table);
    scroller.addEventListener('scroll', () => edgeFades(scroller), { passive: true });
  }
  for (const pre of prose.querySelectorAll('pre')) pre.tabIndex = 0;
}
function syncTocMode() {
  const toc = rdBody.querySelector('.rd-toc');
  if (toc) toc.open = WIDE.matches;
}
WIDE.addEventListener('change', syncTocMode);
// give tables that don't fit the 68ch measure a wider column (wide screens only; phones scroll them)
function fitTables() {
  const grid = rdBody.querySelector('.rd-grid');
  if (!grid) return;
  const scrollers = [...grid.querySelectorAll('.rd-scroller')];
  grid.classList.remove('wide');
  if (scrollers.some(s => s.scrollWidth > s.clientWidth + 1)) grid.classList.add('wide');
  scrollers.forEach(edgeFades);
}
// fade the edge a table can still scroll towards, so a clipped column reads as "more this way"
function edgeFades(s) {
  const wrap = s.parentElement, max = s.scrollWidth - s.clientWidth;
  wrap.classList.toggle('more-l', s.scrollLeft > 1);
  wrap.classList.toggle('more-r', s.scrollLeft < max - 1);
}
window.addEventListener('resize', () => { if (!reader.hidden) fitTables(); });

// reads first, then writes, so a scroll frame never forces a second layout
function onReaderScroll() {
  rd.raf = 0;
  const top = rdBody.scrollTop, max = rdBody.scrollHeight - rdBody.clientHeight;
  const current = currentTocLink(top, max);
  rdProgress.style.transform = `scaleX(${max > 0 ? clamp(top / max, 0, 1) : 1})`;
  if (current !== rd.tocCurrent) {
    if (rd.tocCurrent) rd.tocCurrent.removeAttribute('aria-current');
    if (current) current.setAttribute('aria-current', 'location');
    rd.tocCurrent = current;
  }
}
function currentTocLink(top, max) {
  const links = rd.tocLinks;
  if (!links.length) return null;
  if (max > 0 && top >= max - 2) return links[links.length - 1].a;
  const line = rdBody.getBoundingClientRect().top + 110;
  let current = links[0].a;
  for (const { a, h } of links) {
    if (h.getBoundingClientRect().top > line) break;
    current = a;
  }
  return current;
}
function saveReaderScroll() {
  if (rd.key && rd.data) store('sessionStorage','fleet.reader.scroll.' + rd.key, String(Math.round(rdBody.scrollTop)));
}
rdBody.addEventListener('scroll', () => { if (!rd.raf) rd.raf = requestAnimationFrame(onReaderScroll); }, { passive: true });
addEventListener('pagehide', saveReaderScroll);
rdBody.addEventListener('click', ev => {
  const linked = ev.target.closest('a[data-library-doc]');
  if (linked) {
    ev.preventDefault();
    const doc = libraryDocs.find(item => item.project === rd.doc.project && item.id === linked.dataset.libraryDoc);
    if (doc) openLibraryReader(doc);
    return;
  }
  const a = ev.target.closest('a[href^="#"]');
  if (!a) return;
  const target = document.getElementById(a.getAttribute('href').slice(1));
  if (!target) return;
  ev.preventDefault();
  target.scrollIntoView({ block: 'start', behavior: REDUCED ? 'auto' : 'smooth' });
  const toc = a.closest('.rd-toc');
  if (toc && !WIDE.matches) toc.open = false;
});
reader.addEventListener('click', ev => { if (ev.target.closest('[data-close]')) closeReader(); });
document.getElementById('rdTheme').addEventListener('click', () => {
  const theme = rdSheet.dataset.theme === 'dark' ? 'paper' : 'dark';
  setReaderTheme(theme);
  store('localStorage', 'fleet.reader.theme', theme);
});
document.getElementById('rdCopy').addEventListener('click', ev => {
  if (!rd.data) return;
  const b = ev.currentTarget, label = b.querySelector('.lb');
  const done = () => { label.textContent = 'Copied'; b.classList.add('ok'); setTimeout(() => { label.textContent = 'Copy'; b.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(rd.data.markdown).then(done, () => fallbackCopy(rd.data.markdown, done));
  else fallbackCopy(rd.data.markdown, done);
});
document.getElementById('rdDownload').addEventListener('click', () => {
  if (!rd.data) return;
  const prefix = rd.source === 'attention' ? 'attention' : rd.source === 'library' ? rd.doc.project : rd.job.id;
  const base = `${prefix}-${(rd.data.name || rd.doc.name).replace(/\.(md|markdown|mdx)$/i, '')}`.replace(/[^\w.-]+/g, '-').replace(/-+/g, '-');
  const url = URL.createObjectURL(new Blob([rd.data.markdown], { type: 'text/markdown;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: base + '.md' });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
// keep Tab inside the open reader
reader.addEventListener('keydown', ev => {
  if (ev.key !== 'Tab') return;
  const items = [...reader.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]')].filter(el => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && (document.activeElement === first || document.activeElement === rdSheet)) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
});
