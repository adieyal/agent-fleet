// The canvas: a client of the Fleet kernel. Every gesture sends one operation; the store is authoritative,
// moves are shown at once and snap back with the refusal's message and source line when the kernel refuses.
import { CG, CW, FX, bandAt, columnRects, frameRect, inside, itemPosition, docPosition, epicPosition } from './geometry.js';
import { morph } from './morph.js';
import { emptyPage, epicOf, itemOf, recipient, render, uninitialisedPage } from './render.js';
import { uid } from './util.js';
import { drafts } from './widgets.js';

const app = document.getElementById('app');
const space = decodeURIComponent((location.pathname.match(/^\/canvas\/([^/]+)/) || [])[1] || '');
const STORE_KEY = 'fleet-canvas:' + space;

const ui = {
  mode: 'canvas', pan: { x: 16, y: 8 }, zoom: 0.56, tool: 'select', focus: null, selected: null, drawer: null, doc: null,
  showLog: false, showPalette: false, editing: false, codeDraft: '', codeError: '', codeBase: null, codeTarget: null, preview: null,
  readerObj: null, pageEditing: false, pageDraft: '', naming: null, nameDraft: '', nameError: '', regionProposal: null,
  insert: null, decomp: null, toast: null, drag: null, dropTarget: null, linkFrom: null, ghost: null, drawing: null,
  cmdDraft: '', cmdError: '', convoHidden: false, sending: false, sessDraft: '', sessError: '',
  specEditing: false, specDraft: '', criteriaEditing: null, critDraft: '', chEditing: false, chDraft: '', chError: '',
  chClause: '', chClauseErr: '', clauseRule: '', agentEditing: null, agentHost: '', agentCwd: '',
  collapsedEpics: [], pending: {}, live: { ok: true }, error: null,
};
let model = null;
let spacesList = null;
let missing = false;

try {
  const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
  if (saved && saved.pan && typeof saved.zoom === 'number') Object.assign(ui, { pan: saved.pan, zoom: saved.zoom, mode: saved.mode || 'canvas', collapsedEpics: Array.isArray(saved.collapsedEpics) ? saved.collapsedEpics.filter((id) => typeof id === 'string') : [] });
} catch (error) { /* a private window keeps no per-viewer conveniences */ }

function remember() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify({ pan: ui.pan, zoom: ui.zoom, mode: ui.mode, collapsedEpics: ui.collapsedEpics })); } catch (error) { /* ignore */ }
}

// ---------------------------------------------------------------- rendering
let frame = 0;
function paint() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    if (!space) { morph(app, emptyPage(spacesList || {}, ui)); return; }
    if (missing) { morph(app, uninitialisedPage(space, ui)); return; }
    if (!model) return;
    morph(app, render(model, ui));
  });
}

function toast(text, { tone = 'refuse', source = null } = {}) {
  ui.toast = { text, tone, source, at: Date.now() };
  paint();
  const at = ui.toast.at;
  setTimeout(() => { if (ui.toast && ui.toast.at === at) { ui.toast = null; paint(); } }, tone === 'info' ? 4000 : 7000);
}

function refusalText(body) {
  const source = body.source && body.source.object ? `Refused by ${body.source.object} v${body.source.version}${body.source.line ? `, line ${body.source.line}` : ''}` : 'Refused';
  return { text: `${source}: ${body.message}`, source: body.code };
}

// ---------------------------------------------------------------- talking to Fleet
async function load() {
  if (!space) {
    const response = await fetch('/api/canvas/spaces');
    spacesList = await response.json();
    paint();
    return;
  }
  const response = await fetch('/api/canvas?space=' + encodeURIComponent(space));
  if (response.status === 404) { missing = true; model = null; paint(); return; }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    app.innerHTML = `<div class="cv-empty"><h1>The canvas could not load</h1><p class="cv-err"></p><p><a href="/canvas">All projects</a></p></div>`;
    app.querySelector('.cv-err').textContent = body.error || response.statusText;
    return;
  }
  missing = false;
  model = await response.json();
  model.layout = model.layout || {};
  if (ui.selected && !selectionExists()) { ui.selected = null; if (ui.drawer === 'select') ui.drawer = null; }
  document.title = `${model.name} · Fleet canvas`;
  paint();
}

function selectionExists() {
  const sel = ui.selected;
  if (sel.kind === 'task' || sel.kind === 'session') return !!itemOf(model, sel.id);
  if (sel.kind === 'epic') return !!epicOf(model, sel.id);
  if (sel.kind === 'zone') return model.regions.some((region) => region.id === sel.id);
  if (sel.kind === 'widget') return model.views.some((view) => view.id === sel.id);
  if (sel.kind === 'stage') return model.workflow.stages.some((stage) => stage.id === sel.id);
  return true;
}

