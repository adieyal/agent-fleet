// Renders a document as the reader shows it (contents, prose, images, diagrams, code, tables), into any element.
// The deck's reader and the canvas both draw documents with this, so a document reads the same everywhere.
import { REDUCED } from './env.js';
import { esc } from './util.js';
import { drawnDiagrams, enrichProse, linkImages } from './rich.js';
export { DOC_KIND, INPUT_KINDS, docsOf, inputDocsOf, jobDocSequence, kindOf } from './doc-kinds.js';

export const WIDE = matchMedia('(min-width: 1101px)');


// the theme button shows the theme it switches to
export const THEME_ICON = {
  dark: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/></svg><span class="lb">Paper</span>',
  paper: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z"/></svg><span class="lb">Dark</span>',
};

function keepImageSizes(prose, fragment) {
  const sizes = new Map([...prose.querySelectorAll('img')].filter(img => img.naturalWidth)
    .map(img => [img.getAttribute('src'), [img.naturalWidth, img.naturalHeight]]));
  for (const img of fragment.querySelectorAll('img')) {
    const size = sizes.get(img.getAttribute('src'));
    if (size && !img.hasAttribute('width')) { img.width = size[0]; img.height = size[1]; }
  }
}

// `refresh` swaps the contents of a document already drawn in `body` (the grid stays, so nothing flickers).
// `assetUrl(path)` points the document's images home; `linkDoc(href)` returns a document id a link opens, if any.
// Returns the prose element and the contents links with their headings.
export function renderDocument(body, data, { assetUrl, theme, refresh = false, linkDoc = null }) {
  const toc = (data.toc || []).filter(x => x.id && x.level <= 3);
  const showToc = toc.length >= 3, top = Math.min(...toc.map(x => x.level));
  const links = showToc ? toc.map(x => `<a href="#doc-${esc(x.id)}" class="l${x.level - top + 1}">${esc(x.text)}</a>`).join('') : '';
  const grid = body.querySelector('.rd-grid');
  const inPlace = refresh && grid && grid.classList.contains('has-toc') === showToc && grid.querySelector('.prose');
  if (inPlace) {
    const nav = grid.querySelector('.rd-toc nav');
    if (nav) nav.innerHTML = `<p class="lbl">Contents</p>${links}`;
    const count = grid.querySelector('.rd-toc summary span');
    if (count) count.textContent = toc.length;
  } else {
    body.innerHTML = `<div class="rd-grid${showToc ? ' has-toc' : ''}">
      ${showToc ? `<details class="rd-toc"><summary>Contents<span>${toc.length}</span></summary><nav aria-label="Contents"><p class="lbl">Contents</p>
        ${links}</nav></details>` : ''}
      <article class="prose"></article></div>`;
  }
  const prose = body.querySelector('.prose');
  const html = document.createElement('template');
  html.innerHTML = data.html;   // rendered server-side with raw HTML escaped; inert until its images are pointed home
  linkImages(html.content, assetUrl);
  // a refresh shows unchanged diagrams as drawn and keeps each image's room while it reloads, so nothing jumps
  const drawn = inPlace ? drawnDiagrams(prose) : new Map();
  if (inPlace) keepImageSizes(prose, html.content);
  prose.replaceChildren(html.content);
  enrichProse(prose, theme, drawn);
  if (data.truncated) prose.insertAdjacentHTML('beforeend', '<p class="rd-note">This document was truncated for the reader. Download the Markdown for the full text.</p>');
  tidyProse(prose, linkDoc);
  if (!inPlace) syncTocMode(body);
  fitTables(body);
  if (document.fonts) document.fonts.ready.then(() => fitTables(body));
  const tocLinks = [...body.querySelectorAll('.rd-toc a')]
    .map(a => ({ a, h: document.getElementById(a.getAttribute('href').slice(1)) }))
    .filter(x => x.h);
  return { prose, tocLinks, refreshed: !!inPlace };
}

