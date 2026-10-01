// Document reader: loads a job or library document and renders it with a table of contents.

import { DEMO, REDUCED } from './env.js';
import { age, clamp, esc, stamp, store } from './util.js';
import { hostLook } from './looks.js';
import { DOC_KIND, isUpdating, jobDocSequence, kindOf } from './docs3d.js';
import { hideDocTip } from './camera.js';
import { fallbackCopy } from './panel.js';
import { libraryDocs } from './library.js';
import { demoDoc } from './demo.js';
import { drawnDiagrams, enrichProse, linkImages, rethemeDiagrams } from './rich.js';

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
  rethemeDiagrams(rdBody, theme);
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
  rd.host = e.host; rd.job = e.job; rd.doc = doc; rd.data = null; rd.stale = false; rd.refreshError = null;
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
// A copy from a project's document store: a job's document (shown as from the job panel, though the job may have
// left the floor or its host) or one of the project's working documents (no job).
export function openStoredReader(url, doc, job) {
  if (!reader.hidden) saveReaderScroll();
  rd.req++;
  rd.key = 'stored:' + url;
  rd.source = 'stored'; rd.url = url;
  rd.host = job ? job.host : null;
  rd.job = job ? { id: job.id, description: job.description, agent: job.agent } : { id: 'working', description: 'working documents' };
  rd.doc = doc; rd.data = null;
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
  rd.doc = { id: item.id, name: item.summary, kind: 'file', seen: item.last_seen };
  const lines = [item.summary, `Source: ${item.source}`, `Context: ${item.context_reference}`,
    `State: ${item.state}`, `Last seen: ${new Date(item.last_seen * 1000).toISOString()}`];
  rd.data = { name: item.summary, markdown: lines.join('\n\n'), html: lines.map(line => `<p>${esc(line)}</p>`).join(''), toc: [] };
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderBody();
  rdSheet.focus();
  if (item.kind === 'decision' || item.blocked) loadDecision(item.id, rd.req);
}
async function loadDecision(id, req) {
  try {
    const res = await fetch('/api/decision?' + new URLSearchParams({ id }));
    const detail = await res.json();
    if (!res.ok) throw new Error(detail.error);
    if (req !== rd.req) return;
    const prose = rdBody.querySelector('.prose');
    if (detail.refusals) { renderRefusals(prose, id, detail); return; }
    if (detail.session_question) { renderSessionQuestion(prose, detail.session_question); return; }
    if (detail.blocked) { renderBlocked(prose, id, detail.blocked); return; }
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
// A question an interactive session asked in its terminal. Fleet cannot type there, so it only shows where to answer.
function renderSessionQuestion(prose, s) {
  const where = s.project ? `${esc(s.project)} · ` : '';
  prose.innerHTML = `<p class="session-answer" role="note"><b>Answer this in the session’s terminal on ${esc(s.host)}.</b>
      Fleet cannot type there; this item closes once the session has its answer.</p>
    <p class="session-where">${where}${s.cwd ? `<code>${esc(s.cwd)}</code>` : `${esc(s.label)} (working directory not reported)`} · ${esc(s.host)} · session ${esc(s.session)}</p>
    ${s.state === 'resolved' ? `<p role="status">${esc(s.resolution)}</p>` : ''}
    ${s.questions.map(q => `<section class="session-question">
      ${q.header ? `<p class="qh">${esc(q.header)}</p>` : ''}<h2>${esc(q.question)}</h2>
      ${q.multi_select ? '<p class="qm">More than one may be chosen.</p>' : ''}
      <ol class="question-options">${q.options.map(o => `<li><b>${esc(o.label)}</b>${o.description ? `<span>${esc(o.description)}</span>` : ''}</li>`).join('')}</ol>
    </section>`).join('')}`;
}
// A job step that ended asking its supervisor: its final message, answered by a step that carries the reply.
function renderBlocked(prose, id, b) {
  const open = b.state !== 'resolved';
  prose.innerHTML = `<h2>Step ${b.step + 1} of job ${esc(b.job)} on ${esc(b.host)} is waiting for you</h2>
    ${b.message === null ? `<p class="refusal-note">fleetd on ${esc(b.host)} reported no final message for this step; upgrade it to see the question here, or read the step’s report.</p>`
      : `<blockquote class="blocked-message">${esc(b.message)}</blockquote>`}
    ${open ? `<form class="decision-answer">
        <label>Your answer<textarea name="answer" rows="5" required></textarea></label>
        <p class="refusal-note">Sending adds your answer to job ${esc(b.job)} as a new step, which continues where step ${b.step + 1} stopped.</p>
        <button type="submit">Send answer</button><p role="alert"></p><p role="status"></p>
      </form>` : `<p role="status">${esc(b.resolution)}</p>`}`;
  const form = prose.querySelector('form');
  if (!form) return;
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    form.querySelector('[role="alert"]').textContent = '';
    try {
      const res = await fetch('/api/attention/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, answer: form.elements.answer.value }) });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error);
      form.querySelector('[role="status"]').textContent = result.resolution;
      form.elements.answer.disabled = true;
    } catch (error) {
      form.querySelector('[role="alert"]').textContent = error.message;
      button.disabled = false;
    }
  });
}
// A job step's refused permission requests: every one listed, answered by changing the job's permissions.
function renderRefusals(prose, id, detail) {
  const r = detail.refusals, open = r.state !== 'resolved';
  const denied = r.requests.filter(q => q.denied_by && q.denied_by.length), allDenied = denied.length === r.requests.length;
  const covers = q => q.denied_by && q.denied_by.length ? `<span class="denied">denied by ${q.denied_by.map(esc).join(', ')}: no rule allowed for the job can override it</span>`
    : q.rules === null ? 'this worker names no rule' : q.rules.length ? q.rules.map(esc).join(', ') : 'no rule covers this';
  prose.innerHTML = `<h2>${esc(detail.question)}</h2>
    <p>Job ${esc(r.job)} on ${esc(r.host)} ran step ${r.step + 1} with nobody at the prompt, so Claude refused these and carried on.</p>
    ${open ? `<div class="refusal-actions">
        <button data-scope="refused"${r.rules && r.rules.length ? '' : ' disabled'}>Allow these for this job</button>
        <button data-scope="bash"${allDenied ? ' disabled' : ''}>Allow all Bash for this job</button>
        <button data-dismiss>Dismiss</button></div>
      <p class="refusal-note">${allDenied ? `A deny rule in the host’s Claude settings refuses ${denied.length === 1 ? 'this' : 'these'}; remove it there to let jobs run ${denied.length === 1 ? 'it' : 'them'}, or dismiss.`
        : r.rules === null ? 'This worker’s fleetd names no rules, so only all of Bash can be allowed from here.'
        : `Allowing adds the rules to job ${esc(r.job)}; a new step continues step ${r.step + 1} with them.${denied.length ? ' Requests a deny rule refuses stay refused.' : ''}`}</p>`
      : `<p role="status">${esc(r.resolution)}</p>`}
    <p role="alert"></p><p role="status" class="refusal-done"></p>
    <ol class="refusals">${r.requests.map(q => `<li><code><b>${esc(q.tool)}</b> ${esc(q.detail)}</code>
      <small>${q.description ? `${esc(q.description)} · ` : ''}${covers(q)}</small></li>`).join('')}</ol>`;
  prose.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-scope],[data-dismiss]');
    if (!b || b.disabled) return;
    const buttons = prose.querySelectorAll('.refusal-actions button');
    for (const x of buttons) x.disabled = true;
    prose.querySelector('[role="alert"]').textContent = '';
    try {
      const res = await fetch(b.dataset.scope ? '/api/attention/allow' : '/api/attention/dismiss', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b.dataset.scope ? { id, scope: b.dataset.scope } : { id }) });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error);
      prose.querySelector('.refusal-done').textContent = result.resolution;
    } catch (error) {
      prose.querySelector('[role="alert"]').textContent = error.message;
      for (const x of buttons) x.disabled = (x.dataset.scope === 'refused' && !(r.rules && r.rules.length))
        || (x.dataset.scope === 'bash' && allDenied);
    }
  });
}
async function loadDoc(req) {
  try {
    const data = rd.source === 'library' ? await fetchLibraryDoc(rd.doc.project, rd.doc.id)
      : rd.source === 'stored' ? await fetchJson(rd.url)
      : DEMO ? await demoDoc(rd.host, rd.job.id, rd.doc.id) : await fetchDoc(rd.host, rd.job.id, rd.doc.id);
    if (req !== rd.req) return;
    rd.data = data;
    renderReaderHead();
    renderReaderBody();
    if (rd.stale && rd.source === 'job') refreshDoc(req);
  } catch (err) {
    if (req === rd.req) renderReaderError(err.message || String(err));
  }
}
// A job document the agent is still writing: when a state update brings it a new mtime or size, fetch it again and
// swap the text in place, keeping the reading position, or the bottom for someone following along.
export function readerTarget() { return reader.hidden || rd.source !== 'job' ? null : `${rd.host}:${rd.job.id}`; }
export function followDoc(e) {
  if (reader.hidden || rd.source !== 'job' || !e || e.host !== rd.host || e.job.id !== rd.job.id) return;
  const doc = (e.job.documents || []).find(d => d.id === rd.doc.id);
  rd.job = e.job;
  if (!doc || (doc.mtime === rd.doc.mtime && doc.size === rd.doc.size)) return;
  rd.doc = doc;
  // still loading or refreshing: fetch once more when that lands
  if (!rd.data || rd.refreshing === rd.req) rd.stale = true;
  else refreshDoc(rd.req);
}
async function refreshDoc(req) {
  rd.refreshing = req; rd.stale = false;
  try {
    const data = DEMO ? await demoDoc(rd.host, rd.job.id, rd.doc.id) : await fetchDoc(rd.host, rd.job.id, rd.doc.id);
    if (req !== rd.req) return;
    rd.data = data; rd.refreshError = null;
    const max = rdBody.scrollHeight - rdBody.clientHeight;
    renderReaderBody(max > 0 && rdBody.scrollTop >= max - 4 ? Infinity : rdBody.scrollTop);
  } catch (err) {
    // the last text that did load stays on screen, and the head says it is out of date
    if (req === rd.req) rd.refreshError = err.message || String(err);
  } finally {
    if (req === rd.req) {
      rd.refreshing = 0;
      renderReaderHead();
      if (rd.stale) refreshDoc(req);
    }
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
  return fetchJson('/api/library/doc?' + new URLSearchParams({ project, id }));
}
async function fetchJson(url) {
  const res = await fetch(url);
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
    rd.source === 'attention' ? `<span>Attention item · ${esc(doc.id)}</span><span title="${esc(new Date(doc.seen * 1000).toLocaleString())}">${stamp(doc.seen)} · ${age(doc.seen)} ago</span>`
      : rd.source === 'library' ? `<span>${esc(doc.project)} · ${esc(doc.id)}</span>`
      : rd.source === 'stored' && !rd.host ? `<span>working · ${esc(doc.id)}</span>`
      : `<span title="${esc(d.job_description || rd.job.description)}"><i class="hd" style="background:${hostLook(rd.host).color}"></i>${esc(rd.host)} · ${esc(rd.job.id)} · ${esc(d.agent || rd.job.agent)}</span>`,
    step != null ? `<span>step ${step + 1}</span>` : '',
    d.minutes && d.media !== 'image' ? `<span>${d.minutes} min read</span>` : '',
    (d.mtime || doc.mtime) ? `<span>updated ${age(d.mtime || doc.mtime)} ago</span>` : '',
    rd.source === 'job' && isUpdating(rd.job, doc) ? '<span class="rd-live">updating live</span>' : '',
    rd.source === 'job' && rd.refreshError ? `<span class="rd-stale" title="${esc(rd.refreshError)}">couldn’t refresh: showing an older version</span>` : '',
  ].join('');
  const image = d.media === 'image' || doc.media === 'image';
  document.getElementById('rdCopy').hidden = image;
  document.getElementById('rdCopy').disabled = !rd.data;
  const download = document.getElementById('rdDownload');
  download.disabled = !rd.data;
  download.title = image ? 'Download the image' : 'Download as .md';
  download.querySelector('.lb').textContent = image ? 'Image' : '.md';
  const list = siblings(), at = list.findIndex(x => x.id === doc.id);
  document.getElementById('rdStep').hidden = list.length < 2 || at < 0;
  document.getElementById('rdPos').textContent = at < 0 ? '' : `${at + 1} / ${list.length}`;
  document.getElementById('rdPrev').disabled = at <= 0;
  document.getElementById('rdNext').disabled = at < 0 || at >= list.length - 1;
}
// The documents Previous and Next step through: a job's, in the panel's order, or the same project's in the library.
function siblings() {
  if (rd.source === 'job' && rd.job) return jobDocSequence(rd.job);
  if (rd.source === 'library') return libraryDocs.filter(x => x.project === rd.doc.project);
  return [];
}
function stepDoc(delta) {
  if (reader.hidden) return;
  const list = siblings(), at = list.findIndex(x => x.id === rd.doc.id), next = list[at + delta];
  if (at < 0 || !next) return;
  saveReaderScroll();
  if (rd.source === 'library') openLibraryReader(next);
  else openReader({ host: rd.host, job: rd.job }, next);
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
function keepImageSizes(prose, fragment) {
  const sizes = new Map([...prose.querySelectorAll('img')].filter(img => img.naturalWidth)
    .map(img => [img.getAttribute('src'), [img.naturalWidth, img.naturalHeight]]));
  for (const img of fragment.querySelectorAll('img')) {
    const size = sizes.get(img.getAttribute('src'));
    if (size && !img.hasAttribute('width')) { img.width = size[0]; img.height = size[1]; }
  }
}
// With a scroll position this is a refresh of the open document: the grid stays, only the contents and the text are
// swapped, and the position is restored before the browser paints, so nothing flickers. Infinity follows the bottom.
function renderReaderBody(scrollTop) {
  const d = rd.data;
  const toc = (d.toc || []).filter(x => x.id && x.level <= 3);
  const showToc = toc.length >= 3, top = Math.min(...toc.map(x => x.level));
  const links = showToc ? toc.map(x => `<a href="#doc-${esc(x.id)}" class="l${x.level - top + 1}">${esc(x.text)}</a>`).join('') : '';
  const grid = rdBody.querySelector('.rd-grid');
  const refresh = scrollTop !== undefined && grid && grid.classList.contains('has-toc') === showToc && grid.querySelector('.prose');
  if (refresh) {
    const nav = grid.querySelector('.rd-toc nav');
    if (nav) nav.innerHTML = `<p class="lbl">Contents</p>${links}`;
    const count = grid.querySelector('.rd-toc summary span');
    if (count) count.textContent = toc.length;
  } else {
    rdBody.innerHTML = `<div class="rd-grid${showToc ? ' has-toc' : ''}">
      ${showToc ? `<details class="rd-toc"><summary>Contents<span>${toc.length}</span></summary><nav aria-label="Contents"><p class="lbl">Contents</p>
        ${links}</nav></details>` : ''}
      <article class="prose"></article></div>`;
  }
  const prose = rdBody.querySelector('.prose');
  const html = document.createElement('template');
  html.innerHTML = d.html;   // rendered server-side with raw HTML escaped; inert until its images are pointed home
  linkImages(html.content, assetUrl);
  // a refresh shows unchanged diagrams as drawn and keeps each image's room while it reloads, so nothing jumps
  const drawn = refresh ? drawnDiagrams(prose) : new Map();
  if (refresh) keepImageSizes(prose, html.content);
  prose.replaceChildren(html.content);
  enrichProse(prose, rdSheet.dataset.theme, drawn);
  if (d.truncated) prose.insertAdjacentHTML('beforeend', '<p class="rd-note">This document was truncated for the reader. Download the Markdown for the full text.</p>');
  tidyProse(prose);
  if (!refresh) syncTocMode();
  fitTables();
  if (document.fonts) document.fonts.ready.then(fitTables);
  rd.tocLinks = [...rdBody.querySelectorAll('.rd-toc a')]
    .map(a => ({ a, h: document.getElementById(a.getAttribute('href').slice(1)) }))
    .filter(x => x.h);
  rd.tocCurrent = null;
  rdBody.scrollTop = scrollTop === Infinity ? rdBody.scrollHeight
    : scrollTop ?? (Number(store('sessionStorage','fleet.reader.scroll.' + rd.key)) || 0);
  onReaderScroll();
}
// images resolve beside the document, under the same roots the document was read from
function assetUrl(path) {
  if (rd.source === 'library') return '/api/library/asset?' + new URLSearchParams({ project: rd.doc.project, id: rd.doc.id, path });
  if (rd.source === 'job') return '/api/doc/asset?' + new URLSearchParams({ host: rd.host, job: rd.job.id, id: rd.doc.id, path });
  return null;
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
  if (rd.data.media === 'image') {   // an outbox image downloads as itself, not as the page that shows it
    const a = Object.assign(document.createElement('a'), { href: assetUrl(rd.data.path.split('/').pop()), download: rd.data.path.split('/').pop() });
    document.body.appendChild(a); a.click(); a.remove();
    return;
  }
  const prefix = rd.source === 'attention' ? 'attention' : rd.source === 'library' ? rd.doc.project : rd.job.id;
  const base = `${prefix}-${(rd.data.name || rd.doc.name).replace(/\.(md|markdown|mdx)$/i, '')}`.replace(/[^\w.-]+/g, '-').replace(/-+/g, '-');
  const url = URL.createObjectURL(new Blob([rd.data.markdown], { type: 'text/markdown;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: base + '.md' });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.getElementById('rdPrev').addEventListener('click', () => stepDoc(-1));
document.getElementById('rdNext').addEventListener('click', () => stepDoc(1));
// ← and → step between documents, except while typing or when a modifier asks for something else
reader.addEventListener('keydown', ev => {
  if ((ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
  if (ev.target.closest('input,textarea,select,[contenteditable="true"]')) return;
  ev.preventDefault();
  stepDoc(ev.key === 'ArrowLeft' ? -1 : 1);
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