let reloading = null, again = false;
function reload() {
  if (reloading) { again = true; return reloading; }
  reloading = load().catch(() => { ui.live.ok = false; paint(); }).finally(() => {
    reloading = null;
    if (again) { again = false; reload(); }
  });
  return reloading;
}

async function post(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({ error: response.statusText }));
  return { status: response.status, data };
}

// One operation. Returns the result, or null when it was refused (the refusal is shown).
async function op(name, args = {}, { quiet = false } = {}) {
  let response;
  try {
    response = await post('/api/canvas/op', { space, op: name, args, op_id: uid() });
  } catch (error) {
    toast('Fleet could not be reached; nothing was changed.');
    return null;
  }
  const { status, data } = response;
  if (status === 409 && data.refused) {
    const refusal = refusalText(data);
    if (!quiet) toast(refusal.text, { source: refusal.source });
    await reload();
    return { refused: data };
  }
  if (status !== 200) {
    if (!quiet) toast(data.error || 'That did not work.');
    return null;
  }
  for (const text of data.toasts || []) toast(text, { tone: 'info' });
  await reload();
  return data.result || {};
}

async function layout(object, props) {
  try { await post('/api/canvas/layout', { space, object, props }); } catch (error) { /* personal arrangement only */ }
  reload();
}

function subscribe() {
  if (!space) return;
  let source;
  const open = () => {
    source = new EventSource('/api/canvas/stream?space=' + encodeURIComponent(space));
    source.addEventListener('change', () => { ui.live.ok = true; reload(); });
    source.addEventListener('ping', () => { if (!ui.live.ok) { ui.live.ok = true; paint(); } });
    source.onerror = () => { ui.live.ok = false; paint(); };
  };
  open();
  setInterval(() => { if (!ui.drag) reload(); }, 20000);
}

// ---------------------------------------------------------------- geometry helpers
function viewport() { return app.querySelector('[data-viewport]'); }

function toWorld(event) {
  const rect = viewport().getBoundingClientRect();
  return { x: (event.clientX - rect.left - ui.pan.x) / ui.zoom, y: (event.clientY - rect.top - ui.pan.y) / ui.zoom };
}

function viewCentre(fx = 0.45, fy = 0.35) {
  const rect = viewport().getBoundingClientRect();
  return { x: (rect.width * fx - ui.pan.x) / ui.zoom, y: (rect.height * fy - ui.pan.y) / ui.zoom };
}

function centreOn(point, zoom = 0.8) {
  const rect = viewport() ? viewport().getBoundingClientRect() : { width: 1200, height: 700 };
  ui.zoom = zoom;
  ui.pan = { x: rect.width * 0.35 - point.x * zoom, y: rect.height * 0.3 - point.y * zoom };
  remember();
}

function epicAt(point) {
  // Hit-test the epic boxes as drawn: their height follows their content.
  const rect = viewport().getBoundingClientRect();
  const client = { x: rect.left + ui.pan.x + point.x * ui.zoom, y: rect.top + ui.pan.y + point.y * ui.zoom };
  return model.epics.find((epic) => {
    const element = app.querySelector(`[data-key="e-${CSS.escape(epic.id)}"]`);
    if (!element) return false;
    const box = element.getBoundingClientRect();
    return client.x >= box.left && client.x <= box.right && client.y >= box.top && client.y <= box.bottom;
  });
}

function regionAt(point) {
  return model.regions.slice().reverse().find((region) => inside(point, region.rect));
}

function objectPosition(kind, id) {
  if (kind === 'task') return itemPosition(model, model.layout, itemOf(model, id), null);
  if (kind === 'zone') { const region = model.regions.find((entry) => entry.id === id); return { x: region.rect.x, y: region.rect.y }; }
  if (kind === 'epic') return epicPosition(model, model.layout, epicOf(model, id), model.epics.findIndex((epic) => epic.id === id), null);
  if (kind === 'widget') { const view = model.views.find((entry) => entry.id === id); return { x: view.x, y: view.y }; }
  if (kind === 'doc') return docPosition(model, model.layout, id, null);
  if (kind === 'block') { const block = model.blocks.find((entry) => entry.id === id); return { x: block.x, y: block.y }; }
  return { x: 0, y: 0 };
}

// ---------------------------------------------------------------- pointer interaction
let press = null;
let paletteDropped = false;
const MOVABLE = ['task', 'zone', 'epic', 'widget', 'doc', 'block'];

