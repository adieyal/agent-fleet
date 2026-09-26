// A pipeline's run as a Sankey: columns from the run, bands from edge counts, dots travelling as items flow.

import { REDUCED } from './env.js';
import { age, esc } from './util.js';
import { hostLook } from './looks.js';

// ------------------------------------------------------------------ layout (shared with the wall screen's thumbnail)
const MIN_NODE = 4, MIN_BAND = 1.5;
export const fmt = n => Math.round(n).toLocaleString('en-US');

// Nodes keep their true counts; a small one is drawn no thinner than MIN_NODE (bands MIN_BAND) so it stays visible.
// With a baseline, each node's slot is as tall as the larger of its two runs, and the baseline's bands (`ghost`) stack
// in it as the current ones do, so its outlines stay inside the plot and between the node's neighbours.
export function layout(columns, counts, edges, W, H, { nodeW = 12, gap = 14, baseline = {}, baseEdges = [] } = {}) {
  const value = n => counts[n] || 0;
  let k = Infinity;
  for (const col of columns) {
    const sum = col.reduce((s, n) => s + Math.max(value(n), baseline[n] || 0), 0);
    if (sum > 0) k = Math.min(k, (H - gap * (col.length - 1) - MIN_NODE * col.length) / sum);
  }
  if (!Number.isFinite(k) || k < 0) k = 0;
  const nodes = new Map();
  columns.forEach((col, ci) => col.forEach(name => nodes.set(name, { name, col: ci, value: value(name), out: [], in: [],
    x: columns.length > 1 ? ci * (W - nodeW) / (columns.length - 1) : 0 })));
  const bands = [], was = new Map(baseEdges.map(([s, t, c]) => [s + '→' + t, c]));
  for (const [s, t, count] of edges) {
    const a = nodes.get(s), b = nodes.get(t), key = s + '→' + t;
    if (!a || !b) continue;
    const band = { key, s: a, t: b, count, w: Math.max(MIN_BAND, count * k),
      ghost: was.has(key) ? { w: Math.max(1, was.get(key) * k) } : null };
    bands.push(band); a.out.push(band); b.in.push(band);
  }
  const sum = (bs, w) => bs.reduce((s, b) => s + (w(b) || 0), 0);
  columns.forEach(col => {
    const list = col.map(n => nodes.get(n));
    for (const n of list) {
      n.hc = Math.max(MIN_NODE, n.value * k, sum(n.out, b => b.w), sum(n.in, b => b.w));   // drawn
      n.h = Math.max(n.hc, (baseline[n.name] || 0) * k, sum(n.out, b => b.ghost?.w), sum(n.in, b => b.ghost?.w));   // its slot
    }
    const total = list.reduce((s, n) => s + n.h, 0);
    const space = list.length > 1 ? Math.max(gap, (H - total) / (list.length - 1)) : 0;
    let y = list.length > 1 ? 0 : (H - total) / 2;
    for (const n of list) { n.y = y; y += n.h + space; }
  });
  for (const n of nodes.values()) {   // bands leave in their targets' order and arrive in their sources', so few cross
    n.out.sort((p, q) => p.t.y - q.t.y); n.in.sort((p, q) => p.s.y - q.s.y);
    for (const [list, end] of [[n.out, 0], [n.in, 1]]) {
      let y = n.y, g = n.y;
      for (const b of list) {
        b['y' + end] = y + b.w / 2; y += b.w;
        if (b.ghost) { b.ghost['y' + end] = g + b.ghost.w / 2; g += b.ghost.w; }
      }
    }
  }
  for (const b of bands) {
    b.x0 = b.s.x + nodeW; b.x1 = b.t.x;
    if (b.ghost) { b.ghost.x0 = b.x0; b.ghost.x1 = b.x1; }
  }
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
// What a node holds beyond what has left it: items that ended there, or that have not gone on yet.
const leaving = (edges, n) => edges.reduce((s, [a, , c]) => s + (a === n ? c : 0), 0);
export const held = (report, n) => Math.max(0, (report.counts?.[n] || 0) - leaving(report.edges || [], n));
// Nodes items end in, holding at least one. Once the run is done, whatever a node holds ended there. Before that, a run
// line that declares its `ends` (the nodes before the last column that items may stop at) settles it: those and the
// last column end items, what any other node holds is still waiting. Without it a node ends items only if nothing has
// left it in this run nor in the baseline and the run has got past its column (it is the last, a neighbour already
// passes items on, or the run failed). That guess fails for a stage that runs in a burst at the end of a run with no
// baseline: its nodes look like ends until the burst.
export function terminals(run, base) {
  const last = run.nodes.length - 1, done = run.status === 'done', declared = Array.isArray(run.ends) ? new Set(run.ends) : null;
  const left = new Set([...run.edges, ...(base?.edges || [])].map(([s]) => s));
  return new Set(run.nodes.flatMap((col, ci) => col.filter(n => held(run, n) > 0 && (done
    || (declared ? ci === last || declared.has(n)
      : !left.has(n) && (run.status !== 'running' || ci === last || col.some(m => left.has(m))))))));
}
// Items held by nodes that are not ends: waiting for their next step while the run goes, left unfinished if it failed.
export function waiting(run, ends) {
  if (run.status === 'done') return new Map();
  return new Map(run.nodes.flat().filter(n => !ends.has(n)).map(n => [n, held(run, n)]).filter(([, w]) => w > 0));
}
export const waitWord = run => run.status === 'running' ? 'waiting' : 'unfinished';
// A node's share is of the items that reached its column, not of the run's declared total: part-way through a run
// most items have not reached the later columns yet.
export function shareOf(columns, counts, node, value = counts[node] || 0) {
  const col = columns.find(c => c.includes(node)) || [];
  const reached = col.reduce((s, n) => s + (counts[n] || 0), 0);
  return reached ? value / reached : null;
}
export const processed = run => run.nodes[0]?.reduce((s, n) => s + (run.counts[n] || 0), 0) || 0;
// A run part-way has fewer items than its finished baseline, so until it finishes too the two compare by share: the
// baseline's share is shown beside the run's ("prev 27.4%"). Once done, the change in count.
export function versus(run, base, node, value) {
  if (!base) return null;
  const was = held(base, node);
  if (run.status !== 'done') {
    const figure = pct(shareOf(run.nodes, base.counts || {}, node, was)) || '–';
    return { text: `prev ${figure}`, figure };
  }
  const d = Math.round(value) - was, figure = `${d > 0 ? '+' : d < 0 ? '−' : '±'}${fmt(Math.abs(d))}`;
  return { text: figure, figure };
}
// A column whose items each carry a list (their reasons) and are counted under its first entry: every recorded item in
// it names its node first in the same attr. Returns column index → attr key.
export function firstOfColumns(run) {
  const out = new Map();
  run.nodes.forEach((col, ci) => {
    let key = null, seen = 0;
    for (const n of col) for (const item of run.recent?.[n] || []) {
      const k = Object.entries(item.attrs || {}).find(([, v]) => Array.isArray(v) && v[0] === n)?.[0];
      if (!k || (key && k !== key)) return;
      key = k; seen++;
    }
    if (seen) out.set(ci, key);
  });
  return out;
}
// How the bands into each node are coloured: as the run line declares (good, warn, muted), and a node it doesn't name
// that is fed only by nodes of one tone takes theirs, except that what follows a warning (its reasons) is bad.
// Anything else stays neutral.
const TONES = ['good', 'warn', 'bad', 'muted'];
export function tonesOf(run) {
  const tones = new Map(Object.entries(run.tones || {}).filter(([, t]) => TONES.includes(t)));
  for (const col of run.nodes) for (const n of col) {
    if (tones.has(n)) continue;
    const from = new Set(run.edges.filter(([, t]) => t === n).map(([s]) => tones.get(s) ?? null));
    if (from.size !== 1 || from.has(null)) continue;
    const [t] = from;
    tones.set(n, t === 'warn' ? 'bad' : t);
  }
  return tones;
}
const pct = share => share === null ? '' : `${(100 * share).toFixed(1)}%`;

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
const TWEEN_MS = 400, DOT_MS = 1400, DOTS_PER_UPDATE = 80, PAD = 18, WAIT_W = 16;
const LABEL_GAP = 36, LABEL_TOP = 36;   // room above each node, and above the top ones, for a two-line label
const CAPTION_H = 38, MORE_H = 44, LABEL_W = 210, LABEL_W_MAX = 400, MIN_WIDTH = 760, NARROW = 700, LABEL_W_NARROW = 190, COL_W_NARROW = 150;
const NODE_W = 12;
const sk = { key: null, p: null, from: null, to: null, t0: 0, dots: [], carry: {}, raf: 0, selected: null, lastFocus: null, geo: null };

const keyOf = p => `${p.host}:${p.pipeline}`;
const valuesOf = run => ({ counts: { ...run.counts }, edges: Object.fromEntries(run.edges.map(([s, t, c]) => [s + '→' + t, c])) });

export function openSankey(p) {
  if (sankeyPane.hidden) sk.lastFocus = document.activeElement;
  sk.key = keyOf(p); sk.p = null; sk.from = sk.to = null; sk.dots = []; sk.carry = {}; sk.selected = null;
  sankeyPane.hidden = false;
  chart.scrollLeft = 0;
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

// A phone keeps the chart's left-to-right reading: columns stay COL_W_NARROW apart, wide enough for a label above each
// node, end labels wrap to two lines, and the chart scrolls sideways with a visible bar and a "more columns" button.
// The last column's labels sit in the margin right of it, as wide as its longest name needs, up to LABEL_W_MAX (on a
// phone, what is left of the view beside its node); a name wider still wraps (see nameLines).
function geometry(run) {
  const columns = run.nodes.length, narrow = chart.clientWidth < NARROW;
  const most = narrow ? Math.max(LABEL_W_NARROW, chart.clientWidth - PAD * 2 - NODE_W - 8) : LABEL_W_MAX;
  const names = Math.max(0, ...(run.nodes[columns - 1] || []).map(n => textWidth(n, NAME_FONT)));
  const labelW = Math.min(most, Math.max(narrow ? LABEL_W_NARROW : LABEL_W, names + LABEL_ROOM));
  const least = narrow ? PAD * 2 + labelW + NODE_W + COL_W_NARROW * Math.max(0, columns - 1) : MIN_WIDTH;
  const width = Math.max(chart.clientWidth, least), height = chart.clientHeight;
  for (const el of [svg, dotsCanvas]) el.style.width = width + 'px';
  const clear = narrow ? MORE_H : 0;   // the strip the "more columns" button floats over
  return { W: width - PAD * 2 - labelW, H: Math.max(160, height - PAD * 2 - clear), width, height, clear, nameW: labelW - LABEL_ROOM };
}
// How far an end label (its name's lines and a count line, 14 px apart) reaches below its node's middle, and where a
// caption's baseline goes below what it must clear.
const endLabelBelow = (name, nameW) => 7 * (nameLines(name, nameW).length + 1) + 15, CAPTION_GAP = 22;
// Text widths as the chart's labels draw them, measured on a canvas so the margin is known before the chart is laid out.
const NAME_FONT = '600 12.5px', LABEL_ROOM = 7 + 6;   // the gap from the node, and slack for the label's halo
const measurer = document.createElement('canvas').getContext('2d'), widths = new Map();
function textWidth(text, font) {
  const key = font + '|' + text;
  if (!widths.has(key)) {
    measurer.font = `${font} ${getComputedStyle(svg).getPropertyValue('--sans') || 'sans-serif'}`;
    widths.set(key, measurer.measureText(text).width);
  }
  return widths.get(key);
}
// An end node's name in lines no wider than `max`: whole if it fits, else broken at the last space that lets the first
// line fit, the rest ending in "…" if even that is too wide. The node keeps its full name in a tooltip.
export function nameLines(name, max) {
  const fits = s => textWidth(s, NAME_FONT) <= max, words = name.split(' ');
  if (fits(name)) return [name];
  let i = words.length - 1;
  while (i > 0 && !fits(words.slice(0, i).join(' '))) i--;
  const lines = i ? [words.slice(0, i).join(' '), words.slice(i).join(' ')] : [name];
  let last = lines.pop();
  if (!fits(last)) {
    while (last.length > 1 && !fits(last + '…')) last = last.slice(0, -1);
    last = last.trimEnd() + '…';
  }
  return [...lines, last];
}
const more = document.getElementById('skMore');
function edgeFade() {
  const max = chart.scrollWidth - chart.clientWidth;
  chart.classList.toggle('more-r', chart.scrollLeft < max - 1);
  chart.classList.toggle('more-l', chart.scrollLeft > 1);
  // columns whose node is still out of view to the right
  const xs = sk.geo ? [...new Set([...sk.geo.nodes.values()].map(n => n.x))] : [];
  const hidden = xs.filter(x => PAD + x + sk.geo.nodeW > chart.scrollLeft + chart.clientWidth).length;
  more.hidden = !(hidden && sk.p?.run);
  if (more.hidden && document.activeElement === more) sheet.focus();   // keep Esc and Tab in the sheet
  more.textContent = `${hidden} more column${hidden === 1 ? '' : 's'} →`;
  more.setAttribute('aria-label', 'Scroll the chart right');
}
chart.addEventListener('scroll', edgeFade, { passive: true });
more.addEventListener('mousedown', ev => ev.preventDefault());   // a click leaves focus in the sheet
more.addEventListener('click', () => chart.scrollBy({ left: chart.clientWidth * 0.8, behavior: REDUCED ? 'auto' : 'smooth' }));
function drawFrame() {
  sk.raf = 0;
  if (!sk.key || !sk.p?.run) return;
  const values = current(), run = sk.p.run, base = sk.p.baseline;
  const { W, H, width, height, clear, nameW } = geometry(run), firsts = firstOfColumns(run);
  const edges = Object.entries(values.edges).map(([key, c]) => [...key.split('→'), c]);
  // an end label is centred on its node, so under the last column's caption room for the half of it below a thin node
  const lastCol = run.nodes.length - 1, lowest = run.nodes[lastCol]?.at(-1);
  const tail = firsts.has(lastCol) && lowest ? endLabelBelow(lowest, nameW) - CAPTION_GAP : 0;
  const L = layout(run.nodes, values.counts, edges, W, H - LABEL_TOP - (firsts.size ? CAPTION_H + Math.max(0, tail) : 0),
    { baseline: base?.counts, baseEdges: base?.edges, gap: LABEL_GAP });
  sk.geo = L;
  renderChart(L, run, base, width, height, clear, nameW, firsts, { ...run, counts: values.counts, edges });
  edgeFade();
  drawDots(L, width, height);
  const tweening = performance.now() - sk.t0 < TWEEN_MS;
  if (tweening || sk.dots.length) sk.raf = requestAnimationFrame(drawFrame);
}
// A label is its node's name and count, then share and prev for an end node, or what waits in it. Baseline outlines go
// under the bands; what a node holds that has not gone on yet is a hatched stub at its right edge.
function renderChart(L, run, base, width, height, clear, nameW, firsts, now) {
  // which nodes end or wait is the report's; how much, the eased values' (a tween's fractions would make "0 waiting")
  const ends = terminals(run, base), values = now.counts, waits = new Map([...waiting(run, ends).keys()].map(n => [n, held(now, n)]));
  const lastCol = run.nodes.length - 1;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const ghosts = L.bands.filter(b => b.ghost).map(b => `<path class="sk-ghost" d="${bandPath(b.ghost)}"/>`).join('');
  // nearly opaque, edged in the background colour and widest first, so crossings don't add up into phantom bands
  const tones = tonesOf(run);
  const bands = [...L.bands].sort((p, q) => q.w - p.w).map(b => {
    const was = base?.edges?.find(([s, t]) => s + '→' + t === b.key)?.[2], tone = tones.get(b.t.name);
    const tip = `${b.s.name} → ${b.t.name}: ${fmt(b.count)}${was !== undefined ? ` (baseline ${fmt(was)})` : ''}`;
    return `<path class="sk-band${tone ? ' t-' + tone : ''}${sk.selected && b.t.name === sk.selected ? ' on' : ''}" data-band="${esc(b.key)}" d="${bandPath(b)}"><title>${esc(tip)}</title></path>`;
  }).join('');
  const stubRects = [...waits].map(([name, w]) => {
    const n = L.nodes.get(name);
    return { name, w, x: n.x + L.nodeW, y: n.y + n.out.reduce((s, b) => s + b.w, 0), width: WAIT_W, height: Math.max(MIN_BAND, w * L.k) };
  });
  const stubs = stubRects.map(r => `<rect class="sk-wait" x="${r.x}" y="${r.y}" width="${r.width}" height="${r.height}"><title>${
    esc(`${r.name}: ${fmt(r.w)} ${waitWord(run)}`)}</title></rect>`).join('');
  const nodes = [...L.nodes.values()].map(n => {
    const end = ends.has(n.name), kept = end ? held(now, n.name) : 0, share = end ? pct(shareOf(run.nodes, values, n.name, kept)) : '';
    const vs = end ? versus(run, base, n.name, kept) : null;
    const rest = end ? [Math.round(kept) < Math.round(n.value) ? `${fmt(kept)} end here` : '', share, vs?.text].filter(Boolean).join(' · ')
      : waits.has(n.name) ? `${fmt(waits.get(n.name))} ${waitWord(run)}` : '';
    // drawn at the origin; placeLabels moves each where it keeps off the bands
    const lines = n.col === lastCol ? nameLines(n.name, nameW) : [n.name];
    const label = `<text>${lines.map((line, i) => `<tspan class="nm"${i ? ' x="0" dy="14"' : ''}>${esc(line)}</tspan>`).join('')}${n.col === lastCol
        ? `<tspan class="ct" x="0" dy="14">${fmt(n.value)}${rest ? ' · ' + esc(rest) : ''}</tspan>`
        : `<tspan class="ct" dx="6">${fmt(n.value)}</tspan>${rest ? `<tspan class="ct" x="0" dy="14">${esc(rest)}</tspan>` : ''}`}</text>`;
    return `<g class="sk-node${end ? ' end' : ''}${sk.selected === n.name ? ' on' : ''}" data-node="${esc(n.name)}" tabindex="${end ? 0 : -1}"
      ${end ? `role="button" aria-label="${esc(`${n.name}: ${fmt(n.value)}, show its latest items`)}"` : ''}>
      <rect x="${n.x}" y="${n.y}" width="${L.nodeW}" height="${n.hc}" rx="2"/>${label}${
      lines.join(' ') !== n.name ? `<title>${esc(n.name)}</title>` : ''}</g>`;
  }).join('');
  // under a column that counts each item once, by the first of its reasons, say so: below its nodes, and below the end
  // labels beside them
  const captions = [...firsts].map(([ci, key]) => {
    const col = [...L.nodes.values()].filter(n => n.col === ci), x = col[0]?.x ?? 0;
    const bottom = Math.max(0, ...col.map(n => Math.max(n.y + n.h,
      ci === lastCol ? n.y + n.hc / 2 + endLabelBelow(n.name, nameW) - CAPTION_GAP : 0)));
    return `<text class="sk-cap" x="${x}" y="${bottom + CAPTION_GAP}"><tspan>by first of its ${esc(key)}:</tspan><tspan x="${x}" dy="14">each item counted once</tspan></text>`;
  }).join('');
  svg.innerHTML = `<defs><pattern id="skHatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect class="sk-hatch" width="2" height="5"/></pattern></defs>
    <g transform="translate(${PAD},${PAD + LABEL_TOP})">${ghosts}${bands}${stubs}${nodes}${captions}</g>`;
  placeLabels(L, lastCol, width, height - clear, stubRects);
  chart.querySelector('.sk-empty')?.remove();
  const off = !sk.p.host_ok ? `<div class="sk-note" role="status">${esc(sk.p.host)} is offline (${esc(sk.p.host_error || 'no connection')}): this is its last report, written ${age(run.updated_at)} ago</div>` : '';
  const note = chart.querySelector('.sk-note');
  if (off && !note) chart.insertAdjacentHTML('afterbegin', off); else if (!off && note) note.remove();
}
// Which parts of the plot are taken: a grid of CELL-sized cells over the SVG (column by column, so a band's slice is
// one fill), filled where bands (swept along their centre lines), nodes and stubs lie, and a copy with the baseline
// outlines added; each summed so a box is tested at once. Returns [with outlines, without], each (x, y, w, h) →
// whether that box touches anything.
const CELL = 2, BAND_STEPS = 48;
function occupancy(L, rects, x0, y0, width, height) {
  const cols = Math.ceil(width / CELL), rows = Math.ceil(height / CELL);
  const cell = (v, o, n) => Math.min(n - 1, Math.max(0, Math.floor((v - o) / CELL)));
  const fill = (grid, xa, xb, ya, yb) => {
    const r0 = cell(ya, y0, rows), r1 = cell(yb, y0, rows) + 1, c1 = cell(xb, x0, cols);
    for (let c = cell(xa, x0, cols); c <= c1; c++) grid.fill(1, c * rows + r0, c * rows + r1);
  };
  const sweep = (grid, b, half) => {
    let p = bandPoint(b, 0, 0);
    for (let i = 1; i <= BAND_STEPS; i++) {
      const q = bandPoint(b, i / BAND_STEPS, 0);
      fill(grid, p.x, q.x, Math.min(p.y, q.y) - half, Math.max(p.y, q.y) + half);
      p = q;
    }
  };
  const loose = new Uint8Array(cols * rows);
  for (const b of L.bands) sweep(loose, b, b.w / 2 + 0.5);
  for (const r of rects) fill(loose, r.x, r.x + r.width, r.y, r.y + r.height);
  const strict = loose.slice();
  for (const b of L.bands) if (b.ghost) sweep(strict, b.ghost, b.ghost.w / 2 + 1);
  const H = rows + 1;
  return [strict, loose].map(grid => {
    const sum = new Uint32Array((cols + 1) * H);
    for (let c = 0, o = 0, s = H; c < cols; c++, o += rows, s += H) {
      let run = 0;
      for (let r = 0; r < rows; r++) { run += grid[o + r]; sum[s + r + 1] = sum[s - H + r + 1] + run; }
    }
    return (x, y, w, h) => {
      const c0 = cell(x, x0, cols), c1 = cell(x + w, x0, cols) + 1, r0 = cell(y, y0, rows), r1 = cell(y + h, y0, rows) + 1;
      return sum[c1 * H + r1] - sum[c0 * H + r1] - sum[c1 * H + r0] + sum[c0 * H + r0] > 0;
    };
  });
}
// Labels never lie on a band. The last column's sit right of their node, where nothing flows. A node bands leave has
// its label in the nearest free spot of the gaps above or below it, from wholly left of it to wholly right; a node
// nothing leaves (yet), touching it, so it never drifts off towards the next column. Clear of outlines too if it can
// be, else of the bands only. With no free spot it sits on a chip level with its node, left of it if it fits (its stub
// is on the right), moved up or down off other labels, covering the bands there.
const LABEL_REACH = 60, LABEL_STEP = 4, LABEL_M = 2, LABEL_DX = 400;
// the gap search's offsets once, cheapest first: level with the node's left edge, then either way, then further off
const GAP_OFFSETS = [false, true].flatMap(below => Array.from({ length: LABEL_REACH / LABEL_STEP + 1 }, (_, i) => i * LABEL_STEP)
  .flatMap(dy => Array.from({ length: LABEL_DX / LABEL_STEP + 1 }, (_, j) => j * LABEL_STEP)
    .flatMap(d => (d ? [-d, d] : [0]).map(dx => ({ below, dy, dx, cost: dy * 2 + d + (below ? 12 : 0) })))))
  .sort((p, q) => p.cost - q.cost);
function placeLabels(L, lastCol, width, height, stubs) {
  const x0 = -PAD, y0 = -PAD - LABEL_TOP, M = LABEL_M;
  const nodeRects = [...L.nodes.values()].map(n => ({ x: n.x, y: n.y, width: L.nodeW, height: n.hc }));
  const [strict, loose] = occupancy(L, [...nodeRects, ...stubs], x0, y0, width, height);
  const placed = [...svg.querySelectorAll('.sk-cap')].map(el => el.getBBox());
  const inView = (x, y, w, h) => x >= x0 + 1 && y >= y0 + 1 && x + w <= x0 + width - 1 && y + h <= y0 + height - 1;
  const clash = (x, y, w, h) => placed.some(p => x < p.x + p.width + M && p.x < x + w + M && y < p.y + p.height + M && p.y < y + h + M);
  const free = (taken, x, y, w, h) => inView(x, y, w, h) && !taken(x - M, y - M, w + 2 * M, h + 2 * M) && !clash(x, y, w, h);
  // a node bands leave: the gaps above or below it, from wholly left of it to wholly right, nearest first
  function* gapSpots(n, w, h) {
    for (const o of GAP_OFFSETS) if (Math.abs(o.dx) <= w + L.nodeW) yield { x: n.x + o.dx, y: o.below ? n.y + n.hc + 4 + o.dy : n.y - 4 - h - o.dy };
  }
  // a node nothing leaves: touching it (and its stub) above, right, below or left
  const stubEnd = new Map(stubs.map(s => [s.name, s.x + s.width]));
  const besideSpots = (n, w, h) => {
    const right = stubEnd.get(n.name) ?? n.x + L.nodeW, mid = n.y + n.hc / 2 - h / 2, spots = [];
    for (let d = 0; d <= Math.max(w, n.hc / 2 + h / 2); d += LABEL_STEP) {
      if (d <= w - 4) spots.push({ cost: d, x: n.x - d, y: n.y - 4 - h }, { cost: d + 12, x: n.x - d, y: n.y + n.hc + 4 });
      if (d <= right - n.x - 4) spots.push({ cost: d, x: n.x + d, y: n.y - 4 - h }, { cost: d + 12, x: n.x + d, y: n.y + n.hc + 4 });
      if (d <= Math.max(0, n.hc / 2 + h / 2 - 4)) for (const dy of d ? [-d, d] : [0])
        spots.push({ cost: 6 + d, x: right + 4, y: mid + dy }, { cost: 8 + d, x: n.x - 4 - w, y: mid + dy });
    }
    return spots.sort((p, q) => p.cost - q.cost);
  };
  const labels = [...svg.querySelectorAll('.sk-node')].map(g => ({ g, n: L.nodes.get(g.dataset.node), text: g.querySelector('text') }))
    .map(l => ({ ...l, box: l.text.getBBox() }))   // the last column's first: their spots are fixed
    .sort((p, q) => (q.n.col === lastCol) - (p.n.col === lastCol) || p.n.col - q.n.col || p.n.y - q.n.y);  for (const { g, n, text, box } of labels) {
    const w = box.width, h = box.height;
    let spot = null;
    if (n.col === lastCol) spot = { x: n.x + L.nodeW + 7, y: n.y + n.hc / 2 - h / 2 };
    else for (const taken of [strict, loose]) {
      for (const c of n.out.length ? gapSpots(n, w, h) : besideSpots(n, w, h)) if (free(taken, c.x, c.y, w, h)) { spot = c; break; }
      if (spot) break;
    }
    if (!spot) {   // on a chip level with its node, left of it if it fits, shifted up or down off other labels
      const right = n.x + L.nodeW + WAIT_W + 4, mid = n.y + n.hc / 2 - h / 2;
      const chips = [0, ...Array.from({ length: 2 * LABEL_REACH / LABEL_STEP }, (_, i) => (i % 2 ? 1 : -1) * LABEL_STEP * (1 + (i >> 1)))]
        .flatMap(dy => [{ x: n.x - 4 - w, y: mid + dy }, { x: right, y: mid + dy }]).filter(c => inView(c.x, c.y, w, h));
      spot = chips.find(c => !clash(c.x - 4, c.y - 2, w + 8, h + 4)) || chips[0] || { x: n.x, y: mid };
      const chip = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      for (const [k, v] of Object.entries({ class: 'sk-chip', x: spot.x - 4, y: spot.y - 2, width: w + 8, height: h + 4, rx: 4 }))
        chip.setAttribute(k, v);
      g.insertBefore(chip, text);
      g.classList.add('chip');
    }
    text.setAttribute('transform', `translate(${spot.x - box.x},${spot.y - box.y})`);
    placed.push({ x: spot.x, y: spot.y, width: w, height: h });
  }
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
    g.beginPath(); g.arc(PAD + at.x, PAD + LABEL_TOP + at.y, 2, 0, Math.PI * 2); g.fill();
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
    r ? `<span title="items that entered ${esc(r.nodes[0]?.join(', ') || 'the run')}${r.total ? ', of the run\'s declared total' : ''}">${fmt(processed(r))}${r.total ? ' / ' + fmt(r.total) : ''} processed</span>` : '',
    r && r.status === 'running' ? `<span title="items entering ${esc(r.nodes[0]?.join(', ') || 'the first column')}, over the last 10 s">${r.item_rate ?? 0} item${(r.item_rate ?? 0) === 1 ? '' : 's'}/s</span>` : '',
    p.baseline ? `<span title="${esc(p.baseline.run_id)}">outlines: ${esc(p.baseline.label || p.baseline.run_id)}</span>` : '',
  ].join('');
}
function renderEmpty() {
  const p = sk.p;
  svg.innerHTML = '';
  more.hidden = true;
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
    const items = [...(run.recent?.[sk.selected] || [])].reverse(), node = sk.selected;
    // in a list that starts with this node (the item's reasons), the first is why it is counted here
    const entry = (v, i) => i === 0 && v[0] === node
      ? `<li class="first"><b>${esc(v[0])}</b> <small>first · counted here</small></li>`
      : `<li>${esc(typeof v[i] === 'object' ? JSON.stringify(v[i]) : v[i])}</li>`;
    const by = firstOfColumns(run).get(run.nodes.findIndex(col => col.includes(node)));
    side.innerHTML = `<button class="sk-back" data-back>← All end nodes</button>
      <h3>${esc(sk.selected)} <span>${fmt(held(run, node))}</span></h3>
      <p class="sk-sub">${items.length ? `The latest ${items.length} item${items.length === 1 ? '' : 's'} in, newest first`
        : run.edges.some(([s]) => s === node) ? 'Items that ended here are not listed: only nodes nothing leaves keep their latest items'
        : 'No items recorded into this node yet'}${
        by ? `. An item with several ${esc(by)} is counted once, under the first; the others are listed with it` : ''}</p>
      <ol class="sk-items">${items.map(item => `<li><div class="it"><b>${esc(item.item)}</b><time>${item.ts ? age(item.ts) + ' ago' : ''}</time></div>${
        Object.entries(item.attrs || {}).map(([key, v]) => `<div class="at"><span>${esc(key)}</span>${
          Array.isArray(v) ? `<ul>${v.map((_, i) => entry(v, i)).join('')}</ul>`
            : `<em>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</em>`}</div>`).join('')}</li>`).join('')}</ol>`;
    return;
  }
  const ends = [...terminals(run, sk.p.baseline)], base = sk.p.baseline, done = run.status === 'done';
  const sub = !base ? '' : done ? '; change is on the outlined run' : '; prev is the outlined run\'s share, until this one finishes';
  if (!ends.length) { side.innerHTML = '<h3>End nodes</h3><p class="sk-sub">No item has ended anywhere yet.</p>'; return; }
  side.innerHTML = `<h3>End nodes</h3><p class="sk-sub">Choose one for its latest items. Share is of the items that reached its column${sub}.</p>
    <table class="sk-table"><thead><tr><th scope="col">Node</th><th scope="col">Items</th><th scope="col">Share</th>${base ? `<th scope="col">${done ? 'Change' : 'Prev'}</th>` : ''}</tr></thead>
    <tbody>${ends.map(n => {
      const c = held(run, n), vs = versus(run, base, n, c);
      return `<tr data-node="${esc(n)}" tabindex="0"><th scope="row">${esc(n)}</th><td>${fmt(c)}</td><td>${pct(shareOf(run.nodes, run.counts, n, c))}</td>${
        vs ? `<td>${esc(vs.figure)}</td>` : ''}</tr>`;
    }).join('')}</tbody></table>`;
}
// On a phone the chosen node may be off to the side of the chart: scroll it and its whole label into view; for one in
// the last column, to the chart's end, where all that column's labels are whole.
function reveal(node) {
  const g = [...svg.querySelectorAll('.sk-node')].find(el => el.dataset.node === node);
  if (!g) return;
  const r = g.getBoundingClientRect(), c = chart.getBoundingClientRect(), M = 12;
  const last = sk.p?.run?.nodes.at(-1)?.includes(node), end = chart.scrollWidth - chart.clientWidth;
  const left = last ? end : chart.scrollLeft + (r.right + M > c.right ? r.right + M - c.right : r.left - M < c.left ? r.left - M - c.left : 0);
  if (Math.abs(left - chart.scrollLeft) > 0.5) chart.scrollTo({ left, behavior: REDUCED ? 'auto' : 'smooth' });
}
function choose(node) {
  const run = sk.p?.run;
  if (!run || (node && !terminals(run, sk.p.baseline).has(node))) return;
  sk.selected = node;
  renderSide();
  drawFrame();
  if (node) reveal(node);
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
