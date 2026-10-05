// Document reader: loads a job or library document and renders it with a table of contents.

import { DEMO, REDUCED } from './env.js';
import { age, clamp, esc, stamp, store } from './util.js';
import { hostLook } from './looks.js';
import { DOC_KIND, isUpdating, jobDocSequence, kindOf } from './docs3d.js';
import { hideDocTip } from './camera.js';
import { fallbackCopy, idChip } from './panel.js';
import { libraryDocs } from './library.js';
import { openAttention } from './attention.js';
import { demoDoc } from './demo.js';
import { drawnDiagrams, enrichProse, linkImages, rethemeDiagrams } from './rich.js';

// ------------------------------------------------------------------ reader
export const reader = document.getElementById('reader');
const rdSheet = reader.querySelector('.rd-sheet');
const rdBody = document.getElementById('rdBody');
const WIDE = matchMedia('(min-width: 1101px)');
const rdProgress = document.getElementById('rdProgress');
const rd = { key: null, source: 'job', host: null, job: null, doc: null, data: null, req: 0, raf: 0, lastFocus: null, tocLinks: [], tocCurrent: null };
// Unsent answers stay per attention item until submitted or this page is closed.
const answerDrafts = new Map();
// Versioned entries share both completed reads and reads still on the wire.
const docCache = new Map();
const CACHE_LIMIT = 12;
function target(doc = rd.doc) {
  const source = rd.source, host = rd.host, job = rd.job?.id, url = rd.url;
  const identity = JSON.stringify([source, source === 'stored' ? url : source === 'library' ? doc.project : host,
    source === 'job' ? job : null, doc.id]);
  const version = source === 'stored' ? 'immutable' : `${doc.mtime}-${doc.size}`;
  return { source, host, job, url, doc: { ...doc }, identity, key: `${identity}:${version}` };
}
function touchEntry(key, entry) {
  docCache.delete(key);
  docCache.set(key, entry);
  while (docCache.size > CACHE_LIMIT) docCache.delete(docCache.keys().next().value);
  return entry;
}
function cachedRead(t) {
  const cached = docCache.get(t.key);
  if (cached) return touchEntry(t.key, cached).promise;
  const entry = { key: t.key, identity: t.identity, data: null };
  entry.promise = (t.source === 'library' ? fetchLibraryDoc(t.doc.project, t.doc.id)
    : t.source === 'stored' ? fetchJson(t.url)
    : DEMO ? demoDoc(t.host, t.job, t.doc.id) : fetchDoc(t.host, t.job, t.doc.id))
    .then(data => { entry.data = data; return data; }, error => {
      if (docCache.get(t.key) === entry) docCache.delete(t.key);
      throw error;
    });
  touchEntry(t.key, entry);
  return entry.promise;
}
function openDoc() {
  rd.stale = false; rd.refreshError = null;
  const t = target(), current = docCache.get(t.key);
  const old = current?.data ? current : [...docCache.values()].reverse().find(e => e.identity === t.identity && e.data);
  if (old) {
    rd.data = old.data;
    rd.stale = !current?.data;
    touchEntry(old.key, old);
    renderReaderHead();
    renderReaderBody();
    if (rd.stale) refreshDoc(rd.req);
  } else {
    renderReaderHead();
    renderReaderLoading();
    loadDoc(rd.req);
  }
  rdSheet.focus();
}
function prefetchNeighbors() {
  if (rd.source === 'attention' || rd.source === 'stored') return;
  const { list, at, left } = readerPlace();
  const next = at >= 0 ? at + 1 : left;
  if (next == null) return;
  const indices = [next, next + 1, at >= 0 ? at - 1 : left - 1];
  for (const index of indices) {
    const doc = list[index];
    if (!doc || (doc.media === 'image' && doc.size > 16 * 1024 * 1024)) continue;
    const t = target(doc);
    cachedRead(t).then(data => {
      if (data.media !== 'image') return;
      const entry = docCache.get(t.key);
      if (!entry || entry.image) return;
      const image = new Image();
      entry.image = image;
      image.src = documentAssetUrl(t, data.path.split('/').pop(), data);
      image.decode().catch(() => { if (entry.image === image) entry.image = null; });
    }).catch(() => {}); // speculative errors are retried when the document is opened
  }
}

