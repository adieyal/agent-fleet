// Reader extras: highlighted code with a copy button, Mermaid and Graphviz diagrams, and images a document links to.
// The vendored libraries load only when a document needs them.

import { esc } from './util.js';
import { fallbackCopy } from './util.js';

const DIAGRAMS = { mermaid: 'mermaid', dot: 'dot', graphviz: 'dot' };
let highlighter = null, mermaidLoad = null, vizLoad = null, mermaidQueue = Promise.resolve(), diagramSeq = 0;

function loadHighlighter() {
  return highlighter ||= import('/vendor/highlight/highlight.min.js').then(m => m.default);
}
// mermaid ships as a classic script that sets window.mermaid
function loadMermaid() {
  return mermaidLoad ||= new Promise((resolve, reject) => {
    const script = Object.assign(document.createElement('script'), { src: '/vendor/mermaid/mermaid.min.js' });
    script.onload = () => resolve(window.mermaid);
    script.onerror = () => { mermaidLoad = null; reject(new Error('The Mermaid renderer could not be loaded.')); };
    document.head.appendChild(script);
  });
}
function loadViz() {
  return vizLoad ||= import('/vendor/viz/viz.js').then(m => m.instance());
}

function languageOf(code) {
  const name = [...code.classList].find(c => c.startsWith('language-'));
  return name ? name.slice(9).toLowerCase() : '';
}

/** Points a document's images at its asset endpoint before they reach the page, so nothing loads from elsewhere.
 *  `assetUrl(path)` gives the URL for an image beside the document, or null when there is no document to resolve against. */
export function linkImages(fragment, assetUrl) {
  for (const img of fragment.querySelectorAll('img')) linkedImage(img, assetUrl);
}

/** Highlights code and draws diagrams inside rendered Markdown already on the page. `drawn` (from drawnDiagrams)
 *  holds diagrams already drawn in this theme: an unchanged one is shown as it was rather than drawn again. */
export function enrichProse(prose, theme, drawn = new Map()) {
  const highlight = [];
  for (const code of prose.querySelectorAll('pre > code')) {
    const pre = code.parentElement, language = languageOf(code);
    if (DIAGRAMS[language]) diagram(pre, DIAGRAMS[language], theme, drawn.get(`${DIAGRAMS[language]}\n${code.textContent}`));
    else {
      codeBlock(pre);
      if (language) highlight.push([code, language]);
    }
  }
  if (highlight.length) loadHighlighter().then(hljs => {
    for (const [code, language] of highlight) {
      if (!hljs.getLanguage(language)) continue;
      code.innerHTML = hljs.highlight(code.textContent, { language, ignoreIllegals: true }).value;
      code.classList.add('hljs');
    }
  }, error => console.warn('highlighting unavailable:', error));
}

/** The diagrams drawn under `root`, by kind and source, for enrichProse to reuse when the document is replaced. */
export function drawnDiagrams(root) {
  const drawn = new Map();
  for (const figure of root.querySelectorAll('.rd-diagram:not(.failed)')) {
    const view = figure.querySelector('.rd-diagram-view');
    if (!view.hasAttribute('role')) drawn.set(`${figure.dataset.diagram}\n${figure.dataset.source}`, view.innerHTML);
  }
  return drawn;
}

/** Redraws a document's diagrams in the reader's new theme. */
export function rethemeDiagrams(root, theme) {
  for (const figure of root.querySelectorAll('.rd-diagram')) drawDiagram(figure, theme);
}