app.addEventListener('pointerdown', (event) => {
  if (!model || ui.mode !== 'canvas' && !event.target.closest('[data-palette]')) return;
  const paletteItem = event.target.closest('[data-palette]');
  if (paletteItem) {
    press = { mode: 'palette', type: paletteItem.getAttribute('data-palette'), sx: event.clientX, sy: event.clientY, moved: false, id: event.pointerId };
    return;
  }
  if (event.target.closest('[data-overlay]') || event.target.closest('button, input, textarea, select, a, label')) return;
  if (!event.target.closest('[data-viewport]')) return;
  if (event.button !== undefined && event.button !== 0) return;
  const handle = event.target.closest('[data-drag]');
  const point = toWorld(event);
  if (ui.tool === 'region' && !handle) {
    press = { mode: 'draw', id: event.pointerId };
    ui.drawing = { x0: point.x, y0: point.y, x1: point.x, y1: point.y };
    viewport().setPointerCapture(event.pointerId);
    paint();
    return;
  }
  if (handle) {
    const [kind, ...rest] = handle.getAttribute('data-drag').split(':');
    const id = rest.join(':');
    const fixed = !MOVABLE.includes(kind);
    const origin = fixed ? null : objectPosition(kind, id);
    press = { mode: 'press', kind, id, fixed, sx: event.clientX, sy: event.clientY, w0: point, origin, moved: false, pointer: event.pointerId };
    try { viewport().setPointerCapture(event.pointerId); } catch (error) { /* capture is a convenience */ }
    return;
  }
  press = { mode: 'pan', sx: event.clientX, sy: event.clientY, px: ui.pan.x, py: ui.pan.y, moved: false, pointer: event.pointerId };
});

window.addEventListener('pointermove', (event) => {
  if (!press) return;
  const distance = Math.abs(event.clientX - press.sx) + Math.abs(event.clientY - press.sy);
  if (press.mode === 'draw') {
    const point = toWorld(event);
    ui.drawing = { ...ui.drawing, x1: point.x, y1: point.y };
    paint();
    return;
  }
  if (press.mode === 'palette') {
    if (distance > 5) press.moved = true;
    if (press.moved && viewport()) {
      const point = toWorld(event);
      ui.ghost = { type: press.type, x: point.x - 40, y: point.y - 20 };
      paint();
    }
    return;
  }
  if (!press.moved && distance < 5) return;
  press.moved = true;
  if (press.mode === 'pan') {
    ui.pan = { x: press.px + event.clientX - press.sx, y: press.py + event.clientY - press.sy };
    paint();
    return;
  }
  if (press.fixed) return;
  const point = toWorld(event);
  ui.drag = { kind: press.kind, id: press.id, moved: true, x: press.origin.x + point.x - press.w0.x, y: press.origin.y + point.y - press.w0.y };
  ui.dropTarget = dropTargetAt(press.kind, point);
  paint();
});

window.addEventListener('pointerup', (event) => {
  const current = press;
  press = null;
  if (!current || !model) return;
  if (current.mode === 'draw') {
    const d = ui.drawing;
    ui.drawing = null;
    const rect = { x: Math.min(d.x0, d.x1), y: Math.min(d.y0, d.y1), w: Math.abs(d.x1 - d.x0), h: Math.abs(d.y1 - d.y0) };
    if (rect.w < 80 || rect.h < 60) { toast('Drag out a larger region to create one.', { tone: 'info' }); return; }
    Object.assign(ui, { tool: 'select', naming: { rect }, nameDraft: '', nameError: '' });
    paint();
    setTimeout(() => { const input = document.getElementById('zone-name'); if (input) input.focus(); });
    return;
  }
  if (current.mode === 'palette') {
    ui.ghost = null;
    if (!current.moved) return; // a click places it in view through its action
    paletteDropped = true;
    setTimeout(() => { paletteDropped = false; }, 0);
    const over = event.target.closest && event.target.closest('.cv-palette');
    if (over || !viewport()) { paint(); return; }
    const point = toWorld(event);
    if (current.type.startsWith('preset:')) placePreset(current.type.slice(7), point.x - 40, point.y - 20);
    else placeWidget(current.type, point.x - 40, point.y - 20);
    return;
  }
  if (current.mode === 'pan') {
    remember();
    if (!current.moved) {
      ui.selected = null; ui.editing = false; ui.linkFrom = null;
      if (ui.drawer === 'select') ui.drawer = null;
      paint();
    }
    return;
  }
  if (!current.moved) { click(current); return; }
  if (current.fixed) return;
  drop(current, toWorld(event));
});

