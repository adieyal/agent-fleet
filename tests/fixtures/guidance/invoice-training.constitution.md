# Constitution: invoice-training

Project principles for agents working on invoice extraction and parsing. Draft, 2026-10-01.

Precedence: a task brief overrides an epic charter, and an epic charter overrides this document. An override must be stated in the brief. If you find a conflict nobody stated, escalate; do not pick a side.

## Goals

1. **Correct stored invoice data.** Line items and totals must match the printed invoice. Correctness beats coverage: falling back is better than accepting a wrong result.
2. **Traceability.** Every stored value can be traced to the printed evidence and to the reader and profile that produced it.
3. **Evidence over claims.** A claim about accuracy or behaviour is backed by runs on real invoices, not by reasoning alone.

## Anti-goals

1. No fallbacks, defaults or placeholders that hide missing or empty data. Surface the gap and name its cause.
2. No supplier pricing rules or loosened tolerances in checks to make them pass. Checks detect misreads; they do not model pricing.
3. No edits to `textract.py` without the user's approval, including edits gated on profile markers and pure reformatting. Propose the change and its replay evidence, then wait.
4. No refactors, tooling changes or new behaviour beyond the stated goal.
5. No parallel rebuild of work that already exists. Start from the epic's canonical line, or say why not.

## Hard limits

1. Never push, force-push, open or close a PR, or delete a branch without the user's approval.
2. Read production only with explicit approval, and only read-only. Never write to the prod bucket or database. S3 scripts pin dev settings and assert the bucket.
3. LLM calls stay within the budget stated in the brief. The default is a small sample (5 to 10 invoices), one run per invoice.
4. Never touch the user's fleet store or config. Tests use temporary `FLEET_CONFIG` and `FLEET_STORE` paths.

## Evidence rules

1. **The printed document is the source of truth.** If a fixture and the print disagree, the print wins and the report says so. Never edit an expected fixture without checking the print.
2. Never weaken an assertion to make a test pass.
3. If a test fails, check whether it also fails on the base commit before assuming you caused it.
4. Suppliers without a profile must behave identically. Prove it with a replay; do not argue it.
5. Measure before estimating, and run a sample before a full batch.

## Decide yourself, and record the decision with the principle you used

- Typing and naming cleanups in files you already touch, with no behaviour change.
- Test-only fixes that keep assertion strength.
- A choice between options that all satisfy this document and the brief.
- Imports of the composition root (`main.containers`) from entry points.

## Escalate

- Scope changes, and any behaviour change for suppliers without a profile.
- Any edit to `textract.py`.
- Production access, budget increases, and destructive git operations.
- A conflict between the brief, the epic charter and this document.
- Changes to acceptance invoices or success criteria.

## Upkeep

When the user overrules an agent, check whether a principle was missing. A decision that recurs within an epic becomes one of that epic's decisions in force. A decision that recurs across epics belongs here. Date every change.