function codeBlock(pre) {
  const wrap = document.createElement('div');
  wrap.className = 'rd-code';
  pre.replaceWith(wrap);
  wrap.innerHTML = '<button type="button" class="rd-code-copy" aria-label="Copy code">Copy</button>';
  wrap.prepend(pre);
  wrap.querySelector('button').addEventListener('click', ev => {
    const button = ev.currentTarget, text = pre.textContent;
    const done = () => { button.textContent = 'Copied'; button.classList.add('ok');
      setTimeout(() => { button.textContent = 'Copy'; button.classList.remove('ok'); }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    else fallbackCopy(text, done);
  });
  return wrap;
}

function diagram(pre, kind, theme, drawnView) {
  const figure = document.createElement('figure');
  figure.className = 'rd-diagram';
  figure.dataset.diagram = kind;
  figure.dataset.source = pre.textContent;
  pre.replaceWith(figure);
  figure.innerHTML = `<div class="rd-diagram-view" role="status">Drawing ${kind === 'dot' ? 'Graphviz' : 'Mermaid'} diagram…</div>
    <figcaption><button type="button" class="rd-diagram-toggle" aria-expanded="false">Show source</button></figcaption>`;
  const source = codeBlock(pre);
  source.hidden = true;
  figure.append(source);
  const toggle = figure.querySelector('.rd-diagram-toggle');
  toggle.addEventListener('click', () => showSource(figure, source.hidden));
  if (drawnView === undefined) { drawDiagram(figure, theme); return; }
  const view = figure.querySelector('.rd-diagram-view');
  view.innerHTML = drawnView;
  view.removeAttribute('role');
}

function showSource(figure, shown) {
  const toggle = figure.querySelector('.rd-diagram-toggle');
  figure.querySelector('.rd-code').hidden = !shown;
  toggle.setAttribute('aria-expanded', String(shown));
  toggle.textContent = shown ? 'Hide source' : 'Show source';
}

async function drawDiagram(figure, theme) {
  const view = figure.querySelector('.rd-diagram-view'), source = figure.dataset.source;
  const colors = palette(figure);
  try {
    if (figure.dataset.diagram === 'mermaid') {
      const svg = await renderMermaid(source, theme, colors);
      view.innerHTML = svg;   // mermaid's strict security level sanitises labels and drops scripts and links
    } else {
      const viz = await loadViz();
      const fg = { color: colors.text, fontcolor: colors.text, fontname: 'Helvetica,Arial,sans-serif' };
      const svg = viz.renderString(source, { format: 'svg', graphAttributes: { ...fg, bgcolor: 'transparent' },
        nodeAttributes: fg, edgeAttributes: { ...fg, color: colors.muted } });
      // an <img> keeps anything in the SVG from running
      view.innerHTML = `<img alt="Graphviz diagram" src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}">`;
    }
    view.removeAttribute('role');
    figure.classList.remove('failed');
  } catch (error) {
    const message = (error && error.message) || String(error);
    view.setAttribute('role', 'alert');
    view.innerHTML = `<p class="rd-diagram-error"><b>This diagram could not be drawn.</b><code>${esc(message)}</code></p>`;
    figure.classList.add('failed');
    showSource(figure, true);
  }
}

// one render at a time: mermaid keeps global configuration
function renderMermaid(source, theme, colors) {
  const run = mermaidQueue.then(async () => {
    const mermaid = await loadMermaid();
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true, theme: 'base',
      fontFamily: colors.font, themeVariables: { darkMode: theme === 'dark', background: colors.bg, fontFamily: colors.font,
        primaryColor: colors.bg2, primaryTextColor: colors.text, primaryBorderColor: colors.muted, lineColor: colors.muted,
        secondaryColor: colors.code, tertiaryColor: colors.bg, textColor: colors.text, noteBkgColor: colors.code,
        noteTextColor: colors.text, noteBorderColor: colors.rule } });
    const id = `rd-mermaid-${++diagramSeq}`;
    try {
      return (await mermaid.render(id, source)).svg;
    } finally {
      document.getElementById(id)?.remove();
      document.getElementById('d' + id)?.remove();
    }
  });
  mermaidQueue = run.catch(() => {});
  return run;
}

function palette(element) {
  const style = getComputedStyle(element), value = name => style.getPropertyValue(name).trim();
  return { bg: value('--rd-bg'), bg2: value('--rd-bg2'), code: value('--rd-code'), text: value('--rd-text'),
    muted: value('--rd-muted'), rule: value('--rd-rule'), font: value('--sans') };
}

// Relative images load through the document's asset endpoint; remote ones are never fetched.
function linkedImage(img, assetUrl) {
  const src = img.getAttribute('src') || '', alt = img.getAttribute('alt') || '';
  if (/^data:image\//i.test(src)) return;
  if (/^([a-z][a-z\d+.-]*:|\/\/)/i.test(src)) {
    const label = `Remote image not loaded: ${alt || src}`;
    img.replaceWith(Object.assign(document.createElement('span'), { className: 'rd-img-note',
      innerHTML: /^https?:/i.test(src) ? `<a href="${esc(src)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label) }));
    return;
  }
  let path;
  try { path = decodeURIComponent(src.split(/[?#]/)[0]); } catch (error) { path = src; }
  const url = path && assetUrl ? assetUrl(path) : null;
  if (!url) { unavailable(img, alt || src, 'no document to resolve it against'); return; }
  img.decoding = 'async';
  img.addEventListener('error', async () => {
    let reason = 'it could not be loaded';
    try { reason = (await (await fetch(url)).json()).error || reason; } catch (error) { /* not JSON */ }
    unavailable(img, alt || path, reason);
  }, { once: true });
  img.src = url;
}

function unavailable(img, name, reason) {
  img.replaceWith(Object.assign(document.createElement('span'), { className: 'rd-img-note',
    textContent: `Image unavailable: ${name} (${reason})` }));
}