window.addEventListener('pointercancel', () => {
  if (!press) return;
  press = null;
  Object.assign(ui, { drag: null, dropTarget: null, ghost: null, drawing: null });
  paint();
});

function dropTargetAt(kind, point) {
  if (kind === 'task') {
    if (epicAt(point)) return 'epic:' + epicAt(point).id;
    const col = columnRects(model).find((rect) => inside(point, rect));
    if (col) return 'col:' + col.id;
    const region = regionAt(point);
    if (region) return 'zone:' + region.id;
  }
  if (kind === 'doc' || kind === 'widget') {
    const region = regionAt(point);
    if (region && region.accepts_documents) return 'zone:' + region.id;
  }
  return null;
}

function click(current) {
  const { kind, id } = current;
  if (ui.tool === 'link' && kind === 'task') {
    if (!ui.linkFrom) { ui.linkFrom = id; toast('Now click the task that has to wait for it.', { tone: 'info' }); return; }
    if (ui.linkFrom === id) { ui.linkFrom = null; paint(); return; }
    const first = ui.linkFrom;
    ui.linkFrom = null; ui.tool = 'select';
    op('dep.add', { from: first, to: id });
    return;
  }
  if (kind === 'doc') { Object.assign(ui, { drawer: 'docs', doc: id, selected: null }); paint(); return; }
  if (kind === 'block') { toast('Drag the new stage into the workflow where it belongs.', { tone: 'info' }); return; }
  select({ kind: kind === 'col' ? 'stage' : kind, id });
}

function select(target) {
  Object.assign(ui, { selected: target, drawer: 'select', editing: false, preview: null, codeError: '', criteriaEditing: null, agentEditing: null });
  paint();
}

async function settle(kind, id, work) {
  ui.pending[id] = true;
  ui.drag = { ...ui.drag, settled: true };
  paint();
  try { await work(); } finally {
    delete ui.pending[id];
    if (ui.drag && ui.drag.id === id) ui.drag = null;
    ui.dropTarget = null;
    await reload();
  }
}

function drop(current, point) {
  const { kind, id } = current;
  const at = { x: Math.round(ui.drag.x), y: Math.round(ui.drag.y) };
  ui.dropTarget = null;
  if (kind === 'task') {
    const item = itemOf(model, id);
    settle(kind, id, async () => {
      const epic = epicAt(point);
      if (epic) { if (item.epic !== epic.id) await op('item.reparent', { item: id, epic: epic.id }); return; }
      const col = columnRects(model).find((rect) => inside(point, rect));
      const region = regionAt(point);
      if (col) {
        const band = bandAt(model, point.y);
        if (band && band !== (item.band || 'later') && item.status !== 'done') {
          const result = await op('item.set_band', { item: id, band });
          if (result && result.refused) return;
        }
        if (col.id !== item.stage || item.region) await op('item.move', { item: id, stage: col.id });
        return;
      }
      if (region) {
        if (item.region !== region.id) await op('region.enter', { region: region.id, item: id });
        return;
      }
      if (item.region) await op('region.exit', { region: item.region, item: id });
      if (!item.stage) await layout('task:' + id, at);
      else if (!item.region) toast(`${item.title} returned to its column: open canvas carries no meaning.`, { tone: 'info' });
    });
    return;
  }
  if (kind === 'zone') {
    const region = model.regions.find((entry) => entry.id === id);
    settle(kind, id, () => op('region.configure', { region: id, rect: { ...at, w: region.rect.w, h: region.rect.h } }));
    return;
  }
  if (kind === 'epic') { settle(kind, id, () => layout('epic:' + id, at)); return; }
  if (kind === 'doc') {
    const titles = { spec: `${model.name} spec`, star: 'North star', report: 'Status report' };
    settle(kind, id, async () => {
      await layout('doc:' + id, at);
      await syncContext({ kind: 'doc', id, title: titles[id] }, point);
    });
    return;
  }
  if (kind === 'widget') {
    const view = model.views.find((entry) => entry.id === id);
    settle(kind, id, async () => {
      await op('view.configure', { view: id, at }, { quiet: true });
      if (view && (view.type === 'note' || view.type === 'doc')) {
        const title = view.type === 'note' ? `Note: “${(view.text || '(empty)').slice(0, 60)}”` : view.title;
        await syncContext({ kind: 'view', id, title }, point);
      }
    });
    return;
  }
  if (kind === 'block') {
    if (inside(point, frameRect(model))) {
      const index = Math.max(0, Math.min(model.workflow.stages.length, Math.round((point.x - FX - 110 - CW / 2 + (CW + CG) / 2) / (CW + CG))));
      const block = model.blocks.find((entry) => entry.id === id);
      ui.drag = null;
      ui.insert = { block: id, index, compiled: null };
      post('/api/canvas/compile', { text: block.code }).then(({ data }) => { if (ui.insert) { ui.insert.compiled = data; paint(); } });
      paint();
      return;
    }
    ui.drag = null;
    paint();
  }
}

