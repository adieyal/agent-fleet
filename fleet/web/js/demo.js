// Demo data (?demo): a synthetic fleet and a small Markdown renderer for its documents.

import { DEBUG, POLL_MS, QS } from './env.js';
import { esc, hash, seeded } from './util.js';
import { DOC_FILE, activityOf } from './activity.js';

// Demo only: a small Markdown renderer producing the same shapes as the server's markdown-it
// (heading ids, tables, task lists, footnotes). Everything is escaped before any tag is added.
function mdInline(s) {
  return s.split(/(`[^`]+`)/).map(part => part.length > 1 && part.startsWith('`') && part.endsWith('`')
    ? `<code>${esc(part.slice(1, -1))}</code>`
    : esc(part)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
      .replace(/\[\^(\w+)\]/g, '<sup class="footnote-ref"><a href="#fn-$1" id="fnref-$1">[$1]</a></sup>')
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>')).join('');
}
function mdList(lines) {
  const indent = l => l.match(/^\s*/)[0].length, bullet = /^\s*([-*]|\d+\.)\s+/;
  const base = indent(lines[0]), items = [];
  for (const l of lines) {
    if (indent(l) <= base && bullet.test(l)) items.push({ text: l.replace(bullet, ''), kids: [] });
    else if (items.length) items[items.length - 1].kids.push(l);
  }
  const tag = /^\s*\d+\./.test(lines[0]) ? 'ol' : 'ul';
  let tasks = false;
  const lis = items.map(it => {
    const m = it.text.match(/^\[([ xX])\]\s+(.*)$/);
    if (m) tasks = true;
    const box = m ? `<input class="task-list-item-checkbox" disabled type="checkbox"${m[1] === ' ' ? '' : ' checked'}>` : '';
    return `<li${m ? ' class="task-list-item"' : ''}>${box}${mdInline(m ? m[2] : it.text)}${it.kids.length ? mdList(it.kids) : ''}</li>`;
  }).join('');
  return `<${tag}${tasks ? ' class="contains-task-list"' : ''}>${lis}</${tag}>`;
}
function mdToHtml(md) {
  const lines = md.split('\n'), out = [], toc = [], notes = [], used = new Set();
  const slug = text => { const b = text.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-') || 'section'; let k = b, n = 1; while (used.has(k)) k = `${b}-${n++}`; used.add(k); return k; };
  const starts = l => /^(#{1,6}\s|```|>|\||\s*([-*]|\d+\.)\s|\[\^\w+\]:)/.test(l) || /^(-{3,}|\*{3,})\s*$/.test(l);
  const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  let i = 0, m;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if ((m = l.match(/^(#{1,6})\s+(.*)$/))) {
      const level = m[1].length, text = m[2].trim();
      if (level <= 3) { const id = slug(text); toc.push({ level, id, text }); out.push(`<h${level} id="${id}">${mdInline(text)}</h${level}>`); }
      else out.push(`<h${level}>${mdInline(text)}</h${level}>`);
      i++;
    } else if (l.startsWith('```')) {
      const lang = l.slice(3).trim(), buf = [];
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) buf.push(lines[i]);
      i++;
      out.push(`<pre><code${lang ? ` class="language-${esc(lang)}"` : ''}>${esc(buf.join('\n'))}\n</code></pre>`);
    } else if (/^(-{3,}|\*{3,})\s*$/.test(l)) { out.push('<hr>'); i++; }
    else if (l.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith('>')) buf.push(lines[i++].replace(/^>\s?/, ''));
      out.push(`<blockquote>${mdToHtml(buf.join('\n')).html}</blockquote>`);
    } else if (l.startsWith('|')) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith('|')) rows.push(lines[i++]);
      const align = cells(rows[1] || '').map(c => /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : '');
      const at = k => align[k] ? ` style="text-align:${align[k]}"` : '';
      out.push(`<table><thead><tr>${cells(rows[0]).map((c, k) => `<th${at(k)}>${mdInline(c)}</th>`).join('')}</tr></thead><tbody>${
        rows.slice(2).map(r => `<tr>${cells(r).map((c, k) => `<td${at(k)}>${mdInline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
    } else if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const buf = [];
      while (i < lines.length && (/^\s*([-*]|\d+\.)\s/.test(lines[i]) || /^\s{2,}\S/.test(lines[i]))) buf.push(lines[i++]);
      out.push(mdList(buf));
    } else if ((m = l.match(/^\[\^(\w+)\]:\s*(.*)$/))) { notes.push({ id: m[1], text: m[2] }); i++; }
    else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !(buf.length && starts(lines[i]))) buf.push(lines[i++].trim());
      out.push(`<p>${mdInline(buf.join(' '))}</p>`);
    }
  }
  if (notes.length) out.push(`<hr class="footnotes-sep"><section class="footnotes"><ol class="footnotes-list">${notes.map(n =>
    `<li id="fn-${esc(n.id)}" class="footnote-item"><p>${mdInline(n.text)} <a href="#fnref-${esc(n.id)}" class="footnote-backref">↩︎</a></p></li>`).join('')}</ol></section>`);
  return { html: out.join('\n'), toc };
}

const DEMO_DOCS = {
  shadowDiff: `# Shadow-parse vs Textract: line-item diff

Ran the shadow parser over **60 invoices** from today’s sample (12 suppliers) and diffed every line item against the Textract baseline. 51 invoices match exactly; 9 have at least one mismatch, 23 rows in total.

> The shadow parser is ahead on credit notes and multi-page invoices, but still trails Textract on handwritten quantity corrections. Nothing here blocks the rollout to the 12 pilot restaurants.

## Summary

| Measure | Shadow parser | Textract | Δ |
|---|---:|---:|---:|
| Invoices parsed | 60 | 60 | 0 |
| Exact line-item match | 51 | 47 | +4 |
| Rows with a mismatch | 23 | 31 | −8 |
| Median latency (s) | 2.8 | 6.1 | −3.3 |
| Cost per invoice (¢) | 0.9 | 1.5 | −0.6 |

## Method

1. Pulled the sample with \`fleet-sample --date 2026-09-25 --limit 60\` and hashed every page image so both parsers saw identical input.
2. Ran both parsers with the same images and no retries.
3. Normalised supplier codes and units before comparing:
   - \`KG\`, \`kg\` and \`Kilo\` all become \`kg\`
   - pack sizes such as \`6x1L\` split into quantity and unit
4. Compared line by line on *description, quantity, unit price and line total*, with a 0.5 % tolerance on totals.

\`\`\`python
def rows_match(a: LineItem, b: LineItem, tolerance: Decimal = Decimal("0.005")) -> bool:
    if normalise(a.description) != normalise(b.description):
        return False
    if a.quantity != b.quantity:
        return False
    return abs(a.line_total - b.line_total) <= tolerance * abs(b.line_total)
\`\`\`

## Mismatches by cause

### Handwritten corrections

Seven rows. A driver crossed out the printed quantity and wrote a new one beside it; Textract read the handwriting in four of them and we read none. This is the only category where the shadow parser is clearly behind.

| Invoice | Supplier | Line | Printed | Written | Shadow | Textract |
|---|---|---|---:|---:|---:|---:|
| INV-1001 | Supplier A | Chicken thigh 2 kg | 6 | 4 | 6 | 4 |
| INV-1001 | Supplier A | Rapeseed oil 20 L | 2 | 1 | 2 | 1 |
| INV-1002 | Supplier B | Double cream 2 L | 10 | 8 | 10 | 10 |
| INV-1003 | Supplier B | Free-range eggs ×180 | 3 | 2 | 3 | 2 |
| INV-1006 | Supplier C | Lemons (box of 80) | 2 | 1 | 2 | 1 |

### Credit notes

Textract flipped negative line totals to positive in five of six credit notes. The shadow parser keeps the sign because it reads the document type before any line items[^1]. This matters more than the row count suggests: a flipped credit note overstates spend twice, once for the credit and once for the original.

### Multi-page invoices

Textract dropped the carried-forward subtotal on page two of three invoices, so its totals were short by exactly the page-one amount:

\`\`\`text
INV-1004  page 1 subtotal   £412.60   carried forward: missing
INV-1004  page 2 total      £198.35   expected £610.95
INV-1005  page 1 subtotal   £1,084.20 carried forward: missing
\`\`\`

### Unit-price rounding

Four rows differ by a penny because the supplier prints unit prices to three decimal places. Both parsers are arguably right. The relative tolerance should absorb these, and does, except for line totals under £1, where half a percent is less than a penny.

## What I changed

- Added \`luc_tolerance_abs = Decimal("0.01")\` alongside the relative tolerance, and a test for a £0.84 line.
- Kept document-type detection ahead of line parsing, with a regression test for each of the six credit notes.
- Did **not** touch handwriting: that needs a model change, not a rule, and belongs in its own job.

## Checklist

- [x] Sample pulled and hashed
- [x] Both parsers run on identical images
- [x] Mismatches clustered by cause
- [ ] Taxonomy update drafted (next step)
- [ ] Findings reviewed by the invoices team

## Open questions

1. Should handwritten corrections be *flagged for review* rather than parsed? A flag is cheap and safe; parsing them wrong is expensive.
2. When printed and written quantities disagree and there is no delivery note, which one do we trust?
3. Is a penny of rounding worth surfacing to the restaurant at all, or should it be silently absorbed?

---

The full row-level list is in the outbox as \`mismatches.md\`.

[^1]: \`DocumentKind.CREDIT_NOTE\` is detected from the header block before any line items are read, so the sign is applied once, at the end.`,

  mismatches: `# Mismatches (23 rows)

Row-level list behind the step 3 report. Amounts in GBP.

| # | Invoice | Supplier | Cause | Shadow | Textract | Expected |
|---:|---|---|---|---:|---:|---:|
| 1 | INV-1001 | Supplier A | handwriting | 6 | 4 | 4 |
| 2 | INV-1001 | Supplier A | handwriting | 2 | 1 | 1 |
| 3 | INV-1002 | Supplier B | handwriting | 10 | 10 | 8 |
| 4 | CN-1001 | Supplier B | credit sign | −12.40 | 12.40 | −12.40 |
| 5 | CN-1002 | Supplier A | credit sign | −3.10 | 3.10 | −3.10 |
| 6 | INV-1004 | Supplier C | carried forward | 610.95 | 198.35 | 610.95 |
| 7 | INV-1006 | Supplier C | rounding | 0.84 | 0.85 | 0.84 |
| 8 | INV-1007 | Supplier C | rounding | 0.63 | 0.62 | 0.63 |

Rows 9–23 follow the same four causes; see the step report for the breakdown.`,

  diffMethod: `# How the Textract diff works

A short note so the next run can reuse the method.

- Both parsers get the **same page images**, hashed before the run.
- Units and supplier codes are normalised first; see \`normalise()\` in \`shadow/diff.py\`.
- Totals use a relative tolerance of 0.5 % *and* an absolute floor of one penny.

\`\`\`bash
python -m shadow.diff --sample samples/2026-09-25 --baseline textract --out outbox/
\`\`\``,

  article: `# PAR by weekday

Par levels tell you how much of each item to keep on hand. Most kitchens set one number and live with it, but Friday is not Tuesday. **PAR by weekday** lets you set a different par for each day, so you order for the service you are actually going to have.

## When to use it

- Your covers swing a lot across the week (for example, quiet Mondays and a packed Saturday).
- You receive deliveries on fixed days and want each order to cover the gap until the next one.
- You waste perishables early in the week and run short at the weekend.

## Setting it up

1. Open **Inventory → Par levels** and pick an item.
2. Switch *Same every day* to **By weekday**.
3. Enter a par for each day. Leave a day blank to fall back to the item’s default.

| Day | Covers (avg) | Par: salmon fillets |
|---|---:|---:|
| Monday | 62 | 8 |
| Friday | 148 | 20 |
| Saturday | 171 | 24 |

> Start with your busiest day and your quietest day. The days in between are usually obvious once those two are right.

## What changes in ordering

Suggested orders now use the par for the day the delivery **covers**, not the day you place it. If Tuesday’s delivery has to last until Friday, the suggestion uses the highest par across those days.`,

  deckLayout: `# Deck layout notes

How the rooms are laid out, so the next change doesn’t fight the walk paths.

## Room grid

| Prop | Tiles (x, y) | Who stands there |
|---|---|---|
| Terminals | 0.9–6.3, 0.25 | bash |
| Whiteboard | 6.9–9.5, 0 | plan, delegate fallback |
| Workbench | 4.8–7.6, 4.2 | edit |
| Document press | 11.1–11.9, 2.9–5.5 | nobody; documents land here |

## Walk paths

- Two aisles, at y = 2.6 and y = 7.3.
- Crossings at x = 3.6 and x = 8.7, so nobody walks through the workbench.

## Still to do

- [x] Trays tinted by host
- [x] Printed-sheet animation
- [ ] Tune stack height at 390 px`,

  rootCause: `# Root cause: flaky invoice upload e2e

The upload test failed about one run in twelve. The poller and the upload handler both called \`commit()\` on the same session, and whichever landed second raised \`StaleDataError\`.

## Fix

\`\`\`python
with upload_lock(invoice.id):
    session.refresh(invoice)
    invoice.status = Status.PARSED
    session.commit()
\`\`\`

## Evidence

| Runs | Before | After |
|---:|---:|---:|
| 50 | 4 failures | 0 failures |
| 200 | 17 failures | 0 failures |

- [x] Reproduced 20× before the fix
- [x] 250 green runs after`,
};

function stepReport(job, i) {
  const s = job.steps[i];
  return `# Step ${i + 1}: ${s.title}

${s.result || 'Done.'}

## What I did

- Read the code paths involved and the existing tests before changing anything.
- Made the change in small commits in \`${job.cwd}\`, re-running the affected tests after each one.
- Checked the result against the step’s goal rather than just the tests.

## Checks

| Check | Command | Result |
|---|---|---|
| Unit tests | \`pytest -q\` | 142 passed |
| Lint | \`ruff check .\` | clean |
| Types | \`mypy .\` | no issues |

## Left open

Nothing blocking for the next step.`;
}

export let demoDoc = null;   // set by demoSource: (host, job, id) → document, as /api/doc would return it

export function demoSource() {
  const rand = seeded(42);
  const pick = arr => arr[Math.floor(rand() * arr.length)];
  const now = () => Date.now() / 1000;
  const SUMMARIES = {
    bash: ['pytest tests/invoices/test_upload.py -q', 'npx vitest run src/v2/suppliers', 'git diff --stat', 'ruff check app/suppliers', 'make migrate', 'npm run lint -- --fix', 'git log --oneline -5',
      'git commit -m "Port supplier filters to the V2 route"', 'git push -u origin HEAD', 'npm install', 'npm run build', 'docker compose up -d db', 'sleep 30',
      'curl -s https://api.github.com/repos/example/demo-store/pulls', 'ssh node-b fleet ls', 'psql -c "select count(*) from invoices"', 'ls -la fleet/web', 'mypy app/invoices',
      'git add -A', 'python scripts/export_suppliers.py --dry-run', 'cat package.json', 'git checkout -b fix/upload-poller'],
    edit: ['frontend/src/v2/routes/suppliers.tsx', 'app/invoices/parser/luc.py', 'fleet/web/index.html', 'docs/guides/onboarding.md', 'app/suppliers/adapters.py', 'tests/test_fleetd_parsers.py'],
    read: ['app/suppliers/views.py', 'docs/adr/0002-module-refactor.md', 'frontend/src/v2/router.tsx', 'fleet/remote/fleetd.py', 'invoices/sample-0412.json'],
    search: ['SupplierRow', '**/*.spec.ts', 'luc_tolerance', 'docs/**/*.png'],
    web: ['https://tanstack.com/router/latest/docs/guide/data-loading', 'https://docs.python.org/3/library/decimal.html', 'https://playwright.dev/docs/screenshots'],
    think: ['Weighing whether the tolerance should be relative to the line total…', 'The flake only happens when the poller fires twice…', 'Two ways to split the loader; the second keeps parity…'],
    plan: ['{"todos": […]}'],
    delegate: ['Find every caller of get_active_restaurants', 'List routes still on the legacy table'],
  };
  // shaped like fleetd's events: the Claude tool name, its main argument, and for shell calls sometimes Claude's description
  const INTENTS = { 'make migrate': 'Run the database migrations', 'git diff --stat': 'Show what changed', 'ruff check app/suppliers': 'Lint the suppliers module',
    'git push -u origin HEAD': 'Push the branch', 'sleep 30': 'Wait for the server to come up' };
  const demoTool = kind => {
    const summary = pick(SUMMARIES[kind]);
    const name = { bash: 'Bash', edit: DOC_FILE.test(summary) ? 'Write' : 'Edit', read: 'Read', web: 'WebFetch', think: '', plan: 'TodoWrite', delegate: 'Agent' }[kind]
      ?? (summary.includes('*') ? 'Glob' : 'Grep');
    const ev = { kind: 'tool', tool: kind, name, summary };
    if (kind === 'bash' && INTENTS[summary] && rand() < 0.7) ev.intent = INTENTS[summary];
    return ev;
  };
  const WEIGHTS = [['bash', 8], ['edit', 4], ['read', 4], ['search', 2], ['web', 1], ['think', 3], ['plan', 1], ['delegate', 1.2]];
  // ?debug&pin=ship,read,…: running jobs only do these activities (job i does the i-th, round the list), for close-ups;
  // an entry like type+ship alternates between them
  const PINS = DEBUG && QS.get('pin') ? QS.get('pin').split(',') : null;
  const pinned = act => {
    for (let k = 0; k < 400; k++) { const ev = demoTool(pickTool()); if (activityOf(ev) === act) return ev; }
    return demoTool('bash');
  };
  const pickTool = () => { let x = rand() * WEIGHTS.reduce((s, w) => s + w[1], 0); for (const [k, w] of WEIGHTS) { if ((x -= w) <= 0) return k; } return 'bash'; };
  const RESULTS = ['Done — 14 files touched, tests green.', 'Found the race: poller and upload both call commit(). Patched with a lock.', 'Parity confirmed against master for all 6 filters.', 'Wrote findings to outbox/mismatches.md (23 rows).', 'All steps reproduced locally; nothing left open.'];
  const TODO_POOL = ['Read the failing test', 'Reproduce locally', 'Write the fix', 'Run the affected tests', 'Update the docs', 'Check parity with master', 'Summarise for the orchestrator'];
  const specs = [
    ['node-a', 'demo-store', 'Migrate the suppliers list to a V2 React route', 'claude', 'claude-opus-5-5', ['Map legacy supplier list behaviour', 'Build SuppliersRoute with a TanStack loader', 'Port filters and sort', 'Add parity tests', 'Run vitest + e2e', 'Write the PR description'], 2, 'running'],
    ['node-b', 'demo-store', 'Fix the flaky invoice upload e2e test', 'codex', 'gpt-5-codex', ['Reproduce the flake 20×', 'Find the race in the upload poller', 'Patch and rerun 50×', 'Summarise the root cause'], 1, 'running'],
    ['node-c', 'demo-store', 'Apply review feedback', 'claude', 'claude-opus-5-5', ['Collect review threads', 'Apply naming fixes', 'Re-run affected tests'], 0, 'running'],
    ['node-c', 'demo-parser', 'Shadow-parse 60 invoices and diff against Textract', 'claude', 'claude-opus-5-5', ['Pull today’s invoice sample', 'Run the shadow parser', 'Diff line items', 'Cluster mismatches', 'Write findings to outbox', 'Draft taxonomy update', 'Summarise'], 3, 'running'],
    ['node-b', 'demo-parser', 'Tune LUC tolerance for credit notes', 'codex', 'gpt-5-codex', ['Add a failing test for negative LUC', 'Adjust the tolerance', 'Run the parser suite'], 1, 'failed'],
    ['node-a', 'agent-fleet', 'Isometric deck view for fleet web', 'claude', 'claude-opus-5-5', ['Sketch the room layout', 'Draw androids per host', 'Walk-to-prop animation', 'Detail panel', 'Screenshot at 390px'], 2, 'running'],
    ['node-c', 'agent-fleet', 'Unit tests for the fleetd parsers', 'codex', 'gpt-5-codex', ['Claude stream fixtures', 'Codex exec fixtures', 'Runner lock tests'], 0, 'queued'],
    ['node-b', 'demo-docs', 'Refresh the onboarding guide screenshots', 'claude', 'claude-opus-5-5', ['List stale screenshots', 'Capture new ones', 'Update the markdown'], 1, 'stalled'],
    ['node-a', 'demo-docs', 'Support article: PAR by weekday', 'claude', 'claude-opus-5-5', ['Read the feature PR', 'Draft the article', 'Tighten the copy'], 3, 'done'],
    // a batch of six at the comms dish, checking links: more than five at one station gather into a group figure
    ...[1, 2, 3, 4, 5, 6].map(n => [['node-a', 'node-b', 'node-c'][n % 3], 'demo-docs', `Check the links in guide chapter ${n}`, 'claude', 'claude-opus-5-5',
      ['Collect the links', 'Fetch each one', 'List the dead ones'], 1, 'running']),
  ];
  const t0 = now();
  const focusOf = project => project === 'demo-parser' ? 'background' : 'priority';   // lit warm while its parse runs
  function newTodos(job) { const start = Math.floor(rand() * 4); job.todos = TODO_POOL.slice(start, start + 3).map((text, i) => ({ text, status: i === 0 ? 'in_progress' : 'pending' })); }
  const jobs = specs.map(([host, project, description, agent, model, titles, cur, status], i) => {
    const id = hash(description).toString(16).padStart(8, '0').slice(0, 6);
    const job = {
      id, host, project, focus: focusOf(project), description, agent, model, status, cwd: `~/src/${project}`,
      permission: agent === 'claude' ? 'acceptEdits' : 'workspace-write',
      created_at: t0 - 3600 + i * 240, updated_at: t0 - (status === 'stalled' ? 1500 : 20),
      session_id: null, tmux: `tmux -L fleet attach -t fleet-${id}`, todos: [], events: [], activity: null, ticks: 0, documents: [],
      steps: titles.map((title, idx) => ({
        index: idx, title, result: null, started_at: null, finished_at: null,
        status: status === 'done' || idx < cur ? 'done' : idx > cur ? 'pending'
          : (status === 'running' || status === 'stalled') ? 'running' : status === 'failed' ? 'failed' : 'pending',
      })),
    };
    job.steps.forEach(s => { if (s.status === 'done') s.result = pick(RESULTS); });
    if (status === 'failed') job.steps[cur].result = 'test_negative_luc_credit_note still fails: expected -12.40, got -12.00 (tolerance applied before sign).';
    const at = (k, e) => job.events.push({ ts: t0 - 400 + k * 30 + i, step: Math.max(0, Math.min(cur, titles.length - 1)), ...e });
    at(0, { kind: 'job', status: 'queued', summary: `job created: ${description}` });
    if (status !== 'queued') for (let k = 1; k < 7; k++) at(k, demoTool(pickTool()));
    if (status === 'failed') at(8, { kind: 'error', summary: 'exit 1: pytest tests/test_luc.py' });
    if (status === 'done') at(9, { kind: 'job', status: 'done', summary: 'job done' });
    if (status === 'stalled') at(9, { kind: 'log', summary: 'runner exited unexpectedly (SIGKILL)' });
    if (status === 'running') newTodos(job);
    job.activity = [...job.events].reverse().find(e => e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') || null;
    return job;
  });
  const batch = new Set(jobs.filter(job => job.description.startsWith('Check the links')));
  for (const job of batch) push(job, demoTool('web'));

  // interactive CLI sessions, shaped like `fleetd sessions` output: a working Claude, an idle one, a working Codex, and
  // a Claude idle for an hour (off the deck) that gets back to work soon after the page opens
  const sessionSpecs = [
    ['node-a', 'agent-fleet', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000001', 'Show live CLI sessions on the deck', 'working', 1900, 4],
    ['node-b', 'demo-store', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000002', 'Why does the stocktake import modal re-render twice?', 'idle', 2600, 380],
    ['node-c', 'demo-parser', 'codex', 'gpt-6-sol', '00000000-0000-4000-8000-000000000003', 'review the unstaged changes are they correct and safe?', 'working', 900, 4],
    ['node-b', 'demo-store', 'claude', 'claude-opus-5-5', '00000000-0000-4000-8000-000000000004', 'Tidy the supplier import logs', 'idle', 5400, 3600],
  ];
  const dormant = sessionSpecs.length - 1;
  const sessions = sessionSpecs.map(([host, project, agent, model, id, title, status, startedAgo, quietFor], i) => {
    const cwd = `~/src/${project}`;
    const session = {
      id, host, agent, cwd, project, focus: focusOf(project), title, status, model, started_at: t0 - startedAgo,
      updated_at: t0 - quietFor, todos: [], events: [], activity: null,
      resume: `cd ${cwd} && ${agent === 'codex' ? 'codex resume' : 'claude --resume'} ${id}`,
    };
    for (let k = 0; k < 6; k++) session.events.push({ ...demoTool(pickTool()), ts: t0 - 700 + k * 50 + i });
    if (status === 'idle') session.events.push({ kind: 'text', summary: 'The modal re-renders because the loader returns a new object each time. Want me to memoise it or move the fetch up?', ts: session.updated_at });
    if (agent === 'claude' && status === 'working') session.todos = [
      { text: 'Discover sessions in fleetd', status: 'completed' }, { text: 'Carry them through the server', status: 'completed' },
      { text: 'Session androids on the deck', status: 'in_progress' }, { text: 'Screenshots', status: 'pending' }];
    session.activity = [...session.events].reverse().find(e => e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') || null;
    return session;
  });

  // documents: jobs carry metadata only, as from the server; the markdown stays here for demoDoc
  const docText = new Map();
  function addDoc(job, id, kind, name, step, markdown, mtime) {
    if (job.documents.some(d => d.id === id)) return;
    const path = kind === 'report' ? `~/.fleet/jobs/${job.id}/result-${step}.md` : kind === 'outbox' ? `~/.fleet/jobs/${job.id}/outbox/${name}` : `${job.cwd}/${name}`;
    job.documents.push({ id, kind, name, step, path, size: new TextEncoder().encode(markdown).length, mtime: mtime || now() });
    docText.set(`${job.host}:${job.id}:${id}`, markdown);
  }
  function addReport(job, i, mtime) { addDoc(job, `report-${i}`, 'report', `Step ${i + 1}: ${job.steps[i].title}`, i, stepReport(job, i), mtime); }
  const [suppliers, flaky, review, shadow, luc, deck, , , article] = jobs;
  addReport(suppliers, 0, t0 - 2400); addReport(suppliers, 1, t0 - 1300);
  addDoc(suppliers, 'file-0', 'file', 'docs/suppliers-v2-parity.md', 1, DEMO_DOCS.deckLayout.replace('Deck layout notes', 'Suppliers V2 parity notes'), t0 - 1250);
  addReport(flaky, 0, t0 - 900);
  addReport(shadow, 0, t0 - 3000); addReport(shadow, 1, t0 - 2100);
  addDoc(shadow, 'file-0', 'file', 'notes/textract-diff-method.md', 2, DEMO_DOCS.diffMethod, t0 - 1500);
  addDoc(shadow, 'report-2', 'report', 'Step 3: Diff line items', 2, DEMO_DOCS.shadowDiff, t0 - 700);
  addDoc(shadow, 'outbox-mismatches.md', 'outbox', 'mismatches.md', null, DEMO_DOCS.mismatches, t0 - 650);
  addReport(luc, 0, t0 - 1800);
  addReport(deck, 0, t0 - 2600); addReport(deck, 1, t0 - 1400);
  addReport(article, 0, t0 - 5200);
  addDoc(article, 'file-0', 'file', 'drafts/par-by-weekday-v1.md', 1, DEMO_DOCS.article, t0 - 4800);
  addDoc(article, 'file-1', 'file', 'drafts/par-by-weekday-v2.md', 1, DEMO_DOCS.article, t0 - 4300);
  addReport(article, 1, t0 - 4000);
  addDoc(article, 'file-2', 'file', 'articles/par-by-weekday.md', 2, DEMO_DOCS.article, t0 - 3500);
  addReport(article, 2, t0 - 3300);
  addDoc(article, 'outbox-par-by-weekday.md', 'outbox', 'par-by-weekday.md', null, DEMO_DOCS.article, t0 - 3200);
  // A synthetic pipeline shaped like fleetd's reports: items pass the first two columns tick by tick, the gates run
  // in a burst at the end, and a few ticks later a new run starts with the finished one as its baseline.
  const PIPE = { nodes: [['items'], ['decided', 'tied', 'unlearnable'], ['alone', 'agree', 'disagree', 'profile', 'unsettled'],
    ['confident', 'review'], ['null cell', 'sum mismatch', 'profile disagree', 'low support']], total: 1800,
    tones: { confident: 'good', review: 'warn', unsettled: 'muted', unlearnable: 'muted' } };
  const prand = seeded(7), ppick = arr => arr[Math.floor(prand() * arr.length)];   // its own stream: the fleet's stays as it was
  const weighted = weights => { let x = prand() * Object.values(weights).reduce((s, w) => s + w, 0); for (const [k, w] of Object.entries(weights)) if ((x -= w) <= 0) return k; return Object.keys(weights)[0]; };
  let pipe = null, pipeBase = null, pipeSeq = 0;
  function newPipeRun(n) {
    pipe = { n, run_id: `demo-${n}`, label: `demo run ${n} (synthetic)`, started_at: now(), edges: new Map(), inflow: {}, outflow: {},
      recent: {}, items: [], status: 'running', ended_at: null, updated_at: now(), rate: 0, doneAt: 0 };
  }
  function pipeFlow(item, from, to, attrs) {
    const key = from + '→' + to;
    pipe.edges.set(key, (pipe.edges.get(key) || 0) + 1);
    pipe.outflow[from] = (pipe.outflow[from] || 0) + 1; pipe.inflow[to] = (pipe.inflow[to] || 0) + 1;
    (pipe.recent[to] ||= []).push({ item, ts: now(), ...(attrs ? { attrs } : {}) });
    if (pipe.recent[to].length > 25) pipe.recent[to].shift();
  }
  function pipeCounts(p) {
    return Object.fromEntries([...new Set([...Object.keys(p.inflow), ...Object.keys(p.outflow)])].map(n => [n, Math.max(p.inflow[n] || 0, p.outflow[n] || 0)]));
  }
  function stepPipe() {
    if (!pipe) newPipeRun(1);
    if (pipe.status === 'done') { if (++pipe.doneAt > 4) { pipeBase = pipe; newPipeRun(pipe.n + 1); } return; }
    const before = pipe.items.length;
    for (let k = 0, n = 60 + Math.floor(prand() * 60); k < n && pipe.items.length < PIPE.total; k++) {
      const item = `D-${pipe.n}-${String(pipe.items.length).padStart(5, '0')}`;
      const first = weighted({ decided: 0.7, tied: 0.2, unlearnable: 0.1 });
      pipeFlow(item, 'items', first, first === 'unlearnable' ? { why: ppick(['no printed total', 'unreadable scan']) } : null);
      const second = first === 'unlearnable' ? null
        : weighted(first === 'decided' ? { alone: 0.3, agree: 0.4, disagree: 0.15, profile: 0.15 } : { agree: 0.3, unsettled: 0.7 });
      if (second) pipeFlow(item, first, second);
      pipe.items.push([item, second]);
    }
    if (pipe.items.length >= PIPE.total) {   // the gates, all at once
      const reasons = ['null cell', 'sum mismatch', 'profile disagree', 'low support'];
      for (const [item, second] of pipe.items) {
        if (!second) continue;
        const confident = prand() < (['alone', 'agree', 'profile'].includes(second) ? 0.85 : 0.25);
        pipeFlow(item, second, confident ? 'confident' : 'review');
        if (!confident) {
          const why = reasons.filter(() => prand() < 0.45);
          if (!why.length) why.push(ppick(reasons));
          pipeFlow(item, 'review', why[0], { reasons: why });
        }
      }
      pipe.status = 'done'; pipe.ended_at = now();
    }
    pipe.rate = Math.round((pipe.items.length - before) / (POLL_MS / 1000) * 10) / 10;   // items into the first column
    pipe.updated_at = now();
  }
  function pipeReport() {
    const edges = [...pipe.edges].map(([key, c]) => [...key.split('→'), c]), counts = pipeCounts(pipe);
    return { host: 'node-a', pipeline: 'demo-training', project: 'demo-training', project_id: null, declared: true, host_ok: true,
      host_error: null, seq: ++pipeSeq,
      run: { run_id: pipe.run_id, pipeline: 'demo-training', label: pipe.label, started_at: pipe.started_at, total: PIPE.total,
        tones: PIPE.tones, nodes: PIPE.nodes, edges, counts, flows: edges.reduce((s, e) => s + e[2], 0),
        recent: Object.fromEntries(Object.entries(pipe.recent).filter(([n]) => !pipe.outflow[n])),
        item_rate: pipe.status === 'running' ? pipe.rate : 0, updated_at: pipe.updated_at, status: pipe.status, ended_at: pipe.ended_at },
      baseline: pipeBase && { run_id: pipeBase.run_id, label: pipeBase.label, started_at: pipeBase.started_at, total: PIPE.total,
        edges: [...pipeBase.edges].map(([key, c]) => [...key.split('→'), c]), counts: pipeCounts(pipeBase) } };
  }

  let tickCount = 0;
  demoDoc = async (host, jobId, id) => {
    await new Promise(resolve => setTimeout(resolve, 350));
    const job = jobs.find(j => j.host === host && j.id === jobId);
    const meta = job && job.documents.find(d => d.id === id);
    if (!meta) throw new Error(`no document ${id} on ${host}:${jobId}`);
    const markdown = docText.get(`${host}:${jobId}:${id}`), { html, toc } = mdToHtml(markdown);
    const words = markdown.split(/\s+/).filter(Boolean).length;
    return { ...meta, job: job.id, project: job.project, agent: job.agent, host, job_description: job.description,
      markdown, html, toc, words, minutes: Math.max(1, Math.round(words / 230)), truncated: false };
  };
  function push(job, e) {
    job.events.push({ ts: now(), step: job.steps.findIndex(s => s.status === 'running'), ...e });
    if (job.events.length > 15) job.events.splice(0, job.events.length - 15);
    job.updated_at = now();
    if (e.kind === 'tool' || e.kind === 'text' || e.kind === 'error') job.activity = job.events[job.events.length - 1];
  }
  // failed and stalled jobs are blockers under their room's lantern, as fleet/attention.py derives them
  function blocker(job) {
    const step = job.steps.find(s => s.status === (job.status === 'failed' ? 'failed' : 'running'));
    return { id: `job:${job.host}:${job.id}:${job.status}:${step.index}`, kind: 'blocker', state: 'open', project: job.project,
      project_id: null, source: 'Demo job', context_reference: `${job.host}:${job.id}`, last_seen: job.updated_at,
      owner: { type: 'job', host: job.host, id: job.id, key: `${job.host}:${job.id}` },
      summary: `step ${step.index + 1} ${job.status}: ${step.title}`, since: job.updated_at };
  }
  function finish(job) { job.status = 'done'; job.ticks = 0; job.todos = []; push(job, { kind: 'job', status: 'done', summary: 'job done' }); }
  function tick() {
    tickCount++;
    if (tickCount === 3) { addDoc(deck, 'file-0', 'file', 'docs/deck-layout.md', 2, DEMO_DOCS.deckLayout); push(deck, { kind: 'tool', tool: 'edit', summary: 'docs/deck-layout.md' }); }
    if (tickCount === 7) addDoc(flaky, 'outbox-root-cause.md', 'outbox', 'root-cause.md', null, DEMO_DOCS.rootCause);
    if (tickCount === 4 && review.status === 'running') {   // one job finishes soon after the page opens, so its android is seen walking out
      for (const s of review.steps) if (s.status !== 'done') { s.status = 'done'; s.finished_at = now(); s.result = pick(RESULTS); }
      finish(review);
    }
    for (const job of jobs) {
      job.ticks++;
      if (job.status === 'queued' && job.ticks > 7) {
        job.status = 'running'; job.steps[0].status = 'running'; job.steps[0].started_at = now(); newTodos(job);
        push(job, { kind: 'step', status: 'running', summary: job.steps[0].title });
        continue;
      }
      if (job.status === 'done' && job.ticks > 16) {   // re-queued: idles in the lounge for a while, then starts over
        job.steps.forEach(s => { s.status = 'pending'; s.result = null; });
        job.status = 'queued'; job.ticks = 0;
        push(job, { kind: 'job', status: 'queued', summary: `${job.steps.length} step(s) re-queued` });
        continue;
      }
      if (job.status !== 'running' || batch.has(job)) continue;
      const i = jobs.indexOf(job);
      if (PINS) {
        const seq = PINS[i % PINS.length].split('+'), act = seq[Math.floor(tickCount / 5) % seq.length];   // type+ship: alternate every 10 s
        // a pinned test run alternates with its outcome: an error half the time, otherwise the next command
        if (act === 'test' && job.tested) push(job, rand() < 0.5 ? { kind: 'error', summary: 'exit 1: pytest -q (2 failed, 41 passed)' } : { kind: 'tool', tool: 'bash', name: 'Bash', summary: 'git add -A' });
        else push(job, pinned(act));
        job.tested = act === 'test' && !job.tested;
        continue;
      }
      // a test run is followed by its outcome: sometimes a failure, otherwise the agent simply carries on
      if (job.tested) {
        job.tested = false;
        if (rand() < 0.35) { push(job, { kind: 'error', summary: 'exit 1: 2 failed, 41 passed' }); continue; }
      }
      if (rand() < 0.55) {
        const tool = pickTool();
        const sameRoom = jobs.some(o => o !== job && o.project === job.project);
        const kind = tool === 'delegate' && !sameRoom ? 'read' : tool;
        if (kind === 'think' && rand() < 0.4) push(job, { kind: 'text', summary: pick(SUMMARIES.think) });
        else push(job, demoTool(kind));
        job.tested = activityOf(job.activity) === 'test';
      }
      if (rand() < 0.3 && job.todos.length) {
        const i = job.todos.findIndex(td => td.status !== 'completed');
        if (i >= 0) { job.todos[i].status = 'completed'; if (job.todos[i + 1]) job.todos[i + 1].status = 'in_progress'; }
      }
      if (rand() < 0.08) {
        const i = job.steps.findIndex(s => s.status === 'running');
        if (i < 0) continue;
        job.steps[i].status = 'done'; job.steps[i].finished_at = now(); job.steps[i].result = pick(RESULTS);
        addReport(job, i);
        push(job, { kind: 'step', status: 'done', summary: job.steps[i].result });
        if (job.steps[i + 1]) { job.steps[i + 1].status = 'running'; job.steps[i + 1].started_at = now(); newTodos(job); push(job, { kind: 'step', status: 'running', summary: job.steps[i + 1].title }); }
        else finish(job);
      }
    }
    if (tickCount === 6) { sessions[dormant].status = 'working'; sessions[dormant].updated_at = now(); }
    for (const s of sessions) {
      if (s.status !== 'working' || rand() > 0.5) continue;
      const tool = pickTool();
      s.events.push(tool === 'think' && rand() < 0.4 ? { kind: 'text', summary: pick(SUMMARIES.think), ts: now() }
        : { ...demoTool(tool === 'delegate' ? 'read' : tool), ts: now() });
      if (s.events.length > 15) s.events.splice(0, s.events.length - 15);
      s.activity = s.events[s.events.length - 1];
      s.updated_at = now();
    }
    stepPipe();
    const doc = { time: now(), pipelines: [pipeReport()], attention: jobs.filter(j => j.status === 'failed' || j.status === 'stalled').map(blocker), hosts: [
      ...['node-a', 'node-b', 'node-c'].map(name => ({ name, ok: true, error: null, jobs: jobs.filter(j => j.host === name),
        sessions: sessions.filter(s => s.host === name) })),
      { name: 'node-d', ok: false, error: 'node-d: ssh: connect to host 192.0.2.10 port 22: Connection timed out', jobs: [] },
    ] };
    const first = doc.attention[0];
    doc.attention.push({ ...first, id: 'demo-release-review', kind: 'decision', summary: 'Review the release plan' });
    const rooms = {};
    for (const item of doc.attention) {
      if (!rooms[item.project]) rooms[item.project] = { count: 0, level: 'open', kind: 'blocker', glyph: '✱', open_ids: [], shown: [], listed: [] };
      const marker = rooms[item.project];
      marker.count++;
      for (const key of ['open_ids', 'shown', 'listed']) marker[key].push(item.id);
    }
    doc.attention_display = { rooms, places: [{ place: 'lobby', count: doc.attention.length, level: 'open',
      kind: 'blocker', glyph: '✱', open_ids: doc.attention.map(item => item.id) }],
      front_desk: doc.attention.map(item => item.id), open_count: doc.attention.length };
    return JSON.parse(JSON.stringify(doc));
  }
  return tick;
}
