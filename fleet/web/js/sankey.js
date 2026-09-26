// A pipeline's run as a Sankey: columns from the run, bands from edge counts, dots travelling as items flow.

import { REDUCED } from './env.js';
import { age, esc } from './util.js';
import { hostLook } from './looks.js';

// ------------------------------------------------------------------ layout (shared with the wall screen's thumbnail)
const MIN_NODE = 4, MIN_BAND = 1.5;
export const fmt = n => Math.round(n).toLocaleString('en-US');

// Nodes keep their true counts; a small one is drawn no thinner than MIN_NODE (bands MIN_BAND) so it stays visible.
// The scale leaves room for the baseline's counts too, so its outlines fit.
export function layout(columns, counts, edges, W, H, { nodeW = 12, gap = 14, baseline = {} } = {}) {
  const value = n => counts[n] || 0;
  let k = Infinity;
  for (const col of columns) {
    const sum = Math.max(col.reduce((s, n) => s + value(n), 0), col.reduce((s, n) => s + (baseline[n] || 0), 0));
    if (sum > 0) k = Math.min(k, (H - gap * (col.length - 1) - MIN_NODE * col.length) / sum);
  }
  if (!Number.isFinite(k) || k < 0) k = 0;
  const nodes = new Map();
  columns.forEach((col, ci) => col.forEach(name => nodes.set(name, { name, col: ci, value: value(name), out: [], in: [],
    x: columns.length > 1 ? ci * (W - nodeW) / (columns.length - 1) : 0 })));
  const bands = [];
  for (const [s, t, count] of edges) {
    const a = nodes.get(s), b = nodes.get(t);
    if (!a || !b) continue;
    const band = { key: s + '→' + t, s: a, t: b, count, w: Math.max(MIN_BAND, count * k) };
    bands.push(band); a.out.push(band); b.in.push(band);
  }
  columns.forEach(col => {
    const list = col.map(n => nodes.get(n));
    for (const n of list) n.h = Math.max(MIN_NODE, n.value * k, ...[n.out, n.in].map(bs => bs.reduce((s, b) => s + b.w, 0)));
    const total = list.reduce((s, n) => s + n.h, 0);
    const space = list.length > 1 ? Math.max(gap, (H - total) / (list.length - 1)) : 0;
    let y = list.length > 1 ? 0 : (H - total) / 2;
    for (const n of list) { n.y = y; y += n.h + space; }
  });
  for (const n of nodes.values()) {   // bands leave in their targets' order and arrive in their sources', so few cross
    n.out.sort((p, q) => p.t.y - q.t.y); n.in.sort((p, q) => p.s.y - q.s.y);
    let y = n.y;
    for (const b of n.out) { b.y0 = y + b.w / 2; y += b.w; }
    y = n.y;
    for (const b of n.in) { b.y1 = y + b.w / 2; y += b.w; }
  }
  for (const b of bands) { b.x0 = b.s.x + nodeW; b.x1 = b.t.x; }
  return { nodes, bands, k, nodeW };
}
export function bandPath(b, w = b.w) {
  const xm = (b.x0 + b.x1) / 2, h = w / 2;
  return `M${b.x0},${b.y0 - h}C${xm},${b.y0 - h} ${xm},${b.y1 - h} ${b.x1},${b.y1 - h}L${b.x1},${b.y1 + h}C${xm},${b.y1 + h} ${xm},${b.y0 + h} ${b.x0},${b.y0 + h}Z`;
}
function bandPoint(b, u, dy) {   // a point along the band's centre line, dy off it
  const v = 1 - u, xm = (b.x0 + b.x1) / 2;
  return { x: v * v * v * b.x0 + 3 * v * v * u * xm + 3 * v * u * u * xm + u * u * u * b.x1,
    y: v * v * v * b.y0 + 3 * v * v * u * b.y0 + 3 * v * u * u * b.y1 + u * u * u * b.y1 + dy };
}
// Nodes items end in: nothing has left them in this run nor in the baseline, and the run has got past their column
// (it finished, they are in the last column, or a neighbour in their column already passes items on). A run part-way
// has not reached its gates, so its middle columns are not ends yet.
export function terminals(run, base) {
  const left = new Set([...run.edges, ...(base?.edges || [])].map(([s]) => s));
  const last = run.nodes.length - 1;
  return new Set(run.nodes.flatMap((col, ci) => col.filter(n => !left.has(n)
    && (run.status !== 'running' || ci === last || col.some(m => left.has(m))))));
}
export const shareBase = run => run.total || run.nodes[0]?.reduce((s, n) => s + (run.counts[n] || 0), 0) || 0;