async function syncContext(doc, point) {
  const region = regionAt(point);
  const inContext = model.context.some((entry) => entry.kind === doc.kind && entry.doc === doc.id);
  if (region && region.accepts_documents) await op('context.add', { doc, region: region.id });
  else if (inContext) await op('context.remove', { doc });
}

app.addEventListener('wheel', (event) => {
  if (!model || ui.mode !== 'canvas' || event.target.closest('[data-overlay]') || !event.target.closest('[data-viewport]')) return;
  event.preventDefault();
  const rect = viewport().getBoundingClientRect();
  const mx = event.clientX - rect.left, my = event.clientY - rect.top;
  const zoom = Math.min(1.6, Math.max(0.3, ui.zoom * Math.exp(-event.deltaY * 0.0015)));
  const wx = (mx - ui.pan.x) / ui.zoom, wy = (my - ui.pan.y) / ui.zoom;
  ui.zoom = zoom;
  ui.pan = { x: mx - wx * zoom, y: my - wy * zoom };
  remember();
  paint();
}, { passive: false });

// ---------------------------------------------------------------- placing things
async function placeWidget(type, x, y) {
  const at = x === undefined ? viewCentre() : { x, y };
  const result = await op('view.place', { type, x: Math.round(at.x), y: Math.round(at.y) });
  if (result && result.view) select({ kind: 'widget', id: result.view });
}

async function placePreset(kind, x, y) {
  const at = x === undefined ? viewCentre(0.3, 0.3) : { x, y };
  const sizes = (model.presets || {})[kind] || [];
  let left = Math.round(at.x), last = null;
  for (const entry of sizes) {
    const result = await op('region.create', { name: entry.name, rect: { x: left, y: Math.round(at.y), w: entry.w, h: entry.h } });
    if (!result || result.refused) return;
    last = result.region;
    left += entry.w + 40;
  }
  if (last) select({ kind: 'zone', id: last });
}

// ---------------------------------------------------------------- actions
function codeFor(sel) {
  if (sel.kind === 'stage') { const stage = model.workflow.stages.find((entry) => entry.id === sel.id); return stage && { kind: 'stage', id: stage.id, code: stage.code, base: stage.version, level: 'enforced' }; }
  if (sel.kind === 'zone') { const region = model.regions.find((entry) => entry.id === sel.id); return region && { kind: 'zone', id: region.id, code: region.code, base: region.version, level: region.level }; }
  if (sel.kind === 'sched') return { kind: 'schedule', id: 'main', code: model.schedule.code, base: model.schedule.version, level: 'enforced' };
  if (sel.kind === 'epic') return { kind: 'epicflow', id: 'main', code: model.epicflow.code, base: model.epicflow.version, level: 'enforced' };
  if (sel.kind === 'widget') { const view = model.views.find((entry) => entry.id === sel.id); return view && { kind: 'view', id: view.id, code: view.code, base: view.version, level: 'enforced' }; }
  return null;
}

let previewTimer = 0;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    if (!ui.editing) return;
    const level = ui.codeTarget ? ui.codeTarget.level : 'enforced';
    const { data } = await post('/api/canvas/compile', { text: ui.codeDraft, level });
    if (ui.editing) { ui.preview = data; paint(); }
  }, 350);
}

let noteTimers = {};
function saveNote(id, text) {
  clearTimeout(noteTimers[id]);
  noteTimers[id] = setTimeout(() => op('view.configure', { view: id, text }, { quiet: true }), 600);
}

