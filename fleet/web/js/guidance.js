// The room's guidance: a constitution or charter with its version, editor and history, and an epic's decisions.
// Markup only; bench.js holds the state and handles the clicks.
import { esc } from './util.js';
import { idChip } from './panel.js';

const icon = paths => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
export const ICONS = {
  edit: icon('<path d="M10.8 2.6 13.4 5.2 6 12.6 2.8 13.2 3.4 10Z"/><path d="M9.4 4 12 6.6"/>'),   // a pencil
  history: icon('<path d="M2.6 8a5.4 5.4 0 1 0 1.6-3.8"/><path d="M2.4 2.4v2.4h2.4M8 5v3.2l2.2 1.4"/>'),   // a clock turning back
  open: icon('<path d="M4 1.8h5.2L12.5 5v9.2H4z"/><path d="M9 1.8V5.3h3.5M6 8.2h4.3M6 10.8h4.3"/>'),   // a page
  save: icon('<path d="M3 8.5 6.5 12 13 4.5"/>'),
  cancel: icon('<path d="M4 4l8 8M12 4l-8 8"/>'),
  promote: icon('<path d="M8 13V4M4.5 7.5 8 4l3.5 3.5M3 2h10"/>'),   // up into the charter
  copy: icon('<rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 5.5V3.6A1.1 1.1 0 0 0 9.4 2.5H3.6a1.1 1.1 0 0 0-1.1 1.1v5.8a1.1 1.1 0 0 0 1.1 1.1h1.9"/>'),
};
const day = time => new Date(time).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
const when = time => `<span title="${esc(new Date(time).toLocaleString())}">${esc(day(time))}</span>`;
export const versionLine = v => `version ${v.number} · ${esc(v.actor)} · ${when(v.time)}`;

// One line for the floor's constitution entry.
export function guidanceSummary(view) {
  if (!view) return 'Loading…';
  if (view.error) return esc(view.error);
  return view.guidance ? versionLine(view.guidance.version) : 'Not recorded';
}

function inherits(g) {
  if (!g.constitution) return '<small data-inherits>Inherits no constitution: none recorded</small>';
  const at = g.inherits ? `constitution version ${g.inherits.number}` : 'no constitution (written before version 1)';
  const current = g.inherits && g.inherits.number === g.constitution.number ? '' : `; current version ${g.constitution.number}`;
  return `<small data-inherits>Inherits ${at}${current}</small>`;
}

// A constitution (`kind` "constitution") or an epic's charter ("charter"). `editing` is the open editor's state for
// this document, or null; `history` whether its versions list is open.
export function guidancePanel(kind, view, { editing = null, history = false } = {}) {
  const label = kind === 'charter' ? 'Charter' : 'Constitution';
  if (!view) return `<section data-guidance="${kind}" aria-label="${label}"><h3>${label}</h3><p data-empty>Loading…</p></section>`;
  if (view.error) return `<section data-guidance="${kind}" aria-label="${label}"><h3>${label}</h3><p role="alert">${esc(view.error)}</p></section>`;
  const g = view.guidance;
  const tools = editing ? '' : `<span data-guidance-tools><button data-guidance-edit="${kind}" title="${g ? 'Edit' : 'Write'} the ${kind}" aria-label="${g ? 'Edit' : 'Write'} the ${kind}">${ICONS.edit}</button>${
    view.history.length ? `<button data-guidance-history="${kind}" aria-expanded="${history}" title="Versions" aria-label="Versions">${ICONS.history}</button>` : ''}</span>`;
  const head = `<div data-guidance-head><h3>${label}</h3>${g ? `<small data-guidance-version>${versionLine(g.version)}</small>` : ''}${tools}</div>`;
  const versions = history && !editing ? `<ol data-guidance-versions>${view.history.map(v =>
    `<li><span>${versionLine(v)}</span><button data-guidance-open="${kind}" data-version="${v.number}" title="Read version ${v.number}" aria-label="Read version ${v.number}">${ICONS.open}</button></li>`).join('')}</ol>` : '';
  const body = editing
    ? `<div data-guidance-editor><textarea data-guidance-text aria-label="${label} Markdown" spellcheck="true">${esc(editing.text)}</textarea>
        <div data-guidance-actions><small>${editing.base ? `Editing version ${editing.base}` : `A new ${kind}`}</small>
        <button data-guidance-save title="Save a new version" aria-label="Save a new version" ${editing.saving ? 'disabled' : ''}>${ICONS.save}</button><button data-guidance-cancel title="Discard the edit" aria-label="Discard the edit">${ICONS.cancel}</button></div>
        ${editing.error ? `<p role="alert">${esc(editing.error)}</p>` : ''}</div>`
    : g ? `<div data-guidance-body>${view.html}</div>` : `<p data-empty>No ${kind} recorded.</p>`;
  return `<section data-guidance="${kind}" aria-label="${label}" ${editing ? 'data-editing' : ''}>${head}${g && kind === 'charter' ? inherits(g) : ''}${versions}${body}</section>`;
}

// An epic's decisions, newest first. Promote shows when there is a charter that does not yet hold the decision.
export function decisionsPanel(list) {
  if (!list) return '<section data-decisions aria-label="Decisions"><h3>Decisions</h3><p data-empty>Loading…</p></section>';
  const rows = list.decisions.map(d => {
    const items = d.work_items.map(w => w.title === null ? esc(w.id.slice(0, 8)) : esc(w.title)).join(', ');
    const promote = d.promoted === true ? '<small data-in-force>In force</small>'
      : d.promoted === false ? `<button data-promote="${esc(d.id)}" title="Add to the charter's decisions in force" aria-label="Add to the charter's decisions in force">${ICONS.promote}</button>` : '';
    return `<li data-decision="${esc(d.id)}"><div data-decision-head><b>${esc(d.question)}</b>${promote}</div>
      <p>${esc(d.answer)}</p>
      <small data-principle>${d.principle === null ? 'Principle unknown' : `Principle: ${esc(d.principle)}`}</small>
      <small>${esc(d.actor)} · ${when(d.time)} · ${items} · ${idChip(d.id)}</small></li>`;
  }).join('');
  return `<section data-decisions aria-label="Decisions"><h3>Decisions</h3>${rows ? `<ol>${rows}</ol>` : '<p data-empty>No decisions recorded.</p>'}${
    list.error ? `<p role="alert">${esc(list.error)}</p>` : ''}</section>`;
}
