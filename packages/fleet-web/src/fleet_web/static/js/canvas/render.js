// Renders the whole canvas from the read model and the person's UI state as one HTML string.
import {
  BANDS, BAND_NAMES, CARD_W, bandLayout, blockPosition, columnRects, docPosition, epicPosition, epicRect, frameRect,
  itemPosition, regionRect,
} from './geometry.js';
import { EPIC_STAGE, LEVELS, STATUS, act, bind, clock, codeLines, esc, lighten, plural, statusColor, tint, who } from './util.js';
import { PALETTE, PALETTE_NAMES, attentionCard, renderWidget, reportSummary } from './widgets.js';

const ICON = (path) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const ICONS = {
  select: '<path d="M5 3l13 7-5.5 1.8L10.7 17z"/><path d="M12.6 12.6l5 5"/>',
  region: '<rect x="3.5" y="3.5" width="17" height="17" rx="3" stroke-dasharray="3 3"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  stage: '<rect x="3" y="4" width="6" height="16" rx="1.5"/><rect x="11" y="4" width="6" height="9" rx="1.5"/><path d="M19.5 15v6M16.5 18h6"/>',
  palette: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
  attn: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  docs: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M10 12h5M10 16h5"/>',
  log: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  out: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5"/>',
  in: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>',
  fit: '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
};

function icon(name, label, action, argument, on, alert, badge) {
  return `<button class="cv-icon ${on ? 'on' : ''} ${alert ? 'alert' : ''}" title="${esc(label)}" aria-label="${esc(label)}"`
    + `${on !== undefined ? ` aria-pressed="${on ? 'true' : 'false'}"` : ''} ${act(action, argument)}>${ICON(ICONS[name])}`
    + `${badge ? `<span class="cv-badge">${badge}</span>` : ''}</button>`;
}

export function epicOf(model, id) {
  return model.epics.find((epic) => epic.id === id) || null;
}

export function itemOf(model, id) {
  return model.items.find((item) => item.id === id) || null;
}

function stageName(model, id) {
  if (id === 'done') return 'Done';
  if (!id) return 'no stage';
  return (model.workflow.stages.find((stage) => stage.id === id) || { name: id }).name;
}

export function scopedAttention(model, ui) {
  const epicFor = (entry) => entry.epic || (entry.item ? (itemOf(model, entry.item) || {}).epic : null);
  return model.attention.filter((entry) => !ui.focus || epicFor(entry) === ui.focus);
}

export function recipient(model, ui) {
  const sel = ui.selected;
  if (!sel || !['task', 'session', 'epic', 'stage', 'zone', 'widget', 'sched'].includes(sel.kind)) return null;
  if (sel.kind === 'task' || sel.kind === 'session') {
    const item = itemOf(model, sel.id);
    if (!item) return null;
    const run = item.run;
    return { key: `task:${item.id}`, target: { kind: 'task', id: item.id },
      label: run && run.agent && ['running', 'struggling', 'blocked'].includes(run.state) ? `${run.role} ${run.agent} · ${item.title}` : `orchestrator · ${item.title}` };
  }
  if (sel.kind === 'epic') {
    const epic = epicOf(model, sel.id);
    return epic ? { key: `epic:${epic.id}`, target: { kind: 'epic', id: epic.id }, label: `owner of ${epic.title}` } : null;
  }
  if (sel.kind === 'stage') return { key: `stage:${sel.id}`, target: { kind: 'stage', id: sel.id }, label: `orchestrator · stage ${stageName(model, sel.id)}` };
  if (sel.kind === 'zone') {
    const region = model.regions.find((entry) => entry.id === sel.id);
    return region ? { key: `zone:${region.id}`, target: { kind: 'zone', id: region.id }, label: `orchestrator · region ${region.name}` } : null;
  }
  if (sel.kind === 'widget') {
    const view = model.views.find((entry) => entry.id === sel.id);
    return view ? { key: `view:${view.id}`, target: { kind: 'view', id: view.id }, label: `orchestrator · view ${view.title}` } : null;
  }
  return { key: 'sched:main', target: { kind: 'sched', id: 'main' }, label: 'orchestrator · scheduler' };
}

// ---------------------------------------------------------------- header
function header(model, ui) {
  const focus = ui.focus ? epicOf(model, ui.focus) : null;
  const attention = scopedAttention(model, ui);
  const canvasTools = ui.mode === 'canvas' ? `
    ${icon('select', 'Select and move (pan by dragging empty canvas)', 'tool', 'select', ui.tool === 'select')}
    ${icon('region', 'Draw a region', 'tool', 'region', ui.tool === 'region')}
    ${icon('link', 'Draw a dependency: click the task that must finish first, then the task that waits for it', 'tool', 'link', ui.tool === 'link')}
    ${icon('stage', 'Draft a stage to add to the workflow', 'draftStage')}
    <span class="cv-sep" aria-hidden="true"></span>
    ${icon('palette', 'Components and region presets', 'togglePalette', undefined, ui.showPalette)}` : '';
  const live = ui.live.ok ? 'live' : 'reconnecting…';
  return `<header class="cv-head">
    <div class="cv-brand"><b><a href="/" title="Back to the deck">Fleet</a></b>
      <nav class="cv-crumbs" aria-label="Breadcrumb">
        <button class="cv-btn link" style="color: ${focus ? 'var(--accent2)' : 'var(--muted)'}; font-size: 14px; text-decoration: none" ${act('exitFocus')}>${esc(model.name)}</button>
        ${focus ? `<span class="sep">›</span><span style="font-weight: 500">${esc(focus.title)} (${esc(focus.ref)})</span><button class="cv-btn sm" style="margin-left: 6px" ${act('exitFocus')}>Leave epic</button>` : ''}
      </nav>
    </div>
    <div class="cv-tools" role="toolbar" aria-label="Canvas tools">
      <div class="cv-seg" role="radiogroup" aria-label="View">
        <button role="radio" aria-checked="${ui.mode === 'canvas'}" ${act('mode', 'canvas')}>Canvas</button>
        <button role="radio" aria-checked="${ui.mode === 'reader'}" ${act('mode', 'reader')}>Reader</button>
      </div>
      ${canvasTools}
    </div>
    <div class="cv-row" style="gap: 2px">
      ${icon('attn', 'Needs you', 'drawer', 'attn', ui.drawer === 'attn', attention.length > 0, attention.length || '')}
      ${icon('docs', 'Documents', 'drawer', 'docs', ui.drawer === 'docs')}
      ${icon('log', 'Runtime log', 'toggleLog', undefined, ui.showLog)}
      <span class="cv-sep" aria-hidden="true"></span>
      ${ui.mode === 'canvas' ? `${icon('out', 'Zoom out', 'zoom', 1 / 1.2)}<span class="cv-mono cv-small cv-muted" style="min-width: 40px; text-align: center">${Math.round(ui.zoom * 100)}%</span>${icon('in', 'Zoom in', 'zoom', 1.2)}${icon('fit', 'Fit to view', 'fit')}<span class="cv-sep" aria-hidden="true"></span>` : ''}
      <span class="cv-live ${ui.live.ok ? '' : 'bad'}" title="Updates arrive as records change">${live}</span>
    </div>
  </header>`;
}

