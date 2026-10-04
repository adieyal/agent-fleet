// P8: capture before view handlers so closing help never consumes the underlying Escape.
const overlay = document.getElementById('keyboardHelp');
const sheet = overlay.querySelector('[role="dialog"]');
const trigger = document.getElementById('keyboardHelpOpen');
let previous = null, inert = [];
const visible = id => !document.getElementById(id).hidden;
function context() {
  if (visible('reader')) return ['Document reader', 'Close the document reader.', [['← / →', 'Previous / next document']]];
  const bench = document.getElementById('benchRoute');
  if (visible('sankey')) return ['Pipeline', 'Close the pipeline diagram.', [['Enter / Space', 'Open the focused node']]];
  if (visible('libraryPane')) return ['Project library', 'Close the project library.', []];
  if (visible('workarea')) return ['Workarea', 'Close the workarea; keep the job panel open.', []];
  if (visible('runPanel')) return ['Running', 'Close the Running list.', [['Enter / Space', 'Open the focused job']]];
  if (visible('attnPanel')) return ['Attention', 'Close the attention list.', []];
  if (document.getElementById('panel').classList.contains('open')) return ['Job or session panel', 'Close the job or session panel.', []];
  if (bench && !bench.hidden && bench.dataset.level !== 'floor') {
    if (bench.hasAttribute('data-editing')) return ['Guidance editor', 'Ask before discarding an unsaved edit; stay here while saving.', []];
    return ['Floor route', 'Step back one level in the floor.', []];
  }
  const view = document.body.dataset.view;
  if (view === 'building') return ['Building', document.querySelector('#buildingUi [role="dialog"]') ? 'Close the building dialog.' : 'No open dialog to close.', []];
  if (view === 'world') return ['World', 'Frame the whole project floor.', []];
  if (view === 'floor') return ['Floor', document.body.hasAttribute('data-readonly') ? 'Return to the storehouse.' : bench.dataset.level === 'floor' ? 'Return to the building.' : 'Step back one level in the floor.', [['+ / =', 'Zoom in'], ['− / _', 'Zoom out'], ['F', 'Fit the floor']]];
  return ['Deck', 'No open panel to close.', [['+ / =', 'Zoom in'], ['− / _', 'Zoom out'], ['F', 'Fit the deck'], ['Enter / Space', 'Spread a focused crowd badge']]];
}
function close() {
  overlay.hidden = true;
  for (const [el, was] of inert) el.inert = was;
  inert = [];
  trigger.setAttribute('aria-expanded', 'false');
  if (previous?.isConnected) previous.focus({ preventScroll: true });
}
function open() {
  if (!overlay.hidden) return;
  const [view, escape, keys] = context();
  overlay.querySelector('[data-help-context]').textContent = view;
  overlay.querySelector('[data-help-escape]').textContent = escape;
  const list = overlay.querySelector('[data-help-keys]');
  list.replaceChildren();
  for (const [key, action] of [...keys, ['Tab / Shift+Tab', 'Move between controls'], ['Enter / Space', 'Activate a focused button'], ['?', 'Open keyboard help']]) {
    const row = document.createElement('div'), kbd = document.createElement('kbd'), text = document.createElement('span');
    kbd.textContent = key; text.textContent = action; row.append(kbd, text); list.append(row);
  }
  previous = document.activeElement;
  inert = [...document.body.children].filter(el => el !== overlay && !['SCRIPT'].includes(el.tagName)).map(el => [el, el.inert]);
  for (const [el] of inert) el.inert = true;
  overlay.hidden = false;
  trigger.setAttribute('aria-expanded', 'true');
  sheet.focus({ preventScroll: true });
}
trigger.addEventListener('click', open);
overlay.addEventListener('click', ev => { if (ev.target.closest('[data-help-close]')) close(); });
overlay.addEventListener('pointerdown', ev => ev.stopPropagation());
window.addEventListener('keydown', ev => {
  if (!overlay.hidden) {
    ev.stopImmediatePropagation();
    if (ev.key === 'Escape' || ev.key === '?') { ev.preventDefault(); close(); }
    else if (ev.key === 'Tab') { ev.preventDefault(); overlay.querySelector('button').focus(); }
    return;
  }
  if (ev.key !== '?' || ev.ctrlKey || ev.metaKey || ev.altKey
      || ev.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return;
  ev.preventDefault(); ev.stopImmediatePropagation(); open();
}, { capture: true });
