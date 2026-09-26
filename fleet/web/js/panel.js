// Side panel, crew manifest, stats, deck log and hints.

import * as THREE from 'three';
import { BOT_H, DEBUG, DEMO, PI, QS } from './env.js';
import { age, clock, esc, mix, trunc } from './util.js';
import { AGENT_COLOR, TOOL_ICON, hostLook } from './looks.js';
import { isSession, shortId } from './activity.js';
import { ROBOT, renderer } from './scene.js';
import {
  ents, everLoaded, feed, feedSeeded, hosts, live, seenEvents, selectedKey, setFeedSeeded, setSelectedKey, workOf,
} from './model.js';
import { DOC_KIND, docMeta, docsOf, kindOf } from './docs3d.js';
import { action, buildRobot } from './agents.js';
import { dismiss, entered, hiddenCount, restoreDismissed, retiredCount, showFinished, toggleFinished } from './state.js';
import { focusOn } from './camera.js';
import { openCount } from './attention.js';
import { openReader } from './reader.js';
import { openWorkarea } from './workarea.js';

// ------------------------------------------------------------------ portraits for the manifest and the panel
// Rendered once per look into an offscreen target with the main renderer, then copied into small 2D canvases.
const portraits = new Map();
let portraitStage = null;
function renderPortrait(look, agent, pose, W, H) {
  if (!portraitStage) {
    const s = new THREE.Scene();
    s.add(new THREE.HemisphereLight(0xc8d8ff, 0x302838, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(2, 3, 4); s.add(key);
    const c = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 50);
    portraitStage = { scene: s, camera: c };
  }
  const { scene: s, camera: c } = portraitStage;
  const bot = buildRobot(look, agent);
  action(bot, 'Idle').play();
  bot.mixer.update(0.5);
  if (pose === 'off') { bot.main.color.set(mix(look.color, '#475163', 0.55)); bot.face.color.set('#3a4252'); if (bot.eyes) { bot.eyes.color.set('#3a4252'); bot.eyes.emissiveIntensity = 0; } }
  bot.root.rotation.y = 0.45 + (DEBUG && QS.get('bots') === 'back' ? PI : 0);
  s.add(bot.root);
  const hgt = BOT_H + 0.4, wid = hgt * W / H;
  c.left = -wid / 2; c.right = wid / 2; c.top = hgt / 2; c.bottom = -hgt / 2;
  c.position.set(0, hgt / 2 + 0.6, 6); c.lookAt(0, hgt / 2 - 0.05, 0); c.updateProjectionMatrix();
  const rt = new THREE.WebGLRenderTarget(W * 2, H * 2);
  rt.texture.colorSpace = THREE.SRGBColorSpace;
  renderer.setRenderTarget(rt);
  renderer.clear();
  renderer.render(s, c);
  const px = new Uint8Array(W * 2 * H * 2 * 4);
  renderer.readRenderTargetPixels(rt, 0, 0, W * 2, H * 2, px);
  renderer.setRenderTarget(null);
  s.remove(bot.root);
  rt.dispose(); bot.main.dispose(); bot.face.dispose(); bot.hostMat.dispose(); bot.eyes?.dispose();
  const out = document.createElement('canvas'); out.width = W * 2; out.height = H * 2;
  const img = out.getContext('2d').createImageData(W * 2, H * 2);
  const row = W * 2 * 4;
  for (let y = 0; y < H * 2; y++) img.data.set(px.subarray((H * 2 - 1 - y) * row, (H * 2 - y) * row), y * row);
  out.getContext('2d').putImageData(img, 0, 0);
  return out;
}
export function miniBot(canvasEl, look, agent, pose) {
  const w = canvasEl.clientWidth || 34, h = canvasEl.clientHeight || 44;
  const r = Math.min(window.devicePixelRatio || 1, 2);
  canvasEl.width = w * r; canvasEl.height = h * r;
  if (!ROBOT) return;
  const key = [look.color, look.acc, agent, pose, w, h].join('|');
  let img = portraits.get(key);
  if (!img) { img = renderPortrait(look, agent, pose, w * r, h * r); portraits.set(key, img); }
  const g = canvasEl.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.drawImage(img, 0, 0, canvasEl.width, canvasEl.height);
}

// ------------------------------------------------------------------ side panel
const panel = document.getElementById('panel');
const PANEL_SCROLL_HOLD_MS = 180;
export let panelScrollUntil = 0, panelRenderPending = false;
document.getElementById('panelBody').addEventListener('scroll', () => {
  panelScrollUntil = performance.now() + PANEL_SCROLL_HOLD_MS;
}, { passive: true });
export function select(key) {
  if (key !== selectedKey) document.getElementById('panelBody').scrollTop = 0;
  setSelectedKey(key);
  panel.classList.add('open');
  panel.setAttribute('aria-hidden', 'false');
  renderPanel();
  focusOn(ents.get(key));
}
export function closePanel() {
  setSelectedKey(null);
  panel.classList.remove('open');
  panel.setAttribute('aria-hidden', 'true');
}
export function renderPanel() {
  // mid-scroll, updates wait until the scroll settles rather than rewriting content under it
  const wait = panelScrollUntil - performance.now();
  if (wait > 0) {
    if (!panelRenderPending) { panelRenderPending = true; setTimeout(() => { panelRenderPending = false; renderPanel(); }, wait + 20); }
    return;
  }
  const e = workOf(selectedKey);
  if (!e) return;
  if (isSession(e)) { renderSessionPanel(e); return; }
  const j = e.job;
  const ref = `${e.host}:${j.id}`;
  const headHtml = `<canvas style="width:46px;height:60px"></canvas>
    <div style="min-width:0;flex:1"><h2>${esc(j.description)}</h2>
      <div class="sub">
        <span class="chip"><i style="background:${e.look.color}"></i><b>${esc(e.host)}</b></span>
        <span class="chip"><i style="background:${AGENT_COLOR[j.agent] || '#ccc'}"></i>${esc(j.agent)}</span>
        <span class="chip st-${esc(j.status)}">${esc(j.status)}</span>
      </div></div>
    ${j.status !== 'running' ? DISMISS_BUTTON : ''}
    <button id="close" aria-label="Close">✕</button>`;
  const steps = j.steps || [];
  const stepIcon = { done: '✓', running: '▶', failed: '✗', cancelled: '⊘', pending: '○' };
  const events = (j.events || []).filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').slice(-18).reverse();
  const cmds = [`fleet attach ${ref}`, `fleet tail ${ref} -f`, `fleet show ${ref}`];
  // state updates arrive for every job, many times a second: replace only the sections that changed,
  // so the rest of the panel keeps its nodes and the scroll position stays put
  const sections = [`
    <h3>Job</h3>
    <dl class="meta">
      <dt>ref</dt><dd>${esc(ref)}</dd>
      <dt>project</dt><dd>${esc(j.project)}</dd>
      <dt>model</dt><dd>${esc(j.model || 'default')}</dd>
      <dt>cwd</dt><dd>${esc(j.cwd)}</dd>
      ${j.permission ? `<dt>perms</dt><dd>${esc(j.permission)}</dd>` : ''}
      <dt>updated</dt><dd>${esc(age(j.updated_at))} ago</dd>
    </dl>`, `
    <h3>Steps · ${steps.filter(s => s.status === 'done').length}/${steps.length}<button class="wa-open" data-workarea="${esc(j.project ?? '')}"${
      j.project ? '' : ' disabled'} title="Open this room's workarea: plan wall, question desk and report tray">Workarea</button></h3>
    <ol class="steps">${steps.map(s => `<li class="${esc(s.status)}"><span class="si">${stepIcon[s.status] || '?'}</span>
      <span class="t">${s.index + 1}. ${esc(s.title)}</span>${s.result ? `<span class="r">${esc(trunc(s.result, 400))}</span>` : ''}</li>`).join('')}</ol>`,
    docsPanelHtml(e),
    (j.todos && j.todos.length) ? `<h3>Agent's own todo list</h3><ul class="todos">${j.todos.map(td => `<li class="${esc(td.status)}">${td.status === 'completed' ? '✓' : td.status === 'in_progress' ? '▸' : '·'} ${esc(td.text)}</li>`).join('')}</ul>` : '', `
    <h3>Recent activity</h3>
    ${events.length ? `<ul class="evs">${events.map(ev => `<li class="${ev.kind === 'error' ? 'err' : ''}"><time>${clock(ev.ts)}</time><span class="k">${esc(eventIcon(ev))}</span><span class="${isCodeEvent(ev) ? 'code' : ''}">${esc(trunc(ev.summary || ev.status || ev.kind, 220))}</span></li>`).join('')}</ul>` : '<p class="muted" style="font-size:12px">No events yet.</p>'}`, `
    <h3>Commands</h3>
    ${cmds.map(c => `<div class="cmd"><code>${esc(c)}</code><button data-copy="${esc(c)}">copy</button></div>`).join('')}`];
  patchPanel(headHtml, sections, e, j.status === 'done' ? 'off' : j.status === 'stalled' ? 'slump' : 'normal');
}
const DISMISS_BUTTON = '<button id="dismiss" title="Hide this agent from the deck until it has new activity">Dismiss</button>';
// State updates arrive for every job, many times a second: rewrite the head and each body section only when its
// markup changed, so the rest of the panel keeps its nodes and the scroll position stays put.
function patchPanel(headHtml, sections, e, pose) {
  const head = document.getElementById('panelHead');
  if (head.lastHtml !== headHtml) {
    head.lastHtml = headHtml;
    head.innerHTML = headHtml;
    miniBot(head.querySelector('canvas'), e.look, e.job.agent, pose);
    head.querySelector('#close').addEventListener('click', closePanel);
    head.querySelector('#dismiss')?.addEventListener('click', () => dismiss(selectedKey));
  }
  const body = document.getElementById('panelBody');
  if (body.childElementCount !== sections.length) body.replaceChildren(...sections.map(() => document.createElement('div')));
  sections.forEach((html, i) => {
    const part = body.children[i];
    if (part.lastHtml !== html) { part.lastHtml = html; part.innerHTML = html; }
  });
}
// An interactive session: what it is, where it runs, its todos and recent activity. No steps or fleet commands —
// the one useful command is resuming it in a terminal.
function renderSessionPanel(e) {
  const s = e.job;
  const headHtml = `<canvas style="width:46px;height:60px"></canvas>
    <div style="min-width:0;flex:1"><h2>${s.title ? esc(s.title) : '<span class="untitled">no title yet</span>'}</h2>
      <div class="sub">
        <span class="chip sess st-${esc(s.status)}"><i></i>live · ${esc(s.status === 'idle' ? 'waiting for you' : s.status)}</span>
        <span class="chip"><i style="background:${e.look.color}"></i><b>${esc(e.host)}</b></span>
        <span class="chip"><i style="background:${AGENT_COLOR[s.agent] || '#ccc'}"></i>${esc(s.agent)}</span>
      </div></div>
    ${s.status === 'idle' ? DISMISS_BUTTON : ''}
    <button id="close" aria-label="Close">✕</button>`;
  const events = (s.events || []).filter(ev => ev.kind !== 'todos' && ev.kind !== 'session').slice(-18).reverse();
  const sections = [`
    <h3>Interactive session</h3>
    <dl class="meta">
      <dt>session</dt><dd>${esc(s.id)}</dd>
      <dt>project</dt><dd>${esc(s.project)}</dd>
      <dt>model</dt><dd>${esc(s.model || 'not reported yet')}</dd>
      <dt>cwd</dt><dd>${esc(s.cwd)}</dd>
      <dt>started</dt><dd>${s.started_at ? `${esc(clock(s.started_at))} · ${esc(age(s.started_at))} ago` : '<span class="muted">unknown</span>'}</dd>
      <dt>updated</dt><dd>${esc(clock(s.updated_at))} · ${esc(age(s.updated_at))} ago</dd>
    </dl>`,
    (s.todos && s.todos.length) ? `<h3>Agent's own todo list</h3><ul class="todos">${s.todos.map(td => `<li class="${esc(td.status)}">${td.status === 'completed' ? '✓' : td.status === 'in_progress' ? '▸' : '·'} ${esc(td.text)}</li>`).join('')}</ul>` : '', `
    <h3>Recent activity</h3>
    ${events.length ? `<ul class="evs">${events.map(ev => `<li class="${ev.kind === 'error' ? 'err' : ''}"><time>${clock(ev.ts)}</time><span class="k">${esc(eventIcon(ev))}</span><span class="${isCodeEvent(ev) ? 'code' : ''}">${esc(trunc(ev.summary || ev.kind, 220))}</span></li>`).join('')}</ul>` : '<p class="muted" style="font-size:12px">No activity in the transcript tail.</p>'}`,
    s.resume ? `<h3>Resume in a terminal</h3><div class="cmd"><code>${esc(s.resume)}</code><button data-copy="${esc(s.resume)}">copy</button></div>` : ''];
  patchPanel(headHtml, sections, e, 'normal');
}
function docsPanelHtml(e) {
  const docs = docsOf(e.job).reverse();
  if (!docs.length) return '';
  return `<h3>Documents · ${docs.length}</h3><ul class="docs" style="--hc:${e.look.color}">${docs.map(d => {
    const kind = kindOf(d);
    return `<li><button data-doc="${esc(d.id)}" title="Read ${esc(d.name)}"><span class="dk ${kind}" aria-hidden="true">${DOC_KIND[kind].glyph}</span>
      <span class="dn">${esc(d.name)}</span><span class="dm">${DOC_KIND[kind].label.toLowerCase()} · ${esc(docMeta(d))}</span><span class="go">Read →</span></button></li>`;
  }).join('')}</ul>`;
}
panel.addEventListener('click', ev => {
  const workarea = ev.target.closest('[data-workarea]');
  if (workarea) { if (!workarea.disabled) openWorkarea(workarea.dataset.workarea); return; }
  const open = ev.target.closest('[data-doc]');
  if (open) {
    const e = workOf(selectedKey), doc =e && (e.job.documents || []).find(d => d.id === open.dataset.doc);
    if (doc) openReader(e, doc);
    return;
  }
  const b = ev.target.closest('[data-copy]');
  if (!b) return;
  const text = b.dataset.copy;
  const done = () => { b.textContent = 'copied'; b.classList.add('ok'); setTimeout(() => { b.textContent = 'copy'; b.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  else fallbackCopy(text, done);
});
export function fallbackCopy(text, done) {
  const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (err) { /* nothing else to try */ }
  ta.remove();
}
function eventIcon(ev) {
  if (ev.kind === 'tool') return TOOL_ICON[ev.tool] || '•';
  // not a diamond for jobs: that form belongs to the attention lantern alone
  return { text: '“', error: '!', step: '▸', job: '▪', result: '✓', log: '·' }[ev.kind] || '·';
}

// ------------------------------------------------------------------ legend, stats, feed
export function renderLegend() {
  const body = document.getElementById('legendBody');
  if (!hosts.length) { body.innerHTML = '<div class="crew"><small>No hosts reported yet.</small></div>'; return; }
  body.innerHTML = hosts.map(h => {
    const look = hostLook(h.name);
    const jobs = h.jobs || [];
    const running = jobs.filter(j => j.status === 'running').length, sessions = (h.sessions || []).length;
    const sub = h.ok
      ? `${look.label} ·${running ? running + ' working' : jobs.length ? jobs.length + ' job' + (jobs.length === 1 ? '' : 's') : 'idle'}${sessions ? ` · ${sessions} live` : ''}`
      : `offline — ${trunc((h.error || '').replace(/^[^:]+:\s*/, ''), 60)}`;
    return `<div class="crew${h.ok ? '' : ' off'}" title="${esc(h.ok ? h.name : h.error || 'unreachable')}">
      <canvas data-host="${esc(h.name)}"></canvas>
      <div class="who"><b style="color:${look.color}">${esc(h.name)}</b><small>${esc(sub)}</small></div><i class="st"></i></div>`;
  }).join('') + `<div class="agents"><span><i class="visor"></i>visor band · Claude</span><span><span class="eyes"><i></i><i></i></span>twin eyes · Codex</span></div>`;
  for (const cv of body.querySelectorAll('canvas[data-host]')) {
    const h = hosts.find(x => x.name === cv.dataset.host);
    miniBot(cv, hostLook(cv.dataset.host), 'claude', h && !h.ok ? 'off' : 'normal');
  }
}
export function renderStats() {
  const count = { running: 0, queued: 0, done: 0 }, live = { working: 0, idle: 0 };
  for (const e of ents.values()) {
    const tally = isSession(e) ? live : count;
    if (tally[e.job.status] !== undefined) tally[e.job.status]++;
  }
  document.getElementById('stats').innerHTML = `
    ${live.working + live.idle ? `<span class="chip sess" title="interactive Claude Code / Codex sessions"><i></i><b>${live.working + live.idle}</b> live${live.idle ? `<span class="opt"> · ${live.idle} waiting</span>` : ''}</span>` : ''}
    <span class="chip"><i style="background:var(--run)"></i><b>${count.running}</b> working</span>
    <span class="chip opt"><i style="background:var(--warn)"></i><b>${count.queued}</b> queued</span>
    <span class="chip opt"><i style="background:var(--ok)"></i><b>${count.done}</b> done</span>
    <span class="chip" id="needYou" title="open attention items: acknowledged and snoozed ones aren't counted"><i style="background:var(--bad)"></i><b>${openCount}</b> need you</span>
    ${retiredCount ? `<button class="chip restore" id="toggleFinished" title="Show finished jobs that have left the deck"><b>${retiredCount}</b> finished · show</button>`
      : showFinished ? '<button class="chip restore" id="toggleFinished" title="Let finished jobs leave the deck again">hide finished</button>' : ''}
    ${hiddenCount ? `<button class="chip restore" id="restoreDismissed" title="Show dismissed agents again"><b>${hiddenCount}</b> hidden · show</button>` : ''}`;
}
document.getElementById('stats').addEventListener('click', ev => {
  if (ev.target.closest('#restoreDismissed')) restoreDismissed();
  else if (ev.target.closest('#toggleFinished')) toggleFinished();
});
export function renderLive() {
  const el = document.getElementById('live');
  if (DEMO) { el.className = 'live demo'; el.innerHTML = '<i></i><span>demo data</span>'; return; }
  if (live.ok) { el.className = 'live'; el.innerHTML = `<i></i><span>live · ${clock(Date.now() / 1000)}</span>`; }
  else { el.className = 'live bad'; el.innerHTML = `<i></i><span>${everLoaded ? 'server lost · retrying' : 'no server'}</span>`; }
}
export function collectEvents() {
  const fresh = [];
  for (const h of hosts) for (const j of h.jobs || []) for (const ev of j.events || []) {
    if (ev.kind === 'todos' || ev.kind === 'session' || !ev.summary) continue;
    const k = `${h.name}:${j.id}:${ev.ts}:${ev.kind}:${ev.summary}`;
    if (seenEvents.has(k)) continue;
    seenEvents.add(k);
    fresh.push({ host: h.name, id: j.id, label: j.id, ev, isNew: feedSeeded });
  }
  for (const h of hosts) for (const s of h.sessions || []) for (const ev of s.events || []) {
    if (ev.kind === 'todos' || !ev.summary || !s.project) continue;
    const k = `${h.name}:${s.id}:${ev.ts}:${ev.kind}:${ev.summary}`;
    if (seenEvents.has(k)) continue;
    seenEvents.add(k);
    fresh.push({ host: h.name, id: s.id, label: `${s.agent}·${shortId(s.id)}`, live: true, ev, isNew: feedSeeded });
  }
  fresh.sort((a, b) => a.ev.ts - b.ev.ts);
  for (const f of fresh) feed.unshift(f);
  if (!feedSeeded) { feed.sort((a, b) => b.ev.ts - a.ev.ts); setFeedSeeded(true); }
  feed.length = Math.min(feed.length, 60);
  if (seenEvents.size > 5000) { seenEvents.clear(); for (const f of feed) seenEvents.add(`${f.host}:${f.id}:${f.ev.ts}:${f.ev.kind}:${f.ev.summary}`); }
}
// Commands, paths and patterns read better in monospace; agent prose reads better in Inter.
const CODE_TOOLS = new Set(['bash', 'edit', 'read', 'search', 'web']);
function isCodeEvent(ev) { return ev.kind === 'tool' && CODE_TOOLS.has(ev.tool); }

export function renderFeed() {
  const ul = document.getElementById('feedList');
  if (!feed.length) { ul.innerHTML = '<li class="empty">Nothing has happened yet.</li>'; return; }
  ul.innerHTML = feed.map(f => `<li class="${f.ev.kind === 'error' ? 'err ' : ''}${f.isNew ? 'new' : ''}" data-key="${esc(f.host + ':' + f.id)}">
    <i style="background:${hostLook(f.host).color}"></i>${f.live ? '<span class="lv">LIVE</span>' : ''}<b>${esc(f.host)}:${esc(f.label)}</b><span class="k">${esc(eventIcon(f.ev))}</span>
    <span class="s${isCodeEvent(f.ev) ? ' code' : ''}">${esc(f.ev.summary)}</span><time>${clock(f.ev.ts)}</time></li>`).join('');
  for (const f of feed) f.isNew = false;
}
document.getElementById('feedList').addEventListener('click', ev => {
  const li = ev.target.closest('li[data-key]');
  if (li && workOf(li.dataset.key)) select(li.dataset.key);
});
export function updateHint() {
  const hint = document.getElementById('hint');
  if (ents.size) { hint.hidden = true; return; }
  hint.hidden = false;
  hint.innerHTML = entered && everLoaded
    ? '<h2>Nobody’s working here right now</h2><p>Work for this project shows up here when it starts.</p>'
    : everLoaded
    ? `<h2>The deck is quiet</h2><p>No jobs on any host in the last day. Send one with</p><p><code>fleet send -H worker -p myrepo -d "…" -C ~/src/myrepo -s "…"</code></p><p><a href="?demo">See the demo crew</a></p>`
    : `<h2>Waiting for the fleet server</h2><p>This page is served by <code>fleet web</code>. It couldn&apos;t reach <code>/api/stream</code> yet.</p><p><a href="?demo">Open the demo instead</a></p>`;
}
