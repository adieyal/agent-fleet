// Small helpers shared by the canvas renderers.

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// An action button's data attributes: the click handler receives the action name and its argument.
export function act(name, argument) {
  return `data-act="${esc(name)}"` + (argument === undefined ? '' : ` data-a="${esc(JSON.stringify(argument))}"`);
}

export function bind(key) {
  return `data-bind="${esc(key)}"`;
}

export function clock(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function ago(iso, now) {
  if (!iso) return '';
  const seconds = Math.max(0, ((now ? new Date(now) : new Date()) - new Date(iso)) / 1000);
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export const STATUS = {
  working: ['Working', '#9cc3ba'], idle: ['Idle', '#b7c2c5'], 'waiting-you': ['Waiting on you', '#efb44f'],
  'waiting-criteria': ['Waiting: no criteria', '#efb44f'], paused: ['Paused', '#c3a6e8'], done: ['Done', '#6cc9ad'],
  struggling: ['Struggling', '#efb44f'], blocked: ['Blocked', '#f07a63'], queued: ['Queued', '#b7c2c5'],
};
export const EPIC_STAGE = { shape: ['Shaping', '#efb44f'], deliver: ['Delivering', '#9cc3ba'],
  accept: ['Waiting for your acceptance', '#efb44f'], done: ['Accepted', '#6cc9ad'] };
export const LEVELS = { enforced: ['Enforced', 'solid', '#5d8f84', '#9fe0cb'], guidance: ['Guidance', 'dashed', '#9a8650', '#efc77a'],
  label: ['Label only', 'dotted', '#55636a', '#b7c2c5'] };

export function statusColor(status) {
  return (STATUS[status] || STATUS.idle)[1];
}

export function lighten(hex, amount) {
  const rgb = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));
  return '#' + rgb.map((c) => Math.round(c + (255 - c) * amount).toString(16).padStart(2, '0')).join('');
}

export function tint(hex, alpha) {
  const rgb = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));
  return `rgba(${rgb.join(',')},${alpha})`;
}

export function who(actor) {
  return actor === 'user' || actor === 'web-user' ? 'you' : actor;
}

// A compiled snippet as numbered lines with ✓ compiled, ~ guidance, · off markers.
export function codeLines(compiled) {
  if (!compiled || !compiled.lines) return '';
  return `<div class="cv-code">${compiled.lines.map((line) => {
    const cls = line.kind === 'op' || line.kind === 'option' ? 'op' : line.kind === 'guide' ? 'guide' : line.kind === 'off' ? 'off' : 'plain';
    const note = line.note ? `<span class="note">~ ${esc(line.note)}</span>` : '';
    return `<div class="ln ${cls}"><span class="n">${line.n}</span><span class="m">${esc(line.marker || '')}</span>`
      + `<span class="t">${esc(line.raw || ' ')}</span>${note}</div>`;
  }).join('')}</div>`;
}

export function uid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'op-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : (many || one + 's')}`;
}