function saveAnswerDraft() {
  const form = rdBody.querySelector('.decision-answer');
  if (rd.source !== 'attention' || !form?.elements.answer || form.elements.answer.disabled) return;
  answerDrafts.set(rd.doc.id, { answer: form.elements.answer.value, choice: form.querySelector('input[name="choice"]:checked')?.value });
}
function restoreAnswerDraft(form, id) {
  const draft = answerDrafts.get(id);
  if (!draft || !form) return;
  form.elements.answer.value = draft.answer;
  for (const radio of form.querySelectorAll('input[name="choice"]')) radio.checked = radio.value === draft.choice;
  if (draft.answer) form.querySelector('[role="status"]').textContent = 'Unsent answer restored. Submit to send it.';
}
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
  saveAnswerDraft();
  hideDocTip();
  rd.req++;
  rd.key = `${e.host}:${e.job.id}:${doc.id}`;
  rd.source = 'job';
  rd.host = e.host; rd.job = e.job; rd.doc = doc; rd.data = null; rd.stale = false; rd.refreshError = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  openDoc();
}
export function openLibraryReader(doc) {
  saveAnswerDraft();
  if (!reader.hidden) saveReaderScroll();
  rd.req++;
  rd.key = `library:${doc.project}:${doc.id}`;
  rd.source = 'library';
  rd.host = null; rd.job = { id: 'library', description: doc.project }; rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  openDoc();
}
// A copy from a project's document store: a job's document (shown as from the job panel, though the job may have
// left the floor or its host) or one of the project's working documents (no job).
export function openStoredReader(url, doc, job) {
  saveAnswerDraft();
  if (!reader.hidden) saveReaderScroll();
  rd.req++;
  rd.key = 'stored:' + url;
  rd.source = 'stored'; rd.url = url;
  rd.host = job ? job.host : null;
  rd.job = job ? { id: job.id, description: job.description, agent: job.agent } : { id: 'working', description: 'working documents' };
  rd.doc = doc; rd.data = null;
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  openDoc();
}
export function closeReader() {
  if (reader.hidden) return;
  saveAnswerDraft();
  saveReaderScroll();
  rd.req++; rd.key = null;
  reader.hidden = true;
  if (rd.lastFocus && rd.lastFocus.focus) rd.lastFocus.focus();
}
// `scope` limits Previous and Next to where the item was opened from: { name, ids: () => Set of item ids } for a room
// or a job; none (the all-rooms list, the front desk) pages through every unresolved item.
export function openAttentionReader(item, scope = null) {
  saveAnswerDraft();
  rd.req++;
  rd.attentionScope = scope;
  rd.key = `attention:${item.id}`;
  rd.source = 'attention'; rd.stale = false; rd.refreshError = null;
  rd.doc = { id: item.id, name: item.summary, kind: 'file', attentionKind: item.kind, terminal: item.context_reference?.startsWith('session:') && item.source?.startsWith('stream:'), recipient: `${item.source}: ${item.source_reference || item.context_reference}`, seen: item.last_seen };
  const lines = [item.summary, `Source: ${item.source}`, `Context: ${item.context_reference}`,
    `State: ${item.state}`, `Last seen: ${new Date(item.last_seen * 1000).toISOString()}`];
  rd.data = { name: item.summary, markdown: lines.join('\n\n'), html: lines.map(line => `<p>${esc(line)}</p>`).join(''), toc: [] };
  if (reader.hidden) rd.lastFocus = document.activeElement;
  reader.hidden = false;
  renderReaderHead();
  renderReaderBody();
  rdSheet.focus();
  if (item.state === 'resolved') {
    rdBody.querySelector('.prose').insertAdjacentHTML('beforeend', `<p role="note">Read-only: this attention item is resolved. Reading changes no stored state.</p><p>${esc(item.resolution_details || 'Resolution details not recorded')}</p>`);
  }
  // the question's form rewrites the prose when it loads, so the referenced document goes in after it
  const req = rd.req;
  const question = item.state !== 'resolved' && (item.kind === 'decision' || item.blocked) ? loadDecision(item.id, req) : null;
  if (item.state !== 'resolved' && !question && item.kind === 'blocker') renderBlockerHelp(rdBody.querySelector('.prose'));
  Promise.resolve(question).then(() => showContextDocument(item.context_reference, req));
}
// An item whose context is a job document (fleet://host/…/jobs/<job>/outbox/<file>, a step report or brief) shows that
// document below its question: often the question itself is written there. Anything else stays the plain reference.
function jobDocAt(location) {
  const m = /^fleet:\/\/([^/]+)\/(?:.*\/)?jobs\/([^/]+)\/((?:result|brief)-\d+\.md|(?:outbox|context)\/.+)$/.exec(location || '');
  if (!m) return null;
  const doc = m[3].replace(/^result-(\d+)\.md$/, 'report-$1').replace(/^brief-(\d+)\.md$/, 'brief-$1').replace(/^(outbox|context)\//, '$1-');
  return { host: decodeURIComponent(m[1]), job: m[2], doc: decodeURIComponent(doc), name: m[3].split('/').pop() };
}
async function showContextDocument(location, req) {
  const at = jobDocAt(location);
  if (!at || req !== rd.req) return;
  const section = document.createElement('section');
  section.className = 'rd-context-doc';
  section.innerHTML = `<h2>${esc(at.name)}</h2><p class="rd-note">Loading the document this item refers to (${esc(at.host)} · job ${esc(at.job.slice(0, 8))})…</p>`;
  // above the answer box, so it is read before answering
  const prose = rdBody.querySelector('.prose'), form = prose?.querySelector('form');
  if (form) form.before(section); else prose?.append(section);
  try {
    const data = DEMO ? await demoDoc(at.host, at.job, at.doc) : await fetchDoc(at.host, at.job, at.doc);
    if (req !== rd.req) return;
    const html = document.createElement('template');
    html.innerHTML = data.html;
    linkImages(html.content, path => '/api/doc/asset?' + new URLSearchParams({ host: at.host, job: at.job, id: at.doc, path }));
    section.querySelector('.rd-note').replaceWith(html.content);
    enrichProse(section, rdSheet.dataset.theme, new Map());
  } catch (error) {
    if (req === rd.req) section.querySelector('.rd-note').textContent = `Couldn’t open ${at.name} on ${at.host}: ${error.message}`;
  }
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
    if (rd.doc.terminal) {
      prose.innerHTML = `<h2>${esc(detail.question)}</h2><p role="note">Answer this in the session’s terminal. Fleet cannot send an answer there. Close this reader and use Open session in the attention list. Resolving attention does not answer the question.</p><p>${esc(rd.doc.recipient)}</p>`;
      return;
    }
    if (detail.blocked) { renderBlocked(prose, id, detail.blocked); return; }
    if (rd.doc.attentionKind === 'blocker') { renderBlockerHelp(prose); return; }
    prose.innerHTML = `<h2>${esc(detail.question)}</h2><p class="decision-context">${esc(detail.context)}</p>
      ${detail.proposal === null ? '' : `<h3>Proposed change</h3><pre>${esc(detail.proposal.change)}</pre><p>${esc(detail.proposal.reason)}</p>`}
      <form class="decision-answer">
        ${detail.options.length ? `<fieldset><legend>Choices</legend>${detail.options.map(option =>
          `<label><input type="radio" name="choice" value="${esc(option)}"> ${esc(option)}</label>`).join('')}</fieldset>` : ''}
        <label>Your answer<textarea name="answer" rows="4" required></textarea></label>
        <p class="refusal-note">Answer for ${esc(rd.doc.recipient)}. Submitting records your answer as a decision and resolves this request. This cannot be undone in Fleet; it does not send text to a terminal or start a job.</p>
        <button type="submit">Submit answer</button><p role="alert"></p><p role="status"></p>
      </form>`;
    const form = prose.querySelector('form');
    restoreAnswerDraft(form, id);
    let choiceAnswer = null;
    form.elements.answer.addEventListener('input', () => { choiceAnswer = null; });
    form.addEventListener('change', ev => {
      if (ev.target.name !== 'choice') return;
      const answer = form.elements.answer;
      if (!answer.value || answer.value === choiceAnswer) {
        answer.value = ev.target.value;
        choiceAnswer = ev.target.value;
      }
    });
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      form.querySelector('[role="alert"]').textContent = '';
      try {
        const response = await fetch('/api/decision/answer', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, answer: form.elements.answer.value }) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        answerDrafts.delete(id);
        form.querySelector('[role="status"]').textContent = 'Answer recorded; request resolved';
        for (const input of form.querySelectorAll('input, textarea')) input.disabled = true;
        if (req === rd.req) renderReaderHead();
      } catch (error) {
        form.querySelector('[role="alert"]').textContent = error.message;
        button.disabled = false;
      }
    });
  } catch (error) {
    if (req === rd.req) rdBody.querySelector('.prose').insertAdjacentHTML('beforeend', `<p role="alert">${esc(error.message)}</p>`);
  }
}
// Audit 1 batch 3: incomplete blockers still name the place to act.
function renderBlockerHelp(prose) {
  prose.insertAdjacentHTML('beforeend', '<p class="refusal-note" role="note">No answer form is available for this blocker. Close this reader and use Open job or Open session in the attention list. Read the job’s step report and logs, then retry or add a continuation with fleet on its host; answer session questions in that session’s terminal. Resolving the attention item does not restart the job.</p>');
}
// A question an interactive session asked in its terminal. Fleet cannot type there, so it only shows where to answer.
function renderSessionQuestion(prose, s) {
  const where = s.project ? `${esc(s.project)} · ` : '';
  prose.innerHTML = `<p class="session-answer" role="note"><b>Answer this in the session’s terminal on ${esc(s.host)}.</b>
      Fleet cannot type there; this item closes once the session has its answer.</p>
    <p class="session-where">${where}${s.cwd ? `<code>${esc(s.cwd)}</code>` : `${esc(s.label)} (working directory not reported)`} · ${esc(s.host)} · session ${esc(s.session)}</p>
    ${s.state === 'resolved' ? `<p role="status">${esc(s.resolution)}</p>` : ''}
    ${s.questions.length ? '' : `<h2>${esc(rd.doc.name)}</h2>`}
    ${s.questions.map(q => `<section class="session-question">
      ${q.header ? `<p class="qh">${esc(q.header)}</p>` : ''}<h2>${esc(q.question)}</h2>
      ${q.multi_select ? '<p class="qm">More than one may be chosen.</p>' : ''}
      <ol class="question-options">${q.options.map(o => `<li><b>${esc(o.label)}</b>${o.description ? `<span>${esc(o.description)}</span>` : ''}</li>`).join('')}</ol>
    </section>`).join('')}`;
}
// A job step that ended asking its supervisor: its final message, answered by a step that carries the reply.
function renderBlocked(prose, id, b) {
  const open = b.state !== 'resolved';
  prose.innerHTML = `<h2>Step ${b.step + 1} of job ${idChip(b.job)} on ${esc(b.host)} is waiting for you</h2>
    ${b.message === null ? `<p class="refusal-note">fleetd on ${esc(b.host)} reported no final message for this step; upgrade it to see the question here, or read the step’s report.</p>`
      : `<blockquote class="blocked-message">${esc(b.message)}</blockquote>`}
    ${open ? `<form class="decision-answer">
        <label>Your answer<textarea name="answer" rows="5" required></textarea></label>
        <p class="refusal-note">Sending records your answer as a Decision, resolves this request, and adds your answer to job ${idChip(b.job)} as a new step, which continues where step ${b.step + 1} stopped. Sending cannot be undone in Fleet.</p>
        <button type="submit">Send answer</button><p role="alert"></p><p role="status"></p>
      </form>` : `<p role="status">${esc(b.resolution)}</p>`}`;
  const form = prose.querySelector('form');
  if (!form) return;
  restoreAnswerDraft(form, id);
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    form.querySelector('[role="alert"]').textContent = '';
    try {
      const res = await fetch('/api/attention/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, answer: form.elements.answer.value }) });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error);
      answerDrafts.delete(id);
      form.querySelector('[role="status"]').textContent = result.resolution;
      form.insertAdjacentHTML('beforeend', '<p class="decision-receipt">Answer recorded as a Decision.</p>');
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
    <p>Job ${idChip(r.job)} on ${esc(r.host)} ran step ${r.step + 1} with nobody at the prompt, so Claude refused these and carried on.</p>
    ${open ? `<div class="refusal-actions">
        <button data-scope="refused"${r.rules && r.rules.length ? '' : ' disabled'}>Allow these for this job</button>
        <button data-scope="bash"${allDenied ? ' disabled' : ''}>Allow all Bash for this job</button>
        <button data-dismiss title="Resolve this attention item without granting permissions or starting a step; this cannot be undone in Fleet">Resolve without allowing</button></div>
      <p class="refusal-note">${allDenied ? `A deny rule in the host’s Claude settings refuses ${denied.length === 1 ? 'this' : 'these'}; remove it there to let jobs run ${denied.length === 1 ? 'it' : 'them'}, or resolve without allowing.`
        : r.rules === null ? 'This worker’s fleetd names no rules, so only all of Bash can be allowed from here.'
        : `Allowing adds the rules to job ${idChip(r.job)}; a new step continues step ${r.step + 1} with them.${denied.length ? ' Requests a deny rule refuses stay refused.' : ''}`}</p>`
      : `<p role="status">${esc(r.resolution)}</p>`}
    ${open ? `<p class="refusal-note">Allow all Bash grants job ${esc(r.job)} on ${esc(r.host)} permission to run any Bash command in future steps, subject to host deny rules, and starts a continuation. Fleet cannot undo the grant or commands already run. Resolve without allowing closes this item permanently without changing permissions or restarting the job.</p>` : ''}
    <p role="alert"></p><p role="status" class="refusal-done"></p>
    <ol class="refusals">${r.requests.map(q => `<li><code><b>${esc(q.tool)}</b> ${esc(q.detail)}</code>
      <small>${q.description ? `${esc(q.description)} · ` : ''}${covers(q)}</small></li>`).join('')}</ol>`;
  prose.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-scope],[data-dismiss]');
    if (!b || b.disabled) return;
    if (b.dataset.scope === 'bash' && !window.confirm(`Allow job ${r.job} on ${r.host} to run any Bash command in future steps and start a continuation? Host deny rules still apply. Fleet cannot undo the grant or commands already run.`)) return;
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
    const data = await cachedRead(target());
    if (req !== rd.req) return;
    rd.data = data;
    renderReaderHead();
    renderReaderBody();
    if (rd.stale) refreshDoc(req);
  } catch (err) {
    if (req === rd.req) renderReaderError(err.message || String(err));
  }
}
// An attention item open in the reader: a state update may resolve it or add others, so the position and Next follow.
document.addEventListener('fleet:state', () => { if (!reader.hidden && rd.source === 'attention') renderReaderHead(); });
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
  renderReaderHead();
  try {
    const data = await cachedRead(target());
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
  kindEl.className = 'rd-kind ' + kind; kindEl.textContent = rd.source === 'attention' ? (doc.attentionKind === 'decision' ? 'QUESTION' : doc.attentionKind === 'blocker' ? 'BLOCKER' : 'ATTENTION') : DOC_KIND[kind].label;
  document.getElementById('rdTitle').textContent = rd.source === 'library' ? (d.title || doc.title || d.name || doc.name) : (d.name || doc.name);
  const step = d.step ?? doc.step;
  document.getElementById('rdMeta').innerHTML = [
    rd.source === 'attention' ? `<span>Attention item · ${idChip(doc.id)}</span><span title="${esc(new Date(doc.seen * 1000).toLocaleString())}">${stamp(doc.seen)} · ${age(doc.seen)} ago</span>`
      : rd.source === 'library' ? `<span>${esc(doc.project)} · ${esc(doc.id)}</span>`
      : rd.source === 'stored' && !rd.host ? `<span>working · ${esc(doc.id)}</span>`
      : `<span title="${esc(d.job_description || rd.job.description)}"><i class="hd" style="background:${hostLook(rd.host).color}"></i>${esc(rd.host)} · ${idChip(rd.job.id)} ·${esc(d.agent || rd.job.agent)}</span>`,
    rd.source === 'stored' && rd.host ? `<span>Document copy on controller · images read from ${esc(rd.host)}</span>` : '',
    step != null ? `<span>step ${step + 1}</span>` : '',
    d.minutes && d.media !== 'image' ? `<span>${d.minutes} min read</span>` : '',
    (d.mtime || doc.mtime) ? `<span>updated ${age(d.mtime || doc.mtime)} ago</span>` : '',
    (rd.stale || rd.refreshing === rd.req || (rd.source === 'job' && isUpdating(rd.job, doc))) ? '<span class="rd-live">updating live</span>' : '',
    rd.refreshError ? `<span class="rd-stale" title="${esc(rd.refreshError)}">couldn’t refresh: showing an older version</span>` : '',
  ].join('');
  const image = d.media === 'image' || doc.media === 'image';
  document.getElementById('rdCopy').hidden = image;
  document.getElementById('rdFull').hidden = !image || !document.fullscreenEnabled;
  document.getElementById('rdFull').disabled = !rd.data;
  document.getElementById('rdCopy').disabled = !rd.data;
  const download = document.getElementById('rdDownload');
  download.disabled = !rd.data;
  download.title = image ? 'Download the image' : 'Download as .md';
  download.querySelector('.lb').textContent = image ? 'Image' : '.md';
  const { list, at, left } = readerPlace();
  document.getElementById('rdStep').hidden = at < 0 ? left == null || !list.length : list.length < 2;
  const where = rd.attentionScope ? rd.attentionScope.name : 'All rooms and owners';
  document.getElementById('rdPos').textContent = at >= 0 ? `${at + 1} / ${list.length}${rd.source === 'attention' ? ` · ${where}` : ''}`
    : left != null ? `Resolved · ${list.length} left · ${where}` : '';
  for (const [id, direction] of [['rdPrev', 'Previous'], ['rdNext', 'Next']]) {
    const button = document.getElementById(id);
    button.title = rd.source === 'attention' ? `${direction} unresolved attention item ${rd.attentionScope ? `in ${rd.attentionScope.name}` : 'across all rooms and owners'}; unsent answers are kept until this page closes` : `${direction} document`;
    button.setAttribute('aria-label', button.title);
  }
  document.getElementById('rdPrev').disabled = at < 0 ? left == null || left <= 0 : at <= 0;
  document.getElementById('rdNext').disabled = at < 0 ? left == null || left >= list.length : at >= list.length - 1;
}
// Where the open document sits among its siblings. An attention item resolved while open (answered here, or elsewhere)
// leaves the list; `left` is the place it held, so Next goes on to the item now there and Previous to the one before.
function readerPlace() {
  const list = siblings(), at = list.findIndex(x => x.id === rd.doc.id);
  if (at >= 0) rd.place = { id: rd.doc.id, at };
  const left = at < 0 && rd.source === 'attention' && rd.place?.id === rd.doc.id ? rd.place.at : null;
  return { list, at, left };
}
// What Previous and Next step through: a job's documents, in the panel's order; the same project's in the library; or
// every attention item not yet resolved, newest first.
function siblings() {
  if (rd.source === 'job' && rd.job) return jobDocSequence(rd.job);
  if (rd.source === 'library') return libraryDocs.filter(x => x.project === rd.doc.project);
  if (rd.source === 'attention') {
    const ids = rd.attentionScope?.ids();
    return ids ? openAttention().filter(item => ids.has(item.id)) : openAttention();
  }
  return [];
}
function stepDoc(delta) {
  if (reader.hidden) return;
  const { list, at, left } = readerPlace();
  const next = at >= 0 ? list[at + delta] : left != null ? list[delta > 0 ? left : left - 1] : undefined;
  if (!next) return;
  const fullscreen = !!document.fullscreenElement && rdBody.contains(document.fullscreenElement);
  saveReaderScroll();
  rd.fullscreenNext = fullscreen;
  if (rd.source === 'library') openLibraryReader(next);
  else if (rd.source === 'attention') openAttentionReader(next, rd.attentionScope);
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
  prefetchNeighbors();
  if (rd.fullscreenNext) {
    rd.fullscreenNext = false;
    const img = d.media === 'image' && prose.querySelector('img');
    if (img && document.fullscreenElement !== img) showFullscreen(img);
  }
}
// images resolve beside the document, under the same roots the document was read from
function documentAssetUrl(t, path, data) {
  const params = new URLSearchParams(t.source === 'library'
    ? { project: t.doc.project, id: t.doc.id, path }
    : { host: t.host, job: t.job, id: t.doc.id, path });
  // stored copies are immutable, so only live documents need a version to bust the image cache
  if (data?.media === 'image' && t.source !== 'stored') params.set('v', `${data.mtime ?? t.doc.mtime}-${data.size ?? t.doc.size}`);
  if (t.source === 'library') return '/api/library/asset?' + params;
  if (t.source === 'job' || (t.source === 'stored' && t.host && t.job !== 'working')) return '/api/doc/asset?' + params;
  return null;
}
function assetUrl(path) {
  return documentAssetUrl(target(), path, rd.data);
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
  const img = ev.target.closest('.prose img');
  if (img && !img.closest('a')) { showFullscreen(img); return; }
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
// An image fills the screen at its own proportions; Esc or a click on it comes back to the reader.
function showFullscreen(img) {
  if (!document.fullscreenEnabled) return;
  if (document.fullscreenElement === img) { document.exitFullscreen(); return; }
  img.requestFullscreen().catch(() => {});
}
document.getElementById('rdFull').addEventListener('click', () => {
  const img = rdBody.querySelector('.prose img');
  if (img) showFullscreen(img);
});
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
// Keys reach the reader wherever focus sits while it is open: in it, or nowhere (the page itself), as after a click
// on an image, which takes no focus, or on leaving fullscreen.
const forReader = ev => !reader.hidden && (reader.contains(ev.target) || ev.target === document.body || ev.target === document.documentElement);
// ← and → step between documents, except while typing or when a modifier asks for something else. From a fullscreen
// image they go on to the next document fullscreen too, when it is an image.
document.addEventListener('keydown', ev => {
  if ((ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
  if (!forReader(ev) || ev.target.closest('input,textarea,select,[contenteditable="true"]')) return;
  ev.preventDefault();
  stepDoc(ev.key === 'ArrowLeft' ? -1 : 1);
});
// The arrow, page, space, Home and End keys scroll the document wherever focus sits in the reader (the sheet or a
// header button); inside the body, and in fields and buttons that use the key themselves, the browser has them.
const SCROLL_KEYS = new Set(['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', ' ', 'Home', 'End']);
document.addEventListener('keydown', ev => {
  if (!SCROLL_KEYS.has(ev.key) || ev.altKey || ev.ctrlKey || ev.metaKey || ev.defaultPrevented) return;
  if (!forReader(ev) || document.fullscreenElement) return;
  if (rdBody.contains(ev.target) || ev.target.closest('input,textarea,select,[contenteditable="true"],summary')) return;
  if (ev.key === ' ' && ev.target.closest('button')) return;
  ev.preventDefault();
  const page = rdBody.clientHeight * 0.9, line = 40;
  const top = { ArrowDown: line, ArrowUp: -line, PageDown: page, PageUp: -page, ' ': ev.shiftKey ? -page : page }[ev.key];
  if (ev.key === 'Home') rdBody.scrollTo({ top: 0 });
  else if (ev.key === 'End') rdBody.scrollTo({ top: rdBody.scrollHeight });
  else rdBody.scrollBy({ top });
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