export function loadingMarkup() {
  return `<div class="rd-grid"><div class="rd-state rd-skel" aria-label="Loading document" role="status">
    <i class="h"></i>${[96, 88, 93, 60, 0, 91, 97, 85, 70].map(w => w ? `<i style="width:${w}%"></i>` : '<br>').join('')}</div></div>`;
}

export function errorMarkup(message, retryId) {
  return `<div class="rd-grid"><div class="rd-state rd-error" role="alert"><b>Couldn’t open this document</b>
    <code>${esc(message)}</code><br><button class="rd-retry" id="${esc(retryId)}">Try again</button></div></div>`;
}

// heading ids are prefixed so a heading called "panel" or "legend" can't collide with the page's own ids
function tidyProse(prose, linkDoc) {
  for (const el of prose.querySelectorAll('[id]')) el.id = 'doc-' + el.id;
  for (const a of prose.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (href.startsWith('#')) a.setAttribute('href', '#doc-' + href.slice(1));
    else if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    else if (linkDoc) {
      const id = linkDoc(href);
      if (id) { a.dataset.libraryDoc = id; a.href = '#'; }
    }
  }
  for (const table of prose.querySelectorAll('table')) {
    // single tokens (ids, codes) stay on one line; figures get tabular digits
    for (const cell of table.querySelectorAll('td')) {
      const text = cell.textContent.trim();
      if (text.length <= 24 && !/\s/.test(text)) cell.classList.add('nw');
      if (/^[−–+\-]?[£$€]?\d[\d.,]*\s*(%|¢|s|ms|B|KB|MB)?$/.test(text)) cell.classList.add('num');
    }
    const wrap = document.createElement('div'), scroller = document.createElement('div');
    wrap.className = 'rd-table'; scroller.className = 'rd-scroller'; scroller.tabIndex = 0;
    table.replaceWith(wrap); wrap.appendChild(scroller); scroller.appendChild(table);
    scroller.addEventListener('scroll', () => edgeFades(scroller), { passive: true });
  }
  for (const pre of prose.querySelectorAll('pre')) pre.tabIndex = 0;
}

export function syncTocMode(body) {
  const toc = body.querySelector('.rd-toc');
  if (toc) toc.open = WIDE.matches;
}

// give tables that don't fit the 68ch measure a wider column (wide screens only; phones scroll them)
export function fitTables(body) {
  const grid = body.querySelector('.rd-grid');
  if (!grid) return;
  const scrollers = [...grid.querySelectorAll('.rd-scroller')];
  grid.classList.remove('wide');
  if (scrollers.some(s => s.scrollWidth > s.clientWidth + 1)) grid.classList.add('wide');
  scrollers.forEach(edgeFades);
}

// fade the edge a table can still scroll towards, so a clipped column reads as "more this way"
function edgeFades(s) {
  const wrap = s.parentElement, max = s.scrollWidth - s.clientWidth;
  wrap.classList.toggle('more-l', s.scrollLeft > 1);
  wrap.classList.toggle('more-r', s.scrollLeft < max - 1);
}

// An image fills the screen at its own proportions; Esc or a click on it comes back to the reader.
export function showFullscreen(img) {
  if (!document.fullscreenEnabled) return;
  if (document.fullscreenElement === img) { document.exitFullscreen(); return; }
  img.requestFullscreen().catch(() => {});
}

// Clicks inside a drawn document: an image goes fullscreen, a contents link scrolls to its heading.
// Returns true when it handled the click.
export function documentClick(ev) {
  const img = ev.target.closest('.prose img');
  if (img && !img.closest('a')) { showFullscreen(img); return true; }
  const a = ev.target.closest('a[href^="#"]');
  if (!a) return false;
  const target = document.getElementById(a.getAttribute('href').slice(1));
  if (!target) return false;
  ev.preventDefault();
  target.scrollIntoView({ block: 'start', behavior: REDUCED ? 'auto' : 'smooth' });
  const toc = a.closest('.rd-toc');
  if (toc && !WIDE.matches) toc.open = false;
  return true;
}