// ---------------------------------------------------------------- world
function world(model, ui) {
  const zoom = ui.zoom, drag = ui.drag, sel = ui.selected || {};
  const layout = model.layout || {};
  const frame = frameRect(model), cols = columnRects(model), bands = bandLayout(model);
  const parts = [];
  for (const region of model.regions) {
    const rect = regionRect(region, drag);
    const level = LEVELS[region.level] || LEVELS.enforced;
    const color = region.color || level[2];
    const light = region.color ? lighten(color, 0.45) : level[3];
    const selected = sel.kind === 'zone' && sel.id === region.id;
    const hover = ui.dropTarget === 'zone:' + region.id;
    const sub = region.sub ? `<span class="cv-zone-sub" style="font-weight: ${region.sub.tone === 'context' ? 400 : 600}; color: ${region.sub.tone === 'full' ? 'var(--amber)' : region.sub.tone === 'context' ? '#a9cfd8' : 'var(--accent2)'}">${esc(region.sub.text)}</span>` : '';
    parts.push(`<div class="cv-zone" data-key="z-${esc(region.id)}" style="left: ${rect.x}px; top: ${rect.y}px; width: ${rect.w}px; height: ${rect.h}px; background: ${tint(color, region.color ? 0.1 : 0.06)}; border: 2px ${level[1]} ${color}${selected || hover ? '; box-shadow: 0 0 0 3px rgba(232,228,218,0.35)' : ''}">
      <div class="cv-zone-head" tabindex="0" data-drag="zone:${esc(region.id)}"><span class="cv-zone-name" style="color: ${light}">${esc(region.name)}</span>
      <span class="cv-zone-level" style="color: ${light}">${level[0]} · &lt;/&gt; v${region.version} · ${esc(who(region.written_by))}</span>${sub}</div></div>`);
  }
  parts.push(`<div class="cv-frame" style="left: ${frame.x}px; top: ${frame.y}px; width: ${frame.w}px; height: ${frame.h}px"></div>`);
  for (const band of BANDS) {
    const open = model.items.filter((item) => (item.band || 'later') === band && item.status !== 'done').length;
    const limit = model.schedule.limits[band];
    parts.push(`<div class="cv-band-line" style="left: ${frame.x + 12}px; top: ${bands.tops[band] - 4}px; width: ${frame.w - 24}px"></div>
      <div class="cv-band-label ${band}" style="left: ${frame.x + 16}px; top: ${bands.tops[band] + 8}px"><b>${BAND_NAMES[band]}</b>
      <span>${limit !== undefined ? `${open} of ${limit}${open >= limit ? ' · full' : ''}` : `${open} open`}</span></div>`);
  }
  const queued = model.schedule.queue.length;
  parts.push(`<div class="cv-sched ${sel.kind === 'sched' ? 'sel' : ''}" tabindex="0" data-drag="sched:main" style="left: ${frame.x + frame.w - 460}px; top: ${frame.y - 98}px">
    <div class="cv-between"><span class="cv-label accent">Scheduler · v${model.schedule.version}</span><span class="cv-mono cv-small cv-muted">${queued} queued</span></div>
    <div class="cv-row">${model.schedule.slots.map((slot) => `<span class="cv-slot ${!slot.ready ? 'unset' : slot.busy >= slot.cap ? 'full' : ''}" title="${slot.ready ? '' : 'No host set up for this agent'}">${esc(slot.agent)} ${slot.busy}/${slot.cap}${slot.ready ? '' : ' · not set up'}</span>`).join('')}</div></div>`);
  parts.push(`<div class="cv-frame-label" style="left: ${frame.x}px; top: ${frame.y - 40}px"><b>Delivery workflow v${model.workflow.version}</b><span>${esc(model.workflow.flow)}</span></div>`);
  for (const col of cols) {
    const stage = model.workflow.stages.find((entry) => entry.id === col.id);
    const selected = sel.kind === 'stage' && sel.id === col.id;
    const hover = ui.dropTarget === 'col:' + col.id;
    parts.push(`<div class="cv-col ${stage ? '' : 'done'} ${selected ? 'sel' : ''} ${hover ? 'drop' : ''}" data-key="c-${esc(col.id)}" style="left: ${col.x}px; top: ${col.y}px; width: ${col.w}px; height: ${col.h}px">
      <div class="cv-col-head" ${stage ? `tabindex="0" data-drag="col:${esc(col.id)}"` : ''} style="cursor: ${stage ? 'pointer' : 'default'}">
        <div class="cv-between"><b>${esc(stage ? stage.name : 'Done')}</b>${stage ? `<span class="cv-chip">&lt;/&gt; v${stage.version} · ${esc(who(stage.written_by))}${stage.added_in > 1 ? ' · new in v' + stage.added_in : ''}</span>` : ''}</div>
        <span class="sub">${esc(stage ? stage.exit_text : 'terminal')}</span></div></div>`);
  }
  const doc = (id, width, inner) => {
    const at = docPosition(model, layout, id, drag);
    const inContext = model.context.some((entry) => entry.kind === 'doc' && entry.doc === id);
    return `<div class="cv-doc ${inContext ? 'ctx' : ''}" data-key="d-${id}" tabindex="0" data-drag="doc:${id}" style="left: ${at.x}px; top: ${at.y}px; width: ${width}px">${inner}${inContext ? '<span class="cv-small" style="color: #a9cfd8">¶ every agent here reads this</span>' : ''}</div>`;
  };
  const done = model.items.filter((item) => item.stage === 'done').length;
  const waitingCriteria = model.items.filter((item) => item.status === 'waiting-criteria');
  parts.push(doc('spec', 560, `<div class="cv-between"><span class="cv-label">Document</span><span class="cv-mono cv-small cv-muted">spec v${model.spec.version}</span></div>
    <h3>${esc(model.name)} spec</h3><span class="cv-soft">${esc(model.spec.text || 'No spec text yet. Open it to write one.')}</span>
    ${waitingCriteria.length ? `<span style="color: var(--guide-ink)">${plural(waitingCriteria.length, 'task')} waiting for acceptance criteria: ${esc(waitingCriteria.map((item) => item.title).join(', '))}</span>` : ''}
    <span class="cv-small cv-muted">Open it to write acceptance criteria; Plan's exit code reads them.</span>`));
  parts.push(doc('star', 440, `<span class="cv-label accent">North star</span><span style="font-size: 17px; font-weight: 500; line-height: 1.4">${esc(model.charter.north_star || 'No north star yet. Write one in the charter.')}</span>
    <span class="cv-mono cv-small cv-soft">${done} of ${model.items.length} items done</span>`));
  parts.push(doc('report', 440, `<div class="cv-between"><span class="cv-label">Document</span><span class="cv-mono cv-small cv-muted">live</span></div>
    <h3>Status report</h3><span class="cv-soft">${esc(reportSummary(model))}</span><span class="cv-small cv-muted">Click to read the full report</span>`));
  if (ui.tool === 'region') parts.push(`<div class="cv-hint-big" style="left: ${frame.x + frame.w + 60}px; top: ${frame.y + frame.h + 20}px; width: 520px">Drag anywhere on empty canvas to draw a region</div>`);
  for (const block of model.blocks) {
    const at = blockPosition(block, drag);
    parts.push(`<div class="cv-block" data-key="b-${esc(block.id)}" tabindex="0" data-drag="block:${esc(block.id)}" style="left: ${at.x}px; top: ${at.y}px">
      <span class="cv-label accent">New stage</span><span style="font-size: 18px; font-weight: 600">${esc(block.name)}</span>
      <span class="cv-small cv-soft">code drafted by ${esc(block.by)} · drag into the workflow</span></div>`);
  }
  model.epics.forEach((epic, index) => {
    const at = epicPosition(model, layout, epic, index, drag);
    const rect = epicRect(model, layout, epic, index, zoom);
    const selected = sel.kind === 'epic' && sel.id === epic.id;
    const hover = ui.dropTarget === 'epic:' + epic.id;
    const full = zoom >= 0.5;
    const stage = EPIC_STAGE[epic.stage] || EPIC_STAGE.shape;
    const label = stage[0] + (!epic.criteria.length ? ' · no acceptance criteria yet' : '')
      + (epic.criteria.length && (epic.stage === 'shape' || (epic.stage === 'deliver' && epic.gaps)) ? ` · ${plural(epic.gaps, 'criterion', 'criteria')} uncovered` : '');
    const kids = epic.children.map((id) => itemOf(model, id)).filter(Boolean);
    parts.push(`<div class="cv-epic ${ui.focus && ui.focus !== epic.id ? 'dim' : ''}" data-key="e-${esc(epic.id)}" style="left: ${at.x}px; top: ${at.y}px; min-height: ${rect.h}px; border-color: ${selected || ui.focus === epic.id || hover ? epic.color : 'var(--line2)'}">
      <div class="cv-epic-head" tabindex="0" data-drag="epic:${esc(epic.id)}">
        <div class="cv-between"><span class="cv-label" style="color: ${epic.color}">Epic · ${esc(epic.ref)}</span>${epic.attention ? `<span class="cv-attn-pill">${epic.attention} need you</span>` : ''}</div>
        <span class="cv-epic-name">${esc(epic.title)}</span>
        <span style="font-size: 13px; font-weight: 600; color: ${stage[1]}">${esc(label)}</span>
        <div class="cv-bar" style="height: 6px"><div style="width: ${epic.average}%; background: ${epic.color}"></div></div>
        <span class="cv-mono cv-small cv-soft">${epic.done} of ${kids.length} done · ${epic.working} working · ${epic.waiting} waiting or paused</span>
      </div>
      ${full ? `<div class="cv-epic-body">
        ${kids.map((item) => `<button class="cv-kid" ${act('select', { kind: 'task', id: item.id })}><span style="font-size: 13px">${esc(item.title)}</span><span style="color: ${statusColor(item.status)}">${esc(STATUS[item.status][0])}</span></button>`).join('')}
        <span style="font-size: 12px; color: ${epic.gaps ? 'var(--amber)' : 'var(--accent)'}">${epic.criteria.length ? (epic.gaps ? `${epic.gaps} of ${epic.criteria.length} criteria not yet covered by a task with criteria` : 'Every criterion is covered by a child task') : 'No acceptance criteria yet'}</span>
        <div class="cv-row"><button class="cv-btn" ${act('enterEpic', epic.id)}>Enter epic</button><button class="cv-btn" ${act('select', { kind: 'epic', id: epic.id })}>Criteria and workflow</button></div>
      </div>` : ''}</div>`);
  });
  const attention = scopedAttention(model, ui);
  for (const view of model.views) parts.push(renderWidget(model, ui, view, attention, drag));
  if (ui.ghost) {
    const name = ui.ghost.type.startsWith('preset:') ? (ui.ghost.type === 'preset:context' ? 'Context for agents' : 'Now / Next') : PALETTE_NAMES[ui.ghost.type];
    parts.push(`<div class="cv-ghost" style="left: ${ui.ghost.x}px; top: ${ui.ghost.y}px; width: 300px">${esc(name)}</div>`);
  }
  for (const dep of model.deps) {
    const first = itemOf(model, dep.from), waits = itemOf(model, dep.to);
    if (!first || !waits) continue;
    const a = itemPosition(model, layout, first, drag), b = itemPosition(model, layout, waits, drag);
    const x1 = a.x + CARD_W, y1 = a.y + 44, x2 = b.x - 6, y2 = b.y + 44;
    const minx = Math.min(x1, x2) - 80, miny = Math.min(y1, y2) - 80, w = Math.abs(x2 - x1) + 160, h = Math.abs(y2 - y1) + 160;
    const X1 = x1 - minx, Y1 = y1 - miny, X2 = x2 - minx, Y2 = y2 - miny;
    const stuck = ['struggling', 'blocked', 'waiting-you', 'waiting-criteria', 'paused'].includes(first.status);
    const color = first.status === 'done' ? '#3d6d60' : stuck ? '#efb44f' : '#8fa6ad';
    parts.push(`<div class="cv-arrow ${ui.focus && waits.epic !== ui.focus ? 'dim' : ''}" data-key="a-${esc(dep.from)}-${esc(dep.to)}" style="left: ${minx}px; top: ${miny}px; width: ${w}px; height: ${h}px">
      <svg width="${w}" height="${h}" style="overflow: visible" aria-hidden="true"><path d="M ${X1} ${Y1} C ${X1 + 70} ${Y1}, ${X2 - 70} ${Y2}, ${X2} ${Y2}" fill="none" stroke="${color}" stroke-width="2.5" stroke-dasharray="${first.status === 'done' ? '6 5' : 'none'}"/>
      <path d="M ${X2} ${Y2} L ${X2 - 11} ${Y2 - 6} L ${X2 - 11} ${Y2 + 6} Z" fill="${color}"/></svg></div>`);
  }
  const compact = zoom < 0.55;
  for (const item of model.items) {
    const at = itemPosition(model, layout, item, drag);
    const dragging = drag && drag.kind === 'task' && drag.id === item.id && drag.moved;
    const selected = (sel.kind === 'task' || sel.kind === 'session') && sel.id === item.id;
    const epic = item.epic ? epicOf(model, item.epic) : null;
    const color = statusColor(item.status);
    const pending = ui.pending[item.id];
    const stuck = (item.status === 'struggling' || item.status === 'blocked') && item.run && item.run.excerpt;
    const state = STATUS[item.status][0] + (item.run && item.status === 'working' ? ` · ${item.run.role} ${item.run.agent}` : '');
    const classes = ['cv-card', selected ? 'sel' : '', dragging ? 'dragging' : '', pending ? 'pending' : '',
      ui.linkFrom === item.id ? 'linkfrom' : '', !dragging && ui.focus && item.epic !== ui.focus ? 'dim' : ''].join(' ');
    parts.push(`<div class="${classes}" data-key="t-${esc(item.id)}" tabindex="0" data-drag="task:${esc(item.id)}" style="left: ${at.x}px; top: ${at.y}px; border-left: 4px solid ${epic ? epic.color : 'var(--dim)'}">
      <span class="cv-card-title">${esc(item.title)}</span>
      <span class="cv-card-state" style="color: ${color}">${esc(state)}</span>
      ${compact ? '' : `<span class="cv-card-hint">${esc(item.next)}</span>
      <span class="cv-card-epic" style="color: ${epic ? epic.color : 'var(--faint)'}">${esc(epic ? 'Epic · ' + epic.title : 'No epic')}</span>
      ${stuck ? `<div class="cv-excerpt ${item.status === 'blocked' ? 'blocked' : ''}"><span>${esc(item.run.excerpt)}</span><button class="cv-btn link" style="color: inherit; font-size: 12px; align-self: flex-start" ${act('session', { id: item.id })}>Open session</button></div>` : ''}
      <div class="cv-bar"><div style="width: ${item.progress}%; background: ${item.status === 'paused' ? '#7d6a99' : item.status === 'done' ? '#6cc9ad' : '#5f7f78'}"></div></div>
      ${item.badges.length ? `<div class="cv-row" style="gap: 4px">${item.badges.map((badge) => `<span class="cv-tag">${esc(badge)}</span>`).join('')}</div>` : ''}`}
    </div>`);
  }
  if (ui.drawing) {
    const d = ui.drawing;
    parts.push(`<div class="cv-drawn" style="left: ${Math.min(d.x0, d.x1)}px; top: ${Math.min(d.y0, d.y1)}px; width: ${Math.abs(d.x1 - d.x0)}px; height: ${Math.abs(d.y1 - d.y0)}px"></div>`);
  }
  return `<div class="cv-world" style="transform: translate(${ui.pan.x}px, ${ui.pan.y}px) scale(${zoom})">${parts.join('')}</div>`;
}