// What the file says about the run, and whether its host can still be asked.
const QUIET_S = 60;
export function runStatus(p) {
  if (!p.run) return p.host_ok ? { kind: 'none', text: 'no runs yet' } : { kind: 'offline', text: `${p.host} offline` };
  const r = p.run, quiet = Date.now() / 1000 - (r.updated_at || 0);
  if (r.status !== 'running') return { kind: r.status === 'done' ? 'done' : 'failed', text: r.status };
  if (quiet > QUIET_S) return { kind: 'quiet', text: `running · no writes for ${age(r.updated_at)}` };
  return { kind: 'running', text: 'running' };
}

// ------------------------------------------------------------------ the sheet
export const sankeyPane = document.getElementById('sankey');
const sheet = sankeyPane.querySelector('.rd-sheet');
const chart = document.getElementById('skChart'), svg = document.getElementById('skSvg'), dotsCanvas = document.getElementById('skDots');
const side = document.getElementById('skSide');
const TWEEN_MS = 400, DOT_MS = 1400, DOTS_PER_UPDATE = 80, PAD = 18, LABEL_GAP = 28;   // nodes apart by a two-line label
const LABEL_W = 250, MIN_WIDTH = 760, NARROW = 700, LABEL_W_NARROW = 130, MIN_WIDTH_NARROW = 620;
const sk = { key: null, p: null, from: null, to: null, t0: 0, dots: [], carry: {}, raf: 0, selected: null, lastFocus: null, geo: null };

const keyOf = p => `${p.host}:${p.pipeline}`;
const valuesOf = run => ({ counts: { ...run.counts }, edges: Object.fromEntries(run.edges.map(([s, t, c]) => [s + '→' + t, c])) });

export function openSankey(p) {
  if (sankeyPane.hidden) sk.lastFocus = document.activeElement;
  sk.key = keyOf(p); sk.p = null; sk.from = sk.to = null; sk.dots = []; sk.carry = {}; sk.selected = null;
  sankeyPane.hidden = false;
  sheet.focus();
  updateSankey(p);
}
export function closeSankey() {
  if (sankeyPane.hidden) return;
  sankeyPane.hidden = true;
  sk.key = null; sk.dots = [];
  cancelAnimationFrame(sk.raf); sk.raf = 0;
  if (sk.lastFocus && sk.lastFocus.focus) sk.lastFocus.focus();
}
export function sankeyKey() { return sk.key; }

// a new report for the open pipeline: bands ease to the new counts and dots set off along the ones that grew
export function updateSankey(p) {
  if (!sk.key || keyOf(p) !== sk.key) return;
  if (sk.p && sk.p.seq === p.seq && sk.p.host_ok === p.host_ok) return;   // a state document with no news for it
  const before = sk.p?.run, run = p.run;
  sk.p = p;
  renderHead();
  if (!run) { sk.to = null; renderEmpty(); renderSide(); return; }
  const next = valuesOf(run);
  if (!before || before.run_id !== run.run_id || REDUCED) { sk.from = next; sk.dots = []; sk.carry = {}; }
  else { sk.from = current(); emitDots(before, run); }
  sk.to = next; sk.t0 = performance.now();
  if (sk.selected && !run.nodes.flat().includes(sk.selected)) sk.selected = null;
  renderSide();
  drawFrame();
}
function current() {
  if (!sk.to) return sk.from;
  const e = ease(Math.min(1, (performance.now() - sk.t0) / TWEEN_MS));
  const lerp = (a, b) => (a || 0) + ((b || 0) - (a || 0)) * e;
  const out = { counts: {}, edges: {} };
  for (const n of new Set([...Object.keys(sk.from.counts), ...Object.keys(sk.to.counts)])) out.counts[n] = lerp(sk.from.counts[n], sk.to.counts[n]);
  for (const b of new Set([...Object.keys(sk.from.edges), ...Object.keys(sk.to.edges)])) out.edges[b] = lerp(sk.from.edges[b], sk.to.edges[b]);
  return out;
}
const ease = t => 1 - Math.pow(1 - t, 3);