const actions = {
  tool(name) { ui.tool = ui.tool === name && name !== 'select' ? 'select' : name; ui.linkFrom = null; paint(); },
  async draftStage() {
    const result = await op('stage.draft', {});
    if (result && result.block) toast('The orchestrator drafted a new stage. Drag it into the workflow where it belongs.', { tone: 'info' });
  },
  togglePalette() { ui.showPalette = !ui.showPalette; paint(); },
  drawer(name) { ui.drawer = ui.drawer === name ? null : name; if (name === 'docs') ui.doc = null; paint(); },
  toggleLog() { ui.showLog = !ui.showLog; paint(); },
  zoom(factor) {
    const rect = viewport().getBoundingClientRect();
    const mx = rect.width / 2, my = rect.height / 2;
    const zoom = Math.min(1.6, Math.max(0.3, ui.zoom * factor));
    ui.pan = { x: mx - (mx - ui.pan.x) / ui.zoom * zoom, y: my - (my - ui.pan.y) / ui.zoom * zoom };
    ui.zoom = zoom; remember(); paint();
  },
  fit() { ui.zoom = 0.56; ui.pan = { x: 16, y: 8 }; remember(); paint(); },
  mode(mode) { ui.mode = mode; if (mode === 'reader' && ui.drawer === 'select') ui.drawer = null; remember(); paint(); },
  exitFocus() { ui.focus = null; paint(); },
  toggleEpicTasks(id) {
    const collapsed = ui.collapsedEpics.includes(id);
    ui.collapsedEpics = collapsed ? ui.collapsedEpics.filter((value) => value !== id) : [...ui.collapsedEpics, id];
    if (!collapsed) {
      const selected = ui.selected && itemOf(model, ui.selected.id);
      if (selected && selected.epic === id) { ui.selected = { kind: 'epic', id }; }
      const linking = ui.linkFrom && itemOf(model, ui.linkFrom);
      if (linking && linking.epic === id) ui.linkFrom = null;
    }
    remember(); paint();
  },
  enterEpic(id) { ui.collapsedEpics = ui.collapsedEpics.filter((value) => value !== id); remember(); ui.focus = id; ui.selected = null; if (ui.drawer === 'select') ui.drawer = null; paint(); },
  select(target) { if (ui.mode === 'reader') ui.mode = 'canvas'; select(target); },
  session({ id }) { select({ kind: 'session', id }); },
  async resolve({ id, choice, canvas, answer, kind, item }) {
    if (kind === 'Unblock' && choice === 'back') { select({ kind: 'session', id: item }); return; }
    await op('attention.resolve', canvas ? { id, choice } : { id, answer });
  },
  op({ op: name, args }) { return op(name, args); },
  async answerBlocked({ id }) {
    const answer = (drafts[id] || '').trim();
    if (!answer) { toast('Write a reply first.', { tone: 'info' }); return; }
    const result = await op('attention.answer', { id, answer });
    if (result && !result.refused) { delete drafts[id]; paint(); }
  },
  async answerDecision({ id }) {
    const answer = (drafts[id] || '').trim();
    if (!answer) { toast('Write an answer first.', { tone: 'info' }); return; }
    const result = await op('attention.resolve', { id, answer });
    if (result && !result.refused) { delete drafts[id]; paint(); }
  },
  reader(object) {
    Object.assign(ui, { mode: 'reader', readerObj: object, pageEditing: false });
    if (ui.drawer === 'select') ui.drawer = null;
    paint();
  },
  doc({ id }) { ui.drawer = 'docs'; ui.doc = id; paint(); },
  locateDoc(id) { centreOn(docPosition(model, model.layout, id, null), 0.85); paint(); },
  locate(object) {
    const point = object.kind === 'task' ? itemPosition(model, model.layout, itemOf(model, object.id), null)
      : objectPosition('epic', object.id);
    ui.mode = 'canvas';
    centreOn(point);
    select({ kind: object.kind, id: object.id });
  },
  closeDrawer() { Object.assign(ui, { drawer: null, selected: null, editing: false }); paint(); },
  editCode() {
    const target = codeFor(ui.selected || {});
    if (!target) return;
    Object.assign(ui, { editing: true, codeDraft: target.code, codeError: '', codeBase: target.base, codeTarget: target, preview: null });
    paint();
    schedulePreview();
  },
  cancelCode() { Object.assign(ui, { editing: false, codeError: '', preview: null }); paint(); },
  async compile() {
    const target = ui.codeTarget;
    if (!target) return;
    const result = await op('code.compile', { object: { kind: target.kind, id: target.id }, text: ui.codeDraft, base: target.base }, { quiet: true });
    if (result && result.refused) {
      ui.codeError = result.refused.code === 'version_conflict'
        ? `${result.refused.message} Your draft is kept; reopen the editor to start from the newer version.`
        : result.refused.message;
      paint();
      return;
    }
    if (result) { Object.assign(ui, { editing: false, codeError: '', preview: null }); toast(`Compiled as v${result.version}.`, { tone: 'info' }); }
  },
  editCriteria(id) {
    const item = itemOf(model, id);
    const epic = epicOf(model, id);
    const criteria = item ? item.criteria.map((criterion) => criterion.text) : epic ? epic.criteria.map((criterion) => criterion.text) : [];
    Object.assign(ui, { criteriaEditing: id, critDraft: criteria.join('\n') });
    paint();
  },
  cancelCriteria() { ui.criteriaEditing = null; paint(); },
  async saveCriteria(id) {
    const list = ui.critDraft.split('\n').map((line) => line.replace(/^[-*•]\s*/, '').trim()).filter(Boolean);
    const result = await op('criteria.set', { item: id, list });
    if (result && !result.refused) { ui.criteriaEditing = null; paint(); }
  },
  cover({ item, criterion }) {
    const card = itemOf(model, item);
    const covers = card.covers.includes(criterion) ? card.covers.filter((value) => value !== criterion) : card.covers.concat([criterion]);
    return op('item.cover', { item, criteria: covers });
  },
  async sendSession(id) {
    const text = (ui.sessDraft || '').trim();
    if (!text) { ui.sessError = 'Write a message first.'; paint(); return; }
    ui.sessDraft = ''; ui.sessError = '';
    await op('message.send', { text, target: { kind: 'task', id } });
  },
  editAgent(agent) {
    const setting = agent ? model.settings.agents[agent] || {} : {};
    Object.assign(ui, { agentEditing: agent, agentHost: setting.host || '', agentCwd: setting.cwd || '',
      agentRuntime: setting.runtime || (agent === 'codex' ? 'codex' : 'claude') });
    paint();
  },
  async saveAgent(agent) {
    const result = await op('agent.configure', { agent, mode: 'dispatch', host: ui.agentHost, cwd: ui.agentCwd, runtime: ui.agentRuntime });
    if (result && !result.refused) { ui.agentEditing = null; paint(); }
  },
  async simulateAgent(agent) {
    const result = await op('agent.configure', { agent, mode: 'simulate', seconds: 30 });
    if (result && !result.refused) { ui.agentEditing = null; paint(); }
  },
  async decompose(epic) {
    const result = await op('epic.decompose', { epic });
    if (result && !result.refused) { ui.decomp = { epic, proposal: result.proposal, text: result.text }; paint(); }
  },
  async adoptDecomp(id) {
    const result = await op('proposal.resolve', { id, adopt: true });
    if (result && !result.refused) { ui.decomp = null; paint(); }
  },
  closeDecomp() { ui.decomp = null; paint(); },
  proposal({ id, adopt }) { return op('proposal.resolve', { id, adopt }); },
  hideConvo() { ui.convoHidden = true; paint(); },
  toOrchestrator() { ui.selected = null; if (ui.drawer === 'select') ui.drawer = null; paint(); },
  togglePage() { Object.assign(ui, { pageEditing: !ui.pageEditing, pageDraft: model.page.markdown }); paint(); },
  async savePage() {
    const result = await op('page.update', { markdown: ui.pageDraft, base: model.page.version });
    if (result && !result.refused) { ui.pageEditing = false; paint(); }
  },
  editSpec() { Object.assign(ui, { specEditing: true, specDraft: model.spec.text || '' }); paint(); },
  cancelSpec() { ui.specEditing = false; paint(); },
  async saveSpec() {
    if (!(ui.specDraft || '').trim()) { ui.specEditing = false; paint(); return; }
    const result = await op('spec.update', { text: ui.specDraft });
    if (result && !result.refused) { ui.specEditing = false; paint(); }
  },
  editNorthStar() { Object.assign(ui, { chEditing: true, chDraft: model.charter.north_star || '', chError: '' }); paint(); },
  cancelNorthStar() { ui.chEditing = false; paint(); },
  async saveNorthStar() {
    const text = (ui.chDraft || '').trim();
    if (!text) { ui.chError = 'Write the outcome this space exists to produce.'; paint(); return; }
    const result = await op('charter.update', { patch: { north_star: text }, base: model.charter.version });
    if (result && !result.refused) { ui.chEditing = false; paint(); }
  },
  async addClause() {
    const text = (ui.chClause || '').trim();
    if (!text) { ui.chClauseErr = 'Write the clause first.'; paint(); return; }
    const clause = ui.clauseRule ? { text, kind: 'enforced', rule: ui.clauseRule } : { text, kind: 'guidance' };
    const result = await op('charter.update', { patch: { add_clause: clause }, base: model.charter.version });
    if (result && !result.refused) { Object.assign(ui, { chClause: '', chClauseErr: '', clauseRule: '' }); paint(); }
  },
  placeWidget(type) { if (!paletteDropped) placeWidget(type); },
  placePreset(kind) { if (!paletteDropped) placePreset(kind); },
  cancelName() { ui.naming = null; paint(); },
  async adoptRegion(level) {
    const proposal = ui.regionProposal;
    const result = await op('proposal.resolve', { id: proposal.id, adopt: true, level });
    if (result && !result.refused) {
      ui.regionProposal = null;
      const created = (result.results || []).map((entry) => entry.region).find(Boolean);
      if (created) select({ kind: 'zone', id: created }); else paint();
    }
  },
  async discardRegion() {
    const proposal = ui.regionProposal;
    ui.regionProposal = null;
    paint();
    await op('proposal.resolve', { id: proposal.id, adopt: false }, { quiet: true });
  },
  async insert(migration) {
    const insert = ui.insert;
    ui.insert = null;
    paint();
    if (!migration) return;
    await op('workflow.insert', { block: insert.block, index: insert.index, migration });
  },
  async init({ space: target, example }) {
    const { status, data } = await post('/api/canvas/init', { space: target, example: !!example });
    if (status !== 200) { ui.error = data.error || data.message || 'That did not work.'; paint(); return; }
    location.href = '/canvas/' + encodeURIComponent(target);
  },
};