// ---------------------------------------------------------------- side panels
function palette(model, ui, bottom) {
  const presets = [['nownext', 'Now / Next with a WIP limit', '⇥', 'Now holds two items; agents pull from Next when there is room'],
    ['context', 'Context for agents', '¶', 'Drop documents or notes in; every agent in the space reads them']];
  return `<aside class="cv-palette" data-overlay="1" aria-label="Components" style="bottom: ${bottom}px">
    <div class="cv-panel-head"><b>Components</b><button class="cv-btn sm" ${act('togglePalette')}>Close</button></div>
    <p class="cv-small cv-muted" style="margin: 0; padding: 10px 14px 0">Drag onto the canvas, or click to place in view. Views read records and never change them.</p>
    <div class="cv-panel-body">
      ${PALETTE.map((entry) => `<div class="cv-pal" data-palette="${entry[0]}" role="button" tabindex="0" ${act('placeWidget', entry[0])}><span class="glyph">${esc(entry[2])}</span><span class="cv-stack" style="gap: 2px"><span style="font-weight: 500">${esc(entry[1])}</span><span class="cv-small cv-muted">${esc(entry[3])}</span></span></div>`).join('')}
      <span class="cv-label" style="margin: 10px 4px 2px">Region presets</span>
      <span class="cv-small cv-muted" style="margin: 0 4px 4px">Regions change the rules for what you put in them. Their code is shown and editable once placed.</span>
      ${presets.map((entry) => `<div class="cv-pal preset" data-palette="preset:${entry[0]}" role="button" tabindex="0" ${act('placePreset', entry[0])}><span class="glyph">${esc(entry[2])}</span><span class="cv-stack" style="gap: 2px"><span style="font-weight: 500">${esc(entry[1])}</span><span class="cv-small cv-muted">${esc(entry[3])}</span></span></div>`).join('')}
    </div></aside>`;
}

function codeEditor(ui, id, label, button) {
  const preview = ui.preview && !ui.preview.error ? `<span class="cv-label">How Fleet reads it</span>${codeLines(ui.preview)}` : ui.preview && ui.preview.error ? `<span class="cv-err">${esc(ui.preview.error)}</span>` : '';
  return `<label for="${id}" class="cv-small cv-soft">${esc(label)}</label>
    <textarea id="${id}" class="cv-textarea code" rows="12" spellcheck="false" ${bind('codeDraft')} data-preview="1">${esc(ui.codeDraft)}</textarea>
    ${ui.codeError ? `<span class="cv-err">${esc(ui.codeError)}</span>` : ''}
    <div class="cv-row"><button class="cv-btn primary" ${act('compile')}>${esc(button)}</button><button class="cv-btn" ${act('cancelCode')}>Cancel</button></div>${preview}`;
}

const LEGEND = '<span class="cv-small cv-muted"><span style="color: var(--accent)">✓ compiled</span>: the kernel runs it exactly. <span style="color: var(--guide)">~ guidance</span>: an agent interprets it and cites the line. <span style="color: var(--faint)">· off</span>: a label.</span>';

function recentFor(model, id) {
  return model.log.filter((event) => event.subject === id).slice(-6).reverse();
}

function taskInspector(model, ui, item) {
  const epic = item.epic ? epicOf(model, item.epic) : null;
  const approval = model.attention.find((entry) => entry.item === item.id && entry.kind === 'Approve');
  const region = item.region ? model.regions.find((entry) => entry.id === item.region) : null;
  const where = [epic ? `Epic ${epic.title}` : null, item.stage ? `In ${stageName(model, item.stage)} · workflow v${item.pin || model.workflow.version}` : 'Not in the workflow',
    region ? `in region ${region.name}` : null, `owner ${item.owner || 'unassigned'}`, item.budget != null ? `budget $${item.budget}` : null, item.spent ? `spent $${item.spent.toFixed(2)}` : null]
    .filter(Boolean).join(' · ');
  const evidence = Object.values(item.facts.evidence || {}).some(Boolean);
  const facts = [['Revision submitted', item.facts.submitted], ['Test evidence on current revision', evidence],
    ['Acceptance criteria in spec', item.facts.has_criteria], ['Approved by you', item.facts.approved]];
  const deps = item.waits_on.map((id) => ({ id, first: id, waits: item.id, text: 'Waits for' })).concat(item.holds_up.map((id) => ({ id, first: item.id, waits: id, text: 'Holds up' })));
  const editing = ui.criteriaEditing === item.id;
  const last = item.runs[0];
  const canRetry = !item.run && item.stage && item.stage !== 'done' && last && ['failed', 'stopped'].includes(last.state);
  const coverage = epic ? `<span class="cv-label">Covers the epic's criteria</span>${epic.criteria.length ? epic.criteria.map((criterion) => {
    const on = item.covers.includes(criterion.id);
    return `<label class="cv-row" style="font-size: 13px; gap: 8px"><input type="checkbox" ${on ? 'checked' : ''} ${act('cover', { item: item.id, criterion: criterion.id })}> ${esc(criterion.text)}</label>`;
  }).join('') : '<span class="cv-small cv-muted">The epic has no criteria yet.</span>'}` : '';
  return `<span style="font-size: 19px; font-weight: 600; line-height: 1.3">${esc(item.title)}</span>
    <span class="cv-pill" style="color: ${statusColor(item.status)}">${esc(STATUS[item.status][0])}</span>
    <div class="cv-bar" style="height: 6px"><div style="width: ${item.progress}%"></div></div>
    <span class="cv-small cv-soft">${esc(where)}</span>
    <div class="cv-box"><span class="cv-label">What happens next</span><span style="font-size: 15px; line-height: 1.5">${esc(item.next_long)}</span>
      ${approval ? `<div class="cv-row"><button class="cv-btn primary" ${act('resolve', { id: approval.id, choice: 'approve', canvas: true })}>Approve</button><button class="cv-btn" ${act('resolve', { id: approval.id, choice: 'back', canvas: true })}>Send back</button></div>` : ''}
      ${canRetry ? `<div class="cv-row"><button class="cv-btn" ${act('op', { op: 'run.request', args: { item: item.id, role: last.role } })}>Run the ${esc(last.role)} again</button></div>` : ''}
      ${!item.stage ? `<div class="cv-row"><button class="cv-btn" ${act('op', { op: 'item.move', args: { item: item.id, stage: model.workflow.stages[0] ? model.workflow.stages[0].id : 'done' } })}>Start it in ${esc(stageName(model, model.workflow.stages[0] && model.workflow.stages[0].id))}</button></div>` : ''}
    </div>
    <div class="cv-row"><button class="cv-btn" ${act('reader', { kind: 'task', id: item.id })}>Open in reader</button><button class="cv-btn" ${act('session', { id: item.id })}>Session details (advanced)</button></div>
    <span class="cv-label">Facts the workflow checks</span>
    ${facts.map((fact) => `<div class="cv-fact"><span>${fact[0]}</span><span class="v ${fact[1] ? 'yes' : ''}">${fact[1] ? 'yes' : 'no'}</span></div>`).join('')}
    <span class="cv-label">Acceptance criteria</span>
    ${editing ? `<label for="crit-edit" class="cv-small cv-soft">One criterion per line. Changed criteria lose their evidence.</label>
      <textarea id="crit-edit" class="cv-textarea" rows="4" ${bind('critDraft')}>${esc(ui.critDraft)}</textarea>
      <div class="cv-row"><button class="cv-btn primary" ${act('saveCriteria', item.id)}>Save to the spec</button><button class="cv-btn" ${act('cancelCriteria')}>Cancel</button></div>`
    : `${item.criteria.length ? item.criteria.map((criterion) => `<span style="font-size: 13px">• ${esc(criterion.text)}</span>`).join('') : '<span class="cv-small" style="color: var(--guide-ink)">None yet.</span>'}
      <button class="cv-btn sm" style="align-self: flex-start" ${act('editCriteria', item.id)}>Edit criteria</button>`}
    ${coverage}
    <span class="cv-label">Priority and dependencies</span>
    <span class="cv-small cv-soft">Band ${BAND_NAMES[item.band || 'later']}. Drag the card up or down the board to change it.</span>
    ${deps.map((dep) => { const other = itemOf(model, dep.id); return `<div class="cv-between" style="align-items: center; font-size: 13px"><span>${dep.text}: ${esc(other ? other.title : dep.id)} (${esc(other ? STATUS[other.status][0].toLowerCase() : '')})</span><button class="cv-btn sm" ${act('op', { op: 'dep.remove', args: { from: dep.first, to: dep.waits } })}>Remove</button></div>`; }).join('')}
    ${deps.length ? '' : '<span class="cv-small cv-muted">No dependencies. Use the link tool to draw one.</span>'}
    ${model.context.length ? `<span class="cv-label">Context every agent here reads</span>${model.context.map((entry) => `<span class="cv-small" style="color: #a9cfd8">¶ ${esc(entry.title)}</span>`).join('')}` : ''}
    ${item.guidance.length ? `<span class="cv-label">Guidance on this task and its epic</span>${item.guidance.map((entry) => `<span class="cv-small cv-guide">~ ${esc(entry.text)} <span class="cv-muted">(${esc(who(entry.author))})</span></span>`).join('')}` : ''}
    <span class="cv-label">Recent</span>
    ${recentFor(model, item.id).map((event) => `<span class="cv-small cv-soft">${clock(event.time)} · ${esc(event.text)}</span>`).join('') || '<span class="cv-small cv-muted">Nothing yet.</span>'}
    <span class="cv-label">Governed by</span>
    ${item.refs.map((ref) => `<div class="cv-between" style="align-items: center"><span class="cv-mono cv-small" style="color: var(--accent2)">${esc(ref.label)}</span>${ref.kind !== 'workflow' ? `<button class="cv-btn sm" ${act('select', { kind: ref.kind === 'zone' ? 'zone' : 'stage', id: ref.id })}>See code</button>` : ''}</div><span class="cv-small cv-muted">${esc(ref.note)}</span>`).join('') || '<span class="cv-small cv-muted">No code acts on it yet.</span>'}`;
}

