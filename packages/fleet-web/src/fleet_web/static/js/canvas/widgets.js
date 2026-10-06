// Views read records and never change them. Each renders from the read model, scoped to the focused epic.
import { BAND_NAMES, VIEW_WIDTH, viewPosition } from './geometry.js';
import { STATUS, act, esc, statusColor } from './util.js';

export const PALETTE = [
  ['swimlanes', 'Swimlanes', '≡', 'Tasks in rows by agent, owner or status, across stages'],
  ['board', 'Status board', '▦', 'Counts of tasks by status or stage'],
  ['table', 'Task table', '☰', 'One row per task with chosen columns'],
  ['progress', 'Progress bars', '▬', 'How far each task has got through the workflow'],
  ['metric', 'Metric', '#', 'One number you want to keep in view'],
  ['attention', 'Needs you list', '!', 'Pending decisions, answerable in place'],
  ['doc', 'Document', '¶', 'An embedded excerpt of a document'],
  ['agents', 'Agents', '◎', 'Who is working on what, and how it is going'],
  ['charter', 'Charter', '★', 'North star, constitution and decision scope'],
  ['note', 'Note', '✎', 'Free text for you; agents ignore it unless it is in context'],
];
export const PALETTE_NAMES = Object.fromEntries(PALETTE.map((entry) => [entry[0], entry[1]]));

function stageName(model, id) {
  if (id === 'done') return 'Done';
  if (!id) return 'Not started';
  return (model.workflow.stages.find((stage) => stage.id === id) || { name: id }).name;
}

function epicName(model, id) {
  return id ? (model.epics.find((epic) => epic.id === id) || { title: 'Unknown epic' }).title : 'No epic';
}

export function scopedItems(model, ui) {
  return ui.focus ? model.items.filter((item) => item.epic === ui.focus) : model.items;
}

export function attentionCards(model, ui, attention) {
  if (!attention.length) return '<span class="cv-muted">Nothing needs a decision from you.</span>';
  return attention.map((entry) => attentionCard(model, entry)).join('');
}

// One thing waiting for you, answered the way it has to be: an approval, a decision's options, a blocked step's
// reply, a job's refused permissions, or a terminal question that can only be answered at its terminal.
export const drafts = {};

export function refusedList(entry) {
  return (entry.refused || []).map((refusal) => `<span class="cv-mono cv-small" style="white-space: pre-wrap; word-break: break-all">${esc(refusal.tool)}: ${esc(refusal.detail.slice(0, 300))}${refusal.denied ? ' · a deny rule refuses this' : ''}</span>`).join('');
}

export function attentionCard(model, entry) {
  const item = entry.item ? model.items.find((other) => other.id === entry.item) : null;
  const epic = entry.epic ? model.epics.find((other) => other.id === entry.epic) : null;
  const where = `<span class="cv-mono" style="font-size: 11px; color: var(--muted)">raised by ${esc(entry.why)}${epic ? ' · epic ' + esc(epic.title) : ''}${item && !epic ? ' · ' + esc(item.title) : ''}</span>`;
  let controls;
  if (entry.answer === 'blocked') {
    controls = `${entry.message ? `<span class="cv-small cv-soft" style="white-space: pre-line">${esc(entry.message.slice(0, 600))}</span>` : ''}
      <div class="cv-row" style="flex-wrap: nowrap"><input class="cv-input" aria-label="Reply to the agent" data-draft="${esc(entry.id)}" value="${esc(drafts[entry.id] || '')}" placeholder="Reply to the agent"><button class="cv-btn primary sm" ${act('answerBlocked', { id: entry.id })}>Send</button></div>`;
  } else if (entry.answer === 'refusal') {
    controls = `${refusedList(entry)}<div class="cv-row"><button class="cv-btn primary sm" ${act('op', { op: 'attention.allow', args: { id: entry.id } })}>Allow for this job</button><button class="cv-btn sm" ${act('op', { op: 'attention.dismiss', args: { id: entry.id } })}>Dismiss</button></div>`;
  } else if (entry.answer === 'terminal') {
    controls = '<span class="cv-small cv-muted">A session asked this at its terminal; answer it there.</span>';
  } else if (entry.answer === 'decision' && entry.options && entry.options.length) {
    controls = `<div class="cv-row">${entry.options.map((option) => `<button class="cv-btn sm" ${act('resolve', { id: entry.id, choice: 'approve', canvas: false, answer: option })}>${esc(option)}</button>`).join('')}</div>`;
  } else if (entry.answer === 'decision') {
    controls = `<div class="cv-row" style="flex-wrap: nowrap"><input class="cv-input" aria-label="Your answer" data-draft="${esc(entry.id)}" value="${esc(drafts[entry.id] || '')}" placeholder="Your answer"><button class="cv-btn primary sm" ${act('answerDecision', { id: entry.id })}>Answer</button></div>`;
  } else {
    controls = `<div class="cv-row"><button class="cv-btn primary sm" ${act('resolve', { id: entry.id, choice: 'approve', canvas: true, kind: entry.kind, item: entry.item })}>${esc(entry.ok)}</button>${entry.alt ? `<button class="cv-btn sm" ${act('resolve', { id: entry.id, choice: 'back', canvas: true, kind: entry.kind, item: entry.item })}>${esc(entry.alt)}</button>` : ''}</div>`;
  }
  return `<div class="cv-box" data-key="attn-${esc(entry.id)}"><span style="font-weight: 500">${esc(entry.text)}</span>${where}${controls}</div>`;
}

