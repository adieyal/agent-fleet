/* Recogito owns DOM selection/highlights; Fleet validates and persists W3C anchors. */
(() => {
  const initial = JSON.parse(document.getElementById('page-data').textContent);
  const api = `/api/pages/${encodeURIComponent(initial.project)}/${encodeURIComponent(initial.slug)}`;
  const space = /[ \t\r\n\f\v\u00a0]+/gu;
  const normalize = s => s.replace(space, ' ').replace(/^ | $/g, '');
  const points = s => Array.from(s);
  const status = document.getElementById('page-connection');
  const indicator = document.getElementById('page-live');
  // Always-visible connection state; the status line only carries what needs action.
  function live(state, label, detail = '') {
    indicator.dataset.state = state;
    indicator.lastElementChild.textContent = label;
    indicator.title = detail || label;
  }
  const composer = document.getElementById('comment-composer');
  const form = document.getElementById('comment-form');
  const threads = document.getElementById('page-threads');
  const annotators = new Map();
  const unavailable = [];
  let view = initial, pending = null, connected = true, generation = 0, activeId = null, selectionDraft = null;
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
    if (!Array.isArray(selection) && selection.type === 'FragmentSelector') selectionDraft = null;
    form.reset(); composer.hidden = false;
    document.getElementById('selection-comment').hidden = true;
    document.body.classList.add('comments-open');
    composer.style.top = `${Math.max(0, (selectionDraft?.top ?? anchorElement(selection)?.getBoundingClientRect().top ?? 100) - document.getElementById('page-content').getBoundingClientRect().top)}px`;
    form.elements.body.focus(); layoutThreads();
  }
  function cancel() {
    composer.hidden = true; pending = null; selectionDraft = null; document.body.classList.remove('comments-open');
    for (const {anno} of annotators.values()) anno.cancelSelected();
    renderHighlights(); layoutThreads();
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
      await post('comment-text', {...pending, body: form.elements.body.value, owner: form.elements.owner.value});
      cancel(); await refresh(true);
    } catch (error) { document.getElementById('comment-error').textContent = error.message; }
    finally { submit.disabled = false; }
  });
  document.getElementById('page-content').addEventListener('click', event => {
    const el = event.target.closest('[data-comment-block]');
    if (el) showComposer({type: 'FragmentSelector', value: el.dataset.commentBlock},
      `Block: ${el.dataset.commentBlock}`);
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
    const selector = [{type: 'TextQuoteSelector', exact,
      prefix: chars.slice(Math.max(0, start - 40), start).join(''), suffix: chars.slice(end, end + 40).join('')},
      {type: 'TextPositionSelector', start, end}];
    const rect = range.getBoundingClientRect();
    selectionDraft = {selector, description: `Selected: “${exact}”`, top: rect.top};
    const trigger = document.getElementById('selection-comment');
    trigger.style.left = `${Math.min(innerWidth - 46, rect.right + 8)}px`;
    trigger.style.top = `${Math.max(8, rect.top - 4)}px`; trigger.hidden = false;
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
  function anchorElement(selector) {
    const entries = Array.isArray(selector) ? selector : [selector];
    if (entries[0]?.type === 'FragmentSelector') return document.getElementById(entries[0].value);
    return null;
  }
  function anchorFor(thread) {
    if (thread.attachment.state !== 'attached') return null;
    if (thread.attachment.block) return document.getElementById(thread.attachment.block);
    return document.querySelector(`[data-prose-node="${thread.attachment.node}"] .r6o-annotation[data-annotation="${thread.id}"]`)
      || document.querySelector(`[data-prose-node="${thread.attachment.node}"]`);
  }
  function focusThread(id, moveFocus = true, open = true) {
    activeId = id;
    for (const card of threads.querySelectorAll('.thread')) card.classList.toggle('active', card.dataset.attentionId === id);
    for (const el of document.querySelectorAll('.anchor-active')) el.classList.remove('anchor-active');
    const thread = view.threads.find(t => t.id === id);
    if (thread) anchorFor(thread)?.classList.add('anchor-active');
    document.querySelectorAll('.r6o-annotation').forEach(el => el.classList.toggle('annotation-active', el.dataset.annotation === id));
    for (const item of view.threads) {
      const sameAnchor = item.attachment.state !== 'attached' && thread.attachment.state !== 'attached' ||
        item.attachment.state === 'attached' && thread.attachment.state === 'attached' &&
        (item.attachment.block ? item.attachment.block === thread.attachment.block : item.attachment.node === thread.attachment.node);
      document.getElementById(`thread-${item.id}`).classList.toggle('sheet-visible', sameAnchor);
    }
    if (open) document.body.classList.add('comments-open');
    if (moveFocus) document.getElementById(`thread-${id}`)?.focus({preventScroll: true});
  }
  function relative(value) {
    const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 60000));
    return minutes < 1 ? 'now' : minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` : `${Math.floor(minutes / 1440)}d ago`;
  }
  function layoutThreads() {
    const origin = document.getElementById('page-content').getBoundingClientRect().top;
    let bottom = 0;
    const ordered = [...view.threads].sort((a, b) => {
      const ay = anchorFor(a)?.getBoundingClientRect().top ?? Infinity;
      const by = anchorFor(b)?.getBoundingClientRect().top ?? Infinity;
      return ay - by;
    });
    let detached = false;
    for (const thread of ordered) {
      const card = document.getElementById(`thread-${thread.id}`), anchor = anchorFor(thread);
      if (!anchor && !detached) {
        detached = true;
        let heading = threads.querySelector('.detached-heading');
        if (!heading) { heading = element('div', 'Detached', 'detached-heading'); threads.append(heading); }
        bottom = Math.max(bottom, document.getElementById('page-content').offsetHeight);
        heading.style.top = `${bottom}px`; bottom += 28;
      }
      const top = Math.max(bottom, anchor ? anchor.getBoundingClientRect().top - origin : bottom);
      let placed = top;
      if (!composer.hidden) {
        const composerTop = parseFloat(composer.style.top) || 0;
        if (placed < composerTop + composer.offsetHeight + 12 && placed + card.offsetHeight > composerTop)
          placed = composerTop + composer.offsetHeight + 12;
      }
      card.style.top = `${placed}px`; bottom = placed + card.offsetHeight + 12;
    }
    if (!detached) threads.querySelector('.detached-heading')?.remove();
    threads.style.height = `${bottom}px`;
  }
  function renderBadges() {
    document.querySelectorAll('.anchor-badge').forEach(el => el.remove());
    const groups = new Map();
    for (const thread of view.threads) {
      const anchor = thread.attachment.block ? anchorFor(thread) : document.querySelector(`[data-prose-node="${thread.attachment.node}"]`);
      if (!anchor || thread.attachment.state !== 'attached') continue;
      if (!groups.has(anchor)) groups.set(anchor, []);
      groups.get(anchor).push(thread);
    }
    document.getElementById('detached-badge')?.remove();
    const detached = view.threads.filter(t => t.attachment.state !== 'attached');
    if (detached.length) {
      const badge = button(`Detached (${detached.length})`, () => focusThread(detached[0].id));
      badge.id = 'detached-badge'; badge.className = 'detached-badge'; document.getElementById('page-content').append(badge);
    }
    for (const [anchor, group] of groups) {
      const badge = button(String(group.length), () => focusThread(group[0].id));
      badge.className = 'anchor-badge'; badge.setAttribute('aria-label', `${group.length} comments`);
      anchor.parentElement.style.position = 'relative'; anchor.insertAdjacentElement('afterend', badge); badge.style.top = `${anchorFor(group[0]).getBoundingClientRect().top - anchor.parentElement.getBoundingClientRect().top}px`;
    }
  }
  function renderThreads() {
    for (const thread of view.threads) {
      let card = document.getElementById(`thread-${thread.id}`);
      if (!card) {
        card = element('section', undefined, 'thread'); card.id = `thread-${thread.id}`;
        card.dataset.attentionId = thread.id; card.tabIndex = -1;
        const top = element('div', undefined, 'thread-top');
        top.append(element('strong', thread.annotation.creator));
        const time = element('time'); time.dataset.time = ''; time.title = thread.created; top.append(time);
        const toggle = button('Resolved · Show', () => {
          card.classList.toggle('expanded');
          toggle.textContent = card.classList.contains('expanded') ? 'Resolved · Hide' : 'Resolved · Show';
          layoutThreads();
        });
        toggle.dataset.toggle = ''; top.append(toggle); card.append(top);
        const body = element('div', undefined, 'thread-body');
        body.append(element('p', thread.annotation.body));
        const anchor = element('p', '', 'meta'); anchor.dataset.anchor = ''; body.append(anchor);
        const answers = element('div'); answers.dataset.answers = ''; body.append(answers);
        const reply = element('form'); reply.dataset.reply = '';
        const field = element('textarea'); field.name = 'answer'; field.required = true;
        field.placeholder = 'Reply…'; field.setAttribute('aria-label', 'Reply');
        const actions = element('div', undefined, 'reply-actions');
        const submit = element('button', 'Reply'); submit.type = 'submit'; actions.append(submit);
        const error = element('p'); error.setAttribute('role', 'alert');
        reply.append(field, actions, error);
        reply.addEventListener('submit', async event => {
          event.preventDefault(); submit.disabled = true;
          try {
            await post('reply', {item_id: thread.id, body: field.value});
            field.value = ''; card.classList.add('expanded'); await refresh(true);
          } catch (failure) { error.textContent = failure.message; }
          finally { submit.disabled = false; }
        });
        const resolve = button('Resolve', async () => {
          resolve.disabled = true;
          try { await post('resolve', {item_id: thread.id}); await refresh(true); }
          catch (failure) { error.textContent = failure.message; }
          finally { resolve.disabled = false; }
        });
        resolve.dataset.resolve = ''; resolve.title = 'Close this thread without recording a decision';
        const reopen = button('Re-open', async () => {
          reopen.disabled = true;
          try { await post('reopen', {item_id: thread.id}); await refresh(true); }
          catch (failure) { error.textContent = failure.message; }
          finally { reopen.disabled = false; }
        });
        reopen.dataset.reopen = '';
        const answer = button('Answer & resolve', async () => {
          if (!reply.reportValidity()) return;
          answer.disabled = true;
          try { await post('answer', {item_id: thread.id, answer: field.value}); field.value = ''; await refresh(true); }
          catch (failure) { error.textContent = failure.message; }
          finally { answer.disabled = false; }
        });
        answer.dataset.answerResolve = ''; answer.className = 'meta';
        answer.title = 'Record a decision and close this thread'; actions.append(answer);
        body.append(reply, resolve, reopen); card.append(body);
        card.addEventListener('click', () => focusThread(thread.id, false));
        card.addEventListener('focusin', () => focusThread(thread.id, false));
        threads.append(card);
      }
      let agentStatus = card.querySelector('[data-agent-status]');
      if (!agentStatus) { agentStatus = element('p', undefined, 'meta'); agentStatus.dataset.agentStatus = ''; agentStatus.setAttribute('role', 'status'); card.querySelector('.thread-body').prepend(agentStatus); }
      agentStatus.textContent = thread.agent_status || ''; agentStatus.hidden = !thread.agent_status;
      card.querySelector('[data-time]').textContent = relative(thread.created);
      card.classList.toggle('resolved', thread.state === 'resolved');
      card.querySelector('[data-toggle]').hidden = thread.state !== 'resolved';
      card.querySelector('[data-toggle]').textContent = card.classList.contains('expanded') ? 'Resolved · Hide' : 'Resolved · Show';
      card.querySelector('[data-resolve]').hidden = thread.state === 'resolved';
      card.querySelector('[data-reopen]').hidden = thread.state !== 'resolved';
      card.querySelector('[data-answer-resolve]').hidden = thread.state === 'resolved' || thread.kind !== 'decision';
      const anchor = card.querySelector('[data-anchor]');
      anchor.textContent = thread.attachment.reason || '';
      anchor.classList.toggle('detached', thread.attachment.state !== 'attached');
      if (thread.attachment.state !== 'attached') {
        const link = element('a', ' Open creation revision');
        link.href = `/pages/${encodeURIComponent(initial.project)}/${encodeURIComponent(initial.slug)}?revision=${encodeURIComponent(thread.annotation.revision)}`;
        anchor.append(link);
      }
      const answers = card.querySelector('[data-answers]');
      const messages = [...thread.replies.map(reply => ({...reply, text: reply.body, type: 'reply'})),
        ...thread.answers.map(answer => ({...answer, text: answer.answer, type: 'decision'}))]
        .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
      for (const message of messages) {
        if (!answers.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`)) {
          const item = element('div', undefined, 'answer'); item.dataset.messageId = message.id;
          item.dataset.messageType = message.type;
          const time = element('time', relative(message.time), 'meta'); time.title = message.time;
          item.append(element('strong', message.actor), element('p', message.text), time);
          if (message.type === 'decision') item.append(element('span', 'Decision · answered and resolved', 'meta'));
          answers.append(item);
        }
      }
      if (thread.annotation.parent && !card.querySelector('[data-parent]')) {
        const link = element('a', 'Parent comment'); link.dataset.parent = ''; link.href = `#thread-${thread.annotation.parent}`;
        link.addEventListener('click', () => focusThread(thread.annotation.parent)); card.querySelector('.thread-body').append(link);
      }
    }
    renderHighlights(); renderBadges();
    if (activeId) focusThread(activeId, false, false);
    layoutThreads();
    // Recogito paints its span layer on the next frame. Align to that exact quote.
    requestAnimationFrame(() => {
      layoutThreads();
      document.querySelectorAll('.r6o-annotation').forEach(el => el.classList.toggle('annotation-active', el.dataset.annotation === activeId));
    });
  }

  async function refresh(force = false) {
    const request = ++generation;
    try {
      // Keep anchors tied to the prose actually on screen, including when its Git revision changes elsewhere.
      const response = await fetch(`${api}?revision=${encodeURIComponent(initial.revision)}`, {cache: 'no-store'});
      const next = await response.json();
      if (!response.ok) throw new Error(next.error);
      if (request !== generation) return;
      const runtime = next.runtime;
      connected = !runtime || runtime.healthy;
      const instruction = unavailable.length ? `Commenting unavailable for prose blocks ${unavailable.join(', ')}: rendered text differs from canonical text.` : next.historical ? 'Page changed: reload for current prose. Comments refer to the displayed revision.' : '';
      status.textContent = runtime && !runtime.available
        ? 'Runtime unavailable — start fleet serve. Reconnecting; drafts are kept. Stored records remain readable.'
        : runtime && !runtime.healthy
        ? 'Runtime worker failed — check fleet serve status. Drafts are kept.'
        : runtime?.connection === 'reconnected'
        ? `Runtime reconnected — live updates resumed. ${instruction}`.trim() : instruction;
      if (runtime && !runtime.available) live('offline', 'Runtime unavailable', runtime.error || '');
      else if (runtime && !runtime.healthy) live('degraded', 'Degraded', 'A fleet serve worker failed; see fleet serve status');
      else live('live', 'Live', `Updated ${new Date().toLocaleTimeString()}`);
      if (force || next.state_version !== view.state_version) {
        for (const [index, html] of Object.entries(next.directive_html)) {
          const wrapper = document.querySelector(`[data-directive-node="${index}"]`);
          // Only trusted, escaped server templates enter this rendering surface.
          if (wrapper && wrapper.innerHTML !== html) wrapper.innerHTML = html;
        }
        view = next; renderThreads();
        document.getElementById('page-snapshot').textContent =
          `Current records as of ${next.snapshot_time} · state sequence ${next.state_version}`;
      }
    } catch (error) {
      if (request !== generation) return;
      connected = false; status.textContent = `Disconnected: ${error.message}. Drafts are kept; refresh before submitting.`;
      live('offline', 'Disconnected', error.message);
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
      if (id && document.getElementById(`thread-${id}`)) focusThread(id);
    });
    annotators.set(index, {anno, container});
  }
  document.getElementById('selection-comment').addEventListener('click', () => {
    if (selectionDraft) showComposer(selectionDraft.selector, selectionDraft.description);
  });
  document.getElementById('selection-comment').addEventListener('mousedown', event => event.preventDefault());
  document.getElementById('close-threads').addEventListener('click', () => { cancel(); document.body.classList.remove('comments-open'); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { cancel(); document.getElementById('selection-comment').hidden = true; }
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && !composer.hidden) { event.preventDefault(); form.requestSubmit(); }
  });
  addEventListener('resize', layoutThreads);
  const observer = new ResizeObserver(layoutThreads); observer.observe(threads); observer.observe(composer);
  renderThreads();
  const stream = new EventSource('/api/stream');
  let seenVersion = null, seenGeneration = null;
  stream.addEventListener('open', () => { seenVersion = null; refresh(true); });
  stream.addEventListener('state', event => {
    const {version, runtime} = JSON.parse(event.data);
    if (!Number.isInteger(version)) {
      connected = false; status.textContent = 'Live state version missing: refresh before submitting.';
      live('offline', 'Disconnected', 'Live state version missing'); return;
    }
    if (seenVersion === null || version !== seenVersion || runtime?.generation !== seenGeneration) {
      seenVersion = version; seenGeneration = runtime?.generation; refresh(true);
    }
  });
  stream.addEventListener('error', () => {
    connected = false; status.textContent = 'Disconnected: drafts are kept; reconnect before submitting.';
    live('offline', 'Disconnected', 'Live stream lost; reconnecting');
  });
  // Reconcile time-window values between state events as well.
  const timer = setInterval(() => refresh(true), 2000);
  addEventListener('pagehide', () => { clearInterval(timer); stream.close(); });
})();