function sessionInspector(model, ui, item) {
  const run = item.run || item.runs[0];
  const active = !!item.run;
  const stuck = item.status === 'struggling' || item.status === 'blocked';
  const others = model.schedule.slots.map((slot) => slot.agent).filter((agent) => !run || agent !== run.agent);
  const summary = [];
  if (run) summary.push(`The ${run.role} (${run.agent || 'not assigned yet'}) ${active ? 'is on' : 'was on'} “${item.title}” in ${stageName(model, item.stage)}.`);
  if (model.context.length) summary.push(`Reading ${plural(model.context.length, 'context item')} you placed: ${model.context.map((entry) => entry.title).join('; ')}.`);
  if (run && run.state === 'running') summary.push(run.simulated ? `Simulated run, ${run.progress || 0}% through.` : `Running on ${run.host || 'its host'}${run.fleet && run.fleet.current_action ? ': ' + run.fleet.current_action : ''}.`);
  if (run && run.state === 'starting') summary.push(run.queue_reason || 'Starting on its host.');
  if (run && run.state === 'queued') summary.push(run.queue_reason || 'Waiting for the scheduler.');
  if (stuck) summary.push(`${run.excerpt || 'It needs a permission or an answer'}. Retrying will not help; it needs a permission, an answer or a different agent.`);
  if (item.status === 'paused') summary.push('Paused. Nothing is running and nothing is being spent.');
  if (run && run.permit) summary.push(`Refused commands allowed ${run.permit === 'run' ? 'for this run' : run.permit === 'space' ? 'for runs in this space' : 'everywhere'}.`);
  if (run && !active && run.outcome) summary.push(`The last run ended: ${run.outcome}.`);
  const messages = (model.messages['task:' + item.id] || []).slice(-6);
  const budget = item.budget;
  return `<span class="cv-label">Session · run ${esc(run ? (run.fleet_run || run.id).slice(0, 12) : 'none')}</span>
    <span style="font-size: 18px; font-weight: 600; line-height: 1.3">${esc((run ? run.role + ' ' + (run.agent || '') + ' · ' : '') + item.title)}</span>
    <span class="cv-pill" style="color: ${statusColor(item.status)}">${esc(STATUS[item.status][0])}</span>
    ${run ? `<div class="cv-stack" style="gap: 4px"><span class="cv-small cv-soft">Spent $${(item.spent || 0).toFixed(2)}${budget != null ? ` of a $${budget} budget` : ''}${stuck ? ' · spending without progress' : ''}${run.host ? ` · ${esc(run.host)}:${esc(run.job || '')}` : ''}</span>
      ${budget ? `<div class="cv-bar" style="height: 6px"><div style="width: ${Math.min(100, Math.round((item.spent || 0) / budget * 100))}%; background: ${stuck ? 'var(--amber)' : '#5f7f78'}"></div></div>` : ''}</div>` : ''}
    <div class="cv-box"><span class="cv-label">Summary for you</span>${summary.map((line) => `<span style="font-size: 14px; line-height: 1.5">${esc(line)}</span>`).join('') || '<span class="cv-muted">No agent has run on this task yet.</span>'}
      <span style="font-size: 11px; color: var(--faint)">Written from the run's records, not the raw transcript.</span></div>
    <span class="cv-label">Do something</span>
    <div class="cv-row"><button class="cv-btn" ${act('op', { op: item.paused ? 'run.resume' : 'run.pause', args: { item: item.id } })}>${item.paused ? 'Resume' : 'Pause'}</button>
      ${!active && item.stage && item.stage !== 'done' ? `<button class="cv-btn" ${act('op', { op: 'run.request', args: { item: item.id, role: run ? run.role : 'builder' } })}>Request a ${esc(run ? run.role : 'builder')} run</button>` : ''}</div>
    ${stuck ? `<div class="cv-box warn"><span style="font-size: 13px">Allow the refused commands for:</span><div class="cv-row">
      <button class="cv-btn primary" ${act('op', { op: 'run.permit', args: { item: item.id, scope: 'run' } })}>This run</button>
      <button class="cv-btn" ${act('op', { op: 'run.permit', args: { item: item.id, scope: 'space' } })}>This space</button>
      <button class="cv-btn" ${act('op', { op: 'run.permit', args: { item: item.id, scope: 'everywhere' } })}>Everywhere</button></div></div>` : ''}
    <div class="cv-stack" style="gap: 4px"><label for="sess-msg" class="cv-small cv-soft">Send the agent a message</label>
      <div class="cv-row" style="flex-wrap: nowrap"><input id="sess-msg" class="cv-input" ${bind('sessDraft')} value="${esc(ui.sessDraft)}" placeholder="Use the warm image; don't rebuild cold"><button class="cv-btn" ${act('sendSession', item.id)}>Send</button></div>
      ${ui.sessError ? `<span class="cv-err">${esc(ui.sessError)}</span>` : ''}</div>
    ${active && others.length ? `<div class="cv-row"><span class="cv-small cv-soft">Stop and reassign to</span>${others.map((agent) => `<button class="cv-btn" ${act('op', { op: 'run.reassign', args: { item: item.id, agent } })}>${esc(agent)}</button>`).join('')}</div>` : ''}
    ${messages.length ? `<span class="cv-label">Messages</span>${messages.map((message) => `<span style="font-size: 13px"><span style="color: var(--accent2)">${esc(who(message.who))}:</span> ${esc(message.text)}</span>`).join('')}` : ''}
    <span class="cv-label">Runs, newest first</span>
    <div class="cv-code" style="line-height: 1.6">${item.runs.map((entry) => `<div class="ln"><span class="m" style="color: ${entry.state === 'failed' ? 'var(--red)' : entry.state === 'succeeded' ? 'var(--accent)' : 'var(--muted)'}">${entry.state === 'failed' ? '✗' : entry.state === 'succeeded' ? '✓' : '·'}</span><span class="t" style="color: #d9eee7">${esc(entry.role)} ${esc(entry.agent || '')} · ${esc(entry.state)}${entry.simulated ? ' · simulated' : ''}${entry.outcome && entry.outcome !== 'succeeded' ? ' · ' + esc(entry.outcome) : ''} · ${clock(entry.queued_at)}</span></div>`).join('') || '<span class="cv-muted">No runs yet.</span>'}</div>
    <button class="cv-btn" style="align-self: flex-start" ${act('select', { kind: 'task', id: item.id })}>Back to the task</button>`;
}

function codeInspector(model, ui, kind, record) {
  const zone = kind === 'zone';
  const compiled = record.compiled;
  const swatches = zone ? `<span class="cv-label" style="margin-top: 4px">Colour</span>
    <div role="radiogroup" aria-label="Region colour" class="cv-row">${model.zone_colors.map((color) => `<button role="radio" class="cv-swatch" aria-checked="${record.color === color}" aria-label="${color}" title="${color}" style="background: ${color}" ${act('op', { op: 'region.configure', args: { region: record.id, color } })}></button>`).join('')}
    <button class="cv-btn sm" ${act('op', { op: 'region.configure', args: { region: record.id, color: null } })}>Default</button></div>
    <span class="cv-small cv-muted">Colour is yours to choose and carries no meaning for Fleet. The border style still shows whether the region is enforced, guidance or a label.</span>` : '';
  const levels = zone ? `<div class="cv-row">${Object.keys(LEVELS).map((level) => `<button class="cv-level" aria-pressed="${record.level === level}" ${act('op', { op: 'region.configure', args: { region: record.id, level } })}>${LEVELS[level][0]}</button>`).join('')}</div>` : '';
  return `<span style="font-size: 17px; font-weight: 600">${zone ? 'Zone' : 'Stage'} · ${esc(record.name)}</span>
    <span class="cv-mono" style="font-size: 11px; color: var(--muted)">${esc(record.label)} · last written by ${esc(who(record.written_by))}${record.adopted_by && record.adopted_by !== record.written_by ? ', adopted by ' + esc(who(record.adopted_by)) : ''} · ${compiled.compiled} compiled, ${compiled.guided} guidance</span>
    <span class="cv-small cv-soft">${zone ? 'Runs when an item is dragged in or out of this region.' : 'Runs when an item enters this stage; its exit conditions are checked on every tick and on every move.'}</span>
    ${levels}${swatches}
    ${ui.editing ? codeEditor(ui, 'code-edit', "Lines Fleet doesn't recognise become guidance for agents", 'Compile as new version')
    : `${codeLines(compiled)}${LEGEND}<div class="cv-row"><button class="cv-btn" ${act('editCode')}>Edit code</button>${zone ? `<button class="cv-btn danger" ${act('op', { op: 'region.remove', args: { region: record.id } })}>Remove region</button>` : ''}</div>`}`;
}

