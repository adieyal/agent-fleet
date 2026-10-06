// Patch a live DOM tree to match freshly rendered HTML without replacing what did not change,
// so focus, caret, scroll and an input being typed in survive every live update.

export function morph(root, html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  morphChildren(root, template.content);
}

function key(node) {
  return node.nodeType === 1 ? node.getAttribute('data-key') : null;
}

function morphChildren(from, to) {
  const keyed = new Map();
  for (const child of Array.from(from.childNodes)) {
    const k = key(child);
    if (k) keyed.set(k, child);
  }
  let cursor = from.firstChild;
  for (const next of Array.from(to.childNodes)) {
    const k = key(next);
    let match = null;
    if (k && keyed.has(k)) {
      match = keyed.get(k);
      keyed.delete(k);
    } else if (!k && cursor && !key(cursor) && sameKind(cursor, next)) {
      match = cursor;
    }
    if (match) {
      if (match !== cursor) from.insertBefore(match, cursor);
      else cursor = cursor.nextSibling;
      morphNode(match, next);
    } else {
      from.insertBefore(next, cursor);
    }
  }
  while (cursor) {
    const following = cursor.nextSibling;
    from.removeChild(cursor);
    cursor = following;
  }
  for (const stale of keyed.values()) if (stale.parentNode === from) from.removeChild(stale);
}

function sameKind(a, b) {
  return a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName);
}

function morphNode(from, to) {
  if (from.nodeType === 3 || from.nodeType === 8) {
    if (from.nodeValue !== to.nodeValue) from.nodeValue = to.nodeValue;
    return;
  }
  const focused = document.activeElement === from;
  for (const attribute of Array.from(from.attributes)) {
    if (!to.hasAttribute(attribute.name)) from.removeAttribute(attribute.name);
  }
  for (const attribute of Array.from(to.attributes)) {
    if (from.getAttribute(attribute.name) !== attribute.value) from.setAttribute(attribute.name, attribute.value);
  }
  if (from.tagName === 'INPUT' || from.tagName === 'TEXTAREA' || from.tagName === 'SELECT') {
    const value = to.tagName === 'TEXTAREA' ? to.textContent : to.getAttribute('value') ?? '';
    if (!focused && from.value !== value) from.value = value;
    if (from.tagName === 'TEXTAREA') return;
  }
  morphChildren(from, to);
}
