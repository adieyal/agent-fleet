// One read-only audit view for work items, attention items and stored runs.
import { esc } from './util.js';
import { mountRunHistory } from './run-history.js';
import { openArchivedRun } from './panel.js';

const value = v => v == null ? '<span class="muted">not set</span>' : typeof v === 'object'
  ? `<pre>${esc(JSON.stringify(v, null, 2))}</pre>` : `<span>${esc(String(v))}</span>`;
export function mountAuditHistory(root, subject) {
  let request = 0;
  root.dataset.auditSubject = subject;
  root.innerHTML = '<section data-audit-history><h3>Changes · newest first</h3><button data-audit-refresh title="Reload this stored audit trail; changes no stored state">Refresh</button><div data-audit-results aria-live="polite"></div></section>';
  const results = root.querySelector('[data-audit-results]');
  async function load() {
    const at = ++request;
    results.innerHTML = '<p>Loading stored changes…</p>';
    try {
      const response = await fetch('/api/history?' + new URLSearchParams({ subject }));
      const data = await response.json();
      if (at !== request || !root.isConnected) return;
      if (!response.ok) throw new Error(data.error || 'History could not be loaded');
      const entries = [...data.entries].sort((a,b) => Number(b.sequence) - Number(a.sequence));
      results.innerHTML = entries.length ? `<ol class="audit-entries">${entries.map(e => `<li data-audit-sequence="${esc(e.sequence)}"><div><b>${esc(e.actor || 'Actor not recorded')}</b><time title="${esc(e.time)}">${esc(new Date(e.time).toLocaleString())}</time><small>${esc(e.kind)} · #${esc(e.sequence)}</small></div>
        ${e.changes.length ? `<dl>${e.changes.map(c => `<dt>${esc(c.field)}</dt><dd><div data-before>${value(c.before)}</div><span aria-label="changed to">→</span><div data-after>${value(c.after)}</div></dd>`).join('')}</dl>` : '<p>No field changes recorded.</p>'}
        ${e.source_run ? `<button data-audit-run="${esc(e.source_run)}" title="Open the stored source run; changes no stored state">Source run ${esc(e.source_run)}</button>` : ''}${e.job ? `<p>Job: ${esc(e.job)}</p>` : ''}</li>`).join('')}</ol>` : '<p data-audit-empty>No stored changes for this item.</p>';
    } catch (error) { if (at === request && root.isConnected) results.innerHTML = `<p role="alert">${esc(error.message)}</p><button data-audit-retry>Retry</button>`; }
  }
  root.addEventListener('click', ev => {
    if (ev.target.closest('[data-audit-refresh], [data-audit-retry]')) load();
    const run = ev.target.closest('[data-audit-run]');
    if (run) { if (sheet.open) closeItemHistory(); openArchivedRun(run.dataset.auditRun); }
  });
  load();
}

const sheet = document.body.appendChild(document.createElement('dialog'));
sheet.id = 'itemHistoryPanel';
sheet.setAttribute('aria-labelledby', 'itemHistoryTitle');
let opener = null;
export function openItemHistory(info) {
  if (!sheet.open) opener = document.activeElement;
  sheet.innerHTML = `<header><div><h2 id="itemHistoryTitle">${esc(info.title)}</h2><p>${esc(info.kind)} · ${esc(info.id)}</p></div><button data-item-close aria-label="Close item history">✕</button></header><nav role="tablist" aria-label="Item views"><button role="tab" data-item-tab="details" aria-selected="false">Details</button><button role="tab" data-item-tab="history" aria-selected="true">History</button></nav><div data-item-details role="tabpanel" hidden><p>${esc(info.title)}</p>${info.status ? `<p>Status: ${esc(info.status)}</p>` : ''}${info.project ? `<p>Project: ${esc(info.project)}</p>` : ''}</div><div data-item-history role="tabpanel"><div data-item-audit></div>${info.kind === 'work item' ? '<section><div data-item-runs></div></section>' : ''}</div>`;
  if (!sheet.open) sheet.showModal();
  mountAuditHistory(sheet.querySelector('[data-item-audit]'), `${info.kind === 'work item' ? 'work:item' : 'attention'}:${info.id}`);
  if (info.kind === 'work item') mountRunHistory(sheet.querySelector('[data-item-runs]'), null, { work_item: info.id }, { title: 'Runs for this work item' });
  sheet.querySelector('[data-item-tab=history]').focus();
}
function closeItemHistory() { sheet.close(); sheet.replaceChildren(); if (opener?.isConnected) opener.focus({ preventScroll: true }); }
sheet.addEventListener('close', () => { if (!sheet.open) sheet.replaceChildren(); });
sheet.addEventListener('fleet:open-stored-run', closeItemHistory);
sheet.addEventListener('click', ev => {
  if (ev.target.closest('[data-item-close]')) { closeItemHistory(); return; }
  const tab = ev.target.closest('[data-item-tab]');
  if (!tab) return;
  sheet.querySelectorAll('[data-item-tab]').forEach(b => b.setAttribute('aria-selected', String(b === tab)));
  sheet.querySelector('[data-item-details]').hidden = tab.dataset.itemTab !== 'details';
  sheet.querySelector('[data-item-history]').hidden = tab.dataset.itemTab !== 'history';
});
window.addEventListener('keydown', ev => {
  if (!sheet.open) return;
  ev.stopImmediatePropagation();
  if (ev.key === 'Escape') { ev.preventDefault(); closeItemHistory(); }
}, { capture: true });