function schedInspector(model, ui) {
  const schedule = model.schedule;
  const agents = schedule.slots.map((slot) => {
    const setting = model.settings.agents[slot.agent];
    const describe = !setting ? 'not set up: its runs stay queued' : setting.mode === 'simulate' ? `simulated, ${setting.seconds}s a run` : `${setting.runtime} on ${setting.host} in ${setting.cwd}`;
    const editing = ui.agentEditing === slot.agent;
    return `<div class="cv-item-row"><div class="cv-between"><b>${esc(slot.agent)}</b><span class="cv-mono cv-small ${setting ? '' : 'cv-muted'}">${esc(describe)}</span></div>
      ${editing ? `<div class="cv-stack"><label class="cv-small cv-soft" for="ag-host">Host</label><input id="ag-host" class="cv-input" ${bind('agentHost')} value="${esc(ui.agentHost)}" placeholder="worker">
        <label class="cv-small cv-soft" for="ag-cwd">Checkout on that host</label><input id="ag-cwd" class="cv-input" ${bind('agentCwd')} value="${esc(ui.agentCwd)}" placeholder="~/src/project">
        <div class="cv-row"><button class="cv-btn primary sm" ${act('saveAgent', slot.agent)}>Dispatch here</button><button class="cv-btn sm" ${act('simulateAgent', slot.agent)}>Simulate instead</button>${setting ? `<button class="cv-btn sm danger" ${act('op', { op: 'agent.configure', args: { agent: slot.agent, remove: true } })}>Remove</button>` : ''}<button class="cv-btn sm" ${act('editAgent', null)}>Cancel</button></div></div>`
        : `<button class="cv-btn sm" style="align-self: flex-start" ${act('editAgent', slot.agent)}>${setting ? 'Change' : 'Set up'}</button>`}</div>`;
  }).join('');
  return `<span style="font-size: 17px; font-weight: 600">Scheduler</span>
    <span class="cv-mono" style="font-size: 11px; color: var(--muted)">schedule v${schedule.version} · by ${esc(who(schedule.written_by))}</span>
    <span class="cv-small cv-soft">Dispatch is a request. Each tick the scheduler finds runnable work, orders it, and assigns it to free agent capacity on that agent's host.</span>
    <span class="cv-label">Slots</span>
    <div class="cv-row">${schedule.slots.map((slot) => `<span class="cv-slot ${!slot.ready ? 'unset' : slot.busy >= slot.cap ? 'full' : ''}">${esc(slot.agent)} ${slot.busy}/${slot.cap}</span>`).join('')}</div>
    <span class="cv-label">Agents</span>${agents}
    <span class="cv-label">Queue, in the order it will run</span>
    ${schedule.queue.length ? schedule.queue.map((entry, index) => { const item = itemOf(model, entry.item); return `<div class="cv-item-row"><span>${index + 1}. ${esc(entry.role)} for ${esc(item ? item.title : entry.item)} · band ${esc(entry.band)}</span><span class="cv-small" style="color: var(--guide)">${esc(entry.why || 'Next to start')}</span></div>`; }).join('') : '<span class="cv-small cv-muted">Nothing is waiting to run.</span>'}
    <span class="cv-label" style="margin-top: 4px">Policy, as code</span>
    ${ui.editing ? codeEditor(ui, 'sched-edit', "Change capacities or limits; lines Fleet doesn't recognise become guidance", 'Compile as new version')
    : `${codeLines(schedule.compiled)}<button class="cv-btn" style="align-self: flex-start" ${act('editCode')}>Edit policy</button>`}`;
}

function epicInspector(model, ui, epic) {
  const stage = EPIC_STAGE[epic.stage] || EPIC_STAGE.shape;
  const editing = ui.criteriaEditing === epic.id;
  return `<span class="cv-label">Epic · ${esc(epic.ref)}</span>
    <span style="font-size: 19px; font-weight: 600; line-height: 1.3">${esc(epic.title)}</span>
    <span class="cv-pill" style="color: ${stage[1]}">${esc(stage[0])}</span>
    <span class="cv-small cv-soft">${epic.done} of ${epic.children.length} done · ${epic.working} working · ${epic.waiting} waiting or paused · rolled up from its children, never stored</span>
    <div class="cv-box"><span class="cv-label">What happens next</span><span style="font-size: 15px; line-height: 1.5">${esc(epic.next)}</span></div>
    <span class="cv-label">Acceptance criteria and what covers them</span>
    ${editing ? `<label for="ecrit" class="cv-small cv-soft">One criterion per line</label><textarea id="ecrit" class="cv-textarea" rows="5" ${bind('critDraft')}>${esc(ui.critDraft)}</textarea>
      <div class="cv-row"><button class="cv-btn primary" ${act('saveCriteria', epic.id)}>Save</button><button class="cv-btn" ${act('cancelCriteria')}>Cancel</button></div>`
    : epic.criteria.map((criterion) => { const by = criterion.covered_by.map((id) => itemOf(model, id)).filter(Boolean);
      return `<div class="cv-item-row"><span style="font-size: 14px">${esc(criterion.text)}</span><span class="cv-small" style="color: ${criterion.ok ? 'var(--accent)' : 'var(--amber)'}">${by.length ? 'Covered by ' + esc(by.map((item) => item.title + (item.facts.has_criteria ? '' : ' (no criteria yet, so it doesn’t count)')).join(', ')) : 'Not covered by any task'}</span></div>`; }).join('')
      + `<button class="cv-btn sm" style="align-self: flex-start" ${act('editCriteria', epic.id)}>Edit criteria</button>`}
    <div class="cv-row">${epic.gaps ? `<button class="cv-btn primary" ${act('decompose', epic.id)}>Ask an agent to cover the gaps</button>` : ''}
      <button class="cv-btn" ${act('enterEpic', epic.id)}>Enter epic</button><button class="cv-btn" ${act('reader', { kind: 'epic', id: epic.id })}>Open in reader</button></div>
    <span class="cv-label" style="margin-top: 4px">Epic workflow · v${model.epicflow.version}</span>
    ${ui.editing ? codeEditor(ui, 'epic-edit', 'Every epic follows this workflow; lines Fleet does not recognise become guidance', 'Compile as new version')
    : `${codeLines(model.epicflow.compiled)}<button class="cv-btn" style="align-self: flex-start" ${act('editCode')}>Edit epic workflow</button>`}
    ${epic.guidance.length ? `<span class="cv-label">Guidance on this epic</span>${epic.guidance.map((entry) => `<span class="cv-small cv-guide">~ ${esc(entry.text)}</span>`).join('')}` : ''}
    <span class="cv-small cv-muted">Drag a task card onto an epic to move it there. A task only counts toward an epic once it covers one of the epic's criteria.</span>`;
}

function widgetInspector(model, ui, view) {
  const allowed = model.view_types[view.type] || {};
  return `<span style="font-size: 17px; font-weight: 600">${esc(view.title)}</span>
    <span class="cv-mono" style="font-size: 11px; color: var(--muted)">${esc(PALETTE_NAMES[view.type] || view.type)} · view v${view.version} · reads records, changes nothing${view.personal ? ' · only you see it' : ''}</span>
    ${Object.keys(allowed).map((key) => `<div class="cv-stack" style="gap: 4px"><span class="cv-small cv-soft">${esc(key.charAt(0).toUpperCase() + key.slice(1))}</span>
      <div class="cv-row" style="gap: 4px">${allowed[key].map((value) => `<button class="cv-level" aria-pressed="${view.options[key] === value}" ${act('op', { op: 'view.configure', args: { view: view.id, options: { [key]: value } } })}>${esc(value)}</button>`).join('')}</div></div>`).join('')}
    <span class="cv-label" style="margin-top: 4px">The view, as code</span>
    ${ui.editing ? codeEditor(ui, 'view-edit', "Options Fleet doesn't recognise are kept as guidance", 'Apply as new version')
    : `${codeLines(view.compiled)}<div class="cv-row"><button class="cv-btn" ${act('editCode')}>Edit code</button><button class="cv-btn danger" ${act('op', { op: 'view.remove', args: { view: view.id } })}>Remove from canvas</button></div>`}`;
}

function inspector(model, ui) {
  const sel = ui.selected;
  if (!sel) return null;
  if (sel.kind === 'task') { const item = itemOf(model, sel.id); return item ? ['Task', taskInspector(model, ui, item)] : null; }
  if (sel.kind === 'session') { const item = itemOf(model, sel.id); return item ? ['Session', sessionInspector(model, ui, item)] : null; }
  if (sel.kind === 'stage') { const stage = model.workflow.stages.find((entry) => entry.id === sel.id); return stage ? ['Stage code', codeInspector(model, ui, 'stage', stage)] : null; }
  if (sel.kind === 'zone') { const region = model.regions.find((entry) => entry.id === sel.id); return region ? ['Region code', codeInspector(model, ui, 'zone', region)] : null; }
  if (sel.kind === 'sched') return ['Scheduler', schedInspector(model, ui)];
  if (sel.kind === 'epic') { const epic = epicOf(model, sel.id); return epic ? ['Epic', epicInspector(model, ui, epic)] : null; }
  if (sel.kind === 'widget') { const view = model.views.find((entry) => entry.id === sel.id); return view ? ['Component', widgetInspector(model, ui, view)] : null; }
  return null;
}

function specDoc(model, ui) {
  return `<span class="cv-mono" style="font-size: 11px; color: var(--muted)">fleet://projects/${esc(model.space)}/spec · v${model.spec.version}</span>
    <span style="font-size: 22px; font-weight: 600">${esc(model.name)} spec</span>
    <h3 style="margin: 0; font-size: 15px; font-weight: 600">Purpose and scope</h3>
    ${ui.specEditing ? `<textarea class="cv-textarea" rows="6" ${bind('specDraft')} aria-label="Spec text">${esc(ui.specDraft)}</textarea>
      <div class="cv-row"><button class="cv-btn primary" ${act('saveSpec')}>Save as spec v${model.spec.version + 1}</button><button class="cv-btn" ${act('cancelSpec')}>Cancel</button></div>`
    : `<p style="margin: 0; color: #d9d4c9; line-height: 1.6; white-space: pre-line">${esc(model.spec.text || 'Nothing written yet.')}</p><button class="cv-btn sm" style="align-self: flex-start" ${act('editSpec')}>Edit</button>`}
    <h3 style="margin: 0; font-size: 15px; font-weight: 600">Acceptance criteria</h3>
    ${model.items.map((item) => `<div class="cv-item-row"><span style="font-weight: 500">${esc(item.title)}</span>
      ${ui.criteriaEditing === item.id ? `<textarea class="cv-textarea" rows="3" ${bind('critDraft')} aria-label="Criteria for ${esc(item.title)}">${esc(ui.critDraft)}</textarea><div class="cv-row"><button class="cv-btn primary sm" ${act('saveCriteria', item.id)}>Save as spec v${model.spec.version + 1}</button><button class="cv-btn sm" ${act('cancelCriteria')}>Cancel</button></div>`
      : `${item.criteria.map((criterion) => `<span style="font-size: 14px; color: #d9d4c9">${esc(criterion.text)}</span>`).join('') || '<span style="font-size: 14px; color: var(--guide-ink)">[to be written]</span>'}<button class="cv-btn sm" style="align-self: flex-start; margin-top: 4px" ${act('editCriteria', item.id)}>Edit</button>`}</div>`).join('')}
    <h3 style="margin: 0; font-size: 15px; font-weight: 600">Workflow</h3><p style="margin: 0; color: #d9d4c9">${esc(model.workflow.flow)} · v${model.workflow.version}. Each stage's exit code decides when work moves on.</p>`;
}

