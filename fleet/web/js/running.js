// Running view (V4): the header's "N working" chip opens a list of every running, stalled, blocked, failed, lost and queued job on
// every host, grouped by project and then by the work item its current step serves (epic > milestone). Each row says
// where the job runs, how far it is, which branch or worktree it works in, and opens the job's panel. Jobs with no work
// item are listed apart, with the `fleet run link` command that gives them one. Read from the whole state document, so
// dismissed jobs and other floors' jobs are listed too; only a job the deck draws or lists under a lantern can open.

import { duration, esc, trunc } from './util.js';
import { hostLook } from './looks.js';
import { workOf } from './model.js';
import { idChip, select } from './panel.js';

const LISTED = ['running', 'stalled', 'blocked', 'failed', 'lost', 'queued'];
const GLYPH = { running: '▶', stalled: '◍', blocked: '⚑', failed: '✕', lost: '?', queued: '○' };
const UNLINKED = 'Not linked to work';

const panel = document.getElementById('runPanel');
const stats = document.getElementById('stats');
let doc = null, opener = null;

// ------------------------------------------------------------------ what each row stands for
// The step the job is on: the one running, else the blocked one waiting for an answer, else the failed one, else the
// next pending one.
function currentStep(j) {
  const steps = j.steps || [];
  return steps.find(s => s.status === 'running') || steps.find(s => s.status === 'blocked' && !s.answered_by)
    || steps.find(s => s.status === 'failed') || steps.find(s => s.status === 'pending') || null;
}
// The work item the current step serves (root first): the step's own item while it runs, else the job's.
const workChain = j => j.work?.step?.chain?.length ? j.work.step.chain : j.work?.chain || [];

function listed() {
  const rows = [];
  for (const h of doc?.hosts || []) for (const j of h.jobs || []) {
    if (LISTED.includes(j.status)) rows.push({ key: `${h.name}:${j.id}`, host: h.name, job: j, chain: workChain(j) });
  }
  return rows;
}
// Project → work path → rows; the unlinked ones in a group of their own at the end.
function grouped(rows) {
  const projects = new Map(), unlinked = [];
  const label = name => doc.project_labels?.[name] || name;
  for (const r of rows) {
    if (!r.chain.length) { unlinked.push(r); continue; }
    const path = r.chain.filter(n => n.kind !== 'task');
    const name = r.job.project;
    if (!projects.has(name)) projects.set(name, { name, label: label(name), paths: new Map() });
    const paths = projects.get(name).paths, at = path.map(n => n.id).join('/');
    if (!paths.has(at)) paths.set(at, { path, rows: [] });
    paths.get(at).rows.push(r);
  }
  const byLabel = (a, b) => a.label.localeCompare(b.label);
  const byPath = (a, b) => a.path.map(n => n.title).join(' > ').localeCompare(b.path.map(n => n.title).join(' > '));
  return { projects: [...projects.values()].sort(byLabel).map(p => ({ ...p, paths: [...p.paths.values()].sort(byPath) })),
    unlinked, label };
}

// ------------------------------------------------------------------ rows
function stepHtml(j) {
  const steps = j.steps || [], s = currentStep(j);
  if (!s) return `<span title="no step running, blocked or pending">${steps.length} step${steps.length === 1 ? '' : 's'}</span>`;
  const took = s.started_at ? duration((s.finished_at ?? Date.now() / 1000) - s.started_at) : '';
  const since = s.started_at ? `, started ${new Date(s.started_at * 1000).toLocaleString()}` : ', not started';
  return `<span title="${esc(`step ${s.index + 1} of ${steps.length}: ${s.title}${since}`)}">step ${s.index + 1}/${steps.length}${took ? ` · ${took}` : ''}</span>`;
}
const BRANCH_ICON = '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/><circle cx="11.5" cy="5.5" r="1.6"/><path d="M4.5 5.1v5.8M11.5 7.1c0 3-7 2.2-7 3.8"/></svg>';
const baseName = path => path.replace(/\/+$/, '').split('/').pop();
// The branch (or detached head) with the checkout's folder name; the whole path in the title, and a click copies it.
function workspaceHtml(j) {
  const w = j.workspace;
  if (!w) {
    const why = j.workspace_reason ?? 'not reported by this worker';   // an older fleetd sends neither field
    return `<span class="run-ws unknown" title="${esc(`workspace unknown: ${why}`)}">workspace unknown</span>`;
  }
  const where = w.detached ? `detached @ ${w.head}` : w.branch;
  const tree = w.linked_worktree ? `worktree ${w.toplevel} of ${w.repository}` : `checkout ${w.toplevel}`;
  const title = `${tree}\n${w.detached ? 'detached HEAD' : `branch ${w.branch}`}${w.head ? ` @ ${w.head}` : ' (no commits)'}`
    + `${w.dirty ? `\n${w.dirty} uncommitted` : ''}\nClick to copy the path`;
  return `<button class="run-ws${w.linked_worktree ? ' wt' : ''}" data-copy-id="${esc(w.toplevel)}" title="${esc(title)}"
    aria-label="${esc(`${tree}, ${where}: copy the path`)}">${BRANCH_ICON}<span>${esc(trunc(where, 28))}</span>${
    w.linked_worktree ? `<small>${esc(trunc(baseName(w.toplevel), 22))}</small>` : ''}${w.dirty ? `<em>±${w.dirty}</em>` : ''}</button>`;
}
function workHtml(r) {
  const leaf = r.chain.at(-1);
  if (!leaf) return '';
  const own = r.job.work?.chain?.at(-1);
  const step = r.job.work?.step?.chain?.length ? `step ${r.job.work.step.index + 1} serves this ${leaf.kind}` : `linked ${leaf.kind}`;
  const title = `${step}: ${r.chain.map(n => n.title).join(' > ')}${own && own.id !== leaf.id ? `\nthe job is linked to ${own.title}` : ''}`;
  return `<span class="run-work" title="${esc(title)}">◆ ${esc(trunc(leaf.title, 40))}</span>`;
}
function rowHtml(r, unlinked) {
  const j = r.job, here = !!workOf(r.key) || !onDeck();
  const link = `fleet run link ${r.host} ${j.id} <work-item>`;
  return `<li class="run-row" data-key="${esc(r.key)}" data-status="${esc(j.status)}" ${here
    ? 'tabindex="0" role="button"' : 'aria-disabled="true"'} title="${esc(here ? j.description : `${j.description}\nDismissed from the deck`)}">
    <span class="run-g" title="${esc(j.status)}" aria-label="${esc(j.status)}">${GLYPH[j.status]}</span>
    <div class="run-b"><b>${esc(trunc(j.description, 90))}</b>
      <div class="run-m"><span class="run-host"><i style="background:${hostLook(r.host).color}"></i>${esc(r.host)}:${idChip(j.id)}</span>
        ${stepHtml(j)}${workspaceHtml(j)}${unlinked ? `<span class="run-proj">${esc(doc.project_labels?.[j.project] || j.project)}</span>
        <button class="run-link" data-copy-id="${esc(link)}" title="${esc(`Copy: ${link}`)}">fleet run link</button>` : workHtml(r)}</div></div>
  </li>`;
}