function emitDots(before, run) {
  const old = Object.fromEntries(before.edges.map(([s, t, c]) => [s + '→' + t, c]));
  const deltas = run.edges.map(([s, t, c]) => [s + '→' + t, Math.max(0, c - (old[s + '→' + t] || 0))]).filter(([, d]) => d > 0);
  const total = deltas.reduce((s, [, d]) => s + d, 0);
  if (!total) return;
  const per = Math.max(1, Math.ceil(total / DOTS_PER_UPDATE));   // one dot per `per` items, the same for every band
  const now = performance.now();
  for (const [key, d] of deltas) {
    const exact = d / per + (sk.carry[key] || 0), n = Math.floor(exact);
    sk.carry[key] = exact - n;
    for (let i = 0; i < n; i++) sk.dots.push({ key, t0: now + Math.random() * 1000, off: Math.random() * 1.6 - 0.8 });
  }
}

// A phone gets a narrower label margin (end labels wrap to two lines) and scrolls the rest sideways.
function geometry() {
  const narrow = chart.clientWidth < NARROW;
  const width = Math.max(chart.clientWidth, narrow ? MIN_WIDTH_NARROW : MIN_WIDTH), height = chart.clientHeight;
  for (const el of [svg, dotsCanvas]) el.style.width = width + 'px';
  const labelW = narrow ? LABEL_W_NARROW : LABEL_W;
  return { W: width - PAD * 2 - labelW, H: Math.max(160, height - PAD * 2), width, height, narrow };
}
function edgeFade() {
  const max = chart.scrollWidth - chart.clientWidth;
  chart.classList.toggle('more-r', chart.scrollLeft < max - 1);
  chart.classList.toggle('more-l', chart.scrollLeft > 1);
}
chart.addEventListener('scroll', edgeFade, { passive: true });
function drawFrame() {
  sk.raf = 0;
  if (!sk.key || !sk.p?.run) return;
  const values = current(), run = sk.p.run, base = sk.p.baseline;
  const { W, H, width, height, narrow } = geometry();
  const edges = Object.entries(values.edges).map(([key, c]) => [...key.split('→'), c]);
  const L = layout(run.nodes, values.counts, edges, W, H, { baseline: base?.counts, gap: LABEL_GAP });
  sk.geo = L;
  renderChart(L, run, base, width, height, narrow);
  edgeFade();
  drawDots(L, width, height);
  const tweening = performance.now() - sk.t0 < TWEEN_MS;
  if (tweening || sk.dots.length) sk.raf = requestAnimationFrame(drawFrame);
}
// Labels sit right of their node: name over figures, so a middle column's label fits before the next column; the
// last column has room for one line on a wide screen. Baseline outlines go under the bands, which show through.
function renderChart(L, run, base, width, height, narrow) {
  const ends = terminals(run, base), whole = shareBase(run), baseCounts = base?.counts || {};
  const baseEdges = Object.fromEntries((base?.edges || []).map(([s, t, c]) => [s + '→' + t, c]));
  const lastCol = run.nodes.length - 1;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const ghosts = L.bands.filter(b => b.key in baseEdges)
    .map(b => `<path class="sk-ghost" d="${bandPath(b, Math.max(1, baseEdges[b.key] * L.k))}"/>`).join('');
  const bands = L.bands.map(b => {
    const tip = `${b.s.name} → ${b.t.name}: ${fmt(b.count)}${b.key in baseEdges ? ` (baseline ${fmt(baseEdges[b.key])})` : ''}`;
    return `<path class="sk-band${sk.selected && b.t.name === sk.selected ? ' on' : ''}" d="${bandPath(b)}"><title>${esc(tip)}</title></path>`;
  }).join('');
  const nodes = [...L.nodes.values()].map(n => {
    const end = ends.has(n.name), y = n.y + n.h / 2, x = n.x + L.nodeW + 6;
    const share = end && whole ? ` · ${(100 * n.value / whole).toFixed(1)}%` : '';
    const delta = end && base ? Math.round(n.value) - (baseCounts[n.name] || 0) : null;
    const figures = `<tspan class="ct"${n.col === lastCol && !narrow ? ' dx="7"' : ` x="${x}" y="${y + 12}"`}>${fmt(n.value)}${share}</tspan>${
      delta !== null ? `<tspan class="dl" dx="7">${delta > 0 ? '+' : delta < 0 ? '−' : '±'}${fmt(Math.abs(delta))}</tspan>` : ''}`;
    const oneLine = n.col === lastCol && !narrow;
    return `<g class="sk-node${end ? ' end' : ''}${sk.selected === n.name ? ' on' : ''}" data-node="${esc(n.name)}" tabindex="${end ? 0 : -1}"
      ${end ? `role="button" aria-label="${esc(`${n.name}: ${fmt(n.value)}, show its latest items`)}"` : ''}>
      <rect x="${n.x}" y="${n.y}" width="${L.nodeW}" height="${n.h}" rx="2"/>
      <text x="${x}" y="${oneLine ? y + 4 : y - 2}"><tspan class="nm">${esc(n.name)}</tspan>${figures}</text></g>`;
  }).join('');
  svg.innerHTML = `<g transform="translate(${PAD},${PAD})">${ghosts}${bands}${nodes}</g>`;
  chart.querySelector('.sk-empty')?.remove();
  const off = !sk.p.host_ok ? `<div class="sk-note" role="status">${esc(sk.p.host)} is offline (${esc(sk.p.host_error || 'no connection')}): this is its last report, written ${age(run.updated_at)} ago</div>` : '';
  const note = chart.querySelector('.sk-note');
  if (off && !note) chart.insertAdjacentHTML('afterbegin', off); else if (!off && note) note.remove();
}
function drawDots(L, width, height) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (dotsCanvas.width !== Math.round(width * dpr) || dotsCanvas.height !== Math.round(height * dpr)) {
    dotsCanvas.width = Math.round(width * dpr); dotsCanvas.height = Math.round(height * dpr);
  }
  const g = dotsCanvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, width, height);
  if (REDUCED) { sk.dots = []; return; }
  const bands = new Map(L.bands.map(b => [b.key, b])), now = performance.now();
  g.fillStyle = '#e6f6ff';
  sk.dots = sk.dots.filter(d => now - d.t0 < DOT_MS && bands.has(d.key));
  for (const d of sk.dots) {
    const u = (now - d.t0) / DOT_MS;
    if (u < 0) continue;
    const b = bands.get(d.key), at = bandPoint(b, u, d.off * Math.max(0, b.w / 2 - 1.5));
    g.globalAlpha = Math.min(1, u * 6, (1 - u) * 6);
    g.beginPath(); g.arc(PAD + at.x, PAD + at.y, 2, 0, Math.PI * 2); g.fill();
  }
  g.globalAlpha = 1;
}

