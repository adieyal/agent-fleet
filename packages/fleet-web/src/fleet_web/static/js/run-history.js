// Store-backed runs: this component can also be mounted with a work_item filter.
import { age, duration, esc, offlineLabel, storedStatus } from './util.js';
import { fallbackCopy, idChip, openArchivedRun } from './panel.js';

const started = r => r.start ? `<time title="${esc(new Date(r.start).toLocaleString())}">${age(Date.parse(r.start) / 1000)} ago</time>` : 'Start not recorded';
const count = (n, label) => n == null ? `${label} not recorded` : `${n} ${label}`;
function row(r) {
  const link = `fleet run link ${r.host} ${r.remote_job_id} WORK_ITEM`;
  const branch = r.workspace?.detached ? `detached @${r.workspace.head}` : r.workspace?.branch;
  return `<li data-history-run="${esc(r.id)}"><div><button data-open-run="${esc(r.id)}">${r.kind === 'session' ? '◉' : '▣'} ${esc(r.title || 'Title not recorded')}</button>${idChip(r.id)}</div>
    <p>${started(r)} · ${r.duration_seconds == null ? 'Duration not recorded' : duration(r.duration_seconds)} · <b>${esc(storedStatus(r))}</b>${r.reason && !['queued', 'stalled'].includes(r.reason) ? ` · ${esc(r.reason)}` : ''}${r.offline_since ? ` · ${esc(offlineLabel(r))}` : ''}</p>
    <p>${esc(r.host)} · ${esc(r.runtime || 'Runtime not recorded')} · ${esc(r.model || 'Model not recorded')} · ${r.work_item ? `<span title="${esc(r.work_item)}">${esc(r.work_title || r.work_item)}</span>` : `<span data-unlinked title="No work item is linked. Linking attributes this run to an item; it does not complete the item.">Unlinked</span> <button data-copy="${esc(link)}" title="Copy a command to attribute this run to a work item. Replace WORK_ITEM before running; linking does not complete work.">Link command</button>`}</p>
    <p title="${esc(r.workspace_reason || '')}">${esc(branch || 'Branch not recorded')} · ${count(r.commit_count, 'commits')} · ${count(r.push_count, 'pushes')} · ${count(r.document_count, 'documents')}</p></li>`;
}
export function mountRunHistory(root, project, initial = {}, { title = 'History' } = {}) {
  let filters = { ...initial }, limit = 100, request = 0;
  root.innerHTML = `<section data-run-history><h2>${esc(title)}</h2><form data-history-filters>
    <label>Work item<input name="work_item" placeholder="Work item ID or unique prefix"${initial.work_item ? ' readonly' : ''} value="${esc(initial.work_item || '')}"></label>
    <label>Host<input name="host" placeholder="All hosts"></label>
    <label>Status<select name="status"><option value="">All statuses</option>${['running','succeeded','failed','stopped','unknown outcome'].map(s => `<option value="${s}">${s === 'unknown outcome' ? 'unknown outcome (includes queued and stalled)' : s}</option>`).join('')}</select></label>
    <label>Kind<select name="kind"><option value="">Jobs and sessions</option><option value="job">Jobs</option><option value="session">Sessions</option></select></label>
    <label>Since<input name="since" placeholder="e.g. 7d or 2026-10-01"></label><label>Until<input name="until" placeholder="Date or time"></label>
    <label><input type="checkbox" name="unlinked"> Unlinked only</label><button type="submit">Apply filters</button><button type="reset">Reset</button></form>
    <div data-history-results aria-live="polite"></div></section>`;
  const results = root.querySelector('[data-history-results]'), form = root.querySelector('form');
  async function load() {
    const at = ++request;
    results.innerHTML = '<p>Loading stored runs…</p>';
    try {
      const response = await fetch('/api/history/runs?' + new URLSearchParams({ ...(project ? { project } : {}), ...filters, limit }));
      const data = await response.json();
      if (at !== request || !root.isConnected) return;
      if (!response.ok) throw new Error(data.error || 'History could not be loaded');
      const groups = new Map();
      for (const run of data.runs) {
        const day = run.start ? new Date(run.start).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }) : 'Start date not recorded';
        if (!groups.has(day)) groups.set(day, []);
        groups.get(day).push(run);
      }
      results.innerHTML = `<p>${data.runs.length} of ${data.total} stored runs</p>` + (data.runs.length ? [...groups].map(([day, runs]) => `<section data-history-day><h3>${esc(day)}</h3><ul>${runs.map(row).join('')}</ul></section>`).join('') : `<p data-history-empty>${esc(data.empty_reason || 'No stored runs match these filters.')} ${esc(Object.entries(filters).map(([k,v]) => `${k}: ${v}`).join(' · '))}</p>`) + (data.runs.length < data.total ? '<button data-history-more>Show 100 more</button>' : '');
    } catch (error) { if (at === request && root.isConnected) results.innerHTML = `<p role="alert">${esc(error.message)}</p><button data-history-retry>Retry</button>`; }
  }
  form.addEventListener('submit', ev => {
    ev.preventDefault(); filters = { ...initial };
    for (const [key, value] of new FormData(form)) if (value.trim()) filters[key] = key === 'unlinked' ? 'true' : value.trim();
    limit = 100; load();
  });
  form.addEventListener('reset', () => { filters = { ...initial }; limit = 100; load(); });
  root.addEventListener('click', ev => {
    const copy = ev.target.closest('[data-copy]');
    if (copy) {
      ev.stopPropagation();
      const label = copy.textContent;
      const done = () => { copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = label; }, 1400); };
      if (navigator.clipboard?.writeText) navigator.clipboard.writeText(copy.dataset.copy).then(done, () => fallbackCopy(copy.dataset.copy, done));
      else fallbackCopy(copy.dataset.copy, done);
      return;
    }
    const run = ev.target.closest('[data-open-run]');
    if (run) { root.dispatchEvent(new CustomEvent('fleet:open-stored-run', { bubbles: true })); openArchivedRun(run.dataset.openRun); }
    if (ev.target.closest('[data-history-more]')) { limit += 100; load(); }
    if (ev.target.closest('[data-history-retry]')) load();
  });
  load();
}
