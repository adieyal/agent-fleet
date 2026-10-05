/* Recogito owns DOM selection/highlights; Fleet validates and persists W3C anchors. */
(() => {
  const initial = JSON.parse(document.getElementById('page-data').textContent);
  const api = `/api/pages/${encodeURIComponent(initial.project)}/${encodeURIComponent(initial.slug)}`;
  const space = /[ \t\r\n\f\v\u00a0]+/gu;
  const normalize = s => s.replace(space, ' ').replace(/^ | $/g, '');
  const points = s => Array.from(s);
  const status = document.getElementById('page-connection');
  const composer = document.getElementById('comment-composer');
  const form = document.getElementById('comment-form');
  const threads = document.getElementById('page-threads');
  const annotators = new Map();
  const unavailable = [];
  let view = initial, pending = null, connected = true, generation = 0;
  let canonical = '';
  const offsets = new Map();
  for (const [index, node] of initial.nodes.entries()) {
    if (node.kind === 'prose') {
      offsets.set(index, points(canonical).length);
      canonical += node.prose_text + ' ';
    }
  }
  canonical = canonical.replace(/ $/, '');

  function element(tag, value, className) {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = value;
    if (className) el.className = className;
    return el;
  }
  function button(label, callback) {
    const el = element('button', label);
    el.type = 'button'; el.addEventListener('click', callback); return el;
  }
  function showComposer(selection, description, parent = null, revision = initial.revision) {
    if (!connected) { status.textContent = 'Disconnected: refresh before submitting a comment.'; return; }
    // Keep a lost-response request's identity until submission succeeds or the reader cancels.
    pending = {selector: selection, parent, revision, comment_id: crypto.randomUUID()};
    document.getElementById('comment-anchor').textContent = description;
    document.getElementById('comment-error').textContent = '';
    form.reset(); composer.hidden = false;
    form.elements.headline.focus();
  }
  function cancel() {
    composer.hidden = true; pending = null;
    for (const {anno} of annotators.values()) anno.cancelSelected();
    renderHighlights();
  }
  document.getElementById('cancel-comment').addEventListener('click', cancel);

  async function post(operation, payload) {
    if (!connected) throw new Error('Disconnected: refresh before submitting.');
    const response = await fetch(`${api}/${operation}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error);
    return body;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!pending) return;
    const submit = form.querySelector('[type=submit]'); submit.disabled = true;
    try {
      await post('comments', {...pending, headline: form.elements.headline.value,
        body: form.elements.body.value, owner: form.elements.owner.value, reason: form.elements.reason.value});
      cancel(); await refresh(true);
      threads.lastElementChild?.scrollIntoView({block: 'nearest'});
    } catch (error) { document.getElementById('comment-error').textContent = error.message; }
    finally { submit.disabled = false; }
  });
  document.querySelectorAll('[data-comment-block]').forEach(el => {
    el.addEventListener('click', () => showComposer({type: 'FragmentSelector', value: el.dataset.commentBlock},
      `Block: ${el.dataset.commentBlock}`));
  });

  function selected(annotation, container, index) {
    const selectors = annotation.target.selector;
    if (selectors.length !== 1) { status.textContent = 'Select text inside one prose block.'; return; }
    const range = selectors[0].range;
    if (!range || !container.contains(range.startContainer) || !container.contains(range.endContainer)) {
      status.textContent = 'Select text inside one prose block.'; return;
    }
    const before = document.createRange(); before.selectNodeContents(container);
    before.setEnd(range.startContainer, range.startOffset);
    const exact = normalize(range.toString());
    const prefix = before.toString().replace(space, ' ').replace(/^ /, '');
    const local = points(prefix).length;
    const start = offsets.get(index) + local, end = start + points(exact).length;
    const chars = points(canonical);
    if (!exact || chars.slice(start, end).join('') !== exact) {
      status.textContent = 'Select text without leading or trailing whitespace inside one prose block.'; return;
    }
    showComposer([{type: 'TextQuoteSelector', exact,
      prefix: chars.slice(Math.max(0, start - 40), start).join(''), suffix: chars.slice(end, end + 40).join('')},
      {type: 'TextPositionSelector', start, end}], `Selected: “${exact}”`);
  }

  // Map canonical code point offsets back to DOM UTF-16 offsets for Recogito's highlighting.
  function rawOffsets(raw) {
    const chars = points(raw), result = []; let utf16 = 0;
    for (let i = 0; i < chars.length; i++) {
      const start = utf16; utf16 += chars[i].length;
      if (/[ \t\r\n\f\v\u00a0]/u.test(chars[i])) {
        while (i + 1 < chars.length && /[ \t\r\n\f\v\u00a0]/u.test(chars[i + 1])) utf16 += chars[++i].length;
        result.push({char: ' ', start, end: utf16});
      } else result.push({char: chars[i], start, end: utf16});
    }
    if (result[0]?.char === ' ') result.shift();
    if (result.at(-1)?.char === ' ') result.pop();
    return result;
  }
  function renderHighlights() {
    if (pending) return;
    for (const [index, {anno, container}] of annotators.entries()) {
      const map = rawOffsets(container.textContent);
      const annotations = view.threads.filter(t => t.attachment.state === 'attached' && t.attachment.node === index)
        .map(t => {
          const {local_start, local_end} = t.attachment;
          return {id: t.id, bodies: [], target: {annotation: t.id, selector: [{
            start: map[local_start].start, end: map[local_end - 1].end,
            quote: container.textContent.slice(map[local_start].start, map[local_end - 1].end)
          }]}};
        });
      anno.setAnnotations(annotations);
    }
  }
  function renderThreads() {
    const empty = threads.querySelector('[data-empty]');
    if (!view.threads.length && !empty) {
      const el = element('p', 'No comments on this page.'); el.dataset.empty = ''; threads.append(el);
    } else if (view.threads.length) empty?.remove();
    for (const thread of view.threads) {
      let card = document.getElementById(`thread-${thread.id}`);
      if (!card) {
        card = element('section', undefined, 'thread'); card.id = `thread-${thread.id}`;
        card.dataset.attentionId = thread.id;
        card.append(element('h3', thread.headline), element('p', thread.annotation.body));
        const meta = element('p', '', 'meta'); meta.dataset.meta = ''; card.append(meta);
        const anchor = element('p', '', 'meta'); anchor.dataset.anchor = ''; card.append(anchor);
        const answers = element('div'); answers.dataset.answers = ''; card.append(answers);
        const reply = element('form'); reply.dataset.reply = '';
        const label = element('label', 'Answer');
        const field = element('textarea'); field.name = 'answer'; field.required = true; label.append(field);
        const submit = element('button', 'Answer and resolve'); submit.type = 'submit';
        const error = element('p'); error.setAttribute('role', 'alert');
        reply.append(label, submit, error);
        reply.addEventListener('submit', async event => {
          event.preventDefault(); submit.disabled = true;
          try { await post('answer', {item_id: thread.id, answer: field.value}); field.value = ''; await refresh(true); }
          catch (failure) { error.textContent = failure.message; }
          finally { submit.disabled = false; }
        });
        card.append(reply, button('Follow up', () => showComposer(thread.annotation.selector,
          `Follow-up to: ${thread.headline}`, thread.id, thread.annotation.revision)));
        threads.append(card);
      }
      card.querySelector('[data-meta]').textContent = `${thread.owner} · ${thread.state} · ${thread.annotation.creator}`;
      const anchor = card.querySelector('[data-anchor]');
      anchor.textContent = thread.attachment.reason || (thread.attachment.block ? `Block: ${thread.attachment.block}` : 'Attached to selected prose');
      anchor.classList.toggle('detached', thread.attachment.state !== 'attached');
      if (thread.attachment.state !== 'attached' && !anchor.querySelector('a')) {
        const link = element('a', ' Open creation revision');
        link.href = `/pages/${encodeURIComponent(initial.project)}/${encodeURIComponent(initial.slug)}?revision=${encodeURIComponent(thread.annotation.revision)}`;
        anchor.append(link);
      }
      const answers = card.querySelector('[data-answers]');
      for (const answer of thread.answers) {
        if (!answers.querySelector(`[data-decision-id="${CSS.escape(answer.id)}"]`)) {
          const item = element('div', undefined, 'answer'); item.dataset.decisionId = answer.id;
          item.append(element('p', answer.answer), element('p', `${answer.actor} · ${answer.time}`, 'meta')); answers.append(item);
        }
      }
      card.querySelector('[data-reply]').hidden = thread.state === 'resolved';
      if (thread.annotation.parent && !card.querySelector('[data-parent]')) {
        const link = element('a', 'Parent comment'); link.dataset.parent = ''; link.href = `#thread-${thread.annotation.parent}`; card.append(link);
      }
    }
    renderHighlights();
  }

  async function refresh(force = false) {
    const request = ++generation;
    try {
      // Keep anchors tied to the prose actually on screen, including when its Git revision changes elsewhere.
      const response = await fetch(`${api}?revision=${encodeURIComponent(initial.revision)}`, {cache: 'no-store'});
      const next = await response.json();
      if (!response.ok) throw new Error(next.error);
      if (request !== generation) return;
      connected = true;
      status.textContent = unavailable.length ? `Commenting unavailable for prose blocks ${unavailable.join(', ')}: rendered text differs from canonical text.` : next.historical ? 'Page changed: reload for current prose. Comments refer to the displayed revision.' : 'Select prose to comment, or use Comment on block.';
      if (force || next.state_version !== view.state_version) { view = next; renderThreads(); }
    } catch (error) {
      if (request !== generation) return;
      connected = false; status.textContent = `Disconnected: ${error.message}. Drafts are kept; refresh before submitting.`;
    }
  }
  for (const container of document.querySelectorAll('[data-prose-node]')) {
    const index = Number(container.dataset.proseNode);
    if (normalize(container.textContent) !== initial.nodes[index].prose_text) {
      unavailable.push(index);
      status.textContent = 'Commenting unavailable for a prose block: rendered text differs from canonical text.';
      continue;
    }
    const anno = RecogitoJS.createTextAnnotator(container, {style: {fill: '#e0bb72', fillOpacity: 0.35}});
    anno.on('createAnnotation', annotation => selected(annotation, container, index));
    anno.on('selectionChanged', selection => {
      const id = selection[0]?.id;
      if (id && document.getElementById(`thread-${id}`)) document.getElementById(`thread-${id}`).scrollIntoView({block: 'nearest'});
    });
    annotators.set(index, {anno, container});
  }
  renderThreads();
  const stream = new EventSource('/api/stream');
  stream.addEventListener('state', () => refresh());
  // CLI writes need not bump the web process's SSE counter; reconcile persisted thread state as well.
  const timer = setInterval(() => refresh(), 2000);
  addEventListener('pagehide', () => { clearInterval(timer); stream.close(); });
})();
