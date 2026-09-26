// Action glyphs: what an agent is doing, as one small picture instead of a sentence (PRD: agents show action glyphs,
// not sentences). The one mapping from a job's or session's state and latest tool event to an action; the words stay
// in the agent panel and the deck log. Each action is drawn as a 24×24 stroked SVG, so new art can re-skin it by
// `data-action` without touching the mapping.

import { activityOf } from './activity.js';

const SHELL_TOOLS = new Set(['Bash', 'shell', 'BashOutput']);

// The action for one tool event: search (read, grep, glob, web), edit, test, shell, think, ask, error, or other.
export function actionOfEvent(ev) {
  if (!ev || ev.kind === 'text') return 'think';
  if (ev.kind === 'error') return 'error';
  if (ev.kind !== 'tool') return 'other';
  if (ev.name === 'AskUserQuestion') return 'ask';
  switch (activityOf(ev)) {
    case 'read': case 'search': case 'web': return 'search';
    case 'edit': case 'doc': return 'edit';
    case 'test': return 'test';
    case 'think': case 'plan': return 'think';
  }
  return SHELL_TOOLS.has(ev.name) || ev.tool === 'bash' ? 'shell' : 'other';
}

// The action for a job or session: its status first (an idle session waits on its human), then its latest event.
export function actionOf(item) {
  switch (item.status) {
    case 'idle': return 'ask';
    case 'queued': case 'done': case 'failed': case 'stalled': case 'cancelled': return item.status;
  }
  return actionOfEvent(item.activity);
}

const svg = paths => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
export const ACTIONS = {
  search:    { label: 'reading',        svg: svg('<circle cx="10.5" cy="10.5" r="5.5"/><path d="M14.5 14.5 20 20"/>') },
  edit:      { label: 'editing',        svg: svg('<path d="M4 20l1-4.5L16 4.5l3.5 3.5L8.5 19z"/><path d="M13.5 7l3.5 3.5"/>') },
  test:      { label: 'running tests',  svg: svg('<path d="M9.5 3.5h5M10.5 3.5v6L5 19a1.3 1.3 0 0 0 1.2 1.5h11.6A1.3 1.3 0 0 0 19 19l-5.5-9.5v-6"/><path d="M7.5 15h9"/>') },
  shell:     { label: 'at the terminal', svg: svg('<path d="M5 7l5 5-5 5"/><path d="M12 18h7"/>') },
  think:     { label: 'thinking',       svg: svg('<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>') },
  ask:       { label: 'waiting for you', svg: svg('<path d="M9 9a3 3 0 1 1 4.2 2.8c-.8.4-1.2 1-1.2 1.8V15"/><path d="M12 18.5v.01"/>') },
  error:     { label: 'hit an error',   svg: svg('<path d="M12 6v8"/><path d="M12 18v.01"/>') },
  other:     { label: 'working',        svg: svg('<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>') },
  queued:    { label: 'queued',         svg: svg('<path d="M7 3.5h10M7 20.5h10M8 3.5c0 5 8 5 8 8.5s-8 3.5-8 8.5M16 3.5c0 5-8 5-8 8.5s8 3.5 8 8.5"/>') },
  done:      { label: 'done',           svg: svg('<path d="M5 12.5l4.5 4.5L19 7"/>') },
  failed:    { label: 'failed',         svg: svg('<path d="M6 6l12 12M18 6L6 18"/>') },
  stalled:   { label: 'stalled',        svg: svg('<path d="M9 6v12M15 6v12"/>') },
  cancelled: { label: 'cancelled',      svg: svg('<path d="M6 12h12"/>') },
};

// The glyph's markup: labelled for screen readers, keyed by action for styling.
export function glyphHtml(action) {
  const a = ACTIONS[action];
  return `<span class="glyph" data-action="${action}" role="img" aria-label="${a.label}">${a.svg}</span>`;
}