function renderHead() {
  const p = sk.p, r = p.run, st = runStatus(p);
  document.getElementById('skTitle').textContent = p.pipeline;
  document.getElementById('skMeta').innerHTML = [
    `<span><i class="hd" style="background:${hostLook(p.host).color}"></i>${esc(p.host)}</span>`,
    r ? `<span title="${esc(r.run_id)}">${esc(r.label || r.run_id)}</span>` : '',
    `<span class="sk-status ${st.kind}">${esc(st.text)}</span>`,
    r?.started_at ? `<span>started ${age(r.started_at)} ago</span>` : '',
    r?.total ? `<span>${fmt(r.total)} total</span>` : '',
    r && r.status === 'running' ? `<span title="items entering ${esc(r.nodes[0]?.join(', ') || 'the first column')}, over the last 10 s">${r.item_rate ?? 0} items/s</span>` : '',
    p.baseline ? `<span title="${esc(p.baseline.run_id)}">outlines: ${esc(p.baseline.label || p.baseline.run_id)}</span>` : '',
  ].join('');
}
function renderEmpty() {
  const p = sk.p;
  svg.innerHTML = '';
  drawDots({ bands: [] }, chart.clientWidth, chart.clientHeight);
  chart.querySelector('.sk-note')?.remove();
  const text = !p.host_ok
    ? `<b>${esc(p.host)} is offline</b><span>${esc(p.host_error || 'no connection')}. Its pipeline runs show once the deck reaches it again.</span>`
    : `<b>No runs of ${esc(p.pipeline)} on ${esc(p.host)} yet</b><span>A run shows here once it writes events to <code>~/.fleet/pipelines/${esc(p.pipeline)}/</code> on ${esc(p.host)}. A fleetd older than pipeline support reports none.</span>`;
  let el = chart.querySelector('.sk-empty');
  if (!el) el = chart.appendChild(Object.assign(document.createElement('div'), { className: 'sk-empty' }));
  el.setAttribute('role', 'status');
  el.innerHTML = text;
}
// The end nodes as a table (counts, share, change on the baseline); choosing one lists the latest items into it.
function renderSide() {
  const run = sk.p?.run;
  if (!run) { side.innerHTML = ''; return; }
  if (sk.selected) {
    const items = [...(run.recent?.[sk.selected] || [])].reverse();
    side.innerHTML = `<button class="sk-back" data-back>← All end nodes</button>
      <h3>${esc(sk.selected)} <span>${fmt(run.counts[sk.selected] || 0)}</span></h3>
      <p class="sk-sub">${items.length ? `The latest ${items.length} item${items.length === 1 ? '' : 's'} in, newest first` : 'No items recorded into this node yet'}</p>
      <ol class="sk-items">${items.map(item => `<li><div class="it"><b>${esc(item.item)}</b><time>${item.ts ? age(item.ts) + ' ago' : ''}</time></div>${
        Object.entries(item.attrs || {}).map(([key, v]) => `<div class="at"><span>${esc(key)}</span>${
          Array.isArray(v) ? `<ul>${v.map(x => `<li>${esc(typeof x === 'object' ? JSON.stringify(x) : x)}</li>`).join('')}</ul>`
            : `<em>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</em>`}</div>`).join('')}</li>`).join('')}</ol>`;
    return;
  }
  const ends = [...terminals(run, sk.p.baseline)], whole = shareBase(run), base = sk.p.baseline?.counts;
  side.innerHTML = `<h3>End nodes</h3><p class="sk-sub">Choose one for its latest items${base ? '; change is on the outlined run' : ''}.</p>
    <table class="sk-table"><thead><tr><th scope="col">Node</th><th scope="col">Items</th><th scope="col">Share</th>${base ? '<th scope="col">Change</th>' : ''}</tr></thead>
    <tbody>${ends.map(n => {
      const c = run.counts[n] || 0, d = base ? c - (base[n] || 0) : 0;
      return `<tr data-node="${esc(n)}" tabindex="0"><th scope="row">${esc(n)}</th><td>${fmt(c)}</td><td>${whole ? (100 * c / whole).toFixed(1) + '%' : ''}</td>${
        base ? `<td>${d > 0 ? '+' : d < 0 ? '−' : '±'}${fmt(Math.abs(d))}</td>` : ''}</tr>`;
    }).join('')}</tbody></table>`;
}
function choose(node) {
  const run = sk.p?.run;
  if (!run || (node && !terminals(run, sk.p.baseline).has(node))) return;
  sk.selected = node;
  renderSide();
  drawFrame();
  side.scrollTop = 0;
  side.querySelector(node ? '[data-back]' : `tr[data-node]`)?.focus();
}
sankeyPane.addEventListener('click', ev => {
  if (ev.target.closest('[data-sk-close]')) { closeSankey(); return; }
  if (ev.target.closest('[data-back]')) { choose(null); return; }
  const node = ev.target.closest('[data-node]');
  if (node) choose(node.dataset.node);
});
sankeyPane.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') { ev.stopPropagation(); closeSankey(); return; }
  const node = ev.target.closest?.('[data-node]');
  if (node && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); choose(node.dataset.node); return; }
  if (ev.key !== 'Tab') return;
  const items = [...sankeyPane.querySelectorAll('button,[tabindex="0"]')].filter(el => el.getClientRects().length);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && (document.activeElement === first || document.activeElement === sheet)) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
});
new ResizeObserver(() => { if (sk.key && !sk.raf) drawFrame(); }).observe(chart);
// the status line ages ("no writes for 3m") between reports
setInterval(() => { if (sk.key && sk.p) renderHead(); }, 15000);
