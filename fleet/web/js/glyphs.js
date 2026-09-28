// Action glyphs: what an agent is doing, as one small picture instead of a sentence (PRD: agents show action glyphs,
// not sentences). The one mapping from a job's or session's state and latest tool event to an action; the words stay
// in the agent panel and the deck log. Each action is drawn as a 24×24 stroked SVG, so new art can re-skin it by
// `data-action` without touching the mapping.

import { activityOf } from './activity.js';

// Tool glyphs use the server's activity class, just like activity phrases.
export function actionOfEvent(ev) {
  if (!ev || ev.kind === 'text') return 'think';
  if (ev.kind === 'error') return 'error';
  if (ev.kind !== 'tool') return 'other';
  return activityOf(ev);
}

// The action for a job or session: its status first (an idle session waits on its human), then its latest event.
export function actionOf(item) {
  switch (item.status) {
    case 'idle': return 'wait';
    case 'queued': case 'done': case 'failed': case 'stalled': case 'cancelled': return item.status;
  }
  return actionOfEvent(item.activity);
}

export const svg = paths => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
export const ACTIONS = {
  read:      { label: 'reading',        svg: svg('<circle cx="10.5" cy="10.5" r="5.5"/><path d="M14.5 14.5 20 20"/>') },
  search:    { label: 'searching',      svg: svg('<circle cx="10.5" cy="10.5" r="5.5"/><path d="M14.5 14.5 20 20"/>') },
  edit:      { label: 'editing',        svg: svg('<path d="M4 20l1-4.5L16 4.5l3.5 3.5L8.5 19z"/><path d="M13.5 7l3.5 3.5"/>') },
  test:      { label: 'running tests',  svg: svg('<path d="M9.5 3.5h5M10.5 3.5v6L5 19a1.3 1.3 0 0 0 1.2 1.5h11.6A1.3 1.3 0 0 0 19 19l-5.5-9.5v-6"/><path d="M7.5 15h9"/>') },
  think:     { label: 'thinking',       svg: svg('<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>') },
  wait:      { label: 'waiting for you', svg: svg('<path d="M9 9a3 3 0 1 1 4.2 2.8c-.8.4-1.2 1-1.2 1.8V15"/><path d="M12 18.5v.01"/>') },
  web:       { label: 'browsing', svg: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3v18"/>') },
  plan:      { label: 'planning', svg: svg('<path d="M5 5h14v16H5ZM8 9h8M8 13h8M8 17h4"/>') },
  delegate:  { label: 'delegating', svg: svg('<path d="M12 3v8M4 21v-7h16v7M12 14v7"/>') },
  type:      { label: 'typing', svg: svg('<path d="M2 7h20v12H2ZM5 11h2m3 0h2m3 0h2M6 15h12"/>') },
  doc:       { label: 'writing documents', svg: svg('<path d="M5 2h10l4 4v16H5ZM9 10h6M9 14h6M9 18h4"/>') },
  ship:      { label: 'shipping', svg: svg('<path d="m3 12 9-9 9 9M12 3v18M4 21h16"/>') },
  review:    { label: 'reviewing', svg: svg('<path d="M2 12q10-14 20 0-10 14-20 0Z"/><circle cx="12" cy="12" r="3"/>') },
  build:     { label: 'building', svg: svg('<path d="M3 21V10l9-7 9 7v11ZM8 21v-8h8v8"/>') },
  unknown:   { label: 'Action unknown', svg: svg('<path d="M5 5 19 19M19 5 5 19"/>') },
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