// ------------------------------------------------------------------ the panel
const crumbs = path => path.map(n => `<span title="${esc(n.kind)}">${esc(n.title)}</span>`).join('<i aria-hidden="true"> > </i>');
function bodyHtml() {
  const rows = listed();
  if (!rows.length) return '<p class="run-empty">Nothing is running, blocked, failed or queued.</p>';
  const { projects, unlinked } = grouped(rows);
  const count = s => rows.filter(r => r.job.status === s).length;
  const counts = LISTED.filter(count).map(s => `${count(s)} ${s}`).join(' · ');
  return `<p class="run-sum">${esc(counts)}</p>${projects.map(p => `
    <section class="run-proj-g" data-project="${esc(p.name)}" aria-label="${esc(p.label)}"><h4>${esc(p.label)}</h4>${p.paths.map(g => `
      <div class="run-path" data-path="${esc(g.path.map(n => n.id).join('/'))}"><h5>${crumbs(g.path)}</h5>
        <ul>${g.rows.map(r => rowHtml(r, false)).join('')}</ul></div>`).join('')}
    </section>`).join('')}${unlinked.length ? `
    <section class="run-proj-g unlinked" data-unlinked aria-label="${UNLINKED}"><h4>${UNLINKED}</h4>
      <p class="run-need">These need a work item: copy the link command and fill in the item's ID.</p>
      <ul>${unlinked.map(r => rowHtml(r, true)).join('')}</ul>
    </section>` : ''}`;
}
function render() {
  if (panel.hidden) return;
  const html = `<div class="ah"><h3>Running</h3><button data-close aria-label="Close">✕</button></div><div class="run-body">${bodyHtml()}</div>`;
  if (panel.lastHtml === html) return;
  const top = panel.scrollTop;
  panel.lastHtml = html;
  panel.innerHTML = html;
  panel.scrollTop = top;
}
const chip = () => document.getElementById('workingOpen');
function open() {
  opener = chip();
  panel.hidden = false;
  panel.lastHtml = null;
  render();
  const at = opener.getBoundingClientRect(), w = panel.offsetWidth;
  panel.style.transform = `translate(${Math.round(Math.max(8, Math.min(at.right - w, innerWidth - w - 8)))}px,${Math.round(at.bottom + 8)}px)`;
  opener.setAttribute('aria-expanded', 'true');
  panel.querySelector('[data-close]').focus({ preventScroll: true });
}
function close(returnFocus = false) {
  if (panel.hidden) return;
  panel.hidden = true;
  chip()?.setAttribute('aria-expanded', 'false');
  if (returnFocus) chip()?.focus({ preventScroll: true });
}
// A floor shows only its project's jobs and the building shows no job panel: a row for a job out of reach there goes
// to the whole deck. On the deck itself, only a dismissed job is out of reach.
const onDeck = () => document.body.dataset.view === 'deck';
function openRow(row) {
  if (!row || row.getAttribute('aria-disabled') === 'true') return;
  close();
  if (!onDeck() && (document.body.dataset.view === 'building' || !workOf(row.dataset.key))) {
    document.querySelector('#viewToggle [data-view="deck"]').click();
  }
  if (workOf(row.dataset.key)) select(row.dataset.key);
}

stats.addEventListener('click', ev => {
  if (!ev.target.closest('#workingOpen')) return;
  if (panel.hidden) open(); else close(true);
});
panel.addEventListener('click', ev => {
  if (ev.target.closest('[data-close]')) { close(true); return; }
  openRow(ev.target.closest('.run-row'));
});
panel.addEventListener('keydown', ev => {
  if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches('.run-row')) { ev.preventDefault(); openRow(ev.target); }
});
// Captured so this Escape closes only the list; an open reader takes Escape first.
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || panel.hidden || !document.getElementById('reader').hidden) return;
  ev.stopPropagation();
  close(true);
}, true);
document.addEventListener('pointerdown', ev => {
  if (!panel.hidden && !ev.target.closest('#runPanel, #workingOpen')) close();
});
// The header is redrawn with every state document: the chip keeps saying whether the list is open, and the list follows.
document.addEventListener('fleet:state', ev => {
  doc = ev.detail;
  chip()?.setAttribute('aria-expanded', String(!panel.hidden));
  render();
});