const submits = {
  async sendCmd() {
    const text = (ui.cmdDraft || '').trim();
    if (!text) { ui.cmdError = 'Write a message first.'; paint(); return; }
    const target = recipient(model, ui);
    Object.assign(ui, { cmdDraft: '', cmdError: '', convoHidden: false, sending: true });
    const input = document.getElementById('cmd');
    if (input) input.value = '';
    paint();
    await op('message.send', { text, target: target ? target.target : null });
    ui.sending = false;
    paint();
  },
  async submitName() {
    const name = (ui.nameDraft || '').trim();
    if (!name) { ui.nameError = 'Give the region a name, such as Parked.'; paint(); return; }
    const rect = ui.naming.rect;
    const result = await op('region.propose', { name, rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) } }, { quiet: true });
    if (result && result.refused) { ui.nameError = result.refused.message; paint(); return; }
    if (!result) return;
    ui.naming = null;
    ui.regionProposal = { id: result.proposal, name, compiled: result.compiled };
    paint();
  },
};

app.addEventListener('click', (event) => {
  const target = event.target.closest('[data-act]');
  if (!target || !app.contains(target)) return;
  if (target.tagName === 'INPUT' && target.type === 'checkbox') event.preventDefault();
  const name = target.getAttribute('data-act');
  const raw = target.getAttribute('data-a');
  const argument = raw === null ? undefined : JSON.parse(raw);
  if (actions[name]) actions[name](argument, target, event);
});