export function renderWidget(model, ui, view, attention, drag) {
  const width = VIEW_WIDTH[view.type] || 420;
  const at = viewPosition(view, drag);
  const selected = ui.selected && ui.selected.kind === 'widget' && ui.selected.id === view.id;
  const items = scopedItems(model, ui);
  const options = view.options || {};
  const order = model.workflow.stages.map((stage) => stage.id).concat(['done']);
  let body = '';
  if (view.type === 'swimlanes') {
    let rows = items;
    if (options.filter === 'active') rows = rows.filter((item) => item.status !== 'done');
    if (options.filter === 'needs me') rows = rows.filter((item) => item.status === 'waiting-you' || item.status === 'waiting-criteria');
    const rowOf = (item) => options.rows === 'priority' ? BAND_NAMES[item.band || 'later']
      : options.rows === 'epic' ? epicName(model, item.epic)
        : options.rows === 'agent' ? (item.run && item.run.agent ? item.run.agent : 'no agent running')
          : options.rows === 'owner' ? (item.owner || 'unassigned')
            : options.rows === 'status' ? STATUS[item.status][0]
              : (item.region ? (model.regions.find((region) => region.id === item.region) || { name: 'region' }).name : 'no region');
    const colOf = (item) => options.columns === 'stage' ? (item.stage || 'none') : item.status;
    const cols = options.columns === 'stage'
      ? ['none'].concat(order).filter((key) => key !== 'none' || rows.some((item) => !item.stage))
      : Object.keys(STATUS).filter((key) => rows.some((item) => item.status === key));
    const label = (key) => options.columns === 'stage' ? stageName(model, key === 'none' ? null : key) : STATUS[key][0];
    const names = [...new Set(rows.map(rowOf))];
    const cells = ['<div></div>'].concat(cols.map((key) => `<div class="cv-lane-cell" style="border-bottom: 1px solid var(--line)"><span style="font-size: 12px; font-weight: 600; color: var(--soft)">${esc(label(key))}</span></div>`));
    for (const name of names) {
      cells.push(`<div class="cv-lane-cell" style="background: #172124"><span style="font-size: 13px; font-weight: 600; color: var(--accent2)">${esc(name)}</span></div>`);
      for (const key of cols) {
        const chips = rows.filter((item) => rowOf(item) === name && colOf(item) === key).map((item) => `
          <button class="cv-lane-chip" style="border-left: 3px solid ${statusColor(item.status)}" ${act('select', { kind: 'task', id: item.id })}>
            <span style="font-size: 13px; font-weight: 500; line-height: 1.3">${esc(item.title)}</span>
            ${options.show !== 'next step' ? `<span class="cv-bar" style="height: 4px"><div style="width: ${item.progress}%; background: ${statusColor(item.status)}"></div></span>` : ''}
            ${options.show !== 'progress' ? `<span style="font-size: 11px; color: #b7c2c5">${esc(item.next)}</span>` : ''}
          </button>`).join('');
        cells.push(`<div class="cv-lane-cell" style="background: #161e21; min-height: 48px">${chips}</div>`);
      }
    }
    body = `<div style="display: grid; grid-template-columns: 120px repeat(${Math.max(1, cols.length)}, minmax(0, 1fr)); gap: 6px">${cells.join('')}</div>`
      + (rows.length ? '' : `<span class="cv-muted">No tasks match this view's filter.</span>`);
  } else if (view.type === 'board') {
    const keys = options.group === 'status' ? Object.keys(STATUS) : options.group === 'epic' ? model.epics.map((epic) => epic.id) : order.concat(['none']);
    const tiles = keys.map((key) => ({
      label: options.group === 'status' ? STATUS[key][0] : options.group === 'epic' ? epicName(model, key) : stageName(model, key === 'none' ? null : key),
      count: items.filter((item) => options.group === 'status' ? item.status === key : options.group === 'epic' ? item.epic === key : (item.stage || 'none') === key).length,
      color: options.group === 'status' ? STATUS[key][1] : options.group === 'epic' ? (model.epics.find((epic) => epic.id === key) || {}).color : '#9fe0cb',
    })).filter((tile) => tile.count > 0);
    body = `<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 8px">${tiles.map((tile) =>
      `<div class="cv-tile" style="border-top: 3px solid ${tile.color}"><b>${tile.count}</b><span class="cv-small cv-soft">${esc(tile.label)}</span></div>`).join('')}</div>`
      + (tiles.length ? '' : '<span class="cv-muted">No tasks yet.</span>');
  } else if (view.type === 'table') {
    const rank = { stage: (item) => item.stage ? order.indexOf(item.stage) : -1, status: (item) => Object.keys(STATUS).indexOf(item.status),
      progress: (item) => -item.progress, cost: (item) => -(item.spent || 0) };
    const rows = items.slice().sort((a, b) => rank[options.sort || 'stage'](a) - rank[options.sort || 'stage'](b));
    const cols = options.columns === 'cost'
      ? [['Task', (item) => item.title], ['Status', (item) => STATUS[item.status][0]], ['Spent', (item) => '$' + (item.spent || 0).toFixed(2)], ['Budget', (item) => item.budget == null ? '—' : '$' + item.budget]]
      : options.columns === 'compact' ? [['Task', (item) => item.title], ['Status', (item) => STATUS[item.status][0]]]
        : [['Task', (item) => item.title], ['Stage', (item) => stageName(model, item.stage)], ['Status', (item) => STATUS[item.status][0]], ['Next', (item) => item.next]];
    const template = options.columns === 'cost' ? '2fr 1fr 0.8fr 0.8fr' : options.columns === 'compact' ? '2fr 1fr' : '1.6fr 0.8fr 1fr 1.4fr';
    body = `<div style="display: grid; grid-template-columns: ${template}; gap: 6px 12px; align-items: baseline">`
      + cols.map((col) => `<span class="cv-label" style="padding-bottom: 4px">${col[0]}</span>`).join('')
      + rows.map((item) => cols.map((col, index) => `<span style="font-size: 13px; ${index === 0 ? 'font-weight: 500' : col[0] === 'Status' ? 'font-weight: 600; color: ' + statusColor(item.status) : 'color: var(--soft)'}">${esc(col[1](item))}</span>`).join('')).join('')
      + '</div>';
  } else if (view.type === 'progress') {
    const rows = items.filter((item) => options.include === 'all' || (item.status !== 'done' && item.stage));
    body = rows.map((item) => `<div class="cv-stack" style="gap: 3px">
      <div class="cv-between" style="font-size: 13px"><span>${esc(item.title)}</span><span style="font-size: 12px; color: ${statusColor(item.status)}">${esc(STATUS[item.status][0])} · ${esc(stageName(model, item.stage))}</span></div>
      <div class="cv-bar thick"><div style="width: ${item.progress}%; background: ${statusColor(item.status)}"></div></div></div>`).join('')
      || '<span class="cv-muted">Nothing in progress.</span>';
  } else if (view.type === 'metric') {
    const tests = { 'waiting on you': (item) => item.status === 'waiting-you' || item.status === 'waiting-criteria', done: (item) => item.status === 'done',
      working: (item) => item.status === 'working', paused: (item) => item.status === 'paused', 'in test': (item) => item.stage === 'test' };
    const value = items.filter(tests[options.metric || 'waiting on you']).length;
    body = `<span style="font-size: 44px; font-weight: 600; line-height: 1">${value}</span><span class="cv-soft">of ${items.length} tasks · ${esc(options.metric)}${ui.focus ? ' · this epic' : ''}</span>`;
  } else if (view.type === 'attention') {
    body = attentionCards(model, ui, attention);
  } else if (view.type === 'agents') {
    const agents = model.schedule.slots.map((slot) => {
      const mine = items.filter((item) => item.run && item.run.agent === slot.agent && ['running', 'struggling', 'blocked', 'starting'].includes(item.run.state));
      const worst = mine.some((item) => item.status === 'blocked') ? 'blocked' : mine.some((item) => item.status === 'struggling') ? 'struggling'
        : mine.length ? 'working' : 'idle';
      const spent = model.items.reduce((sum, item) => sum + item.runs.filter((run) => run.agent === slot.agent).reduce((acc, run) => acc + (run.cost || 0), 0), 0);
      return { slot, mine, worst, spent, show: options.show === 'all' || mine.length > 0 };
    }).filter((entry) => entry.show);
    body = agents.map((entry) => `<div class="cv-stack" style="gap: 4px; padding: 8px 0; border-top: 1px solid #222c30">
      <div class="cv-between"><b>${esc(entry.slot.agent)}</b><span style="font-size: 12px; font-weight: 600; color: ${entry.worst === 'idle' ? 'var(--muted)' : statusColor(entry.worst)}">${entry.worst === 'idle' ? 'Idle' : STATUS[entry.worst][0]}</span></div>
      ${entry.mine.map((item) => `<button class="cv-btn link" style="align-self: flex-start; font-size: 13px; text-align: left" ${act('session', { id: item.id })}>${esc(item.run.role)} on ${esc(item.title)}</button>`).join('')}
      <span class="cv-mono" style="font-size: 11px; color: var(--muted)">${entry.slot.busy}/${entry.slot.cap} slots · spent $${entry.spent.toFixed(2)}${entry.slot.ready ? '' : ' · no host set up'}</span></div>`).join('')
      || '<span class="cv-muted">No agent is working right now.</span>';
  } else if (view.type === 'charter') {
    const charter = model.charter;
    body = `<span class="cv-label accent">North star</span><span style="font-size: 16px; font-weight: 500; line-height: 1.4">${esc(charter.north_star || 'No north star yet.')}</span>
      <span class="cv-mono cv-small cv-soft">charter v${charter.version} · ${charter.clauses.length} clauses</span>
      <button class="cv-btn" style="align-self: flex-start" ${act('reader', { kind: 'section', id: 'charter' })}>Read and edit in reader</button>`;
  } else if (view.type === 'note') {
    body = `<textarea class="cv-textarea" data-overlay="1" data-note="${esc(view.id)}" aria-label="Note" rows="5" style="background: #1d1a10; border-color: #4a4228"
      placeholder="Write anything. Agents read a note only if you drop it in a context region.">${esc(view.text || '')}</textarea>`;
  } else if (view.type === 'doc') {
    const key = options.doc === 'north star' ? 'star' : options.doc;
    const title = key === 'spec' ? `Spec · v${model.spec.version}` : key === 'report' ? 'Status report · live' : 'North star';
    const text = key === 'spec' ? (model.spec.text || 'No spec text yet.') : key === 'report' ? reportSummary(model) : (model.charter.north_star || 'No north star yet.');
    body = `<span style="font-size: 17px; font-weight: 600">${esc(title)}</span><span class="cv-soft" style="line-height: 1.5">${esc(text)}</span>
      <button class="cv-btn" style="align-self: flex-start" ${act('doc', { id: key })}>Open document</button>`;
  }
  const guidance = (view.compiled.lines || []).filter((line) => line.kind === 'guide').map((line) => line.raw.trim());
  const context = model.context.some((entry) => entry.kind === 'view' && entry.doc === view.id);
  return `<div class="cv-widget ${view.type === 'note' ? 'note' : ''} ${selected ? 'sel' : ''}" data-key="w-${esc(view.id)}"
    style="left: ${at.x}px; top: ${at.y}px; width: ${width}px">
    <div class="cv-widget-head" tabindex="0" data-drag="widget:${esc(view.id)}"><b>${esc(view.title)}</b><span class="cv-chip">${esc(PALETTE_NAMES[view.type] || view.type)} · v${view.version}${view.personal ? ' · yours' : ''}${context ? ' · in context' : ''}</span></div>
    <div class="cv-widget-body">${body}${guidance.length ? `<span style="font-size: 11px; color: var(--guide)">~ Guidance an agent will try to honour: ${esc(guidance.join('; '))}</span>` : ''}</div>
  </div>`;
}

export function reportSummary(model) {
  const count = (test) => model.items.filter(test).length;
  const done = count((item) => item.status === 'done'), you = count((item) => item.status === 'waiting-you');
  const paused = count((item) => item.status === 'paused'), working = count((item) => item.status === 'working');
  return `${done} of ${model.items.length} tasks done. ${working} being worked on, ${you} waiting for your approval`
    + (paused ? `, ${paused} paused` : '') + `. Workflow v${model.workflow.version}: ${model.workflow.flow}.`;
}