function drawerBody(model, ui) {
  if (ui.drawer === 'attn') {
    const attention = scopedAttention(model, ui);
    return ['Needs you', `<div class="cv-stack gap10">${attention.length ? '' : '<p style="margin: 0" class="cv-muted">Nothing needs a decision from you.</p>'}
      ${attention.map((entry) => attentionCard(model, entry)).join('')}
      ${model.notes.length ? `<span class="cv-label" style="margin-top: 6px">From your rules</span>${model.notes.slice(0, 8).map((note) => `<div class="cv-box" style="padding: 8px 10px; gap: 2px"><span>${esc(note.text)}</span><div class="cv-between" style="align-items: center"><span class="cv-mono" style="font-size: 11px; color: var(--muted)">${esc(note.why)}</span><button class="cv-btn sm" ${act('op', { op: 'note.dismiss', args: { id: note.id } })}>Dismiss</button></div></div>`).join('')}` : ''}</div>`];
  }
  if (ui.drawer === 'docs') {
    if (!ui.doc) {
      const docs = [['spec', `${model.name} spec`, `v${model.spec.version} · purpose, scope, acceptance criteria, workflow`], ['report', 'Status report', 'live · ' + reportSummary(model).split('.')[0]], ['star', 'North star', 'the outcome this space exists to produce']];
      return ['Documents', docs.map((doc) => `<button class="cv-box" style="text-align: left; color: var(--text); width: 100%" ${act('doc', { id: doc[0] })}><span style="font-weight: 500; font-size: 15px">${esc(doc[1])}</span><span class="cv-small cv-muted">${esc(doc[2])}</span></button>`).join('')];
    }
    const top = `<div class="cv-row"><button class="cv-btn" ${act('doc', { id: null })}>All documents</button><button class="cv-btn" ${act('locateDoc', ui.doc)}>Show on canvas</button></div>`;
    if (ui.doc === 'spec') return ['Documents', top + specDoc(model, ui)];
    if (ui.doc === 'report') {
      return ['Documents', top + `<span class="cv-mono" style="font-size: 11px; color: var(--muted)">written from records at ${clock(model.now)} · updates live</span>
        <span style="font-size: 22px; font-weight: 600">Status report</span><p style="margin: 0; font-size: 16px; line-height: 1.6">${esc(reportSummary(model))}</p>
        <h3 style="margin: 0; font-size: 15px; font-weight: 600">Each task</h3>
        ${model.items.map((item) => `<button class="cv-item-row" style="width: 100%; text-align: left; background: transparent; border: none; border-top: 1px solid #222c30; color: var(--text)" ${act('select', { kind: 'task', id: item.id })}><span class="cv-between"><span style="font-weight: 500">${esc(item.title)}</span><span style="font-size: 12px; font-weight: 600; color: ${statusColor(item.status)}">${esc(STATUS[item.status][0])}</span></span><span class="cv-small" style="color: #b7c2c5">${esc(item.next_long)}</span></button>`).join('')}
        <h3 style="margin: 0; font-size: 15px; font-weight: 600">Recent changes</h3>
        ${model.log.filter((event) => event.tone !== 'guide').slice(-6).reverse().map((event) => `<span class="cv-small cv-soft">${clock(event.time)} · ${esc(event.text)}</span>`).join('')}`];
    }
    const done = model.items.filter((item) => item.status === 'done').length;
    const waiting = model.attention.length;
    return ['Documents', top + `<span class="cv-label accent">North star</span>
      <p style="margin: 0; font-size: 19px; font-weight: 500; line-height: 1.45">${esc(model.charter.north_star || 'No north star yet.')}</p>
      <div class="cv-item-row"><span>Tasks done</span><span class="cv-small cv-soft">${done} of ${model.items.length}</span></div>
      <div class="cv-item-row"><span>Decisions waiting for you</span><span class="cv-small" style="color: ${waiting ? 'var(--amber)' : 'var(--accent)'}">${waiting || 'None'}</span></div>
      <div class="cv-item-row"><span>Every stage gated by evidence</span><span class="cv-small" style="color: ${model.workflow.stages.some((stage) => stage.id === 'test') ? 'var(--accent)' : 'var(--amber)'}">${model.workflow.stages.some((stage) => stage.id === 'test') ? 'Yes: Test stage in workflow v' + model.workflow.version : 'No test stage yet'}</span></div>
      <button class="cv-btn" style="align-self: flex-start" ${act('reader', { kind: 'section', id: 'charter' })}>Edit in the charter</button>`];
  }
  if (ui.drawer === 'select') return inspector(model, ui);
  return null;
}

// ---------------------------------------------------------------- the reader
function reader(model, ui) {
  const object = ui.readerObj;
  if (object && (object.kind === 'task' || object.kind === 'epic')) return readerObject(model, ui, object);
  let blocks = model.page.blocks;
  if (object && object.kind === 'section') {
    blocks = blocks.filter((block) => block.directive === object.id);
    if (!blocks.length) blocks = [{ kind: 'directive', directive: object.id }];
  }
  return `<div class="cv-reader" data-overlay="1"><article>
    <div class="cv-between" style="flex-wrap: wrap"><span class="cv-mono cv-small cv-muted">fleet://projects/${esc(model.space)} · reading view · written from records at ${clock(model.now)}</span>
      <div class="cv-row"><button class="cv-btn sm" ${act('togglePage')}>${ui.pageEditing ? 'Close page source' : 'Edit page'}</button>${object ? `<button class="cv-btn sm" ${act('reader', null)}>Whole page</button>` : ''}</div></div>
    ${ui.pageEditing ? `<div class="cv-box" style="background: #15201d; border-color: #2f5d51"><label for="page-src" class="cv-small cv-soft">This page is Markdown plus directives. Each :: line embeds a live component; reorder or remove them to change the page.</label>
      <textarea id="page-src" class="cv-textarea code" rows="9" spellcheck="false" ${bind('pageDraft')}>${esc(ui.pageDraft)}</textarea>
      <span class="cv-small cv-muted">Known directives: ${model.page.directives.map((name) => '::' + name).join(' ')}</span>
      <div class="cv-row"><button class="cv-btn primary" ${act('savePage')}>Save page</button><button class="cv-btn" ${act('togglePage')}>Cancel</button></div></div>` : ''}
    ${blocks.map((block) => readerBlock(model, ui, block)).join('')}
  </article></div>`;
}

function readerBlock(model, ui, block) {
  if (block.kind === 'title') return `<h1>${esc(block.text)}</h1>`;
  if (block.kind === 'text') return `<p>${esc(block.text)}</p>`;
  if (block.kind === 'guide') return `<span style="font-size: 13px; color: var(--guide)">~ ${esc(block.text)} isn't a known component yet. It has been kept as guidance for an agent to honour.</span>`;
  const name = block.directive;
  if (name === 'since-last-visit') {
    const events = (model.since_log || model.log).filter((event) => event.seq > model.seen && event.tone !== 'guide');
    const mine = events.filter((event) => event.actor === 'user' || event.actor === 'web-user');
    const refused = events.filter((event) => event.tone === 'refuse');
    const rest = events.filter((event) => !mine.includes(event) && event.tone !== 'refuse');
    const groups = [['Your decisions and changes', mine], ['Refused', refused], ['What Fleet did', rest]].filter((group) => group[1].length);
    const summary = events.length ? `Since ${clock(events[0].time)}: you made ${plural(mine.length, 'decision or change', 'decisions or changes')}, Fleet carried out ${plural(rest.length, 'operation')}${refused.length ? `, and ${plural(refused.length, 'thing was', 'things were')} refused` : ''}. ${plural(model.attention.length, 'decision is', 'decisions are')} waiting for you.`
      : 'Nothing new since you last marked this page as seen.';
    return `<section><div class="cv-between" style="flex-wrap: wrap"><h2>Since your last visit</h2><button class="cv-btn sm" ${act('op', { op: 'reader.mark_seen', args: {} })}>Mark all as seen</button></div>
      <p style="font-size: 16px">${esc(summary)}</p>
      ${groups.map((group) => `<div class="cv-stack" style="gap: 4px"><h3>${group[0]}</h3>${group[1].slice(-8).map((event) => `<div class="cv-since-row"><span class="t">${clock(event.time)}</span><span style="color: ${event.tone === 'refuse' ? 'var(--red)' : who(event.actor) === 'you' ? '#c8efe2' : '#d9d4c9'}">${esc((event.source === 'you' ? '' : event.source + (event.line ? ':' + event.line : '') + ' · ') + event.text)}</span></div>`).join('')}</div>`).join('')}</section>`;
  }
  if (name === 'needs-you') {
    return `<section><h2>Needs you</h2>${model.attention.length ? model.attention.map((entry) => attentionCard(model, entry)).join('') : '<p class="cv-muted" style="font-size: 15px">Nothing is waiting for a decision from you.</p>'}</section>`;
  }
  if (name === 'status-report') {
    return `<section><h2>How it's going</h2><p style="font-size: 16px">${esc(reportSummary(model))}</p>
      ${model.items.map((item) => `<div class="cv-item-row" style="padding: 10px 0"><div class="cv-between" style="flex-wrap: wrap"><button class="lnk" ${act('reader', { kind: 'task', id: item.id })}>${esc(item.title)}</button><span style="font-size: 12px; font-weight: 600; color: ${statusColor(item.status)}">${esc(STATUS[item.status][0])}</span></div><span style="font-size: 14px; color: #b7c2c5; line-height: 1.5">${esc(item.next_long)}</span></div>`).join('')}</section>`;
  }
  if (name === 'epics') {
    return `<section><h2>Epics</h2>${model.epics.map((epic) => { const stage = EPIC_STAGE[epic.stage] || EPIC_STAGE.shape; return `<div class="cv-item-row" style="padding: 10px 0; gap: 4px"><div class="cv-between" style="flex-wrap: wrap"><button class="lnk" style="font-size: 16px; font-weight: 600" ${act('reader', { kind: 'epic', id: epic.id })}>${esc(epic.title)} (${esc(epic.ref)})</button><span style="font-size: 12px; font-weight: 600; color: ${stage[1]}">${stage[0]}</span></div><span class="cv-soft" style="font-size: 14px">${epic.done} of ${epic.children.length} tasks done</span><span style="font-size: 13px; color: ${epic.gaps || !epic.criteria.length ? 'var(--amber)' : 'var(--accent)'}">${epic.gaps ? `${epic.gaps} of ${epic.criteria.length} criteria not yet covered` : epic.criteria.length ? 'Every criterion is covered' : 'No acceptance criteria yet'}</span></div>`; }).join('') || '<p class="cv-muted">No epics yet. Ask the orchestrator to open one.</p>'}</section>`;
  }
  if (name === 'charter') return charterSection(model, ui);
  if (name === 'spec') return `<section><div class="cv-between"><h2>Spec</h2><span class="cv-mono cv-small cv-muted">v${model.spec.version}</span></div>${specDoc(model, ui)}</section>`;
  return '';
}

