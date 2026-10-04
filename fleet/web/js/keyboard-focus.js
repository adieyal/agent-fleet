// Preserve a control's identity when a streamed view replaces its DOM.
export function focusIdentity(root, element = document.activeElement) {
  if (!root.contains(element) || element === root) return null;
  const path = [];
  for (let node = element; node !== root; node = node.parentElement) {
    if (node.id) { path.unshift('#' + CSS.escape(node.id)); break; }
    const identities = ['data-project', 'data-floor', 'data-label', 'data-hosts', 'data-job', 'data-id', 'data-task', 'data-plan-item', 'data-epic', 'data-slice'];
    const data = [...node.attributes].filter(a => a.name.startsWith('data-')
      && (node.matches('button,input,select,textarea') || identities.includes(a.name)));
    let selector = node.tagName.toLowerCase();
    if (data.length) selector += data.map(a => `[${a.name}="${CSS.escape(a.value)}"]`).join('');
    else if (node.classList.length) selector += '.' + CSS.escape(node.classList[0]);
    else selector += `:nth-of-type(${[...node.parentElement.children].filter(n => n.tagName === node.tagName).indexOf(node) + 1})`;
    path.unshift(selector);
  }
  return path.join(' > ');
}
export function restoreFocus(root, identity) {
  const target = identity && root.querySelector(identity);
  if (!target || !target.getClientRects().length || target.disabled) return false;
  target.focus({ preventScroll: true });
  return document.activeElement === target;
}
// Sheets above a floor or building own Escape and keyboard focus.
export function overlayOpen() {
  return ['reader', 'libraryPane', 'attnPanel', 'workarea', 'runPanel', 'sankey', 'itemHistoryPanel', 'keyboardHelp']
    .some(id => { const el = document.getElementById(id); return el && (el.tagName === 'DIALOG' ? el.open : !el.hidden); })
    || document.getElementById('panel').classList.contains('open');
}
