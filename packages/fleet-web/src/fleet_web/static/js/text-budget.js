// Count visible words; callers choose the budget for their scope.
export function textBudget(root) {
  let count = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, el = node.parentElement;
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
    if (['SCRIPT', 'STYLE'].includes(el.tagName) || getComputedStyle(el).color === 'rgba(0, 0, 0, 0)') continue;
    count += node.textContent.trim().match(/\S+/g)?.length ?? 0;
  }
  return count;
}
export function assertTextBudget(root, limit) {
  const count = textBudget(root);
  if (count > limit) throw new Error(`Text budget exceeded: ${count} > ${limit}`);
  return count;
}

if (new URLSearchParams(location.search).has('redact')) {
  document.documentElement.dataset.redact = '';
  const style = document.createElement('style');
  style.textContent = '[data-redact] body * { color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; text-decoration: line-through #64748b .7em !important; } [data-redact] input::placeholder { color: transparent !important; } [data-redact] text { fill: transparent !important; stroke: transparent !important; }';
  document.head.append(style);
  for (const method of ['fillText', 'strokeText']) {
    CanvasRenderingContext2D.prototype[method] = function(text, x, y, maxWidth) {
      const width = Math.min(this.measureText(text).width, maxWidth ?? Infinity);
      const height = parseFloat(this.font.match(/[\d.]+px/)[0]);
      const offset = this.textAlign === 'center' ? width / 2 : ['right', 'end'].includes(this.textAlign) ? width : 0;
      this.fillRect(x - offset, y - height * .7, width, height * .7);
    };
  }
}