function charterSection(model, ui) {
  const charter = model.charter;
  const kinds = model.scope_labels;
  const levels = [['decide', 'Agents decide'], ['tell', 'Decide, then tell me'], ['ask', 'Ask me first']];
  const agentsDecide = kinds.filter((kind) => charter.scope[kind[0]] !== 'ask').length;
  return `<section style="gap: 12px"><div class="cv-between" style="flex-wrap: wrap"><h2>Charter</h2><span class="cv-mono cv-small cv-muted">charter v${charter.version} · ${plural(charter.clauses.length, 'clause')} · agents decide ${agentsDecide} of ${kinds.length} kinds</span></div>
    <span class="cv-label accent">North star</span>
    ${ui.chEditing ? `<label for="ns-r" class="cv-small cv-soft">The outcome this space exists to produce</label><input id="ns-r" class="cv-input" ${bind('chDraft')} value="${esc(ui.chDraft)}">
      ${ui.chError ? `<span class="cv-err">${esc(ui.chError)}</span>` : ''}
      <div class="cv-row"><button class="cv-btn primary" ${act('saveNorthStar')}>Save as a new version</button><button class="cv-btn" ${act('cancelNorthStar')}>Cancel</button></div>`
    : `<div class="cv-row" style="align-items: flex-start"><p style="flex: 1 1 360px; font-size: 20px; line-height: 1.45; font-weight: 500">${esc(charter.north_star || 'No north star yet.')}</p><button class="cv-btn sm" ${act('editNorthStar')}>Edit</button></div>`}
    <span class="cv-label">Constitution</span>
    ${charter.clauses.map((clause, index) => `<div class="cv-row" style="gap: 6px 12px; align-items: baseline; padding: 8px 0; border-top: 1px solid #222c30"><span style="flex: 1 1 360px; font-size: 15px">${esc(clause.text)}</span><span class="cv-mono cv-small" style="color: ${clause.kind === 'enforced' ? 'var(--guide)' : 'var(--muted)'}">${clause.kind === 'enforced' ? 'Enforced by ' + esc(clause.rule) : 'Guidance · agents cite it'}</span>${clause.kind === 'guidance' ? `<button class="cv-btn sm" ${act('op', { op: 'item.create', args: { title: `Make enforceable: ${clause.text}`.slice(0, 200), goal: `Compile the charter clause “${clause.text}” into a named kernel rule, or say why it must stay guidance.` } })}>Make enforceable</button>` : ''}<button class="cv-btn sm" ${act('op', { op: 'charter.update', args: { patch: { remove_clause: index }, base: charter.version } })} aria-label="Remove clause">Remove</button></div>`).join('')}
    <div class="cv-row" style="align-items: flex-end; gap: 8px"><div class="cv-stack" style="flex: 1 1 300px; gap: 4px"><label for="cl-r" class="cv-small cv-soft">Add a clause</label><input id="cl-r" class="cv-input" ${bind('chClause')} value="${esc(ui.chClause)}" placeholder="Prefer reversible changes over fast ones"></div>
      <select class="cv-input" style="flex: 0 0 auto; width: auto" ${bind('clauseRule')} aria-label="Enforced by"><option value="">Guidance (agents cite it)</option>${model.rules.map((rule) => `<option value="${esc(rule)}" ${ui.clauseRule === rule ? 'selected' : ''}>Enforced: ${esc(rule)}</option>`).join('')}</select>
      <button class="cv-btn" style="min-height: 40px" ${act('addClause')}>Add</button></div>
    ${ui.chClauseErr ? `<span class="cv-err">${esc(ui.chClauseErr)}</span>` : ''}
    <span class="cv-label" style="margin-top: 6px">Decision scope</span>
    <span class="cv-soft" style="font-size: 14px">What agents may decide on their own here. Rules in force outrank it.</span>
    ${kinds.map((kind) => `<div class="cv-row" style="gap: 6px 14px; padding: 8px 0; border-top: 1px solid #222c30"><div class="cv-stack" style="flex: 1 1 220px; gap: 0"><span style="font-weight: 500">${esc(kind[1])}</span><span class="cv-small cv-muted">${esc(kind[2])}</span></div>
      <div class="cv-row" style="gap: 4px">${levels.map((level) => `<button class="cv-level ${level[0]}" aria-pressed="${charter.scope[kind[0]] === level[0]}" ${act('op', { op: 'charter.update', args: { patch: { scope: { [kind[0]]: level[0] } }, base: charter.version } })}>${level[1]}</button>`).join('')}</div></div>`).join('')}
  </section>`;
}

function readerObject(model, ui, object) {
  const line = (text, color) => `<span style="font-size: 15px; line-height: 1.6; color: ${color || '#d9d4c9'}">${esc(text)}</span>`;
  let address, title, status, color, next, sections, approval = null;
  if (object.kind === 'task') {
    const item = itemOf(model, object.id);
    if (!item) return `<div class="cv-reader" data-overlay="1"><article><p>That task is no longer in this space.</p><button class="cv-btn" ${act('reader', null)}>← Project page</button></article></div>`;
    const epic = item.epic ? epicOf(model, item.epic) : null;
    approval = model.attention.find((entry) => entry.item === item.id && entry.kind === 'Approve');
    address = `fleet://projects/${model.space}/work/${item.id}`;
    title = item.title; status = STATUS[item.status][0]; color = statusColor(item.status); next = item.next_long;
    sections = [
      ['Where it is', [line(`${epic ? `Epic ${epic.title}. ` : ''}${item.stage ? `Stage ${stageName(model, item.stage)}, band ${item.band}.` : 'Not in the workflow yet.'} Owner ${item.owner || 'unassigned'}${item.budget != null ? `; budget $${item.budget}` : ''}, spent $${(item.spent || 0).toFixed(2)}.`)]],
      ['Goal', [line(item.goal)]],
      ['Acceptance criteria', item.criteria.map((criterion) => line(criterion.text)).concat(item.criteria.length ? [] : [line('[to be written]', 'var(--guide-ink)')])
        .concat([line(`Revision submitted: ${item.facts.submitted ? 'yes' : 'no'} · test evidence: ${Object.values(item.facts.evidence || {}).some(Boolean) ? 'yes' : 'no'} · approved: ${item.facts.approved ? 'yes' : 'no'}`, 'var(--muted)')])],
      ['Dependencies', item.waits_on.map((id) => line('Waits for ' + ((itemOf(model, id) || {}).title || id))).concat(item.holds_up.map((id) => line('Holds up ' + ((itemOf(model, id) || {}).title || id)))).concat(item.waits_on.length + item.holds_up.length ? [] : [line('None.', 'var(--muted)')])],
      ['Guidance', item.guidance.map((entry) => line('~ ' + entry.text, 'var(--guide-ink)')).concat(item.guidance.length ? [] : [line('None yet. Select the card and write to its owner to add some.', 'var(--muted)')])],
      ['History', recentFor(model, item.id).map((event) => line(`${clock(event.time)} · ${event.text}`, 'var(--soft)'))],
    ];
  } else {
    const epic = epicOf(model, object.id);
    if (!epic) return `<div class="cv-reader" data-overlay="1"><article><p>That epic is no longer in this space.</p><button class="cv-btn" ${act('reader', null)}>← Project page</button></article></div>`;
    const stage = EPIC_STAGE[epic.stage] || EPIC_STAGE.shape;
    address = `fleet://projects/${model.space}/epics/${epic.ref}`;
    title = epic.title; status = stage[0]; color = stage[1]; next = epic.next;
    sections = [
      ['Criteria and what covers them', epic.criteria.map((criterion) => line(`${criterion.text} — ${criterion.covered_by.length ? 'covered by ' + criterion.covered_by.map((id) => (itemOf(model, id) || {}).title).join(', ') : 'not covered'}`, criterion.ok ? '#c8efe2' : 'var(--guide-ink)'))],
      ['Tasks', epic.children.map((id) => itemOf(model, id)).filter(Boolean).map((item) => line(`${item.title} · ${STATUS[item.status][0].toLowerCase()}`))],
      ['Guidance', epic.guidance.map((entry) => line('~ ' + entry.text, 'var(--guide-ink)')).concat(epic.guidance.length ? [] : [line('None yet.', 'var(--muted)')])],
    ];
  }
  return `<div class="cv-reader" data-overlay="1"><article>
    <div class="cv-row"><button class="cv-btn sm" ${act('reader', null)}>← Project page</button><button class="cv-btn sm" ${act('locate', object)}>Open on canvas</button></div>
    <span class="cv-mono cv-small cv-muted">${esc(address)}</span>
    <h1 style="font-size: 30px; line-height: 1.2">${esc(title)}</h1>
    <span class="cv-pill" style="color: ${color}">${esc(status)}</span>
    <p>${esc(next)}</p>
    ${approval ? `<div class="cv-row"><button class="cv-btn primary" ${act('resolve', { id: approval.id, choice: 'approve', canvas: true })}>Approve</button><button class="cv-btn" ${act('resolve', { id: approval.id, choice: 'back', canvas: true })}>Send back</button></div>` : ''}
    ${sections.map((section) => `<section style="gap: 6px"><h2 style="font-size: 16px">${esc(section[0])}</h2>${section[1].join('')}</section>`).join('')}
  </article></div>`;
}

// ---------------------------------------------------------------- message bar, log, dialogs
function messageBar(model, ui, left, right, bottom) {
  const target = recipient(model, ui);
  const key = target ? target.key : 'orch';
  const messages = (model.messages[key] || []).slice(-6);
  const proposals = Object.fromEntries(model.proposals.map((proposal) => [proposal.id, proposal]));
  const convo = !ui.convoHidden && messages.length ? `<div class="cv-convo" role="log" aria-label="Conversation">
    <div class="cv-between"><span class="cv-label">Conversation · ${esc(target ? target.label : 'orchestrator')}</span><button class="cv-btn sm" ${act('hideConvo')}>Hide</button></div>
    ${messages.map((message) => `<div class="cv-stack" style="gap: 4px"><span class="who ${who(message.who) === 'you' ? 'you' : ''}">${esc(who(message.who) === 'you' ? 'You' : message.who)} · ${clock(message.time)}</span><span class="text">${esc(message.text)}</span>
      ${(message.proposals || []).map((id) => proposals[id]).filter(Boolean).map((proposal) => `<div class="cv-prop"><span>${esc(proposal.desc)}</span>${proposal.state === 'open'
        ? `<button class="cv-btn primary sm" ${act('proposal', { id: proposal.id, adopt: true })}>Adopt</button><button class="cv-btn sm" ${act('proposal', { id: proposal.id, adopt: false })}>Discard</button>`
        : `<span class="cv-mono cv-small" style="color: var(--accent2)">${proposal.state === 'adopted' ? 'Adopted' : 'Discarded'}</span>`}</div>`).join('')}</div>`).join('')}
    ${ui.sending ? `<span class="cv-small cv-muted">${esc(target ? target.label.split(' · ')[0] : 'The orchestrator')} is replying…</span>` : ''}</div>` : '';
  return `<div class="cv-msgbar" data-overlay="1" style="left: ${left}px; right: ${right}px; bottom: ${bottom}px"><div class="cv-msgbar-inner">${convo}
    <form class="cv-cmd" data-submit="sendCmd"><span class="to">To: ${esc(target ? target.label : 'the orchestrator')}</span>
      ${target ? `<button type="button" class="cv-btn link" style="font-size: 12px; color: var(--muted)" ${act('toOrchestrator')} aria-label="Send to the orchestrator instead">orchestrator instead</button>` : ''}
      <label for="cmd" class="cv-sr">Message</label>
      <input id="cmd" autocomplete="off" ${bind('cmdDraft')} value="${esc(ui.cmdDraft)}" placeholder="${esc(target ? 'Ask why, or tell it what to do differently' : 'Ask for something: “park everything waiting on the deploy”, “what’s costing the most?”')}">
      <button type="submit" class="cv-btn primary">Send</button></form>
    ${ui.cmdError ? `<span class="cv-err" style="padding-left: 10px">${esc(ui.cmdError)}</span>` : ''}</div></div>`;
}

