// The agent panel's Summary tab: what the agent is doing, told in its own narration, with the tool calls between
// narrations folded into counts. Deterministic: every line is cut from what the stream carried, never composed.

import { age, esc } from './util.js';

// ------------------------------------------------------------------ the trace: every event seen for an agent
// The stream carries only each agent's last few events; the deck keeps what it has seen so the summary and the
// Activity tab reach further back than one window.
const TRACE_MAX = 200;
const traces = new Map();   // entity key → events, oldest first
const rawKey = ev => `${ev.ts}|${ev.kind}|${ev.tool ?? ''}|${ev.summary ?? ev.status ?? ''}`;
export function noteTrace(key, events) {
  const w = events || [], h = traces.get(key);
  if (!h || !h.length) { traces.set(key, w.slice(-TRACE_MAX)); return traces.get(key); }
  if (!w.length) return h;
  // the window slides: find where its start overlaps the end of what we have
  for (let k = Math.min(h.length, w.length); k > 0; k--) {
    let same = true;
    for (let i = 0; i < k && same; i++) same = rawKey(h[h.length - k + i]) === rawKey(w[i]);
    if (same) { h.push(...w.slice(k)); if (h.length > TRACE_MAX) h.splice(0, h.length - TRACE_MAX); return h; }
  }
  // no overlap: newer events after a gap extend the trace; anything else is a different history
  if ((w[0].ts ?? 0) >= (h[h.length - 1].ts ?? 0)) { h.push(...w); if (h.length > TRACE_MAX) h.splice(0, h.length - TRACE_MAX); return h; }
  traces.set(key, w.slice(-TRACE_MAX));
  return traces.get(key);
}

// Events worth a row in the Activity tab, each with a key that stays put as the trace grows.
export function traceRows(events) {
  const seen = new Map();
  return events.filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').map(ev => {
    const raw = rawKey(ev), n = (seen.get(raw) ?? 0) + 1;
    seen.set(raw, n);
    return { ev, key: n > 1 ? `${raw}#${n}` : raw };
  });
}

// ------------------------------------------------------------------ grouping: narration plus the tools that followed it
const isThinking = ev => ev.kind === 'tool' && ev.tool === 'think';
// what a tool call counts as, in the order ties are listed
const COUNTS = [['edit', 'edit', 'edits'], ['bash', 'command', 'commands'], ['read', 'read', 'reads'], ['search', 'search', 'searches'],
  ['web', 'web lookup', 'web lookups'], ['delegate', 'helper', 'helpers'], ['question', 'question', 'questions'],
  ['other', 'other tool', 'other tools'], ['error', 'error', 'errors']];
const COUNT_ORDER = new Map(COUNTS.map(([k], i) => [k, i]));
function countKind(ev) {
  if (ev.kind === 'error') return 'error';
  if (ev.kind !== 'tool' || isThinking(ev) || ev.tool === 'plan') return null;   // todo updates show as Progress
  if (ev.name === 'AskUserQuestion') return 'question';
  return COUNT_ORDER.has(ev.tool) ? ev.tool : 'other';
}
// Rows (from traceRows) → groups, oldest first. A narration opens a group; tool calls join the open group. Tools with
// no narration before them in the step make a 'worked' group of their own. A new step closes the open group.
export function groupActivity(rows) {
  const groups = [];
  let open = null;
  for (const { ev, key } of rows) {
    if (ev.kind === 'text') { open = { narration: ev.summary || '', ask: ev.ask, ts: ev.ts, first: key, last: key, counts: {} }; groups.push(open); continue; }
    if (ev.kind === 'step' && ev.status === 'running') { open = null; continue; }
    const kind = countKind(ev);
    if (!kind) continue;
    if (!open) { open = { narration: null, ts: ev.ts, first: key, last: key, counts: {} }; groups.push(open); }
    open.counts[kind] = (open.counts[kind] ?? 0) + 1;
    open.last = key;
  }
  return groups;
}
// { edit: 3, bash: 2, read: 1 } → "3 edits · 2 commands · 1 read": most first, ties in a fixed order
export function countsText(counts) {
  return Object.entries(counts).sort(([a, x], [b, y]) => y - x || COUNT_ORDER.get(a) - COUNT_ORDER.get(b))
    .map(([k, n]) => { const [, one, many] = COUNTS[COUNT_ORDER.get(k)]; return `${n} ${n === 1 ? one : many}`; }).join(' · ');
}

// ------------------------------------------------------------------ trimming
// Cut at a word boundary, with an ellipsis when anything was cut.
export function clip(text, max) {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim();
  if (s.length <= max) return s;
  let cut = s.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  if (space > max / 2) cut = cut.slice(0, space);
  return cut.replace(/[\s,;:.–—-]+$/, '') + '…';
}
// The first sentence: up to a . ! or ? followed by a space (so "par.md." and "v5.2" stay whole).
export function firstSentence(text) {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim();
  const m = s.match(/^.+?[.!?](?=\s)/);
  return m ? m[0] : s;
}
export const ONE_LINE = 80, TWO_LINES = 150;
export const oneLine = text => clip(firstSentence(text), ONE_LINE);