app.addEventListener('submit', (event) => {
  const form = event.target.closest('[data-submit]');
  if (!form) return;
  event.preventDefault();
  const name = form.getAttribute('data-submit');
  if (submits[name]) submits[name]();
});

app.addEventListener('input', (event) => {
  const target = event.target;
  const key = target.getAttribute && target.getAttribute('data-bind');
  if (key) {
    ui[key] = target.value;
    if (key === 'cmdDraft' && ui.cmdError) { ui.cmdError = ''; paint(); }
    if (key === 'nameDraft' && ui.nameError) { ui.nameError = ''; paint(); }
    if (target.hasAttribute('data-preview')) schedulePreview();
  }
  const draft = target.getAttribute && target.getAttribute('data-draft');
  if (draft) drafts[draft] = target.value;
  const note = target.getAttribute && target.getAttribute('data-note');
  if (note) saveNote(note, target.value);
});
app.addEventListener('change', (event) => {
  const key = event.target.getAttribute && event.target.getAttribute('data-bind');
  if (key) ui[key] = event.target.value;
});

document.addEventListener('keydown', (event) => {
  const handle = event.target.closest && event.target.closest('[data-drag]');
  if (handle && (event.key === 'Enter' || event.key === ' ') && event.target === handle && model) {
    event.preventDefault();
    const [kind, ...rest] = handle.getAttribute('data-drag').split(':');
    click({ kind, id: rest.join(':') });
    return;
  }
  const pressable = event.target.closest && event.target.closest('[data-act][role=button]');
  if (pressable && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); pressable.click(); return; }
  if (event.key !== 'Escape') return;
  if (ui.naming || ui.regionProposal || ui.insert || ui.decomp) {
    if (ui.regionProposal) actions.discardRegion(); else Object.assign(ui, { naming: null, insert: null, decomp: null });
  } else if (ui.editing) ui.editing = false;
  else if (ui.tool !== 'select' || ui.linkFrom) Object.assign(ui, { tool: 'select', linkFrom: null });
  else if (ui.drawer) Object.assign(ui, { drawer: null, selected: null });
  paint();
});

// ---------------------------------------------------------------- start
reload();
subscribe();