function runtimeLog(model) {
  return `<section class="cv-log" data-overlay="1" aria-label="Runtime log"><div class="cv-panel-head" style="padding: 8px 12px"><span class="cv-label">Runtime · what ran, from which line</span><span class="cv-mono" style="font-size: 11px; color: var(--muted)">newest first</span></div>
    <div class="cv-log-body">${model.log.slice(-80).reverse().map((event) => `<div class="cv-log-row"><span class="t">${clock(event.time)}</span><span class="s">${esc(event.source)}${event.line ? ':' + event.line : ''}${event.actor && who(event.actor) !== 'you' && event.actor !== event.source ? ' · ' + esc(event.actor) : ''}</span><span class="tone-${esc(event.tone)}">${esc(event.text)}</span></div>`).join('')}</div></section>`;
}

function dialogs(model, ui) {
  if (ui.naming) {
    return `<div class="cv-backdrop" data-overlay="1"><form class="cv-dialog narrow" role="dialog" aria-label="Name this region" data-submit="submitName">
      <h2>What is this region for?</h2><label for="zone-name" class="cv-small cv-soft">Name it the way you'd say it. The orchestrator will propose what it means.</label>
      <input id="zone-name" class="cv-input" ${bind('nameDraft')} value="${esc(ui.nameDraft)}" placeholder="Parked" autocomplete="off">
      ${ui.nameError ? `<span class="cv-err">${esc(ui.nameError)}</span>` : ''}
      <div class="cv-row"><button type="submit" class="cv-btn primary">Interpret it</button><button type="button" class="cv-btn" ${act('cancelName')}>Cancel</button></div></form></div>`;
  }
  if (ui.regionProposal) {
    const proposal = ui.regionProposal;
    const sentences = [];
    const groups = { enter: 'When an item enters: ', exit: 'When it leaves: ', capacity: 'Capacity: ', agents: 'Agents: ', scope: 'Scope: ' };
    for (const section of Object.keys(groups)) {
      const ops = proposal.compiled.lines.filter((line) => line.section === section && line.kind === 'op').map((line) => line.describe);
      if (ops.length) sentences.push(`<span style="color: #d9eee7">${esc(groups[section] + ops.join('; ') + '.')}</span>`);
    }
    proposal.compiled.lines.filter((line) => line.kind === 'guide').forEach((line) => sentences.push(`<span class="cv-guide">Interpreted by an agent each time: “${esc(line.raw.trim())}”</span>`));
    return `<div class="cv-backdrop" data-overlay="1"><div class="cv-dialog" role="dialog" aria-label="Proposed meaning">
      <span class="cv-label accent">the orchestrator's reading of “${esc(proposal.name)}”</span>${sentences.join('')}${codeLines(proposal.compiled)}
      <span class="cv-small cv-soft">Enforce compiles the ✓ lines into kernel behaviour. Guidance makes every line advice that agents interpret and cite. A label has no effect at all. You can change this later.</span>
      <div class="cv-row"><button class="cv-btn primary" ${act('adoptRegion', 'enforced')}>Enforce</button><button class="cv-btn" ${act('adoptRegion', 'guidance')}>Guidance only</button><button class="cv-btn" ${act('adoptRegion', 'label')}>Just a label</button><button class="cv-btn" ${act('discardRegion')}>Discard</button></div></div></div>`;
  }
  if (ui.insert) {
    const block = model.blocks.find((entry) => entry.id === ui.insert.block);
    if (!block) return '';
    const stages = model.workflow.stages;
    const previous = stages[ui.insert.index - 1], next = stages[ui.insert.index];
    const after = stages.slice(ui.insert.index).map((stage) => stage.id);
    const affected = model.items.filter((item) => after.includes(item.stage) && !item.pin);
    const version = model.workflow.version + 1;
    return `<div class="cv-backdrop" data-overlay="1"><div class="cv-dialog" role="dialog" aria-label="Change the workflow">
      <h2>Insert ${esc(block.name)} between ${esc(previous ? previous.name : 'the start')} and ${esc(next ? next.name : 'Done')}?</h2>
      <span class="cv-soft">This creates workflow v${version}. ${esc(block.by)} drafted the stage's code:</span>${codeLines(ui.insert.compiled)}
      ${affected.length ? `<b>Work already in flight</b>${affected.map((item) => `<span class="cv-guide">${esc(item.title)} is in ${esc(stageName(model, item.stage))} and has no ${esc(block.name.toLowerCase())} evidence</span>`).join('')}
        <div class="cv-row"><button class="cv-btn primary" ${act('insert', 'move')}>Adopt v${version} · send them to ${esc(block.name)}</button><button class="cv-btn" ${act('insert', 'pin')}>Adopt v${version} · they finish under v${model.workflow.version}</button><button class="cv-btn" ${act('insert', null)}>Cancel</button></div>`
      : `<div class="cv-row"><button class="cv-btn primary" ${act('insert', 'move')}>Adopt v${version}</button><button class="cv-btn" ${act('insert', null)}>Cancel</button></div>`}</div></div>`;
  }
  if (ui.decomp) {
    const proposal = ui.decomp.proposal ? model.proposals.find((entry) => entry.id === ui.decomp.proposal) : null;
    const epic = epicOf(model, ui.decomp.epic);
    const items = proposal ? proposal.operations.filter((operation) => operation.op === 'item.create') : [];
    const criterionText = (id) => { const found = epic ? epic.criteria.find((criterion) => criterion.id === id) : null; return found ? found.text : id; };
    return `<div class="cv-backdrop" data-overlay="1"><div class="cv-dialog" role="dialog" aria-label="Proposed child tasks">
      <span class="cv-label accent">the orchestrator proposes children for ${esc(epic ? epic.title : 'the epic')}</span>
      ${proposal ? '' : `<span class="cv-soft">${esc(ui.decomp.text || '')}</span>`}
      ${items.map((operation) => `<div class="cv-box" style="gap: 4px"><b>${esc(operation.args.title)}</b>${(operation.args.criteria || []).map((text) => `<span style="font-size: 14px; color: #d9d4c9">Criterion: ${esc(text)}</span>`).join('')}${(operation.args.covers || []).map((id) => `<span class="cv-small" style="color: var(--accent2)">Covers the epic's “${esc(criterionText(id))}”</span>`).join('')}</div>`).join('')}
      ${proposal && proposal.notes && proposal.notes.length ? proposal.notes.map((note) => `<span class="cv-small cv-guide">${esc(note)}</span>`).join('') : ''}
      <span class="cv-small cv-soft">Adopted tasks enter ${esc(model.workflow.stages[0] ? model.workflow.stages[0].name : 'the workflow')} with their criteria, so the workflow can start them straight away.</span>
      <div class="cv-row">${proposal && proposal.state === 'open' ? `<button class="cv-btn primary" ${act('adoptDecomp', proposal.id)}>Adopt these tasks</button>` : ''}<button class="cv-btn" ${act('closeDecomp')}>Close</button></div></div></div>`;
  }
  return '';
}

// ---------------------------------------------------------------- the page
export function render(model, ui) {
  const bottom = ui.showLog ? 196 : 12;
  const drawer = drawerBody(model, ui);
  const palettePanel = ui.showPalette && ui.mode === 'canvas';
  const toast = ui.toast ? `<div class="cv-toast ${ui.toast.tone === 'info' ? 'info' : ''}" role="status">${esc(ui.toast.text)}${ui.toast.source ? `<span class="src">${esc(ui.toast.source)}</span>` : ''}</div>` : '';
  const drawerHtml = drawer ? `<aside class="cv-drawer" data-overlay="1" aria-label="${esc(drawer[0])}" style="bottom: ${bottom}px">
    <div class="cv-panel-head"><b>${esc(drawer[0])}</b><button class="cv-btn sm" ${act('closeDrawer')}>Close</button></div>
    <div class="cv-drawer-body">${drawer[1]}</div></aside>` : '';
  return `<div class="cv-root">${header(model, ui)}
    <div class="cv-vp ${ui.tool === 'region' ? 'drawing' : ''}" data-viewport="1" style="background-size: ${24 * ui.zoom}px ${24 * ui.zoom}px; background-position: ${ui.pan.x}px ${ui.pan.y}px">
      ${world(model, ui)}
      ${palettePanel ? palette(model, ui, bottom) : ''}
      ${ui.mode === 'reader' ? reader(model, ui) : ''}
      ${messageBar(model, ui, palettePanel ? 304 : 12, drawer ? 424 : 12, bottom)}
      ${toast}${drawerHtml}${ui.showLog ? runtimeLog(model) : ''}${dialogs(model, ui)}
    </div></div>`;
}

export function emptyPage(spaces, ui) {
  const projects = spaces.projects || [];
  const withCanvas = new Set((spaces.spaces || []).map((space) => space.id));
  return `<div class="cv-empty"><h1>Fleet canvas</h1>
    <p>The canvas shows one project's work as a workflow board, with regions, epics and views whose rules are written in Fleet's decision language. Choose a project.</p>
    ${ui.error ? `<p class="cv-err">${esc(ui.error)}</p>` : ''}
    <ul>${projects.map((project) => `<li class="cv-row"><a href="/canvas/${encodeURIComponent(project.id)}">${esc(project.name)}</a>
      ${withCanvas.has(project.id) ? '<span class="cv-small cv-muted">has a canvas</span>' : `<button class="cv-btn sm" ${act('init', { space: project.id })}>Give it a canvas</button><button class="cv-btn sm" ${act('init', { space: project.id, example: true })}>…with the example</button>`}</li>`).join('')}</ul>
    ${projects.length ? '' : '<p>No projects yet. Register one with <code>fleet project add NAME</code>.</p>'}
    <p><a href="/">Back to the deck</a></p></div>`;
}

export function uninitialisedPage(space, ui) {
  return `<div class="cv-empty"><h1>${esc(space)} has no canvas yet</h1>
    <p>Giving it a canvas adds a starting workflow (Plan → Implement → Approve), a scheduler, an Inbox region, a charter and a reading page. Existing work items appear as cards.</p>
    ${ui.error ? `<p class="cv-err">${esc(ui.error)}</p>` : ''}
    <div class="cv-row"><button class="cv-btn primary" ${act('init', { space })}>Give it a canvas</button><button class="cv-btn" ${act('init', { space, example: true })}>Add the example work too</button></div>
    <p><a href="/canvas">All projects</a> · <a href="/">Back to the deck</a></p></div>`;
}