// ------------------------------------------------------------------ the Summary tab
// Text budget (docs/design/workspace-prd.md, text budgets per level): the Summary is a glance, not the trace, so
// every section is capped; the Activity tab stays unlimited (L4).
export const RECENT_MAX = 5, FINISHED_MAX = 4, TODOS_MAX = 7;
// Worst cases (every line cut at its limit) fit these; a panel's ordinary day uses a third of them.
export const SUMMARY_BUDGET = { now: 90, progress: 125, recently: 140, finished: 80, total: 420 };

const TODO_MARK = { completed: '✓', in_progress: '▸' };
const STEP_MARK = { done: '✓', failed: '✗', blocked: '⚑', cancelled: '⊘' };
const ago = ts => ts ? `${age(ts)} ago` : '';

// What the agent is waiting on you for, if anything: an idle session, or an unresolved decision or blocker it owns.
// An idle session with no item shows the question its last message ended on, when it asked one.
function waitingHtml(work, session, items, said) {
  const asks = items.filter(i => i.kind === 'decision' || i.kind === 'blocker');
  if (!asks.length && !(session && work.status === 'idle')) return '';
  const lead = asks[0], question = lead ? lead.summary : said?.ask;
  return `<div class="sm-wait" role="status"><b>Waiting for you</b>${question ? `<span>${esc(clip(question, ONE_LINE))}</span>` : ''}
    ${lead && lead.kind === 'decision' ? `<button data-answer="${esc(lead.id)}">Answer</button>` : ''}</div>`;
}

// work: a job or a session (sessions have no steps or documents); rows from traceRows; items: its attention items
export function summarySections(key, work, session, rows, items, expanded) {
  const steps = work.steps || [], todos = work.todos || [];
  const groups = groupActivity(rows);
  const said = [...groups].reverse().find(g => g.narration);
  const running = steps.find(s => s.status === 'running');
  const doing = todos.find(t => t.status === 'in_progress');
  const done = steps.filter(s => s.status === 'done').length;
  const stepLine = running ? `<div class="sm-step"><span class="sm-n">Step ${running.index + 1} of ${steps.length}</span>${esc(clip(running.title, ONE_LINE))}</div>`
    : steps.length ? `<div class="sm-step"><span class="sm-n">${esc(work.status)}</span>${done} of ${steps.length} steps done</div>` : '';
  const now = `<h3>Now</h3>${waitingHtml(work, session, items, said)}${stepLine}
    ${doing ? `<div class="sm-todo">▸ ${esc(clip(doing.text, ONE_LINE))}</div>` : ''}
    ${said ? `<p class="sm-say">${esc(clip(said.narration, TWO_LINES))} <time>${esc(ago(said.ts))}</time></p>`
      : '<p class="sm-say muted">No narration yet.</p>'}`;

  let progress = '';
  if (todos.length) {
    const at = Math.max(0, todos.findIndex(t => t.status !== 'completed'));
    const start = todos.length <= TODOS_MAX ? 0 : Math.max(0, Math.min(at - 2, todos.length - TODOS_MAX));
    const shown = todos.slice(start, start + TODOS_MAX), after = todos.length - start - shown.length;
    progress = `<h3>Progress · ${todos.filter(t => t.status === 'completed').length}/${todos.length}</h3><ul class="todos sm-todos">
      ${start ? `<li class="more">${start} earlier</li>` : ''}
      ${shown.map(t => `<li class="${esc(t.status)}">${TODO_MARK[t.status] || '·'} ${esc(clip(t.text, ONE_LINE))}</li>`).join('')}
      ${after ? `<li class="more">${after} more</li>` : ''}</ul>`;
  }

  const recent = groups.slice(-RECENT_MAX).reverse();
  const recently = `<h3>Recently</h3>${recent.length ? `<ol class="sm-recent">${recent.map(g => {
    const open = expanded.has(`${key}|${g.first}`), counts = countsText(g.counts);
    const line = g.narration != null
      ? `<button class="sm-line" data-expand="${esc(g.first)}" aria-expanded="${open}">${esc(open ? g.narration : oneLine(g.narration))}</button>` : '';
    return `<li>${line}<time>${esc(age(g.ts))}</time>
      ${counts ? `<button class="sm-tools" data-jump="${esc(g.first)}" data-to="${esc(g.last)}" title="Show these in Activity">${g.narration != null ? '' : 'Worked: '}${esc(counts)}</button>` : ''}</li>`;
  }).join('')}</ol>` : '<p class="muted sm-empty">Nothing yet.</p>'}`;

  const ended = steps.filter(s => STEP_MARK[s.status] && s.result);
  const reports = new Map((work.documents || []).filter(d => d.kind === 'report' && d.step != null).map(d => [d.step, d]));
  const finished = ended.length ? `<h3>Finished steps · ${ended.length}</h3><ol class="sm-steps">
    ${ended.length > FINISHED_MAX ? `<li class="more">${ended.length - FINISHED_MAX} earlier</li>` : ''}
    ${ended.slice(-FINISHED_MAX).map(s => {
      const report = reports.get(s.index);
      return `<li class="${esc(s.status)}"><span class="si">${STEP_MARK[s.status]}</span><span class="t">${s.index + 1}. ${esc(clip(String(s.result).split('\n')[0], ONE_LINE))}</span>${
        report ? `<button data-doc="${esc(report.id)}" title="Read ${esc(report.name)}">Report →</button>` : ''}</li>`;
    }).join('')}</ol>` : '';

  return [now, progress, recently, finished].map((html, i) => `<section class="sm" data-part="${['now', 'progress', 'recently', 'finished'][i]}">${html}</section>`);
}
