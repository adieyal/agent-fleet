import { duration, esc } from './util.js';
import { openStoredReader } from './reader.js';

const list = (entries, render, empty) => entries?.length ? `<ul>${entries.map(x => `<li>${render(x)}</li>`).join('')}</ul>` : `<p class="muted">${empty}</p>`;
export function archivedPanes(detail) {
  const r = detail.run, steps = detail.steps || [], trace = detail.trace || {}, events = trace.events || {};
  const docs = detail.kept_documents || [];
  let recorded = [];
  if (events.content) for (const line of events.content.split('\n').filter(Boolean)) {
    try { const ev = JSON.parse(line); recorded.push(`${ev.ts ? new Date(ev.ts * 1000).toLocaleString() + ' · ' : ''}${ev.summary || ev.status || ev.kind || line}`); }
    catch { recorded.push(line); }
  }
  return {
    summary: `<h3>Stored run</h3><p>${esc(r.status)}${r.reason ? ` · ${esc(r.reason)}` : ''}</p><p>${esc(r.host)} · ${esc(r.runtime || 'Runtime not recorded')} · ${esc(r.kind)}</p><p>${r.start ? esc(new Date(r.start).toLocaleString()) : 'Start not recorded'} · ${r.duration_seconds == null ? 'Duration not recorded' : duration(r.duration_seconds)}</p><p>${esc(r.work_title || r.work_item || 'Unlinked: no work item')}</p><p>${esc(r.workspace?.branch || r.workspace_reason || 'Workspace not recorded')}</p><h3>Steps</h3>${list(steps, s => `<b>${s.index + 1}. ${esc(s.title || 'Title not recorded')}</b> · ${esc(s.status)}${s.result ? `<p>${esc(s.result)}</p>` : ''}<p>${esc(s.git?.reason || '')}</p><h4>Commits${s.git?.commit_count != null ? ` · ${s.git.commit_count}` : ''}</h4>${list(s.git?.commits, c => `<code>${esc(c.sha?.slice(0,8))}</code> ${esc(c.subject)}`, Array.isArray(s.git?.commits) ? 'No commits in this step' : 'Commits not recorded')}<h4>Pushes</h4>${list(s.git?.pushes, p => `${esc(p.remote || '')} ${esc(p.ref || '')} ${esc(p.sha || p.head || '')}`, Array.isArray(s.git?.pushes) ? 'No pushes in this step' : 'Pushes not recorded')}`, 'No steps recorded')}`,
    activity: `<h3>Kept trace</h3><p>${esc(events.availability || 'unavailable')}${events.reason ? ` · ${esc(events.reason)}` : ''}</p>${list(recorded, esc, 'No kept events available')}<h3>Worker source</h3><p>${esc(trace.source?.availability || 'Not recorded')}</p>${list(trace.source?.raw, raw => `${esc(raw.name || raw.path || raw.kind || 'Raw trace')} · ${esc(raw.availability || 'Availability not recorded')}${raw.reason ? ` · ${esc(raw.reason)}` : ''}`, 'Raw trace availability not recorded')}${trace.copy_error ? `<p>${esc(trace.copy_error)}</p>` : ''}`,
    documents: `<h3>Documents · ${docs.length}</h3>${list(docs, (d) => `<button data-kept-doc="${docs.indexOf(d)}"${d.stored ? '' : ' disabled'}>${esc(d.name || d.id)}</button> · ${d.stored ? 'kept on controller' : esc(d.error || 'No kept copy available')}`, 'No kept documents recorded')}<h3>Indexed references</h3>${list(detail.documents, d => `${esc(d.title || d.id)} · ${esc(d.availability || 'Availability not recorded')}`, 'No indexed references recorded')}`,
  };
}
export function readArchivedDocument(detail, index) {
  const d = detail.kept_documents[index];
  if (!d?.stored) return;
  const r = detail.run;
  openStoredReader('/api/library/job?' + new URLSearchParams({ project: d.scope, job: d.job_key, id: d.id }), d,
    { host: r.host, id: r.remote_job_id, description: r.title, agent: r.runtime });
}
